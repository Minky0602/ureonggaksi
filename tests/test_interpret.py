"""한 줄 요청 → 규칙 초안: 코드는 확실한 것만 정하고 나머지는 모델 선택지·질문으로 (P04 ①, FR-05·06).

음성 시험은 P04 ① 독립 검수가 찾은 오확정 경로(틀린 칸에 값이 쓰이는 규칙)를 고정한다.
"""

import pytest
from conftest import cell, para, table, write_hwpx, write_xlsx

from ureonggaksi import morph
from ureonggaksi.analyze import analyze
from ureonggaksi.hwpx import read_hwpx
from ureonggaksi.inputs import open_inputs
from ureonggaksi.interpret import (
    _ask_model,
    _blank,
    _mentions,
    _negated,
    _resolve,
    build_rule,
    interpret,
)
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


def picks(meaning, asked=None):
    """가짜 모델: 질문 대상('부서')에 맞는 답(meaning['부서'])을 선택지 순서와 무관하게 고른다."""

    def choose(prompt, options):
        if asked is not None:
            asked.append((prompt, options))
        subject = next((k for k in meaning if f"'{k}'" in prompt), None)
        answer = meaning.get(subject, "없음")
        return answer if answer in options else "없음"

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
    result = run(setup, "이름 기준으로 점수 채워줘", picks({"점수": "평가점수"}, asked))
    assert result.fields == (("평가점수", "점수", "model"),)
    prompt, options = asked[0]
    assert options[0] == "평가점수" and options[-1] == "없음"  # 이름이 비슷한 것을 앞에
    assert "이름이 비슷한 것: 평가점수" in prompt and '예: "90", "85"' in prompt
    without = run(setup, "이름 기준으로 점수 채워줘")  # 모델이 없으면 확정하지 않고 빼서 소명
    assert without.fields == () and without.excluded == ("점수",)
    assert [q[0] for q in without.questions] == ["target_column"]


def test_model_answer_outside_the_options_is_ignored(setup):
    result = run(setup, "이름으로 찾아서 점수 채워줘", lambda prompt, options: "주소")
    assert result.fields == () and result.excluded == ("점수",)


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
        "이름으로 찾아서 부서랑 참석일 채워줘",
        documents,
        view,
        choose=picks({"부서": "소속부서", "참석일": "참석일자"}),
    )
    assert result.record.kind == "table_rows" and result.key == ("성명", "이름")
    assert set(result.fields) == {
        ("부서", "소속부서", "synonym"),
        ("참석일", "참석일자", "synonym"),
    }
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


def test_keep_as_is_is_asked_because_it_can_also_mean_leave_alone(setup):
    """'그대로'는 '그대로 옮겨'(채우기)와 '직급은 그대로'(빼기) 둘 다 된다. 허용 낱말이 아니라 묻는다."""
    for request in (
        "이름 기준으로 이수시간을 그대로 옮겨줘",
        "이름 기준으로 이수시간 채우고 직급은 그대로",
    ):
        assert run(setup, request).fields == ()


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
    """빼기·하지 않기 표현이 닿은 이름은 확정하지 않는다. 확정될 수 있는 것은 깨끗한 이수시간뿐이다."""
    asked = []
    result = run(setup, request_text, choose=first_option(asked))
    assert {(f, c) for f, c, _ in result.fields} <= {("이수시간", "이수시간")} and asked == []
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


# ---------- 4차 독립 검수: 허용 목록·같은 이름 키 경쟁 ----------


@pytest.mark.parametrize(
    "request_text",
    [
        # 부정 목록 밖의 빼기 표현(4차 H1)
        "이름 기준으로 이수시간 채워줘. 직급은 생략",
        "이름 기준으로 이수시간 채워줘. 직급은 패스",
        "이름 기준으로 이수시간 채워줘. 직급은 나중에",
        "이름 기준으로 이수시간 채워줘. 직급은 공란으로",
        "이름 기준으로 이수시간 채워줘. 직급은 현행 유지",
        "이름 기준으로 이수시간 채워줘. 직급은 해당 없음",
        "이름 기준으로 직급 없이 이수시간 채워줘",
        "이름 기준으로 직급 대신 이수시간 채워줘",
        "이름 기준으로 이수시간 채워줘. 직급은 놔 둬",
        # 두 절 요청(4차 H2)
        "이름 기준으로 이수시간을 채우고 비고 칸에 직급을 적어줘",
        "이름 기준으로 이수시간을 먼저 채우고 번호 칸에는 직원번호 넣어줘",
        "이름 기준으로 직급을 적어 주고 비고 칸에 연락처 넣어 줘",
        # 검토 요청(4차 M1)
        "이름 기준으로 이수시간 입력값 점검해줘",
    ],
)
def test_requests_with_unknown_words_confirm_no_pair(setup, request_text):
    """모르는 말이 닿은 이름은 확정하지도 모델에 묻지도 않는다. 깨끗한 이수시간만 확정될 수 있다."""
    asked = []
    result = run(setup, request_text, choose=first_option(asked))
    assert {(f, c) for f, c, _ in result.fields} <= {("이수시간", "이수시간")} and asked == []


@pytest.mark.parametrize(
    "request_text, expected",
    [
        ("이름 기준으로 이수시간 채워줘", {("이수시간", "이수시간")}),
        (
            "이름으로 찾아서 이수시간이랑 직급 넣어 주세요",
            {("이수시간", "이수시간")},
        ),
        ("이수시간이 취합표랑 같은지 이름으로 대조해 줘", {("이수시간", "이수시간")}),
        ("이름 기준으로 직원번호를 번호 칸에 넣어줘", {("직원번호", "번호")}),
        ("이름 기준 이수시간 체크좀", {("이수시간", "이수시간")}),
        ("이수시간 점검해 주세요 이름으로 찾구", {("이수시간", "이수시간")}),
        ("이름으로 찾아서 이수시간 채워주라", {("이수시간", "이수시간")}),
    ],
)
def test_plain_requests_still_confirm_same_name_pairs(setup, request_text, expected):
    result = run(setup, request_text)
    assert {(f, c) for f, c, _ in result.fields} == expected


def test_same_name_key_with_a_rival_person_field_is_asked(tmp_path):
    """4차 H3: 신청인 '성명'과 확인자 '이름'이 모두 이름 열과 맞으면 이름이 같아도 키를 정하지 않는다."""

    def doc(applicant, checker, hours):
        rows = [("성명", applicant), ("이수시간", hours), ("이름", checker)]
        body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
        return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    docs = [doc("가나다", "파하가", "12"), doc("라마바", "타파하", "8")]
    sheet = [
        ["이름", "이수시간"],
        ["가나다", None],
        ["라마바", None],
        ["파하가", None],
        ["타파하", None],
    ]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    for request in ("이름 기준으로 이수시간 채워줘", "이름으로 찾아서 이수시간 넣어줘"):
        result = interpret(request, documents, view)
        assert result.key is None and build_rule(result, view) is None


# ---------- 5차 독립 검수: "A를 B 칸에" 밖의 지목, 이름 속 동사·부정 글자 ----------

PHONE_SHEET = [
    ["이름", "직급", "휴대전화", "전화번호", "이수시간", "비고", "참고사항", "확인자", "입력일"],
    ["가나다", None, None, None, None, None, None, None, None],
    ["라마바", None, None, None, None, None, None, None, None],
]


@pytest.fixture
def phone_setup(tmp_path):
    def doc(name, title, mobile, phone, hours, checker, day):
        rows = [
            ("성명", name),
            ("직급", title),
            ("휴대전화", mobile),
            ("전화번호", phone),
            ("이수시간", hours),
            ("확인자", checker),
            ("입력일", day),
        ]
        body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
        return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    docs = [
        doc("가나다", "주무관", "010-1111", "02-111", "12", "파하가", "2026-11-01"),
        doc("라마바", "팀장", "010-2222", "02-222", "8", "타파하", "2026-11-02"),
    ]
    return build(tmp_path, docs, PHONE_SHEET, "명단")


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 전화번호 칸에 휴대전화를 넣어줘",
        "이름 기준으로 휴대전화를 전화번호에 넣어줘",
        "이름 기준으로 전화번호를 휴대전화로 채워줘",
        "이름 기준으로 휴대전화에서 전화번호 채워줘",
        "이름 기준으로 휴대전화는 전화번호 칸에 넣어줘",
        "이름 기준으로 직급이랑 이수시간을 비고 칸에 넣어줘",
    ],
)
def test_pointing_in_any_other_form_confirms_no_pair(phone_setup, request_text):
    """같은 이름 짝(전화번호→전화번호)이 지목한 칸을 덮어쓰지 않는다. 꼴이 다르면 묻는다."""
    result = run(phone_setup, request_text)
    assert result.fields == () and "target_column" in [q[0] for q in result.questions]


def test_pointing_in_the_canonical_form_still_works(phone_setup):
    result = run(phone_setup, "이름 기준으로 휴대전화를 전화번호 칸에 넣어줘")
    assert {(f, c) for f, c, _ in result.fields} == {("휴대전화", "전화번호")}


@pytest.mark.parametrize(
    "request_text, expected",
    [
        ("이름 기준으로 확인자 채워줘", ("fill", {("확인자", "확인자")})),
        ("이름 기준으로 입력일 채워줘", ("fill", {("입력일", "입력일")})),
        ("이름 기준으로 직급 참고사항 채워줘", ("fill", None)),
    ],
)
def test_verb_or_negation_letters_inside_names_are_part_of_the_name(
    phone_setup, request_text, expected
):
    """'확인자'·'입력일'의 동사 글자, '참고사항'의 '참고'는 이름의 일부다(5차 W01·W02·N01)."""
    work_type, pairs = expected
    result = run(phone_setup, request_text)
    assert result.work_type == work_type
    if pairs is not None:
        assert {(f, c) for f, c, _ in result.fields} == pairs
    else:  # 참고사항은 문서에 없다: 직급만 확정되고 참고사항은 소명된다
        assert ("직급", "직급") in {(f, c) for f, c, _ in result.fields}
        assert "참고사항" in result.excluded


# ---------- P04 ② 모델 역할 판독·값 형태 검증 ----------


@pytest.mark.parametrize(
    "request_text, expected",
    [
        ("이름 기준으로 이수시간 채워줘. 직급은 현행 유지", set()),
        ("이름 기준으로 이수시간 채워줘. 직급은 생략", set()),
        ("이름 기준으로 이수시간 채워줘. 제외: 직급", set()),
        ("이름 기준으로 직급 대신 이수시간 채워줘", set()),
        ("이름 기준으로 이수시간을 채우고 비고 칸에 직급을 적어줘", set()),
        # 문서는 직위: 직급과 직위는 값이 다른 항목이라 동의어표에 없다(DIF-055)
        ("이름 기준으로 직급이랑 이수시간 채워줘", {("이수시간", "이수시간")}),
    ],
)
def test_any_unknown_word_confirms_no_pair(setup, request_text, expected):
    """모르는 말이 하나라도 있으면 짝을 정하지 않는다. 이름별 국소 판정은 검수에서 계속 새어 버렸다(DIF-041)."""
    result = run(setup, request_text)
    assert {(f, c) for f, c, _ in result.fields} == expected


def test_pointing_needs_the_cell_right_after_the_object(setup):
    """'이수시간을 채우고 비고 칸에': '를'과 칸 사이에 동사가 있으면 지목이 아니다(4B 측정 L01)."""
    result = run(setup, "이름 기준으로 이수시간을 채우고 비고 칸에 직급을 적어줘")
    assert ("이수시간", "비고") not in {(f, c) for f, c, _ in result.fields}


