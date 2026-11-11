"""Rule v3: record boundary, fields, target mapping, work type, policy answers (api.md 2.4, 4, 6).

Direct selection, the local AI and imports all produce the same RuleSet. Rules never contain
document paths or document text; they are matched to new files at run time (PRD FR-08).
"""

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import Pairs, ServiceError
from .normalize import UNITS
from .storage import atomic_write, local_path
from .workflow import Rule, Workflow, WorkflowError

if TYPE_CHECKING:
    from .inputs import Inputs
    from .xlsx import TargetView

SCHEMA_VERSION = 3
MAX_RULE_BYTES = 1_048_576
MAX_FIELDS = 100
SKIP = "__skip__"  # 질문을 건너뛴 답. 저장해 두어 재실행 때 다시 묻지 않는다(AT-21)
POLICY_VALUES = {
    "default_unit": set(UNITS),
    "rounding": {"down", "up", "half_up"},
    "date_order": {"ymd", "dmy", "mdy"},
}
VALUE_TYPES = {"text", "number", "money", "date"}
DOCUMENT_KINDS = {"label", "cell", "regex"}  # 문서 하나가 레코드일 때의 항목 찾기
COLUMN_KIND = "column"  # 표 행이 레코드일 때: query = 그 표의 열 머리글 이름
IDENTIFIER = re.compile(r"[0-9a-f]{12}\Z")
UNREADABLE = "규칙 파일을 읽을 수 없거나 형식이 올바르지 않습니다."


class RuleError(ValueError):
    """규칙의 형식 오류. 뜻이 맞는지(열이 있는지 등)는 validate_rule이 RuleIssue로 돌려준다."""


def _check_name(value, label: str, limit: int = 100) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > limit
        or "\x00" in value
    ):
        raise RuleError(f"{label}은(는) 양끝 공백 없이 1~{limit}자여야 합니다.")


@dataclass(frozen=True)
class FieldRule:
    """기존 Rule(label·cell·regex) 또는 표 열(column) + 값 종류·원문 단위·대상 열."""

    name: str
    kind: str
    query: str = ""
    section: str = ""
    table: int = 1
    row: int = 1
    col: int = 1
    direction: str = "right"
    group: int = 1
    required: bool = True
    value_type: str = "text"
    unit: str = ""  # 규칙이 밝힌 원문 단위(표 머리의 "단위: 천원"). money에만 쓴다
    target_column: str = (
        ""  # 대상 열 **이름**. 열 문자가 아니라 이름이어야 열이 끼어들어도 맞는 칸에 쓴다
    )

    def __post_init__(self):
        if self.kind == COLUMN_KIND:
            _check_name(self.name, "항목 이름")
            if not isinstance(self.query, str) or not self.query.strip() or len(self.query) > 200:
                raise RuleError("표 열 항목에는 열 머리글 이름을 적어야 합니다.")
        else:
            try:
                self.to_rule()  # 기존 추출 규칙의 형식 검사를 그대로 받는다
            except WorkflowError as exc:
                raise RuleError(str(exc)) from exc
        if self.value_type not in VALUE_TYPES:
            raise RuleError("값 종류는 text·number·money·date 가운데 하나여야 합니다.")
        if not isinstance(self.unit, str) or not isinstance(self.target_column, str):
            raise RuleError("단위와 대상 열은 글자여야 합니다.")
        if self.unit and self.unit not in UNITS:
            raise RuleError("단위는 원·천원·만원·백만원·억원 가운데 하나여야 합니다.")
        if len(self.target_column) > 200 or "\x00" in self.target_column:
            raise RuleError("대상 열 이름이 유효하지 않습니다.")

    def to_rule(self) -> Rule:
        if self.kind == COLUMN_KIND:
            raise RuleError("표 열 항목은 문서 추출 규칙으로 바꿀 수 없습니다.")
        return Rule(
            self.name,
            self.kind,
            self.query,
            self.section,
            self.table,
            self.row,
            self.col,
            self.direction,
            self.group,
            self.required,
        )


