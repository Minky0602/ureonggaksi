"""Minimal-change XLSX writer and package comparison (PRD FR-15·16, api.md 3절, DIF-012).

Only the target <c> elements of one worksheet change. Every other ZIP entry keeps its exact
content; the worksheet XML keeps every byte outside the written cells (positions come from expat,
the bytes in between are copied, nothing is re-serialised). workbook.xml gets fullCalcOnLoad="1"
when the workbook has formulas. The comparison then proves that nothing else changed.
"""

import datetime as dt
import io
import posixpath
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.parsers import expat
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZipFile, ZipInfo

from defusedxml.ElementTree import fromstring
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string

from .models import CellValue

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_TEXT = 32_767  # Excel 칸 글자 한도
MAX_DIGITS = 15  # Excel 숫자 유효 자릿수. 넘으면 Excel이 끝자리를 바꾼다
DATE_FORMAT_IDS = {14, 15, 16, 17, 22}  # 18~21·45~47은 시각 전용이라 날짜로 보지 않는다
ADDRESS = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}\Z")
NUMBER_TEXT = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?\Z")
X_ESCAPE = re.compile(r"_(x[0-9A-Fa-f]{4}_)")
ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
# calcPr는 CT_Workbook 순서상 이 요소들보다 앞에 온다(없을 때 끼울 자리)
AFTER_CALC_PR = (
    "oleSize",
    "customWorkbookViews",
    "pivotCaches",
    "smartTagPr",
    "smartTagTypes",
    "webPublishing",
    "fileRecoveryPr",
    "webPublishObjects",
    "extLst",
)


class PatchError(ValueError):
    """쓰기 전에 거부하는 경우. 원본·결과 파일은 만들지 않는다."""


@dataclass(frozen=True)
class Check:  # api 2.7 Check
    scope: str  # cell · file · preserve · inputs
    change_id: str | None
    expected: CellValue | None
    actual: CellValue | None
    passed: bool
    message: str


# ---------- 부품 찾기 ----------


def _xml(data: bytes):
    return fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def sheet_part(archive: ZipFile, sheet: str) -> str:
    """시트 이름 → ZIP 안 시트 XML 경로(workbook.xml + rels)."""
    workbook = _xml(archive.read("xl/workbook.xml"))
    rid = None
    for node in workbook.iter():
        if _local(node.tag) == "sheet" and node.get("name") == sheet:
            rid = node.get(f"{{{REL_NS}}}id")
    if rid is None:
        raise PatchError("대상 시트를 통합문서에서 찾지 못했습니다.")
    rels = _xml(archive.read("xl/_rels/workbook.xml.rels"))
    for node in rels.iter():
        if _local(node.tag) == "Relationship" and node.get("Id") == rid:
            target = node.get("Target", "")
            path = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join("xl", target))
            )
            if path not in archive.namelist():
                raise PatchError("대상 시트 부품이 패키지에 없습니다.")
            return path
    raise PatchError("대상 시트의 연결 정보를 찾지 못했습니다.")


def _date1904(archive: ZipFile) -> bool:
    for node in _xml(archive.read("xl/workbook.xml")).iter():
        if _local(node.tag) == "workbookPr":
            return node.get("date1904") in ("1", "true")
    return False


def _styles(archive: ZipFile) -> tuple[list[bool], list[bool]]:
    """cellXfs 순서대로 (날짜 서식인가, 잠금 해제인가)."""
    if "xl/styles.xml" not in archive.namelist():
        return [False], [False]
    root = _xml(archive.read("xl/styles.xml"))
    custom_dates = set()
    for node in root.iter():
        if _local(node.tag) == "numFmt":
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", node.get("formatCode", "")).lower()
            if re.search(r"[yd]", code):  # m은 분일 수 있어 y·d가 있어야 날짜
                custom_dates.add(int(node.get("numFmtId", "-1")))
    dates, unlocked = [], []
    for container in root.iter():
        if _local(container.tag) != "cellXfs":
            continue
        for xf in container:
            if _local(xf.tag) != "xf":
                continue
            fmt = int(xf.get("numFmtId", "0"))
            dates.append(fmt in DATE_FORMAT_IDS or fmt in custom_dates)
            protection = next((p for p in xf if _local(p.tag) == "protection"), None)
            unlocked.append(protection is not None and protection.get("locked") in ("0", "false"))
    return dates or [False], unlocked or [False]


