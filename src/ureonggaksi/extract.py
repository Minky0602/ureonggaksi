import time

import regex

from .hwpx import HwpxError, read_hwpx
from .models import Document, DocumentResult, ExtractedValue, Verification
from .workflow import Rule, Workflow

REGEX_BUDGET_SECONDS = 0.15
TRACKED_CHANGE_MESSAGE = (
    "변경 추적이 남아 있어 값을 읽지 않았습니다. "
    "원문을 확인해 직접 입력하거나, 한글에서 변경 내용을 적용한 뒤 다시 저장해 주세요."
)


def _trim(text: str, start=0, end=None):
    end = len(text) if end is None else end
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _normalized(text: str):
    return "".join(text.split()).rstrip(":：").casefold()


def _candidates(doc: Document, rule: Rule):
    blocks = [b for b in doc.blocks if not rule.section or b.section == rule.section]
    if rule.kind == "cell":
        for b in blocks:
            if b.kind == "cell" and (b.table, b.row, b.col) == (rule.table, rule.row, rule.col):
                yield b, *_trim(b.text)
    elif rule.kind == "label":
        for label in blocks:
            if label.kind != "cell" or _normalized(label.text) != _normalized(rule.query):
                continue
            row = label.row + (label.row_span if rule.direction == "below" else 0)
            col = label.col + (label.col_span if rule.direction == "right" else 0)
            for b in blocks:
                if (
                    b.kind == "cell"
                    and b.section == label.section
                    and b.table == label.table
                    and b.row <= row < b.row + b.row_span
                    and b.col <= col < b.col + b.col_span
                ):
                    yield b, *_trim(b.text)
    else:
        deadline = time.monotonic() + REGEX_BUDGET_SECONDS
        pattern = regex.compile(rule.query)
        for b in blocks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            for match in pattern.finditer(b.text, timeout=remaining):
                start, end = match.span(rule.group)
                if start >= 0:
                    yield b, *_trim(b.text, start, end)


def extract(doc: Document, workflow: Workflow) -> DocumentResult:
    values = []
    for rule in workflow.rules:
        base = dict(
            document_path=doc.path, sha256=doc.sha256, field=rule.name, required=rule.required
        )
        try:
            candidates = []
            tracked = False
            for source, start, end in _candidates(doc, rule):
                if start < end:
                    candidates.append((source, start, end))
                tracked = tracked or source.tracked_change
                if len(candidates) >= 2:
                    break  # Two is sufficient to prove ambiguity; bound result memory.
            if tracked:
                # 후보 가운데 변경 추적 칸이 하나라도 있으면 값을 고르지 않는다. 그 칸의 글자를
                # 모르므로 "하나만 일치"인지 "둘 이상 일치"인지도 판단할 수 없기 때문이다.
                value = ExtractedValue(**base, message=TRACKED_CHANGE_MESSAGE)
            elif not candidates:
                value = ExtractedValue(**base, message="일치하는 값이 없거나 빈 값입니다.")
            elif len(candidates) > 1:
                value = ExtractedValue(
                    **base,
                    status="ambiguous",
                    matches=len(candidates),
                    message="두 개 이상 일치합니다. 구역 또는 규칙을 좁혀 주세요.",
                )
            else:
                source, start, end = candidates[0]
                value = ExtractedValue(
                    **base,
                    status="ok",
                    value=source.text[start:end],
                    source=source,
                    start=start,
                    end=end,
                    matches=1,
                )
        except (TimeoutError, regex.error, RecursionError):
            value = ExtractedValue(
                **base,
                status="error",
                message="정규식 처리 한도를 초과했습니다. 패턴을 단순하게 줄여 주세요.",
            )
        values.append(value)
    return DocumentResult(doc.path, doc.sha256, tuple(values))


def verify(value: ExtractedValue) -> Verification:
    if value.status != "ok" or value.source is None:
        return Verification(False, "성공적으로 추출된 값만 검증할 수 있습니다.")
    try:
        doc = read_hwpx(value.document_path)
    except HwpxError as exc:
        return Verification(False, str(exc))
    if doc.sha256 != value.sha256:
        return Verification(False, "원본 파일이 추출 이후 변경되었습니다. 다시 추출하세요.")
    for source in doc.blocks:
        if (source.section, source.locator) == (value.source.section, value.source.locator):
            if source != value.source:
                return Verification(False, "원문 블록이 추출 당시 기록과 다릅니다.")
            if not 0 <= value.start < value.end <= len(source.text):
                return Verification(False, "문자 범위가 유효하지 않습니다.")
            if source.text[value.start : value.end] != value.value:
                return Verification(False, "추출 값이 원문 문자 범위와 일치하지 않습니다.")
            return Verification(
                True, "검증 완료 · SHA-256, 원문 위치, 추출 값이 모두 일치합니다.", source
            )
    return Verification(False, "원문 위치를 찾을 수 없습니다.")
