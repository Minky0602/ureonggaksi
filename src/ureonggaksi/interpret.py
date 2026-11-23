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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from . import morph
from .errors import ServiceError
from .extract import _normalized as normalized_label
from .models import Block, Document
from .rules import FieldRule, RecordRule, RuleSet, TargetRule
from .synonyms import GROUPS
from .xlsx import TargetView

# 작업 종류 동사. "확인서"·"검토서"처럼 문서 이름에 든 글자는 동사가 아니다
FILL = re.compile(r"채워|채우|채움|넣어|넣기|넣고|넣을|입력|기입|적어|옮겨|반영|써\s?줘")
FILL_NEGATED = re.compile(  # 하지 않는 채우기: "채우지 마", "채워주지 마", "안 채워도 돼", "넣을 필요 없어"
    # '안 채운 칸'·'채우지 않은 칸'은 칸을 꾸미는 관형형이라 하지 않는 채우기가 아니다(Kiwi PR 검수 M1).
    # '-ㄴ 채로'·'않는 게'처럼 관형형이어도 부정인 꼴은 형태소 쪽(morph)이 잡는다(3차 검수 M-2)
    r"(채우|채워|넣|넣어|입력하|기입하|적|적어|옮기|옮겨|써)\s*(주)?\s*지\s*(는|도|만)?\s*(마|말|않(?!은|는))"
    r"|안\s*(채|넣|입력|기입|적|옮|써)(?!운|은|한|긴)|필요\s*(는|도)?\s*없|맙시다|(채우|넣으|입력하|기입하|적으|쓰)면\s*안(?!\s*(될까|되나|되겠))"
)
CHECK = re.compile(
    r"확인(?!서)|대조|검토(?!서)|비교|맞는지|맞나|같은지|일치|검증|틀린|다른지|차이|체크|점검|검사"
    r"|괜찮은지|됐나|됐는지|제대로|봐\s?줘|살펴|없나|맞아|됐어|있어|있나|\?"
)  # "기입 상태 봐줘"·"입력 누락 없나": 채우기 낱말이 명사로 쓰인 검토 요청(P04 ① 2차 검수).
# '?'로 끝나는 묻는 말("입력했어?")은 채우기와 함께 나오면 작업 종류를 묻는다(DIF-050 M1)
# 이름 바로 뒤(공백 제거) 글자로 읽는 표현
# "요청번호 기준", "이름으로 찾아", "이름으로 대조", "발급번호로 행을". '로'만으로는 키가 아니다:
# "비고를 이름으로 채워줘"는 이름을 넣으라는 뜻일 수 있다(PR #18 6차 검수 H1). "직급별로"는 묶기다
KEY_AFTER = re.compile(r"^(기준|(으로|로)(찾|대조|맞추|맞춰|매칭|연결|비교|확인|검토|행))")
NEGATED = re.compile(
    r"^(칸|열|란)?(에는|에|은|는|을|를|이|가|도|까지는|까지|만은|만)?"
    r"(빼(?!놓지|먹지|지말|지마)|제외(?!하지말|하지마)|말고|그대로(?!옮|넣|채|입력|적|기입|반영)|손대지|건드리지|하지마|하지말"
    r"|두고|놔두(?!지말|지마)|참고|비워(?!두지말|두지마)|비우(?!지말|지마)"
    r"|채우지마|채우지말|넣지마|넣지말|보류|생략|취소"
    r"|미루|(나중에?|나중으로|추후에?|이따가?|다음에|다음(주|달|번)에)(따로)?(?=하|해(?!당)|할|요|넣|채|처리|정하|정할|정해(?!진)|[,.;]|$)"
    r"|아직(하지|안하|안해|채우지|넣지|이야|이에요|이요))"
)  # "번호 칸은 비워 둬", "비고 칸에 넣지 말고", "생일은 나중에 하고"(미루기는 SKIP_WORDS와 같은 뜻).
# "빼지 마·비우지 말고·놔두지 마"는 빼지 말라는 말이다(곁마디 검수 r2 L-1)
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
    r"|빈칸|빈칸을|빈칸에|다|모두|전부|전체|각각"  # "다 채워줘": 빼는 동사가 오면 그 동사가 모르는 말이라 묻는다
    rf"|{ENDING}|요|좀|부탁해|부탁해요|부탁합니다|부탁드립니다|감사합니다|감사해요|고맙습니다|[ㅠㅜ]+"
)
NAME_MARK = "\x00"
# 낱말 끝에 붙은 '좀'("체크좀")은 뜻을 바꾸지 않는다
# 이름 뒤 '의'는 허용하지 않는다: "비고의 직급"은 어느 칸에 무엇을 넣는지 갈린다(5차 검수 P03)
NAME_PARTICLE = PARTICLE.replace("|의|", "|")
SIMPLE_TOKEN = re.compile(
    rf"(?:(?:{NAME_MARK}{NAME_PARTICLE}*)+|(?:{NAME_MARK}{NAME_PARTICLE}*)*(?:{ALLOWED_WORD}))좀?"
)
JOINED = re.compile(r"^(이랑|랑|하고|와|과|,|및|그리고)")  # 나열: 부정이 앞 이름까지 걸린다
# 이름 바로 뒤의 자리 낱말은 지목이다("B 칸에", "B 빈칸에", "B의 빈칸", "B 칸", "B 표에", PR #18 4차 검수 H).
# "A를 B …"로 짝이 맞으면 지목대로, 아니면 모두 묻는다
SLOT = re.compile(
    r"^의?(빈칸|칸|열|란|셀|표|시트|부분(?!적)|자리|쪽)"
)  # '부분·자리·쪽'도 지목(3차 M2)
# 지목을 확정하는 꼴: 칸 낱말(표·시트 아님) 바로 뒤에 채우기 동사. "칸 기준으로", "현황 시트에"는 지목으로 확정하지 않는다
POINTED = re.compile(r"^의?(빈칸|칸|열|란|셀)(에|에다|에다가)?(채워|넣어|입력|기입|적어|옮겨|써)")
OBJECT = re.compile(
    r"^(을|를)\x00"
)  # 지목 규칙의 A: '를' 바로 뒤에 칸 이름이 온다(사이에 동사 없음)
DIRECTION = re.compile(
    r"^(에서|에|으로|로)"
)  # "전화번호에", "휴대전화로": 지목인데 "A를 B 칸에" 꼴이 아님
NONE = "없음"
EVERYTHING_KEY, EVERYTHING_OPTIONS = "[요청] 전부", ("예", "아니오")

