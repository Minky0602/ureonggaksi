"""Match records to target rows and judge every cell with the fixed criteria (PRD 6.1, api.md 2.6).

analyze() writes nothing. It applies the seven criteria in order and the first one that fails
becomes the cell's reason. A cell enters the "done" area only when all seven hold.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from openpyxl.utils import get_column_letter

from .errors import Pairs, ServiceError
from .inputs import Inputs, inspect_source
from .manual import ManualItem, action_of, describe, kind_of, sort_key
from .models import CellValue
from .normalize import normalize
from .records import Record, extract_records
from .rules import RuleSet, WorkflowVersion, canonical_json, rule_hash, validate_rule
from .xlsx import TargetCell, inspect_target

ItemStatus = Literal["confirmed", "needs_review", "failed", "excluded", "unchanged"]
Area = Literal["done", "manual", "excluded"]


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


def _short_hash(parts: list[str]) -> str:
    return hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()[:16]


def _same(a: CellValue | None, b: CellValue) -> bool:
    return a is not None and (a.kind, a.value) == (b.kind, b.value)


@dataclass
class _Judged:
    record: Record
    field_name: str
    evidence: Evidence
    proposed: CellValue
    reason_code: str = ""
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
    column_index = {name: index + 1 for index, name in enumerate(view.columns)}

    # 대상 키 → 행 목록. 키가 빈 행은 세지 않는다(합계 행 등)
    rows_by_key: dict[str, list[int]] = {}
    key_of_row: dict[int, str] = {}
    for row in range(target.data_rows[0], target.data_rows[1] + 1):
        cell = cells.get(f"{get_column_letter(key_col)}{row}")
        key_text = cell.value.value if cell else ""
        if key_text:
            rows_by_key.setdefault(key_text, []).append(row)
            key_of_row[row] = key_text

    # 레코드 추출
    sources = [s for s in inputs.active_sources if scope == "all" or s.file_id in scope.file_ids]
    records: list[Record] = []
    for index, source in enumerate(sources, start=1):
        if cancel.is_set():
            raise ServiceError("CANCELLED", "분석을 취소했습니다.")
        progress("문서 읽기", index, len(sources))
        found = extract_records(inspect_source(inputs, source.file_id), source.file_id, ruleset)
        if scope != "all":
            found = found[: max(scope.max_records - len(records), 0)]
        records.extend(found)

    judged = [
        _judge(record, field_name, ruleset, rows_by_key, cells, column_index, key_col)
        for record in records
        for field_name in [f.name for f in ruleset.fields if f.name != ruleset.record.key_field]
    ]
    items = _merge(judged, ruleset, cells, column_index)
    # 문서가 연결된 행: 확정된 행과 복수 후보 행 모두. 후보로라도 문서가 있는 행은 "문서가 없는 행"이 아니다
    matched_rows = {j.target_row for j in judged if j.target_row is not None} | {
        c.row for j in judged for c in j.candidates
    }
    unmatched = tuple(
        (row, key) for row, key in sorted(key_of_row.items()) if row not in matched_rows
    )
    counts = PlanCounts(
        files_ok=len(sources),
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
    )


def _judge(record, field_name, ruleset, rows_by_key, cells, column_index, key_col) -> _Judged:
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
    evidence = Evidence(
        _short_hash(
            [record.file_id, record.record_key, field_name, value.location if value else ""]
        ),
        record.file_id,
        record.sha256,
        record.record_key,
        value.location if value else "",
        raw,
        proposed,
        normalized.transforms,
    )
    judged = _Judged(record, field_name, evidence, proposed)
    # ② 값이 한 곳에서만 찾아졌다 ③ 변환이 모호하지 않다
    if value is None or value.status == "missing":
        judged.reason_code = "missing_value"
    elif value.status == "ambiguous":
        judged.reason_code = "ambiguous_field"
    elif value.status == "tracked":
        judged.reason_code = "tracked_change"
    elif normalized.reason_code:
        judged.reason_code = normalized.reason_code
    # ④ 대상 행이 키의 정확 일치로 정확히 하나다
    rows = rows_by_key.get(record.key, []) if record.key else []
    if len(rows) == 1:
        judged.target_row = rows[0]
    elif rows:
        judged.candidates = tuple(
            TargetCandidate(
                row,
                f"{get_column_letter(key_col)}{row}",
                cells[f"{get_column_letter(key_col)}{row}"].value,
            )
            for row in rows
        )
        judged.reason_code = judged.reason_code or "multi_target"
    else:
        judged.reason_code = judged.reason_code or "no_target"
    return judged


def _target_cell(
    judged: _Judged, ruleset, cells, column_index
) -> tuple[str | None, TargetCell | None]:
    if judged.target_row is None:
        return None, None
    column = column_index[ruleset.field(judged.field_name).target_column]
    address = f"{get_column_letter(column)}{judged.target_row}"
    return address, cells.get(address)


def _merge(judged_list, ruleset, cells, column_index) -> list[ChangeItem]:
    # 같은 (키, 항목)을 노리는 레코드끼리 묶는다. 같은 값이면 근거를 모아 한 번 쓰고, 다르면 충돌이다(⑦)
    groups: dict[tuple[str, str], list[_Judged]] = {}
    for judged in judged_list:
        groups.setdefault((judged.record.record_key, judged.field_name), []).append(judged)
    items = []
    for (record_key, field_name), members in groups.items():
        first = members[0]
        address, target_cell = _target_cell(first, ruleset, cells, column_index)
        evidence = tuple(m.evidence for m in members)
        reason_code = first.reason_code
        proposed = first.proposed
        if not reason_code and target_cell is not None:
            # ⑤ 막힌 칸
            if target_cell.blocked:
                reason_code = f"blocked_{target_cell.blocked}"
            # ⑦ 같은 칸에 다른 값을 쓰려는 원본
            elif any(m.reason_code or not _same(m.proposed, proposed) for m in members[1:]):
                reason_code = "conflict"
        elif not reason_code and target_cell is None:
            reason_code = "no_target"  # 대상 범위 밖(일어나지 않아야 하지만 추측하지 않는다)
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
        if reason_code in ("missing_value", "tracked_change"):
            status = "failed"  # 값 자체를 못 읽었다(FR-20의 '실패'). 나머지 이유는 '검토 필요'
        reason = (
            describe(
                reason_code,
                previous=previous.display() if previous else "",
                proposed=proposed.display(),
                key=record_key,
                candidates=", ".join(c.address for c in first.candidates),
                sources=" · ".join(
                    f"{m.evidence.file_id} {m.proposed.display() or m.evidence.raw_text or '(없음)'}"
                    for m in members
                ),
                raw=first.evidence.raw_text,
            )
            if reason_code
            else ""
        )
        items.append(
            ChangeItem(
                _short_hash([record_key, field_name]),
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
    items.sort(key=lambda i: (i.target_ref or "~", i.record_key, i.field))
    return items


def manual_items(plan: Plan) -> tuple[ManualItem, ...]:
    """직접 처리 영역 + 실패 원본 + 문서가 없는 행. 이유·할 일은 5.1절 표의 문장 틀로 만든다."""
    items: list[ManualItem] = []
    for item in plan.items:
        if item.area != "manual":
            continue
        kind = kind_of(item.reason_code)
        where = f"{plan.sheet}!{item.target_ref}" if item.target_ref else _evidence_where(item)
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
    for row, key in plan.unmatched_rows:
        items.append(
            ManualItem(
                "no_document",
                f"{plan.sheet}!{row}행",
                describe("target_without_source", key=key),
                action_of("no_document"),
                "target_without_source",
                None,
            )
        )
    return tuple(sorted(items, key=sort_key))


def _evidence_where(item: ChangeItem) -> str:
    first = item.evidence[0] if item.evidence else None
    if first is None:
        return item.record_key
    return f"{first.file_id} {first.location or '레코드 ' + first.record_key}"
