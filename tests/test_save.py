"""승인 → 최소 변경 저장 → 검증 → 근거·확인필요 파일 (PRD FR-14~18·20~22, AT-10~13·18·20, api.md 2.7·3·4)."""

import hashlib
import json
from datetime import datetime

import pytest
from conftest import write_hwpx, write_xlsx
from test_analyze import SHEET, fill_rule, plan_doc

from ureonggaksi import save as save_module
from ureonggaksi.analyze import analyze
from ureonggaksi.errors import ServiceError
from ureonggaksi.inputs import open_inputs
from ureonggaksi.models import CellValue
from ureonggaksi.rules import FieldRule, RuleSet
from ureonggaksi.save import approve, plan_hash, save_check, save_result


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_plan(tmp_path, docs, rule=None, sheet=SHEET):
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = [write_hwpx(tmp_path / f"{name}.hwpx", body) for name, body in docs]
    target = write_xlsx(tmp_path / "사업취합.xlsx", sheet)
    inputs = open_inputs(paths, target, tmp_path)
    return analyze(inputs, rule or fill_rule()), paths, target


def output(tmp_path, name="결과.xlsx"):
    return tmp_path / name


def test_fill_save_writes_only_done_cells_and_publishes_three_files(tmp_path):
    """AT-18 축소판: 쓸 칸 + 직접 처리가 섞인 묶음을 저장한다. 입력은 그대로다."""
    docs = [
        ("정상", plan_doc("0012", "350만원")),
        ("다름", plan_doc("0013", "350만원")),
        ("없음", plan_doc("0099", "1원")),
    ]
    plan, paths, target = make_plan(tmp_path, docs)
    before = {p: sha(p) for p in [*paths, target]}
    package = save_result(plan, approve(plan, output(tmp_path)))
    assert package.status == "partial" and package.error is None
    assert all(c.passed for c in package.checks)
    assert {p: sha(p) for p in before} == before  # 입력 지문은 전후 같다(FR-15)
    from openpyxl import load_workbook

    sheet = load_workbook(package.output_path)["사업취합"]
    assert (sheet["C2"].value, sheet["C3"].value) == (
        3500000,
        1,
    )  # 0013은 기존값이 달라 쓰지 않는다
    assert package.output_sha256 == sha(package.output_path)
    evidence = json.loads(package.evidence_path.read_text(encoding="utf-8"))
    assert evidence["outputs"]["xlsx"]["sha256"] == package.output_sha256
    assert evidence["outputs"]["manual"]["sha256"] == sha(package.manual_path)
    assert evidence["approval"]["plan_hash"] and evidence["status"] == "partial"
    assert any(
        e["raw_text"] == "350만원" for i in evidence["items"] for e in i["evidence"]
    )  # 원문값 포함(OQ-05)
    text = package.manual_path.read_text(encoding="utf-8")
    assert text.startswith("[안내]") and "제출·전달용이 아니며" in text
    assert "채운 칸 1" in text and "이미 다른 값이 있음" in text and "들어갈 행을 못 정함" in text
    # 확인필요 항목은 화면 목록과 1:1이다
    assert text.count("\n  - ") == len(package.summary.manual)
    assert not list(tmp_path.glob(".ureonggaksi-*"))  # 임시 파일은 남지 않는다


def test_completed_when_nothing_is_left_for_the_user(tmp_path):
    sheet = [["사업코드", "사업명", "2026 예산"], ["0012", "정상", None]]
    rule = fill_rule(target=fill_rule().target.__class__("사업취합", 1, (2, 2), "사업코드"))
    plan, _, _ = make_plan(tmp_path, [("a", plan_doc("0012", "350만원"))], rule, sheet)
    package = save_result(plan, approve(plan, output(tmp_path)))
    assert package.status == "completed" and package.manual_path is None
    assert not (tmp_path / "결과_확인필요.txt").exists()


def test_nothing_to_write_makes_no_result_file(tmp_path):
    """AT-12: 쓸 칸이 0개면 결과를 만들지 않는다."""
    plan, _, _ = make_plan(tmp_path, [("없음", plan_doc("0099", "1원"))])
    with pytest.raises(ServiceError) as info:
        approve(plan, output(tmp_path))
    assert info.value.code == "NOTHING_TO_WRITE"
    assert not output(tmp_path).exists()


def test_input_changed_after_approval_blocks_saving(tmp_path):
    """AT-10: 미리보기 뒤 대상 XLSX가 바뀌면 저장하지 않는다."""
    plan, _, target = make_plan(tmp_path, [("a", plan_doc("0012", "350만원"))])
    approval = approve(plan, output(tmp_path))
    target.write_bytes(target.read_bytes() + b"\0")
    with pytest.raises(ServiceError) as info:
        save_result(plan, approval)
    assert info.value.code == "APPROVAL_INVALID"
    assert not output(tmp_path).exists()


def test_approval_binds_the_output_paths(tmp_path):
    plan, _, _ = make_plan(tmp_path, [("a", plan_doc("0012", "350만원"))])
    approval = approve(plan, output(tmp_path))
    other = save_module.Approval(**{**approval.__dict__, "output_path": tmp_path / "다른.xlsx"})
    with pytest.raises(ServiceError) as info:
        save_result(plan, other)
    assert info.value.code == "APPROVAL_INVALID"
    assert plan_hash(plan, tmp_path / "a.xlsx", None, None) != plan_hash(
        plan, tmp_path / "b.xlsx", None, None
    )