def test_pairs_whose_value_shapes_clash_are_not_confirmed(tmp_path):
    """문서 값이 코드형(E-11)인데 취합표 열 기존 값이 숫자면 같은 이름이어도 확정하지 않는다."""
    docs = _single_table_docs(
        [
            [("성명", n), ("번호", v)]
            for n, v in zip(["가나다", "라마바"], ["E-11", "E-12"], strict=True)
        ]
    )
    sheet = [["이름", "번호"], ["가나다", "100"], ["라마바", "200"], ["사아자", "300"]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이름 기준으로 번호 확인해줘", documents, view)
    assert result.fields == () and "번호" in result.excluded


def test_model_choice_that_changes_with_option_order_is_not_trusted(setup):
    """첫 선택지만 고르는 모델(위치 편향)은 두 순서에서 답이 달라 확정되지 않는다(2B 실측 N01)."""
    asked = []
    result = run(setup, "이름 기준으로 점수 채워줘", first_option(asked))
    assert result.fields == () and len(asked) == 2
    # '없음'까지 한 칸씩 돌린다. 두 물음은 동시에 나가므로 순서와 무관하게 본다
    one, two = (options for _, options in asked)
    assert two == one[1:] + one[:1] or one == two[1:] + two[:1]


# ---------- PR #18 1차 검수: 별도 절의 부정·되가리킴, 키 바꾸기, 선택지 하나 ----------


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 직급이랑 이수시간 채워주지 마",
        "이름 기준으로 직원번호, 이수시간 채워줘. 둘 다 빼줘",
        "이름 기준으로 직원번호랑 이수시간 채워줘. 앞에 거는 빼줘",
        "이름 기준으로 직원번호랑 이수시간 채워줘 아니다 전부 하지 마",
        "이름 기준으로 직원번호랑 이수시간 넣을 필요 없어",
        "이름 기준으로 직원번호랑 이수시간 채워줘. 그건 다음 주에 할게",
        "이름 기준으로 직원번호를 번호 칸에, 이수시간도 채워줘. 앞에 건 취소",
    ],
)
def test_a_separate_clause_reaches_every_earlier_name(setup, request_text):
    """허용 낱말 뒤에 따로 나온 모르는 말은 앞의 모든 이름에 닿는다. 규칙이 만들어지지 않는다."""
    result = run(setup, request_text)
    _, _, view = setup
    assert build_rule(result, view) is None


def test_switching_the_key_is_asked(setup):
    """'이름 기준 말고 발급번호 기준으로': 키를 둘 말했으면 어느 쪽도 정하지 않는다(PR #18 H2)."""
    result = run(setup, "이름 기준 말고 발급번호 기준으로 이수시간 채워줘")
    assert result.key is None and "key_column" in [q[0] for q in result.questions]


def test_a_single_option_is_still_checked_for_position_bias():
    """선택지가 하나여도 '없음' 자리를 뒤집어 묻는다. 첫 자리만 고르는 답은 받지 않는다(PR #18 M1)."""
    asked = []
    assert _ask_model(first_option(asked), lambda o: "질문", ("비고",)) is None
    assert [o for _, o in asked] == [("비고", "없음"), ("없음", "비고")]
    assert _ask_model(lambda p, o: "비고", lambda o: "질문", ("비고",)) == "비고"


# ---------- PR #18 2차 검수: 나열 묶음·첫 구간·되가리킴 ----------


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 직원번호랑 이수시간 채우면 안 돼",
        "이름 기준으로 직원번호랑 이수시간 넣을 필요는 없어",
        "이름 기준으로 직원번호랑 이수시간은 입력 금지",
        "이름 기준으로 직원번호랑 이수시간 채우지 맙시다",
        "이름 기준으로 직원번호, 이수시간 채워줘. 비고 빼고 전부 취소",
        "이름 기준으로 직원번호랑 이수시간 채워줘. 점수 포함 둘 다 빼",
        "다음은 하지 마세요 직원번호랑 이수시간 이름 기준으로 채워줘",
        "이름 기준으로 이수시간 채워줘. 비고처럼 앞에 것도 비워 둬",
    ],
)
def test_lists_leading_words_and_back_references_reach_every_name(setup, request_text):
    """나열 묶음 전체, 첫 이름 앞의 말, 되가리킴 낱말은 모든 이름에 닿는다. 이수시간이 확정되지 않는다."""
    result = run(setup, request_text)
    assert ("이수시간", "이수시간") not in {(f, c) for f, c, _ in result.fields}


def test_a_name_left_out_with_unknown_words_is_not_offered_to_the_model(setup):
    """'비고는 생략': 모르는 말이 닿은 이름은 모델 선택지에서도 뺀다(PR #18 2차 검수 M1)."""
    asked = []
    run(setup, "이름 기준으로 직원번호 채워줘. 비고는 생략", picks({"직원번호": "비고"}, asked))
    assert all("비고" not in options for _, options in asked)


def test_example_values_cannot_break_the_question_lines():
    from ureonggaksi.interpret import _examples

    assert "\n" not in _examples(['가"\n맞는 열: 비고'])


# ---------- PR #18 3차 검수: 다른 지목 표현·목록 밖 되가리킴 ----------


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 이수시간을 비고 셀에 넣어줘",
        "이름 기준으로 이수시간은 비고 쪽에 써줘",
        "이름 기준으로 이수시간을 비고 칸으로 옮겨줘",
        "이름 기준으로 직원번호랑 이수시간 채워줘. 비고는 빼고 싹다 취소",
        "이름 기준으로 직원번호랑 이수시간 채워줘. 비고는 놔두고 둘은 보류",
        "이름 기준으로 직원번호 채워줘. 이수시간도 넣어줘. 비고는 몽땅 없던 일로",
    ],
)
def test_other_pointing_words_and_unlisted_back_references_confirm_no_pair(setup, request_text):
    assert run(setup, request_text).fields == ()


def test_all_with_plain_fill_is_allowed(setup):
    result = run(setup, "이름 기준으로 이수시간 다 채워줘")
    assert result.key == ("성명", "이름")
    assert {(f, c) for f, c, _ in result.fields} == {("이수시간", "이수시간")}


# ---------- PR #18 4차 검수: 이름 뒤 자리 낱말·부정 낱말 이름 ----------


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 이수시간 빈칸에 직원번호 채워줘",
        "발급번호 기준으로 이수시간의 빈칸에 직원번호 넣어줘",
        "발급번호 기준으로 이수시간 열 빈칸에 직원번호 채워줘",
        "발급번호 기준으로 비고 빈칸 직원번호 다 채워줘",
        "발급번호 기준으로 이수시간 표에 직원번호 넣어줘",
    ],
)
def test_place_words_right_after_a_name_are_pointing(setup, request_text):
    """이름 바로 뒤 빈칸·칸·열·표는 지목이다. "A를 B …" 꼴이 아니면 같은 이름 짝으로 확정하지 않는다."""
    assert run(setup, request_text).fields == ()


def test_pointing_with_a_blank_word_follows_the_pointing(setup):
    result = run(setup, "발급번호 기준으로 이수시간을 비고 빈칸에 넣어줘")
    assert {(f, c) for f, c, _ in result.fields} == {("이수시간", "비고")}


def test_a_column_named_like_an_exclusion_word_is_asked(tmp_path):
    """열 이름이 '제외'면 "직급은 제외"가 이름으로 가려진다. 그런 이름이 나오면 묻는다(4차 M1)."""
    docs = _single_table_docs(
        [
            [("성명", n), ("직급", t), ("이수시간", h)]
            for n, t, h in zip(["가나다", "라마바"], ["주무관", "팀장"], ["12", "8"], strict=True)
        ]
    )
    sheet = [
        ["이름", "직급", "이수시간", "제외"],
        ["가나다", None, None, None],
        ["라마바", None, None, None],
    ]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이름 기준으로 직급은 제외 이수시간 채워줘", documents, view)
    assert ("직급", "직급") not in {(f, c) for f, c, _ in result.fields}
    sheet[0][3] = "보류"
    (tmp_path / "b").mkdir()
    _, documents, view = build(tmp_path / "b", docs, sheet, "명단")
    result = interpret("이름 기준으로 직급 보류 이수시간만 채워줘", documents, view)
    assert ("직급", "직급") not in {(f, c) for f, c, _ in result.fields}


# ---------- PR #18 5차 검수: 지목은 단 하나의 정형 꼴만 ----------


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 직원번호 이수시간을 비고 칸에 넣어줘",
        "이름 기준으로 직원번호/이수시간을 비고 칸에 넣어줘",
        "이름 기준으로 이수시간을 비고 칸 기준으로 채워줘",
        "이름 기준으로 이수시간을 비고 칸 찾아서 채워줘",
        "이름 기준으로 이수시간을 비고 시트에 넣어줘",
        "이름 기준으로 이수시간을 비고 표에 넣어줘",
        "이름 기준으로 이수시간을 이름 칸에 넣어줘",
    ],
)
def test_pointing_is_confirmed_only_in_its_single_plain_form(setup, request_text):
    assert run(setup, request_text).fields == ()


