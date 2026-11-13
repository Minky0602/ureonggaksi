"""Match records to target rows and judge every cell with the fixed criteria (PRD 6.1, api.md 2.6).

analyze() writes nothing. It applies the seven criteria in order and the first one that fails
becomes the cell's reason. A cell enters the "done" area only when all seven hold.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

from openpyxl.utils import get_column_letter

from .errors import Pairs, ServiceError
from .inputs import Inputs, inspect_source
from .manual import ManualItem, action_of, describe, kind_of, sort_key
from .models import CellValue
from .normalize import normalize
from .patch import unsupported_writes
from .records import Record, extract_records
from .rules import RuleSet, WorkflowVersion, canonical_json, rule_hash, validate_rule
from .xlsx import TargetCell, inspect_target

ItemStatus = Literal["confirmed", "needs_review", "failed", "excluded", "unchanged"]
Area = Literal["done", "manual", "excluded"]
UNREADABLE_CODES = (
    "missing_value",
    "ambiguous_field",
    "tracked_change",
    "ambiguous_unit",
    "parse_failed",
    "needs_rounding",
)
FAILED_CODES = {"missing_value", "tracked_change"}  # 값 자체를 못 읽음(FR-20의 '실패')


class Cancel(Protocol):
    def is_set(self) -> bool: ...


Progress = Callable[[str, int, int], None]


class _NeverCancel:
    def is_set(self) -> bool:
        return False


def _no_progress(stage: str, current: int, total: int) -> None:
    return None


@dataclass(frozen=True)
class Sample:
    file_ids: tuple[str, ...]
    max_records: int = 20


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    file_id: str
    sha256: str
    record_key: str
    location: str
    raw_text: str
    extracted: CellValue
    transforms: tuple[str, ...]


@dataclass(frozen=True)
class TargetCandidate:
    row: int
    address: str
    key_value: CellValue


@dataclass(frozen=True)
class ChangeItem:
    change_id: str
    record_key: str
    field: str
    target_ref: str | None
    target_candidates: tuple[TargetCandidate, ...]
    previous: CellValue | None
    proposed: CellValue
    corrected: CellValue | None
    status: ItemStatus
    reason_code: str
    reason: str
    evidence: tuple[Evidence, ...]
    resolved: bool
    write_selected: bool
    review_reason: str | None = None

    @property
    def area(self) -> Area:
        if self.status == "excluded":
            return "excluded"
        if self.status == "unchanged" or self.write_selected:
            return "done"
        return "manual"

    @property
    def final_value(self) -> CellValue:
        return self.corrected if self.corrected is not None else self.proposed


@dataclass(frozen=True)
class PlanCounts:
    files_ok: int
    files_failed: int
    files_excluded: int
    records: int
    cells_write: int
    cells_unchanged: int
    cells_review: int
    cells_failed: int
    cells_excluded: int
    rows_without_source: int


@dataclass(frozen=True)
class Plan:
    run_id: str
    inputs: Inputs
    rule: RuleSet
    rule_hash: str
    rule_ref: WorkflowVersion | None
    scope: Literal["all"] | Sample
    items: tuple[ChangeItem, ...]
    counts: PlanCounts
    sheet: str
    problem_counts: Pairs = ()  # reason_code → 건수(글자). 공통 문제 질문(6.2)은 prepare가 이 수로 판단한다
    unmatched_rows: tuple[tuple[int, str], ...] = field(
        default_factory=tuple
    )  # (행, 키) 문서가 없는 행
    empty_sources: tuple[str, ...] = ()  # 읽혔지만 레코드가 하나도 없는 원본(file_id)
    key_column_letter: str = "A"


def _short_hash(parts: list[str]) -> str:
    return hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()[:16]


def _same(a: CellValue | None, b: CellValue) -> bool:
    return a is not None and (a.kind, a.value) == (b.kind, b.value)


def _address_order(address: str | None) -> tuple:
    """정렬용: 열 글자 → 행 번호(숫자). 'C10'이 'C3' 뒤에 온다."""
    if not address:
        return (99, "~", 0)  # 대상 없는 항목은 맨 뒤
    letters = address.rstrip("0123456789")
    digits = address[len(letters) :]
    return (len(letters), letters, int(digits) if digits.isdigit() else 0)


@dataclass
class _Judged:
    record: Record
    field_name: str
    evidence: Evidence
    proposed: CellValue
    value_code: str = ""  # ②③ 이 레코드의 값을 못 읽은 이유
    target_code: str = ""  # ④ no_target · multi_target
    key_failed: bool = False
    target_row: int | None = None
    candidates: tuple[TargetCandidate, ...] = ()


def analyze(
    inputs: Inputs,
    rule: RuleSet | WorkflowVersion,
    *,
    scope: Literal["all"] | Sample = "all",
    progress: Progress = _no_progress,
    cancel: Cancel | None = None,
) -> Plan:
    """쓰기 없음. 기준 6.1을 칸마다 순서대로 적용한다."""
    cancel = cancel if cancel is not None else _NeverCancel()
    rule_ref = rule if isinstance(rule, WorkflowVersion) else None
    ruleset = rule.rule if isinstance(rule, WorkflowVersion) else rule
    target = ruleset.target
    row_count = max(target.data_rows[1] - target.data_rows[0] + 1, 0)
    view = inspect_target(
        inputs,
        sheet=target.sheet,
        header_row=target.header_row,
        data_rows=target.data_rows,
        preview_rows=row_count,
    )
    issues = validate_rule(ruleset, inputs, view)
    if issues:
        raise ServiceError(
            "RULE_INVALID",
            "규칙이 검증을 통과하지 못했습니다. 자세히에서 고쳐 주세요.",
            tuple((i.path, i.code) for i in issues),
        )
    cells = {c.address: c for c in view.cells}
    key_col = view.columns.index(target.key_column) + 1
    key_letter = get_column_letter(key_col)
    column_index = {name: index + 1 for index, name in enumerate(view.columns)}
    names = {s.file_id: s.path.name for s in inputs.sources}

    # 대상 키 → 행 목록. 양끝 공백만 떼고 글자 그대로 비교한다(문서 쪽도 같다). 키가 빈 행은 세지 않는다
    rows_by_key: dict[str, list[int]] = {}
    key_of_row: dict[int, str] = {}
    for row in range(target.data_rows[0], target.data_rows[1] + 1):
        cell = cells.get(f"{key_letter}{row}")
        key_text = cell.value.value.strip() if cell else ""
        if key_text:
            rows_by_key.setdefault(key_text, []).append(row)
            key_of_row[row] = key_text

    # 레코드 추출
    active = list(inputs.active_sources)
    sources = [s for s in active if scope == "all" or s.file_id in scope.file_ids]
    records: list[Record] = []
    empty_sources: list[str] = []
    for index, source in enumerate(sources, start=1):
        if cancel.is_set():
            raise ServiceError("CANCELLED", "분석을 취소했습니다.")
        progress("문서 읽기", index, len(sources))
        found = extract_records(inspect_source(inputs, source.file_id), source.file_id, ruleset)
        if not found:
            empty_sources.append(source.file_id)  # 읽혔지만 레코드가 없다(표 없음). 소명으로 알린다
        if scope != "all":
            found = found[: max(scope.max_records - len(records), 0)]
        records.extend(found)

    value_fields = [f.name for f in ruleset.fields if f.name != ruleset.record.key_field]
    judged = [
        _judge(record, field_name, ruleset, rows_by_key, cells, key_letter)
        for record in records
        for field_name in value_fields
    ]
    items = _merge(judged, ruleset, cells, column_index, names)
    items = _mark_unsupported(items, inputs, view.sheet)
    # 문서가 연결된 행: 확정된 행과 복수 후보 행 모두. 표본(Sample) 분석에서는 세지 않는다(문서를 다 읽지 않았다)
    matched_rows = {j.target_row for j in judged if j.target_row is not None} | {
        c.row for j in judged for c in j.candidates
    }
    unmatched = (
        tuple((row, key) for row, key in sorted(key_of_row.items()) if row not in matched_rows)
        if scope == "all"
        else ()
    )
    counts = PlanCounts(
        files_ok=len(active),
        files_failed=sum(1 for s in inputs.sources if s.status == "failed"),
        files_excluded=sum(1 for s in inputs.sources if s.status == "excluded"),
        records=len(records),
        cells_write=sum(1 for i in items if i.write_selected),
        cells_unchanged=sum(1 for i in items if i.status == "unchanged"),
        cells_review=sum(1 for i in items if i.status == "needs_review"),
        cells_failed=sum(1 for i in items if i.status == "failed"),
        cells_excluded=sum(1 for i in items if i.status == "excluded"),
        rows_without_source=len(unmatched),
    )
    problems: dict[str, int] = {}
    for item in items:
        if item.area == "manual":
            problems[item.reason_code] = problems.get(item.reason_code, 0) + 1
    return Plan(
        inputs.run_id,
        inputs,
        ruleset,
        rule_hash(ruleset),
        rule_ref,
        scope,
        tuple(items),
        counts,
        view.sheet,
        tuple((code, str(count)) for code, count in sorted(problems.items())),
        unmatched,
        tuple(empty_sources),
        key_letter,
    )


def _judge(record, field_name, ruleset, rows_by_key, cells, key_letter) -> _Judged:
    field_rule = ruleset.field(field_name)
    value = record.value(field_name)
    raw = value.raw_text if value and value.status == "ok" else ""
    normalized = normalize(
        raw,
        field_rule.value_type,
        unit=field_rule.unit,
        default_unit=ruleset.policy_value("default_unit"),
        rounding=ruleset.policy_value("rounding") or None,
        date_order=ruleset.policy_value("date_order") or None,
    )
    proposed = normalized.value if normalized.value is not None else CellValue()
    location = value.location if value else ""
    evidence = Evidence(
        _short_hash([record.file_id, record.record_key, field_name, location]),
        record.file_id,
        record.sha256,
        record.record_key,
        location,
        raw,
        proposed,
        normalized.transforms,
    )
    judged = _Judged(record, field_name, evidence, proposed)
    # 키 항목부터 본다. 키를 못 읽으면 행을 찾을 수 없으므로 키의 코드가 이 칸의 이유다
    key_value = record.value(ruleset.record.key_field)
    if key_value is None or key_value.status != "ok":
        judged.value_code = _status_code(key_value.status if key_value else "missing")
        judged.key_failed = True
        return judged
    # ② 값이 한 곳에서만 찾아졌다 ③ 변환이 모호하지 않다 — 레코드 하나의 판정
    if value is None or value.status != "ok":
        judged.value_code = _status_code(value.status if value else "missing")
    elif normalized.reason_code:
        judged.value_code = normalized.reason_code
    # ④ 대상 행 — 키가 같으면 결과도 같다. 묶음 판정에서 ②③ 다음, ⑤⑦ 앞에 적용한다
    rows = rows_by_key.get(record.key, [])
    if len(rows) == 1:
        judged.target_row = rows[0]
    elif rows:
        judged.candidates = tuple(
            TargetCandidate(row, f"{key_letter}{row}", cells[f"{key_letter}{row}"].value)
            for row in rows
        )
        judged.target_code = "multi_target"
    else:
        judged.target_code = "no_target"
    return judged


def _status_code(status: str) -> str:
    return {"ambiguous": "ambiguous_field", "tracked": "tracked_change"}.get(
        status, "missing_value"
    )


def _target_cell(
    judged: _Judged, ruleset, cells, column_index
) -> tuple[str | None, TargetCell | None]:
    if judged.target_row is None:
        return None, None
    column = column_index[ruleset.field(judged.field_name).target_column]
    address = f"{get_column_letter(column)}{judged.target_row}"
    return address, cells.get(address)


def _group_reason(members: list[_Judged], target_cell: TargetCell | None) -> tuple[str, CellValue]:
    """묶음(같은 키·항목의 레코드들)의 ②~⑤·⑦ 판정과 제안값. 입력 순서에 좌우되지 않는다.

    ②③ 읽힌 값이 하나도 없으면 못 읽은 이유(여럿이면 UNREADABLE_CODES 순서로 앞선 것)
    ④ 대상 행(no_target·multi_target) ⑤ 막힌 칸
    ⑦ 레코드가 여럿이고 하나라도 못 읽었거나 읽힌 값이 서로 다르면 conflict(제안값은 비움)
    """
    readable = [m for m in members if not m.value_code]
    if not readable:
        code = min((m.value_code for m in members), key=UNREADABLE_CODES.index)
        return code, CellValue()
    first = members[0]
    if first.target_code:
        return first.target_code, readable[0].proposed if len(members) == 1 else CellValue()
    if target_cell is None:
        return "no_target", CellValue()  # 대상 범위 밖(일어나지 않아야 하지만 추측하지 않는다)
    agreed = len(readable) == len(members) and all(
        _same(m.proposed, readable[0].proposed) for m in readable[1:]
    )
    if target_cell.blocked:
        # 제안값은 문서들이 모두 같을 때만 보여 준다(입력 순서에 좌우되지 않게, 3차 검수 L-x)
        return f"blocked_{target_cell.blocked}", readable[0].proposed if agreed else CellValue()
    if not agreed:
        return "conflict", CellValue()
    return "", readable[0].proposed


def _merge(judged_list, ruleset, cells, column_index, names) -> list[ChangeItem]:
    # 같은 (키, 항목)을 노리는 레코드끼리 묶는다. 같은 값이면 근거를 모아 한 번 쓰고, 다르면 충돌이다(⑦).
    # 키를 못 읽은 레코드는 자기만의 묶음이다(내부 키가 실제 키 글자와 겹쳐도 섞이지 않게, 검수 L-c)
    groups: dict[tuple[str, str, bool], list[_Judged]] = {}
    for judged in judged_list:
        group = (judged.record.record_key, judged.field_name, judged.key_failed)
        groups.setdefault(group, []).append(judged)
    items = []
    for (record_key, field_name, key_failed), members in groups.items():
        first = members[0]
        address, target_cell = _target_cell(first, ruleset, cells, column_index)
        evidence = tuple(m.evidence for m in members)
        reason_code, proposed = _group_reason(members, target_cell)
        previous = target_cell.value if target_cell is not None else None
        status: ItemStatus = "needs_review"
        write_selected = False
        if not reason_code:
            # ⑥ 작업 종류별 칸 상태
            if ruleset.work_type == "fill":
                if previous is None or previous.kind == "empty":
                    status, write_selected = "confirmed", True
                elif _same(previous, proposed):
                    status = "unchanged"
                else:
                    reason_code = "existing_differs"
            else:
                if previous is None or previous.kind == "empty":
                    reason_code = "missing_in_sheet"
                elif _same(previous, proposed):
                    status = "unchanged"
                else:
                    reason_code = "mismatch"
        if reason_code in FAILED_CODES:
            status = "failed"
        reason = ""
        if reason_code:
            reason = describe(
                reason_code,
                previous=previous.display() if previous else "",
                proposed=proposed.display(),
                key=first.record.key,
                candidates=", ".join(c.address for c in first.candidates),
                sources=" · ".join(
                    f"{names.get(m.evidence.file_id, m.evidence.file_id)} "
                    f"{m.proposed.display() or m.evidence.raw_text or '(없음)'}"
                    for m in members
                ),
                raw=first.evidence.raw_text,
            )
            if key_failed:
                reason = "키 항목을 읽지 못해 행을 찾을 수 없음 — " + reason
        id_parts = [record_key, field_name] + (["key_failed"] if key_failed else [])
        items.append(
            ChangeItem(
                _short_hash(id_parts),
                record_key,
                field_name,
                address,
                first.candidates,
                previous,
                proposed,
                None,
                status,
                reason_code,
                reason,
                evidence,
                resolved=not reason_code,
                write_selected=write_selected,
            )
        )
    items.sort(key=lambda i: (_address_order(i.target_ref), i.record_key, i.field))
    return items


def _mark_unsupported(items: list[ChangeItem], inputs: Inputs, sheet: str) -> list[ChangeItem]:
    """완료 영역의 쓸 칸 가운데 Excel에 그대로 넣을 수 없는 칸(날짜 서식이 아닌 칸의 날짜, 15자리 초과 숫자 등)을
    직접 처리로 돌린다. 미리보기의 완료 영역 = 실제로 쓰는 칸(FR-13). 사용자 결정 2026-11-12."""
    writes = {i.target_ref: i.proposed for i in items if i.write_selected and i.target_ref}
    if not writes:
        return items
    problems = unsupported_writes(inputs.snapshot("target"), sheet, writes)
    if not problems:
        return items
    return [
        replace(
            item,
            status="needs_review",
            reason_code="write_unsupported",
            reason=describe("write_unsupported", detail=problems[item.target_ref]),
            resolved=False,
            write_selected=False,
        )
        if item.write_selected and item.target_ref in problems
        else item
        for item in items
    ]


def manual_items(plan: Plan) -> tuple[ManualItem, ...]:
    """직접 처리 영역 + 실패 원본 + 레코드 없는 원본 + 문서가 없는 행. 이유·할 일은 5.1절 표의 문장 틀로 만든다."""
    names = {s.file_id: s.path.name for s in plan.inputs.sources}
    items: list[ManualItem] = []
    for item in plan.items:
        if item.area != "manual":
            continue
        kind = kind_of(item.reason_code)
        where = (
            f"{plan.sheet}!{item.target_ref}" if item.target_ref else _evidence_where(item, names)
        )
        items.append(
            ManualItem(kind, where, item.reason, action_of(kind), item.reason_code, item.change_id)
        )
    for source in plan.inputs.sources:
        if source.status == "failed":
            items.append(
                ManualItem(
                    "unreadable_file",
                    source.path.name,
                    describe("source_failed", detail=source.reason),
                    action_of("unreadable_file"),
                    "source_failed",
                    None,
                )
            )
    for file_id in plan.empty_sources:
        items.append(
            ManualItem(
                "unreadable_file",
                names.get(file_id, file_id),
                describe("no_records"),
                action_of("unreadable_file"),
                "no_records",
                None,
            )
        )
    for row, key in plan.unmatched_rows:
        items.append(
            ManualItem(
                "no_document",
                f"{plan.sheet}!{plan.key_column_letter}{row}",
                describe("target_without_source", key=key),
                action_of("no_document"),
                "target_without_source",
                None,
            )
        )
    return tuple(sorted(items, key=sort_key))


def _evidence_where(item: ChangeItem, names: dict[str, str]) -> str:
    """대상 칸이 없을 때의 위치: '파일 이름 · 구역 · 표 · 행 열'. 내부 식별자는 쓰지 않는다."""
    first = item.evidence[0] if item.evidence else None
    if first is None:
        return item.record_key
    name = names.get(first.file_id, first.file_id)
    return f"{name} {first.location}" if first.location else name
