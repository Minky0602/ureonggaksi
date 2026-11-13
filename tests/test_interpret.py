"""한 줄 요청 → 규칙 초안: 코드가 정하고 남은 짝만 모델에 묻는다 (P04 ①, PRD FR-05·06, DIF-030)."""

import pytest
from conftest import cell, para, table, write_hwpx, write_xlsx

from ureonggaksi.analyze import analyze
from ureonggaksi.hwpx import read_hwpx
from ureonggaksi.inputs import open_inputs
from ureonggaksi.interpret import build_rule, interpret
from ureonggaksi.xlsx import inspect_target

PEOPLE = [("A-01", "가나다", "주무관", "12시간", "90"), ("A-02", "라마바", "팀장", "8시간", "85")]
SHEET = [
    ["발급번호", "이름", "직급", "이수시간", "점수"],
    ["A-01", "가나다", None, None, None],
    ["A-02", "라마바", None, None, None],
]


def certificate(number, name, title, hours, score):
    """이수 확인서 흉내: 본문 '발급번호: …' + 항목명|값 표."""
    rows = [("성명", name), ("직위", title), ("이수시간", hours), ("평가점수", score)]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


@pytest.fixture
def setup(tmp_path):
    paths = [write_hwpx(tmp_path / f"{p[1]}.hwpx", certificate(*p)) for p in PEOPLE]
    target = write_xlsx(tmp_path / "취합표.xlsx", SHEET, sheet="이수현황")
    inputs = open_inputs(paths, target, tmp_path / "out")
    view = inspect_target(inputs, "이수현황", header_row=1)
    docs = tuple(read_hwpx(p) for p in paths)
    return inputs, docs, view


def run(setup, request, choose=None):
    _, docs, view = setup
    return interpret(request, docs, view, choose=choose)


def test_work_type_comes_from_verbs_not_document_names(setup):
    assert run(setup, "확인서에서 이수시간 채워줘. 이름 기준").work_type == "fill"
    assert run(setup, "이수시간이 맞는지 이름으로 대조해 줘").work_type == "check"
    result = run(setup, "확인서 이수시간")  # 동사가 없다 → 묻는다
    assert result.work_type is None and result.questions[0][0] == "work_type"


def test_key_is_the_named_column_matched_by_data(setup):
    result = run(setup, "이름으로 찾아서 이수시간 채워줘")
    assert result.key == (
        "성명",
        "이름",
    )  # 열 이름(이름)과 문서 항목명(성명)이 달라도 값으로 잇는다
    by_number = run(setup, "발급번호 기준으로 이수시간 채워줘")
    assert by_number.key == ("발급번호", "발급번호")


def test_names_that_contain_each_other_pair_without_the_model(setup):
    result = run(setup, "이름 기준으로 이수시간이랑 점수 채워줘")
    assert set(result.fields) == {("이수시간", "이수시간", "match"), ("평가점수", "점수", "match")}


def test_only_unpaired_names_go_to_the_model_as_a_choice(setup):
    asked = []

    def choose(prompt, options):
        asked.append(options)
        return "직위" if "직위" in options else "직급"

    result = run(setup, "이름으로 찾아서 직급 채워줘", choose)
    assert result.fields == (("직위", "직급", "model"),)
    assert len(asked) == 1 and asked[0][-1] == "없음"  # 고를 것 하나, '없음' 포함
    without = run(setup, "이름으로 찾아서 직급 채워줘")  # 모델이 없으면 그 열은 빼고 묻는다
    assert without.fields == () and without.excluded == ("직급",)
    assert [q[0] for q in without.questions] == ["target_column"]


def test_model_answer_outside_the_options_is_ignored(setup):
    result = run(setup, "이름으로 찾아서 직급 채워줘", lambda prompt, options: "주소")
    assert result.fields == () and result.excluded == ("직급",)


def test_one_sided_names_pair_in_order_and_negated_names_are_skipped(setup):
    result = run(setup, "평가점수를 이름 기준으로 점수 칸에 넣어줘")
    assert result.fields == (("평가점수", "점수", "match"),)
    skipped = run(setup, "이름 기준으로 직급은 빼고 이수시간 채워줘")
    assert skipped.fields == (("이수시간", "이수시간", "match"),) and skipped.excluded == ()


def test_complete_interpretation_builds_a_rule_that_analyzes(setup):
    inputs, _, view = setup
    result = run(setup, "이름 기준으로 이수시간이랑 점수 채워줘")
    rule = build_rule(result, view)
    plan = analyze(inputs, rule)
    done = {(i.target_ref, i.proposed.value) for i in plan.items if i.area == "done"}
    assert done == {("D2", "12시간"), ("D3", "8시간"), ("E2", "90"), ("E3", "85")}
    assert (
        build_rule(run(setup, "확인서 이수시간"), view) is None
    )  # 질문이 남으면 규칙을 만들지 않는다


def test_roster_table_reads_every_participant(tmp_path):
    """명단형 문서: 머리글이 모든 문서에서 같고 취합표 헤더와 둘 이상 맞으면 표의 행마다 한 건이다."""

    def roster(org, people):
        head = [cell(h, 0, i) for i, h in enumerate(("연번", "성명", "부서", "참석일"))]
        rows = [head] + [
            [cell(str(n), n, 0), cell(a, n, 1), cell(b, n, 2), cell(c, n, 3)]
            for n, (a, b, c) in enumerate(people, start=1)
        ]
        info = table([[cell("신청기관", 0, 0), cell(org, 0, 1)]])
        return [
            "<hp:p><hp:run>"
            + info
            + "</hp:run></hp:p><hp:p><hp:run>"
            + table(rows)
            + "</hp:run></hp:p>"
        ]

    paths = [
        write_hwpx(
            tmp_path / "a.hwpx",
            roster("가기관", [("가나다", "총무팀", "12-04"), ("라마바", "기획팀", "12-05")]),
        ),
        write_hwpx(tmp_path / "b.hwpx", roster("나기관", [("사아자", "민원팀", "12-04")])),
    ]
    sheet = [
        ["이름", "소속부서", "참석일자"],
        ["가나다", None, None],
        ["라마바", None, None],
        ["사아자", None, None],
    ]
    target = write_xlsx(tmp_path / "명단.xlsx", sheet, sheet="명단")
    inputs = open_inputs(paths, target, tmp_path / "out")
    view = inspect_target(inputs, "명단", header_row=1)
    docs = tuple(read_hwpx(p) for p in paths)
    result = interpret("이름으로 찾아서 부서랑 참석일 채워줘", docs, view)
    assert result.record.kind == "table_rows" and result.key == ("성명", "이름")
    plan = analyze(inputs, build_rule(result, view))
    done = sorted(i.target_ref for i in plan.items if i.area == "done")
    assert done == ["B2", "B3", "B4", "C2", "C3", "C4"]
