"""한 줄 요청 → 미리보기 → [저장] (P04 ③, FR-05·06·14). 합성 자료만 쓴다."""

import pytest
from conftest import cell, para, table, write_hwpx, write_xlsx

from ureonggaksi.errors import ServiceError
from ureonggaksi.rules import RuleStore
from ureonggaksi.service import commit, prepare

PEOPLE = [
    ("A-01", "가나다", "주무관", "12시간"),
    ("A-02", "라마바", "팀장", "8시간"),
]
SHEET = [
    ["발급번호", "이름", "직급", "이수시간"],
    ["A-01", "가나다", None, None],
    ["A-02", "라마바", None, None],
]


def certificate(number, name, title, hours):
    rows = [("성명", name), ("직위", title), ("이수시간", hours)]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


@pytest.fixture
def files(tmp_path):
    sources = [write_hwpx(tmp_path / f"doc{i}.hwpx", certificate(*p)) for i, p in enumerate(PEOPLE)]
    target = write_xlsx(tmp_path / "취합표.xlsx", SHEET, sheet="이수현황")
    (tmp_path / "out").mkdir()
    return sources, target, tmp_path / "out" / "결과.xlsx", RuleStore(tmp_path / "rules")


def test_a_plain_request_previews_then_saves_once(files):
    sources, target, out, store = files
    prepared = prepare("이름 기준으로 이수시간 채워줘", sources, target, output_path=out)
    assert prepared.question is None and prepared.plan is not None
    assert prepared.interpretation.startswith("채우기:") and "이수시간" in prepared.interpretation
    assert [s.target_ref for s in prepared.samples] and prepared.plan.counts.cells_write == 2
    package = commit(prepared, store)
    assert package.status == "completed" and package.output_path == out and out.exists()
    assert len(store.list()) == 1
    again = prepare(
        "이름 기준으로 이수시간 채워줘", sources, target, output_path=out.with_name("다시.xlsx")
    )
    commit(again, store)
    assert len(store.list()) == 1  # 같은 규칙은 다시 저장하지 않는다


def test_unknown_work_type_is_asked_then_answered(files):
    sources, target, out, store = files
    request = "이름 기준으로 이수시간"  # 동사가 없다
    asked = prepare(request, sources, target, output_path=out)
    assert asked.question is not None and asked.question.slot == "work_type" and asked.plan is None
    with pytest.raises(ServiceError):
        commit(asked, store)
    answered = prepare(request, sources, target, output_path=out, answers=(("work_type", "fill"),))
    assert answered.question is None and answered.plan.counts.cells_write == 2


def test_model_errors_leave_that_pair_out_and_continue(files):
    sources, target, out, _ = files

    def broken(prompt, options):
        raise ServiceError("AI_TIMEOUT", "시간 초과")

    prepared = prepare(
        "이름 기준으로 직급이랑 이수시간 채워줘", sources, target, output_path=out, choose=broken
    )
    assert prepared.plan is not None
    assert [f.target_column for f in prepared.plan.rule.fields if f.target_column] == ["이수시간"]
    assert any("직급" in w for w in prepared.warnings)


def test_a_column_the_model_picked_is_left_blank_with_its_suggestion(files):
    """확실한 칸만 채운다(2026-11-20 사용자 결정): 이름이 다른 짝(직위→직급)은 쓰지 않고 추천으로만 보인다."""
    sources, target, out, _ = files

    def knows(prompt, options):
        return "직위" if "직위" in options else "없음"

    prepared = prepare(
        "이름 기준으로 직급이랑 이수시간 채워줘", sources, target, output_path=out, choose=knows
    )
    targets = {f.name: f.target_column for f in prepared.plan.rule.fields if f.target_column}
    assert targets == {"이수시간": "이수시간"} and ("blank", "직위", "직급") in prepared.unsure
    assert any("추천: 직급 열" in w for w in prepared.warnings)
    assert all(i.target_ref[0] != "C" for i in prepared.plan.items if i.area == "done")


def test_a_saved_rule_runs_without_interpreting(files):
    sources, target, out, store = files
    first = prepare("이름 기준으로 이수시간 채워줘", sources, target, output_path=out)
    commit(first, store)
    saved = store.load(store.list()[0].workflow_id)
    rerun = prepare("", sources, target, workflow=saved, output_path=out.with_name("두번째.xlsx"))
    assert rerun.plan is not None and rerun.plan.rule_hash == first.plan.rule_hash


