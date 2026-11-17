"""Turn a one-line request into a rule draft — code first, the local model only for leftovers (P04, DIF-030).

S01 showed a 4B model mixes up roles when it picks work type, key and fields at once (12.5%). Code
decides only what it can decide without guessing (independent review of P04 ① found guesses that wrote
wrong cells):
- work type from verbs ("채워" vs "확인"); both, neither or a negated fill → ask.
- the key the user named ("이름으로 찾아"), linked by values that appear verbatim in the column; an unnamed
  key only when one identifier-like column clearly wins.
- fields ↔ columns only when names are equal after normalization (DIF-010 labels vs headers), or when the
  user points at the cell ("A를 B 칸에").
- field pairs only for requests made entirely of known words (names with particles, one verb, key and
  sheet words). Any other word ("생략", "패스", a second clause) means nothing is confirmed; it is asked.
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
FILL_NEGATED = re.compile(  # 하지 않는 채우기: "채우지 마", "채워주지 마", "안 채워도 돼", "넣을 필요 없어"
    r"(채우|채워|넣|넣어|입력하|기입하|적|적어|옮기|옮겨|써)\s*(주)?\s*지\s*(는|도|만)?\s*(마|말|않)"
    r"|안\s*(채|넣|입력|기입|적|옮|써)|필요\s*(는|도)?\s*없|맙시다|(채우|넣으|입력하|기입하|적으|쓰)면\s*안"
)
CHECK = re.compile(
    r"확인(?!서)|대조|검토(?!서)|비교|맞는지|맞나|같은지|일치|검증|틀린|다른지|차이|체크|점검|검사"
    r"|괜찮은지|됐나|됐는지|제대로|봐\s?줘|살펴|없나|맞아|됐어|있어|있나|\?"
)  # "기입 상태 봐줘"·"입력 누락 없나": 채우기 낱말이 명사로 쓰인 검토 요청(P04 ① 2차 검수)
# 이름 바로 뒤(공백 제거) 글자로 읽는 표현
KEY_AFTER = re.compile(
    r"^(기준|으로|로)"
)  # "이름으로 찾아", "요청번호 기준". "직급별로"는 묶기라 키가 아니다
NEGATED = re.compile(
    r"^(칸|열|란)?(에는|에|은|는|을|를|이|가|도|까지는|까지|만은|만)?"
    r"(빼(?!놓지|먹지)|제외|말고|그대로(?!옮|넣|채|입력|적|기입|반영)|손대지|건드리지|하지마|하지말|두고|놔두|참고|비워|비우"
    r"|채우지마|채우지말|넣지마|넣지말|보류|생략|취소)"
)  # "번호 칸은 비워 둬", "비고 칸에 넣지 말고"
# 코드가 항목 짝을 확정해도 되는 요청은 모든 낱말이 '이름+조사'이거나 아래 허용 낱말인 것뿐이다.
# 빼기·미루기·두 절 요청처럼 위험한 표현을 목록으로 막으면 목록 밖 표현이 계속 새었다(P04 ① 2~4차 검수).
# 그래서 아는 말만으로 된 요청만 확정하고, 모르는 말이 하나라도 있으면 묻는다.
PARTICLE = r"(?:이랑|랑|하고|과|와|및|을|를|이|가|은|는|도|의|만|으로|로|에서|에)"
ENDING = r"(?:줘|줘요|주세요|주십시오|주라|줄래|줄래요)"  # 동사 끝(부탁)
ALLOWED_WORD = (
    rf"(?:채워|넣어|입력해|기입해|적어|옮겨|반영해|써){ENDING}?"
    rf"|(?:확인|대조|비교|검토|검증|체크|점검|검사)(?:해|해서|하기)?{ENDING}?"
    r"|맞는지|같은지|일치하는지|다른지"
    r"|기준|기준으로|찾아|찾아서|찾고|찾구|행을"
    r"|(?:칸|열|란)에?"
    rf"|(?:취합표|현황표|명단|표|시트|엑셀|문서|서류|신청서|확인서|요청서){PARTICLE}?"
    r"|빈칸|빈칸을|빈칸에|다|모두|전부|각각"  # "다 채워줘": 빼는 동사가 오면 그 동사가 모르는 말이라 묻는다
    rf"|{ENDING}|요|좀|부탁해|부탁해요|부탁합니다|부탁드립니다|감사합니다|감사해요|고맙습니다|[ㅠㅜ]+"
)
NAME_MARK = "\x00"
# 낱말 끝에 붙은 '좀'("체크좀")은 뜻을 바꾸지 않는다
SIMPLE_TOKEN = re.compile(
    rf"(?:(?:{NAME_MARK}{PARTICLE}*)+|(?:{NAME_MARK}{PARTICLE}*)*(?:{ALLOWED_WORD}))좀?"
)
JOINED = re.compile(r"^(이랑|랑|하고|와|과|,|및|그리고)")  # 나열: 부정이 앞 이름까지 걸린다
# 이름 바로 뒤의 자리 낱말은 지목이다("B 칸에", "B 빈칸에", "B의 빈칸", "B 칸", "B 표에", PR #18 4차 검수 H).
# "A를 B …"로 짝이 맞으면 지목대로, 아니면 모두 묻는다
SLOT = re.compile(r"^의?(빈칸|칸|열|란|셀|표|시트)")
OBJECT = re.compile(
    r"^(을|를)\x00"
)  # 지목 규칙의 A: '를' 바로 뒤에 칸 이름이 온다(사이에 동사 없음)
DIRECTION = re.compile(
    r"^(에서|에|으로|로)"
)  # "전화번호에", "휴대전화로": 지목인데 "A를 B 칸에" 꼴이 아님
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
            if label_counts.get(_norm(text), 0) > 1:
                break  # 같은 라벨이 둘(추천인 성명·피추천인 성명)이면 어느 것인지 모른다(3차 D01)
            rule = FieldRule(text, "label", query=text, direction=direction)
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
    """채우기·검토 동사가 하나만 있을 때만 정한다. 하지 않는 채우기("채우지 마")는 채우기가 아니고,
    그것과 다른 채우기가 함께 있으면("직급 채워줘, 이수시간은 안 채워도 돼") 묻는다."""
    negated = bool(FILL_NEGATED.search(request))
    fill = bool(FILL.search(FILL_NEGATED.sub(" ", request)))
    if negated and fill:
        return None
    check = bool(CHECK.search(request))
    return "fill" if fill and not check else "check" if check and not fill else None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _variants(name: str) -> set[str]:
    """요청에서 찾을 글자: 이름 그대로와 괄호(단위)를 뺀 것."""
    bare = re.sub(r"\([^)]*\)|（[^）]*）", "", name).strip()
    return {v for v in (name.strip(), bare) if v}


def _spans(request: str, names: list[str]) -> list[tuple[int, int, str]]:
    """요청에 단어로 나온 이름의 자리(시작, 끝, 이름). 작업 동사 자리는 이름 ''로 막아 둔다.

    - 단어 중간은 이름이 아니다("전화번호"의 '번호'). 앞 글자가 한글·영문·숫자면 건너뛴다.
    - 긴 이름이 먼저 자리를 잡고, 겹치는 짧은 이름은 버린다.
    - 작업 동사 자리("확인해줘")는 먼저 막아 둔다. 취합표에 '확인' 열이 있어도 동사를 열로 읽지 않는다.
      다만 동사 글자를 완전히 감싸는 더 긴 이름('확인자', '입력일')은 이름이다(5차 검수 W01·W02).
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
            inside = [t for t in taken if not t[2] and start <= t[0] and t[1] <= end]
            if any(e - s < end - start for s, e, _ in inside):
                taken = [t for t in taken if t not in inside]
            if not any(s < end and start < e for s, e, _ in taken):
                taken.append((start, end, name))  # 같은 이름이 여러 번 나오면 모두 읽는다
    return sorted(taken)


