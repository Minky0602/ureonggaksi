"""Turn a one-line request into a rule draft — code first, the local model only for leftovers (P04, DIF-030).

S01 showed a 4B model mixes up roles when it picks work type, key and fields at once (12.5%). Code
decides only what it can decide without guessing (independent review of P04 ① found guesses that wrote
wrong cells):
- work type from verbs ("채워" vs "확인"); both, neither or a negated fill → ask.
- the key the user named ("이름으로 찾아"), linked by values that appear verbatim in the column; an unnamed
  key only when one identifier-like column clearly wins.
- fields ↔ columns only when names are equal after normalization (DIF-010 labels vs headers), or when the
  user points at the cell ("A를 B 칸에").
Everything else goes to the model, one name at a time, as a choice from an enum; without a model it is
left out and reported.
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
FILL_NEGATED = re.compile(r"(채우|넣|입력하|기입하|적|옮기)지\s*(마|말|않)")  # "아직 채우지 마"
CHECK = re.compile(
    r"확인(?!서)|대조|검토(?!서)|비교|맞는지|맞나|같은지|일치|검증|틀린|다른지|차이|체크"
    r"|괜찮은지|됐나|됐는지|제대로"
)
# 이름 바로 뒤(공백 제거) 글자로 읽는 표현
KEY_AFTER = re.compile(r"^(기준|으로|로|별)")  # "이름으로 찾아", "요청번호 기준"
NEGATED = re.compile(
    r"^(은|는|을|를|이|가|도|까지는|까지|만은)?"
    r"(빼|제외|말고|그대로|손대지|건드리지|하지마|하지말|두고|놔두|참고|채우지마|넣지마)"
)
JOINED = re.compile(r"^(이랑|랑|하고|와|과|,|및|그리고)")  # 나열: 부정이 앞 이름까지 걸린다
SLOT = re.compile(r"^(칸|열|란)")  # "A를 B 칸에 넣어"
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


def _related(name: str, columns) -> bool:
    """명단 표를 알아보는 데만 쓰는 느슨한 비교(같은 이름이거나 한쪽이 다른 쪽을 포함). 짝을 확정하지 않는다."""
    a = _norm(name)
    return len(a) >= 2 and any(
        a == _norm(c) or (len(_norm(c)) >= 2 and (a in _norm(c) or _norm(c) in a)) for c in columns
    )


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
        overlap = sum(1 for n in names if n and _related(n, view.columns))
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
    """채우기·검토 동사가 하나만 있을 때만 정한다. "채우지 마"처럼 부정한 채우기는 채우기가 아니다."""
    fill = bool(FILL.search(FILL_NEGATED.sub(" ", request)))
    check = bool(CHECK.search(request))
    return "fill" if fill and not check else "check" if check and not fill else None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _variants(name: str) -> set[str]:
    """요청에서 찾을 글자: 이름 그대로와 괄호(단위)를 뺀 것."""
    bare = re.sub(r"\([^)]*\)|（[^）]*）", "", name).strip()
    return {v for v in (name.strip(), bare) if v}


def _mentions(request: str, names: list[str]) -> list[tuple[str, str]]:
    """요청에 단어로 나온 이름과 그 뒤 글자(공백 제거, 12자).

    - 단어 중간은 이름이 아니다("전화번호"의 '번호'). 앞 글자가 한글·영문·숫자면 건너뛴다.
    - 긴 이름이 먼저 자리를 잡고, 겹치는 짧은 이름은 버린다.
    - 작업 동사 자리("확인해줘")는 먼저 막아 둔다. 취합표에 '확인' 열이 있어도 동사를 열로 읽지 않는다.
    """
    taken: list[tuple[int, int, str]] = [
        (m.start(), m.end(), "") for verbs in (FILL, CHECK) for m in verbs.finditer(request)
    ]
    pairs = sorted(
        {(v, n) for n in names for v in _variants(n)}, key=lambda p: -len(_compact(p[0]))
    )
    for variant, name in pairs:
        pattern = r"(?<![가-힣A-Za-z0-9])" + r"\s*".join(map(re.escape, _compact(variant)))
        for match in re.finditer(pattern, request):
            start, end = match.span()
            if not any(s < end and start < e for s, e, _ in taken):
                taken.append((start, end, name))  # 같은 이름이 여러 번 나오면 모두 읽는다
    return [(name, _compact(request[e : e + 20])[:12]) for _, e, name in sorted(taken) if name]


def _resolve(name: str, pool: list[str]) -> str | None:
    """정규화(공백·끝 콜론·괄호 단위·대소문자)한 이름이 같은 것이 하나뿐일 때만. 둘 이상이면 정하지 않는다.

    포함 관계(발급일↔발급일자)는 코드가 확정하지 않는다. 짧은 이름이 엉뚱한 열에 붙기 때문이다
    ('번호'↔'전화번호', P04 ① 검수 H2). 그런 짝은 모델의 선택지나 질문으로 간다.
    """
    exact = [p for p in pool if _norm(p) == _norm(name)]
    return exact[0] if len(exact) == 1 else None


def _negated(mentions: list[tuple[str, str]]) -> set[str]:
    """ "직급이랑 점수는 빼고": 부정 표현이 붙은 이름과, 그 앞에 '랑·하고·,'로 이어진 이름들."""
    out: set[str] = set()
    chain: list[str] = []
    for name, after in mentions:
        chain.append(name)
        if NEGATED.match(after):
            out.update(chain)
        if not JOINED.match(after):
            chain = []
    return out


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


def _identifier_like(values: list[str]) -> bool:
    """키로 추정해도 되는 값: 연번·O/X·작은 수처럼 우연히 겹치기 쉬운 값이 아니다."""
    return not all(len(v) <= 1 or (v.isdigit() and len(v) <= 3) for v in values)


def _key_candidates(
    fields: list[FieldCandidate], view: TargetView, *, named: bool = False
) -> list[tuple[int, str, str]]:
    """(일치 수, 문서 항목, 대상 열): 문서 값이 그 열에 글자 그대로 있는 쌍.

    사용자가 키를 말했으면(named) 일치가 하나라도 있으면 된다. 추정할 때는 열 값이 서로 다르고(90%),
    식별자처럼 보이고, 문서 값의 80% 이상이 그 열에 있어야 한다(검수 H5: 연번 1·2·3과 차수 1·2·3).
    """
    result = []
    for column in view.columns:
        values = _column_values(view, column)
        if not values:
            continue
        if not named and (
            len(values) < 2
            or len(set(values)) < max(2, int(len(values) * 0.9))
            or not _identifier_like(values)
        ):
            continue
        pool = set(values)
        for field in fields:
            samples = [s for s in field.samples if s]
            hits = sum(1 for s in samples if s in pool)
            if hits and (named or hits >= max(2, 0.8 * len(samples))):
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
    """코드가 확정하는 것은 이름이 정규화 후 완전히 같은 짝과, 사용자가 지목한 키뿐이다(P04 ① 검수).

    그 밖의 짝(이름이 다른 것)은 모델이 선택지에서 고르고, 모델이 없거나 '없음'이면 빼고 소명한다.
    """
    questions: list[tuple[str, tuple[str, ...]]] = []
    columns = list(view.columns)
    probe = _mentions(request, columns + [f.name for f in _document_fields(documents)])
    record, fields = _pick_record(documents, view, probe)
    names = [f.name for f in fields]
    by_name = {f.name: f for f in fields}
    found = _mentions(request, columns + names)
    skip = _negated(found)
    mentions = [(n, a) for n, a in found if n not in skip]
    work_type = _work_type(request)
    if work_type is None:
        questions.append(("work_type", ("fill", "check")))

    # 키: 'X로 찾아·X 기준'으로 말한 이름 → 없으면 엄격한 데이터 일치에서 1위가 뚜렷할 때만
    key = None
    hint = next((n for n, after in mentions if KEY_AFTER.match(after)), None)
    if hint:
        column, field = _resolve(hint, columns), _resolve(hint, names)
        picked = [
            c
            for c in _key_candidates(fields, view, named=True)
            if column in (None, c[2]) and field in (None, c[1])
        ]
        if picked and (len(picked) == 1 or picked[0][0] > picked[1][0]):
            key = (picked[0][1], picked[0][2])
    else:
        candidates = _key_candidates(fields, view)
        if candidates and (len(candidates) == 1 or candidates[0][0] > 2 * candidates[1][0]):
            key = (candidates[0][1], candidates[0][2])
    if key is None:
        options = tuple(dict.fromkeys(c[2] for c in _key_candidates(fields, view, named=True)))
        questions.append(("key_column", options[:3]))
    key_field, key_column = key if key else (None, None)

    # 넣을 칸
    pairs: list[tuple[str, str, str]] = []
    only_fields: list[str] = []
    only_columns: list[str] = []
    linked: dict[str, str] = {}  # "A를 B 칸에": 바로 뒤에 '칸·열'이 붙은 열 → 그 앞의 문서 항목
    previous_field = None
    for name, after in mentions:
        if name == hint:
            continue
        column = _resolve(name, [c for c in columns if c != key_column])
        field = _resolve(name, [n for n in names if n != key_field])
        if column is not None and previous_field and SLOT.match(after):
            linked[column] = previous_field  # "평가점수를 이수시간 칸에": 지목한 칸이 이긴다
        elif field is not None and column is not None:
            pairs.append((field, column, "match"))
        elif field is not None:
            if field not in only_fields:
                only_fields.append(field)
            previous_field = field
            continue
        elif column is not None and column not in only_columns:
            only_columns.append(column)
        previous_field = None
    for column, field in linked.items():  # 사용자가 칸을 지목했다
        pairs = [p for p in pairs if p[0] != field]
        if field in only_fields:
            only_fields.remove(field)
        pairs.append((field, column, "match"))
    pairs = list(dict.fromkeys(pairs))
    for field in only_fields:
        taken = {p[1] for p in pairs}
        options = _order(field, tuple(c for c in columns if c != key_column and c not in taken))
        answer = _ask_model(choose, _column_prompt(request, by_name[field], options, view), options)
        if answer:
            pairs.append((field, answer, "model"))
    for column in only_columns:
        taken = {p[0] for p in pairs}
        options = _order(column, tuple(n for n in names if n != key_field and n not in taken))
        answer = _ask_model(choose, _field_prompt(request, column, options, by_name, view), options)
        if answer:
            pairs.append((answer, column, "model"))

    # 한 열에 둘 이상이 몰리면 어느 것도 확정하지 않는다(검수 H7)
    count: dict[str, int] = {}
    for _, column, _ in pairs:
        count[column] = count.get(column, 0) + 1
    mapped = tuple(p for p in pairs if count[p[1]] == 1)
    placed = {p[0] for p in mapped} | {p[1] for p in mapped}
    requested = [*only_fields, *only_columns, *(p[0] for p in pairs), *(p[1] for p in pairs)]
    excluded = tuple(dict.fromkeys(n for n in requested if n not in placed))
    if not mapped:
        questions.append(("target_column", tuple(c for c in columns if c != key_column)[:3]))
    record = replace(record, key_field=key_field or "")
    return Interpretation(work_type, record, key, mapped, tuple(questions), excluded, tuple(fields))


def _examples(values) -> str:
    shown = ", ".join(dict.fromkeys(v for v in values if v))
    return f" (예: {shown})" if shown else ""


def _similar(name: str, options) -> list[str]:
    """이름이 포함 관계인 선택지(요청일 ↔ 요청일자). 코드가 확정하지 않고 모델에 단서로만 준다."""
    a = _norm(name)
    return [
        o
        for o in options
        if len(a) >= 2 and len(_norm(o)) >= 2 and (a in _norm(o) or _norm(o) in a)
    ]


def _order(name: str, options: tuple[str, ...]) -> tuple[str, ...]:
    similar = _similar(name, options)
    return (*similar, *(o for o in options if o not in similar))


def _hint(name: str, options) -> list[str]:
    similar = _similar(name, options)
    return [f"이름이 비슷한 것: {', '.join(similar)} (뜻이 같을 때만 고르세요)"] if similar else []


def _field_prompt(request, column, options, by_name, view) -> str:
    lines = [
        f"요청: {request}",
        f"취합표 열 '{column}'{_examples(_column_values(view, column)[:3])}에 넣을 문서 항목을 고르세요. "
        f"맞는 항목이 없으면 '{NONE}'.",
    ]
    lines += _hint(column, options)
    for option in options:
        lines.append(f"- {option}{_examples(by_name[option].samples[:2])}")
    return "\n".join(lines)


def _column_prompt(request, field: FieldCandidate, options, view) -> str:
    lines = [
        f"요청: {request}",
        f"문서 항목 '{field.name}'{_examples(field.samples[:2])}을 넣을 취합표 열을 고르세요. "
        f"맞는 열이 없으면 '{NONE}'.",
    ]
    lines += _hint(field.name, options)
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
