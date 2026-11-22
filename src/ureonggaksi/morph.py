"""한국어 형태소 분석(Kiwi)으로 요청의 기능어(조사·어미·보조 용언)를 읽는다.

정규식으로 조사를 떼면 낱말 끝 글자까지 먹고('평가는'→'평'), 부정 꼴은 목록 밖에서 샌다('채우진 마').
품사로 읽으면 표현을 하나씩 더하지 않아도 된다(DIF-064). 이름 찾기는 interpret가 하고, 여기서는 기능어만 본다.
Kiwi는 처음 쓸 때 한 번 적재한다(오프라인). 다중 어절 사전을 빼고 적재하면 상주 약 0.28GB, 약 2초다(검수 실측).
"""

import threading
from functools import lru_cache

from kiwipiepy import Kiwi

# 채우기 동사의 어간. '입력·기입'은 명사 + 하다로 나뉜다. '쓰다'는 "키로 쓰다"(사용하다)와 갈리지 않아 넣지 않는다
FILL_STEMS = {"채우", "넣", "적", "옮기"}
FILL_NOUNS = {"입력", "기입"}
NEGATING_AUX = {"말", "않", "못하"}  # 채우지 말다·않다, 채우지 못하게
NEGATING_ADVERBS = {"안", "못"}  # 안 채워도, 못 넣어
# 채우기와 부정 사이에 올 수 있는 보조 용언: 채워 주지·채워 놓지·넣어 두지·채우려 하지
PASSING_AUX = {"주", "두", "놓", "버리", "드리", "하", "쓰"}  # 옮겨 쓰지 마
# '안 되'가 금지인 자리: '-어도·-면·-어서' 뒤("넣어도 안 돼", "채우면 안 돼"). '-고 안 되면'은 조건이다(2차 검수 H-1)
PERMIT_ENDINGS = {"어도", "아도", "면", "으면", "어서", "아서"}
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _kiwi_once() -> Kiwi:
    # 다중 어절 사전은 쓰지 않는다. 상주 메모리가 약 0.5GB에서 약 0.28GB로 준다(Kiwi PR 검수 M4)
    return Kiwi(num_workers=1, load_multi_dict=False)


def _kiwi() -> Kiwi:
    with _lock:  # 처음 부를 때 여러 작업자가 동시에 적재하지 않게(검수 L1)
        return _kiwi_once()


@lru_cache(maxsize=256)
def _tokens(text: str):
    return tuple(_kiwi().tokenize(text))


FUNCTION_TAGS = ("V", "E", "J", "XS", "VCP", "VCN")


def strip_particles(word: str) -> str:
    """낱말 끝의 조사(J*)와 부탁 '요'를 뗀다. '평가는'→'평가', '생일요'→'생일', '그대로'→'그대로'.

    용언·어미·조사뿐인 말('같은', '않아도', '돼요')은 이름이 아니라 ""를 돌려준다(검수 L2).
    """
    tokens = _tokens(word)
    # 끝 '요'는 부탁 표지로 떼고 다시 본다('생일요', '주소지요', '비고요'). 다만 '필요·개요·수요'처럼 '요'로 끝나는
    # 두 글자 이상 명사 토큰이면 낱말의 일부다(2·3차 검수 M-4·L-4). 매번 짧아져 끝난다
    if word.endswith("요") and len(word) > 2 and tokens:
        last = tokens[-1]
        if not (last.tag.startswith("N") and len(last.form) >= 2 and last.form.endswith("요")):
            return strip_particles(word[:-1])
    if tokens and all(t.tag.startswith(FUNCTION_TAGS) for t in tokens):
        return ""
    end = len(word)
    for token in reversed(tokens):
        if not token.tag.startswith("J"):
            break
        end = token.start
    return word[:end]


