"""Approval → minimal-change save → verification → evidence (api.md 2.7·3·4·6, PRD FR-14~18·20~22).

[저장] 한 번이 승인이다. approve는 지금 보이는 계획을 지문(plan_hash)으로 묶고, save_result는 그 지문이
그대로일 때만 쓴다. 결과는 검증을 통과한 것만 공개하고, 어떤 기존 파일도 덮어쓰지 않는다. 근거 JSON을
마지막에 공개해 "JSON이 있으면 완료"가 되게 한다(커밋 기록).
"""

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .analyze import Cancel, ChangeItem, Plan, PlanCounts, manual_items
from .errors import Pairs, ServiceError
from .manual import KINDS, ManualItem
from .models import CellValue
from .patch import Check, PatchError, compare_packages, patch_xlsx
from .rules import canonical_json, rule_to_dict
from .storage import local_path

Progress = Callable[[str, int, int], None]
SCHEMA_VERSION = 1
NOTICE = "이 파일에는 문서의 실제 값이 들어 있습니다. 제출·전달용이 아니며, 작업이 끝나면 보관 기준에 따라 관리하세요."


@dataclass(frozen=True)
class Approval:  # api 2.7, ERD Approval(실행당 1개)
    run_id: str
    approved_change_ids: tuple[str, ...]
    input_fingerprints: Pairs
    rule_hash: str
    plan_hash: str
    review_hash: str
    output_path: Path | None  # 값 검토는 None
    evidence_path: Path
    manual_path: Path | None
    approved_at: str


@dataclass(frozen=True)
class ResultSummary:
    counts: PlanCounts
    excluded_sources: tuple[str, ...]
    failed_sources: tuple[str, ...]
    manual: tuple[ManualItem, ...]
    has_formulas: bool


@dataclass(frozen=True)
class ResultPackage:  # api 2.7, ERD ResultPackage
    run_id: str
    status: Literal["completed", "partial", "failed", "cancelled"]
    output_path: Path | None
    output_sha256: str | None
    evidence_path: Path | None
    evidence_sha256: str | None
    manual_path: Path | None
    checks: tuple[Check, ...]
    summary: ResultSummary
    error: str | None = None
    cleanup_failed: tuple[Path, ...] = ()


# ---------- 지문(api 4절) ----------


def _value(value: CellValue | None):
    return None if value is None else {"kind": value.kind, "value": value.value, "unit": value.unit}


def _path_key(path: Path | None):
    return None if path is None else os.path.normcase(str(Path(path).absolute()))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_hash(plan: Plan, output_path, evidence_path, manual_path) -> str:
    """입력 지문(file_id 순), rule_hash, scope, change_id 순 항목, 출력 경로 3개. run_id는 넣지 않는다."""
    files = sorted(
        [(f.file_id, f.sha256 or "") for f in (*plan.inputs.sources, plan.inputs.target)]
    )
    items = [
        [
            i.change_id,
            i.target_ref,
            _value(i.previous),
            _value(i.final_value),
            i.status,
            i.write_selected,
            sorted(e.evidence_id for e in i.evidence),
        ]
        for i in sorted(plan.items, key=lambda i: i.change_id)
    ]
    scope = "all" if plan.scope == "all" else ["sample", list(plan.scope.file_ids)]
    payload = [
        files,
        plan.rule_hash,
        scope,
        items,
        [_path_key(p) for p in (output_path, evidence_path, manual_path)],
    ]
    return _sha(canonical_json(payload).encode("utf-8"))


def review_hash(plan: Plan) -> str:
    items = [
        [i.change_id, _value(i.corrected), i.review_reason, i.resolved, i.write_selected]
        for i in sorted(plan.items, key=lambda i: i.change_id)
    ]
    return _sha(canonical_json(items).encode("utf-8"))


# ---------- 승인 ----------


