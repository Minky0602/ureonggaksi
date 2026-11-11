"""취합표 미리보기: 시트·헤더·데이터 범위·열·기존값·막힌 칸 (PRD FR-03, api.md 2.3)."""

import datetime as dt
from pathlib import Path

import pytest
from conftest import write_hwpx, write_xlsx

from ureonggaksi.errors import ServiceError
from ureonggaksi.inputs import open_inputs
from ureonggaksi.models import CellValue
from ureonggaksi.xlsx import cell_value, inspect_target

FIXTURES = Path(__file__).parent / "fixtures"


def view_of(tmp_path, xlsx_path, **kwargs):
    source = write_hwpx(tmp_path / "a.hwpx")
    return inspect_target(open_inputs([source], xlsx_path, tmp_path / "out"), **kwargs)


def by_address(view):
    return {c.address: c for c in view.cells}


def test_default_view_detects_header_columns_and_data_range(tmp_path, target):
    view = view_of(tmp_path, target)
    assert (view.sheet, view.sheets) == ("사업취합", ("사업취합",))
    assert (view.header_row, view.data_rows) == (1, (2, 3))
    assert view.columns == ("사업코드", "사업명", "2026 예산")
    cells = by_address(view)
    assert cells["A2"].value == CellValue("text", "0012")  # 숫자로 바꾸지 않는다
    assert cells["C2"].value == CellValue("number", "3500000")
    assert cells["C3"].value == CellValue()  # 빈 칸
    assert all(c.blocked == "" for c in view.cells)


def test_title_rows_above_the_header_are_skipped(tmp_path):
    rows = [
        ["2026년 사업 취합표", None, None],
        [None, None, None],
        ["사업코드", "사업명", "2026 예산"],
        ["0012", "도서관", 1],
        ["0013", "체육관", 2],
        [None, None, None],
    ]
    view = view_of(tmp_path, write_xlsx(tmp_path / "t.xlsx", rows))
    assert (view.header_row, view.data_rows) == (3, (4, 5))
    assert view.columns == ("사업코드", "사업명", "2026 예산")


def test_changing_sheet_or_header_updates_columns_and_range(tmp_path):
    rows = [["메모", None], ["코드", "값"], ["0012", 7]]
    path = write_xlsx(tmp_path / "t.xlsx", rows, extra_sheets=("빈 시트",))
    view = view_of(tmp_path, path, header_row=2)
    assert (view.columns, view.data_rows) == (("코드", "값"), (3, 3))
    view = view_of(tmp_path, path, header_row=1)
    assert view.columns == ("메모", "B")  # 빈 헤더 칸은 열 문자
    assert view.data_rows == (2, 3)
    view = view_of(tmp_path, path, header_row=2, data_rows=(3, 10))
    assert view.data_rows == (3, 10) and len(view.cells) == 8 * 2
    empty = view_of(tmp_path, path, sheet="빈 시트")
    assert (empty.sheet, empty.header_row, empty.data_rows, empty.cells) == (
        "빈 시트",
        1,
        (2, 1),
        (),
    )
    with pytest.raises(ServiceError) as info:
        view_of(tmp_path, path, sheet="없는 시트")
    assert info.value.code == "INPUT_UNSUPPORTED"
    with pytest.raises(ServiceError):
        view_of(tmp_path, path, header_row=2, data_rows=(1, 3))


def test_formula_merged_and_protected_cells_are_blocked(tmp_path):
    rows = [
        ["코드", "값", "합계", "비고"],
        ["0012", 1, "=B2*2", "병합 왼쪽위"],
        ["0013", 2, None, None],
    ]
    path = write_xlsx(tmp_path / "t.xlsx", rows, merged=("D2:D3",), protect=True)
    cells = by_address(view_of(tmp_path, path))
    assert cells["C2"].blocked == "formula"
    assert cells["D2"].blocked == "protected"  # 병합의 왼쪽 위는 보통 칸이다(보호만 걸린다)
    assert cells["D3"].blocked == "merged"
    assert cells["B2"].blocked == "protected"
    unprotected = by_address(
        view_of(tmp_path, write_xlsx(tmp_path / "u.xlsx", rows, merged=("D2:D3",)))
    )
    assert (unprotected["B2"].blocked, unprotected["D3"].blocked) == ("", "merged")