def _mask(request: str, names: list[str]) -> str:
    """이름 자리를 표시 글자로 가린 요청(길이 그대로). 이름 속 글자('참고사항'의 참고)를 낱말로 읽지 않는다."""
    out = list(request)
    for start, end, name in _spans(request, names):
        if name:
            out[start:end] = NAME_MARK * (end - start)
    return "".join(out)


def _mentions(request: str, names: list[str]) -> list[tuple[str, str]]:
    """요청에 나온 이름과 그 뒤 글자(공백 제거, 12자, 뒤에 오는 이름은 가림)."""
    masked = _mask(request, names)
    spans = _spans(request, names)
    return [(name, _compact(masked[e : e + 20])[:12]) for _, e, name in spans if name]


def _simple(request: str, names: list[str]) -> bool:
    """모든 낱말이 '이름+조사' 또는 허용 낱말인 요청. 모르는 말이 하나라도 있으면 짝을 정하지 않는다.

    모르는 말이 어느 이름에 걸리는지를 위치나 낱말 목록으로 정하는 국소 판정은 독립 검수 세 번 모두에서
    나열·되가리킴·다른 지목 표현으로 새었다(PR #18, DIF-041). 그래서 요청 전체로 판단한다.
    """
    marked, last = [], 0
    for start, end, name in _spans(request, names):
        if name:
            marked += [request[last:start], NAME_MARK]
            last = end
    marked.append(request[last:])
    tokens = [t for t in re.split(r"[\s,.!~·、/]+", "".join(marked)) if t]
    return all(SIMPLE_TOKEN.fullmatch(t) for t in tokens)


