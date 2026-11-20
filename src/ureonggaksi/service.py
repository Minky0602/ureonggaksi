"""prepare·commit·describe_rule: 한 줄 요청 → 미리보기 → [저장] (api 2.0, P04 ③, FR-05·06·14).

화면과 CLI는 이 모듈의 함수만 부른다. 함수는 입력을 받아 새 값을 돌려주고 대화 상태를 갖지 않는다.
질문에 답하면 호출자가 answers를 늘려 prepare를 다시 부른다.
"""

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

from .analyze import Cancel, ChangeItem, Plan, Progress, analyze, manual_items
from .errors import Pairs, ServiceError
from .inputs import Inputs, inspect_source, open_inputs
from .interpret import AskMany, Chooser, _same, build_rule, interpret
from .manual import ManualItem
from .rules import POLICY_VALUES, SKIP, RuleSet, RuleStore, WorkflowVersion, validate_rule
from .save import ResultPackage, approve, default_output_path, save_result
from .storage import local_path
from .xlsx import TargetView, inspect_target

QUESTION_RATIO = 0.30  # 공통 문제 질문 기준(api 2.0). Q01 정답셋으로 조정한다
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
    # 코드가 확신하지 못한 해석: (종류, 이름, 대상 열). 미리보기에 '확인 필요'로 보인다(DIF-050).
    # pair: 모델이 읽은 짝(문서 항목, 열). 이름이 다른 짝(성명→신청인)도 같은 이름 짝처럼 채운다(2026-11-20
    # 사용자 결정). 미리보기는 값 예시와 [빼기](답 drop_field)를 보인다 / skip: 빼라는 말로 읽고 비워 둔 이름
    # (이름, "") / key: 키 질문에 열만 답했는데 값이 맞는 문서 항목의 이름이 그 열과 다름(문서 항목, 열)
    unsure: tuple[tuple[str, str, str], ...] = ()


# ---------- 해석 한 줄 ----------


def _letter(view: TargetView, column: str) -> str:
    letters = sorted(
        {c.address.rstrip("0123456789") for c in view.cells}, key=lambda s: (len(s), s)
    )
    index = view.columns.index(column) if column in view.columns else -1
    return letters[index] if 0 <= index < len(letters) else ""


def examples(prepared: "Prepared", field: str, limit: int = 2) -> tuple[str, ...]:
    """그 문서 항목이 실제로 들어갈 값 예시. 이름 짝보다 값을 보면 틀린 짝을 바로 알 수 있다."""
    if prepared.plan is None:
        return ()
    values = (
        str(i.proposed.value)
        for i in prepared.plan.items
        if i.field == field and i.area == "done" and i.proposed
    )
    return tuple(dict.fromkeys(values))[:limit]


def describe_rule(rule: RuleSet, view: TargetView, unsure=()) -> str:
    """검증된 규칙에서 문장 틀로 만든 한 줄. AI가 쓴 문장이 아니다. 확신하지 못한 해석에는 '(확인 필요)'."""
    unsure = set(unsure)
    key = f"{rule.target.key_column}(으)로 행을 찾아"
    by = [n for kind, n, c in unsure if kind == "key" and c == rule.target.key_column]
    if by:  # 열만 보면 남의 번호(추천인사번)로 찾는 것을 알 수 없다(3차 M1)
        key = f"{rule.target.key_column}(으)로 행을 찾아(문서의 ‘{by[0]}’ 값으로, 확인 필요)"
    parts = []
    for field in rule.fields:
        if not field.target_column:
            continue
        letter = _letter(view, field.target_column)
        place = f"{letter}열({field.target_column})" if letter else f"‘{field.target_column}’ 열"
        mark = " (확인 필요)" if ("pair", field.name, field.target_column) in unsure else ""
        parts.append(f"‘{field.name}’을 {place}{mark}")
    joined = ", ".join(parts)
    skipped = ", ".join(f"‘{name}’" for kind, name, _ in sorted(unsure) if kind == "skip")
    tail = f"; {skipped}은(는) 빼라는 말로 읽고 비워 둠 (확인 필요)" if skipped else ""
    if rule.work_type == "check":
        return f"검토: 표를 바꾸지 않고 {key} {joined}과 대조함{tail}"
    return f"채우기: {key} {joined}에 넣음{tail}"


