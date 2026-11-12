"""Read the target XLSX from the in-memory snapshot with openpyxl (api.md 2.3).

Writing is not here yet: the minimal-change patch arrives with the save step (P03).
"""

import datetime as dt
import io
import posixpath
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring, iterparse
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries

from . import isolation
from .errors import ServiceError
from .models import CellValue

if TYPE_CHECKING:
    from .inputs import Inputs

# 한도(OQ-04 제안값, 실측 뒤 조정한다). 시트 사용 범위는 행×열, ZIP 한도는 hwpx.py와 같다.
MAX_SHEET_CELLS = 500_000
MAX_ENTRIES = 2048
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_WORKBOOK_CELLS = 1_000_000  # openpyxl은 통합문서 전체를 읽으므로 시트 합계에도 한도를 둔다
MAX_SHEET_XML_BYTES = 64 * 1024 * 1024
MAX_RANGE_REFS = 10_000  # 시트 하나의 병합·배열 수식 범위 수
HEADER_SEARCH_ROWS = 20
PREVIEW_ROWS = 50

UNSUPPORTED_MESSAGE = "대상 XLSX를 읽을 수 없습니다. 손상·암호화 여부와 형식을 확인하세요."
RANGE_MESSAGE = f"대상 시트의 사용 범위가 {MAX_SHEET_CELLS:,}칸을 초과했습니다."
RANGE_ELEMENTS = {"mergeCell", "f", "dimension"}
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OBJECT_ELEMENTS = {"c", "row", "hyperlink"}
HEAVY_ELEMENTS = {"conditionalFormatting", "dataValidation"}
MAX_HEAVY_ELEMENTS = 20_000
MAX_XML_NODES = 2_000_000  # 항목 하나의 XML 요소 수. 정상 450,000칸 시트가 약 95만 요소다

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
        # `A:Z`·`1:5`(열·행 전체)는 한도 초과. `x` 같은 단일 토큰은 범위가 아니므로 무시한다
        return MAX_SHEET_CELLS + 1 if ":" in ref else 0
    min_col, min_row, max_col, max_row = bounds
    return (max_row - min_row + 1) * (max_col - min_col + 1)


def _check_entry_xml(stream) -> int:
    """ZIP 항목 하나를 XML 파서로 훑어 셀(<c>) 수를 돌려준다. XML이 아니면 0.

    openpyxl은 병합·배열 수식의 ref 범위 전체와 모든 <c>에 객체를 만들므로, 그 전에 범위 넓이·범위 수·셀 수를 본다.
    항목 이름(확장자·위치)이나 루트 이름으로 시트를 고르지 않는다. openpyxl은 rels가 가리키는 항목을 이름과
    상관없이 시트로 읽고 루트 이름도 보지 않기 때문이다(재검수 H1'·H1''). 글자 검색(정규식)도 표기로 우회된다.
    """
    cells = heavy = refs = nodes = merged_area = formula_area = 0
    try:
        for _event, element in iterparse(
            stream, events=("start",), forbid_dtd=True, forbid_entities=True, forbid_external=True
        ):
            nodes += 1
            if nodes > MAX_XML_NODES:
                raise ServiceError(
                    "INPUT_LIMIT", "대상 XLSX 항목의 XML 요소 수가 한도를 초과했습니다."
                )
            tag = element.tag.rsplit("}", 1)[-1]
            if tag in OBJECT_ELEMENTS:
                # openpyxl이 요소마다 객체를 만드는 것들을 셀과 같은 바구니로 센다(<c> 없는 <row> 홍수, 재검수 M8)
                cells += 1
                if cells > MAX_SHEET_CELLS:
                    raise ServiceError("INPUT_LIMIT", RANGE_MESSAGE)
            elif tag in HEAVY_ELEMENTS:
                # 요소당 비용이 셀의 몇 배(자식 객체·범위 해석)라 따로 센다(5차 Y4: 20만 개 → 103초·568 MiB)
                heavy += 1
                if heavy > MAX_HEAVY_ELEMENTS:
                    raise ServiceError(
                        "INPUT_LIMIT",
                        "대상 시트의 조건부 서식·데이터 유효성 수가 한도를 초과했습니다.",
                    )
            if tag in RANGE_ELEMENTS and element.get("ref"):
                refs += 1
                if refs > MAX_RANGE_REFS:
                    raise ServiceError(
                        "INPUT_LIMIT", "대상 시트의 병합·수식 범위 수가 한도를 초과했습니다."
                    )
                area = _range_area(element.get("ref"))
                if tag == "mergeCell":
                    # 겹쳐도 openpyxl은 각각 MergedCell을 만든다 → 합계로 묶는다
                    merged_area += area
                    area = merged_area
                elif tag == "f":
                    # 배열·공유 수식 범위도 합계로 묶는다. 미리보기가 이 범위를 펼쳐 칸 조회를 하므로(2차 검수 N4)
                    formula_area += area
                    area = formula_area
                if area > MAX_SHEET_CELLS:
                    raise ServiceError("INPUT_LIMIT", RANGE_MESSAGE)
            element.clear()
    except ParseError:
        return 0  # 그림·글꼴 같은 XML이 아닌 항목. openpyxl도 XML로 읽지 않는다
    return cells


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
            total_cells = 0
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
                if entry.file_size > MAX_SHEET_XML_BYTES:
                    raise ServiceError("INPUT_LIMIT", "대상 XLSX 항목이 처리 한도를 초과했습니다.")
                with archive.open(name) as stream:
                    total_cells += _check_entry_xml(stream)
                if total_cells > MAX_WORKBOOK_CELLS:
                    raise ServiceError(
                        "INPUT_LIMIT",
                        f"통합문서 전체 셀 수가 {MAX_WORKBOOK_CELLS:,}칸을 초과했습니다.",
                    )
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
    except MemoryError:
        raise  # 작업자의 메모리 상한 → isolation이 INPUT_LIMIT로 바꾼다
    except Exception as exc:  # openpyxl은 zipfile·xml·자체 예외를 섞어 던진다
        raise ServiceError("INPUT_UNSUPPORTED", UNSUPPORTED_MESSAGE) from exc