def test_a_word_shared_by_two_names_is_not_read_as_either(tmp_path):
    """비고(1)·비고(2)일 때 '비고'는 어느 열인지 모른다. 실행할 때마다 다른 열을 고르지 않고 묻는다(5차 H3)."""
    docs = _single_table_docs(
        [
            [("성명", n), ("이수시간", h)]
            for n, h in zip(["가나다", "라마바"], ["12", "8"], strict=True)
        ]
    )
    sheet = [["이름", "비고(1)", "비고(2)"], ["가나다", None, None], ["라마바", None, None]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이름 기준으로 이수시간을 비고 칸에 넣어줘", documents, view)
    assert result.fields == ()


def test_a_column_named_none_is_not_offered():
    asked = []
    assert _ask_model(first_option(asked), lambda o: "질문", ("없음",)) is None and asked == []


def test_name_with_possessive_is_asked(setup):
    """'비고의 직급': 어느 칸에 무엇을 넣는지 갈린다. 짝을 정하지도 모델에 묻지도 않는다(5차 검수 P03)."""
    asked = []
    result = run(setup, "이름 기준으로 비고의 직원번호 채워줘", picks({"비고": "직원번호"}, asked))
    assert result.fields == () and asked == []


# ---------- PR #18 6차 검수: 키 경로 ----------


@pytest.mark.parametrize(
    "request_text",
    ["비고를 이름으로 채워줘", "이수시간은 성명으로 채워줘", "비고를 직원번호로 채워줘"],
)
def test_fill_with_ro_is_not_a_key(setup, request_text):
    """'B로 채워줘'는 B를 넣으라는 뜻일 수 있다. 키로 읽어 같은 이름 짝을 확정하지 않는다(6차 H1)."""
    _, _, view = setup
    assert build_rule(run(setup, request_text), view) is None


def test_a_named_key_needs_most_values_to_match_a_differently_named_column(tmp_path):
    """'연락처로 찾아서': 이름이 다른 열에서 값 하나가 우연히 맞아도 키가 아니다(6차 H2)."""
    docs = _single_table_docs(
        [
            [("성명", n), ("연락처", c), ("직급", t)]
            for n, c, t in zip(
                ["가나다", "라마바"], ["내선 101", "내선 102"], ["주무관", "팀장"], strict=True
            )
        ]
    )
    sheet = [["이름", "직급", "비고"], ["김라마", None, "내선 101"], ["이사아", None, None]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("연락처로 찾아서 직급 채워줘", documents, view)
    assert result.key is None


def test_a_column_named_like_an_allowed_word_is_not_a_name(tmp_path):
    """열 이름이 '기준'이어도 "발급번호 기준으로"의 '기준'은 키 표현이다(6차 K05)."""
    docs = _single_table_docs(
        [
            [("발급번호", b), ("직급", t)]
            for b, t in zip(["T-1", "T-2"], ["주무관", "팀장"], strict=True)
        ]
    )
    sheet = [["발급번호", "직급", "기준"], ["T-1", None, "x"], ["T-2", None, "y"]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("발급번호 기준으로 직급 채워줘", documents, view)
    assert result.key == ("발급번호", "발급번호")


def test_topic_marked_name_followed_by_another_name_is_asked(setup):
    """'비고는 직급 채워줘'는 대응을 말한 것일 수 있다(6차 M02)."""
    assert run(setup, "발급번호로 찾아서 비고는 이수시간 채워줘").fields == ()


# ---------- 모르는 말이 섞인 요청: 모델이 읽고 '확인 필요'로 표시(DIF-048), 1차 검수 회귀(DIF-050) ----------


def reads(roles, meant=None):
    """가짜 ask_many: 이름 역할과 모르는 말의 '열/역할'을 선택지 순서와 무관하게 같은 답으로 준다."""

    def ask(prompt, fields):
        out = {}
        for key, options in fields.items():
            name = key.split("] ", 1)[1]
            answer = roles.get(name) if key.startswith("[이름]") else (meant or {}).get(name)
            if answer in options:
                out[key] = answer
        return out

    return ask


def interpret_with(setup, request, roles, meant=None, choose=None):
    _, docs, view = setup
    return interpret(request, docs, view, choose=choose, ask_many=reads(roles, meant))


def _referral(name, referrer_id, hours):
    rows = [("성명", name), ("추천인사번", referrer_id), ("이수시간", hours)]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


def test_number_like_column_does_not_break_a_key_tie(tmp_path):
    """H1: 남의 사번(추천인사번)이 사번 열과 맞아도 성명과 동점이면 묻는다. 값 모양으로 가르지 않는다."""
    people = [("가나다", "S-1002", "12"), ("라마바", "S-1003", "8"), ("사아자", "S-1001", "4")]
    sheet = [
        ["사번", "성명", "이수시간"],
        ["S-1001", "가나다", None],
        ["S-1002", "라마바", None],
        ["S-1003", "사아자", None],
    ]
    _, documents, view = build(tmp_path, [_referral(*p) for p in people], sheet, "명단")
    for ask_many in (None, reads({"이수시간": "채움"})):
        result = interpret("이수시간 채워 주시면 돼요", documents, view, ask_many=ask_many)
        assert result.key is None and build_rule(result, view) is None


def test_an_unnamed_same_name_key_is_assumed(tmp_path):
    """말하지 않은 키: 이름이 같고 문서 값이 모두 그 열에 있는 후보 하나면 쓰고 '확인 필요'(2026-11-23 결정)."""
    people = [("가나다", "S-1", "12"), ("라마바", "S-2", "8"), ("사아자", "S-3", "4")]
    sheet = [["이수시간", "성명"], [None, "가나다"], [None, "라마바"], [None, "사아자"]]
    _, documents, view = build(tmp_path, [_referral(*p) for p in people], sheet, "명단")
    named = interpret("성명 기준으로 이수시간 채워줘", documents, view)
    assert named.key == ("성명", "성명") and not named.key_assumed
    guessed = interpret("이수시간 채워줘", documents, view)
    assert guessed.key == ("성명", "성명") and guessed.key_assumed
    assert "key_column" not in dict(guessed.questions)


def _person(name, number, hours):
    rows = [("성명", name), ("관리번호", number), ("이수시간", hours)]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


def test_an_unnamed_key_is_assumed_when_every_match_points_to_the_same_rows(tmp_path):
    """성명·관리번호가 모두 맞아도 각 문서를 같은 행으로 보내면 어느 쪽이든 같다 → 추정."""
    people = [("가나다", "A-11", "12"), ("라마바", "A-12", "8"), ("사아자", "A-13", "4")]
    sheet = [
        ["관리번호", "성명", "이수시간"],
        ["A-13", "사아자", None],
        ["A-11", "가나다", None],
        ["A-12", "라마바", None],
    ]
    _, documents, view = build(tmp_path, [_person(*p) for p in people], sheet, "명단")
    result = interpret("이수시간 채워줘", documents, view)
    assert result.key_assumed and result.key in (("성명", "성명"), ("관리번호", "관리번호"))


def test_an_unnamed_key_is_asked_when_same_name_columns_point_to_different_rows(tmp_path):
    """성명은 1행, 관리번호는 2행을 가리키면 어느 쪽이 맞는지 모른다 → 묻는다."""
    people = [("가나다", "A-12", "12"), ("라마바", "A-13", "8"), ("사아자", "A-11", "4")]
    sheet = [
        ["관리번호", "성명", "이수시간"],
        ["A-11", "가나다", None],
        ["A-12", "라마바", None],
        ["A-13", "사아자", None],
    ]
    _, documents, view = build(tmp_path, [_person(*p) for p in people], sheet, "명단")
    result = interpret("이수시간 채워줘", documents, view)
    assert result.key is None and "key_column" in dict(result.questions)


def test_an_unnamed_key_is_not_assumed_when_a_partial_match(tmp_path):
    """문서 값 하나라도 열에 없으면 추정하지 않는다."""
    people = [("가나다", "S-1", "12"), ("라마바", "S-2", "8"), ("하하하", "S-3", "4")]
    sheet = [["이수시간", "성명"], [None, "가나다"], [None, "라마바"], [None, "사아자"]]
    _, documents, view = build(tmp_path, [_referral(*p) for p in people], sheet, "명단")
    result = interpret("이수시간 채워줘", documents, view)
    assert result.key is None and "key_column" in dict(result.questions)


def test_an_unnamed_key_is_not_assumed_from_a_name_the_request_used(tmp_path):
    """요청이 채우라고 말한 이름은 키로 삼지 않는다."""
    people = [("가나다", "S-1", "12"), ("라마바", "S-2", "8"), ("사아자", "S-3", "4")]
    sheet = [["이수시간", "성명"], [None, "가나다"], [None, "라마바"], [None, "사아자"]]
    _, documents, view = build(tmp_path, [_referral(*p) for p in people], sheet, "명단")
    assert interpret("성명이랑 이수시간 채워줘", documents, view).key is None


@pytest.mark.parametrize(
    "request_text",
    ["이름 기준으로 직급 비고 칸에 채워줘", "이름 기준으로 직급 비고칸에 넣어줘"],
)
def test_a_slot_after_another_name_is_pointing_and_is_asked(setup, request_text):
    """H2: '직급 비고 칸에'는 직급을 비고에 넣으라는 말일 수 있다. 직급 열에 넣지 않고 묻는다."""
    for ask_many in (None, reads({"직급": "채움", "비고": "채움"})):
        _, docs, view = setup
        result = interpret(request_text, docs, view, ask_many=ask_many)
        assert result.fields == () and build_rule(result, view) is None


@pytest.mark.parametrize(
    "request_text",
    [
        "이름 기준으로 직급 말고도 이수시간도 채워줘",
        "이름 기준으로 직급 비워진 칸이랑 이수시간 채워줘",
    ],
)
def test_a_code_negation_the_model_does_not_share_is_left_blank(setup, request_text):
    """H3: 코드가 빼기로 읽었는데 모델은 채우라고 읽으면 그 이름은 채우지도 '뺐다'고도 하지 않고 비워
    두며 갈렸다고 알린다. 나머지 확실한 짝은 채운다(DIF-062, 전에는 요청 전체를 물었다)."""
    result = interpret_with(setup, request_text, {"직급": "채움", "이수시간": "채움"})
    assert [(f, c) for f, c, _ in result.fields] == [("이수시간", "이수시간")]
    assert result.skipped == () and result.conflicted == ("직급",) and "직급" in result.excluded


def test_an_agreed_exclusion_is_reported_as_unsure_not_as_unresolved(setup):
    """H3·M3: 코드와 모델이 모두 빼라고 읽으면 빼되, '확인 필요'로 보이게 skipped에 남긴다."""
    result = interpret_with(
        setup,
        "이름 기준으로 직급 말고 이수시간 채워 주세요 ㅎㅎ",
        {"직급": "제외", "이수시간": "채움"},
    )
    assert [(f, c) for f, c, _ in result.fields] == [("이수시간", "이수시간")]
    assert result.skipped == ("직급",) and "직급" not in result.excluded
    model_only = interpret_with(
        setup,
        "이름 기준으로 직급이랑 이수시간 채우고 직급은 현행 유지",
        {"직급": "제외", "이수시간": "채움"},
    )
    assert model_only.skipped == ("직급",) and all(c != "직급" for _, c, _ in model_only.fields)


def test_a_synonym_the_user_excludes_is_not_filled(setup):
    """M2: 모르는 말 '직책'이 직급을 가리켜도 빼라는 말이면 채우지 않는다. 채우라는 말과 겹치면 묻는다."""
    excluded = interpret_with(
        setup,
        "이름 기준으로 이수시간 채우고 직책은 이번엔 놔두세요",
        {"이수시간": "채움"},
        {"직책": "직급/제외"},
        choose=picks({"직급": "직위"}),
    )
    assert all(c != "직급" for _, c, _ in excluded.fields) and "직급" in excluded.skipped
    clash = interpret_with(
        setup,
        "이름 기준으로 직급이랑 이수시간 채우고 직책은 이번엔 놔두세요",
        {"직급": "채움", "이수시간": "채움"},
        {"직책": "직급/제외"},
        choose=picks({"직급": "직위"}),
    )
    assert all(c != "직급" for _, c, _ in clash.fields) and "직급" in clash.conflicted


def test_model_paths_are_marked_and_answers_outside_the_lists_are_ignored(setup):
    """M6: 계약을 어긴 답(목록 밖 이름·열)은 버린다. 이 경로의 짝은 모두 assumed다."""

    def rogue(prompt, fields):
        return {
            "[이름] 없는이름": "채움",
            **dict.fromkeys(fields, "없는열"),
            "[이름] 이수시간": "채움",
        }

    _, docs, view = setup
    result = interpret("이름 기준으로 이수시간 채워 주시면 돼요", docs, view, ask_many=rogue)
    assert [(f, c, s) for f, c, s in result.fields] == [("이수시간", "이수시간", "assumed")]


def test_unknown_words_leave_out_verbs_the_code_already_knows(setup):
    """M4: '채우고'·'놔두세'처럼 코드가 아는 동사는 열 이름의 다른 말로 묻지 않는다(키 낭비·답 잘림)."""
    from ureonggaksi.interpret import _unknown_words

    _, _, view = setup
    words = _unknown_words(
        "이름 기준으로 이수시간 채우고 직책은 이번엔 놔두세요", list(view.columns)
    )
    assert "직책" in words and not {"채우고", "놔두세", "놔두세요"} & set(words)


@pytest.mark.parametrize(
    "request_text", ["이름 기준으로 이수시간 입력했어?", "이름 기준으로 이수시간 채워졌어?"]
)
def test_question_mark_requests_ask_for_the_work_type(setup, request_text):
    """M1: 묻는 말은 채우기로 읽지 않는다."""
    assert interpret_with(setup, request_text, {"이수시간": "채움"}).work_type is None


def test_a_code_negation_the_model_calls_unrelated_is_skipped_and_marked(setup):
    """2차 M-2: 실제 4B는 뺄 항목을 '무관'으로 답한다. 코드 빼기 표지와 어긋나지 않으므로 빼고 표시한다."""
    result = interpret_with(
        setup,
        "이름 기준으로 직급은 빼고 이수시간 채워 주시면 돼요",
        {"직급": "무관", "이수시간": "채움"},
    )
    assert result.skipped == ("직급",) and [c for _, c, _ in result.fields] == ["이수시간"]


def test_a_single_name_with_a_slot_word_is_still_filled(setup):
    """2차 L-4: 'X 칸'은 다른 항목 이름이 없으면 X를 채우라는 말이다."""
    result = run(setup, "이름 기준으로 이수시간 칸 채워줘")
    assert result.fields == (("이수시간", "이수시간", "match"),)


def test_words_beyond_the_model_limit_are_warned_not_dropped(setup):
    """3차 M3: 모델에 묻는 모르는 말은 8개까지다. 넘친 말은 unread로 남긴다."""
    request = (
        "이름 기준으로 이수시간 채워 주시면 정말 너무너무 감사하겠습니다 그리고 혹시 가능하시다면 "
        "오늘 안으로 담당업무도 부탁드려요"
    )
    result = interpret_with(setup, request, {"이수시간": "채움"})
    assert "담당업무" in result.unread


@pytest.mark.parametrize(
    "request_text",
    ["이름 기준으로 직급 비고 부분에 넣어 주세요", "이름 기준으로 직급 비고 자리에 넣어줘"],
)
def test_other_place_words_are_pointing_too(setup, request_text):
    """3차 M2: '부분·자리'도 '칸'처럼 지목이다. 직급을 직급 열에 넣지 않고 묻는다."""
    result = interpret_with(setup, request_text, {"직급": "채움", "비고": "채움"})
    assert result.fields == ()


def test_a_place_word_inside_another_word_is_not_pointing(setup):
    """4차 L1: '부분적으로'는 지목이 아니다."""
    result = interpret_with(
        setup,
        "이름 기준으로 직급 이수시간 부분적으로 채워 주세요",
        {"직급": "채움", "이수시간": "채움"},
    )
    assert set(result.fields) == {("이수시간", "이수시간", "assumed")}


# ---------- 동의어표(DIF-055) ----------


def _birth(number, name, birth, hours="12"):
    rows = [("발급번호", number), ("성명", name), ("생년월일", birth), ("이수시간", hours)]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


@pytest.fixture
def birth_setup(tmp_path):
    docs = [
        _birth("A-01", "가나다", "1990-01-02", "12"),
        _birth("A-02", "라마바", "1985-03-04", "8"),
    ]
    sheet = [
        ["발급번호", "신청인", "생일", "이수시간"],
        ["A-01", None, None, None],
        ["A-02", None, None, None],
    ]
    return build(tmp_path, docs, sheet, "명단")


def test_a_listed_synonym_pairs_without_the_model(birth_setup):
    """같은 묶음의 후보가 양쪽 모두 하나뿐이면 모델 없이 짝짓는다. 출처는 synonym이라 '확인 필요'로 보인다."""
    _, docs, view = birth_setup
    asked = []
    result = interpret(
        "발급번호로 찾아서 신청인이랑 생일 채워줘", docs, view, choose=first_option(asked)
    )
    assert set(result.fields) == {("성명", "신청인", "synonym"), ("생년월일", "생일", "synonym")}
    assert asked == []


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 생일은 나중에 하고 신청인이랑 이수시간 넣어줘",
        "발급번호 기준으로 생일은 다음에 하고 신청인이랑 이수시간 넣어줘",
        "발급번호 기준으로 생일은 미루고 신청인이랑 이수시간 넣어줘",
        "발급번호 기준으로 생일은 아직 하지 말고 신청인이랑 이수시간 넣어줘",
        "발급번호 기준으로 생일은 나중에 넣을게 신청인이랑 이수시간 넣어줘",
        "발급번호 기준으로 생일은 다음에 처리하고 신청인이랑 이수시간 넣어줘",
        "발급번호 기준으로 생일은 이따가 하고 신청인이랑 이수시간 넣어줘",
        "발급번호 기준으로 생일은 나중으로 하고 신청인이랑 이수시간 넣어줘",
        "발급번호 기준으로 신청인이랑 이수시간 넣어줘, 생일은 아직이야",
    ],
)
def test_a_column_put_off_for_later_is_never_filled(birth_setup, request_text):
    """실제 4B는 '생일은 나중에 하고'의 생일을 '채움'으로 읽었다(DIF-060). 코드가 미루기로 읽은 열은
    모델이 채우라고 해도 채우지 않는다: 모델이 빼기로 읽으면 빼고, 아니면 묻는다."""
    _, docs, view = birth_setup
    for role in ("채움", "무관"):
        reader = reads({"생일": role, "신청인": "채움", "이수시간": "채움"})
        result = interpret(request_text, docs, view, ask_many=reader)
        assert "생일" not in {c for _, c, _ in result.fields}
        if role == "채움":  # 갈린 생일만 비우고 확실한 짝은 모두 채운다(DIF-062)
            assert result.conflicted == ("생일",)
            assert {c for _, c, _ in result.fields} == {"신청인", "이수시간"}
    assert "생일" in result.skipped and result.conflicted == ()  # '무관'이면 빼라는 말로 정리된다


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 신청인 다음에 생일 넣어줘",
        "발급번호 기준으로 이수시간 다음에도 신청인이랑 생일 채워",
        "발급번호 기준으로 생일 아직 안 채운 칸만 신청인이랑 생일 넣어줘",
        "발급번호 기준으로 생일은 나중에 확인할 수 있게 신청인이랑 생일 넣어줘",
        "발급번호 기준으로 신청인이랑 생일 다음 주까지 채워 주세요",
        "발급번호 기준으로 신청인이랑 생일 다음에 해당하는 칸 채워",
    ],
)
def test_order_and_blank_words_are_not_put_offs(birth_setup, request_text):
    """'다음에'(순서)·'아직 안 채운'(빈칸)·'나중에 확인할'은 미루기가 아니다. 모델이 채우라면 채운다(PR #27 검수)."""
    _, docs, view = birth_setup
    reader = reads({"생일": "채움", "신청인": "채움", "이수시간": "채움"})
    result = interpret(request_text, docs, view, ask_many=reader)
    assert {"신청인", "생일"} <= {c for _, c, _ in result.fields}