# ---------- prepare ----------


def _safe(choose: Chooser | None, failures: list[str] | None = None) -> Chooser | None:
    """모델 오류는 그 짝만 질문으로 남기고 계속한다(P04 ③ 사용자 결정 2026-11-17). 오류는 failures에 모은다."""
    if choose is None:
        return None

    def ask(prompt: str, options: tuple[str, ...]) -> str | None:
        try:
            return choose(prompt, options)
        except ServiceError as error:
            if error.code in AI_ERRORS:
                if failures is not None:
                    failures.append(error.message)
                return None
            raise

    return ask


def _noted(ask_many: AskMany | None, failures: list[str]) -> AskMany | None:
    """interpret가 AI 오류를 받아 모델 없이 계속하므로, 오류를 failures에 남기고 그대로 넘긴다."""
    if ask_many is None:
        return None

    def ask(prompt: str, fields: dict[str, tuple[str, ...]]) -> dict[str, str]:
        try:
            return ask_many(prompt, fields)
        except ServiceError as error:
            if error.code in AI_ERRORS:
                failures.append(error.message)
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


RULE_SLOTS = ("work_type", "key_column", "target_column")


def _check_answers(answers: Pairs) -> None:
    """답은 아는 질문의 허용 값만 받는다. 정책 답은 규칙 저장 형식과 같은 값(api 2.4)."""
    for slot, value in answers:
        ok = (
            (slot == "work_type" and value in ("fill", "check", SKIP))
            or (
                slot in ("key_column", "target_column", "drop_field")
                and isinstance(value, str)
                and value
            )
            or (slot in POLICY_CHOICES and (value == SKIP or value in POLICY_VALUES.get(slot, ())))
        )
        if not ok:
            raise ServiceError("INPUT_UNSUPPORTED", f"답이 올바르지 않습니다: {slot}")


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


def _key_question(result, view: TargetView, options: tuple[str, ...]) -> Question:
    """키 질문. 열마다 값이 맞는 문서 항목을 함께 보여 남의 번호(추천인사번 → 사번)를 고르지 않게 한다(DIF-050 H-1)."""
    question = _rule_question("key_column", options)
    hints = [
        f"{c}은(는) 문서의 ‘{f}’"
        for c in question.choices
        if (f := _key_field_for(result, view, c))
    ]
    if not hints:
        return question
    return replace(question, text=f"{question.text} {', '.join(hints)} 값과 맞습니다.")


def _key_field_for(result, view: TargetView, column: str) -> str | None:
    """그 열과 값이 맞는 문서 항목이 하나뿐이면 그 항목. 둘 이상(성명·추천인)이면 답으로 정할 수 없다."""
    from .interpret import _key_candidates  # 같은 해석 기준으로 키 항목을 찾는다

    fields = list(result.candidates)
    matches = {
        c[1] for c in _key_candidates(fields, view, named=True, any_hit=True) if c[2] == column
    }
    return matches.pop() if len(matches) == 1 else None


def _apply_answers(result, answers: Pairs, view: TargetView):
    """규칙이 서지 않을 때의 답(work_type·key_column)을 해석 결과에 반영한다. (결과, 키 때문에 뺀 짝)."""
    dropped: tuple[tuple[str, str, str], ...] = ()
    work_type = _answer(answers, "work_type")
    if work_type in ("fill", "check") and result.work_type is None:
        result = replace(
            result,
            work_type=work_type,
            questions=tuple(q for q in result.questions if q[0] != "work_type"),
        )
    column = _answer(answers, "key_column")
    if column and result.key is None and column in view.columns:
        field = _key_field_for(result, view, column)
        if field is not None:
            # 키를 모를 때 만든 짝 가운데 키 항목·키 열의 짝은 뺀다. 키 열은 이미 값이 있는 열이다(3차 H1)
            kept = tuple(p for p in result.fields if p[0] != field and p[1] != column)
            dropped = tuple(p for p in result.fields if p not in kept)
            result = replace(
                result,
                fields=kept,
                key=(field, column),
                record=replace(result.record, key_field=field),
                questions=tuple(q for q in result.questions if q[0] != "key_column"),
            )
    return result, dropped


