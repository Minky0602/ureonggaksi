"""Read HWPX structures without Hancom, network calls or disk extraction."""

import hashlib
import io
import posixpath
import re
from pathlib import Path, PurePosixPath
from xml.etree.ElementTree import Element, ParseError
from zipfile import BadZipFile, ZipFile
from zlib import error as ZlibError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from .models import Block, Document
from .storage import local_path

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_NODES = 200_000
MAX_BLOCKS = 20_000
MAX_DEPTH = 128
SECTION = re.compile(r"Contents/section\d+\.xml\Z")
# <hp:t> 안에서 글자 대신 나오는 빈 요소와 그 뜻. 실제 한글 저장본에서 확인한 것만 적는다.
# nbSpace(줄바꿈 금지 공백)·fwSpace(고정폭 공백)는 일반 공백으로 읽는다. 키 대조에 쓰이기 때문이다.
SPACING_ELEMENTS = {"tab": "\t", "lineBreak": "\n", "nbSpace": " ", "fwSpace": " "}
# 변경 추적 표식. 이 사이의 글자는 삽입 또는 삭제가 확정되지 않은 상태다.
TRACK_CHANGE_ELEMENTS = {"insertBegin", "insertEnd", "deleteBegin", "deleteEnd"}
# 그 밖의 표식(markpenBegin/End 형광펜, titleMark 제목)은 글자를 바꾸지 않으므로 무시하고 글자만 잇는다.


class HwpxError(ValueError):
    pass