def test_a_put_off_reaches_names_joined_before_it(birth_setup):
    """'빼고'처럼 미루기도 '랑'으로 이어진 앞 이름까지 걸린다."""
    _, docs, view = birth_setup
    reader = reads({"생일": "무관", "신청인": "무관", "이수시간": "채움"})
    result = interpret(
        "발급번호 기준으로 신청인이랑 생일은 나중에 하고 이수시간 넣어줘",
        docs,
        view,
        ask_many=reader,
    )
    assert [c for _, c, _ in result.fields] == ["이수시간"]
    assert {"신청인", "생일"} <= set(result.skipped)


def test_two_candidates_in_one_synonym_group_go_to_the_model(tmp_path):
    """문서에 성명과 신청인명이 함께 있으면 '이름' 열에 어느 것인지 표로 정하지 않고 모델에 묻는다."""
    rows = [("발급번호", "A-01"), ("성명", "가나다"), ("신청인명", "라마바"), ("이수시간", "12")]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    docs = [["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]]
    sheet = [["발급번호", "이름", "이수시간"], ["A-01", None, None]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    asked = []
    result = interpret("발급번호 기준으로 이름 채워줘", documents, view, choose=first_option(asked))
    assert asked and all(src != "synonym" for _, _, src in result.fields)


def test_a_synonym_pair_still_needs_matching_value_shapes(tmp_path):
    """동의어표로 지은 짝도 값 형태 검사를 거친다(날짜 값을 연락처 열에 넣지 않는다)."""
    rows = [("발급번호", "A-01"), ("연락처", "2026-03-01"), ("이수시간", "12")]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    docs = [["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]]
    sheet = [
        ["발급번호", "휴대폰", "이수시간"],
        ["A-01", "010-1111-2222", None],
        ["A-02", None, None],
    ]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("발급번호 기준으로 휴대폰 채워줘", documents, view)
    assert result.fields == ()


def test_a_request_word_in_the_synonym_table_is_read_like_a_name(birth_setup):
    """요청의 '이름'은 동의어표로 신청인 열을 가리키고, 채우라는 뜻인지는 모델의 역할 판단으로 정한다(검수 M1)."""
    _, docs, view = birth_setup
    request = "발급번호 기준으로 이름이랑 이수시간 채워 주시면 돼요"
    filled = interpret(request, docs, view, ask_many=reads({"이수시간": "채움", "신청인": "채움"}))
    assert ("성명", "신청인", "synonym") in filled.fields
    unsure = interpret(request, docs, view, ask_many=reads({"이수시간": "채움"}))
    assert all(c != "신청인" for _, c, _ in unsure.fields) and "이름" in unsure.unread


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 이수시간 채우고 이름이랑 이메일은 빼 주세요",
        "발급번호 기준으로 이수시간 채우고 이름은 이번엔 놔두고요",
        "발급번호 기준으로 이수시간 채우고 이름 채우는 건 다음 주에요",
        "발급번호 기준으로 이름은 비워 두고 이수시간 채워 주세요",
        "발급번호 기준으로 이름은 그대로 두고 이수시간 채워 주세요",
    ],
)
@pytest.mark.parametrize("role", ["채움", "무관"])
def test_a_request_word_followed_by_a_skip_is_not_filled(birth_setup, request_text, role):
    """같은 마디에 빼기·미루기 표현이 있는 동의어 열은 모델 답과 무관하게 그 열만 빼고 표시한다.
    다른 칸(이수시간)은 그대로 채운다(검수 M1·M-1·L-1·L-2)."""
    _, docs, view = birth_setup
    reader = reads({"이수시간": "채움", "신청인": role})
    result = interpret(request_text, docs, view, ask_many=reader)
    assert [c for _, c, _ in result.fields] == ["이수시간"] and "신청인" in result.skipped


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 이름이랑 이수시간 다음 주까지 채워 주세요",
        "발급번호 기준으로 이름 빼먹지 말고 이수시간이랑 채워 주세요",
    ],
)
def test_ordinary_words_are_not_read_as_skips(birth_setup, request_text):
    """'다음 주까지'·'빼먹지 말고'는 미루거나 빼라는 말이 아니다(검수 L-1)."""
    _, docs, view = birth_setup
    result = interpret(
        request_text, docs, view, ask_many=reads({"이수시간": "채움", "신청인": "채움"})
    )
    assert {c for _, c, _ in result.fields} == {"이수시간", "신청인"}


@pytest.mark.parametrize(
    "request_text, expected",
    [
        ("이름 기준으로 이수시간 넣어 줄래?", "fill"),
        ("이름 기준으로 이수시간 채워 줄 수 있어?", "fill"),
        ("이름 기준으로 이수시간 입력했어?", None),
        ("이름 기준으로 이수시간 맞는지 확인해 줄래?", "check"),
        ("이름 기준으로 이수시간 입력했는지 봐 줄래?", None),
    ],
)
def test_polite_requests_ending_in_a_question_mark_are_requests(setup, request_text, expected):
    assert run(setup, request_text).work_type == expected


def test_a_synonym_needs_a_single_candidate_in_both_directions(tmp_path):
    """문서에 신청인(보호자)과 대상자명이 함께 있으면 '신청인 채워줘'도 성명 열에 표로 짓지 않는다(검수 M3)."""
    rows = [("발급번호", "A-01"), ("신청인", "보호자"), ("대상자명", "본인"), ("이수시간", "12")]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    sheet = [["발급번호", "성명", "이수시간"], ["A-01", None, None]]
    docs = [["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("발급번호 기준으로 신청인 채워줘", documents, view)
    assert all(src != "synonym" for _, _, src in result.fields)


# ---------- "전부/다 채워": 이름 없이 모든 항목 (DIF-063) ----------


def test_fill_everything_pairs_same_names_and_synonyms_without_a_model(setup):
    """'다 채워'는 문서 항목 전부가 후보다. 모델 없이는 같은 이름·동의어표 짝만, 나머지는 비우고 소명한다."""
    result = run(setup, "발급번호 기준으로 다 채워줘")
    # 사용자가 그 열을 말한 것이 아니므로 같은 이름도 '확인 필요'(assumed)다(검수 H-3)
    assert set(result.fields) == {("성명", "이름", "synonym"), ("이수시간", "이수시간", "assumed")}
    assert result.questions == ()
    assert {"직위", "평가점수", "연락처", "직원번호"} <= set(result.excluded)


def test_fill_everything_asks_the_model_once_for_the_rest(setup):
    _, docs, view = setup
    calls = []

    def many(prompt, fields):
        calls.append(dict(fields))
        return reads({}, {"직위": "직급", "평가점수": "점수"})(prompt, fields)

    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=many)
    assert ("직위", "직급", "model") in result.fields and (
        "평가점수",
        "점수",
        "model",
    ) in result.fields
    # 두 번(순서를 뒤집어) 묻고, 한 번에 남은 항목을 모두 묻는다. 이미 정한 열·항목은 묻지 않는다
    assert len(calls) == 2 and all("[항목] 성명" not in c for c in calls)
    assert all("이름" not in options for c in calls for options in c.values())


def test_fill_everything_keeps_only_answers_both_orders_agree_on(setup):
    _, docs, view = setup
    turns = iter([reads({}, {"직위": "직급"}), reads({}, {"직위": "비고"})])

    def disagree(prompt, fields):
        return next(turns)(prompt, fields)

    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=disagree)
    assert "직위" not in {f for f, _, _ in result.fields} and "직위" in result.excluded


def test_fill_everything_never_puts_two_items_in_one_column(setup):
    _, docs, view = setup
    reader = reads({}, {"직위": "직급", "평가점수": "직급"})
    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=reader)
    assert "직급" not in {c for _, c, _ in result.fields}