@dataclass(frozen=True)
class RecordRule:
    """사업 레코드 경계. document: 문서 하나가 레코드 하나. table_rows: 지정 표의 헤더 아래 각 행이 레코드."""

    kind: str = "document"
    key_field: str = ""  # 레코드를 대상 행과 잇는 항목 이름
    section: str = ""  # table_rows일 때 표가 있는 구역. 비면 첫 번째로 맞는 표
    table: int = 1
    header_row: int = 1

    def __post_init__(self):
        if self.kind not in {"document", "table_rows"}:
            raise RuleError("레코드 경계는 document 또는 table_rows여야 합니다.")
        if (
            not isinstance(self.key_field, str)
            or len(self.key_field) > 100
            or self.key_field != self.key_field.strip()
        ):
            raise RuleError("키 항목 이름이 유효하지 않습니다.")
        for name in ("table", "header_row"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 1_000_000:
                raise RuleError(f"{name} 번호가 유효하지 않습니다.")
        if self.section and not re.fullmatch(r"Contents/section\d+\.xml", self.section):
            raise RuleError("구역은 Contents/section0.xml 형식이어야 합니다.")


@dataclass(frozen=True)
class TargetRule:
    sheet: str = ""
    header_row: int = 1
    data_rows: tuple[int, int] = (2, 1)  # (header_row+1, header_row)는 "데이터 없음"
    key_column: str = ""  # 대상 키 열 이름

    def __post_init__(self):
        if not isinstance(self.sheet, str) or not isinstance(self.key_column, str):
            raise RuleError("시트와 키 열은 글자여야 합니다.")
        if type(self.header_row) is not int or not 1 <= self.header_row <= 1_048_576:
            raise RuleError("헤더 행 번호가 유효하지 않습니다.")
        rows = self.data_rows
        if (
            not isinstance(rows, tuple)
            or len(rows) != 2
            or any(type(r) is not int for r in rows)
            or rows[0] <= self.header_row
            or rows[1] < rows[0] - 1
            or rows[1] > 1_048_576
        ):
            raise RuleError("데이터 범위는 헤더 행 아래의 올바른 구간이어야 합니다.")


@dataclass(frozen=True)
class RuleSet:
    record: RecordRule
    fields: tuple[FieldRule, ...]
    target: TargetRule
    work_type: str = "fill"  # fill: 빈칸 채우기 / check: 값 검토
    policy: Pairs = ()  # 질문으로 정한 값: default_unit·rounding·date_order. 답이 SKIP이면 건너뛴 것

    def __post_init__(self):
        if not isinstance(self.fields, tuple) or not 1 <= len(self.fields) <= MAX_FIELDS:
            raise RuleError(f"항목은 1~{MAX_FIELDS}개여야 합니다.")
        if not all(isinstance(f, FieldRule) for f in self.fields):
            raise RuleError("항목 형식이 올바르지 않습니다.")
        if len({f.name for f in self.fields}) != len(self.fields):
            raise RuleError("항목 이름은 중복될 수 없습니다.")
        if self.work_type not in {"fill", "check"}:
            raise RuleError("작업 종류는 fill 또는 check여야 합니다.")
        if not isinstance(self.policy, tuple) or not all(
            isinstance(p, tuple) and len(p) == 2 and all(isinstance(x, str) for x in p)
            for p in self.policy
        ):
            raise RuleError("질문 답 형식이 올바르지 않습니다.")
        if len({k for k, _ in self.policy}) != len(self.policy):
            raise RuleError("질문 답이 중복되었습니다.")
        for key, value in self.policy:
            if key not in POLICY_VALUES or (value != SKIP and value not in POLICY_VALUES[key]):
                raise RuleError(f"질문 답 {key}의 값이 유효하지 않습니다.")

    def policy_value(self, key: str) -> str:
        """답 값. 건너뛴(SKIP) 답은 빈 글자다."""
        value = next((v for k, v in self.policy if k == key), "")
        return "" if value == SKIP else value

    def answered(self, key: str) -> bool:
        """이미 물었는가(건너뛴 것도 물은 것이다). 재실행 때 다시 묻지 않는 기준."""
        return any(k == key for k, _ in self.policy)

    def field(self, name: str) -> FieldRule | None:
        return next((f for f in self.fields if f.name == name), None)


def canonical_json(data) -> str:
    """해시 계약(api.md 4절): 키 정렬, 구분자 `,`·`:`, ensure_ascii=False."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rule_to_dict(rule: RuleSet) -> dict:
    data = asdict(rule)
    data["target"]["data_rows"] = list(rule.target.data_rows)
    data["policy"] = [list(p) for p in sorted(rule.policy)]  # 순서에 뜻이 없으므로 정렬(해시 계약)
    return data


def rule_from_dict(data: dict) -> RuleSet:
    try:
        target = dict(data["target"])
        target["data_rows"] = tuple(target["data_rows"])
        return RuleSet(
            RecordRule(**data["record"]),
            tuple(FieldRule(**f) for f in data["fields"]),
            TargetRule(**target),
            data.get("work_type", "fill"),
            tuple(tuple(p) for p in data.get("policy", [])),
        )
    except RuleError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RuleError("규칙 JSON의 형식이 올바르지 않습니다.") from exc


def rule_hash(rule: RuleSet) -> str:
    return hashlib.sha256(canonical_json(rule_to_dict(rule)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkflowVersion:
    workflow_id: str
    name: str
    revision: int
    schema_version: int
    rule: RuleSet
    rule_hash: str
    saved_at: str
    request: str = (
        ""  # 이 규칙을 만든 한 줄 요청. 사용자가 쓴 문장이므로 저장 전 화면이 안내한다(P04)
    )


@dataclass(frozen=True)
class WorkflowSummary:
    workflow_id: str
    name: str
    latest_revision: int
    saved_at: str


@dataclass(frozen=True)
class RuleIssue:
    path: str  # 예: "fields[1].unit"
    code: str
    message: str


@dataclass(frozen=True)
class RuleDraft:
    draft_id: str
    origin: str  # manual · ai · imported
    rule: RuleSet | None  # 형식 오류면 None
    questions: tuple[str, ...] = ()
    issues: tuple[RuleIssue, ...] = ()


def _column_issue(path: str, name: str, columns: tuple[str, ...], label: str) -> RuleIssue | None:
    count = columns.count(name)
    if count == 1:
        return None
    code = f"{label}_unknown" if count == 0 else "column_ambiguous"
    return RuleIssue(path, code, "대상 열이 헤더에 없거나 둘 이상입니다.")


def validate_rule(rule: RuleSet, inputs: "Inputs", view: "TargetView") -> tuple[RuleIssue, ...]:
    """뜻 검사. 예외가 아니라 목록으로 돌려줘 자세히 화면이 한 번에 보여 준다.

    view는 rule.target의 시트·헤더 행으로 만든 미리보기여야 한다. 다르면 view_mismatch로 알린다.
    """
    issues: list[RuleIssue] = []
    names = {f.name for f in rule.fields}
    if not rule.record.key_field:
        issues.append(RuleIssue("record.key_field", "key_field_missing", "키 항목을 정해 주세요."))
    elif rule.record.key_field not in names:
        issues.append(
            RuleIssue("record.key_field", "key_field_unknown", "키 항목이 항목 목록에 없습니다.")
        )
    wanted = DOCUMENT_KINDS if rule.record.kind == "document" else {COLUMN_KIND}
    for index, item in enumerate(rule.fields):
        if item.kind not in wanted:
            issues.append(
                RuleIssue(
                    f"fields[{index}].kind",
                    "kind_mismatch",
                    "문서 레코드는 label·cell·regex, 표 행 레코드는 column 항목만 쓸 수 있습니다.",
                )
            )
    if not rule.target.sheet:
        issues.append(RuleIssue("target.sheet", "sheet_missing", "대상 시트를 정해 주세요."))
    elif rule.target.sheet not in view.sheets:
        issues.append(
            RuleIssue("target.sheet", "sheet_unknown", "대상 시트가 통합문서에 없습니다.")
        )
    elif rule.target.sheet != view.sheet or rule.target.header_row != view.header_row:
        issues.append(
            RuleIssue(
                "target", "view_mismatch", "미리보기를 규칙의 시트·헤더 행으로 다시 만들어 주세요."
            )
        )
    if not rule.target.key_column:
        issues.append(
            RuleIssue("target.key_column", "key_column_missing", "대상 키 열을 정해 주세요.")
        )
    else:
        issue = _column_issue(
            "target.key_column", rule.target.key_column, view.columns, "key_column"
        )
        if issue:
            issues.append(issue)
    if rule.target.data_rows[0] <= view.header_row:
        issues.append(
            RuleIssue(
                "target.data_rows", "data_rows_invalid", "데이터 범위는 헤더 행 아래여야 합니다."
            )
        )
    seen_columns: dict[str, str] = {}
    value_fields = 0
    for index, item in enumerate(rule.fields):
        path = f"fields[{index}]"
        if item.unit and item.value_type != "money":
            issues.append(
                RuleIssue(f"{path}.unit", "unit_not_money", "단위는 금액 항목에만 둡니다.")
            )
        if item.name == rule.record.key_field:
            if item.value_type != "text":
                issues.append(
                    RuleIssue(
                        f"{path}.value_type",
                        "key_not_text",
                        "키 항목은 글자로 다룹니다(앞자리 0 보존).",
                    )
                )
            if item.target_column:
                issues.append(
                    RuleIssue(
                        f"{path}.target_column",
                        "key_target_ignored",
                        "키 항목은 대상 열에 쓰지 않습니다.",
                    )
                )
            continue
        value_fields += 1
        if not item.target_column:
            issues.append(
                RuleIssue(
                    f"{path}.target_column", "target_column_missing", "대상 열을 정해 주세요."
                )
            )
            continue
        issue = _column_issue(
            f"{path}.target_column", item.target_column, view.columns, "target_column"
        )
        if issue:
            issues.append(issue)
        elif item.target_column == rule.target.key_column:
            issues.append(
                RuleIssue(f"{path}.target_column", "target_is_key", "키 열에는 값을 쓰지 않습니다.")
            )
        elif item.target_column in seen_columns:
            issues.append(
                RuleIssue(
                    f"{path}.target_column",
                    "target_column_duplicate",
                    f"'{seen_columns[item.target_column]}' 항목도 같은 열에 씁니다.",
                )
            )
        else:
            seen_columns[item.target_column] = item.name
    if value_fields == 0:
        issues.append(
            RuleIssue("fields", "no_value_fields", "키 말고 쓸 항목이 하나 이상 있어야 합니다.")
        )
    if not inputs.active_sources:
        issues.append(RuleIssue("inputs", "no_sources", "읽기에 성공한 원본이 없습니다."))
    return tuple(issues)


def default_store_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base).expanduser().absolute() / "ureonggaksi" / "rules"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _load_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError) as exc:
        raise RuleError(UNREADABLE) from exc


class RuleStore:
    """로컬 폴더 rules/<workflow_id>/<revision>.json. 같은 rule_hash는 다시 저장하지 않는다(멱등).

    깨진 파일 하나가 목록·저장을 막지 않도록 읽기 실패는 problems에 모아 두고 건너뛴다(검수 M-1).
    """

    def __init__(self, root: Path | None = None):
        self.root = local_path(root if root is not None else default_store_root())
        self.problems: list[tuple[str, str]] = []  # (파일 경로, 이유). 화면이 알리는 용도

    def _workflow_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.iterdir() if p.is_dir() and IDENTIFIER.fullmatch(p.name))

    def _revisions(self, workflow_id: str) -> list[int]:
        folder = self.root / workflow_id
        if not folder.is_dir():
            return []
        return sorted(int(p.stem) for p in folder.glob("*.json") if p.stem.isdigit())

    def _read(self, path: Path) -> WorkflowVersion:
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_RULE_BYTES + 1)
        except OSError as exc:
            raise RuleError(UNREADABLE) from exc
        if len(raw) > MAX_RULE_BYTES:
            raise RuleError("규칙 파일은 1 MiB 이하여야 합니다.")
        data = _load_json(raw)
        try:
            if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
                raise RuleError("지원하지 않는 규칙 파일 버전입니다.")
            workflow_id, revision = data["workflow_id"], data["revision"]
            # 파일 안의 식별자는 폴더·파일 이름과 같아야 한다. 아니면 저장 폴더 밖을 가리킬 수 있다(검수 M-2)
            if (
                not isinstance(workflow_id, str)
                or not IDENTIFIER.fullmatch(workflow_id)
                or workflow_id != path.parent.name
                or type(revision) is not int
                or str(revision) != path.stem
            ):
                raise RuleError("규칙 파일의 식별자가 저장 위치와 다릅니다.")
            _check_name(data["name"], "규칙 이름")
            rule = rule_from_dict(data["rule"])
            version = WorkflowVersion(
                workflow_id,
                data["name"],
                revision,
                SCHEMA_VERSION,
                rule,
                data["rule_hash"],
                str(data["saved_at"]),
                str(data.get("request", "")),
            )
        except RuleError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleError(UNREADABLE) from exc
        if version.rule_hash != rule_hash(rule):
            raise RuleError("규칙 파일의 지문이 내용과 다릅니다. 파일이 바뀌었습니다.")
        return version

    def _try_read(self, path: Path) -> WorkflowVersion | None:
        try:
            return self._read(path)
        except RuleError as exc:
            entry = (str(path), str(exc))
            if entry not in self.problems:
                self.problems.append(entry)
            return None

    def _versions(self, workflow_id: str | None = None):
        folders = [self.root / workflow_id] if workflow_id else self._workflow_dirs()
        for folder in folders:
            for revision in self._revisions(folder.name):
                version = self._try_read(folder / f"{revision}.json")
                if version is not None:
                    yield version

    def list(self) -> tuple[WorkflowSummary, ...]:
        summaries = []
        for folder in self._workflow_dirs():
            latest = None
            for version in self._versions(folder.name):
                latest = version  # revision 오름차순이라 마지막이 최신
            if latest is not None:
                summaries.append(
                    WorkflowSummary(
                        latest.workflow_id, latest.name, latest.revision, latest.saved_at
                    )
                )
        return tuple(sorted(summaries, key=lambda s: (s.saved_at, s.workflow_id), reverse=True))

    def load(self, workflow_id: str, revision: int | None = None) -> WorkflowVersion:
        if not isinstance(workflow_id, str) or not IDENTIFIER.fullmatch(workflow_id):
            raise RuleError("규칙 식별자가 유효하지 않습니다.")
        revisions = self._revisions(workflow_id)
        if not revisions or (revision is not None and revision not in revisions):
            raise RuleError("저장된 규칙을 찾을 수 없습니다.")
        chosen = revisions[-1] if revision is None else revision
        return self._read(self.root / workflow_id / f"{chosen}.json")

    def save(
        self, name: str, rule: RuleSet, base: WorkflowVersion | None = None, request: str = ""
    ) -> WorkflowVersion:
        """같은 지문이 있으면 그 버전을 돌려준다. base가 있으면 그 작업 안에서만 찾는다(검수 M-3).

        base 없이 새로 저장할 때만 전체에서 찾아, 다른 작업의 수정본이 그 작업의 revision을 가로채지 않게 한다.
        """
        _check_name(name, "규칙 이름")
        if not isinstance(request, str) or len(request) > 500 or "\x00" in request:
            raise RuleError("요청 문장은 500자 이하여야 합니다.")
        if base is not None and not IDENTIFIER.fullmatch(base.workflow_id):
            raise RuleError("규칙 식별자가 유효하지 않습니다.")
        digest = rule_hash(rule)
        for existing in self._versions(base.workflow_id if base else None):
            if existing.rule_hash == digest:
                return existing  # 멱등: 같은 규칙은 새로 저장하지 않는다(FR-08)
        if base is None:
            workflow_id, revision = uuid.uuid4().hex[:12], 1
        else:
            workflow_id = base.workflow_id
            revision = (self._revisions(workflow_id) or [0])[-1] + 1
        version = WorkflowVersion(
            workflow_id, name, revision, SCHEMA_VERSION, rule, digest, _now(), request
        )
        data = {
            "schema_version": SCHEMA_VERSION,
            "workflow_id": workflow_id,
            "name": name,
            "revision": revision,
            "request": request,
            "saved_at": version.saved_at,
            "rule_hash": digest,
            "rule": rule_to_dict(rule),
        }
        folder = self.root / workflow_id
        folder.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        atomic_write(folder / f"{revision}.json", lambda p: p.write_text(text, encoding="utf-8"))
        return version

    def delete(self, workflow_id: str) -> None:
        """규칙 폴더만 지운다. 결과 파일은 지우지 않는다."""
        if not isinstance(workflow_id, str) or not IDENTIFIER.fullmatch(workflow_id):
            raise RuleError("규칙 식별자가 유효하지 않습니다.")
        folder = self.root / workflow_id
        if not folder.is_dir():
            raise RuleError("저장된 규칙을 찾을 수 없습니다.")
        for path in folder.iterdir():
            if path.is_file():
                path.unlink()  # 중단된 저장의 .tmp 잔여물도 함께
        folder.rmdir()

    def import_file(self, path: Path) -> RuleDraft:
        """외부 v1(추출만)·v3 JSON → 확인 전 초안. 저장하지 않는다. v2 형식은 배포된 적이 없어 받지 않는다."""
        draft_id = uuid.uuid4().hex[:12]

        def failed(code: str, message: str) -> RuleDraft:
            return RuleDraft(draft_id, "imported", None, (), (RuleIssue("file", code, message),))

        try:
            resolved = local_path(path)
            with resolved.open("rb") as stream:
                raw = stream.read(MAX_RULE_BYTES + 1)
        except (OSError, ValueError):
            return failed("unreadable", "파일을 읽을 수 없습니다. 경로와 접근 권한을 확인하세요.")
        if len(raw) > MAX_RULE_BYTES:
            return failed("too_large", "규칙 파일은 1 MiB 이하여야 합니다.")
        try:
            data = _load_json(raw)
        except RuleError:
            return failed("unreadable", "JSON 형식이 올바르지 않습니다.")
        if (
            isinstance(data, dict)
            and data.get("schema_version") == SCHEMA_VERSION
            and "rule" in data
        ):
            try:
                return RuleDraft(draft_id, "imported", rule_from_dict(data["rule"]))
            except RuleError as exc:
                return failed("invalid", str(exc))
        try:
            workflow = Workflow.load(path)
        except WorkflowError as exc:
            return failed("unsupported", str(exc))
        fields = tuple(FieldRule(**asdict(r)) for r in workflow.rules)
        rule = RuleSet(RecordRule("document", ""), fields, TargetRule())
        issues = (
            RuleIssue("record.key_field", "key_field_missing", "키 항목을 정해 주세요."),
            RuleIssue("target", "target_missing", "대상 시트·키 열·항목별 대상 열을 정해 주세요."),
        )
        return RuleDraft(draft_id, "imported", rule, (), issues)


def service_error(exc: RuleError) -> ServiceError:
    return ServiceError("RULE_INVALID", str(exc))
