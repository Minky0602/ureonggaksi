import json
from dataclasses import asdict, dataclass
from pathlib import Path

import regex

from .storage import atomic_write, local_path


class WorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    name: str
    kind: str
    query: str = ""
    section: str = ""
    table: int = 1
    row: int = 1
    col: int = 1
    direction: str = "right"
    group: int = 1
    required: bool = True

    def __post_init__(self):
        for name, limit in [("name", 100), ("query", 2000), ("section", 200)]:
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > limit or "\x00" in value:
                raise WorkflowError(f"{name} 문자열이 유효하지 않습니다.")
        if not self.name.strip() or self.name != self.name.strip():
            raise WorkflowError("항목명은 비어 있거나 양끝에 공백이 있을 수 없습니다.")
        if self.kind not in {"label", "cell", "regex"}:
            raise WorkflowError("지원하지 않는 추출 방식입니다.")
        if self.direction not in {"right", "below"} or type(self.required) is not bool:
            raise WorkflowError("방향 또는 필수 여부가 유효하지 않습니다.")
        for name in ("table", "row", "col", "group"):
            value = getattr(self, name)
            if type(value) is not int or not (0 if name == "group" else 1) <= value <= 1_000_000:
                raise WorkflowError(f"{name} 번호가 유효하지 않습니다.")
        if self.section and not regex.fullmatch(r"Contents/section\d+\.xml", self.section):
            raise WorkflowError("구역은 Contents/section0.xml 형식이어야 합니다.")
        if self.kind == "cell" and not self.section:
            raise WorkflowError("지정 셀 규칙에는 구역을 지정해야 합니다.")
        if self.kind in {"label", "regex"} and not self.query.strip():
            raise WorkflowError("검색할 항목명 또는 정규식을 입력하세요.")
        if self.kind == "regex":
            try:
                pattern = regex.compile(self.query)
                if self.group > pattern.groups:
                    raise WorkflowError(
                        "정규식에 해당 캡처 그룹이 없습니다. 전체 일치는 그룹 0입니다."
                    )
            except (regex.error, RecursionError, OverflowError) as exc:
                raise WorkflowError("정규식 문법이 올바르지 않습니다.") from exc

    @property
    def description(self):
        if self.kind == "cell":
            return f"{self.section} · 표 {self.table} · {self.row}행 {self.col}열"
        if self.kind == "label":
            return f"{self.query} → {'오른쪽' if self.direction == 'right' else '아래'}"
        return f"{self.query} · 그룹 {self.group}"


@dataclass(frozen=True)
class Workflow:
    name: str
    rules: tuple[Rule, ...]
    version: int = 1

    def __post_init__(self):
        if type(self.version) is not int or self.version != 1:
            raise WorkflowError("지원하지 않는 워크플로 버전입니다.")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100:
            raise WorkflowError("워크플로 이름을 1~100자로 입력하세요.")
        if not isinstance(self.rules, tuple) or not 1 <= len(self.rules) <= 100:
            raise WorkflowError("추출 규칙은 1~100개여야 합니다.")
        if not all(isinstance(r, Rule) for r in self.rules):
            raise WorkflowError("추출 규칙 형식이 올바르지 않습니다.")
        if len({r.name for r in self.rules}) != len(self.rules):
            raise WorkflowError("추출 항목 이름은 중복될 수 없습니다.")

    @classmethod
    def load(cls, path: str | Path):
        try:
            path = local_path(path)
            with path.open("rb") as stream:
                raw = stream.read(1_048_577)
            if len(raw) > 1_048_576:
                raise WorkflowError("워크플로는 1 MiB 이하여야 합니다.")
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict) or set(data) != {"name", "version", "rules"}:
                raise WorkflowError("워크플로 JSON의 최상위 필드가 올바르지 않습니다.")
            if not isinstance(data["rules"], list) or not 1 <= len(data["rules"]) <= 100:
                raise WorkflowError("규칙 목록은 1~100개여야 합니다.")
            return cls(data["name"], tuple(Rule(**r) for r in data["rules"]), data["version"])
        except WorkflowError:
            raise
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise WorkflowError("워크플로 파일을 읽을 수 없거나 형식이 올바르지 않습니다.") from exc

    def save(self, path: str | Path):
        path = local_path(path)
        if path.suffix.lower() != ".json":
            raise WorkflowError(
                "워크플로는 .json 파일로만 저장할 수 있습니다. 원본 문서는 덮어쓰지 않습니다."
            )
        data = json.dumps(asdict(self), ensure_ascii=False, indent=2)
        atomic_write(path, lambda p: p.write_text(data + "\n", encoding="utf-8"))