def _resolve(name: str, pool: list[str]) -> str | None:
    """정규화(공백·끝 콜론·괄호 단위·대소문자)한 이름이 같은 것이 하나뿐일 때만. 둘 이상이면 정하지 않는다.

    단위가 다르면 다른 이름이다. 포함 관계(발급일↔발급일자)는 코드가 확정하지 않는다. 짧은 이름이 엉뚱한 열에 붙기 때문이다
    ('번호'↔'전화번호', P04 ① 검수 H2). 그런 짝은 모델의 선택지나 질문으로 간다.
    """
    exact = [p for p in pool if _same(p, name)]
    return exact[0] if len(exact) == 1 else None


def _unit(text: str) -> str:
    """괄호 안 글자(단위). 여러 개면 이어 붙인다."""
    groups = re.findall(r"\(([^)]*)\)|（([^）]*)）", text or "")
    return "".join(normalized_label(a or b) for a, b in groups)


def _same(a: str, b: str) -> bool:
    """정규화 이름과 괄호 안 단위가 모두 같다. 예산(천원)과 예산(원), 금액(천원)과 금액은 다르다(2차 검수 N01·N02)."""
    return _norm(a) == _norm(b) and _unit(a) == _unit(b)


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


def _covers_all_rows(view: TargetView) -> bool:
    """미리보기가 데이터 행 끝까지 담았나. 일부만 보고 값을 비교하면 51행 아래 사람을 놓친다(3차 K04)."""
    last = max((int(re.sub(r"\D", "", c.address)) for c in view.cells), default=view.header_row)
    return last >= view.data_rows[1]


def _numeric(value: str) -> bool:
    """날짜·금액·번호처럼 숫자와 구분 기호뿐인 값."""
    return bool(re.fullmatch(r"[\d\s.,:/\-]+", value))


def _identifier_like(values: list[str]) -> bool:
    """키로 추정해도 되는 값: 연번·O/X·작은 수처럼 우연히 겹치기 쉬운 값이 아니다."""
    return not all(len(v) <= 1 or (v.isdigit() and len(v) <= 3) for v in values)


