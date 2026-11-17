"""CLI 두 단계: prepare(해석·미리보기·계획 지문) → commit(지문이 같을 때만 저장) (api 2.9, FR-14).

화면이 없어도 "본 것만 저장"을 지킨다. commit은 계획 파일의 규칙으로 AI 없이 다시 분석하고,
계산한 지문이 --plan-hash와 같을 때만 쓴다. 계획 파일에는 원문 값을 넣지 않는다(경로·지문·규칙만).
"""

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

from .ai import LocalModel, ai_status
from .errors import ServiceError
from .rules import RuleError, RuleStore, rule_from_dict, rule_to_dict
from .save import publish_new
from .service import Prepared, approval_hash, commit, prepare, replay
from .storage import local_path

PLAN_FORMAT = "ureonggaksi-plan/1"
EXIT_OK, EXIT_PARTIAL, EXIT_ERROR, EXIT_QUESTION = 0, 1, 2, 3


def _write_plan(path: Path, data: bytes, taken: list[Path]) -> None:
    """계획 파일은 새로 만들기만 한다. 이미 있거나 입력·결과 경로와 같으면 쓰지 않는다(검수 H1)."""
    final = local_path(path)
    if any(final == local_path(p) for p in taken if p):
        raise ServiceError("PATH_CONFLICT", "계획 파일 경로가 입력이나 결과 파일과 같습니다.")
    if final.exists():
        raise ServiceError("PATH_CONFLICT", "계획 파일이 이미 있습니다. 다른 경로를 지정하세요.")
    handle, name = tempfile.mkstemp(prefix=".ureonggaksi-", suffix=".tmp", dir=final.parent)
    temp = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        publish_new(temp, final)
    except FileExistsError as exc:
        raise ServiceError(
            "PATH_CONFLICT", "계획 파일이 이미 있습니다. 다른 경로를 지정하세요."
        ) from exc
    finally:
        with contextlib.suppress(OSError):
            temp.unlink()


def _store(args) -> RuleStore:
    return RuleStore(args.rules_dir) if args.rules_dir else RuleStore()


def _answers(raw: list[str], parser) -> tuple[tuple[str, str], ...]:
    pairs = []
    for item in raw:
        slot, sep, value = item.partition("=")
        if not sep or not slot.strip() or not value.strip():
            parser.error(f"--answer는 SLOT=값 꼴이어야 합니다: {item}")
        pairs.append((slot.strip(), value.strip()))
    return tuple(pairs)


def _chooser(args):
    """실행 파일과 모델 파일이 모두 있으면 짝 선택에 쓴다. 없으면 모델 없이 해석한다."""
    status = ai_status(args.runtime, args.model)
    if not status.ready:
        return None, "로컬 모델: 사용 안 함"
    return LocalModel((str(args.runtime),), args.model).choose, "로컬 모델: 사용"


def _print_preview(prepared: Prepared) -> None:
    plan = prepared.plan
    counts = plan.counts
    print(f"해석: {prepared.interpretation}")
    if plan.rule.work_type == "check":
        print(
            f"일치 {counts.cells_unchanged} · 직접 확인 {sum(1 for i in plan.items if i.area == 'manual')}"
        )
    else:
        print(
            f"채울 칸 {counts.cells_write} · 이미 같은 값 {counts.cells_unchanged} · "
            f"직접 처리 {sum(1 for i in plan.items if i.area == 'manual')} · 읽지 못한 파일 {counts.files_failed}"
        )
    for item in prepared.samples:
        print(f"  예: {item.target_ref} ← {item.proposed.value} ({item.field})")
    for warning in prepared.warnings:
        print(f"참고: {warning}")


def run_prepare(args, parser) -> int:
    try:
        store = _store(args)
        workflow = None
        if args.workflow:
            workflow_id, _, revision = args.workflow.partition(":")
            workflow = store.load(workflow_id, int(revision) if revision else None)
        choose, model_note = _chooser(args)
        print(model_note)
        prepared = prepare(
            args.request,
            list(args.files),
            args.target,
            workflow=workflow,
            answers=_answers(args.answer, parser),
            output_path=args.output,
            choose=choose,
        )
        if prepared.question is not None:
            question = prepared.question
            print(f"질문({question.slot}): {question.text}")
            if question.examples:
                print("  예시: " + ", ".join(question.examples))
            if question.choices:
                print(
                    f"  답: --answer {question.slot}=<{'|'.join(question.choices)}> 로 다시 실행하세요."
                )
            return EXIT_QUESTION
        _print_preview(prepared)
        digest = approval_hash(prepared)
        plan = prepared.plan
        record = {
            "format": PLAN_FORMAT,
            "request": prepared.request,
            "workflow": (
                f"{prepared.workflow.workflow_id}:{prepared.workflow.revision}"
                if prepared.workflow
                else ""
            ),
            "sources": [str(Path(s.path)) for s in plan.inputs.sources],
            "target": str(Path(plan.inputs.target.path)),
            "output": str(prepared.output_path),
            "fingerprints": [
                [f.file_id, f.sha256 or ""] for f in (*plan.inputs.sources, plan.inputs.target)
            ],
            "rule": rule_to_dict(plan.rule),
            "plan_hash": digest,
        }
        data = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        taken = [*args.files, args.target, prepared.output_path]
        _write_plan(args.out, data, taken)
        print(f"계획 파일: {args.out}")
        print(f"계획 지문: {digest}")
        print(f'저장: ureonggaksi commit --plan "{args.out}" --plan-hash {digest}')
        return EXIT_OK
    except (ServiceError, RuleError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR


def run_commit(args) -> int:
    try:
        record = json.loads(local_path(args.plan).read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("format") != PLAN_FORMAT:
            raise ServiceError("APPROVAL_INVALID", "계획 파일 형식이 아닙니다.")
        if args.plan_hash != record.get("plan_hash"):
            raise ServiceError("PLAN_HASH_MISMATCH", "계획 지문이 계획 파일과 다릅니다.")
        store = _store(args)
        workflow = None
        if record.get("workflow"):  # '최근 작업'으로 만든 계획은 그 작업에 이어 저장한다(검수 M2)
            workflow_id, _, revision = str(record["workflow"]).partition(":")
            workflow = store.load(workflow_id, int(revision) if revision else None)
        prepared = replay(
            rule_from_dict(record["rule"]),
            [Path(p) for p in record["sources"]],
            Path(record["target"]),
            request=str(record.get("request", "")),
            output_path=Path(record["output"]),
            workflow=workflow,
        )
        if approval_hash(prepared) != args.plan_hash:  # 파일이 바뀌었거나 계획이 손댔다
            raise ServiceError(
                "PLAN_HASH_MISMATCH",
                "파일이나 계획이 prepare 때와 달라졌습니다. 다시 prepare 하세요.",
            )
        package = commit(prepared, store)
        for path in (package.output_path, package.manual_path, package.evidence_path):
            if path:
                print(f"저장: {path}")
        print(f"상태: {package.status}")
        return EXIT_OK if package.status == "completed" else EXIT_PARTIAL
    except (
        ServiceError,
        RuleError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        print(str(exc) or "계획 파일 형식이 올바르지 않습니다.", file=sys.stderr)
        return EXIT_ERROR
