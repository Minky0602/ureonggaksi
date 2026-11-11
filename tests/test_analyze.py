"""레코드 추출·키 매칭·6.1 판정·6.3 소명 (PRD FR-09·11·12·21·22, AT-07·18·20, api.md 2.6)."""

import shutil

import pytest
from conftest import cell, para, table, write_hwpx, write_xlsx

from ureonggaksi.analyze import Sample, analyze, manual_items
from ureonggaksi.errors import ServiceError
from ureonggaksi.inputs import inspect_source, open_inputs
from ureonggaksi.models import CellValue
from ureonggaksi.records import extract_records
from ureonggaksi.rules import FieldRule, RecordRule, RuleSet, TargetRule

WON = "원"


def plan_doc(code, amount, extra_rows=(), tracked_amount=False):
    """사업계획서 흉내: '사업코드 | 값' · '사업비 | 값' 표."""
    amount_cell = (
        '<hp:tc><hp:subList><hp:p><hp:run><hp:t><hp:deleteBegin Id="1" TcId="1"/>'
        + amount
        + '<hp:deleteEnd Id="1" TcId="1" paraend="0"/></hp:t></hp:run></hp:p></hp:subList>'
        '<hp:cellAddr rowAddr="1" colAddr="1"/><hp:cellSpan rowSpan="1" colSpan="1"/></hp:tc>'
        if tracked_amount
        else cell(amount, 1, 1)
    )
    rows = [[cell("사업코드", 0, 0), cell(code, 0, 1)], [cell("사업비", 1, 0), amount_cell]]
    for index, (label, value) in enumerate(extra_rows, start=2):
        rows.append([cell(label, index, 0), cell(value, index, 1)])
    return ["<hp:p><hp:run>" + table(rows) + "</hp:run></hp:p>"]


def fill_rule(**overrides):
    base = dict(
        record=RecordRule("document", "사업코드"),
        fields=(
            FieldRule("사업코드", "label", query="사업코드"),
            FieldRule(
                "사업비", "label", query="사업비", value_type="money", target_column="2026 예산"
            ),
        ),
        target=TargetRule("사업취합", 1, (2, 11), "사업코드"),
        work_type="fill",
    )
    base.update(overrides)
    return RuleSet(**base)


SHEET = [
    ["사업코드", "사업명", "2026 예산"],
    ["0012", "정상", None],  # 2: 빈칸 → 쓸 칸
    ["0013", "기존값 다름", 1],  # 3
    ["0014", "복수 행", None],  # 4
    ["0014", "복수 행", None],  # 5
    ["0015", "충돌", None],  # 6
    ["0016", "수식 칸", "=C2*2"],  # 7
    ["0017", "문서가 없는 행", None],  # 8
    ["0018", "이미 같은 값", 3500000],  # 9
    ["0019", "단위 모호", None],  # 10
    ["0020", "변경 추적", None],  # 11
]


@pytest.fixture
def bundle(tmp_path):
    """AT-18 묶음: 정상·기존값 다름·대상 없음·복수 행·충돌·수식 칸·단위 모호·읽기 실패·변경 추적·문서가 없는 행."""
    docs = {
        "정상": plan_doc("0012", "350만원"),
        "기존값다름": plan_doc("0013", "350만원"),
        "복수행": plan_doc("0014", "350만원"),
        "충돌A": plan_doc("0015", "350만원"),
        "충돌B": plan_doc("0015", "400만원"),
        "수식칸": plan_doc("0016", "350만원"),
        "같은값": plan_doc("0018", "3,500,000원"),
        "대상없음": plan_doc("0099", "350만원"),
        "단위모호": plan_doc("0019", "3,500"),
        "변경추적": plan_doc("0020", "350만원", tracked_amount=True),
    }
    paths = [write_hwpx(tmp_path / f"{name}.hwpx", sections) for name, sections in docs.items()]
    broken = tmp_path / "손상.hwpx"
    broken.write_bytes(b"not a zip")
    paths.append(broken)
    target = write_xlsx(tmp_path / "사업취합.xlsx", SHEET)
    return open_inputs(paths, target, tmp_path / "out"), target