def default_output_path(plan: Plan, *, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    target = plan.inputs.target.path
    suffix = "검토" if plan.rule.work_type == "check" else "우렁각시"
    return plan.inputs.output_dir / f"{target.stem}_{suffix}_{stamp}.xlsx"


def _companions(output_path: Path, has_manual: bool) -> tuple[Path, Path | None]:
    evidence = output_path.with_name(output_path.stem + ".evidence.json")
    manual = output_path.with_name(output_path.stem + "_확인필요.txt") if has_manual else None
    return evidence, manual


def _check_paths(plan: Plan, paths: list[Path | None]) -> None:
    inputs = {_path_key(f.path) for f in (*plan.inputs.sources, plan.inputs.target)}
    for path in paths:
        if path is None:
            continue
        if _path_key(path) in inputs:
            raise ServiceError("PATH_CONFLICT", "결과 파일 이름이 입력 파일과 같습니다.")
        if path.exists():
            raise ServiceError(
                "PATH_CONFLICT",
                "같은 이름의 결과 파일이 이미 있습니다. 다시 미리보기해 새 이름으로 저장하세요.",
            )
        if not path.parent.is_dir():
            raise ServiceError("PATH_CONFLICT", "결과를 저장할 폴더가 없습니다.")


def approve(plan: Plan, output_path: Path | None = None) -> Approval:
    """[저장]을 누른 순간 보이는 계획을 묶는다. 예외: PLAN_IS_SAMPLE · NOTHING_TO_WRITE · PATH_CONFLICT."""
    if plan.scope != "all":
        raise ServiceError(
            "PLAN_IS_SAMPLE", "표본 분석으로는 저장할 수 없습니다. 전체를 분석한 뒤 저장하세요."
        )
    writes = tuple(i.change_id for i in plan.items if i.area == "done" and i.write_selected)
    check = plan.rule.work_type == "check"
    if not check and not writes:
        raise ServiceError(
            "NOTHING_TO_WRITE",
            "쓸 칸이 없습니다. 직접 처리 목록만 확인하세요(결과 파일을 만들지 않습니다).",
        )
    base = local_path(output_path) if output_path is not None else default_output_path(plan)
    evidence, manual = _companions(base, bool(manual_items(plan)))
    xlsx = None if check else base
    _check_paths(plan, [xlsx, evidence, manual])
    return Approval(
        plan.run_id,
        writes,
        tuple((f.file_id, f.sha256 or "") for f in (*plan.inputs.sources, plan.inputs.target)),
        plan.rule_hash,
        plan_hash(plan, xlsx, evidence, manual),
        review_hash(plan),
        xlsx,
        evidence,
        manual,
        datetime.now(UTC).replace(microsecond=0).isoformat(),
    )


# ---------- 파일 내용 ----------


def manual_text(plan: Plan, result_name: str | None) -> str:
    """확인필요 파일(api 3절 형식). 종류 순서로 묶고 묶음마다 왜·할 일·항목."""
    items = manual_items(plan)
    counts = plan.counts
    lines = [f"[안내] {NOTICE}", ""]
    if result_name:
        lines.append(f"결과 파일: {result_name}")
    if plan.rule.work_type == "check":
        mismatch = sum(1 for i in plan.items if i.reason_code == "mismatch")
        missing = sum(1 for i in plan.items if i.reason_code == "missing_in_sheet")
        undecided = sum(1 for i in plan.items if i.area == "manual") - mismatch - missing
        lines.append(
            f"일치 {counts.cells_unchanged} · 불일치 {mismatch} · 표에 빠짐 {missing} · "
            f"판정 불가 {undecided} · 문서가 없는 행 {counts.rows_without_source}"
        )
        lines.append("표와 문서 가운데 어느 쪽이 맞는지는 판단하지 않습니다.")
    else:
        unfilled = sum(1 for i in plan.items if i.area == "manual")
        lines.append(
            f"채운 칸 {counts.cells_write} · 이미 같은 값 {counts.cells_unchanged} · "
            f"안 채운 칸 {unfilled} · 읽지 못한 파일 {counts.files_failed}"
        )
    number = 0
    for info in KINDS:
        group = [m for m in items if m.kind == info.kind]
        if not group:
            continue
        number += 1
        lines += ["", f"[{number}] {info.title} ({len(group)})", f"할 일: {info.action}"]
        for item in group:
            lines.append(f"  - {item.where}: {item.reason}")
    return "\n".join(lines) + "\n"


def _item_json(item: ChangeItem) -> dict:
    return {
        "change_id": item.change_id,
        "record_key": item.record_key,
        "field": item.field,
        "target_ref": item.target_ref,
        "target_candidates": [
            {"row": c.row, "address": c.address, "key_value": _value(c.key_value)}
            for c in item.target_candidates
        ],
        "previous": _value(item.previous),
        "proposed": _value(item.proposed),
        "corrected": _value(item.corrected),
        "final": _value(item.final_value),
        "status": item.status,
        "area": item.area,
        "reason_code": item.reason_code,
        "reason": item.reason,
        "review_reason": item.review_reason,
        "write_selected": item.write_selected,
        "evidence": [
            {
                "evidence_id": e.evidence_id,
                "file_id": e.file_id,
                "sha256": e.sha256,
                "record_key": e.record_key,
                "location": e.location,
                "raw_text": e.raw_text,
                "extracted": _value(e.extracted),
                "transforms": list(e.transforms),
            }
            for e in item.evidence
        ],
    }


def evidence_json(
    plan: Plan,
    approval: Approval,
    checks,
    status: str,
    output_sha: str | None,
    manual_sha: str | None,
) -> bytes:
    data = {
        "schema_version": SCHEMA_VERSION,
        "notice": NOTICE,
        "run_id": plan.run_id,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "status": status,
        "inputs": [
            {
                "file_id": f.file_id,
                "path": str(f.path),
                "role": f.role,
                "sha256": f.sha256,
                "status": f.status,
                "reason": f.reason,
            }
            for f in (*plan.inputs.sources, plan.inputs.target)
        ],
        "rule": rule_to_dict(plan.rule),
        "rule_hash": plan.rule_hash,
        "approval": {
            "plan_hash": approval.plan_hash,
            "review_hash": approval.review_hash,
            "approved_at": approval.approved_at,
            "approved_change_ids": list(approval.approved_change_ids),
        },
        "sheet": plan.sheet,
        "counts": asdict(plan.counts),
        "items": [_item_json(i) for i in plan.items],
        "unmatched_rows": [{"row": r, "key": k} for r, k in plan.unmatched_rows],
        "checks": [
            {
                "scope": c.scope,
                "change_id": c.change_id,
                "expected": _value(c.expected),
                "actual": _value(c.actual),
                "passed": c.passed,
                "message": c.message,
            }
            for c in checks
        ],
        "outputs": {
            "xlsx": None
            if approval.output_path is None
            else {"path": str(approval.output_path), "sha256": output_sha},
            "manual": None
            if approval.manual_path is None
            else {"path": str(approval.manual_path), "sha256": manual_sha},
        },
    }
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# ---------- 공개 ----------


def publish_new(temp: Path, final: Path) -> None:
    """덮어쓰지 않는 공개: 같은 볼륨이면 하드 링크(대상이 있으면 실패), 안 되면 배타적 생성 + 복사 + fsync."""
    try:
        os.link(temp, final)
        return
    except FileExistsError:
        raise
    except OSError:
        pass  # 하드 링크를 지원하지 않는 파일 시스템
    with temp.open("rb") as source, open(final, "xb") as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())