# ---------- 시트 XML 위치 지도 ----------


def _tag_end(data: bytes, start: int) -> int:
    """data[start]의 '<'로 시작하는 태그가 끝나는 '>' 다음 위치. 속성 값 안의 '>'는 건너뛴다."""
    quote = None
    for index in range(start + 1, len(data)):
        byte = data[index]
        if quote:
            if byte == quote:
                quote = None
        elif byte in (0x22, 0x27):
            quote = byte
        elif byte == 0x3E:
            return index + 1
    raise PatchError("시트 XML이 올바르지 않습니다.")


@dataclass
class _Element:
    name: str  # 접두사 포함 원래 이름
    attrs: dict
    start: int  # '<' 위치
    start_end: int  # 시작 태그 '>' 다음
    end: int = -1  # 요소 끝(닫는 태그 '>' 다음)
    self_closing: bool = False
    children: list = None


def _scan(data: bytes) -> dict:
    """sheetData·row·c·dimension·mergeCell·sheetProtection의 바이트 위치를 모은다."""
    head = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    declared = re.match(rb"<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']+)[\"']", head)
    if not head.startswith(b"<") or (
        declared and declared.group(1).lower() not in (b"utf-8", b"utf8")
    ):
        raise PatchError("UTF-8이 아닌 시트 XML은 쓰지 않습니다.")
    parser = expat.ParserCreate()  # 이름공간 처리 없이 원래 이름(접두사 포함)을 본다
    found = {
        "sheetData": None,
        "rows": [],
        "cells": {},
        "dimension": None,
        "merges": [],
        "protected": False,
        "formula_cells": set(),
        "array_refs": [],
        "cols": [],  # (min, max, style)
    }
    stack: list[_Element] = []

    def doctype(*_args):
        raise PatchError("DOCTYPE이 있는 시트 XML은 쓰지 않습니다.")

    def start(name, attrs):
        begin = parser.CurrentByteIndex
        element = _Element(name, attrs, begin, _tag_end(data, begin), children=[])
        local = name.rsplit(":", 1)[-1]
        if local in ("sheetData", "row", "c", "dimension", "f"):
            if local == "sheetData":
                found["sheetData"] = element
            elif local == "row":
                found["rows"].append(element)
            elif local == "c":
                address = attrs.get("r")
                if not address:
                    raise PatchError("주소(r)가 없는 칸이 있는 시트는 쓰지 않습니다.")
                if address in found["cells"]:
                    raise PatchError("같은 주소의 칸이 둘 있는 시트는 쓰지 않습니다.")
                found["cells"][address] = element
            elif local == "dimension":
                found["dimension"] = element
            elif local == "f" and stack and stack[-1].name.rsplit(":", 1)[-1] == "c":
                found["formula_cells"].add(stack[-1].attrs.get("r"))
                if attrs.get("t") == "array" and attrs.get("ref"):
                    found["array_refs"].append(attrs["ref"])
        if local == "col" and attrs.get("min") and attrs.get("style"):
            low = int(attrs["min"])
            found["cols"].append((low, int(attrs.get("max", low)), attrs["style"]))
        if local == "mergeCell" and attrs.get("ref"):
            found["merges"].append(attrs["ref"])
        if local == "sheetProtection" and attrs.get("sheet") in ("1", "true"):
            found["protected"] = True
        stack.append(element)

    def end(name):
        element = stack.pop()
        # <c .../> 같은 빈 요소는 시작 태그가 '/>'로 끝난다. expat의 위치 값으로 판별하지 않는다
        if data[element.start_end - 2 : element.start_end] == b"/>":
            element.self_closing = True
            element.end = element.start_end
        else:
            element.end = _tag_end(data, parser.CurrentByteIndex)

    parser.StartDoctypeDeclHandler = doctype
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise PatchError("시트 XML을 읽을 수 없습니다.") from exc
    if found["sheetData"] is None:
        raise PatchError("시트에 sheetData가 없습니다.")
    for row in found["rows"]:
        if not row.attrs.get("r"):
            raise PatchError("행 번호(r)가 없는 행이 있는 시트는 쓰지 않습니다.")
    return found


