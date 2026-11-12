"""최소 변경 쓰기 독립 검수 1차의 회귀 시험 (P03-1 H1~H3, M1~M4, L1~L3)."""

import os
import re
import shutil
import subprocess

import pytest
from test_patch import (
    AT22,
    SHEET,
    entries,
    failures,
    number,
    replace_entry,
    sheet_path,
    write_xlsx_bytes,
)

from ureonggaksi.models import CellValue
from ureonggaksi.patch import PatchError, compare_packages, patch_xlsx


def sheet_with(sheet_data: bytes) -> bytes:
    """기본 합성 통합문서의 sheetData를 sheet_data로 바꾼다."""
    base = write_xlsx_bytes([["코드", "값"], ["0012", None]])
    part = sheet_path(base)
    xml = re.sub(
        rb"<sheetData>.*</sheetData>|<sheetData/>", sheet_data, entries(base)[part], flags=re.S
    )
    return replace_entry(base, part, xml)


def test_cells_inside_an_array_formula_range_are_refused():
    """H1: 배열 수식 범위 안의 칸(자기 <f>가 없어도)에 쓰면 Excel에서 값이 사라진다."""
    data = sheet_with(
        b'<sheetData><row r="1"><c r="A1"><f t="array" ref="A1:A3">1</f><v>1</v></c></row>'
        b'<row r="2"><c r="A2"><v>2</v></c></row></sheetData>'
    )
    with pytest.raises(PatchError, match="수식"):
        patch_xlsx(data, SHEET, {"A2": number("99")})


def test_literal_x_escape_text_is_escaped_and_verified():
    """H2: 글자 '_x0041_'은 Excel이 'A'로 풀지 않도록 _x005F_로 감싼다."""
    original = write_xlsx_bytes([["코드", "값"], ["0012", None]])
    writes = {"B2": CellValue("text", "가 _x0041_ =A1")}
    patched = patch_xlsx(original, SHEET, writes)
    assert b"_x005F_x0041_" in entries(patched)[sheet_path(patched)]
    assert failures(compare_packages(original, patched, SHEET, writes)) == []


@pytest.mark.parametrize("tamper", ["move_cell", "swap_cells", "drop_row", "dimension_attr"])
def test_compare_catches_structural_tampering(tamper):
    """H3: 칸을 다른 행으로 옮기거나 순서·행·dimension을 바꾸면 비교가 잡는다."""
    original = write_xlsx_bytes(
        [["코드", "값", "비고"], ["0012", None, "x"], None, None, ["0015", 1, 2]]
    )
    writes = {"B2": number("7")}
    patched = patch_xlsx(original, SHEET, writes)
    part = sheet_path(patched)
    xml = entries(patched)[part]
    if tamper == "move_cell":
        start = xml.index(b'<c r="C2"')
        cell = xml[start : xml.index(b"</c>", start) + 4]
        xml = xml.replace(cell, b"", 1).replace(b'<c r="A5"', cell + b'<c r="A5"', 1)
    elif tamper == "swap_cells":
        first = xml[xml.index(b'<c r="A2"') : xml.index(b'<c r="B2"')]
        xml = xml.replace(first, b"", 1)
        end = xml.index(b"</row>", xml.index(b'<row r="2"'))
        xml = xml[:end] + first + xml[end:]
    elif tamper == "drop_row":
        start = xml.index(b'<row r="5"')
        xml = xml[:start] + xml[xml.index(b"</row>", start) + 6 :]
    else:
        xml = xml.replace(b"<dimension ", b'<dimension extra="1" ', 1)
    assert failures(compare_packages(original, replace_entry(patched, part, xml), SHEET, writes))


def test_empty_self_closing_row_and_sheet_data_are_writable():
    """M1·M2: Excel 템플릿의 빈 행 <row .../>, 빈 <sheetData/>에도 쓰고 비교가 통과한다."""
    data = sheet_with(
        b'<sheetData><row r="1"><c r="A1"><v>1</v></c></row>'
        b'<row r="2" ht="30" customHeight="1"/></sheetData>'
    )
    writes = {"B2": number("5"), "A2": CellValue("text", "코드")}
    assert failures(compare_packages(data, patch_xlsx(data, SHEET, writes), SHEET, writes)) == []
    empty = sheet_with(b"<sheetData/>")
    writes = {"A1": number("1"), "A2": number("2"), "B3": number("3")}
    patched = patch_xlsx(empty, SHEET, writes)
    assert entries(patched)[sheet_path(patched)].count(b"<sheetData") == 1
    assert failures(compare_packages(empty, patched, SHEET, writes)) == []


def test_new_cells_inherit_column_style_for_protection_and_dates():
    """M3: 열 전체를 잠금 해제·날짜 서식으로 둔 템플릿의 빈 입력 칸에 쓸 수 있고 그 서식을 물려받는다."""
    original = AT22.read_bytes()
    part = "xl/worksheets/sheet1.xml"
    # 픽스처의 F열(제출일) 서식 s=5는 잠금 해제 + 날짜 서식이다. G열 전체에 같은 서식을 준다
    xml = entries(original)[part].replace(
        b"<sheetData>",
        b'<cols><col min="7" max="7" style="5" width="12" customWidth="1"/></cols><sheetData>',
        1,
    )
    data = replace_entry(original, part, xml)
    writes = {"G3": CellValue("date", "2026-11-06")}
    patched = patch_xlsx(data, SHEET, writes)
    assert b'<c r="G3" s="5">' in entries(patched)[part]
    assert failures(compare_packages(data, patched, SHEET, writes)) == []