# 모델 호출: (질문 글, 선택지) → 선택지 하나 또는 None
Chooser = Callable[[str, tuple[str, ...]], str | None]
# 여러 칸을 한 번에 묻기: (질문 글, {칸: 선택지}) → {칸: 답}. 목록 밖·빠진 칸은 없다(ai.LocalModel.ask_fields)
AskMany = Callable[[str, dict[str, tuple[str, ...]]], dict[str, str]]
ROLES = ("채움", "키", "제외", "무관")
FILL_ROLE, SKIP_ROLE = ROLES[0], ROLES[2]


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
    # (문서 항목, 대상 열, 출처). match: 코드가 확신(같은 이름·정형 지목) /
    # assumed: 이름은 같은데 모르는 말이 섞여 '채우라는 뜻'을 모델이 읽은 짝 → 채우고 '확인 필요' /
    # synonym: 동의어표·'명/일자' 규칙으로 지은 짝 / model: 어느 열인지를 모델이 고른 짝.
    # assumed·synonym·model은 채우고 '확인 필요'로 보인다(2026-11-20 사용자 결정 "동일 취급")
    fields: tuple[tuple[str, str, str], ...]
    questions: tuple[tuple[str, tuple[str, ...]], ...]  # (슬롯, 후보) — 6.2 질문 재료
    excluded: tuple[str, ...] = ()  # 요청했지만 대응 열이 없는 항목·열
    candidates: tuple[
        FieldCandidate, ...
    ] = ()  # 문서에서 찾은 항목들(build_rule이 추출 규칙을 가져간다)
    skipped: tuple[str, ...] = ()  # 모르는 말이 섞인 요청에서 빼라는 말로 읽은 이름 → '확인 필요'
    unread: tuple[str, ...] = ()  # 모델이 어느 열과도 잇지 못한 모르는 말 → 경고
    # 빼라는 말로 읽었지만 모델이 채우라고 읽었거나 채우라고도 빼라고도 읽은 이름. 이 이름만 비우고
    # 경고로 알린다. 남은 짝이 없어 묻게 되면 질문이 이 이름을 짚는다(DIF-060·062)
    conflicted: tuple[str, ...] = ()
    # 요청이 말하지 않은 키를 코드가 추정했다(이름이 같고 문서 값이 모두 맞으며, 다른 후보와 행이 같음) → '확인 필요'
    key_assumed: bool = False


def _norm(text: str) -> str:
    """비교용: 공백·끝 콜론 제거, 괄호 안(단위 등) 제거, 대소문자 무시."""
    text = re.sub(r"\([^)]*\)|（[^）]*）", "", text or "")
    return normalized_label(text)


_GROUP_OF = {normalized_label(word): i for i, group in enumerate(GROUPS) for word in group}


def _stem(name: str) -> str:
    """끝의 '명'·'일자'·'금액'만 다른 이름을 같게 본다(사업장명=사업장, 참석일자=참석일, 집행금액=집행액).
    두 글자 이름(성명, 금액)은 그대로."""
    text = re.sub(r"일자$", "일", _norm(name))
    text = re.sub(r"(?<=.)금액$", "액", text)
    return re.sub(r"(?<=..)명$", "", text)


COMPARE_AFTER = re.compile(r"^\s*(이랑|랑|하고|와|과)\s*(다른|다르|같|일치|대조|비교|맞)")


def _said_together(mentions, hint) -> bool:
    """키 말고 이름이 둘이고, 둘을 바로 잇거나("A랑 B") 뒤 이름 뒤에 대조 표현이 온다("A … B랑 다른").
    '랑'이 키와 잇는 말("비고랑 발급번호 기준으로")은 아니다(검수 r1 L-3)."""
    others = [(i, a) for i, (n, a) in enumerate(mentions) if n != hint]
    if len(others) != 2:
        return False
    (i, first), (j, second) = others
    adjacent = j == i + 1 and re.match(rf"^\s*(이랑|랑|하고|와|과)\s*{NAME_MARK}", first)
    return bool(adjacent or COMPARE_AFTER.match(second))


def _synonym(name: str, pool: tuple[str, ...]) -> str | None:
    """name과 같은 동의어 묶음에 들었거나 끝의 '명'·'일자'·'금액'만 다른 후보가 pool에 하나뿐이면 그것.
    없거나 둘 이상이면 None(모델에 넘긴다)."""
    group, stem = _GROUP_OF.get(_norm(name)), _stem(name)
    found = [
        p
        for p in pool
        if _norm(p) != _norm(name)
        and _unit(p)
        == _unit(name)  # 단위가 다르면 다른 이름이다(집행금액(천원) ≠ 집행액, 검수 r1 M-2)
        and ((group is not None and _GROUP_OF.get(_norm(p)) == group) or _stem(p) == stem)
    ]
    return found[0] if len(found) == 1 else None


def _headed(word: str, pool: tuple[str, ...]) -> str | None:
    """word가 pool의 이름 하나만의 끝 명사와 같으면 그 이름('계좌'→지원계좌). 둘 이상('번호'→관리번호·
    전화번호)이거나 없으면 None. 4B는 이런 줄인 말을 열과 잇지 못했다(DIF-068·069)."""
    # 끝 글자가 같은 이름을 모두 센다. Kiwi가 '전화번호'를 한 낱말로 읽어도 '번호'로 끝나는 후보다
    ending = [p for p in pool if _norm(p).endswith(_norm(word))]
    # 단위가 다르면 다른 이름이다('금액' ≠ 집행금액(천원), _synonym과 같다, 검수 L-3)
    ending = [p for p in ending if _unit(p) == _unit(word)] if len(ending) == 1 else ending
    if len(ending) != 1 or morph.head_noun(ending[0]) != word:
        return None
    return ending[0]


def _named_in(request: str, names: list[str], spans: list[tuple[int, int]]) -> set[str]:
    """요청에 나온 자리마다 글자 전부가 spans 안에 드는 이름. 한 번이라도 밖에 나오면 아니다."""
    covered = {i for a, b in spans for i in range(a, b)}
    out = set()
    for name in names:
        places = [m.span() for m in re.finditer(re.escape(name), request)]
        if places and all(set(range(a, b)) <= covered for a, b in places):
            out.add(name)
    return out


# 낱말 뒤 같은 마디 안의 빼기·미루기 표현("생일이랑 이메일은 빼", "생일은 이번엔 놔두고", "다음 주에")
SKIP_WORDS = re.compile(
    r"빼(?!놓지|먹지|지\s*[말마])|제외(?!\s*하지\s*[말마])|(?<!먹지 )(?<!먹지)말고|놔두(?!지\s*[말마])|두고"
    r"|비워(?!\s*두지\s*[말마])|비우(?!지\s*[말마])|그대로(?!옮|넣|채|입력|적|기입|반영)|참고|취소|보류|생략|미루"
    r"|손대지|건드리지|하지\s*마|하지\s*말"
    r"|(나중에?|나중으로|추후에?|이따가?|다음에|다음\s*(주|달|번)에)(\s*따로)?(?=\s*(하|해(?!당)|할|요|넣|채|처리|정하|정할|정해(?!진)|[,.;]|$))"
    r"|아직\s*(하지|안\s*하|안\s*해|채우지|넣지|이야|이에요|이요)"
)  # 이름 경로의 빼기 낱말(NEGATED)과 같은 뜻을 쓴다. 미루기는 '하다'·마디 끝이 뒤따를 때만이다:
# '다음 주까지'·'아직 비어 있는'·'신청인 다음에 생일'·'아직 안 채운 칸'·'나중에 확인할'은 미루기가 아니다(검수 L-1, PR #27)
CLAUSE_END = re.compile(r"[,.;]|고\s")  # "생일 채우고 이수시간은 빼": '고 '에서 마디가 끝난다
ONLY_AFTER = re.compile(r"(에)?만(?![원큼])")  # 낱말 바로 뒤 보조사 '만'("시간만", "비고에만")
UNDO_BEFORE = re.compile(
    r"(빼|제외하|놔두|비워\s*두|두|미루|비우)지\s*$"
)  # '말고·마' 앞의 부정되는 빼기


def _said_to_skip(request: str, word: str) -> bool:
    """그 낱말이 나온 어느 자리든, 같은 마디 뒤쪽에 빼기·미루기 표현이 있다. 넓게 잡는다(틀리면 덜 채울 뿐)."""
    for found in re.finditer(re.escape(word), request):
        rest = request[found.end() :]
        if ONLY_AFTER.match(rest):
            continue  # "시간만 넣어": 그것만 하라는 말이지 빼라는 말이 아니다(DIF-069 검수 H-1)
        end = CLAUSE_END.search(rest)
        for hit in SKIP_WORDS.finditer(rest):
            # 빼기 낱말이 첫 마디 끝보다 앞에서 시작하면 그 마디의 말이다("비워 두고 " 자체가 '고 '로 끝난다, 검수 M-1)
            if end is not None and hit.start() >= end.start():
                break
            # "빼지 말고": 앞의 빼기를 부정하는 '말고·마'는 빼기가 아니다(DIF-069 검수 M-1)
            if hit.group().startswith(("말", "마")) and UNDO_BEFORE.search(rest[: hit.start()]):
                continue
            return True
    return False


