"""Connect source HWPX files, one target XLSX and an output folder into one run (api.md 2.2).

The bytes that were fingerprinted are kept in memory as the snapshot. Analysis and later
writing use only this snapshot, so "the file we inspected" is always "the file we write from".
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from . import xlsx
from .errors import ServiceError
from .hwpx import MAX_FILE_BYTES, HwpxError, parse_hwpx
from .models import Document
from .storage import local_path

# 입력 한도(OQ-04). 사무용 PC 실측 전의 제안값이다. 넘으면 분석을 시작하기 전에 INPUT_LIMIT로 설명한다.
MAX_SOURCES = 200
MAX_TARGET_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class InputFile:
    file_id: str  # 실행 안의 식별자("src-1"…, "target"). 내용 지문과 분리한다
    path: Path
    role: Literal["source", "target"]
    sha256: str | None  # 읽기 실패면 None
    status: Literal["ok", "failed", "excluded", "duplicate"]
    reason: str = ""
    duplicate_of: str | None = None  # 같은 내용의 다른 경로


@dataclass(frozen=True)
class Inputs:
    run_id: str  # 입력이 바뀌면 새 run_id
    sources: tuple[InputFile, ...]
    target: InputFile
    output_dir: Path
    # 지문을 계산한 바이트. 읽기 전용 매핑이며 repr·비교에서 제외한다.
    snapshots: MappingProxyType = field(repr=False, compare=False, default=MappingProxyType({}))

    def snapshot(self, file_id: str) -> bytes:
        return self.snapshots[file_id]

    @property
    def active_sources(self) -> tuple[InputFile, ...]:
        return tuple(s for s in self.sources if s.status == "ok")


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ServiceError("INPUT_LIMIT", f"파일이 {limit // (1024 * 1024)} MiB를 초과했습니다.")
    return data


def _read_target(path: Path) -> tuple[Path, bytes, str]:
    try:
        resolved = local_path(path)
    except ValueError as exc:
        raise ServiceError("INPUT_UNSUPPORTED", str(exc)) from exc
    if resolved.suffix.lower() != ".xlsx":
        raise ServiceError("INPUT_UNSUPPORTED", "대상 파일은 XLSX만 지원합니다.")
    try:
        data = _read(resolved, MAX_TARGET_BYTES)
    except OSError as exc:
        raise ServiceError(
            "INPUT_UNSUPPORTED", "대상 파일을 읽을 수 없습니다. 경로와 접근 권한을 확인하세요."
        ) from exc
    xlsx.sheet_names(data)  # 손상·암호화·형식 오류는 여기서 INPUT_UNSUPPORTED로 바뀐다
    return resolved, data, _fingerprint(data)


def _read_source(file_id: str, path: Path, seen: dict[str, str]) -> tuple[InputFile, bytes | None]:
    try:
        resolved = local_path(path)
        if resolved.suffix.lower() != ".hwpx":
            raise HwpxError("HWPX 파일만 지원합니다. HWP는 HWPX로 변환해 주세요.")
        data = _read(resolved, MAX_FILE_BYTES)
    except (OSError, ValueError, HwpxError) as exc:
        return InputFile(file_id, Path(path), "source", None, "failed", str(exc)), None
    except ServiceError as exc:
        return InputFile(file_id, resolved, "source", None, "failed", exc.message), None
    digest = _fingerprint(data)
    if digest in seen:
        return (
            InputFile(
                file_id,
                resolved,
                "source",
                digest,
                "duplicate",
                "같은 내용의 파일이 이미 있습니다.",
                seen[digest],
            ),
            None,
        )
    try:
        parse_hwpx(data, resolved)
    except HwpxError as exc:
        # 읽기 검증에 실패한 파일은 지문을 두지 않는다(계약: 읽기 실패면 None). 바이트도 보관하지 않는다.
        return InputFile(file_id, resolved, "source", None, "failed", str(exc)), None
    seen[digest] = file_id
    return InputFile(file_id, resolved, "source", digest, "ok"), data


def _check_paths(sources: list[Path], target: Path, output_dir: Path) -> Path:
    try:
        out = local_path(output_dir)
    except ValueError as exc:
        raise ServiceError("PATH_CONFLICT", str(exc)) from exc
    if out.exists() and not out.is_dir():
        raise ServiceError("PATH_CONFLICT", "출력 위치는 폴더여야 합니다.")
    inputs = {_same_file(p) for p in [*sources, target]}
    if _same_file(out) in inputs:
        raise ServiceError("PATH_CONFLICT", "출력 폴더가 입력 파일과 같은 경로입니다.")
    if _same_file(target) in {_same_file(p) for p in sources}:
        raise ServiceError("PATH_CONFLICT", "대상 파일이 원본 파일과 같은 경로입니다.")
    return out


def _same_file(path: Path) -> str:
    import os

    return os.path.normcase(str(Path(path).expanduser().absolute()))


def open_inputs(sources: list[Path], target: Path, output_dir: Path) -> Inputs:
    """원본 실패는 status="failed"로 돌려준다. 대상 실패·경로 충돌·한도 초과는 ServiceError."""
    if len(sources) > MAX_SOURCES:
        raise ServiceError("INPUT_LIMIT", f"원본은 한 번에 {MAX_SOURCES}개까지 넣을 수 있습니다.")
    out = _check_paths(sources, target, output_dir)
    target_path, target_data, target_digest = _read_target(target)
    snapshots: dict[str, bytes] = {"target": target_data}
    total = len(target_data)
    files = []
    seen: dict[str, str] = {}
    for index, path in enumerate(sources, start=1):
        file_id = f"src-{index}"
        info, data = _read_source(file_id, path, seen)
        files.append(info)
        if data is not None:
            snapshots[file_id] = data
            total += len(data)
            if total > MAX_SNAPSHOT_BYTES:
                raise ServiceError(
                    "INPUT_LIMIT",
                    f"입력 파일 합계가 {MAX_SNAPSHOT_BYTES // (1024 * 1024)} MiB를 초과했습니다. 원본을 나눠 넣어 주세요.",
                )
    return Inputs(
        uuid.uuid4().hex,
        tuple(files),
        InputFile("target", target_path, "target", target_digest, "ok"),
        out,
        MappingProxyType(snapshots),
    )


def exclude_source(inputs: Inputs, file_id: str) -> Inputs:
    """실패하거나 원하지 않는 원본을 뺀다. 입력이 바뀌었으므로 새 run_id를 받는다."""
    if not any(s.file_id == file_id for s in inputs.sources):
        raise ServiceError("INPUT_UNSUPPORTED", "제외할 원본이 입력에 없습니다.")
    sources = tuple(
        InputFile(s.file_id, s.path, s.role, s.sha256, "excluded", s.reason, s.duplicate_of)
        if s.file_id == file_id
        else s
        for s in inputs.sources
    )
    snapshots = {k: v for k, v in inputs.snapshots.items() if k != file_id}
    return Inputs(
        uuid.uuid4().hex, sources, inputs.target, inputs.output_dir, MappingProxyType(snapshots)
    )


def inspect_source(inputs: Inputs, file_id: str) -> Document:
    """기존 hwpx.Document를 스냅샷 바이트에서 만든다."""
    info = next((s for s in inputs.sources if s.file_id == file_id), None)
    if info is None or info.status != "ok":
        raise ServiceError("INPUT_UNSUPPORTED", "읽기에 성공한 원본만 미리 볼 수 있습니다.")
    return parse_hwpx(inputs.snapshot(file_id), info.path)
