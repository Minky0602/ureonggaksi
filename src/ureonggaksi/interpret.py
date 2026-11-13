"""Turn a one-line request into a rule draft — code first, the local model only for leftovers (P04, DIF-030).

S01 showed a 4B model mixes up roles when it picks work type, key and fields at once (12.5%). Most of
that is decidable from data, so code decides:
- work type from verbs ("채워" vs "확인"); unclear → ask.
- the key from data: a document field whose values appear verbatim in a target column of distinct values.
- fields ↔ columns by label text that repeats across documents (DIF-010) and equals a header.
Only fields that no rule can place go to the model, one at a time, as a choice from an enum.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from .extract import _normalized as normalized_label
from .models import Block, Document
from .rules import FieldRule, RecordRule, RuleSet, TargetRule
from .xlsx import TargetView

FILL_VERBS = (
    "채워",
    "채우",
    "채움",
    "넣어",
    "넣기",
    "넣고",
    "입력",
    "기입",
    "적어",
    "옮겨",
    "반영",
    "써 줘",
    "써줘",
)
CHECK_VERBS = (
    "확인",
    "대조",
    "검토",
    "비교",
    "맞는지",
    "맞나",
    "일치",
    "검증",
    "틀린",
    "다른지",
    "차이",
)
ALL_WORDS = ("전부", "전체", "모두", "모든", "다 ")
KEY_MARKERS = (
    "기준",
    "로 찾",
    "으로 찾",
    "로 맞",
    "으로 맞",
    "로 행",
    "으로 행",
    "로 연결",
    "으로 연결",
    "별로",
)
NONE = "없음"

# 모델 호출: (질문 글, 선택지) → 선택지 하나 또는 None
Chooser = Callable[[str, tuple[str, ...]], str | None]


@dataclass(frozen=True)
class FieldCandidate:
    name: str  # 문서의 항목명(라벨·머리글 글자)
    rule: FieldRule  # 그 값을 찾는 추출 규칙(대상 열은 비어 있음)
    samples: tuple[str, ...]  # 문서(또는 행)마다 읽은 값


@dataclass(frozen=True)
class Interpretation:
    work_type: str | None
    record: RecordRule
    key: tuple[str, str] | None  # (문서 항목, 대상 열)
    fields: tuple[tuple[str, str, str], ...]  # (문서 항목, 대상 열, 출처 "match"|"model")
    questions: tuple[tuple[str, tuple[str, ...]], ...]  # (슬롯, 후보) — 6.2 질문 재료
    excluded: tuple[str, ...] = ()  # 요청했지만 대응 열이 없는 항목·열
    candidates: tuple[
        FieldCandidate, ...
    ] = ()  # 문서에서 찾은 항목들(build_rule이 추출 규칙을 가져간다)


def _norm(text: str) -> str:
    """비교용: 공백·끝 콜론 제거, 괄호 안(단위 등) 제거, 대소문자 무시."""
    text = re.sub(r"\([^)]*\)|（[^）]*）", "", text or "")
    return normalized_label(text)


# ---------- 항목 찾기 ----------


def _cells(doc: Document) -> dict[tuple[str, int, int, int], Block]:
    return {(b.section, b.table, b.row, b.col): b for b in doc.blocks if b.kind == "cell"}


def _right_or_below(cells, block: Block):
    right = cells.get((block.section, block.table, block.row, block.col + block.col_span))
    below = cells.get((block.section, block.table, block.row + block.row_span, block.col))
    return right, below


def _document_fields(documents: tuple[Document, ...]) -> list[FieldCandidate]:
    """같은 위치에 같은 글자가 모든 문서에 있으면 항목명, 그 오른쪽(없으면 아래) 칸이 값이다."""
    first, maps = documents[0], [_cells(d) for d in documents]
    found: dict[str, FieldCandidate] = {}
    label_counts: dict[str, int] = {}
    for block in first.blocks:
        if block.kind == "cell" and block.text.strip():
            label_counts[_norm(block.text)] = label_counts.get(_norm(block.text), 0) + 1
    for key, block in maps[0].items():
        text = block.text.strip()
        if not text or len(text) > 30:
            continue
        if any(m.get(key) is None or m[key].text.strip() != text for m in maps[1:]):
            continue  # 문서마다 다르면 값이지 항목명이 아니다(DIF-010)
        for direction in ("right", "below"):
            neighbours = [
                _right_or_below(m, m[key])[0 if direction == "right" else 1] for m in maps
            ]
            if any(n is None for n in neighbours):
                continue
            values = tuple(n.text.strip() for n in neighbours)
            label_like = all(v == values[0] for v in values) and len(documents) > 1
            if label_like or not any(values):
                continue  # 이웃도 모든 문서에서 같으면 그것도 항목명이다
            unique = label_counts.get(_norm(text), 0) == 1
            rule = (
                FieldRule(text, "label", query=text, direction=direction)
                if unique
                else FieldRule(
                    text,
                    "cell",
                    section=neighbours[0].section,
                    table=neighbours[0].table,
                    row=neighbours[0].row,
                    col=neighbours[0].col,
                )
            )
            found.setdefault(_norm(text), FieldCandidate(text, rule, values))
            break
    # 본문의 "항목: 값" 문단
    pattern = re.compile(r"^\s*([^:：\s][^:：]{0,19}?)\s*[:：]\s*(\S.*?)\s*$")
    paragraphs = [[b for b in d.blocks if b.kind == "paragraph"] for d in documents]
    for block in paragraphs[0]:
        match = pattern.match(block.text)
        if not match:
            continue
        label = match.group(1)
        values = []
        for blocks in paragraphs:
            hit = next(
                (
                    pattern.match(b.text)
                    for b in blocks
                    if (m := pattern.match(b.text)) and m.group(1) == label
                ),
                None,
            )
            values.append(hit.group(2) if hit else "")
        if _norm(label) in found or not any(values):
            continue
        query = rf"^\s*{re.escape(label)}\s*[:：]\s*(.+?)\s*$"
        found[_norm(label)] = FieldCandidate(
            label, FieldRule(label, "regex", query=query, group=1), tuple(values)
        )
    return list(found.values())


def _roster_table(documents: tuple[Document, ...], view: TargetView):
    """명단형 표: 첫 행이 모든 문서에서 같고(머리글), 그 머리글이 대상 헤더와 둘 이상 맞는 표."""
    headers = {_norm(c) for c in view.columns}
    first = documents[0]
    tables = sorted({(b.section, b.table) for b in first.blocks if b.kind == "cell"})
    best = None
    for section, table in tables:
        cells = [
            b for b in first.blocks if b.kind == "cell" and (b.section, b.table) == (section, table)
        ]
        top = min(b.row for b in cells)
        head = [b for b in cells if b.row == top]
        names = [b.text.strip() for b in sorted(head, key=lambda b: b.col)]
        rows_below = {b.row for b in cells if b.row > top}
        if len(rows_below) < 1:
            continue
        same_everywhere = all(
            [
                b.text.strip()
                for b in sorted(
                    (
                        c
                        for c in d.blocks
                        if c.kind == "cell"
                        and (c.section, c.table) == (section, table)
                        and c.row == top
                    ),
                    key=lambda c: c.col,
                )
            ]
            == names
            for d in documents[1:]
        )
        overlap = sum(1 for n in names if _norm(n) in headers)
        if same_everywhere and overlap >= 2 and (best is None or overlap > best[0]):
            best = (overlap, section, table, top, names)
    return best


def _roster_fields(documents, section, table, top, names) -> list[FieldCandidate]:
    found = []
    for name in names:
        if not name:
            continue
        values = []
        for d in documents:
            cells = [
                b for b in d.blocks if b.kind == "cell" and (b.section, b.table) == (section, table)
            ]
            header = next((b for b in cells if b.row == top and b.text.strip() == name), None)
            if header is None:
                continue
            values += [b.text.strip() for b in cells if b.row > top and b.col == header.col]
        found.append(FieldCandidate(name, FieldRule(name, "column", query=name), tuple(values)))
    return found


# ---------- 요청 읽기 ----------


def _work_type(request: str) -> str | None:
    fill = any(v in request for v in FILL_VERBS)
    check = any(v in request for v in CHECK_VERBS)
    return "fill" if fill and not check else "check" if check and not fill else None


def _mentions(request: str, names) -> list[str]:
    """요청에 그대로 나온 이름들(긴 이름 먼저, 겹치면 긴 쪽만)."""
    text = re.sub(r"\s+", "", request)
    hits = []
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        compact = re.sub(r"\s+", "", name)
        if compact and compact in text and not any(compact in h for h in hits):
            hits.append(compact)
    return [n for n in names if re.sub(r"\s+", "", n or "") in hits]


def _key_hint(request: str, names: list[str]) -> str | None:
    """'X로 찾아', 'X 기준' 처럼 키로 말한 이름."""
    compact = re.sub(r"\s+", "", request)
    for name in sorted(names, key=len, reverse=True):
        n = re.sub(r"\s+", "", name)
        index = compact.find(n)
        if index >= 0 and any(
            compact[index + len(n) : index + len(n) + 6].startswith(m.replace(" ", ""))
            for m in KEY_MARKERS
        ):
            return name
    return None


# ---------- 키 ----------


def _column_values(view: TargetView, column: str) -> list[str]:
    index = view.columns.index(column)
    letters = {c.address.rstrip("0123456789") for c in view.cells}
    ordered = sorted(letters, key=lambda s: (len(s), s))
    if index >= len(ordered):
        return []
    letter = ordered[index]
    return [
        c.value.value.strip()
        for c in view.cells
        if c.address.rstrip("0123456789") == letter and c.value.value.strip()
    ]


def _key_candidates(fields: list[FieldCandidate], view: TargetView) -> list[tuple[int, str, str]]:
    """(일치 수, 문서 항목, 대상 열): 문서 값이 그 열에 글자 그대로 있고, 그 열의 값이 서로 다른 쌍."""
    result = []
    for column in view.columns:
        values = _column_values(view, column)
        if not values or len(set(values)) < max(2, int(len(values) * 0.9)):
            continue
        pool = set(values)
        for field in fields:
            samples = [s for s in field.samples if s]
            hits = sum(1 for s in samples if s in pool)
            if hits and hits * 2 >= len(samples):
                result.append((hits, field.name, column))
    return sorted(result, reverse=True)


# ---------- 해석 ----------


def interpret(
    request: str,
    documents: tuple[Document, ...],
    view: TargetView,
    *,
    choose: Chooser | None = None,
) -> Interpretation:
    questions: list[tuple[str, tuple[str, ...]]] = []
    roster = _roster_table(documents, view)
    if roster:
        _, section, table, top, names = roster
        fields = _roster_fields(documents, section, table, top, names)
        record = RecordRule("table_rows", "", section=section, table=table, header_row=top)
    else:
        fields = _document_fields(documents)
        record = RecordRule("document", "")
    by_name = {_norm(f.name): f for f in fields}
    work_type = _work_type(request)
    if work_type is None:
        questions.append(("work_type", ("fill", "check")))

    # 키: 요청에서 말한 것 → 데이터 일치 후보
    candidates = _key_candidates(fields, view)
    mentioned_columns = _mentions(request, list(view.columns))
    mentioned_fields = _mentions(request, [f.name for f in fields])
    hint = _key_hint(request, mentioned_columns + mentioned_fields)
    key = None
    if hint:
        picked = [
            c
            for c in candidates
            if hint in (c[1], c[2]) or _norm(hint) in (_norm(c[1]), _norm(c[2]))
        ]
        if picked:
            key = (picked[0][1], picked[0][2])
    if key is None:
        best = [c for c in candidates if c[0] == candidates[0][0]] if candidates else []
        if len({(c[1], c[2]) for c in best}) == 1:
            key = (best[0][1], best[0][2])
        else:
            questions.append(("key_column", tuple(dict.fromkeys(c[2] for c in candidates))[:3]))

    # 넣을 열: 요청에서 말한 열 → 요청에서 말한 항목의 열 → "전부"
    key_column = key[1] if key else None
    key_field = key[0] if key else None
    wanted: list[str] = [c for c in mentioned_columns if c != key_column]
    for name in mentioned_fields:
        if _norm(name) == _norm(key_field or "") or any(_norm(name) == _norm(c) for c in wanted):
            continue
        same = [c for c in view.columns if _norm(c) == _norm(name) and c != key_column]
        if same:
            wanted.append(same[0])
        elif not any(_norm(name) == _norm(c) for c in mentioned_columns):
            wanted.append(f"?{name}")  # 열 이름이 다른 문서 항목: 모델이 열을 고른다
    if not wanted and any(w in request for w in ALL_WORDS):
        wanted = [c for c in view.columns if c != key_column and _norm(c) in by_name]
    mapped: list[tuple[str, str, str]] = []
    excluded: list[str] = []
    for column in wanted:
        if column.startswith("?"):
            name = column[1:]
            options = tuple(
                c for c in view.columns if c != key_column and c not in {m[1] for m in mapped}
            )
            answer = (
                choose(
                    _column_prompt(request, name, by_name.get(_norm(name)), options),
                    (*options, NONE),
                )
                if choose
                else None
            )
            if answer and answer != NONE:
                mapped.append((by_name[_norm(name)].name, answer, "model"))
            else:
                excluded.append(name)
            continue
        field = by_name.get(_norm(column))
        if field is not None:
            mapped.append((field.name, column, "match"))
            continue
        options = tuple(
            f.name for f in fields if f.name != key_field and f.name not in {m[0] for m in mapped}
        )
        answer = (
            choose(_field_prompt(request, column, view, options, by_name), (*options, NONE))
            if choose
            else None
        )
        if answer and answer != NONE:
            mapped.append((answer, column, "model"))
        else:
            excluded.append(column)
    if not mapped:
        questions.append(("target_column", tuple(c for c in view.columns if c != key_column)[:3]))
    record = RecordRule(
        record.kind, key_field or "", record.section, record.table, record.header_row
    )
    return Interpretation(
        work_type, record, key, tuple(mapped), tuple(questions), tuple(excluded), tuple(fields)
    )


def _field_prompt(request, column, view, options, by_name) -> str:
    examples = {o: ", ".join(s for s in by_name[_norm(o)].samples[:2] if s) for o in options}
    lines = [
        f"요청: {request}",
        f"취합표 열 '{column}'에 넣을 문서 항목을 고르세요. 맞는 항목이 없으면 '{NONE}'.",
    ]
    lines += [f"- {o} (예: {examples[o]})" for o in options]
    return "\n".join(lines)


def _column_prompt(request, name, field, options) -> str:
    sample = ", ".join(s for s in (field.samples[:2] if field else ()) if s)
    lines = [
        f"요청: {request}",
        f"문서 항목 '{name}'(예: {sample})을 넣을 취합표 열을 고르세요. 맞는 열이 없으면 '{NONE}'.",
    ]
    lines += [f"- {o}" for o in options]
    return "\n".join(lines)


def build_rule(result: Interpretation, view: TargetView) -> RuleSet | None:
    """해석이 완전할 때만 RuleSet을 만든다(질문이 남았으면 None)."""
    if result.questions or result.key is None or result.work_type is None:
        return None
    by_name = {f.name: f for f in result.candidates}
    key_field = by_name[result.key[0]].rule
    rules = [key_field]
    for name, column, _source in result.fields:
        base = by_name[name].rule
        rules.append(FieldRule(**{**base.__dict__, "target_column": column}))
    last = view.data_rows[1]
    target = TargetRule(
        view.sheet,
        view.header_row,
        (view.header_row + 1, max(last, view.header_row)),
        result.key[1],
    )
    return RuleSet(result.record, tuple(rules), target, result.work_type)
