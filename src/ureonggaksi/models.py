from dataclasses import dataclass


@dataclass(frozen=True)
class Block:
    section: str
    locator: str
    text: str
    kind: str
    table: int = 0
    row: int = 0
    col: int = 0
    row_span: int = 1
    col_span: int = 1
    # 한글의 변경 추적(삽입·삭제 표식)이 남아 있는 블록. 어느 글자가 최종인지 추측하지 않고
    # text를 비워 둔다. 사용자가 한글에서 변경 내용을 적용한 뒤 다시 저장해야 읽는다.
    tracked_change: bool = False

    @property
    def location(self) -> str:
        if self.kind == "cell":
            return f"{self.section} · 표 {self.table} · {self.row}행 {self.col}열"
        return f"{self.section} · {self.locator}"


@dataclass(frozen=True)
class Document:
    path: str
    sha256: str
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class ExtractedValue:
    document_path: str
    sha256: str
    field: str
    value: str = ""
    status: str = "missing"
    source: Block | None = None
    start: int = 0
    end: int = 0
    matches: int = 0
    message: str = ""
    required: bool = True


@dataclass(frozen=True)
class DocumentResult:
    path: str
    sha256: str
    values: tuple[ExtractedValue, ...]
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all(v.status == "ok" or not v.required for v in self.values)


@dataclass(frozen=True)
class Verification:
    ok: bool
    message: str
    source: Block | None = None
