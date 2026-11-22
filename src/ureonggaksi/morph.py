"""한국어 형태소 분석(Kiwi)으로 요청의 기능어(조사·어미·보조 용언)를 읽는다.

정규식으로 조사를 떼면 낱말 끝 글자까지 먹고('평가는'→'평'), 부정 꼴은 목록 밖에서 샌다('채우진 마').
품사로 읽으면 표현을 하나씩 더하지 않아도 된다(DIF-064). 이름 찾기는 interpret가 하고, 여기서는 기능어만 본다.
Kiwi는 처음 쓸 때 한 번 적재한다(약 1.4초, 오프라인).
"""

from functools import lru_cache

from kiwipiepy import Kiwi

# 채우기 동사의 어간. '입력·기입'은 명사 + 하다로 나뉜다
FILL_STEMS = {"채우", "넣", "적", "옮기", "쓰"}
FILL_NOUNS = {"입력", "기입"}
NEGATING_AUX = {"말", "않"}  # 채우지 말다, 채우지 않다
NEGATING_ADVERBS = {"안", "못"}  # 안 채워도, 못 넣어
# 동사와 부정 보조 용언 사이에 올 수 있는 것: 어미(지·어)·보조사(ㄴ·는·도·만)·주다·선어말 어미
BETWEEN = {"EC", "JX", "EP"}


@lru_cache(maxsize=1)
def _kiwi() -> Kiwi:
    return Kiwi(num_workers=1)


@lru_cache(maxsize=256)
def _tokens(text: str):
    return tuple(_kiwi().tokenize(text))


def strip_particles(word: str) -> str:
    """낱말 끝의 조사(J*)를 뗀다. '평가는'→'평가', '단가만'→'단가', '그대로'→'그대로'."""
    tokens = _tokens(word)
    end = len(word)
    for token in reversed(tokens):
        if not token.tag.startswith("J"):
            break
        end = token.start
    return word[:end] if end > 0 else word


def _is_fill(tokens, i) -> bool:
    token = tokens[i]
    if token.tag == "VV" and token.form in FILL_STEMS:
        return True
    return (
        token.tag == "NNG"
        and token.form in FILL_NOUNS
        and i + 1 < len(tokens)
        and tokens[i + 1].tag == "XSV"
    )


def negated_fill_spans(text: str) -> list[tuple[int, int]]:
    """하지 않는 채우기의 글자 범위. "다 채우진 마", "채워주지 마", "안 채워도 돼", "입력하지 않아도"."""
    tokens = _tokens(text)
    spans = []
    for i in range(len(tokens)):
        if not _is_fill(tokens, i):
            continue
        start = tokens[i].start
        if i > 0 and tokens[i - 1].tag == "MAG" and tokens[i - 1].form in NEGATING_ADVERBS:
            spans.append((tokens[i - 1].start, tokens[i].end))
            continue
        j = i + 1 + (tokens[i].tag == "NNG")  # 입력/NNG 하/XSV 다음부터
        while j < len(tokens):
            token = tokens[j]
            if token.tag == "VX" and token.form in NEGATING_AUX:
                spans.append((start, token.end))
                break
            if token.tag in BETWEEN or (token.tag == "VX" and token.form == "주"):
                j += 1
                continue
            break
    return spans