# ---------- 항목 찾기 ----------


def _cells(doc: Document) -> dict[tuple[str, int, int, int], Block]:
    return {(b.section, b.table, b.row, b.col): b for b in doc.blocks if b.kind == "cell"}


def _right_or_below(cells, block: Block):
    right = cells.get((block.section, block.table, block.row, block.col + block.col_span))
    below = cells.get((block.section, block.table, block.row + block.row_span, block.col))
    return right, below


def _overflowed_values(documents: tuple[Document, ...], fields) -> set[str]:
    """항목명으로 잡혔지만 실은 옆 라벨의 값 칸인 이름. '주소 | 서울시 중구 / (아래) 세부주소'에서 값이 문서마다
    우연히 같으면 '서울시 중구'가 항목명이 되고 '주소'는 항목을 잃는다. 고정 칸 X가 항목이 못 됐고 그 오른쪽 칸이
    아래 값을 가진 항목이면 그 항목은 X의 값 칸이다(4차 검수 M-3)."""
    by_norm = {_norm(f.name): f for f in fields}
    maps = [_cells(d) for d in documents]
    found = set()
    for key, block in maps[0].items():
        text = block.text.strip()
        if not text or _norm(text) in by_norm:
            continue
        if any(m.get(key) is None or m[key].text.strip() != text for m in maps[1:]):
            continue
        right, below = _right_or_below(maps[0], block)
        below_texts = [
            n.text.strip() if (n := _right_or_below(m, m[key])[1]) is not None else None
            for m in maps
        ]
        if below is not None and all(t == below_texts[0] for t in below_texts):
            continue  # 아래 칸도 문서마다 같으면 머리글 표다('연번 | 성명' 아래 '1', 재확인 L-2)
        field = by_norm.get(_norm(right.text)) if right is not None else None
        if field is not None and field.rule.direction == "below":
            found.add(field.name)
    return found


def _document_fields(documents: tuple[Document, ...]) -> list[FieldCandidate]:
    """같은 위치에 같은 글자가 모든 문서에 있으면 항목명, 그 오른쪽(없으면 아래) 칸이 값이다."""
    first, maps = documents[0], [_cells(d) for d in documents]
    found: dict[str, FieldCandidate] = {}
    label_counts: dict[str, int] = {}
    for block in first.blocks:
        if block.kind == "cell" and block.text.strip():
            label_counts[_norm(block.text)] = label_counts.get(_norm(block.text), 0) + 1
    paragraph_label = re.compile(r"^\s*([^:：\s][^:：]{0,19}?)\s*[:：]")
    for (
        block
    ) in first.blocks:  # 문단 "직급: 팀장"과 표 칸 '직급'이 함께 있으면 어느 것인지 모른다(6차 A01)
        if block.kind == "paragraph" and (m := paragraph_label.match(block.text)):
            label_counts[_norm(m.group(1))] = label_counts.get(_norm(m.group(1)), 0) + 1
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
        if _norm(label) in found or not any(values) or label_counts.get(_norm(label), 0) > 1:
            continue
        query = rf"^\s*{re.escape(label)}\s*[:：]\s*(.+?)\s*$"
        found[_norm(label)] = FieldCandidate(
            label, FieldRule(label, "regex", query=query, group=1), tuple(values)
        )
    return list(found.values())


def _ambiguous_labels(documents: tuple[Document, ...]) -> set[str]:
    """첫 문서에서 둘 이상 나오는 라벨(표 칸·문단 "라벨:"). 항목 후보에서 빠진 이름이다."""
    counts: dict[str, int] = {}
    paragraph_label = re.compile(r"^\s*([^:：\s][^:：]{0,19}?)\s*[:：]")
    for block in documents[0].blocks:
        text = block.text.strip() if block.kind == "cell" else ""
        if block.kind == "paragraph" and (m := paragraph_label.match(block.text)):
            text = m.group(1)
        if text:
            counts[_norm(text)] = counts.get(_norm(text), 0) + 1
    return {name for name, n in counts.items() if n > 1}


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


# "넣어 줄래?", "채워 줄 수 있어?": 부탁으로 끝나는 '?'는 묻는 말(검토)이 아니다(DIF-055)
POLITE_ASK = re.compile(
    r"(채워|넣어|입력해|기입해|적어|옮겨|반영해|써)\s*"
    r"(줄래요?|줄\s*수\s*(있어|있어요|있나요|있을까요?)|주실\s*(래요|수\s*있나요)|주시겠어요|주실래요?"
    r"|주시?면\s*안\s*(될까요?|되나요?|되겠습니까|돼요?))\s*\?"  # 채워 주시면 안 될까요?(3차 검수 M-3)
)


def _work_type(request: str) -> str | None:
    """채우기·검토 동사가 하나만 있을 때만 정한다. 하지 않는 채우기("채우지 마")는 채우기가 아니고,
    그것과 다른 채우기가 함께 있으면("직급 채워줘, 이수시간은 안 채워도 돼") 묻는다."""
    request = POLITE_ASK.sub(
        lambda m: m.group(1) + " ", request
    )  # 부탁 어미째 지운다("있어"는 검토 표지이기도 하다)
    # 정규식이 아는 꼴과 형태소로 읽은 꼴("다 채우진 마", "입력하지 않아도")을 함께 지운다(DIF-064)
    spans = [m.span() for m in FILL_NEGATED.finditer(request)]
    spans += morph.negated_fill_spans(request)
    rest = request
    for start, end in spans:
        rest = rest[:start] + " " * (end - start) + rest[end:]
    negated = bool(spans)
    fill = bool(FILL.search(rest))
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
    owners: dict[str, set[str]] = {}
    for variant, name in pairs:
        owners.setdefault(_compact(variant), set()).add(name)
    for variant, name in pairs:
        if len(owners[_compact(variant)]) > 1 or re.fullmatch(ALLOWED_WORD, _compact(variant)):
            name = ""  # 비고(1)·비고(2)의 '비고': 어느 이름인지 모른다. 모르는 말로 남긴다(5차 검수 H3)
        pattern = r"(?<![가-힣A-Za-z0-9])" + r"\s*".join(map(re.escape, _compact(variant)))
        for match in re.finditer(pattern, request):
            start, end = match.span()
            inside = [t for t in taken if not t[2] and start <= t[0] and t[1] <= end]
            if any(e - s < end - start for s, e, _ in inside):
                taken = [t for t in taken if t not in inside]
            if not any(s < end and start < e for s, e, _ in taken):
                taken.append((start, end, name))  # 같은 이름이 여러 번 나오면 모두 읽는다
                if not name:
                    taken[-1] = (start, end, "")
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


def _blank(text: str, spans) -> str:
    """범위를 같은 길이의 공백으로 가린다(다른 글자의 위치는 그대로)."""
    for a, b in spans:
        text = text[:a] + " " * (b - a) + text[b:]
    return text