def _prefix(name: str) -> str:
    return name.rsplit(":", 1)[0] + ":" if ":" in name else ""


def _split(address: str) -> tuple[int, int]:
    letters, row = coordinate_from_string(address)
    return row, column_index_from_string(letters)


# ---------- 칸 글자 만들기 ----------


def _serial(value: str, date1904: bool) -> str:
    try:
        day = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PatchError(f"날짜 값이 올바르지 않습니다: {value}") from exc
    if date1904:
        base = dt.date(1904, 1, 1)
    else:
        if day < dt.date(1900, 3, 1):
            raise PatchError("1900년 3월 1일 이전 날짜는 쓰지 않습니다(Excel 1900 윤년 버그).")
        base = dt.date(1899, 12, 30)
    serial = (day - base).days
    if serial < 0:
        raise PatchError("통합문서 날짜 기준보다 이른 날짜입니다.")
    return str(serial)


def _cell_xml(
    prefix: str, address: str, style: str | None, value: CellValue, date1904: bool
) -> bytes:
    s_attr = f" s={quoteattr(style)}" if style is not None else ""
    head = f"<{prefix}c r={quoteattr(address)}{s_attr}"
    if value.kind == "number":
        if not NUMBER_TEXT.match(value.value):  # NaN·Infinity·지수·밑줄은 쓰지 않는다
            raise PatchError(f"{address}: 숫자 표기가 올바르지 않습니다.")
        try:
            number = Decimal(value.value)
        except InvalidOperation as exc:
            raise PatchError(f"숫자 값이 올바르지 않습니다: {address}") from exc
        if len(number.normalize().as_tuple().digits) > MAX_DIGITS:
            raise PatchError(
                f"{address}: 15자리를 넘는 숫자는 Excel이 끝자리를 바꿔 쓰지 않습니다."
            )
        body = f"><{prefix}v>{value.value}</{prefix}v></{prefix}c>"
    elif value.kind == "date":
        body = f"><{prefix}v>{_serial(value.value, date1904)}</{prefix}v></{prefix}c>"
    elif value.kind == "bool":
        if value.value not in ("TRUE", "FALSE"):
            raise PatchError(f"{address}: 참/거짓 값이 올바르지 않습니다.")
        body = f' t="b"><{prefix}v>{1 if value.value == "TRUE" else 0}</{prefix}v></{prefix}c>'
    elif value.kind == "text":
        if (
            len(value.value) > MAX_TEXT
            or ILLEGAL_XML.search(value.value)
            or "\r" in value.value  # XML이 \n으로 바꿔 다른 값이 된다
            or not value.value.strip()
        ):
            raise PatchError(f"{address}: Excel 칸에 쓸 수 없는 글자이거나 너무 깁니다.")
        # 인라인 문자열: 공유 문자열 표를 건드리지 않고, '='로 시작해도 수식이 되지 않는다.
        # "_x0041_" 같은 글자는 Excel이 문자 코드로 풀므로 ECMA-376 규칙대로 "_x005F_"로 감싼다(검수 H2)
        body = (
            f' t="inlineStr"><{prefix}is><{prefix}t xml:space="preserve">{escape(escape_x(value.value))}'
            f"</{prefix}t></{prefix}is></{prefix}c>"
        )
    else:
        raise PatchError(f"{address}: 빈 값은 쓰지 않습니다.")
    return (head + body).encode("utf-8")


def escape_x(text: str) -> str:
    """ECMA-376 문자 이스케이프: 글자 그대로의 "_xHHHH_"를 "_x005F_xHHHH_"로."""
    return X_ESCAPE.sub(r"_x005F_\1", text)


