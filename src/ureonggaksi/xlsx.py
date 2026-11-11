"""Read the target XLSX from the in-memory snapshot with openpyxl (api.md 2.3).

Writing is not here yet: the minimal-change patch arrives with the save step (P03).
"""

import datetime as dt
import io
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import iterparse
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
MAX_SHEET_XML_BYTES = 64 * 1024 * 1024
MAX_RANGE_REFS = 10_000  # 시트 하나의 병합·배열 수식 범위 수
HEADER_SEARCH_ROWS = 20
PREVIEW_ROWS = 50

UNSUPPORTED_MESSAGE = "대상 XLSX를 읽을 수 없습니다. 손상·암호화 여부와 형식을 확인하세요."
RANGE_MESSAGE = f"대상 시트의 사용 범위가 {MAX_SHEET_CELLS:,}칸을 초과했습니다."
XML_ENTRY = re.compile(r".*\.xml\Z", re.IGNORECASE)
RANGE_ELEMENTS = {"mergeCell", "f", "dimension"}

Blocked = Literal["", "formula", "merged", "protected"]


@dataclass(frozen=True)
class TargetCell:
    address: str
    value: CellValue
    blocked: Blocked


@dataclass(frozen=True)
class TargetView:
    sheet: str
    sheets: tuple[str, ...]
    header_row: int
    data_rows: tuple[int, int]  # 데이터가 없으면 (header_row + 1, header_row)
    columns: tuple[str, ...]  # 헤더 글자. 비어 있으면 열 문자("C")
    cells: tuple[TargetCell, ...]  # 미리보기 행의 주소·기존값·막힘 여부


def _range_area(ref: str) -> int:
    """`'시트'!A1:B2`의 시트 이름은 떼고 본다. 열·행 전체(`A:Z`, `1:5`)는 한도를 넘는 것으로 친다."""
    try:
        bounds = range_boundaries(ref.rsplit("!", 1)[-1])
    except (ValueError, TypeError) as exc:
        raise ServiceError("INPUT_UNSUPPORTED", UNSUPPORTED_MESSAGE) from exc
    if any(b is None for b in bounds):
        return MAX_SHEET_CELLS + 1
    min_col, min_row, max_col, max_row = bounds
    return (max_row - min_row + 1) * (max_col - min_col + 1)


def _check_sheet_xml(stream) -> None:
    """XML 파서로 시트를 한 번 훑는다(글자 검색은 따옴표·접두사·공백 표기로 우회된다, 재검수 H1').

    openpyxl은 병합·배열 수식의 ref 범위 전체와 모든 <c>에 객체를 만들므로, 그 전에 범위 넓이·범위 수·셀 수를 본다.
    루트가 worksheet가 아닌 XML은 첫 요소만 보고 끝낸다.
    """
    cells = refs = 0
    root_seen = False
    for _event, element in iterparse(
        stream, events=("start",), forbid_dtd=True, forbid_entities=True, forbid_external=True
    ):
        tag = element.tag.rsplit("}", 1)[-1]
        if not root_seen:
            root_seen = True
            if tag != "worksheet":
                return
        if tag == "c":
            cells += 1
            if cells > MAX_SHEET_CELLS:
                raise ServiceError("INPUT_LIMIT", RANGE_MESSAGE)
        elif tag in RANGE_ELEMENTS and element.get("ref"):
            refs += 1
            if refs > MAX_RANGE_REFS:
                raise ServiceError(
                    "INPUT_LIMIT", "대상 시트의 병합·수식 범위 수가 한도를 초과했습니다."
                )
            if _range_area(element.get("ref")) > MAX_SHEET_CELLS:
                raise ServiceError("INPUT_LIMIT", RANGE_MESSAGE)
        element.clear()