def _key_candidates(
    fields: list[FieldCandidate], view: TargetView, *, named: bool = False
) -> list[tuple[int, str, str]]:
    """(일치 수, 문서 항목, 대상 열): 문서 값이 그 열에 글자 그대로 있는 쌍.

    일치한 값은 식별자처럼 보여야 한다. 사용자가 키를 말했으면(named) 일치가 하나라도 있으면 된다.
    추정할 때는 열 값이 서로 다르고(80%), 문서 값의 80% 이상이 그 열에 있어야 하며, 이름이 다른 열이면
    숫자로만 된 값(날짜·금액)으로는 잇지 않는다.
    """
    result = []
    for column in view.columns:
        values = _column_values(view, column)
        if not values:
            continue
        if not named and (
            len(values) < 2
            or len(set(values)) < max(2, 0.8 * len(values))
            or not _identifier_like(values)
        ):
            continue
        pool = set(values)
        for field in fields:
            samples = [s for s in field.samples if s]
            hit_values = [s for s in samples if s in pool]
            if not hit_values or not _identifier_like(hit_values):
                continue  # 차수 1·2·3과 번호 1·2·3(2차 검수 K03)
            if not named and not _same(field.name, column) and all(map(_numeric, hit_values)):
                continue  # 이름이 다른 열의 날짜·금액은 우연히 같을 수 있다(2차 검수 K01·K02)
            hits = len(hit_values)
            if named or hits >= max(2, 0.8 * len(samples)):
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