# ---------- 쓰기 ----------


def patch_xlsx(data: bytes, sheet: str, writes: dict[str, CellValue]) -> bytes:
    """writes(주소 → 값)만 바꾼 새 XLSX 바이트. 막힌 칸·서명 파일·서식이 맞지 않는 날짜는 PatchError."""
    if not writes:
        raise PatchError("쓸 칸이 없습니다.")
    with ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if any(n.startswith("_xmlsignatures/") for n in names):
            raise PatchError("전자서명이 있는 통합문서는 쓰지 않습니다(서명이 깨진다).")
        part = sheet_part(archive, sheet)
        sheet_xml = archive.read(part)
        workbook_xml = archive.read("xl/workbook.xml")
        date1904 = _date1904(archive)
        date_styles, unlocked_styles = _styles(archive)
        has_formula = any(_has_formula(archive.read(n)) for n in _worksheet_parts(archive))
        infos = archive.infolist()
        contents = {info.filename: archive.read(info.filename) for info in infos}
    new_sheet = _patch_sheet(sheet_xml, writes, date1904, date_styles, unlocked_styles)
    new_workbook = _set_full_calc(workbook_xml) if has_formula else workbook_xml
    output = io.BytesIO()
    with ZipFile(output, "w") as target:
        for info in infos:
            payload = contents[info.filename]
            if info.filename == part:
                payload = new_sheet
            elif info.filename == "xl/workbook.xml":
                payload = new_workbook
            clone = ZipInfo(info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.external_attr = info.external_attr
            clone.create_system = info.create_system
            target.writestr(clone, payload)
    return output.getvalue()


def _worksheet_parts(archive: ZipFile) -> list[str]:
    rels = _xml(archive.read("xl/_rels/workbook.xml.rels"))
    parts = []
    for node in rels.iter():
        if _local(node.tag) == "Relationship" and node.get("Type", "").endswith("/worksheet"):
            target = node.get("Target", "")
            path = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join("xl", target))
            )
            if path in archive.namelist():
                parts.append(path)
    return parts


def _has_formula(sheet_xml: bytes) -> bool:
    try:
        return bool(_scan(sheet_xml)["formula_cells"])
    except PatchError:
        return True  # 판단할 수 없으면 다시 계산하도록 표시한다(무해)


def _in_ranges(address: str, refs: list[str]) -> bool:
    from openpyxl.utils.cell import range_boundaries

    row, col = _split(address)
    for ref in refs:
        min_col, min_row, max_col, max_row = range_boundaries(ref)
        if min_row <= row <= max_row and min_col <= col <= max_col:
            return True
    return False


def _inherited_style(found: dict, row, col: int) -> str | None:
    """XML에 없는 칸이 Excel에서 받는 서식: 행 서식(customFormat) → 열 서식 → 없음(검수 M3)."""
    if row is not None and row.attrs.get("customFormat") in ("1", "true") and row.attrs.get("s"):
        return row.attrs["s"]
    for low, high, style in found["cols"]:
        if low <= col <= high:
            return style
    return None


def _in_merge_follower(address: str, merges: list[str]) -> bool:
    from openpyxl.utils.cell import range_boundaries

    row, col = _split(address)
    for ref in merges:
        min_col, min_row, max_col, max_row = range_boundaries(ref)
        if (
            min_row <= row <= max_row
            and min_col <= col <= max_col
            and (row, col) != (min_row, min_col)
        ):
            return True
    return False


