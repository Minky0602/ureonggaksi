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
    if tokens and all(t.tag.startswith(FUNCTION_TAGS) for t in tokens):
        return ""
    end = len(word)
    for token in reversed(tokens):
        if not token.tag.startswith("J"):
            break
        end = token.start
    stem = word[:end]
    # 낱말 하나만 분석하면 '생일요'의 '요'를 따로 떨어진 명사로 읽는다. 그 '요'만 뗀다(검수 M3).
    # '조치필요'·'사업개요'는 '필요'·'개요'가 한 토큰이라 그대로 둔다(2차 검수 M-4)
    last = [t for t in tokens if t.start < end]
    if len(last) > 1 and last[-1].form == "요" and last[-1].start == end - 1:
        return strip_particles(word[: end - 1])  # '성함은요'→'성함은'→'성함'(매번 짧아져 끝난다)
    return stem


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
    if j < len(tokens) and tokens[j].form == "금지":  # 입력 금지('입력 금지된 칸'은 칸을 꾸민다)
        if j + 1 < len(tokens) and tokens[j + 1].form == "되":
            return None
        return j + 1, True
    return None


def _attributive(tokens, k) -> bool:
    """k 자리가 칸을 꾸미는 관형형('못 채운 칸', '않은 칸', '못 채운 거 채워줘')이면 하지 않는 채우기가 아니다(검수 M1).

    관형형 뒤가 명사일 때만 꾸밈이다. '-ㄴ 채로', '않는 게'(것+이), '않는 걸로', '말 것'(할 것, 명령)은 부정을
    남긴다(2차 검수 M-1·M-2).
    """
    if k + 1 >= len(tokens) or tokens[k].tag != "ETM" or not tokens[k + 1].tag.startswith("N"):
        return False
    noun = tokens[k + 1]
    if noun.form in ("채", "걸"):
        return False
    contracted = k + 2 < len(tokens) and tokens[k + 2].start == noun.start  # 게(것+이)·걸(것+을)
    command = tokens[k].form in ("ᆯ", "을") and noun.form in ("것", "거")  # 채우지 말 것
    return not (contracted or command)


def _forbids(tokens, k) -> bool:
    """k의 '안 되'가 금지인가. 앞이 '-어도·-면·-어서'(조사 'ㄴ·는·도' 허용)이거나 '-는 건'이어야 하고,
    '되' 뒤가 조건('-면')이나 관형형('안 되는 칸')이면 아니다."""
    before = tokens[k - 1]
    if before.tag == "JX" and k >= 2:
        before = tokens[k - 2]
    permitted = (before.tag == "EC" and before.form in PERMIT_ENDINGS) or before.form in (
        "거",
        "것",
    )
    after = tokens[k + 2] if k + 2 < len(tokens) else None
    conditional = after is not None and (
        after.tag == "ETM" or (after.tag == "EC" and after.form in ("면", "으면"))
    )
    return permitted and not conditional


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
            if token.form == "안" and k + 1 < len(tokens) and tokens[k + 1].form == "되":
                if _forbids(tokens, k):
                    spans.append((start, tokens[k + 1].end))  # 넣어도 안 돼, 채우면 안 돼
                break
            if (
                token.tag in ("EC", "JX", "EP")
                or (token.tag in ("VX", "VV") and token.form in PASSING_AUX)
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
