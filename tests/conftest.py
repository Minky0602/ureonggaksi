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


def write_xlsx(
    path: Path, rows=None, *, sheet="사업취합", merged=(), protect=False, extra_sheets=()
):
    """합성 취합표. rows는 1행부터의 값 목록이고 None은 빈 칸, "=..." 문자열은 수식이다."""
    from openpyxl import Workbook

    if rows is None:
        rows = [
            ["사업코드", "사업명", "2026 예산"],
            ["0012", "도서관 리모델링", 3500000],
            ["0013", "체육관 보수", None],
        ]
    book = Workbook()
    ws = book.active
    ws.title = sheet
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row, start=1):
            if value is not None:
                ws.cell(r, c, value)
    for ref in merged:
        ws.merge_cells(ref)
    ws.protection.sheet = protect
    for name in extra_sheets:
        book.create_sheet(name)
    book.save(path)
    return path


@pytest.fixture
def target(tmp_path):
    return write_xlsx(tmp_path / "사업취합.xlsx")


@pytest.fixture(autouse=True)
def _inline_isolation(request, monkeypatch):
    """작업자 프로세스 격리는 test_isolation.py가 검증한다. 다른 시험은 같은 함수를 바로 불러 시간을 줄인다.

    격리 실행과 직접 실행의 결과가 같음은 test_isolation.py의 종단 시험이 확인한다.
    """
    if request.module.__name__.endswith("test_isolation"):
        return
    from ureonggaksi import isolation

    monkeypatch.setattr(isolation, "run", lambda func, *args, **_kwargs: func(*args))