def _check_archive(data: bytes) -> None:
    """openpyxl에 넘기기 전에 ZIP 자체를 본다(hwpx.py와 같은 기준): 경로·중복 항목·암호화·압축 폭탄·범위 폭탄."""
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_UNPACKED_BYTES:
                raise ServiceError(
                    "INPUT_LIMIT", "대상 XLSX의 항목 수 또는 해제 크기가 한도를 초과했습니다."
                )
            names: set[str] = set()
            for entry in entries:
                name = entry.filename
                if (
                    name in names
                    or "\\" in name
                    or name.startswith("/")
                    or ":" in name
                    or ".." in PurePosixPath(name).parts
                    or "\x00" in name
                ):
                    raise ServiceError(
                        "INPUT_UNSUPPORTED",
                        "XLSX 안에 안전하지 않은 경로 또는 중복 항목이 있습니다.",
                    )
                names.add(name)
                if entry.flag_bits & 1:
                    raise ServiceError("INPUT_UNSUPPORTED", "암호화된 XLSX는 지원하지 않습니다.")
                if (
                    entry.file_size > 1_048_576
                    and entry.file_size / max(entry.compress_size, 1) > 1000
                ):
                    raise ServiceError("INPUT_LIMIT", "비정상적으로 높은 압축률의 XLSX입니다.")
                if XML_ENTRY.fullmatch(name):
                    # 시트 부품의 이름·위치는 rels가 정하므로 이름으로 고르지 않고 XML 전부를 본다
                    if entry.file_size > MAX_SHEET_XML_BYTES:
                        raise ServiceError("INPUT_LIMIT", "대상 XML이 처리 한도를 초과했습니다.")
                    with archive.open(name) as stream:
                        _check_sheet_xml(stream)
    except (
        BadZipFile,
        OSError,
        ValueError,
        RuntimeError,
        LookupError,
        ParseError,
        DefusedXmlException,
    ) as exc:
        raise ServiceError("INPUT_UNSUPPORTED", UNSUPPORTED_MESSAGE) from exc


def _workbook(data: bytes, *, data_only: bool, read_only: bool = False, checked: bool = False):
    if not checked:
        _check_archive(data)
    try:
        return load_workbook(io.BytesIO(data), data_only=data_only, read_only=read_only)
    except Exception as exc:  # openpyxl은 zipfile·xml·자체 예외를 섞어 던진다
        raise ServiceError("INPUT_UNSUPPORTED", UNSUPPORTED_MESSAGE) from exc


def sheet_names(data: bytes) -> tuple[str, ...]:
    book = _workbook(data, data_only=True, read_only=True)
    try:
        if not book.sheetnames:
            raise ServiceError("INPUT_UNSUPPORTED", "대상 XLSX에 시트가 없습니다.")
        return tuple(book.sheetnames)
    finally:
        book.close()


