"""최소 변경 쓰기와 패키지 비교 검증 (PRD FR-15·16, AT-09·22, api.md 3절, DIF-012)."""

import io
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from conftest import write_xlsx

from ureonggaksi.models import CellValue
from ureonggaksi.patch import PatchError, compare_packages, patch_xlsx, sheet_part

FIXTURES = Path(__file__).parent / "fixtures"
AT22 = FIXTURES / "excel_at22.xlsx"
D1904 = FIXTURES / "excel_1904.xlsx"
SHEET = "사업취합"


def number(value):
    return CellValue("number", value)


def entries(data: bytes) -> dict[str, bytes]:
    with ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def failures(checks):
    return [c.message for c in checks if not c.passed]


def test_at22_only_written_cells_change_and_everything_else_is_preserved():
    original = AT22.read_bytes()
    writes = {
        "C2": number("3500000"),
        "C3": number("35000000"),
        "F2": CellValue("date", "2026-11-06"),
    }
    patched = patch_xlsx(original, SHEET, writes)
    assert failures(compare_packages(original, patched, SHEET, writes)) == []
    before, after = entries(original), entries(patched)
    assert list(before) == list(after)
    changed = sorted(n for n in before if before[n] != after[n])
    assert changed == ["xl/workbook.xml", "xl/worksheets/sheet1.xml"]  # 수식이 있으니 재계산 표시
    sheet = after["xl/worksheets/sheet1.xml"]
    # 서식(s)은 유지, 수식 캐시값·x14 확장·도형 연결·보호는 그대로
    assert b'<c r="C2" s="4"><v>3500000</v></c>' in sheet
    assert b"<f>C2+D2</f><v>1000000</v>" in sheet
    assert b"x14:conditionalFormattings" in sheet and b'<drawing r:id="rId1"/>' in sheet
    assert b"sheetProtection" in sheet
    assert b'fullCalcOnLoad="1"' in after["xl/workbook.xml"]
    assert before["docProps/core.xml"] == after["docProps/core.xml"]  # 저장 시각도 그대로(DIF-022)
    assert before["xl/sharedStrings.xml"] == after["xl/sharedStrings.xml"]


def test_text_is_written_inline_and_formula_like_text_stays_text():
    original = write_xlsx_bytes([["코드", "비고"], ["0012", None]])
    writes = {"B2": CellValue("text", "=SUM(A1:A9) & <위험>")}
    patched = patch_xlsx(original, "사업취합", writes)
    assert failures(compare_packages(original, patched, "사업취합", writes)) == []
    sheet = entries(patched)[sheet_path(patched)]
    assert b't="inlineStr"' in sheet and b"<f>" not in sheet
    assert b"=SUM(A1:A9) &amp; &lt;\xec\x9c\x84\xed\x97\x98&gt;" in sheet


def test_missing_cells_and_rows_are_inserted_in_order():
    original = write_xlsx_bytes(
        [["코드", "값", "비고"], ["0012", None, None], None, ["0014", None, None]]
    )
    writes = {
        "C2": CellValue("text", "끝 칸"),
        "B2": number("1"),
        "B3": number("2"),  # 없는 행
        "D9": number("3"),  # 범위 밖 행·열 → dimension 넓힘
    }
    patched = patch_xlsx(original, "사업취합", writes)
    assert failures(compare_packages(original, patched, "사업취합", writes)) == []
    sheet = entries(patched)[sheet_path(patched)].decode()
    assert sheet.index('r="B2"') < sheet.index('r="C2"')
    assert sheet.index('<row r="3">') < sheet.index('<row r="4"')
    assert 'ref="A1:D9"' in sheet


def test_blocked_cells_are_refused_again_at_write_time():
    original = AT22.read_bytes()
    with pytest.raises(PatchError, match="수식"):
        patch_xlsx(original, SHEET, {"E2": number("1")})
    with pytest.raises(PatchError, match="병합"):
        patch_xlsx(original, SHEET, {"C7": CellValue("text", "x")})
    with pytest.raises(PatchError, match="보호"):
        patch_xlsx(original, SHEET, {"D2": number("1")})  # D열은 잠긴 칸


def test_dates_use_the_workbook_date_system_and_need_a_date_style():
    original = D1904.read_bytes()
    writes = {"C3": CellValue("date", "2026-11-06")}
    patched = patch_xlsx(original, SHEET, writes)
    assert failures(compare_packages(original, patched, SHEET, writes)) == []
    assert b"<v>44870</v>" in entries(patched)[sheet_path(patched)]  # 1904 기준 일련번호
    with pytest.raises(PatchError, match="날짜 서식"):
        patch_xlsx(original, SHEET, {"B3": CellValue("date", "2026-11-06")})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (CellValue(), "빈 값"),
        (number("1234567890123456"), "15자리"),
        (CellValue("text", "a\x01b"), "쓸 수 없는"),
        (CellValue("text", "가" * 32_768), "쓸 수 없는"),
    ],
)
def test_values_excel_cannot_hold_exactly_are_refused(value, message):
    with pytest.raises(PatchError, match=message):
        patch_xlsx(write_xlsx_bytes([["코드", "값"], ["0012", None]]), "사업취합", {"B2": value})


