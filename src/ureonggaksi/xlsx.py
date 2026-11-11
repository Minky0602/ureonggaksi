"""Read the target XLSX from the in-memory snapshot with openpyxl (api.md 2.3).

Writing is not here yet: the minimal-change patch arrives with the save step (P03).
"""

import datetime as dt
import io
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries

from .errors import ServiceError
from .models import CellValue

if TYPE_CHECKING:
    from .inputs import Inputs

# 한도(OQ-04 제안값, 실측 뒤 조정한다). 시트 사용 범위는 행×열, ZIP 한도는 hwpx.py와 같다.
MAX_SHEET_CELLS = 500_000
MAX_ENTRIES = 2048
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
HEADER_SEARCH_ROWS = 20
PREVIEW_ROWS = 50


@dataclass(frozen=True)
class TargetCell:
    address: str
    value: CellValue
    blocked: str  # "" · "formula" · "merged" · "protected"


@dataclass(frozen=True)
class TargetView:
    sheet: str
    sheets: tuple[str, ...]
    header_row: int
    data_rows: tuple[int, int]  # 데이터가 없으면 (header_row + 1, header_row)
    columns: tuple[str, ...]  # 헤더 글자. 비어 있으면 열 문자("C")
    cells: tuple[TargetCell, ...]  # 미리보기 행의 주소·기존값·막힘 여부


def _check_archive(data: bytes) -> None:
    """openpyxl에 넘기기 전에 ZIP 자체를 본다. 압축 해제 폭탄·암호화·항목 수를 한도로 막는다(hwpx.py와 같은 기준)."""
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except (BadZipFile, OSError, ValueError) as exc:
        raise ServiceError(
            "INPUT_UNSUPPORTED",
            "대상 XLSX를 읽을 수 없습니다. 손상·암호화 여부와 형식을 확인하세요.",
        ) from exc
    if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_UNPACKED_BYTES:
        raise ServiceError(
            "INPUT_LIMIT", "대상 XLSX의 항목 수 또는 해제 크기가 한도를 초과했습니다."
        )
    for entry in entries:
        if entry.flag_bits & 1:
            raise ServiceError("INPUT_UNSUPPORTED", "암호화된 XLSX는 지원하지 않습니다.")
        if entry.file_size > 1_048_576 and entry.file_size / max(entry.compress_size, 1) > 1000:
            raise ServiceError("INPUT_LIMIT", "비정상적으로 높은 압축률의 XLSX입니다.")


def _workbook(data: bytes, *, data_only: bool):
    _check_archive(data)
    try:
        return load_workbook(io.BytesIO(data), data_only=data_only)
    except Exception as exc:  # openpyxl은 zipfile·xml·자체 예외를 섞어 던진다
        raise ServiceError(
            "INPUT_UNSUPPORTED",
            "대상 XLSX를 읽을 수 없습니다. 손상·암호화 여부와 형식을 확인하세요.",
        ) from exc


def sheet_names(data: bytes) -> tuple[str, ...]:
    book = _workbook(data, data_only=True)
    try:
        if not book.sheetnames:
            raise ServiceError("INPUT_UNSUPPORTED", "대상 XLSX에 시트가 없습니다.")
        return tuple(book.sheetnames)
    finally:
        book.close()


def _decimal_text(number: float | int | Decimal) -> str:
    text = format(Decimal(str(number)).normalize(), "f")
    return text if text != "-0" else "0"


def cell_value(raw) -> CellValue:
    """openpyxl이 돌려준 값을 뜻이 고정된 CellValue로 바꾼다. 글자는 해석하지 않는다(그건 ③)."""
    if raw is None:
        return CellValue()
    if isinstance(raw, bool):
        return CellValue("bool", "TRUE" if raw else "FALSE")
    if isinstance(raw, (int, float, Decimal)):
        return CellValue("number", _decimal_text(raw))
    if isinstance(raw, dt.datetime):
        if (raw.hour, raw.minute, raw.second, raw.microsecond) == (0, 0, 0, 0):
            return CellValue("date", raw.date().isoformat())
        return CellValue("date", raw.isoformat(sep=" ", timespec="seconds"))
    if isinstance(raw, dt.date):
        return CellValue("date", raw.isoformat())
    text = str(raw)
    return CellValue("text", text) if text.strip() else CellValue()


