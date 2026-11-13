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
    """H3·2차 N01·N02: 괄호 안 단위까지 같아야 같은 이름이다(천원 값을 원 칸에 쓰지 않는다)."""
    assert _resolve("금액", ["금액(천원)", "금액(원)"]) is None
    assert _resolve("금액(원)", ["금액(원)", "비고"]) == "금액(원)"
    assert _resolve("예산(천원)", ["예산(원)", "비고"]) is None
    assert _resolve("금액(천원)", ["금액", "비고"]) is None


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


# ---------- 2차 독립 검수: 지목 규칙·부정·키 추정·작업 종류 ----------


@pytest.mark.parametrize(
    "request_text, expected",
    [
        # 지목은 "A를 B 칸에"뿐이다. "A 채우고 B 칸은 비워 둬"는 지목이 아니다
        ("이름 기준으로 직원번호 채우고 번호 칸은 비워 둬", ()),
        ("이름 기준으로 연락처 넣어줘 번호 칸 말고", ()),
        ("이름 기준으로 연락처 채우고 비고란은 그대로", ()),
        # 부정이 섞이면 지목도 확정하지 않는다(3차 검수 fail-safe)
        ("이름 기준으로 연락처를 비고 칸에 넣지 말고 번호 칸에 넣어줘", ()),
        # 같은 이름 짝보다 지목한 칸이 이긴다
        ("이름 기준으로 이수시간을 점수 칸에 넣어줘", (("이수시간", "점수"),)),
        # 한 칸에 둘을 지목하면 어느 것도 정하지 않는다
        ("이름 기준으로 직원번호를 번호 칸에, 연락처를 번호 칸에 넣어줘", ()),
        # 넣을 것이 문서 항목이 아니면(열 이름 '점수') 그 칸은 정하지 않는다
        ("이름으로 찾아서 점수를 이수시간 칸에 넣어줘", ()),
    ],
)
def test_pointing_needs_an_object_and_a_cell_and_respects_negation(setup, request_text, expected):
    result = run(setup, request_text)
    assert {(f, c) for f, c, _ in result.fields} == set(expected)


def test_excluded_columns_are_not_offered_to_the_model(setup):
    """빼라고 한 열은 모델이 고를 수 없다(2차 검수 E04·X23: 가짜 모델이 '비고'를 골랐다)."""
    asked = []
    result = run(setup, "이름 기준으로 직원번호 채우고 비고는 비워둬", choose=first_option(asked))
    assert all("비고" not in options for _, options in asked)
    assert all(column != "비고" for _, column, _ in result.fields)


def test_keep_as_is_is_not_negation_when_it_modifies_the_verb(setup):
    """'그대로 옮겨줘'는 빼라는 말이 아니다(2차 검수 E02)."""
    result = run(setup, "이름 기준으로 이수시간을 그대로 옮겨줘")
    assert ("이수시간", "이수시간") in {(f, c) for f, c, _ in result.fields}


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 이수시간 기입 상태 봐줘",
        "이름 기준으로 이수시간 반영 여부 봐줘",
        "이름 기준으로 이수시간 입력 누락 없나 봐줘",
    ],
)
def test_fill_words_used_as_nouns_in_a_check_request_ask_for_the_work_type(setup, request_text):
    assert run(setup, request_text).work_type is None


def _single_table_docs(rows_per_doc):
    out = []
    for rows in rows_per_doc:
        body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
        out.append(["<hp:p><hp:run>" + body + "</hp:run></hp:p>"])
    return out


def test_unnamed_key_is_not_guessed_from_a_differently_named_column(tmp_path):
    """2차 검수 K01: 문서 발급일과 취합표 교육일이 우연히 같아도 키로 정하지 않는다."""
    dates = ["2026-11-01", "2026-11-02", "2026-11-03"]
    docs = _single_table_docs(
        [
            [("성명", n), ("발급일", d), ("이수시간", h)]
            for n, d, h in zip(
                ["가나다", "라마바", "사아자"], dates, ["12", "8", "16"], strict=True
            )
        ]
    )
    sheet = [["이름", "교육일", "이수시간"]] + [
        [o, d, None] for o, d in zip(["김", "이", "박"], dates, strict=True)
    ]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이수시간 채워줘", documents, view)
    assert result.key is None and build_rule(result, view) is None


def test_named_key_is_not_linked_by_small_numbers(tmp_path):
    """2차 검수 K03: '번호로 찾아'라도 번호 1·2·3과 문서 차수 1·2·3의 우연한 일치는 키가 아니다."""
    docs = _single_table_docs(
        [
            [("차수", str(i)), ("성명", n), ("이수시간", "8")]
            for i, n in enumerate(["가나다", "라마바", "사아자"], 1)
        ]
    )
    sheet = [["번호", "이름", "이수시간"]] + [[str(i), o, None] for i, o in enumerate("김이박", 1)]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("번호로 찾아서 이수시간 채워줘", documents, view)
    assert result.key is None and build_rule(result, view) is None


def test_grouping_word_is_not_a_key(tmp_path):
    """2차 검수 K04: '직급별로'는 묶기다. 값이 겹치는 직급 열을 키로 정하지 않는다."""
    docs = _single_table_docs(
        [
            [("성명", n), ("직급", t), ("이수시간", "8")]
            for n, t in zip(
                ["가나다", "라마바", "사아자"], ["주무관", "주무관", "팀장"], strict=True
            )
        ]
    )
    sheet = [
        ["이름", "직급", "이수시간"],
        ["김", "주무관", None],
        ["이", "주무관", None],
        ["박", "팀장", None],
    ]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("직급별로 이수시간 채워줘", documents, view)
    assert result.key is None