def test_fill_everything_leaves_out_what_the_request_skips(setup):
    """'연락처는 빼고': 뺀 항목은 채우지 않고 모델 선택지·물음에도 없다."""
    _, docs, view = setup
    calls = []

    def many(prompt, fields):
        calls.append(dict(fields))
        return reads({"연락처": "제외"}, {"연락처": "비고", "전부": "예"})(prompt, fields)

    result = interpret(
        "발급번호 기준으로 문서에 있는 거 전부 넣어줘. 연락처는 빼고", docs, view, ask_many=many
    )
    assert "연락처" not in {f for f, _, _ in result.fields} and "연락처" in result.skipped
    assert all("[항목] 연락처" not in c for c in calls)
    # 모르는 말이 섞인 요청의 같은 이름 짝은 '확인 필요'로 보인다
    assert ("이수시간", "이수시간", "assumed") in result.fields


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 다른 거 채워줘",  # '다른'은 '다'가 아니다
        "발급번호 기준으로 이수시간 다 채워줘",  # 이름을 말했으면 그 이름만
    ],
)
def test_fill_everything_needs_the_word_alone_and_no_named_items(setup, request_text):
    result = run(setup, request_text)
    assert {f for f, _, _ in result.fields} <= {"이수시간"}
    assert "성명" not in {f for f, _, _ in result.fields}


def test_fill_everything_with_unknown_words_and_no_model_fills_nothing(setup):
    result = run(setup, "발급번호 기준으로 문서에 있는 거 전부 넣지 말고 확인만")
    assert result.fields == ()


def test_fill_everything_is_off_when_only_the_model_reads_a_name_as_skipped(setup):
    """검수 r2 M-1: 코드가 빼기로 읽지 않은 이름을 모델만 '제외'로 읽으면 끈다. 모델 오판 하나로 "직급만"이
    "직급 빼고 전부"로 뒤집힐 수 있다. 모델이 채우라고 읽어도 그 이름만 채우는 요청이다."""
    _, docs, view = setup
    request = "발급번호 기준으로 보고서에 있는 거 다 채워줘. 직급은 내가 따로 정할게"
    for role in ("제외", "채움"):
        reader = reads({"직급": role}, {"평가점수": "점수", "전부": "예"})
        result = interpret(request, docs, view, ask_many=reader)
        assert not {"이름", "이수시간", "점수"} & {c for _, c, _ in result.fields}


def everyone_fills(prompt, fields):
    """가짜 모델: 이름은 모두 '채움', 항목은 아는 짝(직위→직급, 평가점수→점수, 연락처→비고)."""
    known = {"직위": "직급", "평가점수": "점수", "연락처": "비고"}
    out = {}
    for key, options in fields.items():
        name = key.split("] ", 1)[1]
        answer = "채움" if key.startswith("[이름]") else known.get(name, "없음")
        if answer in options:
            out[key] = answer
    return out


@pytest.mark.parametrize(
    "request_text",
    ["발급번호 기준으로 교육시간만 다 채워주실래요", "발급번호 기준으로 성함만 전부 넣어주실래요"],
)
def test_fill_everything_is_off_when_a_synonym_names_the_target(setup, request_text):
    """검수 H-1: 동의어로 한 열을 말했으면 그 열만 채우는 요청이다. 다른 항목을 후보로 삼지 않는다."""
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=everyone_fills)
    assert len(result.fields) <= 1
    assert not {"직급", "점수", "비고"} & {c for _, c, _ in result.fields}


@pytest.mark.parametrize(
    "request_text",
    ["발급번호 기준으로 직급만 다 채워주실래요", "발급번호 기준으로 전부 빼고 이수시간만 채워줘"],
)
@pytest.mark.parametrize("role", ["무관", None])
def test_fill_everything_is_off_when_a_named_item_is_not_read(setup, request_text, role):
    """검수 H-2: 말한 이름을 모델이 '채움'으로 정하지 못했다고 그 이름만 빼고 나머지를 채우면 뜻이 뒤집힌다."""
    _, docs, view = setup
    roles = {"직급": role, "이수시간": role} if role else {}
    result = interpret(request_text, docs, view, ask_many=reads(roles, {"평가점수": "점수"}))
    assert not {"이름", "점수", "비고"} & {c for _, c, _ in result.fields}


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 전부 넣지 마",
        "발급번호 기준으로 다 채우지 마세요",
        "발급번호 기준으로 전부 비워 둬",
        "발급번호 기준으로 다 빼줘",
        "발급번호 기준으로 전부 넣으면 안 돼",
        "발급번호 기준으로 다 하지 말고 확인만 해",
    ],
)
def test_fill_everything_is_off_for_negated_requests(setup, request_text):
    """검수 H-4: 하지 말라는 '전부'는 전부가 아니다. 작업 종류를 물은 뒤 채우기로 답해도 켜지지 않는다."""
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=everyone_fills)
    assert not {"이름", "직급", "점수", "비고"} & {c for _, c, _ in result.fields}


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 다른 거 채워줘",
        "발급번호 기준으로 다음 칸 채워줘",
        "발급번호 기준으로 채워주면 됩니다.",
    ],
)
def test_fill_everything_needs_the_word_alone_even_with_a_model(setup, request_text):
    """검수 M-4: '다른·다음·됩니다'는 '다'가 아니다(모델이 있어도 정규식이 막는다)."""
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=everyone_fills)
    assert not {"직급", "점수", "비고"} & {c for _, c, _ in result.fields}


def test_fill_everything_drops_none_and_off_list_answers(setup):
    """검수 M-4: 두 번 다 '없음'이거나 목록 밖(키 열·뺀 열)을 답하면 짝을 짓지 않는다."""
    _, docs, view = setup

    def odd(prompt, fields):
        out = {}
        for key in fields:
            if key.startswith("[항목]"):
                out[key] = {"[항목] 직위": "발급번호", "[항목] 평가점수": "비고"}.get(key, "없음")
        if "[요청] 전부" in fields:  # 전부를 켜야 이 경로를 시험한다
            out["[요청] 전부"] = "예"
        return out

    result = interpret(
        "발급번호 기준으로 문서에 있는 거 전부 넣어줘. 비고는 빼고", docs, view, ask_many=odd
    )
    columns = {c for _, c, _ in result.fields}
    assert "발급번호" not in columns and "비고" not in columns and "없음" not in columns


def test_fill_everything_flips_the_choices_on_the_second_ask(setup):
    _, docs, view = setup
    calls = []

    def many(prompt, fields):
        calls.append({k: v for k, v in fields.items() if k.startswith("[항목]")})
        return {}

    interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=many)
    first, second = calls[0], calls[1]
    key = next(iter(first))
    assert list(first) == list(reversed(list(second)))
    assert first[key][:-1] == tuple(reversed(second[key][:-1])) and first[key][-1] == "없음"


def test_fill_everything_keeps_going_when_the_model_fails(setup):
    """검수 M-1: 모델 오류면 같은 이름·동의어 짝만 채우고 나머지는 소명한다(예외로 끝나지 않는다)."""
    from ureonggaksi.errors import ServiceError

    _, docs, view = setup

    def boom(prompt, fields):
        if any(k.startswith("[항목]") for k in fields):
            raise ServiceError("AI_TIMEOUT", "시간 초과")
        return {}

    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=boom)
    assert ("이수시간", "이수시간", "assumed") in result.fields and "직위" in result.excluded


def test_fill_everything_does_not_offer_a_column_that_differs_only_in_unit(tmp_path):
    """검수 M-2: 예산(천원) 항목에 예산(원) 열을 선택지로 주지 않는다(값이 천 배 틀린다)."""

    def doc(number, budget):
        rows = [("성명", "가"), ("예산(천원)", budget)]
        body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
        return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    sheet = [["발급번호", "예산(원)"], ["A-01", None], ["A-02", None]]
    _, docs, view = build(tmp_path, [doc("A-01", "100"), doc("A-02", "200")], sheet)
    asked = []

    def many(prompt, fields):
        asked.extend(o for k, o in fields.items() if k == "[항목] 예산(천원)")
        key = "[항목] 예산(천원)"
        return {key: "예산(원)"} if key in fields else {}

    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=many)
    assert all("예산(원)" not in o for o in asked)
    assert "예산(천원)" not in {f for f, _, _ in result.fields}