def _write_temp(folder: Path, data: bytes, suffix: str) -> Path:
    handle, name = tempfile.mkstemp(prefix=".ureonggaksi-", suffix=suffix, dir=folder)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return Path(name)


def _remove_if_same(path: Path, sha: str) -> bool:
    """공개했던 파일을 지문이 같을 때만 지운다(다른 사람이 같은 이름으로 만든 파일은 건드리지 않는다)."""
    try:
        if _sha(path.read_bytes()) == sha:
            path.unlink()
        return True
    except OSError:
        return False


def _disk_fingerprints_match(plan: Plan, approval: Approval) -> bool:
    expected = dict(approval.input_fingerprints)
    for entry in (*plan.inputs.sources, plan.inputs.target):
        if entry.status not in ("ok",) and entry.role == "source":
            continue  # 실패·제외·중복 원본은 쓰지 않았다
        try:
            if _sha(Path(entry.path).read_bytes()) != expected.get(entry.file_id):
                return False
        except OSError:
            return False
    return True


# ---------- 저장 ----------


def _summary(plan: Plan, has_formulas: bool) -> ResultSummary:
    return ResultSummary(
        plan.counts,
        tuple(s.path.name for s in plan.inputs.sources if s.status == "excluded"),
        tuple(s.path.name for s in plan.inputs.sources if s.status == "failed"),
        manual_items(plan),
        has_formulas,
    )


def _status(plan: Plan) -> str:
    if manual_items(plan) or any(s.status in ("failed", "excluded") for s in plan.inputs.sources):
        return "partial"
    return "completed"


def _no_progress(stage: str, current: int, total: int) -> None:
    return None


def save_result(
    plan: Plan,
    approval: Approval,
    *,
    progress: Progress = _no_progress,
    cancel: Cancel | None = None,
) -> ResultPackage:
    """api 3절 ①~⑦. 쓰기 시작 전의 실패는 예외, 그 뒤는 ResultPackage(status="failed"|"cancelled")."""
    # ① 사전 확인(예외)
    if approval.plan_hash != plan_hash(
        plan, approval.output_path, approval.evidence_path, approval.manual_path
    ):
        raise ServiceError(
            "APPROVAL_INVALID", "승인한 뒤 계획이 바뀌었습니다. 다시 미리보기한 뒤 저장하세요."
        )
    snapshot_prints = {
        f.file_id: f.sha256 or "" for f in (*plan.inputs.sources, plan.inputs.target)
    }
    if snapshot_prints != dict(approval.input_fingerprints):
        raise ServiceError(
            "APPROVAL_INVALID", "승인과 입력 파일 지문이 다릅니다. 다시 미리보기하세요."
        )
    if not _disk_fingerprints_match(plan, approval):
        raise ServiceError(
            "APPROVAL_INVALID", "미리보기 뒤 입력 파일이 바뀌었습니다. 다시 미리보기하세요."
        )
    _check_paths(plan, [approval.output_path, approval.evidence_path, approval.manual_path])
    return _save(plan, approval, progress, cancel)