def test_a_common_unit_problem_is_asked_once_and_the_answer_is_kept(tmp_path):
    """단위 없는 금액이 30% 이상이면 단위를 한 번 묻고, 답은 규칙에 남는다(PRD 6.2)."""
    from dataclasses import replace

    def budget(number, name, amount):
        rows = [("성명", name), ("예산", amount)]
        body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
        return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    sources = [
        write_hwpx(tmp_path / "a.hwpx", budget("A-01", "가나다", "1,500")),
        write_hwpx(tmp_path / "b.hwpx", budget("A-02", "라마바", "2,000")),
    ]
    target = write_xlsx(
        tmp_path / "표.xlsx", [["이름", "예산"], ["가나다", None], ["라마바", None]], sheet="현황"
    )
    store = RuleStore(tmp_path / "rules")
    base = prepare("이름 기준으로 예산 채워줘", sources, target, output_path=tmp_path / "r.xlsx")
    money = replace(
        base.plan.rule,
        fields=tuple(
            replace(f, value_type="money") if f.target_column == "예산" else f
            for f in base.plan.rule.fields
        ),
    )
    saved = store.save("예산 취합", money)
    asked = prepare("", sources, target, workflow=saved, output_path=tmp_path / "r.xlsx")
    assert asked.question is not None and asked.question.slot == "default_unit"
    assert asked.question.affects == 2 and "1,500" in asked.question.examples
    answered = prepare(
        "",
        sources,
        target,
        workflow=saved,
        answers=(("default_unit", "천원"),),
        output_path=tmp_path / "r.xlsx",
    )
    assert answered.question is None
    assert dict(answered.plan.rule.policy) == {"default_unit": "천원"}
    assert {i.proposed.value for i in answered.plan.items if i.area == "done"} == {
        "1500000",
        "2000000",
    }


def test_answers_are_validated_and_rule_questions_cannot_be_skipped(files):
    sources, target, out, _ = files
    with pytest.raises(ServiceError) as bad:
        prepare(
            "이름 기준으로 이수시간",
            sources,
            target,
            output_path=out,
            answers=(("rounding", "foo"),),
        )
    assert bad.value.code == "INPUT_UNSUPPORTED"
    with pytest.raises(ServiceError) as skipped:
        prepare(
            "이름 기준으로 이수시간",
            sources,
            target,
            output_path=out,
            answers=(("work_type", "__skip__"),),
        )
    assert skipped.value.code == "RULE_INVALID"


def test_a_saved_rule_with_new_answers_is_saved_as_the_next_revision(files):
    """'최근 작업'에 답을 더해 저장하면 같은 작업의 다음 revision이다(검수 M2)."""
    sources, target, out, store = files
    commit(prepare("이름 기준으로 이수시간 채워줘", sources, target, output_path=out), store)
    saved = store.load(store.list()[0].workflow_id)
    rerun = prepare(
        "",
        sources,
        target,
        workflow=saved,
        answers=(("rounding", "__skip__"),),
        output_path=out.with_name("2.xlsx"),
    )
    commit(rerun, store)
    assert len(store.list()) == 1 and store.load(saved.workflow_id).revision == 2
    assert store.load(saved.workflow_id).rule.answered("rounding")  # 건너뛴 답도 남는다


def test_key_choices_only_offer_columns_an_answer_can_settle(tmp_path):
    """성명·추천인이 모두 '이름' 열과 맞으면 '이름'은 답으로 정할 수 없다. 선택지에 내놓지 않는다(검수 M-a)."""

    def doc(name, referrer, hours):
        rows = [("성명", name), ("추천인", referrer), ("이수시간", hours)]
        return [
            "<hp:p><hp:run>"
            + table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
            + "</hp:run></hp:p>"
        ]

    sources = [
        write_hwpx(tmp_path / "a.hwpx", doc("가나다", "라마바", "12")),
        write_hwpx(tmp_path / "b.hwpx", doc("라마바", "가나다", "8")),
    ]
    target = write_xlsx(
        tmp_path / "표.xlsx",
        [["이름", "이수시간"], ["가나다", None], ["라마바", None]],
        sheet="현황",
    )
    asked = prepare("이수시간 채워줘", sources, target, output_path=tmp_path / "r.xlsx")
    assert asked.question.slot == "key_column" and "이름" not in asked.question.choices


def test_a_path_conflict_saves_no_rule(files):
    """결과 경로에 다른 파일이 생기면 저장하지 않고 규칙도 남기지 않는다(검수 L-a)."""
    sources, target, out, store = files
    prepared = prepare("이름 기준으로 이수시간 채워줘", sources, target, output_path=out)
    out.write_bytes(b"someone else")
    with pytest.raises(ServiceError):
        commit(prepared, store)
    assert store.list() == () and out.read_bytes() == b"someone else"