def _unknown_words(request: str, names: list[str]) -> list[str]:
    """허용 낱말도 이름도 아닌 말(조사·'좀'·'요'를 뗀 것). 모델이 열 이름의 다른 말인지 읽는다.
    이유 말·미루는 말("개인정보라", "나중에 따로 정할게")은 곁말이라 묻지 않는다(morph.aside_spans)."""
    request = _blank(request, morph.aside_spans(request))
    marked, last = [], 0
    for start, end, name in _spans(request, names):
        if name:
            marked += [request[last:start], NAME_MARK]
            last = end
    marked.append(request[last:])
    words = []
    for token in re.split(r"[\s,.!~·、/?]+", "".join(marked)):
        if not token or SIMPLE_TOKEN.fullmatch(token):
            continue
        # 조사는 형태소로 뗀다. 정규식으로 떼면 낱말 끝 글자까지 먹었다('평가는'→'평', DIF-064)
        word = morph.strip_particles(re.sub(r"좀$", "", token.replace(NAME_MARK, "")))
        # 코드가 이미 아는 동사·빼기 표현('채우고', '놔두세')은 열 이름의 다른 말이 아니다(DIF-050 M4)
        if any(p.search(word) for p in (FILL, FILL_NEGATED, CHECK)) or NEGATED.match(word):
            continue
        # '일단', '우선': 열을 가리키지 않는다. 열 이름 안에 든 말('현재'→현재직급)은 거르지 않는다(DIF-069, 검수 L-2)
        if morph.is_adverb(word) and not any(_norm(word) in _norm(n) for n in names):
            continue

        if len(word) >= 2 and re.search(r"[가-힣A-Za-z]", word):
            words.append(word)
    return list(dict.fromkeys(words))


def _read_request(
    ask_many: AskMany, request: str, names: list[str], words: list[str], columns, ask_all=False
):
    """이름마다 역할, 모르는 말마다 뜻하는 열과 그 역할을 한 번에 묻는다. 순서를 뒤집어 두 번 묻고 같은 답만 받는다.
    ask_all이면 '문서 항목 전부를 하라는 요청인가'도 같은 물음에 칸 하나로 묻는다(DIF-063 3차).

    돌려주는 것: (이름 → 역할, 채우라는 말이 가리킨 열, 빼라는 말이 가리킨 열, 열과 잇지 못한 말,
    두 번 모두 '예'라고 답한 '전부' 여부).
    """
    word_options = (
        *(f"{c}/{FILL_ROLE}" for c in columns),
        *(f"{c}/{SKIP_ROLE}" for c in columns),
        NONE,
    )
    fields: dict[str, tuple[str, ...]] = {f"[이름] {n}": ROLES for n in names}
    fields |= {f"[말] {w}": word_options for w in words}
    if ask_all:
        fields[EVERYTHING_KEY] = EVERYTHING_OPTIONS

    def prompt(order) -> str:
        lines = [
            f"요청: {request}",
            "[이름]마다 요청에서의 역할을 고르세요. 채움=채우거나 대조하라는 항목, 키=행을 찾는 기준, "
            "제외=빼거나 하지 말거나 그대로 두거나 나중에 하라는 항목(빼라고 했으면 무관이 아니라 제외), "
            "무관=그 밖. 확신이 없으면 무관.",
            "[말]마다 그 말이 가리키는 취합표 열과, 요청이 그 열을 채우라는지(채움) 빼라는지(제외)를 "
            f"'열/역할'로 고르세요. 열 이름의 다른 말이 아니면 '{NONE}'.",
            *(
                [
                    f"{EVERYTHING_KEY}: 항목을 따로 말하지 않고 문서의 항목 전부를 채우거나 대조하라는 요청이면 "
                    "예. 어느 항목만(예: 'X만') 하라거나, 하지 말라거나, 확실하지 않으면 아니오."
                ]
                if ask_all
                else []
            ),
        ]
        return "\n".join([*lines, *(f"- {_clean(k, 40)}" for k in order)])

    def asked(order: dict[str, tuple[str, ...]]) -> dict[str, str]:
        # 계약을 어긴 답(목록 밖 이름·선택지)은 버린다(DIF-050 M6)
        answer = ask_many(prompt(list(order)), order)
        return {k: v for k, v in answer.items() if k in fields and v in fields[k]}

    keys = list(fields)
    flipped = {k: tuple(reversed(fields[k])) for k in reversed(keys)}
    # 두 번 묻기는 서로 독립이라 동시에 띄운다. 모델 적재·프롬프트 읽기가 겹쳐 실제 4B에서 약 25% 빨랐다
    # (DIF-061). 한쪽이 예외면 그대로 올린다(답을 반쪽만 쓰지 않는다)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_job, second_job = pool.submit(asked, fields), pool.submit(asked, flipped)
        first, second = first_job.result(), second_job.result()
    agreed = {k: v for k, v in first.items() if second.get(k) == v}
    roles = {k.removeprefix("[이름] "): v for k, v in agreed.items() if k.startswith("[이름] ")}
    meant = [v.rsplit("/", 1) for k, v in agreed.items() if k.startswith("[말] ") and v != NONE]
    unread = [w for w in words if agreed.get(f"[말] {w}", NONE) == NONE]
    skip_words = [
        w for w in words if agreed.get(f"[말] {w}", NONE).endswith(f"/{SKIP_ROLE}")
    ]  # 모델이 두 번 모두 어느 열의 빼기로 읽은 말
    return (
        roles,
        list(dict.fromkeys(c for c, r in meant if r == FILL_ROLE)),
        list(dict.fromkeys(c for c, r in meant if r == SKIP_ROLE)),
        unread,
        agreed.get(EVERYTHING_KEY) == EVERYTHING_OPTIONS[0],
        skip_words,
    )


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


def _rows_of(view: TargetView, column: str) -> dict[str, set[str]]:
    """열 값 → 그 값이 있는 행 번호들."""
    index = view.columns.index(column)
    letters = sorted(
        {c.address.rstrip("0123456789") for c in view.cells}, key=lambda s: (len(s), s)
    )
    rows: dict[str, set[str]] = {}
    if index >= len(letters):
        return rows
    for c in view.cells:
        value = c.value.value.strip()
        if c.address.rstrip("0123456789") == letters[index] and value:
            rows.setdefault(value, set()).add(c.address[len(letters[index]) :])
    return rows


def _same_rows(a, b, by_name, view: TargetView) -> bool:
    """키 후보 a와 후보 b (일치 수, 문서 항목, 열)가 각 문서를 다른 행으로 보내지 않는다. 남의 번호(추천인사번)가
    사번 열과 맞으면 성명과 다른 행을 가리킨다(H1). a 열 값은 모두 달라 한 행이다. b 값이 여러 행에 있으면
    b가 거의 식별자인 열(겹친 칸이 20% 이하, 작은 표는 1칸)일 때만 그 행들 안에 a의 행이 있는지 본다(검수 r2 M1).
    같은 단체·같은 달처럼 흔히 겹치는 열의 값은 행을 가리키지 못해 견주지 않는다. 열에 없는 값도 견주지
    않는다. 표본 수가 다르면(병합 칸이 있는 명단 표) 문서끼리 맞출 수 없어 같다고 보지 않는다(검수 r1 H1)."""
    rows_a, rows_b = _rows_of(view, a[2]), _rows_of(view, b[2])
    samples_a, samples_b = by_name[a[1]].samples, by_name[b[1]].samples
    if len(samples_a) != len(samples_b):
        return False
    filled = sum(len(r) for r in rows_b.values())
    # 겹친 칸이 20% 이하(작은 표는 1칸까지, 검수 r3)
    identifier = filled > 0 and filled - len(rows_b) <= max(1, 0.2 * filled)
    for x, y in zip(samples_a, samples_b, strict=True):
        if len(rows_a.get(x, ())) != 1 or y not in rows_b:
            continue
        if (len(rows_b[y]) == 1 or identifier) and not rows_a[x] <= rows_b[y]:
            return False
    return True