def _with_policy(rule: RuleSet, answers: Pairs) -> RuleSet:
    policy = dict(rule.policy)
    for slot in POLICY_CHOICES:
        value = _answer(answers, slot)
        if value is not None:  # 건너뛴 답(SKIP)도 저장해 다시 묻지 않는다(AT-21)
            policy[slot] = value
    return replace(rule, policy=tuple(sorted(policy.items())))


def _policy_question(plan: Plan, answers: Pairs) -> Question | None:
    """답 하나로 정해지는 공통 문제의 칸이 분석 대상의 QUESTION_RATIO 이상이면 하나만 묻는다."""
    total = len(plan.items)
    counts = dict(plan.problem_counts)
    best = None
    for code, slot in POLICY_SLOTS.items():
        affects = int(counts.get(code, 0))
        if not total or affects < QUESTION_RATIO * total or plan.rule.answered(slot):
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
    ask_many: AskMany | None = None,
    progress: Progress = _no_progress,
    cancel: Cancel | None = None,
) -> Prepared:
    """쓰기 없음. 규칙이 서지 않거나 공통 문제가 크면 질문 하나, 아니면 미리보기를 돌려준다."""
    _check_answers(answers)
    out_dir = local_dir(output_path, target)
    inputs = open_inputs(list(sources), target, out_dir)
    warnings: list[str] = []
    unsure: tuple[tuple[str, str, str], ...] = ()
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

        def read(chooser, reader):
            found = interpret(request, documents, view, choose=chooser, ask_many=reader)
            unnamed = found.key is None
            return (*_apply_answers(found, answers, view), unnamed)

        failures: list[
            str
        ] = []  # 모델 오류(모델 없이 해석한 부분이 있음). 사용자에게 알린다(DIF-053)
        # 작업 종류·키를 물어야 하면 모델을 부르지 않고 바로 묻는다. 둘은 코드만으로 정해진다(DIF-054)
        result, used_for_key, unnamed_key = read(None, None)
        pending = any(q[0] in ("work_type", "key_column") for q in result.questions)
        if not pending and (choose is not None or ask_many is not None):
            result, used_for_key, unnamed_key = read(
                _safe(choose, failures), _noted(ask_many, failures)
            )
        # 열만 답한 키의 문서 항목 이름이 열과 다르면(추천인사번 → 사번) 미리보기에도 보인다(3차 M1)
        answered_key = (
            [("key", *result.key)] if unnamed_key and result.key and not _same(*result.key) else []
        )
        # 사용자가 미리보기에서 [빼기]한 짝은 쓰지 않는다(답 drop_field = 문서 항목)
        dropped = {value for slot, value in answers if slot == "drop_field"}
        removed = tuple(p for p in result.fields if p[0] in dropped)
        if removed:
            kept = tuple(p for p in result.fields if p[0] not in dropped)
            asks = result.questions if kept else (*result.questions, ("target_column", ()))
            result = replace(result, fields=kept, questions=asks)
        rule = build_rule(result, view)
        if rule is None:
            slot, options = result.questions[0] if result.questions else ("target_column", ())
            if (
                _answer(answers, slot) is not None
            ):  # 건너뛰었거나 답으로 정해지지 않았다: 같은 질문을 되풀이하지 않는다
                raise ServiceError(
                    "RULE_INVALID",
                    "이 질문은 답으로 정할 수 없습니다. '자세히'에서 직접 지정하세요.",
                )
            if (
                slot == "key_column"
            ):  # 답으로 정해지는 열만 선택지로 준다(검수 M-a). 없으면 '자세히'로 안내
                options = tuple(c for c in options if _key_field_for(result, view, c))
                return _asking(request, answers, inputs, _key_question(result, view, options))
            question = _rule_question(slot, options)
            if failures:  # 모델이 돌지 않아 질문이 생겼을 수 있다(DIF-053)
                question = replace(
                    question, text=f"{question.text} (로컬 AI를 쓰지 못했습니다: {failures[0]})"
                )
            return _asking(request, answers, inputs, question)
        if failures:
            warnings.append(f"로컬 AI를 쓰지 못해 일부를 AI 없이 해석했습니다({failures[0]}).")
        warnings += [f"‘{name}’은(는) 정하지 못해 비워 둡니다." for name in result.excluded]
        warnings += [  # 키로 쓴 항목·열의 짝은 채우지 않는다(4차 M1)
            f"‘{c}’ 열은 행을 찾는 데 쓴 ‘{f}’과(와) 겹쳐 채우지 않고 비워 둡니다."
            for f, c, _ in used_for_key
        ]
        if result.unread:  # 모델이 열과 잇지 못한 말. 빠진 항목일 수 있다(2차 M-1)
            words = "·".join(f"‘{w}’" for w in result.unread)
            warnings.append(
                f"요청의 {words}은(는) 어느 열로도 정하지 못했습니다. 빠진 항목이 있으면 '자세히'에서 고르세요."
            )
        warnings += [f"‘{f}’ → {c} 열은 빼서 비워 둡니다." for f, c, _ in removed]
        warnings += [  # 뺄 짝이 없는 이름: 뺐다고 믿지 않게 알린다(검수 L1)
            f"‘{name}’은(는) 뺄 짝이 없습니다. 문서 항목 이름으로 빼세요."
            for name in sorted(dropped - {p[0] for p in removed})
        ]
        unsure = (
            *answered_key,
            *(("pair", f, c) for f, c, source in result.fields if source != "match"),
            *(("skip", name, "") for name in result.skipped),
        )
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
        describe_rule(rule, view, unsure),
        _samples(plan),
        tuple(warnings),
        manual_items(plan),
        path,
        workflow,
        unsure,
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
    # 검토도 prepare가 정한 경로로 승인한다(기본 경로는 시각이 들어가 다시 만들면 달라진다, 검수 M1).
    # 승인 확인(경로 충돌 등)을 규칙 저장보다 먼저 해, 저장하지 못할 결과의 규칙을 남기지 않는다(검수 L-a)
    approval = approve(plan, prepared.output_path)
    store.save(
        prepared.workflow.name if prepared.workflow else _rule_name(prepared),
        plan.rule,
        base=prepared.workflow,
        request=prepared.request[:500],
    )
    return save_result(plan, approval, progress=progress, cancel=cancel)