def test_existing_output_name_is_never_overwritten(tmp_path):
    """AT-11: 결과 파일 이름이 이미 있으면 쓰지 않는다."""
    plan, _, _ = make_plan(tmp_path, [("a", plan_doc("0012", "350만원"))])
    existing = output(tmp_path)
    existing.write_bytes(b"keep")
    with pytest.raises(ServiceError) as info:
        approve(plan, existing)
    assert info.value.code == "PATH_CONFLICT" and existing.read_bytes() == b"keep"
    approval = approve(plan, output(tmp_path, "새결과.xlsx"))
    approval.evidence_path.write_bytes(b"someone else")
    with pytest.raises(ServiceError):
        save_result(plan, approval)
    assert approval.evidence_path.read_bytes() == b"someone else"


def test_verification_failure_publishes_nothing(tmp_path, monkeypatch):
    """AT-11: 검증 값이 다르면 정상 결과를 내지 않는다(부분 완료로 낮추지 않는다)."""
    plan, _, _ = make_plan(tmp_path, [("a", plan_doc("0012", "350만원"))])
    approval = approve(plan, output(tmp_path))
    real = save_module.compare_packages
    monkeypatch.setattr(
        save_module,
        "compare_packages",
        lambda *a: (*real(*a), save_module.Check("cell", "C2", None, None, False, "조작된 불일치")),
    )
    package = save_result(plan, approval)
    assert package.status == "failed" and package.error.startswith("VERIFY_FAILED")
    assert package.output_path is None and not output(tmp_path).exists()
    assert not approval.evidence_path.exists()


def test_evidence_publish_failure_rolls_back_the_result(tmp_path, monkeypatch):
    """AT-13: 근거 보고서 공개가 실패하면 완료가 아니고, 먼저 공개한 결과는 지운다."""
    plan, _, _ = make_plan(
        tmp_path, [("a", plan_doc("0012", "350만원")), ("b", plan_doc("0099", "1원"))]
    )
    approval = approve(plan, output(tmp_path))
    real = save_module.publish_new

    def fail_on_json(temp, final):
        if final.suffix == ".json":
            raise PermissionError("디스크가 잠김")
        real(temp, final)

    monkeypatch.setattr(save_module, "publish_new", fail_on_json)
    package = save_result(plan, approval)
    assert package.status == "failed" and package.error.startswith("PUBLISH_FAILED")
    assert not approval.output_path.exists() and not approval.manual_path.exists()
    assert package.cleanup_failed == ()


def test_cancel_before_publishing_leaves_no_files(tmp_path):
    plan, _, _ = make_plan(tmp_path, [("a", plan_doc("0012", "350만원"))])
    approval = approve(plan, output(tmp_path))

    class Cancelled:
        def is_set(self):
            return True

    package = save_result(plan, approval, cancel=Cancelled())
    assert package.status == "cancelled" and not output(tmp_path).exists()
    assert not list(tmp_path.glob(".ureonggaksi-*"))


def test_check_mode_writes_no_xlsx(tmp_path):
    """AT-20: 값 검토는 결과 XLSX 없이 확인필요 파일과 근거만 만들고 입력은 그대로다."""
    docs = [("같음", plan_doc("0018", "350만원")), ("다름", plan_doc("0013", "350만원"))]
    plan, paths, target = make_plan(tmp_path, docs, fill_rule(work_type="check"))
    before = sha(target)
    package = save_check(plan)
    assert package.output_path is None and package.status == "partial"
    assert sha(target) == before
    text = package.manual_path.read_text(encoding="utf-8")
    assert "일치 1 · 불일치 1" in text and "어느 쪽이 맞는지는 판단하지 않습니다" in text
    evidence = json.loads(package.evidence_path.read_text(encoding="utf-8"))
    assert evidence["outputs"]["xlsx"] is None
    assert not list(tmp_path.glob("*_검토_*.xlsx"))


def test_unwritable_value_is_moved_to_manual_before_approval(tmp_path):
    """사용자 결정(2026-11-12): Excel에 그대로 넣을 수 없는 값은 그 칸만 직접 처리(write_unsupported)."""
    sheet = [["사업코드", "사업명", "제출일"], ["0012", "정상", None]]
    rule = RuleSet(
        fill_rule().record,
        (
            FieldRule("사업코드", "label", query="사업코드"),
            FieldRule("사업비", "label", query="사업비", value_type="date", target_column="제출일"),
        ),
        fill_rule().target.__class__("사업취합", 1, (2, 2), "사업코드"),
    )
    plan, _, _ = make_plan(tmp_path, [("a", plan_doc("0012", "2026-11-06"))], rule, sheet)
    (item,) = plan.items
    assert (item.reason_code, item.write_selected) == ("write_unsupported", False)
    assert "날짜 서식" in item.reason
    with pytest.raises(ServiceError):  # 쓸 칸이 없으니 결과 파일을 만들지 않는다
        approve(plan, output(tmp_path))


def test_default_output_name_uses_target_name_and_time(tmp_path):
    plan, _, _ = make_plan(tmp_path, [("a", plan_doc("0012", "350만원"))])
    name = save_module.default_output_path(plan, now=datetime(2026, 11, 12, 9, 5)).name
    assert name == "사업취합_우렁각시_20261112-0905.xlsx"
    assert CellValue("number", "1").kind == "number"
