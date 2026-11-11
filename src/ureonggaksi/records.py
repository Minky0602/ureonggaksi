"""Split a document into business records and read each field's text (PRD FR-09, api.md 2.4).

document: one document is one record; fields come from the existing label/cell/regex extractor.
table_rows: each row below the header of one table is a record; fields are columns found by header text.
Nothing here converts values — that is normalize.py — and nothing guesses: two matching places is
"ambiguous", a track-change cell is "tracked", an absent place is "missing".
"""

from dataclasses import dataclass

from .extract import TRACKED_CHANGE_MESSAGE, _normalized, extract
from .models import Block, Document
from .rules import COLUMN_KIND, RuleSet
from .workflow import Workflow

FieldStatus = str  # "ok" · "missing" · "ambiguous" · "tracked"


@dataclass(frozen=True)
class FieldValue:
    name: str
    raw_text: str  # 양끝 공백만 뗀 원문 글자. ok가 아니면 ""
    status: FieldStatus
    location: str = ""  # Block.location. 없으면 ""
    message: str = ""
    source: Block | None = None


@dataclass(frozen=True)
class Record:
    file_id: str
    sha256: str
    index: int  # 문서 안 순서(1부터). document 종류는 항상 1
    key: str  # 키 항목의 원문 글자. 못 읽었으면 ""
    values: tuple[FieldValue, ...]

    @property
    def record_key(self) -> str:
        """change_id의 재료. 키를 못 읽은 레코드는 파일·순서로 구분한다."""
        return self.key if self.key else f"{self.file_id}#{self.index}"

    def value(self, name: str) -> FieldValue | None:
        return next((v for v in self.values if v.name == name), None)


def extract_records(doc: Document, file_id: str, rule: RuleSet) -> tuple[Record, ...]:
    if rule.record.kind == "document":
        return (_document_record(doc, file_id, rule),)
    return _table_records(doc, file_id, rule)


def _document_record(doc: Document, file_id: str, rule: RuleSet) -> Record:
    workflow = Workflow("analyze", tuple(f.to_rule() for f in rule.fields))
    values = []
    for extracted in extract(doc, workflow).values:
        if extracted.status == "ok":
            values.append(
                FieldValue(
                    extracted.field,
                    extracted.value.strip(),
                    "ok",
                    extracted.source.location if extracted.source else "",
                    "",
                    extracted.source,
                )
            )
        elif extracted.status == "ambiguous":
            values.append(FieldValue(extracted.field, "", "ambiguous", "", extracted.message))
        elif extracted.message == TRACKED_CHANGE_MESSAGE:
            values.append(FieldValue(extracted.field, "", "tracked", "", extracted.message))
        else:  # missing, 정규식 한도 초과(error)도 "값을 못 읽음"이다
            values.append(FieldValue(extracted.field, "", "missing", "", extracted.message))
    key = rule.record.key_field
    key_value = next((v for v in values if v.name == key and v.status == "ok"), None)
    return Record(file_id, doc.sha256, 1, key_value.raw_text if key_value else "", tuple(values))


def _table_cells(doc: Document, rule: RuleSet) -> list[Block]:
    """규칙이 가리키는 표의 셀들. 구역이 비어 있으면 그 번호의 표가 있는 첫 구역."""
    cells = [b for b in doc.blocks if b.kind == "cell" and b.table == rule.record.table]
    if rule.record.section:
        return [b for b in cells if b.section == rule.record.section]
    first = next((b.section for b in cells), None)
    return [b for b in cells if b.section == first] if first else []


def _cell_at(cells: list[Block], row: int, col: int) -> Block | None:
    return next(
        (
            b
            for b in cells
            if b.row <= row < b.row + b.row_span and b.col <= col < b.col + b.col_span
        ),
        None,
    )


def _table_records(doc: Document, file_id: str, rule: RuleSet) -> tuple[Record, ...]:
    cells = _table_cells(doc, rule)
    if not cells:
        return ()
    header_row = rule.record.header_row
    headers = [b for b in cells if b.row <= header_row < b.row + b.row_span]
    # 데이터는 머리글 블록이 덮는 행(rowSpan) 다음부터다(검수 M5)
    data_start = max((h.row + h.row_span for h in headers), default=header_row + 1)
    data_cells = [b for b in cells if b.row >= data_start]
    # 항목 → 열 번호. 머리글이 둘 이상 맞으면 ambiguous. 머리글 하나가 여러 열에 걸치면(colSpan) 그 아래 데이터
    # 칸이 실제로 나뉘는지 본다: 모든 데이터 칸이 머리글과 같은 시작 열·같은 폭이면 한 열(2차 검수 N3),
    # 하나라도 나뉘면 어느 열인지 알 수 없어 ambiguous(1차 검수 H1: 첫 하위 열을 조용히 읽지 않는다).
    columns: dict[str, list[int]] = {}
    for field in rule.fields:
        wanted = _normalized(field.query if field.kind == COLUMN_KIND else field.name)
        found: set[int] = set()
        for h in headers:
            if not h.text or _normalized(h.text) != wanted:
                continue
            under = [b for b in data_cells if h.col <= b.col < h.col + h.col_span]
            if h.col_span == 1 or all((b.col, b.col_span) == (h.col, h.col_span) for b in under):
                found.add(h.col)
            else:
                found.update(range(h.col, h.col + h.col_span))
        columns[field.name] = sorted(found)
    last_row = max(b.row + b.row_span - 1 for b in cells)
    records = []
    for row in range(data_start, last_row + 1):
        row_cells = [b for b in cells if b.row <= row < b.row + b.row_span]
        if not any(b.text.strip() or b.tracked_change for b in row_cells):
            continue  # 빈 행(합계·여백)은 레코드가 아니다
        values = []
        for field in rule.fields:
            cols = columns[field.name]
            if not cols:
                values.append(
                    FieldValue(field.name, "", "missing", "", "표에 그런 열 머리글이 없습니다.")
                )
                continue
            if len(cols) > 1:
                values.append(
                    FieldValue(field.name, "", "ambiguous", "", "같은 열 머리글이 둘 이상입니다.")
                )
                continue
            cell = _cell_at(cells, row, cols[0])
            if cell is None:
                values.append(FieldValue(field.name, "", "missing", "", "그 행에 칸이 없습니다."))
            elif cell.tracked_change:
                values.append(
                    FieldValue(field.name, "", "tracked", cell.location, TRACKED_CHANGE_MESSAGE)
                )
            elif cell.text.strip():
                values.append(
                    FieldValue(field.name, cell.text.strip(), "ok", cell.location, "", cell)
                )
            else:
                values.append(FieldValue(field.name, "", "missing", cell.location, "빈 칸입니다."))
        key_value = next(
            (v for v in values if v.name == rule.record.key_field and v.status == "ok"), None
        )
        records.append(
            Record(
                file_id,
                doc.sha256,
                len(records) + 1,
                key_value.raw_text if key_value else "",
                tuple(values),
            )
        )
    return tuple(records)