def _fill_end(tokens, i):
    """i가 채우기 동사면 그 동사(명사형은 '하')가 끝나는 토큰 다음 자리와 그 사이 '안' 여부. 아니면 None.

    '입력은 하지 마'·'입력 안 해도'처럼 명사와 '하' 사이에 조사·'안'이 올 수 있다. '입력되다'(피동)는 아니다.
    """
    token = tokens[i]
    if token.tag == "VV" and token.form in FILL_STEMS:
        return i + 1, False
    if token.tag != "NNG" or token.form not in FILL_NOUNS:
        return None
    j, adverb = i + 1, False
    while j < len(tokens) and (tokens[j].tag.startswith("J") or tokens[j].form == "안"):
        adverb = adverb or tokens[j].form == "안"
        j += 1
    if j < len(tokens) and tokens[j].form == "하" and tokens[j].tag in ("XSV", "VV"):
        return j + 1, adverb
    if j < len(tokens) and tokens[j].form == "금지":  # 입력 금지
        # '입력 금지된 칸'·'입력 금지인 칸'(되·이 + 관형형 + 명사)과 '입력 금지 칸'(명사)은 칸을 꾸민다(3차 검수 L-1).
        # '금지야'·'금지돼 있어'·'금지 처리(해)줘'·'금지 부탁드려요'는 금지를 서술하고, 꾸밈을 받은 명사가 다시
        # 서술어면('금지 칸이야', '금지된 걸로 하고') 역시 금지다(4차 검수 H-A·H-C·M-B)
        nxt = tokens[j + 1] if j + 1 < len(tokens) else None
        after = tokens[j + 2] if j + 2 < len(tokens) else None
        if (
            nxt is not None
            and nxt.form in ("되", "이")
            and after is not None
            and after.tag == "ETM"
        ):
            head = j + 3
            if (
                head < len(tokens)
                and tokens[head].tag.startswith("N")
                and not _restated(tokens, head)
            ):
                return None
        elif nxt is not None and nxt.tag.startswith("N"):
            predicate = after is not None and (
                after.tag == "XSV" or (after.tag.startswith("VV") and after.start == nxt.end)
            )
            head = j + 1
            while head + 1 < len(tokens) and tokens[head + 1].tag.startswith("N"):
                head += 1  # 명사가 이어지면 마지막 명사가 머리다('입력 금지 대상 항목이야')
            if not predicate and not _restated(tokens, head):
                return None
        return j + 1, True
    return None


def _restated(tokens, h) -> bool:
    """꾸밈을 받은 명사 h가 다시 서술어가 되는가. '…칸이야'(이다), '…걸로 하고'(것 + 부사격), '…게 낫겠어'·'…편이
    좋겠어'(이/가 + 형용사)는 꾸밈이 아니라 하지 않겠다는 말이다. '…칸이면'(조건)과 '…걸 채워'·'…게 있으면'은
    꾸밈이다(4차 검수 H-B·H-C·M-A)."""
    nxt = tokens[h + 1] if h + 1 < len(tokens) else None
    if nxt is None:
        return False
    if nxt.tag == "VCP":
        follow = tokens[h + 2] if h + 2 < len(tokens) else None
        return not (follow is not None and follow.tag == "EC" and follow.form in ("면", "으면"))
    if nxt.tag == "JKB" and tokens[h].form in ("것", "거", "걸"):
        return True
    return nxt.tag in ("JKS", "JKC") and h + 2 < len(tokens) and tokens[h + 2].tag.startswith("VA")


def _attributive(tokens, k) -> bool:
    """k 자리가 칸을 꾸미는 관형형('못 채운 칸', '않은 칸', '못 채운 거 채워줘')이면 하지 않는 채우기가 아니다(검수 M1).

    관형형 뒤가 명사일 때만 꾸밈이다. '-ㄴ 채로', '않는 게'(것+이), '않는 걸로', '말 것'(할 것, 명령)은 부정을
    남긴다(2차 검수 M-1·M-2).
    """
    if k + 1 >= len(tokens) or tokens[k].tag != "ETM" or not tokens[k + 1].tag.startswith("N"):
        return False
    noun = tokens[k + 1]
    if noun.form == "채":  # 안 채운 채로
        return False
    if _restated(tokens, k + 1):  # 채우지 않은 걸로 하고, 안 채운 게 낫겠어
        return False
    # '않는 게'·'않는 걸로'(현재형 + 것의 축약)는 하지 않겠다는 말이다. 과거형 '못 채운 걸 채워줘'·'안 채운 게
    # 있으면'은 칸을 꾸민다(3차 검수 M-1)
    contracted = k + 2 < len(tokens) and tokens[k + 2].start == noun.start  # 게(것+이)·걸(것+을)
    present = tokens[k].form == "는"
    command = tokens[k].form in ("ᆯ", "을") and noun.form in ("것", "거")  # 채우지 말 것
    return not ((present and (contracted or noun.form == "걸")) or command)