def _ask_model(choose: Chooser | None, prompt_for, options: tuple[str, ...]) -> str | None:
    """선택지를 한 칸씩 돌려 두 번 묻고 같은 답일 때만 받아들인다. 자리에 흔들리는 답은 믿지 않는다.

    '없음'까지 돌리므로 모든 자리의 선택지가 바뀐다(뒤집기는 가운데 자리가 남는다, PR #18 3차 검수 L1).
    첫 답이 '없음'이거나 목록 밖이면 확정하지 않으므로 두 번째는 묻지 않는다.
    """
    if choose is None or not options:
        return None
    first = (*options, NONE)
    answer = choose(prompt_for(options), first)
    if answer not in options:
        return None
    second = first[1:] + first[:1]
    again = choose(prompt_for(tuple(o for o in second if o != NONE)), second)
    return answer if again == answer else None


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
    simple = _simple(request, columns + names)
    # 열·항목 이름 자체가 빼기 낱말('제외')이면 이름으로 가려져 부정을 못 본다. 그런 이름이 나오면 묻는다(4차 M1)
    simple = simple and not any(
        re.fullmatch(NEGATED.pattern, _compact(v)) for n, _ in found for v in _variants(n)
    )
    work_type = _work_type(_mask(request, columns + names))
    if work_type is None:
        questions.append(("work_type", ("fill", "check")))

    # 키: 'X로 찾아·X 기준'으로 말한 이름 → 없으면 엄격한 데이터 일치에서 1위가 뚜렷할 때만.
    # "이름으로 찾아서 이름 말고"처럼 말한 키를 스스로 지웠으면 다른 열을 추정하지 않고 묻는다(2차 R03)
    key = None
    hint = next((n for n, after in mentions if KEY_AFTER.match(after)), None)
    named_keys = {n for n, a in found if KEY_AFTER.match(a)}
    # 키를 둘 말했거나("이름 기준 말고 발급번호 기준으로"), 말한 키에 모르는 말이 닿았으면 묻는다(PR #18 H2)
    conflicting = any(n in skip for n in named_keys) or len(named_keys) > 1
    if conflicting:
        hint = None
    elif hint:
        column, field = _resolve(hint, columns), _resolve(hint, names)
        picked = [
            c
            for c in _key_candidates(fields, view, named=True)
            if column in (None, c[2]) and field in (None, c[1])
        ]
        if len(picked) == 1:
            key = (picked[0][1], picked[0][2])
    else:  # 키를 말하지 않았다
        candidates = _key_candidates(fields, view)
        if candidates and (len(candidates) == 1 or candidates[0][0] > 2 * candidates[1][0]):
            key = (candidates[0][1], candidates[0][2])
    if key is not None:
        # 행 전체를 봤고, 그 열에 값이 맞는 다른 문서 항목이 없을 때만(3차 H1 성명·추천인, 4차 H3 이름·이름)
        rivals = [
            c
            for c in _key_candidates(fields, view, named=True)
            if c[2] == key[1] and c[1] != key[0]
        ]
        if rivals or not _covers_all_rows(view):
            key = None
    if key is None:
        options = tuple(dict.fromkeys(c[2] for c in _key_candidates(fields, view, named=True)))
        questions.append(("key_column", options[:3]))
    key_field, key_column = key if key else (None, None)

    # 넣을 칸
    pairs: list[tuple[str, str, str]] = []
    only_fields: list[str] = []
    only_columns: list[str] = []
    # "A를 B 칸에": A는 목적격 조사가 붙은 문서 항목, B는 바로 뒤에 '칸에·열에'가 붙은 열이다
    linked: list[tuple[str, str]] = []
    unclear: list[
        str
    ] = []  # 지목은 했는데 무엇을 넣을지 모르는 칸과 그 앞 이름("점수를 이수시간 칸에")
    previous = None  # (이름, 문서 항목 또는 None, 뒤 글자)
    before_previous = (
        None  # 그 앞 이름의 뒤 글자: "직급이랑 이수시간을 비고 칸에"의 나열을 알아본다
    )
    pointing_unclear = False  # "A를 B 칸에" 꼴이 아닌 지목(5차 검수 H): 짝을 정하지 않고 묻는다
    for name, after in mentions:
        if name == hint:
            continue
        column = _resolve(name, [c for c in columns if c != key_column])
        field = _resolve(name, [n for n in names if n != key_field])
        if column is not None and SLOT.match(after) and previous and OBJECT.match(previous[2]):
            if previous[1] is not None:
                linked.append((previous[1], column))  # 지목한 칸이 같은 이름 짝보다 이긴다
            else:
                unclear += [column, previous[0]]
            if before_previous is not None and JOINED.match(before_previous):
                pointing_unclear = True  # 나열한 이름 가운데 어느 것을 넣을지 모른다
            previous = before_previous = None
            continue
        if SLOT.match(after) or DIRECTION.match(after):
            pointing_unclear = True
        if field is not None and column is not None:
            pairs.append((field, column, "match"))
        elif field is not None:
            if field not in only_fields:
                only_fields.append(field)
        elif column is not None and column not in only_columns:
            only_columns.append(column)
        before_previous = previous[2] if previous else None
        previous = (name, field, after)
    for field, column in linked:  # 사용자가 칸을 지목했다
        pairs = [p for p in pairs if p[0] != field]
        if field in only_fields:
            only_fields.remove(field)
        pairs.append((field, column, "match"))
    pairs = [p for p in dict.fromkeys(pairs) if p[0] not in unclear and p[1] not in unclear]
    only_fields = [n for n in only_fields if n not in unclear]
    only_columns = [n for n in only_columns if n not in unclear]
    # 애매한 지목이나 모르는 말이 있으면 짝을 정하지도 모델에 묻지도 않고 모두 소명한다
    mentioned = [*(p[0] for p in pairs), *(p[1] for p in pairs), *only_fields, *only_columns]
    drop = set(mentioned) if pointing_unclear or not simple else set()
    held = [n for n in mentioned if n in drop]
    pairs = [p for p in pairs if p[0] not in drop and p[1] not in drop]
    only_fields = [n for n in only_fields if n not in drop]
    only_columns = [n for n in only_columns if n not in drop]
    off_limits = skip | set(unclear)  # 빼라고 한 열·이름은 모델 선택지에도 넣지 않는다
    for field in only_fields:
        taken = {p[1] for p in pairs}
        options = _order(
            field,
            tuple(c for c in columns if c != key_column and c not in taken and c not in off_limits),
        )
        answer = _ask_model(
            choose, lambda o, f=field: _column_prompt(request, by_name[f], o, view), options
        )
        if answer:
            pairs.append((field, answer, "model"))
    for column in only_columns:
        taken = {p[0] for p in pairs}
        options = _order(
            column,
            tuple(n for n in names if n != key_field and n not in taken and n not in off_limits),
        )
        answer = _ask_model(
            choose, lambda o, c=column: _field_prompt(request, c, o, by_name, view), options
        )
        if answer:
            pairs.append((answer, column, "model"))

    # 값 형태가 어긋나는 짝(코드형 값을 숫자 열에)은 확정하지 않는다
    fits = [p for p in pairs if _shape_fits(by_name[p[0]].samples, _column_values(view, p[1]))]
    held += [name for p in pairs if p not in fits for name in p[:2]]
    pairs = fits

    # 한 열에 둘 이상이 몰리면 어느 것도 확정하지 않는다(검수 H7)
    count: dict[str, int] = {}
    for _, column, _ in pairs:
        count[column] = count.get(column, 0) + 1
    mapped = tuple(p for p in pairs if count[p[1]] == 1)
    placed = {p[0] for p in mapped} | {p[1] for p in mapped}
    requested = [
        *only_fields,
        *only_columns,
        *unclear,
        *held,
        *(p[0] for p in pairs),
        *(p[1] for p in pairs),
    ]
    excluded = tuple(dict.fromkeys(n for n in requested if n not in placed))
    if not mapped:
        questions.append(("target_column", tuple(c for c in columns if c != key_column)[:3]))
    record = replace(record, key_field=key_field or "")
    return Interpretation(work_type, record, key, mapped, tuple(questions), excluded, tuple(fields))