def _blank(raw) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _detect_header(values_sheet, max_row: int, max_col: int) -> int:
    best_row, best_count = 1, -1
    for row in range(1, min(max_row, HEADER_SEARCH_ROWS) + 1):
        count = sum(
            1 for col in range(1, max_col + 1) if not _blank(values_sheet.cell(row, col).value)
        )
        if count > best_count:
            best_row, best_count = row, count
    return best_row


def _last_data_row(values_sheet, header_row: int, max_row: int, max_col: int) -> int:
    for row in range(max_row, header_row, -1):
        if any(not _blank(values_sheet.cell(row, col).value) for col in range(1, max_col + 1)):
            return row
    return header_row


def _blocked_sets(formula_sheet) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    formula = set()
    for ref in formula_sheet.array_formulae.values():
        rows, cols = _range_coords(ref)
        formula.update((r, c) for r in rows for c in cols)
    merged_followers = set()
    for merged in formula_sheet.merged_cells.ranges:
        for row in range(merged.min_row, merged.max_row + 1):
            for col in range(merged.min_col, merged.max_col + 1):
                if (row, col) != (merged.min_row, merged.min_col):
                    merged_followers.add((row, col))
    return formula, merged_followers


def _range_coords(ref: str):
    min_col, min_row, max_col, max_row = range_boundaries(ref)
    return range(min_row, max_row + 1), range(min_col, max_col + 1)


def inspect_target(
    inputs: "Inputs",
    sheet: str | None = None,
    header_row: int | None = None,
    data_rows: tuple[int, int] | None = None,
    preview_rows: int = PREVIEW_ROWS,
) -> TargetView:
    data = inputs.snapshot("target")
    formulas = _workbook(data, data_only=False)
    values = _workbook(data, data_only=True)
    try:
        sheets = tuple(formulas.sheetnames)
        name = sheet if sheet is not None else formulas.active.title
        if name not in sheets:
            raise ServiceError(
                "INPUT_UNSUPPORTED", "대상 XLSX에 그런 시트가 없습니다.", (("sheet", name),)
            )
        formula_sheet, values_sheet = formulas[name], values[name]
        max_row, max_col = formula_sheet.max_row, formula_sheet.max_column
        if max_row * max_col > MAX_SHEET_CELLS:
            raise ServiceError(
                "INPUT_LIMIT", f"대상 시트의 사용 범위가 {MAX_SHEET_CELLS:,}칸을 초과했습니다."
            )
        header = (
            header_row if header_row is not None else _detect_header(values_sheet, max_row, max_col)
        )
        if not 1 <= header <= max(max_row, 1):
            raise ServiceError("INPUT_UNSUPPORTED", "헤더 행이 시트 범위를 벗어났습니다.")
        if data_rows is None:
            data_rows = (header + 1, _last_data_row(values_sheet, header, max_row, max_col))
        first, last = data_rows
        if first <= header or last < header:
            raise ServiceError("INPUT_UNSUPPORTED", "데이터 범위는 헤더 행 아래여야 합니다.")
        columns = tuple(
            str(values_sheet.cell(header, col).value).strip()
            if not _blank(values_sheet.cell(header, col).value)
            else get_column_letter(col)
            for col in range(1, max_col + 1)
        )
        array_cells, merged_followers = _blocked_sets(formula_sheet)
        protected = bool(formula_sheet.protection.sheet)
        cells = []
        for row in range(first, min(last, first + preview_rows - 1) + 1):
            for col in range(1, max_col + 1):
                source = formula_sheet.cell(row, col)
                if source.data_type == "f" or (row, col) in array_cells:
                    blocked = "formula"
                elif (row, col) in merged_followers:
                    blocked = "merged"
                elif protected and source.protection.locked:
                    blocked = "protected"
                else:
                    blocked = ""
                cells.append(
                    TargetCell(
                        f"{get_column_letter(col)}{row}",
                        cell_value(values_sheet.cell(row, col).value),
                        blocked,
                    )
                )
        return TargetView(name, sheets, header, (first, last), columns, tuple(cells))
    finally:
        formulas.close()
        values.close()
