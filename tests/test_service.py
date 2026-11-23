"""한 줄 요청 → 미리보기 → [저장] (P04 ③, FR-05·06·14). 합성 자료만 쓴다."""

import pytest
from conftest import cell, para, table, write_hwpx, write_xlsx

from ureonggaksi import service as service_module
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


def test_a_column_the_model_picked_is_filled_and_marked_with_examples(files):
    """이름이 다른 짝(직위→직급)은 같은 이름 짝처럼 채우고, 값 예시와 함께 '확인 필요'로 보인다(2026-11-20)."""
    from ureonggaksi.service import examples

    sources, target, out, _ = files

    def knows(prompt, options):
        return "직위" if "직위" in options else "없음"

    prepared = prepare(
        "이름 기준으로 직급이랑 이수시간 채워줘", sources, target, output_path=out, choose=knows
    )
    targets = {f.name: f.target_column for f in prepared.plan.rule.fields if f.target_column}
    assert targets == {"직위": "직급", "이수시간": "이수시간"}
    assert ("pair", "직위", "직급") in prepared.unsure
    assert examples(prepared, "직위") == ("주무관", "팀장")


def test_a_dropped_pair_is_left_blank(files):
    """미리보기의 [빼기](답 drop_field)는 그 짝만 비우고 나머지는 그대로 둔다."""
    sources, target, out, _ = files

    def knows(prompt, options):
        return "직위" if "직위" in options else "없음"

    request = "이름 기준으로 직급이랑 이수시간 채워줘"
    kept = prepare(
        request, sources, target, answers=(("drop_field", "직위"),), output_path=out, choose=knows
    )
    targets = {f.name: f.target_column for f in kept.plan.rule.fields if f.target_column}
    assert targets == {"이수시간": "이수시간"} and any("빼서 비워" in w for w in kept.warnings)
    only = prepare(
        request,
        sources,
        target,
        answers=(("drop_field", "직위"), ("drop_field", "이수시간")),
        output_path=out,
        choose=knows,
    )
    assert only.question is not None and only.question.slot == "target_column"


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


def test_a_synonym_word_brings_in_its_column(files):
    """'직책'이 직급을 가리킨다고 모델이 읽으면 그 열도 채우고 표시한다."""
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
    assert targets == {"직위": "직급", "이수시간": "이수시간"}
    assert {("pair", "이수시간", "이수시간"), ("pair", "직위", "직급")} <= set(prepared.unsure)


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


def test_a_disputed_skip_is_left_blank_and_the_rest_is_filled(files):
    """코드는 '나중에'로 빼라고 읽었는데 모델이 채우라고 읽으면 그 열만 비우고 알린다(DIF-062).
    갈린 열 말고 채울 것이 없으면 그 열을 짚어 묻는다(DIF-060)."""
    sources, target, out, _ = files
    both = reader({"이수시간": "채움", "직급": "채움"})
    prepared = prepare(
        "이름 기준으로 직급은 나중에 하고 이수시간 채워줘",
        sources,
        target,
        output_path=out,
        ask_many=both,
    )
    targets = [f.target_column for f in prepared.plan.rule.fields if f.target_column]
    assert targets == ["이수시간"]
    assert "‘직급’은(는) 뺄지 채울지 확실하지 않아 비워 둡니다." in prepared.warnings
    alone = prepare(
        "이름 기준으로 직급은 나중에 하고 나머지 채워 주세요",
        sources,
        target,
        output_path=out,
        ask_many=reader({"직급": "채움"}),
    )
    assert alone.plan is None and alone.question.slot == "target_column"
    assert alone.question.text.startswith("‘직급’을(를) 뺄지 채울지 확실하지 않아")


