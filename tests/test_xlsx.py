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


def test_huge_merge_or_array_ref_is_refused_before_openpyxl_expands_it(tmp_path):
    """5 KB 파일의 ref="A1:XFD1048576" 하나로 수백만 객체가 생기지 않게 로드 전에 막는다."""
    import time
    from zipfile import ZIP_DEFLATED, ZipFile

    seed = write_xlsx(tmp_path / "seed.xlsx")
    for tag, replacement in [
        (
            "merge",
            b'<mergeCells count="1"><mergeCell ref="A1:XFD1048576"/></mergeCells></worksheet>',
        ),
        (
            "array",
            b'<sheetData><row r="1"><c r="A1"><f t="array" ref="A1:XFD1048576">1</f></c></row></sheetData></worksheet>',
        ),
    ]:
        bomb = tmp_path / f"{tag}.xlsx"
        with ZipFile(seed) as src, ZipFile(bomb, "w", ZIP_DEFLATED) as dst:
            for item in src.infolist():
                payload = src.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    if tag == "merge":
                        payload = payload.replace(b"</worksheet>", replacement)
                    else:
                        start = payload.index(b"<sheetData")
                        payload = payload[:start] + replacement
                dst.writestr(item, payload)
        source = write_hwpx(tmp_path / f"{tag}.hwpx")
        began = time.monotonic()
        with pytest.raises(ServiceError) as info:
            open_inputs([source], bomb, tmp_path / "out")
        assert info.value.code == "INPUT_LIMIT"
        assert time.monotonic() - began < 2


def _rezip(seed, out, transform):
    """seed XLSX의 항목을 transform(name, bytes) -> (name, bytes)로 바꿔 새 ZIP을 만든다."""
    from zipfile import ZIP_DEFLATED, ZipFile

    with ZipFile(seed) as src, ZipFile(out, "w", ZIP_DEFLATED) as dst:
        for item in src.infolist():
            name, payload = transform(item.filename, src.read(item.filename))
            dst.writestr(name, payload)
    return out


BIG = b"A1:Z200000"


@pytest.mark.parametrize(
    "case",
    [
        "single_quote",
        "spaced_equals",
        "ns_prefix",
        "renamed_part",
        "sheet_qualified",
        "whole_columns",
    ],
)
def test_range_bomb_spellings_are_all_refused_quickly(tmp_path, case):
    """글자 검색이 아니라 XML 파서로 보므로 따옴표·공백·접두사·부품 이름·시트 한정·열 전체 표기를 모두 잡는다."""
    import time

    def merge(xml: bytes, ref_attr: bytes, prefix: bytes = b"") -> bytes:
        tail = (
            b"<"
            + prefix
            + b"mergeCells><"
            + prefix
            + b"mergeCell "
            + ref_attr
            + b"/></"
            + prefix
            + b"mergeCells>"
        )
        return xml.replace(b"</worksheet>", tail + b"</worksheet>")

    def transform(name, payload):
        if name == "xl/worksheets/sheet1.xml":
            if case == "single_quote":
                payload = merge(payload, b"ref='" + BIG + b"'")
            elif case == "spaced_equals":
                payload = merge(payload, b'ref = "' + BIG + b'"')
            elif case == "ns_prefix":
                payload = payload.replace(
                    b"<worksheet ",
                    b'<worksheet xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ',
                    1,
                )
                payload = merge(payload, b'ref="' + BIG + b'"', prefix=b"x:")
            elif case == "sheet_qualified":
                payload = merge(
                    payload, b"ref=\"'" + "아주 긴 시트 제목 ".encode() * 6 + b"'!" + BIG + b'"'
                )
            elif case == "whole_columns":
                payload = merge(payload, b'ref="A:Z"')
            elif case == "renamed_part":
                payload = merge(payload, b'ref="' + BIG + b'"')
                name = "xl/worksheets/data1.xml"
        if case == "renamed_part" and name in ("xl/_rels/workbook.xml.rels", "[Content_Types].xml"):
            payload = payload.replace(b"worksheets/sheet1.xml", b"worksheets/data1.xml")
        return name, payload

    bomb = _rezip(write_xlsx(tmp_path / "seed.xlsx"), tmp_path / f"{case}.xlsx", transform)
    source = write_hwpx(tmp_path / "a.hwpx")
    began = time.monotonic()
    with pytest.raises(ServiceError) as info:
        open_inputs([source], bomb, tmp_path / "out")
    assert info.value.code == "INPUT_LIMIT"
    assert time.monotonic() - began < 2