def by_key_field(plan):
    return {(i.record_key, i.field): i for i in plan.items}


def test_fill_plan_applies_the_seven_criteria(bundle):
    inputs, _ = bundle
    plan = analyze(inputs, fill_rule())
    items = by_key_field(plan)
    assert items[("0012", "사업비")].write_selected and items[("0012", "사업비")].target_ref == "C2"
    assert items[("0013", "사업비")].reason_code == "existing_differs"
    assert (
        "기존 1" in items[("0013", "사업비")].reason
        and "3,500,000원" in items[("0013", "사업비")].reason
    )
    assert items[("0019", "사업비")].reason_code == "ambiguous_unit"
    assert (items[("0020", "사업비")].reason_code, items[("0020", "사업비")].status) == (
        "tracked_change",
        "failed",
    )
    assert items[("0014", "사업비")].reason_code == "multi_target"
    assert [c.row for c in items[("0014", "사업비")].target_candidates] == [4, 5]
    assert items[("0015", "사업비")].reason_code == "conflict"
    assert len(items[("0015", "사업비")].evidence) == 2
    assert items[("0016", "사업비")].reason_code == "blocked_formula"
    assert items[("0018", "사업비")].status == "unchanged"
    assert items[("0099", "사업비")].reason_code == "no_target"
    assert items[("0099", "사업비")].target_ref is None
    assert plan.unmatched_rows == ((8, "0017"),)
    assert plan.counts.files_failed == 1 and plan.counts.records == 10
    # 완료 영역에는 6.1 기준을 모두 만족한 칸만 있다
    done = [i for i in plan.items if i.area == "done"]
    assert {(i.record_key, i.status) for i in done} == {
        ("0012", "confirmed"),
        ("0018", "unchanged"),
    }
    assert plan.counts.cells_write == 1 and plan.counts.cells_unchanged == 1
    assert dict(plan.problem_counts) == {
        "existing_differs": "1",
        "multi_target": "1",
        "conflict": "1",
        "blocked_formula": "1",
        "no_target": "1",
        "ambiguous_unit": "1",
        "tracked_change": "1",
    }


def test_clean_bundle_writes_exactly_the_empty_matching_cells(tmp_path):
    paths = [
        write_hwpx(tmp_path / "a.hwpx", plan_doc("0012", "350만원")),
        write_hwpx(tmp_path / "b.hwpx", plan_doc("0017", "35,000천원")),
    ]
    target = write_xlsx(tmp_path / "t.xlsx", SHEET)
    plan = analyze(open_inputs(paths, target, tmp_path / "out"), fill_rule())
    items = by_key_field(plan)
    a = items[("0012", "사업비")]
    assert (a.status, a.write_selected, a.target_ref, a.proposed) == (
        "confirmed",
        True,
        "C2",
        CellValue("number", "3500000", WON),
    )
    assert a.previous == CellValue() and a.evidence[0].transforms == ("단위 만원→원 ×10,000(글자)",)
    b = items[("0017", "사업비")]
    assert (b.target_ref, b.proposed.value, b.write_selected) == ("C8", "35000000", True)
    assert plan.counts.cells_write == 2 and plan.counts.rows_without_source == 8
    assert manual_items(plan)[0].kind == "no_document"


def test_manual_items_match_manual_area_one_to_one_and_are_grouped(bundle):
    inputs, _ = bundle
    plan = analyze(inputs, fill_rule())
    manual = manual_items(plan)
    manual_cells = [i for i in plan.items if i.area == "manual"]
    assert (
        len(manual)
        == len(manual_cells) + plan.counts.files_failed + plan.counts.rows_without_source
    )
    kinds = [m.kind for m in manual]
    order = [
        "existing_differs",
        "no_row",
        "source_conflict",
        "unreadable_value",
        "blocked_cell",
        "unreadable_file",
        "no_document",
    ]
    assert kinds == sorted(kinds, key=order.index)  # 6.3절 종류 순서
    assert all(m.where for m in manual)
    broken = next(m for m in manual if m.kind == "unreadable_file")
    assert (broken.where, broken.change_id) == ("손상.hwpx", None)
    missing_doc = next(m for m in manual if m.kind == "no_document")
    assert "0017" in missing_doc.reason and missing_doc.action == "문서가 빠졌는지 확인"
    blocked = next(m for m in manual if m.kind == "blocked_cell")
    assert blocked.where == "사업취합!C7" and blocked.action == "칸 구조를 확인해 직접 입력"