def test_named_key_that_is_then_negated_is_asked_not_replaced(setup):
    """2차 검수 R03: '이름으로 찾아서 이름 말고'는 충돌이다. 발급번호 같은 다른 키를 추정하지 않는다."""
    result = run(setup, "이름으로 찾아서 이름 말고 이수시간 채워줘")
    assert result.key is None and "key_column" in [q[0] for q in result.questions]


def test_same_name_with_a_different_unit_is_not_offered_to_the_model(tmp_path):
    """2차 검수 N01: 예산(천원) 값을 예산(원) 칸에 넣는 선택지는 모델에게도 주지 않는다."""
    docs = _single_table_docs(
        [
            [("성명", n), ("예산(천원)", b)]
            for n, b in zip(["가나다", "라마바"], ["150", "80"], strict=True)
        ]
    )
    sheet = [["이름", "예산(원)", "비고"], ["가나다", None, None], ["라마바", None, None]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    asked = []
    interpret("이름 기준으로 예산 채워줘", documents, view, choose=first_option(asked))
    assert all("예산(원)" not in options for _, options in asked)


# ---------- 3차 독립 검수: 부정 fail-safe·키 경쟁·의문문 ----------


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 이수시간 채워줘. 직급은 건들지 마",
        "이름 기준으로 이수시간 채워줘. 직급은 놔둬",
        "이름 기준으로 이수시간 채워줘. 직급엔 손대지 마",
        "이름 기준으로 이수시간 채워줘. 직급은 안 채워도 돼",
        "이름 기준으로 이수시간 채워줘. 직급은 필요 없어",
        "이름 기준으로 이수시간 채워줘. 직급은 냅둬",
        "이름 기준으로 이수시간 채워줘. 제외: 직급",
        "이름 기준으로 이수시간 채우고 직급·비고는 빼줘",
        "이름 기준으로 이수시간 채우고 직급이나 비고는 그대로",
        "이름 기준으로 이수시간 채우고, 직급은 이미 맞으니 그냥 둬",
        "이름 기준으로 이수시간 채워줘. 직급은 쓰지 마",
        "이름 기준으로 이수시간 채워줘. 직급은 넣지는 마",
        "이름 기준으로 이수시간 채우고, 직원번호를 번호 칸에 넣지는 마",
        "이름 기준으로 이수시간 채우고, 직원번호를 번호 칸에 넣으면 안 돼",
        "이름 기준으로 이수시간 채우고 연락처를 비고 칸에 쓰지 말 것",
        "이름 기준으로 직원번호를 번호 칸에서 빼고 이수시간 채워줘",
        "이름 기준으로 직급도 빼지 말고 이수시간이랑 채워줘",
    ],
)
def test_any_restriction_in_the_request_confirms_no_pair(setup, request_text):
    """부정 어휘를 다 셀 수 없으므로, 빼기·하지 않기가 섞인 요청은 짝을 정하지 않고 묻는다."""
    asked = []
    result = run(setup, request_text, choose=first_option(asked))
    assert result.fields == () and asked == []
    assert "target_column" in [q[0] for q in result.questions]
    assert result.excluded  # 말한 항목은 소명 목록에 남는다


@pytest.mark.parametrize(
    "request_text",
    ["이름 기준으로 이수시간 입력된 거 맞아?", "이름 기준으로 이수시간 기입 잘 됐어?"],
)
def test_questions_are_not_fill_requests(setup, request_text):
    assert run(setup, request_text).work_type is None


def _recommendation(name, referrer, hours):
    rows = [("성명", name), ("추천인", referrer), ("이수시간", hours)]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


def test_key_is_not_set_when_two_document_fields_match_the_key_column(tmp_path):
    """3차 H1: 성명과 추천인이 모두 이름 열과 맞으면 어느 쪽이 키인지 묻는다(남의 행에 쓰지 않는다)."""
    docs = [
        _recommendation("가나다", "사아자", "12"),
        _recommendation("라마바", "차카타", "8"),
        _recommendation("신규자", "가나다", "16"),
    ]
    sheet = [
        ["이름", "이수시간"],
        ["가나다", None],
        ["라마바", None],
        ["사아자", None],
        ["차카타", None],
    ]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    for request in ("이름 기준으로 이수시간 채워줘", "이수시간 채워줘"):
        result = interpret(request, documents, view)
        assert result.key is None and build_rule(result, view) is None


def test_key_is_not_linked_by_values_when_the_preview_misses_rows(tmp_path):
    """3차 K04: 미리보기 밖(51행 아래)에 있는 사람은 비교되지 않으므로 값으로 키를 정하지 않는다."""
    docs = [_recommendation(n, r, "8") for n, r in (("가나다", "사아자"), ("라마바", "차카타"))]
    filler = [[f"사람{i:02d}", None] for i in range(60)]
    sheet = [["이름", "이수시간"], ["사아자", None], ["차카타", None], *filler, ["가나다", None]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이름 기준으로 이수시간 채워줘", documents, view)
    assert result.key is None


def test_repeated_label_is_not_a_candidate(tmp_path):
    """3차 D01: 한 문서에 '성명'이 두 번(추천인·피추천인) 나오면 어느 것도 항목으로 쓰지 않는다."""

    def doc(a, b, hours):
        rows = [("성명", a), ("성명", b), ("이수시간", hours)]
        body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
        return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    docs = [doc("가나다", "라마바", "12"), doc("사아자", "차카타", "8")]
    sheet = [["성명", "이수시간"], ["가나다", None], ["라마바", None], ["사아자", None]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("성명 기준으로 이수시간 채워줘", documents, view)
    assert result.key is None and build_rule(result, view) is None