def test_a_skip_without_a_model_answer_keeps_the_plain_question(files):
    """모델 오류나 답이 없으면 갈린 것이 아니다. 이미 '빼고'라고 쓴 사람에게 '빼고'라고 쓰라 하지 않는다(PR #28 검수)."""
    sources, target, out, _ = files
    request = "이름 기준으로 직급은 빼고 이수시간 채워줘"

    def broken(prompt, fields):
        raise ServiceError("AI_TIMEOUT", "시간 초과")

    failed = prepare(request, sources, target, output_path=out, ask_many=broken)
    assert failed.question is not None and "뺄지 채울지" not in failed.question.text
    # 모델이 직급에 답하지 않으면 직급만 비우고 이수시간은 채운다. 갈렸다고 하지 않고, 빼라는 말로
    # 읽은 이름으로 '확인 필요'에 남긴다(검수 M2: 소명 없이 사라지면 안 된다)
    silent = prepare(
        request, sources, target, output_path=out, ask_many=reader({"이수시간": "채움"})
    )
    assert silent.question is None and not any("뺄지 채울지" in w for w in silent.warnings)
    assert ("skip", "직급", "") in silent.unsure
    assert "직급" not in {f.target_column for f in silent.plan.rule.fields}
    # 두 번 물은 답이 어긋나도(채움 ↔ 제외) 답이 없을 때와 같다(2차 검수 L-b)
    turns = iter(
        [reader({"직급": "채움", "이수시간": "채움"}), reader({"직급": "제외", "이수시간": "채움"})]
    )

    def disagree(prompt, fields):
        return next(turns)(prompt, fields)

    split = prepare(request, sources, target, output_path=out, ask_many=disagree)
    assert split.question is None and not any("뺄지 채울지" in w for w in split.warnings)
    assert ("skip", "직급", "") in split.unsure
    assert "직급" not in {f.target_column for f in split.plan.rule.fields}


def test_a_question_left_by_a_dropped_pair_does_not_blame_the_conflict(files):
    """검수 L1: 갈린 직급 말고 남은 짝을 사용자가 [빼기]해 비었으면, 갈림 탓으로 묻지 않는다."""
    sources, target, out, _ = files
    request = "이름 기준으로 직급은 나중에 하고 이수시간 채워줘"
    model = reader({"직급": "채움", "이수시간": "채움"})
    first = prepare(request, sources, target, output_path=out, ask_many=model)
    assert first.question is None and any("뺄지 채울지" in w for w in first.warnings)
    emptied = prepare(
        request,
        sources,
        target,
        output_path=out,
        ask_many=model,
        answers=(("drop_field", "이수시간"),),
    )
    assert emptied.question is not None and "뺄지 채울지" not in emptied.question.text


def test_the_conflict_text_names_every_column_and_reads_for_any_ending():
    text = service_module._conflict_text(("비고", "직급"))
    assert text.startswith("‘비고’·‘직급’을(를) 뺄지 채울지")
    assert "‘비고은(는) 빼고’" in text


def test_unsure_pairs_and_exclusions_are_shown_with_examples(files, capsys):
    """CLI는 추정 짝마다 값 예시와 빼는 방법을, 빼기로 읽은 이름은 그대로 보인다."""
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
    synonym = prepare(
        "이름 기준으로 직급이랑 이수시간 채워줘", sources, target, output_path=out, choose=knows
    )
    assert [k for k, _, _ in skipped.unsure] == ["pair", "skip"]
    assert "(확인 필요)" in skipped.interpretation and "빼라는 말로" in skipped.interpretation
    _print_preview(skipped)
    _print_preview(synonym)
    shown = [x for x in capsys.readouterr().out.splitlines() if x.startswith("확인 필요:")]
    assert len(shown) == 3 and "직급 ← 문서 ‘직위’  예: 주무관, 팀장" in shown[-1]


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
        "이름 기준으로 담당업무랑 이수시간 채워 주시면 돼요",
        sources,
        target,
        output_path=out,
        ask_many=reader({"이수시간": "채움"}),
    )
    assert any("‘담당업무’" in w and "정하지 못했습니다" in w for w in prepared.warnings)


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
        assert any("‘성명’ 열은 행을 찾는 데" in w for w in answered.warnings)


def test_a_key_answered_ahead_still_shows_its_document_field(tmp_path):
    """3차 M1: 질문을 보기 전에 답을 준 경로에서도 남의 번호(추천인사번 → 사번)로 찾는 키는 '확인 필요'다."""
    sources, target, out = _referral_files(tmp_path)
    by_number = prepare(
        "이수시간 채워줘", sources, target, answers=(("key_column", "사번"),), output_path=out
    )
    assert ("key", "추천인사번", "사번") in by_number.unsure
    assert "문서의 ‘추천인사번’ 값으로, 확인 필요" in by_number.interpretation