def test_many_small_cells_are_counted_before_loading(tmp_path, monkeypatch):
    from ureonggaksi import xlsx

    rows = [["코드"]] + [[f"{i:04d}"] for i in range(30)]
    path = write_xlsx(tmp_path / "t.xlsx", rows)
    monkeypatch.setattr(xlsx, "MAX_SHEET_CELLS", 20)
    source = write_hwpx(tmp_path / "a.hwpx")
    with pytest.raises(ServiceError) as info:
        open_inputs([source], path, tmp_path / "out")
    assert info.value.code == "INPUT_LIMIT"


def test_numeric_year_headers_below_a_title_row_are_detected(tmp_path):
    rows = [
        ["2026년 취합표", None, None, None],
        ["사업명", 2024, 2025, 2026],
        ["도서관", 1, 2, 3],
        ["체육관", 4, 5, 6],
    ]
    view = view_of(tmp_path, write_xlsx(tmp_path / "t.xlsx", rows))
    assert (view.header_row, view.columns) == (2, ("사업명", "2024", "2025", "2026"))


def test_duplicate_zip_entries_are_refused(tmp_path):
    from zipfile import ZIP_DEFLATED, ZipFile

    seed = write_xlsx(tmp_path / "seed.xlsx")
    twin = tmp_path / "twin.xlsx"
    with ZipFile(seed) as src, ZipFile(twin, "w", ZIP_DEFLATED) as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        with pytest.warns(UserWarning, match="Duplicate name"):
            dst.writestr("xl/worksheets/sheet1.xml", src.read("xl/worksheets/sheet1.xml"))
    source = write_hwpx(tmp_path / "a.hwpx")
    with pytest.raises(ServiceError) as info:
        open_inputs([source], twin, tmp_path / "out")
    assert info.value.code == "INPUT_UNSUPPORTED"


def test_big_integers_are_not_rounded():
    assert cell_value(12345678901234567890123456789012) == CellValue(
        "number", "12345678901234567890123456789012"
    )
    assert cell_value(0.1 + 0.2) == CellValue("number", "0.30000000000000004")
    assert cell_value(-5) == CellValue("number", "-5")
    assert cell_value(dt.time(9, 30)) == CellValue("text", "09:30:00")


def test_header_with_blank_cells_still_wins_over_a_full_data_row(tmp_path):
    rows = [["사업코드", "사업명", None, "예산"], ["0012", "도서관", "비고1", 1]]
    view = view_of(tmp_path, write_xlsx(tmp_path / "t.xlsx", rows))
    assert (view.header_row, view.columns) == (1, ("사업코드", "사업명", "C", "예산"))


def test_unlocked_cell_on_a_protected_sheet_is_writable(tmp_path):
    from openpyxl import load_workbook
    from openpyxl.styles import Protection

    path = write_xlsx(tmp_path / "t.xlsx", protect=True)
    book = load_workbook(path)
    book.active["C2"].protection = Protection(locked=False)
    book.save(path)
    cells = by_address(view_of(tmp_path, path))
    assert (cells["C2"].blocked, cells["B2"].blocked) == ("", "protected")


def test_reversed_data_rows_and_negative_preview_are_rejected(tmp_path, target):
    with pytest.raises(ServiceError):
        view_of(tmp_path, target, header_row=1, data_rows=(5, 3))
    with pytest.raises(ServiceError):
        view_of(tmp_path, target, preview_rows=-1)
    assert view_of(tmp_path, target, preview_rows=0).cells == ()


def test_date_header_is_shown_as_a_date(tmp_path):
    rows = [["코드", dt.date(2026, 1, 1)], ["0012", 1]]
    view = view_of(tmp_path, write_xlsx(tmp_path / "t.xlsx", rows))
    assert view.columns == ("코드", "2026-01-01")


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