def test_check_plan_reports_match_mismatch_and_missing_in_sheet(tmp_path):
    paths = [
        write_hwpx(tmp_path / "a.hwpx", plan_doc("0018", "350만원")),  # 표 3,500,000 → 일치
        write_hwpx(tmp_path / "b.hwpx", plan_doc("0013", "350만원")),  # 표 1 → 불일치
        write_hwpx(tmp_path / "c.hwpx", plan_doc("0012", "350만원")),  # 표 빈칸 → 표에 빠짐
        write_hwpx(tmp_path / "d.hwpx", plan_doc("0099", "350만원")),  # 대상 없음
        write_hwpx(tmp_path / "e.hwpx", plan_doc("0017", "미정")),  # 판정 불가
    ]
    target = write_xlsx(tmp_path / "t.xlsx", SHEET)
    plan = analyze(open_inputs(paths, target, tmp_path / "out"), fill_rule(work_type="check"))
    items = by_key_field(plan)
    assert items[("0018", "사업비")].status == "unchanged"
    assert items[("0013", "사업비")].reason_code == "mismatch"
    assert items[("0012", "사업비")].reason_code == "missing_in_sheet"
    assert items[("0099", "사업비")].reason_code == "no_target"
    assert items[("0017", "사업비")].reason_code == "parse_failed"
    assert not any(i.write_selected for i in plan.items)  # 값 검토는 쓰지 않는다
    assert plan.counts.cells_unchanged == 1
    kinds = {m.kind for m in manual_items(plan)}
    assert {"check_diff", "no_row", "unreadable_value", "no_document"} <= kinds


def test_keys_match_only_as_exact_text(tmp_path):
    """'0012'와 숫자 12는 다른 키다(FR-11). 안쪽 공백이 달라도 다르다."""
    rows = [["사업코드", "사업명", "2026 예산"], [12, "숫자 키", None], ["00 12", "공백 키", None]]
    target = write_xlsx(tmp_path / "t.xlsx", rows)
    source = write_hwpx(tmp_path / "a.hwpx", plan_doc("0012", "350만원"))
    plan = analyze(
        open_inputs([source], target, tmp_path / "out"),
        fill_rule(target=TargetRule("사업취합", 1, (2, 3), "사업코드")),
    )
    (item,) = plan.items
    assert item.reason_code == "no_target" and "파일 오류 의심" in item.reason


def test_same_value_from_two_sources_is_written_once_with_both_evidences(tmp_path):
    paths = [
        write_hwpx(tmp_path / f"{n}.hwpx", plan_doc("0012", v))
        for n, v in [("a", "350만원"), ("b", "3,500,000원")]
    ]
    target = write_xlsx(tmp_path / "t.xlsx", SHEET)
    plan = analyze(open_inputs(paths, target, tmp_path / "out"), fill_rule())
    (item,) = [i for i in plan.items if i.record_key == "0012"]
    assert item.write_selected and len(item.evidence) == 2
    assert {e.file_id for e in item.evidence} == {"src-1", "src-2"}


def test_analysis_is_deterministic(bundle):
    inputs, _ = bundle
    first, second = analyze(inputs, fill_rule()), analyze(inputs, fill_rule())
    assert [(i.change_id, i.status, i.reason_code) for i in first.items] == [
        (i.change_id, i.status, i.reason_code) for i in second.items
    ]
    assert first.counts == second.counts and first.problem_counts == second.problem_counts


def test_invalid_rule_is_rejected_before_analysis(bundle):
    inputs, _ = bundle
    bad = fill_rule(target=TargetRule("사업취합", 1, (2, 11), "없는 열"))
    with pytest.raises(ServiceError) as info:
        analyze(inputs, bad)
    assert (
        info.value.code == "RULE_INVALID"
        and ("target.key_column", "key_column_unknown") in info.value.detail
    )


