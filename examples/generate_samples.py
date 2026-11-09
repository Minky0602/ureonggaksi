"""Generate synthetic, non-sensitive parser fixtures (not full Hancom layout documents)."""

from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HS = "http://www.hancom.co.kr/hwpml/2011/section"


def paragraph(text):
    return "<hp:p><hp:run><hp:t>" + escape(text) + "</hp:t></hp:run></hp:p>"


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    records = [
        ("홍길동", "한빛초등학교", "12", "A-001"),
        ("김영희", "새봄중학교", "8", "A-002"),
        ("박민수", "푸른고등학교", "15", "A-003"),
    ]
    for name, organization, count, identifier in records:
        rows = []
        for row, (key, value) in enumerate(
            [("성명", name), ("기관명", organization), ("신청 인원", count)]
        ):
            cells = []
            for col, text in enumerate([key, value]):
                cells.append(
                    f"<hp:tc><hp:subList>{paragraph(text)}</hp:subList>"
                    f'<hp:cellAddr rowAddr="{row}" colAddr="{col}"/>'
                    '<hp:cellSpan rowSpan="1" colSpan="1"/></hp:tc>'
                )
            rows.append("<hp:tr>" + "".join(cells) + "</hp:tr>")
        xml = f'<hs:sec xmlns:hp="{HP}" xmlns:hs="{HS}">'
        xml += paragraph("2026 방과후 프로그램 신청서 (합성 예제)") + paragraph(
            "접수번호: " + identifier
        )
        xml += (
            '<hp:p><hp:run><hp:tbl rowCnt="3" colCnt="2">'
            + "".join(rows)
            + "</hp:tbl></hp:run></hp:p></hs:sec>"
        )
        path = directory / f"신청서_{name}.hwpx"
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            archive.writestr("mimetype", "application/hwp+zip")
            archive.writestr("Contents/section0.xml", xml)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for path in generate(Path(__file__).parent / "generated"):
        print(path)
