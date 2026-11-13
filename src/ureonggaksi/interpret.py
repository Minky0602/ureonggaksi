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
from dataclasses import dataclass, replace

from .extract import _normalized as normalized_label
from .models import Block, Document
from .rules import FieldRule, RecordRule, RuleSet, TargetRule
from .xlsx import TargetView

# 작업 종류 동사. "확인서"·"검토서"처럼 문서 이름에 든 글자는 동사가 아니다
FILL = re.compile(r"채워|채우|채움|넣어|넣기|넣고|넣을|입력|기입|적어|옮겨|반영|써\s?줘")
CHECK = re.compile(
    r"확인(?!서)|대조|검토(?!서)|비교|맞는지|맞나|같은지|일치|검증|틀린|다른지|차이|체크"
)
ALL_WORDS = ("전부", "전체", "모두", "모든")
KEY_AFTER = re.compile(r"^(기준|으로|로|별)")  # "이름으로 찾아", "요청번호 기준"
NEGATED = re.compile(r"^(은|는|을|를)?(빼|제외)")  # "연번은 빼고"
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
        if not text or len(text) > 30 or text.isdigit():  # 연번 같은 숫자는 항목명이 아니다
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
        overlap = sum(1 for n in names if n and _resolve(n, list(view.columns)))
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
    fill = bool(FILL.search(request))
    check = bool(CHECK.search(request))
    return "fill" if fill and not check else "check" if check and not fill else None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _mentions(request: str, names: list[str]) -> list[tuple[str, str]]:
    """요청에 글자 그대로 나온 이름과 그 뒤 몇 글자. 긴 이름 먼저 자리를 잡고, 겹치는 짧은 이름은 버린다.

    작업 동사 자리("확인해줘")는 먼저 막아 둔다. 취합표에 '확인' 열이 있어도 동사를 열 이름으로 읽지 않는다.
    """
    text = _compact(request)
    taken: list[tuple[int, int, str]] = [
        (m.start(), m.end(), "") for verbs in (FILL, CHECK) for m in verbs.finditer(text)
    ]
    for name in sorted({n for n in names if _compact(n)}, key=lambda n: -len(_compact(n))):
        needle = _compact(name)
        start = text.find(needle)
        while start >= 0:
            end = start + len(needle)
            if not any(s < end and start < e for s, e, _ in taken):
                taken.append((start, end, name))
                break
            start = text.find(needle, start + 1)
    return [(name, text[e : e + 6]) for _, e, name in sorted(taken) if name]


def _resolve(name: str, pool: list[str]) -> str | None:
    """같은 이름(정규화) → 하나뿐인 포함 관계(발급일 ↔ 발급일자). 여럿이면 정하지 않는다."""
    target = _norm(name)
    exact = [p for p in pool if _norm(p) == target]
    if exact:
        return exact[0]
    if len(target) < 2:
        return None
    near = [p for p in pool if len(_norm(p)) >= 2 and (_norm(p) in target or target in _norm(p))]
    return near[0] if len(near) == 1 else None


# ---------- 키 ----------


def _column_values(view: TargetView, column: str) -> list[str]:
    index = view.columns.index(column)
    letters = sorted(
        {c.address.rstrip("0123456789") for c in view.cells}, key=lambda s: (len(s), s)
    )
    if index >= len(letters):
        return []
    letter = letters[index]
    return [
        c.value.value.strip()
        for c in view.cells
        if c.address.rstrip("0123456789") == letter and c.value.value.strip()
    ]


def _key_candidates(
    fields: list[FieldCandidate], view: TargetView, *, distinct: bool = True
) -> list[tuple[int, str, str]]:
    """(일치 수, 문서 항목, 대상 열): 문서 값이 그 열에 글자 그대로 있고, 그 열의 값이 서로 다른 쌍."""
    result = []
    for column in view.columns:
        values = _column_values(view, column)
        if not values or (distinct and len(set(values)) < max(2, int(len(values) * 0.9))):
            continue  # 키로 말하지 않았으면 값이 거의 다른 열만 키 후보다
        pool = set(values)
        for field in fields:
            samples = [s for s in field.samples if s]
            hits = sum(1 for s in samples if s in pool)
            if hits and hits * 2 >= len(samples):
                result.append((hits, field.name, column))
    return sorted(result, key=lambda c: -c[0])


# ---------- 해석 ----------


def _pick_record(documents, view, mentions):
    """문서 항목(1문서 1건)과 명단 표(1문서 여러 건) 가운데 요청이 말한 항목을 더 많이 담은 쪽."""
    document = _document_fields(documents)
    roster = _roster_table(documents, view)
    if roster is None:
        return RecordRule("document", ""), document
    _, section, table, top, names = roster
    rows = _roster_fields(documents, section, table, top, names)

    def covered(fields):
        pool = [f.name for f in fields]
        return sum(1 for name, _ in mentions if _resolve(name, pool))

    if covered(rows) and covered(rows) >= covered(document):  # 같으면 여러 건을 다 읽는 명단
        return RecordRule("table_rows", "", section=section, table=table, header_row=top), rows
    return RecordRule("document", ""), document


def _ask_model(choose: Chooser | None, prompt: str, options: tuple[str, ...]) -> str | None:
    if choose is None or not options:
        return None
    answer = choose(prompt, (*options, NONE))
    return answer if answer in options else None


