from dataclasses import replace

import pytest
from conftest import para, write_hwpx
from openpyxl import load_workbook

from ureonggaksi.export import ExportError, export_excel
from ureonggaksi.extract import extract
from ureonggaksi.hwpx import read_hwpx
from ureonggaksi.workflow import Rule, Workflow


def test_xlsx_readback_trace_and_formula_safety(tmp_path):
    source = write_hwpx(tmp_path / "=문서.hwpx", [para('=HYPERLINK("https://evil.test", "click")')])
    wf = Workflow("시험", (Rule("=항목", "regex", query="(.+)"),))
    result = extract(read_hwpx(source), wf)
    target = tmp_path / "result.xlsx"
    export_excel((result,), wf, target)
    wb = load_workbook(target)
    assert wb.sheetnames == ["취합", "출처", "실행정보"]
    assert wb["취합"]["B2"].value.startswith("=HYPERLINK")
    assert wb["취합"]["B2"].data_type == "s"
    assert wb["취합"]["B1"].data_type == "s"
    assert wb["취합"]["B2"].hyperlink.location == "'출처'!A2"
    assert wb["취합"]["B2"].hyperlink.target is None
    assert result.sha256 in str(list(wb["출처"].values))
    assert "Contents/section0.xml" in str(list(wb["출처"].values))
    assert all(c.data_type != "f" for s in wb for row in s for c in row)


def test_export_rejects_loss_and_keeps_existing_file(sample, tmp_path):
    wf = Workflow("시험", (Rule("이름", "label", query="성명"),))
    result = extract(read_hwpx(sample), wf)
    target = tmp_path / "old.xlsx"
    target.write_bytes(b"existing")
    value = replace(result.values[0], value="x" * 32768)
    with pytest.raises(ExportError):
        export_excel((replace(result, values=(value,)),), wf, target)
    assert target.read_bytes() == b"existing"


def test_failed_document_export_includes_error(tmp_path):
    from ureonggaksi.models import DocumentResult

    wf = Workflow("시험", (Rule("이름", "label", query="성명"),))
    result = DocumentResult(str(tmp_path / "bad.hwpx"), "", (), "손상된 문서")
    target = tmp_path / "errors.xlsx"
    export_excel((result,), wf, target)
    wb = load_workbook(target)
    assert "손상된 문서" in str(list(wb["취합"].values))