def _forbids(tokens, k) -> bool:
    """k의 '안 되'가 금지인가. 앞이 '-어도·-면·-어서'(조사 'ㄴ·는·도', '절대' 같은 부사 허용)이거나 '-는 건'이어야
    한다. '되' 뒤가 조건('-면'), 칸을 꾸미는 관형형('안 되는 칸'), 묻는 말('안 될까요?')이면 아니다.
    '안 될 것 같아'·'안 되는 거야'는 금지다(3차 검수 H-1·H-2·M-3)."""
    b = k - 1
    while b > 0 and tokens[b].tag == "MAG" and tokens[b].form not in NEGATING_ADVERBS:
        b -= 1  # 채우면 절대 안 돼
    before = tokens[b]
    if before.tag == "JX" and b >= 1:
        before = tokens[b - 1]
    permitted = (before.tag == "EC" and before.form in PERMIT_ENDINGS) or before.form in (
        "거",
        "것",
    )
    after = tokens[k + 2] if k + 2 < len(tokens) else None
    modifies = (
        after is not None
        and after.tag == "ETM"
        and k + 3 < len(tokens)
        and tokens[k + 3].tag in ("NNG", "NNP")
        and tokens[k + 3].form != "걸"  # '안 되는 걸로'의 '걸'은 것+을이다
    )
    conditional = after is not None and after.tag == "EC" and after.form in ("면", "으면")
    # 묻는 말인지는 '되'가 든 마디 안에서만 본다('넣어도 안 돼, 알겠지?'는 금지다, 4차 검수 L-1)
    clause_end = next((t for t in tokens[k + 2 :] if t.tag in ("SF", "SP")), None)
    asking = (clause_end is not None and clause_end.form == "?") or (
        after is not None
        and after.tag == "EF"
        and after.form.endswith(("까", "까요", "나", "나요", "니"))
    )
    return permitted and not (modifies or conditional or asking)


def negated_fill_spans(text: str) -> list[tuple[int, int]]:
    """하지 않는 채우기의 글자 범위. "다 채우진 마", "채워 놓지 마", "넣어도 안 돼", "입력은 하지 마"."""
    tokens = _tokens(text)
    spans = []
    for i in range(len(tokens)):
        found = _fill_end(tokens, i)
        if found is None:
            continue
        k, adverb = found
        start = tokens[i].start
        before = i > 0 and tokens[i - 1].tag == "MAG" and tokens[i - 1].form in NEGATING_ADVERBS
        if (before or adverb) and not _attributive(tokens, k):
            spans.append((tokens[i - 1].start if before else start, tokens[k - 1].end))
            continue
        while k < len(tokens):
            token = tokens[k]
            if token.tag == "VX" and token.form in NEGATING_AUX:
                # '-고 말다'는 끝내 하고 만다는 뜻이다("채우고 말 거야", 검수 L3). '채울지 말지'는 고르는 말이다
                done = tokens[k - 1].tag == "EC" and tokens[k - 1].form in ("고", "ᆯ지", "을지")
                if not done and not _attributive(tokens, k + 1):
                    spans.append((start, token.end))
                break
            if (
                token.tag == "MAG"
                and token.form not in NEGATING_ADVERBS
                and k + 1 < len(tokens)
                and tokens[k + 1].form == "안"
            ):
                k += 1  # '채우면 절대 안 돼'의 '절대'
                continue
            if token.form == "안" and k + 1 < len(tokens) and tokens[k + 1].form == "되":
                if _forbids(tokens, k):
                    spans.append((start, tokens[k + 1].end))  # 넣어도 안 돼, 채우면 안 돼
                break
            if (
                token.tag in ("EC", "JX", "EP")
                or (
                    token.tag in ("VX", "VV")
                    and token.form in PASSING_AUX
                    # '쓰다'는 '옮겨 쓰지'처럼 바로 붙을 때만 보조 용언이다('채우고 쓰지 마'는 아님, 3차 검수 L-2)
                    and (token.form != "쓰" or tokens[k - 1].form in ("어", "아"))
                )
                or (
                    token.tag == "ETM"
                    and k + 1 < len(tokens)
                    and tokens[k + 1].form in ("거", "것")
                )
                or (token.tag == "NNB" and token.form in ("거", "것"))
            ):
                k += 1
                continue
            break
    return spans