def test_existing_full_calc_off_is_turned_on_and_compare_allows_it():
    """M4: 원본 calcPr에 fullCalcOnLoad="0"이 있어도 비교가 통과한다."""
    original = AT22.read_bytes()
    workbook = entries(original)["xl/workbook.xml"].replace(
        b"<calcPr ", b'<calcPr fullCalcOnLoad="0" ', 1
    )
    data = replace_entry(original, "xl/workbook.xml", workbook)
    writes = {"C2": number("1")}
    patched = patch_xlsx(data, SHEET, writes)
    assert b'fullCalcOnLoad="1"' in entries(patched)["xl/workbook.xml"]
    assert failures(compare_packages(data, patched, SHEET, writes)) == []


@pytest.mark.parametrize(
    ("address", "value"),
    [
        ("B2", CellValue("number", "NaN")),
        ("B2", CellValue("number", "1E+5")),
        ("B2", CellValue("number", "1_000")),
        ("B2", CellValue("bool", "true")),
        ("B2", CellValue("text", "a\rb")),
        ("b2", number("1")),  # 소문자 주소
    ],
)
def test_malformed_values_and_addresses_are_refused(address, value):
    with pytest.raises(PatchError):
        patch_xlsx(write_xlsx_bytes([["코드", "값"], ["0012", None]]), SHEET, {address: value})


def test_analysis_and_writer_agree_on_column_style_protection(tmp_path):
    """분석(xlsx)도 XML에 없는 칸의 잠금을 열 서식으로 판정한다(쓰기와 같은 규칙)."""
    from conftest import write_hwpx
    from openpyxl import Workbook
    from openpyxl.styles import Protection

    from ureonggaksi.inputs import open_inputs
    from ureonggaksi.xlsx import inspect_target

    book = Workbook()
    ws = book.active
    ws.title = SHEET
    ws.append(["코드", "값"])
    ws.append(["0012", None])
    ws.column_dimensions["B"].protection = Protection(locked=False)
    ws.protection.sheet = True
    path = tmp_path / "t.xlsx"
    book.save(path)
    view = inspect_target(open_inputs([write_hwpx(tmp_path / "a.hwpx")], path, tmp_path / "out"))
    cells = {c.address: c.blocked for c in view.cells}
    assert (cells["A2"], cells["B2"]) == ("protected", "")
    data = path.read_bytes()
    writes = {"B2": number("1")}
    assert failures(compare_packages(data, patch_xlsx(data, SHEET, writes), SHEET, writes)) == []


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("powershell") is None or os.environ.get("CI") == "true",
    reason="이 PC의 Excel로 실제 열기(로컬 전용)",
)
def test_excel_reads_inserted_rows_and_escaped_text_as_written(tmp_path):
    """새 행·새 칸 끼우기, 빈 행에 쓰기, _x 글자, '='로 시작하는 글자를 Excel이 쓴 그대로 읽는다."""
    data = sheet_with(
        b'<sheetData><row r="1"><c r="A1"><v>1</v></c></row>'
        b'<row r="3" ht="30" customHeight="1"/></sheetData>'
    )
    writes = {
        "B9": CellValue("text", "_x0041_ 가 =A1"),
        "A3": number("3"),
        "AA2": CellValue("text", "=SUM(A1:A3)"),
    }
    path = tmp_path / "result.xlsx"
    path.write_bytes(patch_xlsx(data, SHEET, writes))
    out = tmp_path / "out.txt"
    script = tmp_path / "open.ps1"
    script.write_text(
        f"""$ErrorActionPreference = 'Stop'
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false; $excel.DisplayAlerts = $false
try {{
  $wb = $excel.Workbooks.Open('{path}', 0, $true)
  $ws = $wb.Worksheets.Item(1)
  $line = @($ws.Range('B9').Value2, $ws.Range('A3').Value2, $ws.Range('AA2').Value2, $ws.Range('AA2').HasFormula) -join '|'
  [IO.File]::WriteAllText('{out}', $line, [Text.Encoding]::UTF8)
  $wb.Close($false)
}} finally {{ $excel.Quit() }}
""",
        encoding="utf-8-sig",
    )
    done = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        timeout=180,
    )
    stderr = done.stderr.decode("cp949", errors="replace")
    if done.returncode != 0 and ("80040154" in stderr or "ComObject" in stderr):
        pytest.skip("Excel 없음")
    assert done.returncode == 0, stderr
    b9, a3, aa2, aa2_formula = out.read_text(encoding="utf-8-sig").strip().split("|")
    assert (b9, a3, aa2, aa2_formula) == ("_x0041_ 가 =A1", "3", "=SUM(A1:A3)", "False")