def test_sample_scope_and_cancel(bundle):
    inputs, _ = bundle

    class Cancelled:
        def is_set(self):
            return True

    with pytest.raises(ServiceError) as info:
        analyze(inputs, fill_rule(), cancel=Cancelled())
    assert info.value.code == "CANCELLED"
    plan = analyze(inputs, fill_rule(), scope=Sample(("src-1",)))
    assert plan.counts.records == 1 and plan.counts.files_ok == 10  # files_ok는 전체 정상 원본 수


def test_table_rows_records_split_two_projects_in_one_document(tmp_path):
    rows = [
        [cell("코드", 0, 0), cell("사업명", 0, 1), cell("사업비(천원)", 0, 2)],
        [cell("0012", 1, 0), cell("도서관", 1, 1), cell("3,500", 1, 2)],
        [cell("0017", 2, 0), cell("체육관", 2, 1), cell("35,000", 2, 2)],
        [cell("합계", 3, 0), cell("", 3, 1), cell("38,500", 3, 2)],
    ]
    sections = [para("2026 사업 계획") + "<hp:p><hp:run>" + table(rows) + "</hp:run></hp:p>"]
    source = write_hwpx(tmp_path / "a.hwpx", sections)
    rule = fill_rule(
        record=RecordRule("table_rows", "코드", table=1, header_row=1),
        fields=(
            FieldRule("코드", "column", query="코드"),
            FieldRule(
                "사업비",
                "column",
                query="사업비(천원)",
                value_type="money",
                unit="천원",
                target_column="2026 예산",
            ),
        ),
    )
    inputs = open_inputs([source], write_xlsx(tmp_path / "t.xlsx", SHEET), tmp_path / "out")
    records = extract_records(inspect_source(inputs, "src-1"), "src-1", rule)
    assert [r.key for r in records] == [
        "0012",
        "0017",
        "합계",
    ]  # 합계 행도 레코드지만 키가 표에 없다
    plan = analyze(inputs, rule)
    items = by_key_field(plan)
    assert (
        items[("0012", "사업비")].proposed.value == "3500000"
        and items[("0012", "사업비")].write_selected
    )
    assert items[("0017", "사업비")].proposed.value == "35000000"
    assert items[("합계", "사업비")].reason_code == "no_target"  # 합계 행의 키는 표에 없다


def test_table_rows_with_duplicate_header_is_ambiguous(tmp_path):
    rows = [
        [cell("코드", 0, 0), cell("사업비", 0, 1), cell("사업비", 0, 2)],
        [cell("0012", 1, 0), cell("1", 1, 1), cell("2", 1, 2)],
    ]
    source = write_hwpx(tmp_path / "a.hwpx", ["<hp:p><hp:run>" + table(rows) + "</hp:run></hp:p>"])
    rule = fill_rule(
        record=RecordRule("table_rows", "코드"),
        fields=(
            FieldRule("코드", "column", query="코드"),
            FieldRule(
                "사업비",
                "column",
                query="사업비",
                value_type="money",
                unit="원",
                target_column="2026 예산",
            ),
        ),
    )
    inputs = open_inputs([source], write_xlsx(tmp_path / "t.xlsx", SHEET), tmp_path / "out")
    (record,) = extract_records(inspect_source(inputs, "src-1"), "src-1", rule)
    assert record.value("사업비").status == "ambiguous"
    assert by_key_field(analyze(inputs, rule))[("0012", "사업비")].reason_code == "ambiguous_field"


def test_same_rule_applies_when_the_table_moves(tmp_path):
    """FR-09: 표 앞에 문단이 더 있어도 라벨 규칙은 같은 값을 찾는다."""
    moved = [para("머리말"), para("또 다른 문단")] + plan_doc("0012", "350만원")
    paths = [
        write_hwpx(tmp_path / "a.hwpx", plan_doc("0012", "350만원")),
        write_hwpx(tmp_path / "b.hwpx", moved),
    ]
    target = write_xlsx(tmp_path / "t.xlsx", SHEET)
    plan = analyze(open_inputs(paths, target, tmp_path / "out"), fill_rule())
    (item,) = [i for i in plan.items if i.record_key == "0012"]
    assert item.write_selected and len(item.evidence) == 2


