import os
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HS = "http://www.hancom.co.kr/hwpml/2011/section"


def para(text, ident="0"):
    return f'<hp:p id="{ident}"><hp:run><hp:t>{escape(text)}</hp:t></hp:run></hp:p>'


def cell(text, row, col, rowspan=1, colspan=1):
    return (
        "<hp:tc><hp:subList>" + para(text) + "</hp:subList>"
        f'<hp:cellAddr rowAddr="{row}" colAddr="{col}"/>'
        f'<hp:cellSpan rowSpan="{rowspan}" colSpan="{colspan}"/></hp:tc>'
    )


def table(rows):
    return "<hp:tbl>" + "".join("<hp:tr>" + "".join(r) + "</hp:tr>" for r in rows) + "</hp:tbl>"


def write_hwpx(path: Path, sections=None, extra=None):
    if sections is None:
        sections = [
            para("접수번호: A-001")
            + "<hp:p><hp:run>"
            + table(
                [
                    [cell("성명", 0, 0), cell("홍길동", 0, 1)],
                    [cell("기관명", 1, 0), cell("한빛초등학교", 1, 1)],
                ]
            )
            + "</hp:run></hp:p>"
        ]
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/hwp+zip")
        for n, xml in enumerate(sections):
            z.writestr(
                f"Contents/section{n}.xml",
                f'<hs:sec xmlns:hp="{HP}" xmlns:hs="{HS}">{xml}</hs:sec>',
            )
        for name, content in (extra or {}).items():
            z.writestr(name, content)
    return path


@pytest.fixture
def sample(tmp_path):
    return write_hwpx(tmp_path / "지원서_홍길동.hwpx")