# ---------- 모르는 말이 섞인 요청: 모델이 읽고 '확인 필요'로 표시(2026-11-18) ----------


def reader(roles, meant=None):
    """가짜 ask_many: 이름 역할과 모르는 말이 뜻하는 '열/역할'을 순서와 무관하게 같은 답으로 준다."""

    def ask(prompt, fields):
        out = {}
        for key, options in fields.items():
            name = key.split("] ", 1)[1]
            answer = (
                roles.get(name) if key.startswith("[이름]") else (meant or {}).get(name, "없음")
            )
            if answer in options:
                out[key] = answer
        return out

    return ask


def test_natural_wording_is_read_by_the_model_and_marked_unsure(files):
    sources, target, out, _ = files
    prepared = prepare(
        "신청서 보고 이름 기준으로 이수시간 채워 주시면 돼요",
        sources,
        target,
        output_path=out,
        ask_many=reader({"이수시간": "채움"}),
    )
    assert prepared.plan is not None and ("pair", "이수시간", "이수시간") in prepared.unsure
    assert "(확인 필요)" in prepared.interpretation


def test_a_synonym_word_is_only_a_suggestion(files):
    """'직책'이 직급을 가리킨다고 모델이 읽어도 그 열은 비워 두고 추천으로 보인다."""
    sources, target, out, _ = files

    def knows(prompt, options):
        return "직위" if "직위" in options else "없음"

    prepared = prepare(
        "이름 기준으로 직책이랑 이수시간 채워 주시면 돼요",
        sources,
        target,
        output_path=out,
        choose=knows,
        ask_many=reader({"이수시간": "채움"}, {"직책": "직급/채움"}),
    )
    targets = {f.name: f.target_column for f in prepared.plan.rule.fields if f.target_column}
    assert targets == {"이수시간": "이수시간"}
    assert ("pair", "이수시간", "이수시간") in prepared.unsure
    assert ("blank", "직위", "직급") in prepared.unsure


def test_model_exclusion_wins_and_model_errors_fall_back_to_asking(files):
    sources, target, out, _ = files
    request = "이름 기준으로 이수시간 채우고 직급은 현행 유지"
    excluded = prepare(
        request,
        sources,
        target,
        output_path=out,
        ask_many=reader({"이수시간": "채움", "직급": "제외"}),
    )
    assert [f.target_column for f in excluded.plan.rule.fields if f.target_column] == ["이수시간"]
    assert ("skip", "직급", "") in excluded.unsure and "빼라는 말로 읽고" in excluded.interpretation
    assert not any("직급" in w for w in excluded.warnings)

    def broken(prompt, fields):
        raise ServiceError("AI_TIMEOUT", "시간 초과")

    fallback = prepare(request, sources, target, output_path=out, ask_many=broken)
    assert fallback.plan is None and fallback.question is not None


def test_unsure_pair_blank_and_exclusion_are_all_shown(files, capsys):
    """채운 추정 짝·비워 둔 짝·빼기로 읽은 이름이 해석 문장과 CLI에 보인다."""
    from ureonggaksi.cli import _print_preview

    sources, target, out, _ = files

    def knows(prompt, options):
        return "직위" if "직위" in options else "없음"

    skipped = prepare(
        "이름 기준으로 이수시간 채우고 직급은 현행 유지",
        sources,
        target,
        output_path=out,
        ask_many=reader({"이수시간": "채움", "직급": "제외"}),
    )
    blank = prepare(
        "이름 기준으로 직급이랑 이수시간 채워줘", sources, target, output_path=out, choose=knows
    )
    assert [k for k, _, _ in skipped.unsure] == ["pair", "skip"]
    assert "(확인 필요)" in skipped.interpretation and "빼라는 말로" in skipped.interpretation
    assert "확실하지 않아 비워 둠" in blank.interpretation
    _print_preview(skipped)
    _print_preview(blank)
    shown = [x for x in capsys.readouterr().out.splitlines() if x.startswith("확인 필요:")]
    assert len(shown) == 3 and "추천: 직급 열" in shown[-1]