def _patch_sheet(data: bytes, writes, date1904, date_styles, unlocked_styles) -> bytes:
    found = _scan(data)
    prefix = _prefix(found["sheetData"].name)
    splices: list[tuple[int, int, bytes]] = []
    by_row: dict[int, list[tuple[int, str]]] = {}
    rows = {int(r.attrs["r"]): r for r in found["rows"]}
    styles: dict[str, str | None] = {}
    for address, value in writes.items():
        if not ADDRESS.match(address):
            raise PatchError(f"칸 주소 형식이 올바르지 않습니다: {address}")
        row, col = _split(address)
        by_row.setdefault(row, []).append((col, address))
        cell = found["cells"].get(address)
        style = (
            cell.attrs.get("s") if cell is not None else _inherited_style(found, rows.get(row), col)
        )
        styles[address] = style
        style_index = int(style) if style is not None and style.isdigit() else 0
        # 막힌 칸은 분석이 이미 걸렀지만, 쓰기 직전에 다시 확인한다
        if address in found["formula_cells"] or _in_ranges(address, found["array_refs"]):
            raise PatchError(f"{address}: 수식 칸에는 쓰지 않습니다.")
        if _in_merge_follower(address, found["merges"]):
            raise PatchError(f"{address}: 병합 칸에는 쓰지 않습니다.")
        if found["protected"] and not (
            style_index < len(unlocked_styles) and unlocked_styles[style_index]
        ):
            raise PatchError(f"{address}: 보호된 칸에는 쓰지 않습니다.")
        if value.kind == "date" and not (
            style_index < len(date_styles) and date_styles[style_index]
        ):
            raise PatchError(
                f"{address}: 날짜 서식이 아닌 칸에는 날짜를 쓰지 않습니다(숫자로 보인다)."
            )
    sheet_data = found["sheetData"]
    new_rows: dict[int, list[bytes]] = {}
    for row_number, targets in sorted(by_row.items()):
        targets.sort()
        row = rows.get(row_number)
        if row is None:
            new_rows[row_number] = [
                _cell_xml(prefix, a, styles[a], writes[a], date1904) for _, a in targets
            ]
            continue
        cells_in_row = sorted(
            (_split(a)[1], e) for a, e in found["cells"].items() if row.start < e.start < row.end
        )
        inserts: dict[int, list[bytes]] = {}
        for col, address in targets:
            existing = found["cells"].get(address)
            if existing is not None:
                splices.append(
                    (
                        existing.start,
                        existing.end,
                        _cell_xml(
                            prefix, address, existing.attrs.get("s"), writes[address], date1904
                        ),
                    )
                )
                continue
            following = next((e for c, e in cells_in_row if c > col), None)
            if following is not None:
                position = following.start
            elif row.self_closing:
                position = -1  # 아래에서 행 전체를 다시 쓴다
            else:
                position = data.rindex(b"</", row.start, row.end)
            inserts.setdefault(position, []).append(
                _cell_xml(prefix, address, styles[address], writes[address], date1904)
            )
        for position, chunks in inserts.items():
            if position == -1:
                start_tag = data[row.start : row.start_end]
                opened = start_tag[:-2].rstrip() + b">"
                closing = f"</{row.name}>".encode()
                splices.append((row.start, row.end, opened + b"".join(chunks) + closing))
            else:
                splices.append((position, position, b"".join(chunks)))
    if new_rows and sheet_data.self_closing:
        opened = data[sheet_data.start : sheet_data.start_end][:-2].rstrip() + b">"
        body = b"".join(
            f'<{prefix}row r="{n}">'.encode() + b"".join(new_rows[n]) + f"</{prefix}row>".encode()
            for n in sorted(new_rows)
        )
        closing = f"</{sheet_data.name}>".encode()
        splices.append((sheet_data.start, sheet_data.end, opened + body + closing))
        new_rows = {}
    if new_rows:
        ordered = sorted(rows)
        for row_number, chunks in sorted(new_rows.items()):
            row_xml = (
                f'<{prefix}row r="{row_number}">'.encode()
                + b"".join(chunks)
                + f"</{prefix}row>".encode()
            )
            following = next((rows[r] for r in ordered if r > row_number), None)
            if following is not None:
                splices.append((following.start, following.start, row_xml))
            elif sheet_data.self_closing:
                opened = data[sheet_data.start : sheet_data.start_end][:-2].rstrip() + b">"
                splices.append(
                    (
                        sheet_data.start,
                        sheet_data.end,
                        opened + row_xml + f"</{sheet_data.name}>".encode(),
                    )
                )
            else:
                position = data.rindex(b"</", sheet_data.start, sheet_data.end)
                splices.append((position, position, row_xml))
    dimension = found["dimension"]
    if dimension is not None and dimension.attrs.get("ref"):
        new_ref = _widen(dimension.attrs["ref"], writes)
        if new_ref != dimension.attrs["ref"]:
            tag = data[dimension.start : dimension.start_end]
            replaced = re.sub(
                rb'ref\s*=\s*(["\'])[^"\']*\1', b'ref="' + new_ref.encode() + b'"', tag, count=1
            )
            splices.append((dimension.start, dimension.start_end, replaced))
    # 같은 위치에 끼우는 것들은 넣은 순서(행·열 순)를 지킨다: 뒤에서부터 적용하되 같은 위치는 묶는다
    merged: dict[tuple[int, int], bytes] = {}
    for start, end, chunk in splices:
        merged[(start, end)] = merged.get((start, end), b"") + chunk
    result = data
    for (start, end), chunk in sorted(
        merged.items(), key=lambda item: (item[0][0], item[0][1]), reverse=True
    ):
        result = result[:start] + chunk + result[end:]
    return result