def test_duplicate_source_files_do_not_double_count(tmp_path):
    original = write_hwpx(tmp_path / "a.hwpx", plan_doc("0012", "350만원"))
    copy = tmp_path / "b.hwpx"
    shutil.copy(original, copy)
    target = write_xlsx(tmp_path / "t.xlsx", SHEET)
    plan = analyze(open_inputs([original, copy], target, tmp_path / "out"), fill_rule())
    assert plan.counts.records == 1 and plan.counts.files_ok == 1


def analyze_docs(tmp_path, docs, rule=None, sheet=SHEET):
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = [write_hwpx(tmp_path / f"{n}.hwpx", s) for n, s in docs]
    target = write_xlsx(tmp_path / "t.xlsx", sheet)
    return analyze(open_inputs(paths, target, tmp_path / "out"), rule or fill_rule())


def test_group_judgement_does_not_depend_on_file_order(tmp_path):
    """같은 키의 문서 둘 중 하나를 못 읽으면 순서와 상관없이 같은 판정이다(검수 M1)."""
    readable = plan_doc("0012", "350만원")
    blank = plan_doc("0012", "")
    forward = analyze_docs(tmp_path / "f", [("a", readable), ("b", blank)])
    backward = analyze_docs(tmp_path / "b", [("a", blank), ("b", readable)])
    f, b = by_key_field(forward)[("0012", "사업비")], by_key_field(backward)[("0012", "사업비")]
    assert (f.reason_code, f.status) == (b.reason_code, b.status) == ("conflict", "needs_review")
    assert forward.problem_counts == backward.problem_counts
    assert "a.hwpx" in f.reason and "src-" not in f.reason  # 소명은 파일 이름으로 말한다


def test_record_whose_key_cannot_be_read_is_an_unreadable_value_not_no_target(tmp_path):
    """키가 두 곳·변경 추적·없음이면 그 레코드의 칸은 '값을 확실히 못 읽음'이다(검수 M2)."""
    two_keys = [
        "<hp:p><hp:run>"
        + table(
            [
                [cell("사업코드", 0, 0), cell("0012", 0, 1)],
                [cell("사업코드", 1, 0), cell("0013", 1, 1)],
                [cell("사업비", 2, 0), cell("350만원", 2, 1)],
            ]
        )
        + "</hp:run></hp:p>"
    ]
    no_key = [
        "<hp:p><hp:run>"
        + table([[cell("사업비", 0, 0), cell("350만원", 0, 1)]])
        + "</hp:run></hp:p>"
    ]
    plan = analyze_docs(tmp_path, [("둘", two_keys), ("없음", no_key)])
    codes = {(i.evidence[0].file_id, i.reason_code, i.status) for i in plan.items}
    assert codes == {
        ("src-1", "ambiguous_field", "needs_review"),
        ("src-2", "missing_value", "failed"),
    }
    manual = manual_items(plan)
    cells_manual = [m for m in manual if m.kind != "no_document"]
    assert all(m.kind == "unreadable_value" for m in cells_manual)
    assert all("#" not in m.reason and "src-" not in m.where for m in cells_manual)
    assert cells_manual[0].where.startswith(("둘.hwpx", "없음.hwpx"))
    assert all("키 항목" in m.reason for m in cells_manual)


def test_document_without_the_table_is_reported_as_a_file_item(tmp_path):
    rule = fill_rule(
        record=RecordRule("table_rows", "코드"),
        fields=(
            FieldRule("코드", "column", query="코드"),
            FieldRule(
                "사업비",
                "column",
                query="사업비",
                value_type="money",
                unit="원",
                target_column="2026 예산",
            ),
        ),
    )
    plan = analyze_docs(tmp_path, [("문단만", [para("표가 없는 문서")])], rule)
    assert plan.counts.records == 0 and plan.empty_sources == ("src-1",)
    item = next(m for m in manual_items(plan) if m.reason_code == "no_records")
    assert (item.kind, item.where) == ("unreadable_file", "문단만.hwpx")