def _key_candidates(
    fields: list[FieldCandidate], view: TargetView, *, named: bool = False, any_hit: bool = False
) -> list[tuple[int, str, str]]:
    """(일치 수, 문서 항목, 대상 열): 문서 값이 그 열에 글자 그대로 있는 쌍.

    일치한 값은 식별자처럼 보여야 한다. 사용자가 이름이 같은 키를 말했으면 일치가 하나라도 있으면 된다.
    그 밖에는 문서 값의 60%(추정) 또는 80%(말한 키) 이상, 2건 이상이 그 열에 있어야 한다. 표에 없는 문서는 직접 처리로 남는다.
    추정할 때는 열 값이 모두 서로 달라야 하고(키는 행을 하나로 가려야 한다), 이름이 다른 열이면
    숫자로만 된 값(날짜·금액)으로는 잇지 않는다.
    """
    result = []
    for column in view.columns:
        values = _column_values(view, column)
        if not values:
            continue
        if not named and (
            len(values) < 2
            or len(set(values)) < len(values)  # 같은 값이 있는 열(대표자·단체명)은 키가 아니다
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
            # 키를 말했어도 이름이 다른 열은 값 한두 개의 우연한 일치로 잇지 않는다(6차 검수 H2).
            # 추정('확인 필요'로 표시)은 60%, 말한 키(표시 없음)는 80%(DIF-050 2차 L-5)
            strong = hits >= max(2, (0.8 if named else 0.6) * len(samples))
            if any_hit or (named and _same(field.name, column)) or strong:
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
    """선택지를 한 칸씩 돌려 두 번 묻는다. 같은 답이거나 두 번째가 '없음'으로 물러서면(선택지 둘 이상) 받고,
    다른 열로 바뀌면 버린다. 받은 짝(출처 model)은 채우고 '확인 필요'로 보인다.

    '없음'까지 돌리므로 모든 자리의 선택지가 바뀐다(뒤집기는 가운데 자리가 남는다, PR #18 3차 검수 L1).
    두 물음은 동시에 띄운다(DIF-061). 첫 답이 '없음'이거나 목록 밖이면 두 번째 답은 버린다.
    """
    options = tuple(o for o in options if o != NONE)  # '없음'이라는 열은 답과 구분할 수 없다
    if choose is None or not options:
        return None
    first = (*options, NONE)
    second = first[1:] + first[:1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_job = pool.submit(choose, prompt_for(options), first)
        second_job = pool.submit(choose, prompt_for(tuple(o for o in second if o != NONE)), second)
        answer, again = first_job.result(), second_job.result()
    if answer not in options:
        return None
    # 두 번째가 같은 답이거나 '없음'으로 물러서면 받는다(모델 짝은 '확인 필요'로 표시된다). 다른 열로 바뀌면 버린다
    # 선택지가 하나면 '없음' 물러섬은 받지 않는다(첫 자리만 고르는 편향을 거를 수 없다)
    return answer if again == answer or (again == NONE and len(options) > 1) else None


def interpret(
    request: str,
    documents: tuple[Document, ...],
    view: TargetView,
    *,
    choose: Chooser | None = None,
    ask_many: AskMany | None = None,
) -> Interpretation:
    """코드가 확정하는 것은 이름이 정규화 후 완전히 같은 짝과, 사용자가 지목한 키다(P04 ① 검수). 추정한 키(key_assumed)는 '확인 필요'로 보인다.

    그 밖의 짝(이름이 다른 것)은 모델이 선택지에서 고르고, 모델이 없거나 '없음'이면 빼고 소명한다.
    """
    questions: list[tuple[str, tuple[str, ...]]] = []
    columns = list(view.columns)
    probe = _mentions(request, columns + [f.name for f in _document_fields(documents)])
    record, fields = _pick_record(documents, view, probe)
    names = [f.name for f in fields]
    by_name = {f.name: f for f in fields}
    found = _mentions(request, columns + names)
    # "계좌는 개인정보라 빼고": 이유 말을 가리고 빼기를 읽는다(이름 순서는 같다)
    skip = _negated(
        _mentions(_blank(request, morph.aside_spans(request, later=False)), columns + names)
    )
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
    key_assumed = False  # 말하지 않은 키를 추정했다 → '확인 필요'
    guess = None  # 말하지 않은 키의 1순위 후보(질문의 첫 선택지)
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
        # 키는 원칙적으로 묻는다(2026-11-20 사용자 결정, 아래 예외는 2026-11-23). 값 일치 1위가
        # 뚜렷하면 질문의 첫 선택지로 둔다. 값 모양(번호처럼 생긴 열)으로 동점을 가르지 않는다(DIF-050 H1)
        candidates = _key_candidates(fields, view)
        if candidates and (len(candidates) == 1 or candidates[0][0] > 2 * candidates[1][0]):
            guess = candidates[0][2]
        # 예외: 이름이 같고 문서 값이 모두 그 열에 있는 후보가 있고, 값이 맞는 모든 후보가 각 문서를 같은 행으로
        # 보내면 키로 쓰고 '확인 필요'로 보인다(2026-11-23 사용자 결정). 어느 후보를 골라도 결과가 같다.
        # 요청이 다른 역할로 말한 이름은 키로 삼지 않는다
        spoken = {n for n, _ in found}
        sure = [
            c
            for c in candidates
            if _same(c[1], c[2])
            and c[0] == len([s for s in by_name[c[1]].samples if s])
            and not ({c[1], c[2]} & spoken)
            and view.columns.count(c[2]) == 1  # 머리글이 둘이면 어느 열인지 모른다(검수 r1 L2)
        ]
        # 값이 하나라도 맞는 모든 후보와 견준다. 약한 후보·값이 겹치는 열도 다른 행을 가리키면 묻는다(검수 r1 M1)
        rivals_all = _key_candidates(fields, view, named=True, any_hit=True)
        if sure and all(_same_rows(sure[0], c, by_name, view) for c in rivals_all):
            key, key_assumed = (sure[0][1], sure[0][2]), True
    if key is not None:
        # 행 전체를 봤고, 그 열에 값이 맞는 다른 문서 항목이 없을 때만(3차 H1 성명·추천인, 4차 H3 이름·이름)
        rivals = [
            c
            for c in _key_candidates(fields, view, named=True, any_hit=True)
            if c[2] == key[1] and c[1] != key[0]
        ]
        if rivals or not _covers_all_rows(view):
            key, key_assumed = None, False
    if key is None:
        options = tuple(
            dict.fromkeys(
                [
                    *([guess] if guess else []),
                    *(c[2] for c in _key_candidates(fields, view, named=True, any_hit=True)),
                ]
            )
        )
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
    pointed_names: list[str] = []
    slotted: list[int] = []  # "X 칸·빈칸"이 붙은 이름의 자리. 뒤에 넣을 다른 항목이 오면 지목이다
    for position, (name, after) in enumerate(mentions):
        if name == hint:
            if SLOT.match(after) or (DIRECTION.match(after) and not KEY_AFTER.match(after)):
                pointing_unclear = True  # 키로 말한 이름을 칸으로도 지목했다(5차 검수 M)
            continue
        column = _resolve(name, [c for c in columns if c != key_column])
        field = _resolve(name, [n for n in names if n != key_field])
        if column is not None and SLOT.match(after) and previous and OBJECT.match(previous[2]):
            if previous[1] is not None and POINTED.match(after):
                linked.append((previous[1], column))  # 지목한 칸이 같은 이름 짝보다 이긴다
                pointed_names += [previous[0], name]
            else:
                pointing_unclear = True
            if before_previous is not None and JOINED.match(before_previous):
                pointing_unclear = True  # 나열한 이름 가운데 어느 것을 넣을지 모른다
            previous = before_previous = None
            continue
        if SLOT.match(after):
            slotted.append(position)  # "이용일 칸 비어 있으면 채워"는 그 칸을 채우라는 말이다
        if DIRECTION.match(after) or re.match(r"^(은|는)\x00", after):
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
    # 지목은 요청에 단 하나, 그 두 이름 말고 다른 항목 이름이 없을 때만 확정한다("직급 이수시간을 비고 칸에", 5차 H1)
    # "X 칸"은 X 하나만 말했을 때만 그 칸을 채우라는 말이다. 앞이든 뒤든 다른 항목이 있으면
    # ("직급 비고 칸에", "이수시간 빈칸에 직급") 무엇을 넣을지 갈린다(DIF-050 H2)
    for position in slotted:
        name = mentions[position][0]
        rest = {n for n, _ in mentions} - {hint, name}
        if rest and name not in pointed_names:
            pointing_unclear = True
    others = {n for n, _ in mentions if n != hint and n not in pointed_names}
    if len(linked) > 1 or (linked and others):
        pointing_unclear = True
    for field, column in linked:  # 사용자가 칸을 지목했다
        pairs = [p for p in pairs if p[0] != field]
        if field in only_fields:
            only_fields.remove(field)
        pairs.append((field, column, "match"))
    pairs = [p for p in dict.fromkeys(pairs) if p[0] not in unclear and p[1] not in unclear]
    only_fields = [n for n in only_fields if n not in unclear]
    only_columns = [n for n in only_columns if n not in unclear]
    # 대조 요청에서 문서 항목 하나와 열 하나만 "A랑 B"로 이어 말했으면 사용자가 짝을 짚었을 수 있다
    # ("집계표 근무일수랑 보고서 출근일수 다른 사람"). 둘 다 같은 이름·동의어·모델로 짝을 못 찾았을 때만 서로
    # 짝짓는다(마지막 대안, 검수 r1 M-1). 채우기의 "A랑 B 채워"는 나열이라 짝으로 보지 않는다. 사용자가 같은
    # 이름을 말한 것이 아니므로 '확인 필요'(assumed)로 보인다(DIF-065)
    joined = (
        (only_fields[0], only_columns[0])
        if work_type == "check"
        and not pairs
        and not pointing_unclear
        and len(only_fields) == 1
        and len(only_columns) == 1
        and _said_together(mentions, hint)
        else None
    )
    # 애매한 지목이면 모두 묻는다. 모르는 말이 섞였으면 모델이 읽게 하고(있을 때만), 두 번 물어 모두 '채움'인
    # 이름만 남긴다. 빼기는 코드 표지(이미 뺐다)나 모델 '제외' 가운데 하나만 말해도 뺀다. 이 경로의 짝은
    # 모두 assumed로 표시해 미리보기에서 '확인 필요'로 보인다(P04 성능 개선, 2026-11-18 사용자 결정)
    mentioned = [*(p[0] for p in pairs), *(p[1] for p in pairs), *only_fields, *only_columns]
    assumed = False
    aliased: dict[str, str] = {}  # 동의어표로 이은 모르는 말 → 열
    alias_skip: list[str] = []
    # '전부'를 말했다: 모르는 말이 없으면 코드가, 섞였으면 모델이 두 번 모두 '예'라고 읽어야 한다(DIF-063 3차)
    # "다 채워줘"의 '다'는 형태소로 읽는다: 바로 뒤가 채우기·점검 동사일 때만('다른', '다 됐으면'은 아님)
    everything_word = bool(morph.everything_spans(request))
    everything_said = simple and everything_word
    skip_settled = (
        True  # 코드가 빼기로 읽은 이름을 모델도 빼기로 읽었다(모르는 말이 없으면 해당 없음)
    )
    skipped: list[str] = []
    unread: list[str] = []  # 모델이 열과 잇지 못한 모르는 말(2차 M-1)
    settled_words: list[str] = []  # 빼기가 확정된 말: '전부' 곁 마디에 와도 된다
    conflicted: list[str] = []
    if pointing_unclear or (not simple and ask_many is None):
        drop = set(mentioned)
    elif not simple:
        assumed = True
        every_word = _unknown_words(request, columns + names)
        words, overflow = (
            every_word[:8],
            every_word[8:],
        )  # 넘친 말은 묻지 않고 경고로 남긴다(3차 M3)
        targets = tuple(c for c in columns if c != key_column)
        # 동의어표: 요청의 모르는 말이 열 하나만 가리키면("생일"→생년월일) 그 열을 요청한 이름처럼 다룬다.
        # 채우라는 뜻인지는 다른 이름과 같이 모델의 역할 판단으로 정하고, 같은 마디에 빼기·미루기 표현이
        # 있으면 빼기로 본다(DIF-055, 검수 M1)
        # 동의어표에 없으면 끝 명사로 잇는다('계좌는 빼고'→지원계좌). 다른 줄인 말과 같이 '확인 필요'다
        for word in every_word:
            column = _synonym(word, targets) or _headed(word, targets)
            if column and column not in mentioned and column not in aliased.values():
                aliased[word] = column
        alias_skip = [c for w, c in aliased.items() if _said_to_skip(request, w)]
        words = [w for w in words if w not in aliased]
        overflow = [w for w in overflow if w not in aliased]
        coded_skip = [n for n in skip if n != hint]
        # 동의어로 추정한 열의 빼기는 그 열만 빼고 표시한다. 다른 칸까지 질문으로 돌리지 않는다(검수 L-1)
        asked_names = list(dict.fromkeys([*mentioned, *aliased.values(), *coded_skip]))
        try:
            roles, meant, meant_skip, unread, everything_said, skip_words = _read_request(
                ask_many, request, asked_names, words, targets, everything_word
            )
        except ServiceError as error:
            if not error.code.startswith("AI_"):
                raise
            roles, meant, meant_skip, unread, everything_said, skip_words = (
                {},
                [],
                [],
                [],
                False,
                [],
            )
        unread = [*unread, *overflow]
        # 코드가 빼기로 읽은 이름(NEGATED)은 오탐이 있다("직급 그대로 다 옮겨", "참고해서"). 모델도 두 번 모두
        # 빼기로 읽었을 때만 '빼고 전부'로 본다(3차 검수 M-1)
        # 형태소로 미루기가 확정된 이름("직무는 나중에 따로 정할게")은 모델 답이 엇갈려도 빼기로 인정한다. 비우는
        # 쪽이라 틀려도 덜 채울 뿐이다. 모델이 두 번 모두 채움·키라고 했으면 갈린 것으로 둔다(DIF-069)
        deferred = _named_in(request, coded_skip, morph.deferred_spans(request))
        skip_settled = all(
            roles.get(n) in (SKIP_ROLE, "무관") or (n in deferred and n not in roles)
            for n in coded_skip
        )
        settled_words += [
            *coded_skip,
            *skip_words,
            *(w for w, c in aliased.items() if c in alias_skip),
        ]
        for word, column in aliased.items():
            if column in alias_skip:
                continue
            if roles.get(column) == FILL_ROLE:
                meant = list(dict.fromkeys([*meant, column]))
            elif roles.get(column) != SKIP_ROLE:
                unread.append(word)  # 모델이 채우라고 읽지 않았다: 빠진 항목일 수 있어 알린다
        # 코드가 빼기로 읽은 이름은 모델이 두 번 모두 '채움'이 아닌 같은 답(제외·무관)이면 뺀다. 실제 4B는
        # 뺄 항목을 '무관'으로 답한다(2차 M-2). 답이 없거나 어긋나면 빼기로 skipped에 남긴다. 모델이
        # '채움'·'키'라고 답하거나 같은 열을 채우라고도 빼라고도 읽었으면 conflicted로 그 이름만 비운다
        # (DIF-050 H3·M2, DIF-062)
        skipped = list(
            dict.fromkeys(
                [
                    *(n for n, r in roles.items() if r == SKIP_ROLE),
                    *coded_skip,
                    *alias_skip,
                    *meant_skip,
                ]
            )
        )
        filled = {n for n, r in roles.items() if r == FILL_ROLE and n not in alias_skip} | set(
            meant
        )
        # 빼라는 말인지 갈린 이름(코드는 빼기, 모델은 아님 / 채우라고도 빼라고도 읽음)만 비워 두고 나머지는
        # 채운다. 전에는 하나만 갈려도 요청 전체를 물어 확실한 짝까지 버렸다(DIF-062, "애매한 칸은 비워 두고 소명")
        uncertain = list(
            dict.fromkeys(
                [
                    *(n for n in coded_skip if roles.get(n) not in (SKIP_ROLE, "무관")),
                    *(n for n in skipped if n in filled),
                ]
            )
        )
        conflicted = [  # 모델이 실제로 채움·키라고 답한 이름만. 답이 없으면(오류·두 답 어긋남) 갈린 것이 아니다
            n for n in uncertain if roles.get(n) in (FILL_ROLE, "키") or n in filled
        ]
        # 모델이 반박하지 않은 빼기(답 없음·어긋남)는 빼라는 말로 남겨 '확인 필요'로 보인다. 틀려도 덜 채울 뿐이다
        skipped = [n for n in skipped if n not in conflicted]
        # 갈린 이름은 짝 후보에서도, 모델 선택지(off_limits)에서도 뺀다. meant도 여기서 걸러진다
        drop = {n for n in mentioned if roles.get(n) != FILL_ROLE} | set(uncertain)
        for column in meant:  # "이름" → 봉사자명: 채우라는 말이 가리킨 열을 요청한 열로 더한다
            if column not in mentioned and column not in only_columns:
                only_columns.append(column)
    else:
        drop = set()
    held = [n for n in mentioned if n in drop and n not in skipped]
    pairs = [p for p in pairs if p[0] not in drop and p[1] not in drop]
    if assumed:
        pairs = [(f, c, "assumed") for f, c, _ in pairs]
    only_fields = [n for n in only_fields if n not in drop]
    only_columns = [n for n in only_columns if n not in drop]
    off_limits = skip | set(unclear) | drop | set(skipped)  # 뺀 열·이름은 모델 선택지에도 없다
    for field in only_fields:
        taken = {p[1] for p in pairs}
        options = _order(
            field,
            tuple(c for c in columns if c != key_column and c not in taken and c not in off_limits),
        )
        listed = _synonym(field, tuple(c for c in columns if c != key_column))
        if (
            listed in options
            and _synonym(listed, tuple(n for n in names if n != key_field)) == field
        ):
            # 동의어표: 양쪽 모두 후보가 하나뿐일 때만(DIF-055, 검수 M3)
            pairs.append((field, listed, "synonym"))
            continue
        answer = _ask_model(
            choose, lambda o, f=field: _column_prompt(request, by_name[f], o, view), options
        )
        if answer:
            pairs.append((field, answer, "model"))
    # 문서에 같은 이름 라벨이 있지만 겹쳐서 뺀 열: 답은 그 라벨뿐이라 다른 항목을 고르게 하지 않는다(6차 A01)
    asked_columns = list(only_columns)  # 겹친 라벨로 빠지기 전, 채우라고 말한(읽은) 열(검수 r2 L-4)
    ambiguous = _ambiguous_labels(documents)
    held += [c for c in only_columns if _norm(c) in ambiguous]
    only_columns = [c for c in only_columns if _norm(c) not in ambiguous]
    for column in only_columns:
        taken = {p[0] for p in pairs}
        options = _order(
            column,
            tuple(n for n in names if n != key_field and n not in taken and n not in off_limits),
        )
        listed = _synonym(column, tuple(n for n in names if n != key_field))
        if (
            listed in options
            and _synonym(listed, tuple(c for c in columns if c != key_column)) == column
        ):
            # 동의어표: 양쪽 모두 후보가 하나뿐일 때만(DIF-055, 검수 M3)
            pairs.append((listed, column, "synonym"))
            continue
        answer = _ask_model(
            choose, lambda o, c=column: _field_prompt(request, c, o, by_name, view), options
        )
        if answer:
            pairs.append((answer, column, "model"))
    if (
        joined
        and joined[0] in only_fields
        and joined[1] in only_columns
        and not any(p[0] == joined[0] or p[1] == joined[1] for p in pairs)
    ):
        pairs.append((*joined, "assumed"))

    # "전부/다 채워": 채울 대상을 하나도 말하지 않았으면 문서 항목 전부가 후보다. 키·뺀 이름은 빼고, 같은 이름 →
    # 동의어표 → 모델(한 번에 묻기) 차례로 짝짓는다. 사용자가 그 열을 말한 것이 아니므로 모두 '확인 필요'로
    # 보인다. 짝이 없는 항목은 비우고 소명한다(DIF-063). 켜는 조건은 좁게 둔다(검수 H-1~H-4, M-3)
    if (
        everything_said
        and _everything_requested(request, work_type)
        and not pointing_unclear
        # 문서 한 장이면 항목명과 값을 가르지 못한다(값이 항목으로 잡힌다)
        and (len(documents) > 1 or record.kind == "table_rows")
        # 말한 이름은 키뿐이어야 한다(코드가 빼기로 읽은 이름은 mentions에 없다). 모델만 빼라고 읽은 이름은
        # "직급만"이 "직급 빼고"로 뒤집힐 수 있어 끈다(검수 r2 M-1). 갈린 이름이 있어도 끈다(r2 M-2)
        and all(n == hint for n, _ in mentions)
        and not conflicted
        # 동의어·모델로 채우라고 읽은 열이 있으면 그것만 채우는 요청이다("교육시간만 다 채워")
        and not [c for c in aliased.values() if c not in alias_skip]
        and not asked_columns
        and skip_settled
        # "수료시간만 다 채워", "일부만": 어느 것만 하라는 말이면 전부가 아니다(3차 검수 H-2)
        and not morph.has_exclusive(request)
        # 빼라고 한 말을 모델이 열과 잇지 못했으면 무엇을 뺄지 모른다("평가는 빼고 다", 3차 검수 H-1)
        and not any(_said_to_skip(request, w) for w in unread)
        # '전부' 동사 밖의 마디("평가는 놔둬", "평가 외에는")는 빼기가 확정된 말·키로만 되어 있어야 한다.
        # 빼기 낱말 목록으로는 목록 밖 표현이 샌다(4차 검수 H-1)
        and _side_clauses_settled(request, settled_words, hint)
        # 같은 라벨이 문서에 두 번 이상 나오는 서식은 값 칸이 항목명으로 잡힌다(3차 검수 H-3)
        and not _ambiguous_labels(documents)
    ):
        # 값이 문서마다 우연히 같으면 그 값 칸이 항목명으로 잡히고("12시간", "팀장") 아래 칸 값을 가져간다.
        # 그러면 다른 항목과 값이 똑같아져 어느 쪽이 진짜인지 가를 수 없다. 값처럼 생긴 이름과, 값이 다른
        # 항목과 똑같은 후보는 모두 빼고 소명한다(덜 채울 뿐이다, 검수 r2 H-3)
        twins = {
            f.name for f in fields for g in fields if g.name != f.name and g.samples == f.samples
        } | _overflowed_values(documents, fields)
        candidates = [
            f
            for f in fields
            if f.name != key_field
            and f.name not in off_limits
            and _kind(f.name) == "text"
            and f.name not in twins
        ]
        everything = _match_all(
            request,
            candidates,
            tuple(c for c in columns if c != key_column and c not in off_limits),
            pairs,
            view,
            ask_many,
        )
        pairs += everything
        placed_names = {p[0] for p in pairs}
        held += [f.name for f in candidates if f.name not in placed_names]
        held += [  # 값이 똑같아 뺀 항목도 비워 둔 까닭을 알린다(진짜 항목이 말없이 사라지지 않게)
            n for n in twins if n != key_field and n not in off_limits and _kind(n) == "text"
        ]

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
        *conflicted,  # 빼기로 읽혀 짝 후보에서 빠졌어도 비워 둔 까닭을 알려야 한다
        *(p[0] for p in pairs),
        *(p[1] for p in pairs),
    ]
    excluded = tuple(dict.fromkeys(n for n in requested if n not in placed))
    if not mapped:
        questions.append(("target_column", tuple(c for c in columns if c != key_column)[:3]))
    record = replace(record, key_field=key_field or "")
    return Interpretation(
        work_type,
        record,
        key,
        mapped,
        tuple(questions),
        excluded,
        tuple(fields),
        skipped=tuple(skipped),
        unread=tuple(unread),
        conflicted=tuple(dict.fromkeys(conflicted)),
        key_assumed=key_assumed,
    )


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


FRAME_NOUNS = (
    *("기준", "칸", "빈칸", "열", "란", "행", "표", "시트", "엑셀", "문서", "서류"),
    *("취합표", "현황표", "명단", "신청서", "확인서", "요청서"),
)  # 자리·자료를 가리키는 말: "발급번호 기준으로 찾아서", "엑셀 보고"


KEY_STEMS = {"찾", "맞추", "대조", "매칭", "연결", "비교"}  # "발급번호로 찾아서": 키를 찾는 마디


def _side_clauses_settled(request: str, skips: list[str], key: str | None) -> bool:
    """'전부' 곁 마디마다 무엇을 하는지 확정됐다. 확정된 빼기 말이 있는 빼기 마디거나, 키가 있는 키 찾기 마디이고,
    나머지 내용어는 빼기 말·키·자리 낱말 안에 든다.

    내용어가 없는 마디("아니다, 하지 마"), 자리 낱말만 빼는 마디("표는 놔두고"), 가리키는 말만 있는 마디("그건
    건너뛰고")는 무엇을 하라는지 몰라 끈다(재확인 M-4·M-5).
    """

    def spans(words):
        return [
            (m.start(), m.end()) for w in words if w for m in re.finditer(re.escape(w), request)
        ]

    skip_spans, key_spans = spans(skips), spans([key] if key else [])
    known = skip_spans + key_spans + spans(FRAME_NOUNS)

    def inside(span, pool):
        return any(a <= span[0] and span[1] <= b for a, b in pool)

    def settled(stem, nouns):
        if not all(inside(n, known) for n in nouns):
            return False
        if any(inside(n, skip_spans) for n in nouns):
            return True
        return stem in KEY_STEMS and any(inside(n, key_spans) for n in nouns)

    return all(settled(stem, nouns) for stem, nouns in morph.side_clauses(request))


def _everything_requested(request: str, work_type) -> bool:
    """작업 종류가 요청에서 정해졌고, 채우지 말라는 말이 없고, '전부' 바로 뒤 같은 마디에 빼기 낱말이 없다.

    "전부 넣지 마"·"다 빼줘"·"전부 비워 둬"는 전부가 아니다(검수 H-4). 이 검사는 모델 판단과 함께(AND) 쓴다.
    """
    # "다 채우진 마"처럼 형태소로 읽은 하지 않는 채우기는 _work_type이 이미 거른다(작업 종류가 정해지지 않음)
    if work_type is None or FILL_NEGATED.search(request):
        return False
    for _, end_of_word in morph.everything_spans(request):
        rest = request[end_of_word:]
        hit, end = SKIP_WORDS.search(rest), CLAUSE_END.search(rest)
        if hit and (end is None or hit.start() < end.start()):
            return False  # 같은 마디에 빼기 낱말: "전부 빼고", "다 놔두고"
    return True


def _match_all(request, fields, columns, pairs, view, ask_many):
    """문서 항목마다 열을 정한다: 같은 이름 → 동의어표(양쪽 후보가 하나뿐) → 모델. 모델은 항목 전부를 한 번에
    묻고(JSON 객체 스키마), 항목·선택지 순서를 뒤집어 두 번 물어 같은 답만 받는다. 항목을 하나씩 물으면
    항목 수만큼 모델을 띄워 너무 느리다(10개면 3분 넘게)."""
    taken_fields = {p[0] for p in pairs}
    taken_columns = {p[1] for p in pairs}
    found: list[tuple[str, str, str]] = []
    left = []
    names = tuple(f.name for f in fields)
    for field in fields:
        if field.name in taken_fields:
            continue
        free = tuple(c for c in columns if c not in taken_columns)
        same = _resolve(field.name, free)
        listed = _synonym(field.name, free)
        if (
            same is not None
        ):  # 사용자가 그 열을 말한 것이 아니다: 같은 이름이라도 '확인 필요'(검수 H-3)
            found.append((field.name, same, "assumed"))
        elif listed is not None and _synonym(listed, names) == field.name:
            found.append((field.name, listed, "synonym"))
        else:
            left.append(field)
            continue
        taken_columns.add(found[-1][1])
    free = tuple(c for c in columns if c not in taken_columns)
    if ask_many is None or not left or not free:
        return found

    def allowed(field) -> tuple[str, ...]:
        # 이름은 같고 단위만 다른 열(예산(천원)↔예산(원))은 값이 틀리므로 선택지에서 뺀다(2차 N01, 검수 M-2)
        return tuple(
            c for c in free if _norm(c) != _norm(field.name) or _unit(c) == _unit(field.name)
        )

    asked = {f"[항목] {f.name}": (*allowed(f), NONE) for f in left}

    def prompt(order) -> str:
        lines = [
            f"요청: {request}",
            "[항목]마다 그 문서 항목의 값을 넣을 취합표 열을 고르세요. 같은 뜻의 열이 없으면 "
            f"'{NONE}'. 한 열에 두 항목을 고르지 마세요.",
            "취합표 열:",
            *(f"- {_clean(c, 40)}{_examples(_column_values(view, c)[:3])}" for c in free),
            "문서 항목:",
            # 문서 글이 지시처럼 읽히지 않게 라벨도 따옴표로 감싼다(3차 검수 L-1)
            *(f'- "{_clean(f.name, 40)}"{_examples(f.samples[:2])}' for f in order),
        ]
        return "\n".join(lines)

    def ask(order, flip):
        fields_asked = {
            k: (*reversed(asked[k][:-1]), NONE) if flip else asked[k]
            for k in (f"[항목] {f.name}" for f in order)
        }
        answer = ask_many(prompt(order), fields_asked)
        # 계약을 어긴 답(목록 밖 항목·선택지)은 버린다
        return {k: v for k, v in answer.items() if k in asked and v in asked[k]}

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_job = pool.submit(ask, left, False)
            second_job = pool.submit(ask, list(reversed(left)), True)
            first, second = first_job.result(), second_job.result()
    except ServiceError as error:  # 모델 오류면 모델 없이 계속하고 남은 항목은 소명한다(검수 M-1)
        if not error.code.startswith("AI_"):
            raise
        return found
    for field in left:
        answer = first.get(f"[항목] {field.name}")
        if answer and answer != NONE and second.get(f"[항목] {field.name}") == answer:
            found.append((field.name, answer, "model"))
    return found


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