def _widen(ref: str, writes) -> str:
    from openpyxl.utils.cell import range_boundaries

    try:
        min_col, min_row, max_col, max_row = range_boundaries(ref)
    except ValueError:
        return ref
    if None in (min_col, min_row, max_col, max_row):
        return ref
    for address in writes:
        row, col = _split(address)
        min_row, max_row = min(min_row, row), max(max_row, row)
        min_col, max_col = min(min_col, col), max(max_col, col)
    first = f"{get_column_letter(min_col)}{min_row}"
    last = f"{get_column_letter(max_col)}{max_row}"
    return first if first == last else f"{first}:{last}"


def _set_full_calc(workbook_xml: bytes) -> bytes:
    """calcPr에 fullCalcOnLoad="1". 없으면 CT_Workbook 순서에 맞는 자리에 끼운다."""
    parser = expat.ParserCreate()
    spots: dict[str, tuple[int, int, str]] = {}
    root_name = []

    def start(name, _attrs):
        local = name.rsplit(":", 1)[-1]
        begin = parser.CurrentByteIndex
        if not root_name:
            root_name.append(name)
        if local not in spots and (local == "calcPr" or local in AFTER_CALC_PR):
            spots[local] = (begin, _tag_end(workbook_xml, begin), name)

    parser.StartElementHandler = start
    parser.StartDoctypeDeclHandler = lambda *a: (_ for _ in ()).throw(PatchError("DOCTYPE"))
    parser.Parse(workbook_xml, True)
    if "calcPr" in spots:
        begin, end, _ = spots["calcPr"]
        tag = workbook_xml[begin:end]
        if re.search(rb'fullCalcOnLoad\s*=\s*["\'](1|true)["\']', tag):
            return workbook_xml
        tag = re.sub(rb'\sfullCalcOnLoad\s*=\s*(["\'])[^"\']*\1', b"", tag)
        closing = b"/>" if tag.endswith(b"/>") else b">"
        tag = tag[: -len(closing)].rstrip() + b' fullCalcOnLoad="1"' + closing
        return workbook_xml[:begin] + tag + workbook_xml[end:]
    prefix = _prefix(root_name[0])
    element = f'<{prefix}calcPr fullCalcOnLoad="1"/>'.encode()
    after = [spots[n] for n in AFTER_CALC_PR if n in spots]
    if after:
        position = min(begin for begin, _, _ in after)
    else:
        position = workbook_xml.rindex(b"</")
    return workbook_xml[:position] + element + workbook_xml[position:]


# ---------- 비교 검증 ----------


def _cell_map(sheet_xml: bytes) -> dict[str, bytes]:
    found = _scan(sheet_xml)
    return {address: sheet_xml[e.start : e.end] for address, e in found["cells"].items()}


def _without(xml: bytes, spans: list[tuple[int, int]]) -> bytes:
    for start, end in sorted(spans, reverse=True):
        xml = xml[:start] + xml[end:]
    return xml