def test_array_formula_blocks_its_whole_range(tmp_path):
    from openpyxl import load_workbook
    from openpyxl.worksheet.formula import ArrayFormula

    path = write_xlsx(tmp_path / "t.xlsx", [["코드", "값"], ["0012", 1], ["0013", 2]])
    book = load_workbook(path)
    book.active["C2"] = ArrayFormula("C2:C3", "=B2:B3*2")
    book.save(path)
    cells = by_address(view_of(tmp_path, path))
    assert (cells["C2"].blocked, cells["C3"].blocked) == ("formula", "formula")


def test_preview_is_limited_to_preview_rows(tmp_path):
    rows = [["코드"]] + [[f"{i:04d}"] for i in range(80)]
    view = view_of(tmp_path, write_xlsx(tmp_path / "t.xlsx", rows), preview_rows=50)
    assert view.data_rows == (2, 81)
    assert len(view.cells) == 50 and view.cells[-1].address == "A51"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, CellValue()),
        ("", CellValue()),
        ("  ", CellValue()),
        ("0012", CellValue("text", "0012")),
        (3500000, CellValue("number", "3500000")),
        (1.5, CellValue("number", "1.5")),
        (1e20, CellValue("number", "100000000000000000000")),
        (True, CellValue("bool", "TRUE")),
        (dt.datetime(2026, 11, 6), CellValue("date", "2026-11-06")),
        (dt.datetime(2026, 11, 6, 9, 30), CellValue("date", "2026-11-06 09:30:00")),
        (dt.date(2026, 11, 6), CellValue("date", "2026-11-06")),
    ],
)
def test_cell_value_keeps_meaning(raw, expected):
    assert cell_value(raw) == expected


def test_display_groups_digits_and_appends_unit():
    assert CellValue("number", "3500000", "원").display() == "3,500,000원"
    assert CellValue("number", "-1234.5").display() == "-1,234.5"
    assert CellValue("text", "0012").display() == "0012"
    assert CellValue().display() == ""


def test_sheet_limit_is_explained(tmp_path, monkeypatch):
    from ureonggaksi import xlsx

    monkeypatch.setattr(xlsx, "MAX_SHEET_CELLS", 4)
    with pytest.raises(ServiceError) as info:
        view_of(tmp_path, write_xlsx(tmp_path / "t.xlsx"))
    assert info.value.code == "INPUT_LIMIT"


@pytest.mark.skipif(not (FIXTURES / "excel_취합표.xlsx").exists(), reason="Excel 저장본 없음")
def test_excel_saved_workbook_reads_like_the_synthetic_one(tmp_path):
    """이 PC의 Excel로 다시 저장한 합성 파일. 수식의 캐시값이 기존값으로 보이고 막힘은 같다."""
    view = view_of(tmp_path, FIXTURES / "excel_취합표.xlsx")
    cells = by_address(view)
    assert (view.header_row, view.columns[:3]) == (1, ("사업코드", "사업명", "2026 예산"))
    assert cells["A2"].value == CellValue("text", "0012")
    assert cells["C2"].value == CellValue("number", "3500000")
    assert (cells["C3"].blocked, cells["C3"].value) == ("formula", CellValue("number", "7000000"))
    assert cells["D3"].blocked == "merged"
    assert cells["E2"].value == CellValue("date", "2026-11-06")


def test_zip_bomb_and_encrypted_entries_are_refused_before_openpyxl(tmp_path, monkeypatch):
    """압축 해제 폭탄은 openpyxl이 XML을 펼치기 전에 ZIP 항목 크기로 막는다."""
    from zipfile import ZIP_DEFLATED, ZipFile

    from ureonggaksi import xlsx

    path = write_xlsx(tmp_path / "t.xlsx")
    with ZipFile(path, "a", ZIP_DEFLATED) as archive:
        archive.writestr("xl/padding.bin", b"\0" * (2 * 1024 * 1024))  # 2 MiB → 수 KiB
    source = write_hwpx(tmp_path / "a.hwpx")
    with pytest.raises(ServiceError) as info:
        open_inputs([source], path, tmp_path / "out")
    assert info.value.code == "INPUT_LIMIT"

    small = write_xlsx(tmp_path / "s.xlsx")
    monkeypatch.setattr(xlsx, "MAX_UNPACKED_BYTES", 100)
    with pytest.raises(ServiceError) as info:
        open_inputs([source], small, tmp_path / "out")
    assert info.value.code == "INPUT_LIMIT"