def _referral_files(tmp_path):
    """성명 + 추천인사번(다른 사람의 사번). 사번 열과 성명 열이 값 일치 수로 동점이다(2차 H-1)."""
    people = [("가나다", "S-1002", "12"), ("라마바", "S-1003", "8"), ("사아자", "S-1001", "4")]
    sources = []
    for i, (name, referrer, hours) in enumerate(people):
        rows = [("성명", name), ("추천인사번", referrer), ("이수시간", hours)]
        body = table([[cell(k, r, 0), cell(v, r, 1)] for r, (k, v) in enumerate(rows)])
        sources.append(
            write_hwpx(tmp_path / f"r{i}.hwpx", ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"])
        )
    sheet = [
        ["사번", "성명", "이수시간"],
        ["S-1001", "가나다", None],
        ["S-1002", "라마바", None],
        ["S-1003", "사아자", None],
    ]
    return sources, write_xlsx(tmp_path / "명단.xlsx", sheet, sheet="명단"), tmp_path / "out.xlsx"


def test_an_unnamed_key_is_asked_and_the_question_shows_the_matching_fields(tmp_path):
    """키는 추정하지 않고 묻는다. 질문에 열마다 값이 맞는 문서 항목을 보여 남의 번호를 고르지 않게 한다(H-1)."""
    sources, target, out = _referral_files(tmp_path)
    asked = prepare("이수시간 채워줘", sources, target, output_path=out)
    question = asked.question
    assert question is not None and question.slot == "key_column"
    assert (
        "사번은(는) 문서의 ‘추천인사번’" in question.text
        and "성명은(는) 문서의 ‘성명’" in question.text
    )
    by_name = prepare(
        "이수시간 채워줘", sources, target, answers=(("key_column", "성명"),), output_path=out
    )
    assert by_name.plan is not None and by_name.unsure == ()


def test_words_the_model_could_not_place_are_warned(files):
    """2차 M-1: 모델이 열과 잇지 못한 모르는 말은 소리 없이 버리지 않고 경고로 보인다."""
    sources, target, out, _ = files
    prepared = prepare(
        "이름 기준으로 직책이랑 이수시간 채워 주시면 돼요",
        sources,
        target,
        output_path=out,
        ask_many=reader({"이수시간": "채움"}),
    )
    assert any("‘직책’" in w and "잇지 못했습니다" in w for w in prepared.warnings)


def test_answering_the_key_drops_the_key_column_from_the_pairs(tmp_path):
    """3차 H1: 키 열을 채울 항목으로도 말한 요청은 키 질문에 답하면 그 짝을 빼고 계속한다(예외로 죽지 않음)."""
    sources = []
    for i, (name, hours) in enumerate([("가나다", "12"), ("라마바", "8"), ("사아자", "4")]):
        rows = [("성명", name), ("이수시간", hours)]
        body = table([[cell(k, r, 0), cell(v, r, 1)] for r, (k, v) in enumerate(rows)])
        sources.append(
            write_hwpx(tmp_path / f"d{i}.hwpx", ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"])
        )
    sheet = [["성명", "이수시간"], ["가나다", None], ["라마바", None], ["사아자", None]]
    target = write_xlsx(tmp_path / "명단.xlsx", sheet, sheet="명단")
    out = tmp_path / "out.xlsx"
    for request in ("성명이랑 이수시간 채워줘", "성명이랑 이수시간 채워 주세요 ㅎㅎ"):
        asked = prepare(
            request,
            sources,
            target,
            output_path=out,
            ask_many=reader({"성명": "채움", "이수시간": "채움"}),
        )
        assert asked.question is not None and asked.question.slot == "key_column"
        answered = prepare(
            request,
            sources,
            target,
            answers=(("key_column", "성명"),),
            output_path=out,
            ask_many=reader({"성명": "채움", "이수시간": "채움"}),
        )
        fields = [f.target_column for f in answered.plan.rule.fields if f.target_column]
        assert fields == ["이수시간"]


def test_a_key_answered_ahead_still_shows_its_document_field(tmp_path):
    """3차 M1: 질문을 보기 전에 답을 준 경로에서도 남의 번호(추천인사번 → 사번)로 찾는 키는 '확인 필요'다."""
    sources, target, out = _referral_files(tmp_path)
    by_number = prepare(
        "이수시간 채워줘", sources, target, answers=(("key_column", "사번"),), output_path=out
    )
    assert ("key", "추천인사번", "사번") in by_number.unsure
    assert "문서의 ‘추천인사번’ 값으로, 확인 필요" in by_number.interpretation


def test_when_every_pair_is_only_a_suggestion_the_question_names_it(files):
    """3차 M4: 짝이 모두 추천이면 막다른 질문 대신 이름과 추천 열을 보인다."""
    sources, target, out, _ = files

    def knows(prompt, options):
        return "직위" if "직위" in options else "없음"

    asked = prepare("이름 기준으로 직급 채워줘", sources, target, output_path=out, choose=knows)
    assert asked.question is not None and "‘직위’(추천: 직급 열)" in asked.question.text