def _tag(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _xml(data: bytes) -> Element:
    root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    stack = [(root, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise HwpxError("XML 구조가 안전한 처리 한도를 초과했습니다.")
        stack.extend((child, depth + 1) for child in node)
    return root


def _walk(root: Element):
    """Stable indexed structural paths, independent of namespace prefix spelling."""
    stack = [(root, f"/{_tag(root)}[1]", ())]
    while stack:
        node, path, ancestors = stack.pop()
        yield node, path, ancestors
        counts: dict[str, int] = {}
        children = []
        for child in node:
            tag = _tag(child)
            counts[tag] = counts.get(tag, 0) + 1
            children.append((child, f"{path}/{tag}[{counts[tag]}]", (*ancestors, node)))
        stack.extend(reversed(children))


def _paragraph_text(node: Element) -> tuple[str, bool]:
    """Return the paragraph's text and whether a track-change mark remains in it."""
    parts: list[str] = []
    tracked = False

    def collect(element):
        nonlocal tracked
        tag = _tag(element)
        if element is not node and tag in {"tbl", "p", "subList"}:
            return
        if tag == "t":
            if element.text:
                parts.append(element.text)
            for child in element:
                child_tag = _tag(child)
                if child_tag in SPACING_ELEMENTS:
                    parts.append(SPACING_ELEMENTS[child_tag])
                elif child_tag in TRACK_CHANGE_ELEMENTS:
                    tracked = True
                if child.tail:
                    parts.append(child.tail)
            return
        if tag in SPACING_ELEMENTS:
            parts.append(SPACING_ELEMENTS[tag])
        elif tag in TRACK_CHANGE_ELEMENTS:
            tracked = True  # 표본에서는 <t> 안에만 나왔지만, 바깥에 있어도 같은 뜻이다
        else:
            for child in element:
                collect(child)

    collect(node)
    return "".join(parts), tracked


def _positive_attribute(node: Element, name: str, default: int, zero=False) -> int:
    value = node.get(name)
    result = default if value is None else int(value)
    if not (0 if zero else 1) <= result <= 1_000_000:
        raise HwpxError("표의 행·열 주소가 유효하지 않습니다.")
    return result


def _blocks(root: Element, section: str) -> list[Block]:
    if _tag(root) != "sec":
        raise HwpxError("본문 구역의 XML 루트가 올바르지 않습니다.")
    walked = list(_walk(root))
    tables = {
        id(n): i
        for i, (n, _, _) in enumerate((item for item in walked if _tag(item[0]) == "tbl"), start=1)
    }
    result = []
    for node, locator, ancestors in walked:
        tag = _tag(node)
        if tag == "tc":
            owner = next((a for a in reversed(ancestors) if _tag(a) == "tbl"), None)
            if owner is None:
                raise HwpxError("표 바깥에 셀이 있습니다.")
            address = next((c for c in node if _tag(c) == "cellAddr"), None)
            span = next((c for c in node if _tag(c) == "cellSpan"), None)
            if address is None:
                raise HwpxError("표 셀의 주소 정보가 없습니다.")
            if address.get("rowAddr") is None or address.get("colAddr") is None:
                raise HwpxError("표 셀의 행·열 주소가 누락되었습니다.")
            texts = []
            tracked = False
            for child, _, parents in _walk(node):
                if _tag(child) == "p" and not any(_tag(p) == "tc" for p in parents[1:]):
                    text, paragraph_tracked = _paragraph_text(child)
                    tracked = tracked or paragraph_tracked
                    if text or not any(_tag(n) == "tbl" for n in child.iter()):
                        texts.append(text)
            result.append(
                Block(
                    section,
                    locator,
                    "" if tracked else "\n".join(texts),
                    "cell",
                    tables[id(owner)],
                    _positive_attribute(address, "rowAddr", 0, zero=True) + 1,
                    _positive_attribute(address, "colAddr", 0, zero=True) + 1,
                    _positive_attribute(span, "rowSpan", 1) if span is not None else 1,
                    _positive_attribute(span, "colSpan", 1) if span is not None else 1,
                    tracked_change=tracked,
                )
            )
        elif tag == "p" and not any(_tag(a) == "tc" for a in ancestors):
            text, tracked = _paragraph_text(node)
            if tracked:
                # 글자를 비운 채 블록은 남겨 "읽지 않은 곳이 있다"는 사실을 보존한다.
                result.append(Block(section, locator, "", "paragraph", tracked_change=True))
            elif text:
                result.append(Block(section, locator, text, "paragraph"))
        if len(result) > MAX_BLOCKS:
            raise HwpxError("문서의 텍스트 블록 수가 한도를 초과했습니다.")
    addresses = set()
    for block in result:
        if block.kind != "cell":
            continue
        key = (block.table, block.row, block.col)
        if key in addresses:
            raise HwpxError("같은 주소를 가진 중복 표 셀이 있습니다.")
        addresses.add(key)
    return result


def _section_order(archive: ZipFile, names: set[str], xml_limit: int) -> list[str]:
    sections = sorted(
        (n for n in names if SECTION.fullmatch(n)),
        key=lambda n: int(re.search(r"(\d+)\.xml$", n)[1]),
    )
    if not sections:
        raise HwpxError("HWPX 본문 구역이 없습니다. HWP는 HWPX로 변환해 주세요.")
    if "Contents/content.hpf" not in names:
        return sections
    if archive.getinfo("Contents/content.hpf").file_size > xml_limit:
        raise HwpxError("패키지 XML이 처리 한도를 초과했습니다.")
    root = _xml(archive.read("Contents/content.hpf"))
    items = {}
    for node in root.iter():
        if _tag(node) == "item":
            href = node.get("href", "")
            if (
                ":" in href
                or "\\" in href
                or href.startswith("/")
                or ".." in PurePosixPath(href).parts
            ):
                raise HwpxError("외부 파일을 참조하는 패키지는 허용하지 않습니다.")
            identifier = node.get("id", "")
            if not identifier or identifier in items:
                raise HwpxError("패키지 항목의 ID가 없거나 중복되었습니다.")
            # Hancom uses package-root paths; other writers use HPF-relative paths.
            candidate = posixpath.normpath(href)
            items[identifier] = (
                candidate
                if candidate in names
                else posixpath.normpath(posixpath.join("Contents", href))
            )
    ordered = [items.get(n.get("idref", ""), "") for n in root.iter() if _tag(n) == "itemref"]
    if any(not item for item in ordered):
        raise HwpxError("패키지 순서가 존재하지 않는 항목을 참조합니다.")
    selected = [n for n in ordered if n in sections]
    if ordered and (len(selected) != len(sections) or len(set(selected)) != len(sections)):
        raise HwpxError("본문 구역의 패키지 순서가 불완전하거나 중복되었습니다.")
    return selected or sections


def read_hwpx(path: str | Path, *, max_xml_bytes=MAX_XML_BYTES) -> Document:
    try:
        path = local_path(path)
        if path.suffix.lower() != ".hwpx":
            raise HwpxError("HWPX 파일만 지원합니다.")
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except HwpxError:
        raise
    except (OSError, ValueError) as exc:
        raise HwpxError(
            "문서를 읽을 수 없습니다. 손상 여부, XML 형식, 로컬 경로와 접근 권한을 확인하세요."
        ) from exc
    return parse_hwpx(data, path, max_xml_bytes=max_xml_bytes)


def parse_hwpx(data: bytes, path: str | Path, *, max_xml_bytes=MAX_XML_BYTES) -> Document:
    """Parse already-read bytes. Analysis works on the in-memory snapshot, never re-reading disk."""
    try:
        if len(data) > MAX_FILE_BYTES:
            raise HwpxError("파일이 64 MiB를 초과했습니다.")
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_TOTAL_BYTES:
                raise HwpxError("압축파일의 항목 수 또는 해제 크기가 한도를 초과했습니다.")
            names = set()
            for entry in entries:
                name = entry.filename
                parts = PurePosixPath(name).parts
                if (
                    name in names
                    or "\\" in name
                    or name.startswith("/")
                    or ":" in name
                    or ".." in parts
                    or "\x00" in name
                ):
                    raise HwpxError("압축파일에 안전하지 않은 경로 또는 중복 항목이 있습니다.")
                names.add(name)
                if entry.flag_bits & 1:
                    raise HwpxError("암호화된 HWPX는 지원하지 않습니다.")
                if (
                    entry.file_size > 1_048_576
                    and entry.file_size / max(entry.compress_size, 1) > 1000
                ):
                    raise HwpxError("비정상적으로 높은 압축률의 문서입니다.")
                if name.lower().endswith((".xml", ".hpf")) and entry.file_size > max_xml_bytes:
                    raise HwpxError("문서 XML이 처리 한도를 초과했습니다.")
            if "mimetype" not in names or archive.getinfo("mimetype").file_size > 128:
                raise HwpxError("HWPX 형식 정보가 없습니다.")
            if archive.read("mimetype").strip() != b"application/hwp+zip":
                raise HwpxError("HWPX 형식 정보가 올바르지 않습니다.")
            if "META-INF/manifest.xml" in names:
                manifest = _xml(archive.read("META-INF/manifest.xml"))
                if any("encrypt" in _tag(n).lower() for n in manifest.iter()):
                    raise HwpxError("암호화된 HWPX는 지원하지 않습니다.")
            blocks = []
            for section in _section_order(archive, names, max_xml_bytes):
                blocks.extend(_blocks(_xml(archive.read(section)), section))
                if len(blocks) > MAX_BLOCKS:
                    raise HwpxError("문서의 텍스트 블록 수가 한도를 초과했습니다.")
        return Document(str(path), hashlib.sha256(data).hexdigest(), tuple(blocks))
    except HwpxError:
        raise
    except (
        OSError,
        ValueError,
        BadZipFile,
        ParseError,
        DefusedXmlException,
        RuntimeError,
        NotImplementedError,
        LookupError,
        ZlibError,
    ) as exc:
        raise HwpxError(
            "문서를 읽을 수 없습니다. 손상 여부, XML 형식, 로컬 경로와 접근 권한을 확인하세요."
        ) from exc