def test_fill_everything_is_off_for_a_single_document(tmp_path):
    """검수 M-3: 문서 한 장이면 항목명과 값을 가르지 못해 값이 항목으로 잡힌다. 전부를 켜지 않는다."""
    _, docs, view = build(tmp_path, [certificate(*PEOPLE[0])], SHEET)
    asked = []

    def many(prompt, fields):
        asked.extend(k for k in fields if k.startswith("[항목]"))
        return {}

    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=many)
    assert asked == [] and result.fields == ()


def test_fill_everything_is_off_when_a_synonym_the_model_did_not_read_names_the_target(setup):
    """검수 H-1: 모델이 '교육시간'에 답하지 않아도 사용자는 한 열을 말했다. 전부로 넓히지 않는다."""
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 교육시간만 다 채워주실래요",
        docs,
        view,
        ask_many=reads({}, {"직위": "직급", "평가점수": "점수"}),
    )
    assert not {"직급", "점수"} & {c for _, c, _ in result.fields}


def test_fill_everything_is_off_when_filling_is_negated_in_another_clause(setup):
    """'다 안 채워도 돼, 확인해줘': 작업 종류가 정해져도 채우지 말라는 말이 있으면 전부가 아니다."""
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 다 안 채워도 돼, 확인해줘", docs, view, ask_many=everyone_fills
    )
    assert not {"이름", "직급", "점수", "비고"} & {c for _, c, _ in result.fields}


def test_fill_everything_needs_the_work_type_from_the_request(setup):
    """'전부 해줘'처럼 작업 종류를 요청에서 정하지 못하면 켜지 않는다(물은 뒤 답으로 넓히지 않는다)."""
    _, docs, view = setup
    result = interpret("발급번호 기준으로 전부 해줘", docs, view, ask_many=everyone_fills)
    assert result.fields == ()


def test_fill_everything_is_off_when_the_model_reads_a_word_as_a_column(setup):
    """'수료시간만 다': 동의어표에 없는 말을 모델이 이수시간 열로 읽었으면 그 열만 채우는 요청이다."""
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 수료시간만 다 채워주실래요",
        docs,
        view,
        ask_many=reads({}, {"수료시간": "이수시간/채움", "직위": "직급", "평가점수": "점수"}),
    )
    assert not {"직급", "점수", "이름"} & {c for _, c, _ in result.fields}


# ---------- '전부' 3차: 모르는 말이 섞이면 모델이 두 번 모두 '전부'라고 읽어야 켠다 (검수 r2) ----------


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 수료시간만 다 채워주실래요",
        "발급번호 기준으로 시수만 전부 넣어주실래요",
        "발급번호 기준으로 다 됐으면 채워줘",
        "발급번호 기준으로 모두 수고 많으셨어요 채워줘",
    ],
)
def test_fill_everything_with_unknown_words_needs_the_model_to_say_so(setup, request_text):
    """검수 r2 H-1·M-3: 모르는 말을 모델이 열과 잇지 못해도, 모델이 '전부'라고 읽지 않으면 켜지 않는다."""
    _, docs, view = setup
    result = interpret(
        request_text, docs, view, ask_many=reads({}, {"직위": "직급", "평가점수": "점수"})
    )
    assert not {"이름", "직급", "점수"} & {c for _, c, _ in result.fields}


def test_fill_everything_is_off_when_reading_the_request_fails(setup):
    """검수 r2 H-2: 요청 읽기에서 AI 오류가 나면 뜻을 모르니 켜지 않는다."""
    from ureonggaksi.errors import ServiceError

    _, docs, view = setup

    def boom(prompt, fields):
        raise ServiceError("AI_TIMEOUT", "시간 초과")

    result = interpret("발급번호 기준으로 수료시간만 다 채워주실래요", docs, view, ask_many=boom)
    assert result.fields == ()


def test_fill_everything_is_on_when_the_model_agrees_twice(setup):
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 문서 내용 다 채워주실래요",
        docs,
        view,
        ask_many=reads({}, {"직위": "직급", "전부": "예"}),
    )
    assert ("직위", "직급", "model") in result.fields


def test_fill_everything_skips_a_value_that_looks_like_an_item_name(tmp_path):
    """검수 r2 H-3: 두 문서의 값이 우연히 같으면("12시간") 값 칸이 항목명으로 잡힌다. 후보에서 뺀다."""
    people = [
        ("A-01", "가나다", "주무관", "12시간", "90", "내선 101", "E-1"),
        ("A-02", "라마바", "팀장", "12시간", "85", "내선 102", "E-2"),
    ]
    _, docs, view = build(tmp_path, [certificate(*p) for p in people], SHEET)
    asked = []

    def many(prompt, fields):
        asked.extend(k for k in fields if k.startswith("[항목]"))
        return {
            k: "이수시간" for k, o in fields.items() if k == "[항목] 12시간" and "이수시간" in o
        }

    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=many)
    assert "[항목] 12시간" not in asked and "12시간" not in {f for f, _, _ in result.fields}


def test_fill_everything_is_off_when_a_skipped_name_is_contested(setup):
    """검수 r2 M-2: 코드는 빼기로, 모델은 채움으로 읽어 갈린 이름이 있으면 켜지 않는다."""
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 직급은 빼지 말고 다 채워주실래요",
        docs,
        view,
        ask_many=reads({"직급": "채움"}, {"평가점수": "점수", "전부": "예"}),
    )
    assert not {"이름", "이수시간", "점수"} & {c for _, c, _ in result.fields}


def test_fill_everything_leaves_out_a_synonym_the_request_skips(setup):
    """'교육시간은 빼고 다 채워': 동의어로 뺀 열만 빼고 나머지를 채운다."""
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 교육시간은 빼고 다 채워주실래요",
        docs,
        view,
        ask_many=reads({}, {"직위": "직급", "전부": "예"}),
    )
    columns = {c for _, c, _ in result.fields}
    assert "이수시간" not in columns and "직급" in columns


def test_fill_everything_is_off_when_the_key_is_also_pointed_at(setup):
    """키로 말한 이름을 칸으로도 지목하면(애매한 지목) 켜지 않는다."""
    result = run(setup, "발급번호 기준으로 발급번호 칸에 다 채워줘")
    assert not {"이름", "이수시간"} & {c for _, c, _ in result.fields}


def test_fill_everything_reads_jeonche_like_the_other_words(setup):
    """L-2: '전체 채워줘'도 아는 말뿐인 요청이다."""
    result = run(setup, "발급번호 기준으로 전체 채워줘")
    assert ("이수시간", "이수시간", "assumed") in result.fields


def test_fill_everything_never_asks_about_the_key_item(setup):
    _, docs, view = setup
    asked = []

    def many(prompt, fields):
        asked.extend(k for k in fields if k.startswith("[항목]"))
        return {}

    interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=many)
    assert asked and "[항목] 발급번호" not in asked


def test_fill_everything_works_on_a_single_roster_document(tmp_path):
    """명단 표는 문서 한 장이어도 항목명(머리글)과 값을 가른다."""
    head = [cell(h, 0, i) for i, h in enumerate(("성명", "부서", "참석일"))]
    rows = [head] + [
        [cell(a, n, 0), cell(b, n, 1), cell(c, n, 2)]
        for n, (a, b, c) in enumerate(
            [("가나다", "총무팀", "12-04"), ("라마바", "기획팀", "12-05")], 1
        )
    ]
    doc = ["<hp:p><hp:run>" + table(rows) + "</hp:run></hp:p>"]
    sheet = [["성명", "부서", "참석일자"], ["가나다", None, None], ["라마바", None, None]]
    _, docs, view = build(tmp_path, [doc], sheet, "명단")
    result = interpret("성명 기준으로 다 채워줘", docs, view)
    assert result.record.kind == "table_rows"
    assert ("부서", "부서", "assumed") in result.fields


def fooled(prompt, fields):
    """가짜 모델: 늘 '전부'라고 답하고(속은 모델) 이름은 채움, 항목은 아는 짝으로 답한다."""
    out = everyone_fills(prompt, fields)
    if "[요청] 전부" in fields:
        out["[요청] 전부"] = "예"
    return out


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 전부 넣지 마",
        "발급번호 기준으로 다 안 채워도 돼, 확인해줘",
        "발급번호 기준으로 전부 해줘",  # 작업 종류를 요청에서 정하지 못함
        "발급번호 기준으로 교육시간만 다 채워주실래요",  # 동의어로 한 열을 말함
    ],
)
def test_fill_everything_code_guards_hold_even_if_the_model_is_fooled(setup, request_text):
    """모델이 '전부'라고 잘못 읽어도 코드 방어선(작업 종류·채우지 말라는 말·말한 열)이 막는다."""
    _, docs, view = setup

    def silent_alias(prompt, fields):  # 동의어 열(이수시간)의 역할에는 답하지 않는다
        out = fooled(prompt, fields)
        out.pop("[이름] 이수시간", None)
        return out

    result = interpret(request_text, docs, view, ask_many=silent_alias)
    assert not {"이름", "직급", "점수", "비고"} & {c for _, c, _ in result.fields}


def test_fill_everything_is_off_when_a_fooled_model_also_reads_a_word_as_a_column(setup):
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 수료시간만 다 채워주실래요",
        docs,
        view,
        ask_many=reads({}, {"수료시간": "이수시간/채움", "직위": "직급", "전부": "예"}),
    )
    assert not {"직급", "이름"} & {c for _, c, _ in result.fields}


def test_fill_everything_with_unknown_words_and_no_model_stays_off(setup):
    result = run(setup, "발급번호 기준으로 문서 내용 다 채워주실래요")
    assert result.fields == ()


def test_fill_everything_skips_a_text_value_shared_by_all_documents(tmp_path):
    """두 문서의 직위가 모두 '팀장'이면 그 값 칸이 항목명처럼 잡힌다. 다른 항목의 값과 같은 이름은 뺀다."""
    people = [
        ("A-01", "가나다", "팀장", "12시간", "90", "내선 101", "E-1"),
        ("A-02", "라마바", "팀장", "8시간", "85", "내선 102", "E-2"),
    ]
    _, docs, view = build(tmp_path, [certificate(*p) for p in people], SHEET)
    asked = []

    def many(prompt, fields):
        asked.extend(k for k in fields if k.startswith("[항목]"))
        return {}

    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=many)
    assert "[항목] 팀장" not in asked
    # 가짜 '팀장'과 값이 똑같아진 이수시간도 어느 쪽이 진짜인지 몰라 비워 둔다(덜 채울 뿐)
    assert "이수시간" not in {c for _, c, _ in result.fields} and "이수시간" in result.excluded


# ---------- '전부' 4차: 형태소로 읽는 방어선 (3차 검수) ----------


def fooled_with(meant):
    """속은 모델: '전부=예', 이름은 채움, 항목은 아는 짝, 모르는 말은 meant대로."""

    def ask(prompt, fields):
        out = fooled(prompt, fields)
        for key, options in fields.items():
            if key.startswith("[말] "):
                answer = meant.get(key[4:])
                if answer in options:
                    out[key] = answer
        return out

    return ask


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 평가는 빼고 다 채워줘",
        "발급번호 기준으로 시간은 빼고 전부 채워줘",
        "발급번호 기준으로 연락 쪽은 놔두고 다 채워줘",
    ],
)
def test_fill_everything_is_off_when_the_skipped_word_is_not_tied_to_a_column(setup, request_text):
    """3차 H-1: 빼라고 한 말을 모델이 열과 잇지 못하면 무엇을 뺄지 모른다. 전부를 켜지 않는다."""
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=fooled_with({}))
    assert not {"이름", "직급", "점수", "비고"} & {c for _, c, _ in result.fields}