def test_merged_header_spanning_two_columns_is_ambiguous_not_the_first_column(tmp_path):
    """병합 머리글 '예산'(2열)에 맞은 항목은 2025·2026 중 어느 열인지 모른다(검수 H1)."""
    rows = [
        [cell("코드", 0, 0, rowspan=2), cell("예산", 0, 1, colspan=2)],
        [cell("2025", 1, 1), cell("2026", 1, 2)],
        [cell("0012", 2, 0), cell("1,000원", 2, 1), cell("2,000원", 2, 2)],
    ]
    sections = ["<hp:p><hp:run>" + table(rows) + "</hp:run></hp:p>"]
    rule = fill_rule(
        record=RecordRule("table_rows", "코드", header_row=1),
        fields=(
            FieldRule("코드", "column", query="코드"),
            FieldRule(
                "예산", "column", query="예산", value_type="money", target_column="2026 예산"
            ),
        ),
    )
    plan = analyze_docs(tmp_path, [("a", sections)], rule)
    (item,) = plan.items
    assert (item.record_key, item.reason_code, item.write_selected) == (
        "0012",
        "ambiguous_field",
        False,
    )
    assert plan.counts.records == 1  # 머리글 블록(rowSpan 2)이 덮는 2행은 레코드가 아니다(검수 M5)
    # 2행을 머리글로 지정하면 '2026' 열을 읽는다
    right = fill_rule(
        record=RecordRule("table_rows", "코드", header_row=2),
        fields=(
            FieldRule("코드", "column", query="코드"),
            FieldRule(
                "예산", "column", query="2026", value_type="money", target_column="2026 예산"
            ),
        ),
    )
    plan = analyze_docs(tmp_path / "r", [("a", sections)], right)
    assert plan.items[0].reason_code == "missing_value" or plan.items[0].proposed.value == "2000"


def test_key_whitespace_is_trimmed_on_both_sides_only_at_the_ends(tmp_path):
    rows = [
        ["사업코드", "사업명", "2026 예산"],
        [" 0012", "표 쪽 앞 공백", None],
        ["00 13", "안쪽 공백", None],
    ]
    docs = [("a", plan_doc("0012", "350만원")), ("b", plan_doc("0013", "350만원"))]
    rule = fill_rule(target=TargetRule("사업취합", 1, (2, 3), "사업코드"))
    plan = analyze_docs(tmp_path, docs, rule, rows)
    items = by_key_field(plan)
    assert items[("0012", "사업비")].write_selected  # 양끝 공백은 양쪽 모두 뗀다(검수 M7)
    assert items[("0013", "사업비")].reason_code == "no_target"  # 안쪽 공백 차이는 다른 키다(FR-11)


def test_items_and_manual_list_sort_by_row_number_not_text(tmp_path):
    rows = [["사업코드", "사업명", "2026 예산"]] + [[f"{i:04d}", "", None] for i in range(1, 13)]
    docs = [(f"d{i}", plan_doc(f"{i:04d}", "350만원")) for i in (3, 10, 11)]
    rule = fill_rule(target=TargetRule("사업취합", 1, (2, 13), "사업코드"))
    plan = analyze_docs(tmp_path, docs, rule, rows)
    assert [i.target_ref for i in plan.items] == ["C4", "C11", "C12"]
    manual = manual_items(plan)
    assert [m.where for m in manual][:3] == [
        "사업취합!A2",
        "사업취합!A3",
        "사업취합!A5",
    ]  # 문서 없는 행, 키 열 주소


def test_sample_scope_counts_files_as_a_whole_and_skips_unmatched_rows(tmp_path):
    docs = [(f"d{i}", plan_doc(code, "350만원")) for i, code in enumerate(["0012", "0013", "0018"])]
    paths = [write_hwpx(tmp_path / f"{n}.hwpx", s) for n, s in docs]
    inputs = open_inputs(paths, write_xlsx(tmp_path / "t.xlsx", SHEET), tmp_path / "out")
    plan = analyze(inputs, fill_rule(), scope=Sample(("src-1", "src-2"), max_records=1))
    assert (plan.counts.files_ok, plan.counts.records, plan.unmatched_rows) == (3, 1, ())