def compare_packages(
    original: bytes, patched: bytes, sheet: str, writes: dict[str, CellValue]
) -> tuple[Check, ...]:
    """원본과 결과를 비교한다. 허용되는 차이는 대상 시트의 작성 칸(끼운 행·dimension, 빈 요소를
    여닫는 형태로 바꾼 것 포함)과 workbook.xml calcPr의 fullCalcOnLoad뿐이다. 그 밖은 모두 실패다."""
    checks: list[Check] = []

    def fail(scope, message, change_id=None, expected=None, actual=None):
        checks.append(Check(scope, change_id, expected, actual, False, message))

    try:
        with ZipFile(io.BytesIO(original)) as a, ZipFile(io.BytesIO(patched)) as b:
            if a.namelist() != b.namelist():
                fail("preserve", "패키지 항목 목록이 원본과 다릅니다.")
                return tuple(checks)
            part = sheet_part(a, sheet)
            for name in a.namelist():
                left, right = a.read(name), b.read(name)
                if name == part:
                    _compare_sheet(left, right, writes, fail)
                elif name == "xl/workbook.xml":
                    if _without_calc(left) != _without_calc(right):
                        fail("preserve", "workbook.xml이 재계산 표시 말고도 바뀌었습니다.")
                elif left != right:
                    fail("preserve", f"{name} 항목이 원본과 다릅니다.")
    except (PatchError, ValueError, KeyError, OSError) as exc:
        fail("file", f"결과 파일 구조를 확인할 수 없습니다: {exc}")
        return tuple(checks)
    checks.extend(_reopen_checks(patched, sheet, writes))
    if not any(not c.passed for c in checks):
        checks.append(Check("file", None, None, None, True, "작성한 칸 말고는 원본과 같습니다."))
    return tuple(checks)


CALC_PR = re.compile(rb"<(?:\w+:)?calcPr\b[^>]*?/?>")


def _without_calc(workbook_xml: bytes) -> bytes:
    """calcPr의 fullCalcOnLoad 속성만 지우고, 그 결과 속성이 없는 calcPr은 빼고 비교한다(검수 M4)."""

    def strip(match):
        tag = re.sub(rb"""\sfullCalcOnLoad\s*=\s*(["'])[^"']*\1""", b"", match.group(0))
        return b"" if re.fullmatch(rb"<(?:\w+:)?calcPr\s*/?>", tag) else tag

    return CALC_PR.sub(strip, workbook_xml)


def _rows_layout(found: dict) -> list[tuple[int, list[str], tuple[int, int]]]:
    """(행 번호, 그 행 안 칸 주소의 문서 순서, 행 위치)를 문서 순서로."""
    layout = []
    for row in found["rows"]:
        inside = sorted(
            (e.start, a) for a, e in found["cells"].items() if row.start < e.start < row.end
        )
        layout.append((int(row.attrs["r"]), [a for _, a in inside], (row.start, row.end)))
    return layout


def _normalize_empty(xml: bytes) -> bytes:
    """<row .../>·<sheetData/>를 여닫는 형태로 맞춘다(쓰기가 빈 요소를 그렇게 바꾸므로, 검수 M1)."""
    return re.sub(rb"<((?:\w+:)?(?:row|sheetData))(\s[^>]*?)?\s*/>", rb"<\1\2></\1>", xml)


