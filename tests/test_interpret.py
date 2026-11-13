"""한 줄 요청 → 규칙 초안: 코드는 확실한 것만 정하고 나머지는 모델 선택지·질문으로 (P04 ①, FR-05·06).

음성 시험은 P04 ① 독립 검수가 찾은 오확정 경로(틀린 칸에 값이 쓰이는 규칙)를 고정한다.
"""

import pytest
from conftest import cell, para, table, write_hwpx, write_xlsx

from ureonggaksi.analyze import analyze
from ureonggaksi.hwpx import read_hwpx
from ureonggaksi.inputs import open_inputs
from ureonggaksi.interpret import _resolve, build_rule, interpret
from ureonggaksi.xlsx import inspect_target

PEOPLE = [
    ("A-01", "가나다", "주무관", "12시간", "90", "내선 101", "E-7781"),
    ("A-02", "라마바", "팀장", "8시간", "85", "내선 102", "E-7782"),
]
SHEET = [
    ["발급번호", "이름", "직급", "이수시간", "점수", "비고", "확인", "번호"],
    ["A-01", "가나다", None, None, None, None, None, None],
    ["A-02", "라마바", None, None, None, None, None, None],
]


def certificate(number, name, title, hours, score, phone, staff):
    """이수 확인서 흉내: 본문 '발급번호: …' + 항목명|값 표."""
    rows = [
        ("성명", name),
        ("직위", title),
        ("이수시간", hours),
        ("평가점수", score),
        ("연락처", phone),
        ("직원번호", staff),
    ]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


def build(tmp_path, docs, sheet, sheet_name="이수현황"):
    paths = [write_hwpx(tmp_path / f"doc{i}.hwpx", body) for i, body in enumerate(docs)]
    target = write_xlsx(tmp_path / "취합표.xlsx", sheet, sheet=sheet_name)
    inputs = open_inputs(paths, target, tmp_path / "out")
    view = inspect_target(inputs, sheet_name, header_row=1)
    return inputs, tuple(read_hwpx(p) for p in paths), view


@pytest.fixture
def setup(tmp_path):
    return build(tmp_path, [certificate(*p) for p in PEOPLE], SHEET)


def run(setup, request, choose=None):
    _, docs, view = setup
    return interpret(request, docs, view, choose=choose)


def first_option(asked):
    """가짜 모델: 첫 선택지를 고르고 질문을 기록한다."""

    def choose(prompt, options):
        asked.append((prompt, options))
        return options[0]

    return choose


# ---------- 코드가 정하는 것 ----------


def test_work_type_comes_from_verbs_not_document_names(setup):
    assert run(setup, "확인서에서 이수시간 채워줘. 이름 기준").work_type == "fill"
    assert run(setup, "이수시간이 맞는지 이름으로 대조해 줘").work_type == "check"
    assert run(setup, "이수시간 채우지 말고 맞는지만 봐 줘").work_type == "check"
    result = run(setup, "확인서 이수시간")  # 동사가 없다 → 묻는다
    assert result.work_type is None and result.questions[0][0] == "work_type"


def test_key_is_the_named_column_matched_by_data(setup):
    assert run(setup, "이름으로 찾아서 이수시간 채워줘").key == ("성명", "이름")
    assert run(setup, "발급번호 기준으로 이수시간 채워줘").key == ("발급번호", "발급번호")


def test_equal_names_pair_without_the_model(setup):
    result = run(setup, "이름 기준으로 이수시간 채워줘")
    assert result.fields == (("이수시간", "이수시간", "match"),)


def test_names_that_only_look_alike_go_to_the_model_with_a_hint(setup):
    asked = []
    result = run(setup, "이름 기준으로 점수 채워줘", first_option(asked))
    assert result.fields == (("평가점수", "점수", "model"),)
    prompt, options = asked[0]
    assert options[0] == "평가점수" and options[-1] == "없음"  # 이름이 비슷한 것을 앞에
    assert "이름이 비슷한 것: 평가점수" in prompt and "예: 90, 85" in prompt
    without = run(setup, "이름 기준으로 점수 채워줘")  # 모델이 없으면 확정하지 않고 빼서 소명
    assert without.fields == () and without.excluded == ("점수",)
    assert [q[0] for q in without.questions] == ["target_column"]


def test_model_answer_outside_the_options_is_ignored(setup):
    result = run(setup, "이름으로 찾아서 직급 채워줘", lambda prompt, options: "주소")
    assert result.fields == () and result.excluded == ("직급",)


def test_user_pointing_at_a_cell_links_the_pair(setup):
    result = run(setup, "평가점수를 이름 기준으로 비고 칸에 넣어줘")
    assert result.fields == (("평가점수", "비고", "match"),) and result.excluded == ()
    # 지목한 칸이 문서 항목 이름과 같아도 지목이 이긴다(검수 X10: "점수를 이수시간 칸에")
    swapped = run(setup, "평가점수를 이름 기준으로 이수시간 칸에 넣어줘")
    assert swapped.fields == (("평가점수", "이수시간", "match"),)


