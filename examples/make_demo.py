"""시연용 합성 자료: 교육 이수확인서 HWPX 3장과 그 값을 채울 취합표 XLSX.

실제 개인정보가 없는 합성 자료다. 한글 프로그램의 전체 서식이 아니라 우렁각시가 읽는 구조(문단·표)만 담는다.

    uv run python examples/make_demo.py            # examples/generated/demo/ 에 만든다

시연 요청 예: "발급번호 기준으로 신청인이랑 생일, 이수시간 채워줘"
- 이수시간은 열 이름이 같아 바로 채운다.
- 신청인 ← 문서 '성명', 생일 ← 문서 '생년월일'은 동의어표로 짝지어 '확인 필요'로 보인다.
- A-003 행의 이수시간에는 문서와 다른 값이 이미 있어 '직접 처리'로 남는다.
"""

import sys
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HS = "http://www.hancom.co.kr/hwpml/2011/section"
PEOPLE = [  # (발급번호, 성명, 직위, 생년월일, 이수시간)
    ("A-001", "홍길동", "주무관", "1990-04-12", "12시간"),
    ("A-002", "김영희", "팀장", "1985-11-03", "8시간"),
    ("A-003", "박민수", "주무관", "1993-07-21", "16시간"),
]


def _paragraph(text: str) -> str:
    return "<hp:p><hp:run><hp:t>" + escape(text) + "</hp:t></hp:run></hp:p>"


def _cell(text: str, row: int, col: int) -> str:
    return (
        f"<hp:tc><hp:subList>{_paragraph(text)}</hp:subList>"
        f'<hp:cellAddr rowAddr="{row}" colAddr="{col}"/>'
        '<hp:cellSpan rowSpan="1" colSpan="1"/></hp:tc>'
    )


def certificate(path: Path, number: str, name: str, title: str, birth: str, hours: str) -> Path:
    items = [("성명", name), ("직위", title), ("생년월일", birth), ("이수시간", hours)]
    rows = "".join(
        "<hp:tr>" + _cell(key, r, 0) + _cell(value, r, 1) + "</hp:tr>"
        for r, (key, value) in enumerate(items)
    )
    xml = (
        f'<hs:sec xmlns:hp="{HP}" xmlns:hs="{HS}">'
        + _paragraph("2026 청렴 교육 이수확인서 (합성 예제)")
        + _paragraph(f"발급번호: {number}")
        + f'<hp:p><hp:run><hp:tbl rowCnt="{len(items)}" colCnt="2">{rows}</hp:tbl></hp:run></hp:p>'
        + "</hs:sec>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", xml)
    return path


def roster(path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = "교육이수현황"
    sheet.append(["발급번호", "신청인", "생일", "직급", "이수시간"])
    for number, *_ in PEOPLE:
        sheet.append([number, None, None, None, None])
    sheet["E4"] = "10시간"  # 문서(16시간)와 다른 값이 이미 있다: 덮어쓰지 않고 직접 처리로 남긴다
    book.save(path)
    return path


def generate(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = [
        certificate(directory / f"이수확인서_{name}.hwpx", number, name, title, birth, hours)
        for number, name, title, birth, hours in PEOPLE
    ]
    paths.append(roster(directory / "교육이수현황.xlsx"))
    return paths


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "generated" / "demo"
    for made in generate(out):
        print(made)