def test_signed_workbooks_and_unknown_sheets_are_refused():
    original = write_xlsx_bytes([["코드"], ["0012"]])
    with pytest.raises(PatchError, match="시트"):
        patch_xlsx(original, "없는 시트", {"A2": number("1")})
    signed = add_entry(original, "_xmlsignatures/sig1.xml", b"<Signature/>")
    with pytest.raises(PatchError, match="전자서명"):
        patch_xlsx(signed, "사업취합", {"A3": number("1")})


def test_compare_detects_any_other_change():
    original = AT22.read_bytes()
    writes = {"C2": number("1")}
    patched = patch_xlsx(original, SHEET, writes)
    tampered = replace_entry(patched, "xl/styles.xml", entries(patched)["xl/styles.xml"] + b" ")
    assert any(
        "styles.xml" in m for m in failures(compare_packages(original, tampered, SHEET, writes))
    )
    sheet = entries(patched)["xl/worksheets/sheet1.xml"].replace(
        b"<v>4000000</v>", b"<v>4000001</v>", 1
    )
    tampered = replace_entry(patched, "xl/worksheets/sheet1.xml", sheet)
    assert any(
        "바뀐 칸" in m for m in failures(compare_packages(original, tampered, SHEET, writes))
    )
    assert failures(
        compare_packages(original, patched, SHEET, {"C2": number("2")})
    )  # 값이 예상과 다름


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("powershell") is None or os.environ.get("CI") == "true",
    reason="이 PC의 Excel로 실제 열기(로컬 전용)",
)
def test_excel_opens_the_result_without_repair_and_reads_the_written_values(tmp_path):
    original = AT22.read_bytes()
    writes = {
        "C2": number("3500000"),
        "F2": CellValue("date", "2026-11-06"),
        "C3": number("35000000"),
    }
    path = tmp_path / "result.xlsx"
    path.write_bytes(patch_xlsx(original, SHEET, writes))
    out = tmp_path / "excel_out.txt"
    script = tmp_path / "open.ps1"
    # PowerShell 5.1은 BOM 없는 .ps1을 ANSI로 읽는다 → utf-8-sig. 결과는 파일로 받아 인코딩 문제를 피한다
    script.write_text(
        f"""$ErrorActionPreference = 'Stop'
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false; $excel.DisplayAlerts = $false
try {{
  # CorruptLoad 기본값: Excel이 복구가 필요하면 열기 자체가 실패하거나 대화상자를 띄운다(DisplayAlerts 꺼짐 → 예외)
  $wb = $excel.Workbooks.Open('{path}', 0, $true)
  $ws = $wb.Worksheets.Item('{SHEET}')
  $line = @($ws.Range('C2').Value2, $ws.Range('C3').Value2, $ws.Range('F2').Value2, $ws.Range('F2').NumberFormat, $ws.Range('E2').Value2) -join '|'
  [IO.File]::WriteAllText('{out}', $line, [Text.Encoding]::UTF8)
  $wb.Close($false)
}} finally {{ $excel.Quit() }}
""",
        encoding="utf-8-sig",
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True,
            timeout=180,
        )
    except FileNotFoundError:
        pytest.skip("PowerShell 없음")
    stderr = done.stderr.decode("utf-8", errors="replace") + done.stderr.decode(
        "cp949", errors="replace"
    )
    if done.returncode != 0 and ("80040154" in stderr or "ComObject" in stderr):
        pytest.skip("Excel 없음")
    assert done.returncode == 0, stderr
    c2, c3, f2, f2_format, e2 = out.read_text(encoding="utf-8-sig").strip().split("|")
    serial = str((date(2026, 11, 6) - date(1899, 12, 30)).days)
    # 날짜는 일련번호 + 원래 날짜 서식(열 너비가 좁으면 표시 글자는 #### 이 된다). 수식 E2(=C2+D2)는 다시 계산된다
    assert (c2, c3, f2, f2_format, e2) == ("3500000", "35000000", serial, "yyyy-mm-dd", "4500000")


# ---------- 도우미 ----------


def write_xlsx_bytes(rows) -> bytes:
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "t.xlsx"
        write_xlsx(path, [r if r is not None else [] for r in rows])
        return path.read_bytes()


def sheet_path(data: bytes) -> str:
    with ZipFile(io.BytesIO(data)) as archive:
        return sheet_part(archive, "사업취합")


def replace_entry(data: bytes, name: str, payload: bytes) -> bytes:
    output = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as src, ZipFile(output, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, payload if item.filename == name else src.read(item.filename))
    return output.getvalue()


def add_entry(data: bytes, name: str, payload: bytes) -> bytes:
    output = io.BytesIO(data)
    with ZipFile(output, "a") as archive:
        archive.writestr(name, payload)
    return output.getvalue()