def save_check(
    plan: Plan, *, progress: Progress = _no_progress, cancel: Cancel | None = None
) -> ResultPackage:
    """값 검토: XLSX를 만들지 않고 확인필요 파일과 근거 JSON만 공개한다(④'·④·⑥'·⑦)."""
    if plan.rule.work_type != "check":
        raise ServiceError("APPROVAL_INVALID", "값 검토 계획이 아닙니다.")
    approval = approve(plan)
    return save_result(plan, approval, progress=progress, cancel=cancel)


def _save(
    plan: Plan, approval: Approval, progress: Progress, cancel: Cancel | None
) -> ResultPackage:
    folder = approval.evidence_path.parent
    temps: list[Path] = []
    published: list[tuple[Path, str]] = []
    checks: tuple[Check, ...] = ()
    snapshot = plan.inputs.snapshot("target")
    has_formulas = b"<f" in snapshot  # 안내용. 정확한 판정은 patch가 workbook.xml에 표시한다

    def result(status, error=None, output_sha=None, evidence_sha=None, cleanup=()):
        done = status in ("completed", "partial")
        return ResultPackage(
            plan.run_id,
            status,
            approval.output_path if done else None,
            output_sha if done else None,
            approval.evidence_path if done else None,
            evidence_sha if done else None,
            approval.manual_path if done else None,
            checks,
            _summary(plan, has_formulas),
            error,
            tuple(cleanup),
        )

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    try:
        output_sha = manual_sha = None
        # ② 최소 변경 작성 ③ 재열기·비교 검증
        if approval.output_path is not None:
            progress("결과 작성", 1, 4)
            writes = {
                i.target_ref: i.final_value
                for i in plan.items
                if i.change_id in set(approval.approved_change_ids)
            }
            try:
                patched = patch_xlsx(snapshot, plan.sheet, writes)
            except PatchError as exc:
                return result("failed", f"WRITE_FAILED: {exc}")
            temps.append(_write_temp(folder, patched, ".xlsx"))
            progress("결과 검증", 2, 4)
            checks = compare_packages(snapshot, patched, plan.sheet, writes)
            if any(not c.passed for c in checks):
                return result("failed", "VERIFY_FAILED: 결과 파일 검증에 실패했습니다.")
            output_sha = _sha(patched)
        if cancelled():
            return result("cancelled", "CANCELLED")
        status = _status(plan)
        # ④' 확인필요 ④ 근거
        if approval.manual_path is not None:
            text = manual_text(
                plan, approval.output_path.name if approval.output_path else None
            ).encode("utf-8")
            temps.append(_write_temp(folder, text, ".txt"))
            manual_sha = _sha(text)
        evidence = evidence_json(plan, approval, checks, status, output_sha, manual_sha)
        temps.append(_write_temp(folder, evidence, ".json"))
        evidence_sha = _sha(evidence)
        # ⑤ 공개 직전 확인
        progress("입력 재확인", 3, 4)
        if not _disk_fingerprints_match(plan, approval):
            return result("failed", "INPUT_CHANGED: 저장 중에 입력 파일이 바뀌었습니다.")
        if cancelled():
            return result("cancelled", "CANCELLED")
        # ⑥ XLSX → ⑥' TXT → ⑦ JSON(커밋 기록). ⑥부터는 취소를 받지 않는다
        progress("공개", 4, 4)
        order = []
        index = 0
        if approval.output_path is not None:
            order.append((temps[index], approval.output_path, output_sha))
            index += 1
        if approval.manual_path is not None:
            order.append((temps[index], approval.manual_path, manual_sha))
            index += 1
        order.append((temps[index], approval.evidence_path, evidence_sha))
        for temp, final, sha in order:
            try:
                publish_new(temp, final)
            except OSError as exc:
                cleanup = [path for path, digest in published if not _remove_if_same(path, digest)]
                return result("failed", f"PUBLISH_FAILED: {type(exc).__name__}", cleanup=cleanup)
            published.append((final, sha))
        return result(status, None, output_sha, evidence_sha)
    except OSError as exc:
        cleanup = [path for path, digest in published if not _remove_if_same(path, digest)]
        return result("failed", f"WRITE_FAILED: {type(exc).__name__}", cleanup=cleanup)
    finally:
        for temp in temps:
            with contextlib.suppress(OSError):
                temp.unlink()