def test_fill_everything_leaves_out_a_skipped_word_the_model_ties_to_a_column(setup):
    """'평가는 빼고 다': 모델이 평가를 점수 열의 빼기로 읽으면 점수만 빼고 나머지를 채운다."""
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 평가는 빼고 다 채워줘",
        docs,
        view,
        ask_many=fooled_with({"평가": "점수/제외"}),
    )
    columns = {c for _, c, _ in result.fields}
    assert "점수" not in columns and "직급" in columns


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 수료시간만 다 채워주실래요",
        "발급번호 기준으로 다 채우는 건 무리고 일부만 채워줘",
        "발급번호 기준으로 다 채우진 마",
        "발급번호 기준으로 다 채우진 말아줘",
        "발급번호 기준으로 다 놔두고 확인해줘",
        "발급번호 기준으로 다 됐으면 채워줘",
    ],
)
def test_fill_everything_stays_off_for_a_fooled_model(setup, request_text):
    """3차 H-2·H-4, M-3: '만', 하지 않는 채우기, 빼기, '다 됐으면'은 모델이 속아도 코드가 막는다."""
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=fooled_with({}))
    assert not {"이름", "직급", "점수", "비고"} & {c for _, c, _ in result.fields}


@pytest.mark.parametrize(
    "request_text",
    ["발급번호 기준으로 직급 그대로 다 옮겨줘", "발급번호 기준으로 직급은 참고해서 다 채워줘"],
)
def test_fill_everything_needs_the_model_to_agree_with_a_coded_skip(setup, request_text):
    """3차 M-1: 코드 빼기 낱말 오탐('그대로', '참고')을 모델이 빼기로 확인하지 않으면 켜지 않는다."""
    _, docs, view = setup

    def no_role(prompt, fields):
        out = fooled(prompt, fields)
        out.pop("[이름] 직급", None)
        return out

    result = interpret(request_text, docs, view, ask_many=no_role)
    assert not {"이름", "점수", "비고"} & {c for _, c, _ in result.fields}


def test_fill_everything_is_off_for_documents_with_repeated_labels(tmp_path):
    """3차 H-3: 같은 라벨이 두 번 나오는 서식(성명|…|직급|… 두 줄)은 값 칸이 항목명으로 잡힌다."""

    def doc(number, rows):
        body = table([[cell(t, r, c) for c, t in enumerate(row)] for r, row in enumerate(rows)])
        return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    docs = [
        doc("A-01", [("성명", "가나다", "직급", "팀장"), ("성명", "이순신", "직급", "과장")]),
        doc("A-02", [("성명", "라마바", "직급", "팀장"), ("성명", "박문수", "직급", "대리")]),
    ]
    sheet = [["발급번호", "직급", "비고"], ["A-01", None, None], ["A-02", None, None]]
    _, documents, view = build(tmp_path, docs, sheet)

    def pairs_the_value(
        prompt, fields
    ):  # 예시 값으로 보면 그럴듯한 짝(팀장 아래 칸 과장·대리 → 직급)
        out = fooled_with({})(prompt, fields)
        for key, options in fields.items():
            if key.startswith("[항목]") and "직급" in options:
                out[key] = "직급"
        return out

    result = interpret("발급번호 기준으로 전부 채워줘", documents, view, ask_many=pairs_the_value)
    assert result.fields == ()


@pytest.mark.parametrize(
    "request_text", ["발급번호 기준으로 채워줘", "발급번호 기준으로 다른지 확인해줘"]
)
def test_fill_everything_needs_the_word_before_the_verb(setup, request_text):
    """전부 낱말이 없거나 '다른'이면 아는 말뿐인 요청이어도 켜지 않는다."""
    result = run(setup, request_text)
    assert not {"이름", "이수시간"} & {c for _, c, _ in result.fields}


def test_fill_everything_needs_both_model_answers(setup):
    _, docs, view = setup
    turns = iter(["예", "아니오"])

    def split(prompt, fields):
        out = everyone_fills(prompt, fields)
        if "[요청] 전부" in fields:
            out["[요청] 전부"] = next(turns)
        return out

    result = interpret("발급번호 기준으로 문서 내용 다 채워주실래요", docs, view, ask_many=split)
    assert not {"직급", "점수"} & {c for _, c, _ in result.fields}


def test_fill_everything_quotes_document_labels_in_the_prompt(setup):
    """3차 L-1: 문서 라벨이 지시처럼 읽히지 않게 따옴표로 감싼다."""
    _, docs, view = setup
    prompts = []

    def many(prompt, fields):
        if any(k.startswith("[항목]") for k in fields):
            prompts.append(prompt)
        return {}

    interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=many)
    assert prompts and '- "직위"' in prompts[0]


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 전부 확인해줘",  # 점검 동사
        "발급번호 기준으로 다 같이 채워주실래요",  # 사이에 부사
        "발급번호 기준으로 다 채우기만 해주실래요",  # '-기만'은 어느 것만이 아니다
    ],
)
def test_fill_everything_reads_these_as_everything(setup, request_text):
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=fooled_with({}))
    assert "직급" in {c for _, c, _ in result.fields}


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 평가 외에는 다 채워줘",
        "발급번호 기준으로 평가는 놔둬, 다 채워줘",
        "발급번호 기준으로 평가는 그냥 둬 다 채워줘",
        "발급번호 기준으로 평가는 건너뛰고 다 채워줘",
        "발급번호 기준으로 평가는 패스하고 다 채워줘",
        "발급번호 기준으로 평가는 됐고 다 채워줘",
        "발급번호 기준으로 평가 말곤 다 채워줘",
        "발급번호 기준으로 평가 이외 전부 채워줘",
        "발급번호 기준으로 평가는 안 해도 되니까 다 채워줘",
        "발급번호 기준으로 돈 빼고 다 채워줘",
        "발급번호 기준으로 다 채워줘, 평가 놔두고",
    ],
)
def test_fill_everything_is_off_when_a_side_clause_names_an_unsettled_word(setup, request_text):
    """4차 검수 H-1: 빼기 낱말 목록 밖 표현이라도, '전부' 동사 밖 마디에 확정되지 않은 말이 있으면 켜지 않는다."""
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=fooled_with({}))
    assert "점수" not in {c for _, c, _ in result.fields}


def test_fill_everything_keeps_a_side_clause_the_model_settled_as_skip(setup):
    """빼라고 한 말을 모델이 두 번 모두 그 열의 빼기로 읽었으면 그 열만 빼고 전부 채운다."""
    _, docs, view = setup
    result = interpret(
        "발급번호 기준으로 평가는 놔둬, 다 채워줘",
        docs,
        view,
        ask_many=fooled_with({"평가": "점수/제외"}),
    )
    columns = {c for _, c, _ in result.fields}
    assert "직급" in columns and "점수" not in columns


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 다 채우면 곤란해",  # 4차 검수 M-1: 딸린 말
        "발급번호 기준으로 다 채우는 건 아닌 것 같아",
        "발급번호 기준으로 연락 칸에만 다 채워줘",  # 4차 검수 M-2: 조사 뒤 '만'
        "발급번호 기준으로 첫 줄에서만 다 채워줘",
    ],
)
def test_fill_everything_is_off_for_subordinate_or_exclusive_requests(setup, request_text):
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=fooled_with({}))
    assert "직급" not in {c for _, c, _ in result.fields}


def test_fill_everything_drops_a_value_cell_that_overflowed_into_a_label(tmp_path):
    """4차 검수 M-3: '주소 | 서울시 중구 / 세부주소'에서 값이 우연히 같은 칸은 항목명이 아니다."""

    def doc(number, name, detail):
        body = table(
            [
                [cell("성명", 0, 0), cell(name, 0, 1)],
                [cell("주소", 1, 0, rowspan=2), cell("서울시 중구", 1, 1)],
                [cell(detail, 2, 1)],
            ]
        )
        return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    sheet = [["발급번호", "이름", "주소"], ["A-01", None, None], ["A-02", None, None]]
    _, docs, view = build(
        tmp_path, [doc("A-01", "가나다", "세종대로 1"), doc("A-02", "라마바", "을지로 2")], sheet
    )

    def model(prompt, fields):
        return {k: "주소" for k, o in fields.items() if "중구" in k and "주소" in o}

    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=model)
    assert "주소" not in {c for _, c, _ in result.fields}
    assert "서울시 중구" in result.excluded


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 그건 건너뛰고 다 채워줘",  # 재확인 M-4: 가리키는 말
        "발급번호 기준으로 세 번째 칸은 건너뛰고 다 채워줘",
        "발급번호 기준으로 3열은 놔둬 다 채워줘",
        "발급번호 기준으로 표는 놔두고 다 채워줘",  # 자리 낱말만 빼는 마디
        "발급번호 기준으로 다 채워줘. 아니다, 하지 마",  # 재확인 M-5: 거둬들이는 마디
        "발급번호 기준으로 다 채워줘, 아니 잠깐 기다려",
    ],
)
def test_fill_everything_is_off_when_a_side_clause_points_at_something_unknown(setup, request_text):
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=fooled_with({}))
    assert "직급" not in {c for _, c, _ in result.fields}


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 다 채워줘, 부탁해",  # 인사는 마디가 아니다
        "발급번호 기준으로 다 채워주세요 감사합니다",
        "발급번호로 찾아서 다 채워줘",  # 키를 찾는 마디
    ],
)
def test_fill_everything_keeps_courtesy_and_key_clauses(setup, request_text):
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=fooled_with({}))
    assert "직급" in {c for _, c, _ in result.fields}


def test_fill_everything_keeps_header_row_fields(tmp_path):
    """재확인 L-2: '연번 | 성명' 머리글 아래 연번 값이 문서마다 같아도 성명은 넘친 값이 아니다."""

    def doc(number, name):
        body = table(
            [[cell("연번", 0, 0), cell("성명", 0, 1)], [cell("1", 1, 0), cell(name, 1, 1)]]
        )
        return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    sheet = [["발급번호", "이름"], ["A-01", None], ["A-02", None]]
    _, docs, view = build(tmp_path, [doc("A-01", "가나다"), doc("A-02", "라마바")], sheet)
    result = interpret("발급번호 기준으로 다 채워줘", docs, view, ask_many=fooled_with({}))
    assert "성명" not in result.excluded


def test_a_name_differing_only_by_geumaek_pairs_without_the_model(tmp_path):
    """'-금액'과 '-액'은 같은 이름이다(집행금액 = 집행액, DIF-065)."""
    rows = [("발급번호", "A-01"), ("집행액", "120000"), ("이수시간", "12")]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    docs = [["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]]
    sheet = [["발급번호", "집행금액", "이수시간"], ["A-01", None, None]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("발급번호 기준으로 집행금액 채워줘", documents, view)
    assert ("집행액", "집행금액", "synonym") in result.fields


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 비고하고 연락처 대조해줘",
        "발급번호 기준으로 비고랑 연락처가 다른 사람 있는지 확인해줘",
    ],
)
def test_a_check_request_pairs_the_field_and_column_said_together(setup, request_text):
    """대조 요청에서 문서 항목 하나와 열 하나를 "A랑 B"로 이어 말했으면 그 둘이 짝이다(확인 필요)."""
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=everyone_fills)
    assert result.fields == (("연락처", "비고", "assumed"),)
    assert not result.questions


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 비고랑 연락처 채워줘",  # 채우기의 "A랑 B"는 나열이다
        "발급번호 기준으로 비고 칸 연락처 맞는지 봐줘",  # 이어 말하지 않았다
        "발급번호 기준으로 비고랑 연락처랑 직위 대조해줘",  # 이름이 셋이다
    ],
)
def test_a_field_and_column_are_not_paired_otherwise(setup, request_text):
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=everyone_fills)
    assert ("연락처", "비고", "assumed") not in result.fields