def _kind(value: str) -> str:
    """값 형태: 날짜·연락처·숫자·코드형·글자. 짝이 그럴듯한지만 본다(값을 바꾸지 않는다)."""
    v = value.strip()
    if re.fullmatch(r"\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}\s*일?\.?", v):
        return "date"
    if re.fullmatch(r"(0\d{1,2}-\d{3,4}(-\d{4})?|내선\s*\d+)", v):
        return "phone"
    if re.fullmatch(r"[\d,]+(\.\d+)?\s*(원|천원|시간|명|개|점|%)?", v):
        return "number"
    if re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+([-_][A-Za-z0-9]+)+", v):
        return "code"
    return "text"


def _main_kind(values) -> str:
    kinds = [_kind(v) for v in values if v and v.strip()]
    if not kinds:
        return "text"
    top = max(set(kinds), key=kinds.count)
    return top if kinds.count(top) * 2 > len(kinds) else "text"


def _shape_fits(samples, existing) -> bool:
    """둘 다 뚜렷한 형태(글자 아님)인데 서로 다르면 맞지 않는다. 열이 비었으면 판단하지 않는다."""
    a, b = _main_kind(samples), _main_kind(existing)
    return "text" in (a, b) or a == b


def _clean(text: str, limit: int = 20) -> str:
    """질문에 넣는 문서·취합표 글: 줄바꿈·따옴표를 공백으로 바꾸고 자른다(줄 구조를 깨지 못하게)."""
    return re.sub(r"[\s\"'`]+", " ", text or "").strip()[:limit]


def _examples(values) -> str:
    # 문서 글이 지시처럼 읽히지 않게 따옴표로 감싸고 자른다(PR #18 검수 M2·L1)
    clean = (_clean(v) for v in values if v and v.strip())
    shown = ", ".join(dict.fromkeys(f'"{c}"' for c in clean if c))
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
    """이름이 비슷한 선택지를 앞에 둔다. 이름은 같고 단위만 다른 것(천원↔원)은 값이 틀리므로 뺀다(2차 N01)."""
    options = tuple(o for o in options if _norm(o) != _norm(name) or _unit(o) == _unit(name))
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
        lines.append(f"- {_clean(option, 40)}{_examples(by_name[option].samples[:2])}")
    return "\n".join(lines)


def _column_prompt(request, field: FieldCandidate, options, view) -> str:
    lines = [
        f"요청: {request}",
        f"문서 항목 '{field.name}'{_examples(field.samples[:2])}을 넣을 취합표 열을 고르세요. "
        f"맞는 열이 없으면 '{NONE}'.",
    ]
    lines += _hint(field.name, options)
    lines += [f"- {_clean(o, 40)}{_examples(_column_values(view, o)[:3])}" for o in options]
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