def interpret(
    request: str,
    documents: tuple[Document, ...],
    view: TargetView,
    *,
    choose: Chooser | None = None,
) -> Interpretation:
    questions: list[tuple[str, tuple[str, ...]]] = []
    columns = list(view.columns)
    probe = _mentions(request, columns + [f.name for f in _document_fields(documents)])
    record, fields = _pick_record(documents, view, probe)
    names = [f.name for f in fields]
    by_name = {f.name: f for f in fields}
    mentions = [
        (name, after)
        for name, after in _mentions(request, columns + names)
        if not NEGATED.match(after)
    ]
    work_type = _work_type(request)
    if work_type is None:
        questions.append(("work_type", ("fill", "check")))

    # 키: 요청에서 'X로 찾아·X 기준'으로 말한 것 → 데이터 일치 후보 가운데 하나뿐인 것
    candidates = _key_candidates(fields, view)
    key = None
    hint = next((n for n, after in mentions if KEY_AFTER.match(after)), None)
    if hint:
        column, field = _resolve(hint, columns), _resolve(hint, names)
        # 사용자가 키를 말했으면 열 값이 겹쳐도(명단의 접수번호처럼) 받는다
        loose = _key_candidates(fields, view, distinct=False)
        picked = [c for c in loose if column in (None, c[2]) and field in (None, c[1])]
        if picked:
            key = (picked[0][1], picked[0][2])
    if key is None and candidates:
        best = {(c[1], c[2]) for c in candidates if c[0] == candidates[0][0]}
        if len(best) == 1:
            key = next(iter(best))
    if key is None:
        questions.append(("key_column", tuple(dict.fromkeys(c[2] for c in candidates))[:3]))
    key_field, key_column = key if key else (None, None)

    # 넣을 칸: 요청에 나온 이름마다 (문서 항목, 열)을 정한다. 한쪽만 정해지면 짝을 찾는다
    pairs: list[tuple[str, str, str]] = []
    only_fields: list[str] = []
    only_columns: list[str] = []
    for name, _after in mentions:
        if name == hint:
            continue
        column = _resolve(name, [c for c in columns if c != key_column])
        field = _resolve(name, [n for n in names if n != key_field])
        if field is not None and column is not None:
            pairs.append((field, column, "match"))
        elif field is not None:
            only_fields.append(field)
        elif column is not None:
            only_columns.append(column)
    if only_fields and len(only_fields) == len(only_columns):
        # "A를 B 칸에 넣어", "표 B가 문서 A와 맞는지": 한쪽 이름만 맞은 것끼리 나온 순서대로 짝짓는다
        pairs += [(f, c, "match") for f, c in zip(only_fields, only_columns, strict=True)]
        only_fields, only_columns = [], []
    excluded: list[str] = []
    for field in only_fields:
        taken = {p[1] for p in pairs}
        options = tuple(c for c in columns if c != key_column and c not in taken)
        answer = _ask_model(choose, _column_prompt(request, by_name[field], options, view), options)
        if answer:
            pairs.append((field, answer, "model"))
        else:
            excluded.append(field)
    for column in only_columns:
        taken = {p[0] for p in pairs}
        options = tuple(n for n in names if n != key_field and n not in taken)
        answer = _ask_model(choose, _field_prompt(request, column, options, by_name, view), options)
        if answer:
            pairs.append((answer, column, "model"))
        else:
            excluded.append(column)
    if not pairs and any(w in request for w in ALL_WORDS):
        for column in columns:
            field = _resolve(column, names)
            if column != key_column and field not in (None, key_field):
                pairs.append((field, column, "match"))
    seen: set[str] = set()
    mapped = []
    for field, column, source in pairs:
        if column not in seen:
            seen.add(column)
            mapped.append((field, column, source))
    if not mapped:
        questions.append(("target_column", tuple(c for c in columns if c != key_column)[:3]))
    record = replace(record, key_field=key_field or "")
    return Interpretation(
        work_type, record, key, tuple(mapped), tuple(questions), tuple(excluded), tuple(fields)
    )


def _examples(values) -> str:
    shown = ", ".join(dict.fromkeys(v for v in values if v))
    return f" (예: {shown})" if shown else ""


def _field_prompt(request, column, options, by_name, view) -> str:
    lines = [
        f"요청: {request}",
        f"취합표 열 '{column}'{_examples(_column_values(view, column)[:3])}에 넣을 문서 항목을 고르세요. "
        f"맞는 항목이 없으면 '{NONE}'.",
    ]
    for option in options:
        lines.append(f"- {option}{_examples(by_name[option].samples[:2])}")
    return "\n".join(lines)


def _column_prompt(request, field: FieldCandidate, options, view) -> str:
    lines = [
        f"요청: {request}",
        f"문서 항목 '{field.name}'{_examples(field.samples[:2])}을 넣을 취합표 열을 고르세요. "
        f"맞는 열이 없으면 '{NONE}'.",
    ]
    lines += [f"- {o}{_examples(_column_values(view, o)[:3])}" for o in options]
    return "\n".join(lines)


def build_rule(result: Interpretation, view: TargetView) -> RuleSet | None:
    """해석이 완전할 때만 RuleSet을 만든다(질문이 남았으면 None)."""
    if result.questions or result.key is None or result.work_type is None:
        return None
    by_name = {f.name: f for f in result.candidates}
    rules = [by_name[result.key[0]].rule]
    for name, column, _source in result.fields:
        rules.append(replace(by_name[name].rule, target_column=column))
    last = max(view.data_rows[1], view.header_row)
    target = TargetRule(view.sheet, view.header_row, (view.header_row + 1, last), result.key[1])
    return RuleSet(result.record, tuple(rules), target, result.work_type)