def test_a_pair_said_together_does_not_override_a_synonym(tmp_path):
    """검수 r1 M-1: 동의어표로 짝이 서는 이름은 "A랑 B"로 이어 말해도 동의어 짝이 먼저다."""
    sheet = [["발급번호", "사번", "비고"], ["A-01", None, None], ["A-02", None, None]]
    _, docs, view = build(tmp_path, [certificate(*p) for p in PEOPLE], sheet)
    result = interpret(
        "발급번호 기준으로 직원번호랑 비고 대조해줘", docs, view, ask_many=everyone_fills
    )
    assert ("직원번호", "비고", "assumed") not in result.fields
    assert ("직원번호", "사번", "synonym") in result.fields


def test_a_joining_particle_toward_the_key_is_not_a_pair(setup):
    """검수 r1 L-3: '비고랑 발급번호 기준으로'의 '랑'은 키와 잇는 말이다."""
    _, docs, view = setup
    result = interpret(
        "비고랑 발급번호 기준으로 연락처 대조해줘", docs, view, ask_many=everyone_fills
    )
    assert ("연락처", "비고", "assumed") not in result.fields


def test_a_name_variant_with_a_different_unit_is_not_a_synonym(tmp_path):
    """검수 r1 M-2: 집행금액(천원)과 집행액은 단위가 달라 같은 이름이 아니다."""
    rows = [("발급번호", "A-01"), ("집행액", "120000"), ("이수시간", "12")]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    docs = [["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]]
    sheet = [["발급번호", "집행금액(천원)", "이수시간"], ["A-01", None, None]]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("발급번호 기준으로 집행금액 채워줘", documents, view)
    assert all(src != "synonym" for _, _, src in result.fields)


def _kv(rows):
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


def test_an_unnamed_key_is_asked_when_a_roster_row_is_merged(tmp_path):
    """검수 r1 H1: 병합 칸이 있는 명단 표는 항목마다 값 수가 달라 행을 맞출 수 없다 → 예외 없이 묻는다."""
    head = [cell(h, 0, i) for i, h in enumerate(("연번", "성명", "사번", "부서"))]
    rows = [
        head,
        [cell("1", 1, 0), cell("가나다", 1, 1), cell("S-1001", 1, 2), cell("총무", 1, 3)],
        [cell("2", 2, 0), cell("라마바", 2, 1), cell("S-1002", 2, 2), cell("민원", 2, 3)],
        [cell("3", 3, 0), cell("사아자", 3, 1), cell("S-1003", 3, 2), cell("감사", 3, 3)],
        [cell("4", 4, 0), cell("홍길동", 4, 1, colspan=2), cell("기획", 4, 3)],
    ]
    docs = [["<hp:p><hp:run>" + table(rows) + "</hp:run></hp:p>"]]
    sheet = [
        ["성명", "사번", "부서"],
        ["가나다", "S-1001", None],
        ["라마바", "S-1002", None],
        ["사아자", "S-1003", None],
        ["홍길동", None, None],
    ]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("부서 채워줘", documents, view)
    assert result.key is None and "key_column" in dict(result.questions)


@pytest.mark.parametrize(
    "people, sheet",
    [
        (  # 사번이 절반만 맞지만(약한 후보) 성명과 다른 행을 가리킨다
            [("가나다", "S-1002"), ("라마바", "S-1001"), ("사아자", "X-9"), ("차카타", "X-8")],
            [
                ["사번", "성명", "이수시간"],
                ["S-1001", "가나다", None],
                ["S-1002", "라마바", None],
                ["S-1003", "사아자", None],
                ["S-1004", "차카타", None],
            ],
        ),
        (  # 사번 열에 같은 값이 있어 엄격 후보에서 빠지지만 성명과 다른 행을 가리킨다
            [("가나다", "S-1002"), ("라마바", "S-1001"), ("사아자", "S-1003")],
            [
                ["사번", "성명", "이수시간"],
                ["S-1001", "가나다", None],
                ["S-1002", "라마바", None],
                ["S-1003", "사아자", None],
                ["S-1003", "차카타", None],
            ],
        ),
    ],
)
def test_an_unnamed_key_is_asked_when_any_matching_column_disagrees(tmp_path, people, sheet):
    """검수 r1 M1: 값이 하나라도 맞는 모든 후보와 견준다."""
    docs = [_kv([("성명", a), ("사번", b), ("이수시간", "1")]) for a, b in people]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이수시간 채워줘", documents, view)
    assert result.key is None and "key_column" in dict(result.questions)


def test_an_unnamed_key_is_not_assumed_from_a_doubled_header(tmp_path):
    """검수 r1 L2: 머리글 '성명'이 둘이면 어느 열인지 모른다."""
    people = [("가나다", "12"), ("라마바", "8"), ("사아자", "4")]
    sheet = [
        ["성명", "이수시간", "성명"],
        ["가나다", None, "김"],
        ["라마바", None, "이"],
        ["사아자", None, "박"],
    ]
    docs = [_kv([("성명", a), ("이수시간", c)]) for a, c in people]
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    assert not interpret("이수시간 채워줘", documents, view).key_assumed


def test_an_unnamed_key_is_asked_when_an_identifier_on_two_rows_misses_its_row(tmp_path):
    """검수 r2 M1: 거의 식별자인 열에서 두 행에 있는 값이라도 키가 가리킨 행이 그 안에 없으면 묻는다."""
    sheet = [
        ["사번", "성명", "이수시간"],
        ["S-1001", "가나다", None],
        ["S-1002", "라마바", None],
        ["S-1003", "사아자", None],
        ["S-1004", "차카타", None],
        ["S-1005", "타파하", None],
        ["S-1003", "거너더", None],
    ]
    docs = [_kv([("성명", "가나다"), ("사번", "S-1003"), ("이수시간", "1")])]
    docs.append(_kv([("성명", "라마바"), ("사번", "S-1002"), ("이수시간", "2")]))
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이수시간 채워줘", documents, view)
    assert result.key is None and "key_column" in dict(result.questions)


def test_an_unnamed_key_is_asked_in_a_small_table_with_one_doubled_identifier(tmp_path):
    """검수 r3: 4행 표에서 겹친 값이 하나여도 식별자 열로 보고 견준다."""
    sheet = [
        ["사번", "성명", "이수시간"],
        ["S-1", "가나다", None],
        ["S-2", "라마바", None],
        ["S-9", "사아자", None],
        ["S-9", "차카타", None],
    ]
    docs = [_kv([("성명", "가나다"), ("사번", "S-9"), ("이수시간", "1")])]
    docs.append(_kv([("성명", "라마바"), ("사번", "S-2"), ("이수시간", "2")]))
    _, documents, view = build(tmp_path, docs, sheet, "명단")
    result = interpret("이수시간 채워줘", documents, view)
    assert result.key is None and "key_column" in dict(result.questions)


def _skips_contact(prompt, fields):
    return reads({"연락처": "제외"}, {"전부": "예"})(prompt, fields)


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 문서에 있는 거 전부 넣어줘. 연락처는 개인정보라 빼고",
        "발급번호 기준으로 다 채워줘, 연락처는 개인정보라서 빼 주세요",
        "발급번호 기준으로 다 채워줘. 연락처는 나중에 따로 정할게",
        "연락처는 나중에 할게요, 발급번호 기준으로 다 채워줘",
        "발급번호 기준으로 다 채워줘. 연락처는 추후 정하겠습니다",
    ],
)
def test_fill_everything_reads_reason_and_later_clauses_as_skips(setup, request_text):
    """이유를 대는 '-이라(서)'는 빼기 마디에 붙고, '나중에 (따로) 정할게'는 미루기(빼기)다."""
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=_skips_contact)
    columns = {c for _, c, _ in result.fields}
    assert "이수시간" in columns and "연락처" not in columns


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 다 채워줘. 연락처는 틀렸으니까 고쳐",  # 이유 뒤가 빼기가 아니다
        "발급번호 기준으로 다 채워줘. 연락처는 나중에 확인할게",  # 미루는 일이 채우기가 아니다
        "발급번호 기준으로 다 채워줘. 연락처는 개인정보라",  # 이유만 있고 무엇을 할지 없다
    ],
)
def test_fill_everything_stays_off_for_other_reason_and_later_clauses(setup, request_text):
    _, docs, view = setup
    result = interpret(request_text, docs, view, ask_many=_skips_contact)
    assert "이수시간" not in {c for _, c, _ in result.fields}


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 다 채워줘. 평가는 민감한 정보라서",  # 검수 r1 H-1: 이유만 대고 끝남
        "발급번호 기준으로 평가는 민감한 정보라서 나머지 다 채워",
    ],
)
def test_fill_everything_stays_off_after_a_reason_with_nothing_to_do(setup, request_text):
    _, docs, view = setup
    reader = reads({}, {"전부": "예", "평가점수": "점수"})
    result = interpret(request_text, docs, view, ask_many=reader)
    assert "점수" not in {c for _, c, _ in result.fields}


def test_a_reason_after_an_object_particle_keeps_the_name_as_skipped(setup):
    """검수 r1 L-1: '연락처를 개인정보라서 빼고'도 연락처를 뺀다(이유 말은 조사 뒤부터)."""
    _, docs, view = setup
    request = "발급번호 기준으로 연락처를 개인정보라서 빼고 다 채워줘"
    result = interpret(request, docs, view, ask_many=_skips_contact)
    columns = {c for _, c, _ in result.fields}
    assert "이수시간" in columns and "연락처" not in columns


@pytest.mark.parametrize(
    "request_text",
    [
        "발급번호 기준으로 이름 다음에 정해진 순서로 다 채워",  # 검수 r1 L-2: '정해진'은 미루기가 아니다
        "발급번호 기준으로 다 채워줘, 연락처는 개인정보라 빼지 말고",  # L-3: '빼지 말고'는 빼기가 아니다
    ],
)
def test_lookalike_skip_words_are_not_skips(request_text):
    blanked = _blank(request_text, morph.aside_spans(request_text, later=False))
    assert not _negated(_mentions(blanked, ["이름", "연락처"]))


@pytest.mark.parametrize(
    "tail",
    [
        "직급은 빼지 마",
        "직급은 비우지 말고",
        "직급은 비워 두지 마",
        "직급은 놔두지 말고",
        "직급은 개인정보라 빼지 마",
    ],
)
def test_fill_everything_stays_off_for_do_not_skip(setup, tail):
    """곁마디 검수 r2 M-1·L-1: '빼지 마'는 빼기가 아니다. 모델이 '무관'이라 해도 '전부'를 켜지 않는다."""
    _, docs, view = setup
    request = f"발급번호 기준으로 다 채워줘. {tail}"
    reader = reads({"직급": "무관"}, {"전부": "예"})
    result = interpret(request, docs, view, ask_many=reader)
    assert "이수시간" not in {c for _, c, _ in result.fields}
    assert "직급" not in _negated(_mentions(request, ["직급"]))
