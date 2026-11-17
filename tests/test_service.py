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


def test_a_model_answer_fills_a_differently_named_pair(files):
    sources, target, out, _ = files

    def knows(prompt, options):
        return "직위" if "직위" in options else "없음"

    prepared = prepare(
        "이름 기준으로 직급이랑 이수시간 채워줘", sources, target, output_path=out, choose=knows
    )
    targets = {f.name: f.target_column for f in prepared.plan.rule.fields if f.target_column}
    assert targets == {"직위": "직급", "이수시간": "이수시간"}


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
