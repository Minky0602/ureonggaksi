import os
from zipfile import ZipFile

import pytest
from conftest import cell, para, table, write_hwpx

from ureonggaksi.export import ExportError, export_excel
from ureonggaksi.extract import extract
from ureonggaksi.hwpx import HwpxError, read_hwpx
from ureonggaksi.storage import atomic_write, local_path
from ureonggaksi.workflow import Rule, Workflow, WorkflowError


def test_atomic_failure_leaves_existing_file_and_removes_temporary(tmp_path):
    target = tmp_path / "original.json"
    target.write_text("original")

    def fail(path):
        path.write_text("partial")
        raise OSError("disk full")

    with pytest.raises(OSError):
        atomic_write(target, fail)
    assert target.read_text() == "original"
    assert len(list(tmp_path.iterdir())) == 1


def test_unc_paths_are_rejected_before_access():
    with pytest.raises(ValueError):
        local_path("//example.invalid/share/secret.hwpx")


@pytest.mark.skipif(os.name != "nt", reason="backslash UNC is a Windows path rule")
def test_backslash_unc_paths_are_rejected_on_windows():
    with pytest.raises(ValueError):
        local_path(r"\\example.invalid\share\secret.hwpx")


def test_xml_depth_and_package_external_reference(tmp_path):
    path = write_hwpx(tmp_path / "depth.hwpx", ["<hp:p>" * 130 + "</hp:p>" * 130])
    with pytest.raises(HwpxError):
        read_hwpx(path)
    path = write_hwpx(
        path,
        extra={
            "Contents/content.hpf": '<package><item id="s" href="https://example.invalid/x"/></package>'
        },
    )
    with pytest.raises(HwpxError):
        read_hwpx(path)


def test_no_external_hyperlink_relationship(sample, tmp_path):
    wf = Workflow("안전", (Rule("성명", "label", query="성명"),))
    path = tmp_path / "result.xlsx"
    export_excel((extract(read_hwpx(sample), wf),), wf, path)
    with ZipFile(path) as archive:
        rels = [archive.read(n) for n in archive.namelist() if n.endswith(".rels")]
    assert all(b'TargetMode="External"' not in rel for rel in rels)


def test_overly_complex_regex_is_validation_error():
    with pytest.raises(WorkflowError):
        Rule("깊은 패턴", "regex", query="(" * 600 + "a" + ")" * 600)


def test_below_merged_label_and_optional_missing(tmp_path):
    xml = (
        "<hp:p><hp:run>"
        + table([[cell("항목", 0, 0, rowspan=2)], [cell("값", 2, 0)]])
        + "</hp:run></hp:p>"
    )
    path = write_hwpx(tmp_path / "below.hwpx", [xml])
    wf = Workflow(
        "시험",
        (
            Rule("항목", "label", query="항목", direction="below"),
            Rule("선택", "label", query="없음", required=False),
        ),
    )
    result = extract(read_hwpx(path), wf)
    assert result.values[0].value == "값"
    assert result.values[1].status == "missing"
    assert result.ok


def test_control_characters_are_rejected_on_export(tmp_path):
    from dataclasses import replace

    path = write_hwpx(tmp_path / "normal.hwpx", [para("text")])
    wf = Workflow("시험", (Rule("본문", "regex", query="(.+)"),))
    result = extract(read_hwpx(path), wf)
    result = replace(result, values=(replace(result.values[0], value="evil\x01"),))
    with pytest.raises(ExportError):
        export_excel((result,), wf, tmp_path / "invalid.xlsx")


def test_workflow_save_cannot_replace_original(sample):
    before = sample.read_bytes()
    wf = Workflow("안전", (Rule("성명", "label", query="성명"),))
    with pytest.raises(WorkflowError):
        wf.save(sample)
    assert sample.read_bytes() == before


def test_unknown_xml_encoding_is_document_error(tmp_path):
    from zipfile import ZIP_DEFLATED

    path = tmp_path / "encoding.hwpx"
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", b'<?xml version="1.0" encoding="nonsense"?><sec/>')
    with pytest.raises(HwpxError):
        read_hwpx(path)


def test_corrupt_deflate_is_document_error(tmp_path):
    path = write_hwpx(tmp_path / "corrupt.hwpx")
    with ZipFile(path) as z:
        info = z.getinfo("Contents/section0.xml")
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    data = bytearray(path.read_bytes())
    data[offset : offset + 4] = b"\xff\xff\xff\xff"
    path.write_bytes(data)
    with pytest.raises(HwpxError):
        read_hwpx(path)


def test_missing_cell_address_attributes_are_not_guessed(tmp_path):
    c = cell("추측 금지", 0, 0).replace('rowAddr="0" colAddr="0"', "")
    path = write_hwpx(
        tmp_path / "no-address.hwpx", ["<hp:p><hp:run>" + table([[c]]) + "</hp:run></hp:p>"]
    )
    with pytest.raises(HwpxError):
        read_hwpx(path)