def sheet_names(data: bytes) -> tuple[str, ...]:
    """사전 검사 뒤 통합문서의 시트 목록을 openpyxl 없이 읽는다(작업자 상한이 필요 없게).

    _rels/.rels가 가리키는 통합문서 부품의 {main}sheets/{main}sheet만 본다. 각 시트의 r:id가
    통합문서 rels를 거쳐 실제 부품을 가리켜야 한다(끊긴 시트는 손상으로 거부, 1차 검수 H1).
    """
    _check_archive(data)
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            workbook = _office_document(archive)
            root = _parse(archive.read(workbook))
            rels_path = posixpath.join(
                posixpath.dirname(workbook), "_rels", posixpath.basename(workbook) + ".rels"
            )
            targets = {}
            for node in _parse(archive.read(rels_path)):
                if node.tag == f"{{{PKG_REL_NS}}}Relationship":
                    target = node.get("Target", "")
                    targets[node.get("Id")] = (
                        target.lstrip("/")
                        if target.startswith("/")
                        else posixpath.normpath(posixpath.join(posixpath.dirname(workbook), target))
                    )
    except (KeyError, BadZipFile, OSError, ValueError, ParseError, DefusedXmlException) as exc:
        raise ServiceError("INPUT_UNSUPPORTED", UNSUPPORTED_MESSAGE) from exc
    sheets = root.find(f"{{{MAIN_NS}}}sheets")
    result = []
    for node in sheets if sheets is not None else ():
        if node.tag != f"{{{MAIN_NS}}}sheet":
            continue
        name, rid = node.get("name", ""), node.get(f"{{{REL_NS}}}id")
        if not name or targets.get(rid) not in names:
            raise ServiceError("INPUT_UNSUPPORTED", "대상 XLSX의 시트 연결이 끊겨 있습니다.")
        result.append(name)
    if not result:
        raise ServiceError("INPUT_UNSUPPORTED", "대상 XLSX에 시트가 없습니다.")
    return tuple(result)


def _parse(xml: bytes):
    return fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)


def _office_document(archive: ZipFile) -> str:
    """패키지 rels(_rels/.rels)가 가리키는 통합문서 부품 경로. 없으면 xl/workbook.xml."""
    try:
        rels = _parse(archive.read("_rels/.rels"))
    except KeyError:
        return "xl/workbook.xml"
    for node in rels:
        if node.get("Type", "").endswith("/officeDocument"):
            return node.get("Target", "").lstrip("/")
    return "xl/workbook.xml"


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
    min_col, min_row, max_col, max_row = range_boundaries(ref.rsplit("!", 1)[-1])
    return min_row, min_col, max_row, max_col


def _blocked(
    formula_sheet, array_ranges, merged_followers: set, protected: bool, row: int, col: int
) -> Blocked:
    """배열 수식 범위는 포함 여부만 보고(보통 몇 개), 병합은 미리 펼친 집합을 본다.

    병합 면적 합계는 사전 검사가 500,000칸으로 묶으므로 펼쳐도 메모리가 안전하다(검수 M6: 범위 3,000개 ×
    칸 50,000개를 선형 탐색하면 30초가 걸렸다).
    """
    source = formula_sheet.cell(row, col)
    if source.data_type == "f" or (row, col) in array_ranges:
        return "formula"
    if (row, col) in merged_followers:
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
    _check_archive(
        data
    )  # 알려진 패턴은 여기서(부모, 싸다). 모르는 패턴은 작업자의 시간·메모리 상한이 막는다
    return isolation.run(
        _inspect, data, sheet, header_row, data_rows, preview_rows, MAX_SHEET_CELLS
    )


def _inspect(data, sheet, header_row, data_rows, preview_rows, max_sheet_cells) -> TargetView:
    """작업자 프로세스에서 돈다. 한도 값은 인자로 받는다(작업자는 부모의 모듈 상태를 보지 않는다)."""
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
        if max_row * max_col > max_sheet_cells:
            raise ServiceError(
                "INPUT_LIMIT", f"대상 시트의 사용 범위가 {max_sheet_cells:,}칸을 초과했습니다."
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
        # 배열 수식 범위를 칸 집합으로 펼친다. 면적 합계는 사전 검사가 500,000칸으로 묶는다(2차 검수 N4)
        array_ranges = {
            (r, c)
            for b in (_bounds(ref) for ref in formula_sheet.array_formulae.values())
            for r in range(b[0], b[2] + 1)
            for c in range(b[1], b[3] + 1)
        }
        merged_followers = {
            (r, c)
            for m in formula_sheet.merged_cells.ranges
            for r in range(m.min_row, m.max_row + 1)
            for c in range(m.min_col, m.max_col + 1)
            if (r, c) != (m.min_row, m.min_col)
        }
        protected = bool(formula_sheet.protection.sheet)
        cells = []
        for row in range(first, min(last, first + preview_rows - 1) + 1):
            for col in range(1, max_col + 1):
                cells.append(
                    TargetCell(
                        f"{get_column_letter(col)}{row}",
                        cell_value(values_sheet.cell(row, col).value),
                        _blocked(
                            formula_sheet, array_ranges, merged_followers, protected, row, col
                        ),
                    )
                )
        return TargetView(name, sheets, header, (first, last), columns, tuple(cells))
    finally:
        formulas.close()
        values.close()