def test_a_pending_key_question_is_asked_without_running_the_model(tmp_path):
    """키를 물어야 하면 모델을 부르지 않고 바로 묻는다(지연, DIF-054)."""
    sources, target, out = _referral_files(tmp_path)
    calls = []

    def counting(prompt, options):
        calls.append(prompt)
        return "없음"

    def reading(prompt, fields):
        calls.append(prompt)
        return {}

    request = "이수시간 채워 주세요 ㅎㅎ"
    asked = prepare(request, sources, target, output_path=out, choose=counting, ask_many=reading)
    assert asked.question is not None and asked.question.slot == "key_column" and calls == []
    prepare(
        request,
        sources,
        target,
        answers=(("key_column", "성명"),),
        output_path=out,
        choose=counting,
        ask_many=reading,
    )
    assert calls  # 키가 정해진 뒤에야 모델이 읽는다


def test_dropping_a_name_with_no_pair_is_warned(files):
    """검수 L1: 열 이름이나 없는 이름으로 빼면 아무것도 빠지지 않으므로 그렇다고 알린다."""
    sources, target, out, _ = files

    def knows(prompt, options):
        return "직위" if "직위" in options else "없음"

    prepared = prepare(
        "이름 기준으로 직급이랑 이수시간 채워줘",
        sources,
        target,
        answers=(("drop_field", "직급"),),
        output_path=out,
        choose=knows,
    )
    assert ("pair", "직위", "직급") in prepared.unsure
    assert any("‘직급’은(는) 뺄 짝이 없습니다" in w for w in prepared.warnings)


def test_a_negated_everything_stays_off_after_the_work_type_answer(files):
    """검수 H-4: '전부 넣지 마'에 작업 종류를 '채우기'로 답해도 문서 항목 전부를 채우지 않는다."""
    sources, target, out, _ = files
    request = "발급번호 기준으로 전부 넣지 마"
    model = reader({}, {"직위": "직급/채움"})
    first = prepare(request, sources, target, output_path=out, ask_many=model)
    assert first.question is not None and first.question.slot == "work_type"
    again = prepare(
        request, sources, target, output_path=out, ask_many=model, answers=(("work_type", "fill"),)
    )
    assert again.plan is None or not again.plan.rule.fields


def test_an_assumed_key_is_shown_as_needing_a_check(tmp_path):
    """말하지 않은 키를 추정하면(이름 같음·값 모두 맞음) 미리보기에 '확인 필요'로 보인다(2026-11-23)."""
    sources = []
    for i, (name, hours) in enumerate([("가나다", "12"), ("라마바", "8"), ("사아자", "4")]):
        rows = [("성명", name), ("이수시간", hours)]
        body = table([[cell(k, r, 0), cell(v, r, 1)] for r, (k, v) in enumerate(rows)])
        sources.append(
            write_hwpx(tmp_path / f"d{i}.hwpx", ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"])
        )
    sheet = [["성명", "이수시간"], ["사아자", None], ["가나다", None], ["라마바", None]]
    target = write_xlsx(tmp_path / "명단.xlsx", sheet, sheet="명단")
    prepared = prepare("이수시간 채워줘", sources, target, output_path=tmp_path / "out.xlsx")
    assert prepared.question is None and prepared.plan is not None
    assert ("key", "성명", "성명") in prepared.unsure
    assert "성명(으)로 행을 찾아(확인 필요)" in prepared.interpretation


def test_a_key_answer_wins_over_an_assumed_key(tmp_path):
    """검수 r1 L1: 미리 준 키 답이 추정한 키보다 앞선다."""
    sources = []
    people = [("가나다", "S-1001", "12"), ("라마바", "S-1002", "8"), ("사아자", "S-1003", "4")]
    for i, (name, number, hours) in enumerate(people):
        rows = [("성명", name), ("사번", number), ("이수시간", hours)]
        body = table([[cell(k, r, 0), cell(v, r, 1)] for r, (k, v) in enumerate(rows)])
        sources.append(
            write_hwpx(tmp_path / f"d{i}.hwpx", ["<hp:p><hp:run>" + body + "</hp:run></hp:p>"])
        )
    sheet = [
        ["사번", "성명", "이수시간"],
        ["S-1001", "가나다", None],
        ["S-1002", "라마바", None],
        ["S-1003", "사아자", None],
    ]
    target = write_xlsx(tmp_path / "명단.xlsx", sheet, sheet="명단")
    out = tmp_path / "out.xlsx"
    assumed = prepare("이수시간 채워줘", sources, target, output_path=out)
    assert any(kind == "key" for kind, _, _ in assumed.unsure)
    for column in ("성명", "사번"):
        answered = prepare(
            "이수시간 채워줘", sources, target, answers=(("key_column", column),), output_path=out
        )
        assert answered.plan.rule.target.key_column == column
        assert not any(kind == "key" for kind, _, _ in answered.unsure)