def _number_text(number: int | float | Decimal) -> str:
    """지수 없는 정규 문자열. int는 자릿수를 그대로 두고(반올림 없음), float·Decimal은 끝의 0만 뗀다."""
    if isinstance(number, int):
        return str(number)
    text = format(Decimal(repr(number)) if isinstance(number, float) else number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def cell_value(raw) -> CellValue:
    """openpyxl이 돌려준 값을 뜻이 고정된 CellValue로 바꾼다. 글자는 해석하지 않는다(그건 ③)."""
    if raw is None:
        return CellValue()
    if isinstance(raw, bool):
        return CellValue("bool", "TRUE" if raw else "FALSE")
    if isinstance(raw, (int, float, Decimal)):
        return CellValue("number", _number_text(raw))
    if isinstance(raw, dt.datetime):
        if (raw.hour, raw.minute, raw.second, raw.microsecond) == (0, 0, 0, 0):
            return CellValue("date", raw.date().isoformat())
        return CellValue("date", raw.isoformat(sep=" ", timespec="seconds"))
    if isinstance(raw, dt.date):
        return CellValue("date", raw.isoformat())
    text = str(raw)  # time·timedelta·오류값("#DIV/0!")은 글자로 둔다(계약에 그 종류가 없다)
    return CellValue("text", text) if text.strip() else CellValue()


def _blank(raw) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _detect_header(values_sheet, max_row: int, max_col: int) -> int:
    """1~20행 가운데, 비지 않은 칸이 가장 많은 행의 절반 이상 채워진 **첫** 행(글자 칸 1개 이상).

    제목 행(칸 1~2개)은 건너뛰고, 헤더에 빈 열이 있어도 꽉 찬 데이터 행에 밀리지 않으며,
    연도처럼 숫자로 된 헤더도 고른다. 틀리면 header_row 인자로 바꾼다.
    """
    rows = range(1, min(max_row, HEADER_SEARCH_ROWS) + 1)
    counts = {}
    for row in rows:
        values = [values_sheet.cell(row, col).value for col in range(1, max_col + 1)]
        filled = [v for v in values if not _blank(v)]
        counts[row] = (len(filled), any(isinstance(v, str) for v in filled))
    widest = max((count for count, _ in counts.values()), default=0)
    for row in rows:
        count, has_text = counts[row]
        if has_text and count * 2 >= widest:
            return row
    return next((row for row in rows if counts[row][0] == widest), 1)


def _last_data_row(values_sheet, header_row: int, max_row: int, max_col: int) -> int:
    for row in range(max_row, header_row, -1):
        if any(not _blank(values_sheet.cell(row, col).value) for col in range(1, max_col + 1)):
            return row
    return header_row


Bounds = tuple[int, int, int, int]  # min_row, min_col, max_row, max_col


def _bounds(ref: str) -> Bounds:
    min_col, min_row, max_col, max_row = range_boundaries(ref)
    return min_row, min_col, max_row, max_col


def _within(bounds: Bounds, row: int, col: int) -> bool:
    return bounds[0] <= row <= bounds[2] and bounds[1] <= col <= bounds[3]


def _blocked(
    formula_sheet, array_ranges, merged_ranges, protected: bool, row: int, col: int
) -> Blocked:
    """범위를 펼치지 않고 칸마다 포함 여부만 본다(큰 ref로 메모리를 채우지 못하게)."""
    source = formula_sheet.cell(row, col)
    if source.data_type == "f" or any(_within(b, row, col) for b in array_ranges):
        return "formula"
    if any(_within(b, row, col) and (row, col) != (b[0], b[1]) for b in merged_ranges):
        return "merged"
    if protected and source.protection.locked:
        return "protected"
    return ""


def inspect_target(
    inputs: "Inputs",
    sheet: str | None = None,
    header_row: int | None = None,
    data_rows: tuple[int, int] | None = None,
    preview_rows: int = PREVIEW_ROWS,
) -> TargetView:
    if preview_rows < 0:
        raise ServiceError("INPUT_UNSUPPORTED", "미리보기 행 수는 0 이상이어야 합니다.")
    data = inputs.snapshot("target")
    _check_archive(data)  # 한 번만 검사하고 두 번 연다
    formulas = _workbook(data, data_only=False, checked=True)
    values = _workbook(data, data_only=True, checked=True)
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
        if first <= header or last < header or last < first - 1:
            raise ServiceError(
                "INPUT_UNSUPPORTED", "데이터 범위는 헤더 행 아래의 올바른 구간이어야 합니다."
            )
        columns = tuple(
            cell_value(values_sheet.cell(header, col).value).value.strip() or get_column_letter(col)
            for col in range(1, max_col + 1)
        )
        array_ranges = [_bounds(ref) for ref in formula_sheet.array_formulae.values()]
        merged_ranges = [
            (m.min_row, m.min_col, m.max_row, m.max_col) for m in formula_sheet.merged_cells.ranges
        ]
        protected = bool(formula_sheet.protection.sheet)
        cells = []
        for row in range(first, min(last, first + preview_rows - 1) + 1):
            for col in range(1, max_col + 1):
                cells.append(
                    TargetCell(
                        f"{get_column_letter(col)}{row}",
                        cell_value(values_sheet.cell(row, col).value),
                        _blocked(formula_sheet, array_ranges, merged_ranges, protected, row, col),
                    )
                )
        return TargetView(name, sheets, header, (first, last), columns, tuple(cells))
    finally:
        formulas.close()
        values.close()