# ---------- CLI 두 단계(api 2.9) ----------


def replay(
    rule: RuleSet,
    sources: list[Path],
    target: Path,
    *,
    request: str = "",
    output_path: Path | None = None,
    workflow: WorkflowVersion | None = None,
    progress: Progress = _no_progress,
    cancel: Cancel | None = None,
) -> Prepared:
    """계획 파일의 규칙으로 AI 없이 다시 분석한다. 질문은 만들지 않는다(규칙이 이미 정해졌다)."""
    inputs = open_inputs(list(sources), target, local_dir(output_path, target))
    view = _full_view(inputs, rule)
    if validate_rule(rule, inputs, view):
        raise ServiceError(
            "RULE_INVALID", "계획의 규칙이 지금 파일에 맞지 않습니다. 다시 prepare 하세요."
        )
    plan = analyze(inputs, rule, progress=progress, cancel=cancel)
    path = output_path or default_output_path(plan)
    return Prepared(
        request,
        (),
        inputs,
        None,
        plan,
        describe_rule(rule, view),
        _samples(plan),
        (),
        manual_items(plan),
        path,
        workflow,
    )


def approval_hash(prepared: Prepared) -> str:
    """[저장]이 승인할 지문: 계획 지문(plan_hash)에 저장될 요청문·이어 저장할 작업을 더한 것.

    commit이 쓰는 것(결과 파일·근거·규칙과 그 요청문·작업 revision)이 모두 들어가야 CLI가 본 것만 저장한다(검수 M2).
    """
    plan = prepared.plan
    if plan is None:
        raise ServiceError("APPROVAL_INVALID", "질문에 먼저 답해야 저장할 수 있습니다.")
    workflow = prepared.workflow
    paths = [str(local_path(f.path)) for f in (*plan.inputs.sources, plan.inputs.target)]
    parts = (
        approve(plan, prepared.output_path).plan_hash,
        "|".join(paths),  # 같은 내용의 다른 경로로 바꿔도 다른 승인이다(검수 M-b)
        prepared.request[:500],
        f"{workflow.workflow_id}:{workflow.revision}" if workflow else "",
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