def test_complete_interpretation_builds_a_rule_that_analyzes(setup):
    inputs, _, view = setup
    result = run(setup, "이름 기준으로 이수시간 채워줘")
    plan = analyze(inputs, build_rule(result, view))
    done = {(i.target_ref, i.proposed.value) for i in plan.items if i.area == "done"}
    assert done == {("D2", "12시간"), ("D3", "8시간")}
    assert build_rule(run(setup, "확인서 이수시간"), view) is None  # 질문이 남으면 규칙 없음


def test_roster_table_reads_every_participant(tmp_path):
    """명단형 문서: 머리글이 모든 문서에서 같고 취합표 헤더와 둘 이상 닮으면 표의 행마다 한 건이다."""

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

    docs = [
        roster("가기관", [("가나다", "총무팀", "12-04"), ("라마바", "기획팀", "12-05")]),
        roster("나기관", [("사아자", "민원팀", "12-04")]),
    ]
    sheet = [
        ["이름", "소속부서", "참석일자"],
        ["가나다", None, None],
        ["라마바", None, None],
        ["사아자", None, None],
    ]
    inputs, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret(
        "이름으로 찾아서 부서랑 참석일 채워줘", documents, view, choose=first_option([])
    )
    assert result.record.kind == "table_rows" and result.key == ("성명", "이름")
    assert set(result.fields) == {("부서", "소속부서", "model"), ("참석일", "참석일자", "model")}
    plan = analyze(inputs, build_rule(result, view))
    done = sorted(i.target_ref for i in plan.items if i.area == "done")
    assert done == ["B2", "B3", "B4", "C2", "C3", "C4"]


# ---------- 오확정 음성 시험(독립 검수 H1~H8) ----------


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 연락처 채워줘, 비고는 그대로 두고",  # H1: 우연히 나온 열을 짝짓지 않는다
        "이름 기준으로 평가점수 넣어줘 비고 참고해서",
        "이름 기준으로 연락처가 맞는지 확인해줘",  # H1: 동사 '확인'은 '확인' 열이 아니다
        "이름 기준으로 전화번호 채워줘",  # H2: 단어 중간의 '번호'는 열 이름이 아니다
        "이름 기준으로 이메일 전부 채워줘",  # H6: '전부'로 다른 열을 채우지 않는다
    ],
)
def test_nothing_is_confirmed_by_accident(setup, request_text):
    assert run(setup, request_text).fields == ()


@pytest.mark.parametrize(
    "request_text",
    [
        "이수시간이랑 점수는 빼고 이름 기준으로 발급번호만 채워줘",  # H4: 부정이 나열 전체에 걸린다
        "이름 기준으로 이수시간 말고 발급번호 채워줘",
        "이름 기준으로 발급번호 채워줘 이수시간까지는 하지 마",
    ],
)
def test_negated_names_are_left_out(setup, request_text):
    result = run(setup, request_text)
    assert "이수시간" not in {c for _, c, _ in result.fields}


def test_negated_fill_and_mixed_intent_ask_for_the_work_type(setup):
    assert run(setup, "이름 기준으로 이수시간은 아직 채우지 마").work_type is None  # H8
    assert run(setup, "입력된 이수시간 괜찮은지 봐줘").work_type is None


def test_units_in_brackets_do_not_make_two_columns_one_name():
    """H3: '금액(천원)'·'금액(원)'은 '금액'과 하나로 정하지 않는다."""
    assert _resolve("금액", ["금액(천원)", "금액(원)"]) is None
    assert _resolve("금액", ["금액(원)", "비고"]) == "금액(원)"


def test_two_names_competing_for_one_column_are_both_reported(setup):
    """H7: 한 열에 둘이 몰리면 어느 것도 확정하지 않고 둘 다 소명한다."""
    result = run(setup, "이름 기준으로 이수시간 채우고 평가점수를 이수시간 칸에 넣어줘")
    assert result.fields == () and set(result.excluded) == {"평가점수", "이수시간"}


def test_unnamed_key_is_not_guessed_from_serial_numbers(tmp_path):
    """H5: 연번 1·2·3이 문서의 차수 1·2·3과 우연히 같아도 키로 정하지 않는다."""

    def doc(order, name, hours):
        rows = [("차수", order), ("성명", name), ("이수시간", hours)]
        body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
        return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    docs = [doc("1", "가나다", "12시간"), doc("2", "라마바", "8시간"), doc("3", "사아자", "4시간")]
    sheet = [
        ["연번", "이름", "이수시간"],
        [1, "김철수", None],
        [2, "가나다", None],
        [3, "이영희", None],
    ]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이수시간 채워줘", documents, view)
    assert result.key is None and "key_column" in [q[0] for q in result.questions]
    assert build_rule(result, view) is None
