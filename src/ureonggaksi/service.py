"""prepare·commit·describe_rule: 한 줄 요청 → 미리보기 → [저장] (api 2.0, P04 ③, FR-05·06·14).

화면과 CLI는 이 모듈의 함수만 부른다. 함수는 입력을 받아 새 값을 돌려주고 대화 상태를 갖지 않는다.
질문에 답하면 호출자가 answers를 늘려 prepare를 다시 부른다.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from .analyze import Cancel, ChangeItem, Plan, Progress, analyze, manual_items
from .errors import Pairs, ServiceError
from .inputs import Inputs, inspect_source, open_inputs
from .interpret import Chooser, build_rule, interpret
from .manual import ManualItem
from .rules import RuleSet, RuleStore, WorkflowVersion, validate_rule
from .save import ResultPackage, approve, default_output_path, save_check, save_result
from .xlsx import TargetView, inspect_target

QUESTION_RATIO = 0.30  # 공통 문제 질문 기준(api 2.0). Q01 정답셋으로 조정한다
SKIP = "__skip__"  # 답: 건너뛰기. 해당 칸은 비워 둔다
# 답 하나로 정해지는 공통 문제(PRD 6.2). 날짜 순서는 읽기 실패와 같은 코드라 아직 따로 묻지 못한다
POLICY_SLOTS = {"ambiguous_unit": "default_unit", "needs_rounding": "rounding"}
POLICY_CHOICES = {
    "default_unit": ("원", "천원", "백만원"),
    "rounding": ("down", "up", "half_up"),
}
POLICY_TEXT = {
    "default_unit": "금액에 단위가 없는 값이 많습니다. 어떤 단위로 읽을까요?",
    "rounding": "원 단위로 바꾸면 소수가 생기는 값이 많습니다. 어떻게 처리할까요?",
}
AI_ERRORS = {"AI_UNAVAILABLE", "AI_TIMEOUT", "AI_BAD_OUTPUT"}


def _no_progress(stage: str, current: int, total: int) -> None:
    pass


@dataclass(frozen=True)
class Question:
    slot: str
    text: str
    examples: tuple[str, ...]
    choices: tuple[str, ...]  # 비면 버튼 없이 '자세히'의 직접 선택으로 안내한다
    affects: int  # 이 답으로 정해지는 칸 수. 규칙이 서지 않는 질문은 0


@dataclass(frozen=True)
class Prepared:
    request: str
    answers: Pairs
    inputs: Inputs
    question: Question | None  # 물을 것이 있으면 하나만. 이때 plan은 None
    plan: Plan | None
    interpretation: str
    samples: tuple[ChangeItem, ...]
    warnings: tuple[str, ...]
    manual: tuple[ManualItem, ...]
    output_path: Path | None
    workflow: WorkflowVersion | None = (
        None  # '최근 작업'으로 연 규칙. commit이 같은 작업에 이어 저장한다
    )


# ---------- 해석 한 줄 ----------


def _letter(view: TargetView, column: str) -> str:
    letters = sorted(
        {c.address.rstrip("0123456789") for c in view.cells}, key=lambda s: (len(s), s)
    )
    index = view.columns.index(column) if column in view.columns else -1
    return letters[index] if 0 <= index < len(letters) else ""


def describe_rule(rule: RuleSet, view: TargetView) -> str:
    """검증된 규칙에서 문장 틀로 만든 한 줄. AI가 쓴 문장이 아니다."""
    key = rule.target.key_column
    parts = []
    for field in rule.fields:
        if not field.target_column:
            continue
        letter = _letter(view, field.target_column)
        place = f"{letter}열({field.target_column})" if letter else f"‘{field.target_column}’ 열"
        parts.append(f"‘{field.name}’을 {place}")
    joined = ", ".join(parts)
    if rule.work_type == "check":
        return f"검토: 표를 바꾸지 않고 {key}(으)로 행을 찾아 {joined}과 대조함"
    return f"채우기: {key}(으)로 행을 찾아 {joined}에 넣음"


# ---------- prepare ----------


def _safe(choose: Chooser | None) -> Chooser | None:
    """모델 오류는 그 짝만 질문으로 남기고 계속한다(P04 ③ 사용자 결정 2026-11-17)."""
    if choose is None:
        return None

    def ask(prompt: str, options: tuple[str, ...]) -> str | None:
        try:
            return choose(prompt, options)
        except ServiceError as error:
            if error.code in AI_ERRORS:
                return None
            raise

    return ask


def _full_view(inputs: Inputs, rule: RuleSet | None = None) -> TargetView:
    """데이터 행 전체를 담은 보기(interpret 계약). 먼저 기본 보기로 범위를 알고 다시 연다."""
    if rule is not None:
        sheet, header = rule.target.sheet, rule.target.header_row
    else:
        sheet, header = None, None
    first = inspect_target(inputs, sheet, header)
    rows = max(first.data_rows[1] - first.data_rows[0] + 1, 0)
    return inspect_target(inputs, first.sheet, first.header_row, preview_rows=rows)


def _answer(answers: Pairs, slot: str) -> str | None:
    return next((value for key, value in answers if key == slot), None)


def _rule_question(slot: str, options: tuple[str, ...]) -> Question:
    if slot == "work_type":
        return Question("work_type", "빈칸을 채울까요, 값만 대조할까요?", (), ("fill", "check"), 0)
    if slot == "key_column":
        return Question(
            "key_column", "어느 열로 행을 찾을까요?", (), tuple(o for o in options if o)[:3], 0
        )
    return Question(
        slot,
        "어느 항목을 어느 열에 넣을지 정하지 못했습니다. '자세히'에서 직접 골라 주세요.",
        (),
        (),
        0,
    )


def _apply_answers(result, answers: Pairs, view: TargetView):
    """규칙이 서지 않을 때의 답(work_type·key_column)을 해석 결과에 반영한다."""
    from .interpret import _key_candidates  # 같은 해석 기준으로 키 항목을 찾는다

    work_type = _answer(answers, "work_type")
    if work_type in ("fill", "check") and result.work_type is None:
        result = replace(
            result,
            work_type=work_type,
            questions=tuple(q for q in result.questions if q[0] != "work_type"),
        )
    column = _answer(answers, "key_column")
    if column and result.key is None and column in view.columns:
        fields = list(result.candidates)
        matches = {
            c[1] for c in _key_candidates(fields, view, named=True, any_hit=True) if c[2] == column
        }
        if len(matches) == 1:
            field = matches.pop()
            result = replace(
                result,
                key=(field, column),
                record=replace(result.record, key_field=field),
                questions=tuple(q for q in result.questions if q[0] != "key_column"),
            )
    return result


def _with_policy(rule: RuleSet, answers: Pairs) -> RuleSet:
    policy = dict(rule.policy)
    for slot in POLICY_CHOICES:
        value = _answer(answers, slot)
        if value is not None and value != SKIP:
            policy[slot] = value
    return replace(rule, policy=tuple(sorted(policy.items())))


def _policy_question(plan: Plan, answers: Pairs) -> Question | None:
    """답 하나로 정해지는 공통 문제의 칸이 분석 대상의 QUESTION_RATIO 이상이면 하나만 묻는다."""
    total = len(plan.items)
    counts = dict(plan.problem_counts)
    best = None
    for code, slot in POLICY_SLOTS.items():
        affects = int(counts.get(code, 0))
        if not total or affects < QUESTION_RATIO * total or _answer(answers, slot) is not None:
            continue
        if best is None or affects > best[1]:
            best = (slot, affects, code)
    if best is None:
        return None
    slot, affects, code = best
    examples = tuple(
        dict.fromkeys(e.raw_text for i in plan.items if i.reason_code == code for e in i.evidence)
    )[:2]
    return Question(slot, POLICY_TEXT[slot], examples, POLICY_CHOICES[slot], affects)


def _samples(plan: Plan) -> tuple[ChangeItem, ...]:
    """완료 영역 최대 3건. 서로 다른 원본을 먼저 고르고, 순서는 change_id로 정해 결정적이다."""
    done = sorted((i for i in plan.items if i.area == "done"), key=lambda i: i.change_id)
    picked: list[ChangeItem] = []
    seen: set[str] = set()
    for item in done:
        source = item.evidence[0].file_id if item.evidence else ""
        if source not in seen:
            picked.append(item)
            seen.add(source)
        if len(picked) == 3:
            return tuple(picked)
    for item in done:
        if item not in picked:
            picked.append(item)
        if len(picked) == 3:
            break
    return tuple(picked)


def _asking(request, answers, inputs, question, workflow=None) -> Prepared:
    return Prepared(request, answers, inputs, question, None, "", (), (), (), None, workflow)


def prepare(
    request: str,
    sources: list[Path],
    target: Path,
    *,
    workflow: WorkflowVersion | None = None,
    answers: Pairs = (),
    output_path: Path | None = None,
    choose: Chooser | None = None,
    progress: Progress = _no_progress,
    cancel: Cancel | None = None,
) -> Prepared:
    """쓰기 없음. 규칙이 서지 않거나 공통 문제가 크면 질문 하나, 아니면 미리보기를 돌려준다."""
    out_dir = local_dir(output_path, target)
    inputs = open_inputs(list(sources), target, out_dir)
    warnings: list[str] = []
    if workflow is not None:  # '최근 작업': AI도 해석도 부르지 않는다
        rule = workflow.rule
        view = _full_view(inputs, rule)
    else:
        view = _full_view(inputs)
        documents = tuple(
            inspect_source(inputs, s.file_id) for s in inputs.sources if s.status == "ok"
        )
        if not documents:
            raise ServiceError("INPUT_UNSUPPORTED", "읽을 수 있는 원본 문서가 없습니다.")
        result = interpret(request, documents, view, choose=_safe(choose))
        result = _apply_answers(result, answers, view)
        rule = build_rule(result, view)
        if rule is None:
            slot, options = result.questions[0] if result.questions else ("target_column", ())
            return _asking(request, answers, inputs, _rule_question(slot, options))
        warnings += [f"‘{name}’은(는) 정하지 못해 비워 둡니다." for name in result.excluded]
    rule = _with_policy(rule, answers)
    issues = validate_rule(rule, inputs, view)
    if issues:
        return _asking(request, answers, inputs, _rule_question("target_column", ()), workflow)
    plan = analyze(inputs, rule, progress=progress, cancel=cancel)
    question = _policy_question(plan, answers)
    if question is not None:
        return _asking(request, answers, inputs, question, workflow)
    path = output_path or default_output_path(plan)
    return Prepared(
        request,
        answers,
        inputs,
        None,
        plan,
        describe_rule(rule, view),
        _samples(plan),
        tuple(warnings),
        manual_items(plan),
        path,
        workflow,
    )


def local_dir(output_path: Path | None, target: Path) -> Path:
    """결과 폴더: 출력 경로를 줬으면 그 폴더, 아니면 대상 파일 폴더(api 2.0 기본값)."""
    return Path(output_path).parent if output_path is not None else Path(target).parent


# ---------- commit ----------


def _rule_name(prepared: Prepared) -> str:
    stem = Path(prepared.inputs.target.path).stem.strip() or "취합"
    return f"{stem} 규칙"[:100]


def commit(
    prepared: Prepared,
    store: RuleStore,
    *,
    progress: Progress = _no_progress,
    cancel: Cancel | None = None,
) -> ResultPackage:
    """[저장] 한 번 = 승인. ① 규칙 저장(같은 지문이면 생략) ② 채우기: approve→save_result / 검토: save_check."""
    plan = prepared.plan
    if plan is None or prepared.question is not None:
        raise ServiceError("APPROVAL_INVALID", "질문에 먼저 답해야 저장할 수 있습니다.")
    store.save(
        prepared.workflow.name if prepared.workflow else _rule_name(prepared),
        plan.rule,
        base=prepared.workflow,
        request=prepared.request[:500],
    )
    if plan.rule.work_type == "check":
        return save_check(plan, progress=progress, cancel=cancel)
    approval = approve(plan, prepared.output_path)
    return save_result(plan, approval, progress=progress, cancel=cancel)