def _compare_sheet(left: bytes, right: bytes, writes, fail) -> None:
    left_found, right_found = _scan(left), _scan(right)
    before = {a: left[e.start : e.end] for a, e in left_found["cells"].items()}
    after = {a: right[e.start : e.end] for a, e in right_found["cells"].items()}
    changed = {a for a in set(before) | set(after) if before.get(a) != after.get(a)}
    if changed != set(writes):
        extra = sorted(changed - set(writes))
        missing = sorted(set(writes) - changed)
        fail(
            "preserve",
            f"대상 시트에서 바뀐 칸이 작성 칸과 다릅니다(더 바뀜 {extra[:5]}, 안 바뀜 {missing[:5]}).",
        )
    # 구조(검수 H3): 결과의 행은 오름차순, 각 칸은 자기 행 번호의 행 안에 열 오름차순으로 있다
    left_rows = {number for number, _, _ in _rows_layout(left_found)}
    layout = _rows_layout(right_found)
    numbers = [number for number, _, _ in layout]
    if numbers != sorted(numbers) or len(set(numbers)) != len(numbers):
        fail("preserve", "결과 시트의 행 순서가 올바르지 않습니다.")
    inserted_spans = []
    for number, addresses, span in layout:
        positions = [_split(a) for a in addresses]
        columns = [c for _, c in positions]
        if any(r != number for r, _ in positions) or columns != sorted(columns):
            fail("preserve", f"{number}행의 칸 소속이나 순서가 올바르지 않습니다.")
        if number not in left_rows:
            if not addresses or not set(addresses) <= set(writes):
                fail("preserve", f"끼운 {number}행에 작성하지 않은 칸이 있습니다.")
            inserted_spans.append(span)
    # dimension은 원본 범위를 작성 칸까지 넓힌 값이어야 하고 다른 속성은 같아야 한다
    left_dim, right_dim = left_found["dimension"], right_found["dimension"]
    if (left_dim is None) != (right_dim is None):
        fail("preserve", "dimension 요소가 추가되거나 사라졌습니다.")
    elif left_dim is not None:
        left_ref = left_dim.attrs.get("ref")
        expected_ref = _widen(left_ref, writes) if left_ref else left_ref
        others_left = {k: v for k, v in left_dim.attrs.items() if k != "ref"}
        others_right = {k: v for k, v in right_dim.attrs.items() if k != "ref"}
        if others_left != others_right or right_dim.attrs.get("ref") != expected_ref:
            fail("preserve", "dimension이 예상과 다릅니다.")

    # 칸 바깥(행 속성·병합·서식·확장 등)은 바이트까지 같아야 한다: 칸·dimension·끼운 행만 빼고 비교
    def outside(xml: bytes, found: dict, extra_spans: list) -> bytes:
        inside_extra = lambda e: any(lo <= e.start < hi for lo, hi in extra_spans)  # noqa: E731
        spans = [(e.start, e.end) for e in found["cells"].values() if not inside_extra(e)]
        spans += extra_spans
        if found["dimension"] is not None:
            spans.append((found["dimension"].start, found["dimension"].end))
        return _normalize_empty(_without(xml, spans))

    if outside(left, left_found, []) != outside(right, right_found, inserted_spans):
        fail("preserve", "대상 시트의 칸 바깥 내용이 바뀌었습니다.")


def _reopen_checks(patched: bytes, sheet: str, writes) -> list[Check]:
    """결과를 openpyxl로 다시 열어 작성 값이 예상과 같은지 본다(작업자 프로세스, 시간·메모리 상한)."""
    from . import isolation

    try:
        actual = isolation.run(_read_values, patched, sheet, tuple(writes))
    except Exception as exc:  # noqa: BLE001 - 열리지 않으면 그 자체가 검증 실패다
        return [
            Check(
                "file",
                None,
                None,
                None,
                False,
                f"결과 파일을 다시 열 수 없습니다: {type(exc).__name__}",
            )
        ]
    checks = []
    for address, expected in writes.items():
        got = actual.get(address)
        want = escape_x(expected.value) if expected.kind == "text" else expected.value
        ok = got is not None and (got.kind, got.value) == (expected.kind, want)
        checks.append(
            Check(
                "cell",
                address,
                expected,
                got,
                ok,
                "일치" if ok else f"{address} 값이 예상과 다릅니다.",
            )
        )
    return checks


def _read_values(patched: bytes, sheet: str, addresses: tuple[str, ...]) -> dict[str, CellValue]:
    from openpyxl import load_workbook

    from .xlsx import cell_value

    book = load_workbook(io.BytesIO(patched), data_only=False)
    try:
        ws = book[sheet]
        return {address: cell_value(ws[address].value) for address in addresses}
    finally:
        book.close()
