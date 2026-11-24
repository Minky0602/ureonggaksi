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


EVERYTHING_WORDS = {"전부", "모두", "다", "전체"}
# 점검 동사: 확인·대조·검토… + 하다, 맞다·같다(맞는지·같은지), 보다(봐 줘)
CHECK_NOUNS = {"확인", "대조", "검토", "비교", "점검", "검사", "검증", "체크"}
CHECK_STEMS = {"맞", "같", "보", "살피"}


def _is_check(tokens, i) -> bool:
    token = tokens[i]
    if token.tag in ("VA", "VV") and token.form in CHECK_STEMS:
        return True
    return (
        token.tag == "NNG"
        and token.form in CHECK_NOUNS
        and i + 1 < len(tokens)
        and tokens[i + 1].tag == "XSV"
    )


def _main_chain(tokens, j):
    """j의 채우기·점검 동사가 문장의 주 서술어면 그 동사 묶음이 끝나는 자리, 아니면 None.

    보조 용언('채워 주세요', '채워 놓아')과 점검 연결('맞는지 확인해')을 지나 종결 어미나 문장 끝에 닿아야
    한다. '다 채우면 곤란해'(조건)·'다 채우는 건 아닌 것 같아'(관형형)는 딸린 말이다(4차 검수 M-1).
    """
    found = _fill_end(tokens, j)
    if found is not None:
        k = found[0]
    elif not _is_check(tokens, j):
        return None
    else:
        k = j + 2 if tokens[j].tag == "NNG" else j + 1
    while k < len(tokens):
        token = tokens[k]
        nxt = tokens[k + 1] if k + 1 < len(tokens) else None
        if token.tag == "EP":
            k += 1
        elif token.tag == "EF":
            return k + 1
        elif token.tag == "ETN":  # 다 채우기만 해 주세요
            m = k + 1
            while m < len(tokens) and tokens[m].tag.startswith("J"):
                m += 1
            if (
                m >= len(tokens)
                or tokens[m].form != "하"
                or not tokens[m].tag.startswith(("VV", "VX"))
            ):
                return None
            k = m + 1
        elif token.tag != "EC":
            return None
        elif nxt is None or nxt.tag in ("SF", "SP"):
            return k + 1
        elif nxt.tag.startswith("VX") and nxt.form not in NEGATING_AUX:
            k += 2  # 채워 주세요, 채워 놓아
        elif token.form.endswith("지") and _is_check(tokens, k + 1):
            return _main_chain(tokens, k + 1)  # 다 맞는지 확인해 줘
        else:
            return None
    return k


def _everything_verbs(tokens) -> list[tuple[int, int, int]]:
    """(전부 낱말 자리, 동사 자리, 동사 묶음 끝) 목록."""
    found = []
    for i, token in enumerate(tokens):
        if token.form not in EVERYTHING_WORDS or token.tag not in ("MAG", "NNG"):
            continue
        j = i + 1
        while j < len(tokens) and (tokens[j].tag == "MAG" or tokens[j].tag.startswith("J")):
            j += 1
        end = _main_chain(tokens, j) if j < len(tokens) else None
        if end is not None:
            found.append((i, j, end))
    return found


def everything_spans(text: str) -> list[tuple[int, int]]:
    """'전부·모두·다·전체'가 주 서술어인 채우기·점검 동사를 꾸미는 자리("다 채워줘", "전부 맞는지 확인해 줘").

    '다른'(관형사)·'다 됐으면'(뒤가 '되다')·'모두 수고'(뒤가 명사)·'다 채우면 곤란해'(딸린 말)는 아니다.
    사이에는 부사("다 같이")와 조사("전부를")만 올 수 있다.
    """
    tokens = _tokens(text)
    return [(tokens[i].start, tokens[i].end) for i, _, _ in _everything_verbs(tokens)]


# 곁 마디의 내용어: 명사·외국어에 더해 대명사('그건')·수사·숫자·관형사('세 번째', '3열')도 무엇을 가리킨다(재확인 M-4)
CONTENT_TAGS = ("NNG", "NNP", "SL", "SH", "NP", "NR", "SN", "MM")
LIGHT_NOUNS = {"것", "거", "수", "쪽", "데"}  # 가리키는 대상이 없는 의존 명사
COURTESY = {"부탁", "감사", "고맙"}  # "다 채워줘, 부탁해": 인사는 마디로 치지 않는다
REASON_ENDINGS = {"라", "라서", "어서", "아서", "여서", "니까", "으니까"}  # "계좌는 개인정보라 빼"
LATER_NOUNS = {"나중", "추후"}  # "직무는 나중에 정할게": 미루는 때를 말하는 명사는 내용어가 아니다
EXCEPT_NOUNS = {"외", "이외", "제외"}  # 평가 외에는, 평가 이외 전부


def side_clauses(text: str) -> list[tuple[str, list[tuple[int, int]]]]:
    """'전부' 채우기 동사 묶음 밖에서 끝나는 마디마다 (서술어 어간, 내용어 글자 범위). 내용어가 없는 마디도 낸다.

    "평가는 놔둬, 다 채워줘"의 [평가], "평가 외에는 다 채워"의 [평가]. 다른 용언이 연결·종결 어미로 마디를
    끝내야 마디다. 관형형("보고서에 있는 거")은 꾸밈이라 마디를 끝내지 않는다. 마디를 끝내는 용언이
    '명사 + 하다'면 그 명사('생략하고'의 '생략')는 서술어라 내용어에서 뺀다(4차 검수 H-1). 이유를 대는
    '-이라(서)·-이니까'는 마디를 끝내지 않고 이유 말을 빼고 뒤 마디에 붙는다. '나중·추후'는 내용어가 아니다.
    """
    tokens = _tokens(text)
    chain = {k for _, j, end in _everything_verbs(tokens) for k in range(j, end)}
    clauses: list[tuple[str, list[tuple[int, int]]]] = []
    nouns: list[tuple[int, int]] = []
    topic = 0  # 이 마디에서 마지막 조사 앞까지의 내용어 수
    reason: list[tuple[int, int]] | None = None  # 이유를 대고 아직 끝나지 않은 마디의 내용어 전부

    def close_reason():
        # 이유만 대고 무엇을 할지 말하지 않은 마디("평가는 민감한 정보라서")는 미확정 마디로 남긴다(검수 r1 H-1)
        if reason is not None:
            clauses.append(("이", reason))

    for k, token in enumerate(tokens):
        if k in chain or token.tag in ("SF", "SP"):
            close_reason()
            nouns, topic, reason = [], 0, None
            continue
        if token.tag.startswith("J"):
            topic = len(nouns)
            continue
        if token.tag.startswith("N") and token.form in EXCEPT_NOUNS:
            clauses.append((token.form, nouns))
            nouns = []
            continue
        if token.tag.startswith(CONTENT_TAGS) or (
            token.tag == "NNB" and token.form not in LIGHT_NOUNS
        ):
            if token.form not in EVERYTHING_WORDS and token.form not in LATER_NOUNS:
                nouns.append((token.start, token.end))
            continue
        if not token.tag.startswith(("VV", "VA", "VX", "XSV", "XSA", "VCP", "VCN")):
            continue
        m = k + 1
        while m < len(tokens) and tokens[m].tag == "EP":
            m += 1
        if m < len(tokens) and tokens[m].tag not in ("EC", "EF"):
            continue  # 관형형·명사형은 마디를 끝내지 않는다
        if (
            m + 1 < len(tokens)
            and tokens[m].tag == "EC"
            and tokens[m + 1].tag == "VX"
            and tokens[m + 1].form not in NEGATING_AUX
        ):
            continue  # "빼 줘": 보조 용언이 마디를 끝낸다. "빼지 마"는 따로 마디가 된다(검수 r2 M-1)
        if token.tag == "VCP" and m < len(tokens) and tokens[m].form in REASON_ENDINGS:
            # "계좌는 개인정보라 빼": 이유를 대는 '-이라'는 마디를 끝내지 않고 뒤 마디에 붙는다. 이유
            # 말('개인정보')은 내용어가 아니고 주제('계좌')만 뒤 서술어의 대상으로 남는다
            reason = (reason or []) + nouns
            nouns = nouns[:topic]
            continue
        stem = token.form
        if token.tag.startswith(("XSV", "XSA")) and nouns and nouns[-1][1] == token.start:
            stem = text[nouns[-1][0] : nouns[-1][1]]
            nouns.pop()
        if stem not in COURTESY:
            clauses.append((stem, nouns))
        nouns, topic, reason = [], 0, None
    close_reason()
    return clauses


def aside_spans(text: str, *, later: bool = True) -> list[tuple[int, int]]:
    """열 이름의 다른 말이 아닌 곁말의 글자 범위: 이유 말("계좌는 [개인정보라] 빼")과 미루는 말("직무는
    [나중에 따로 정할게]"). 모르는 말로 모델에 넘기지 않는다. 마디를 어떻게 읽을지는 side_clauses가 정한다.
    later=False면 이유 말만 낸다(미루는 말은 그 자체가 빼기 표현이다)."""
    tokens = _tokens(text)
    spans: list[tuple[int, int]] = []
    begin = 0  # 이 마디에서 마지막 조사 뒤 첫 글자
    later_at = None  # 이 마디에서 '나중·추후'가 시작한 글자
    for k, token in enumerate(tokens):
        if token.tag in ("SF", "SP"):
            begin, later_at = token.end, None
            continue
        if token.tag.startswith(
            "J"
        ):  # 조사 뒤부터가 이유 말이다("연락처를 [개인정보라서]", 검수 r1 L-1)
            begin = token.end
            continue
        if token.tag.startswith("N") and token.form in LATER_NOUNS and later_at is None:
            later_at = token.start
        if token.tag not in ("EC", "EF"):
            continue
        previous = tokens[k - 1] if k else None
        if previous is not None and previous.tag == "VCP" and token.form in REASON_ENDINGS:
            spans.append((begin, token.end))
        elif later_at is not None and (k + 1 == len(tokens) or tokens[k + 1].tag != "VX"):
            if later:
                spans.append((later_at, token.end))
            later_at = None
        begin = token.end
    return spans


def modifying_fill_spans(text: str) -> list[tuple[int, int]]:
    """채우기 동사가 관형형으로 명사를 꾸미는 자리("신청서에 [적어 낸] 식구 수", "[채운] 칸"). 일을 시키는 말이
    아니라 대상을 설명한다(DIF-074 s02). 사이에는 연결 어미·보조 용언만 온다."""
    tokens = _tokens(text)
    spans = []
    for i, token in enumerate(tokens):
        if _fill_end(tokens, i) is None:
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].tag in ("EC", "VX", "XSV", "EP"):
            j += 1
        if j < len(tokens) and tokens[j].tag == "ETM":
            spans.append((token.start, tokens[j].end))
    return spans


def deferred_spans(text: str) -> list[tuple[int, int]]:
    """미루는 마디("직무는 [나중에 따로 정할게]")에서 '나중·추후' 앞에 온 명사의 글자 범위. 미루는 대상이다.

    마디는 문장 부호나 앞 마디의 연결·종결 어미에서 시작한다. 미루는 말의 범위는 aside_spans와 같게 읽는다.
    """
    tokens = _tokens(text)
    spans: list[tuple[int, int]] = []
    nouns: list[tuple[int, int]] = []  # 이 마디에서 '나중' 앞까지의 명사
    later = negated = False
    for k, token in enumerate(tokens):
        if token.tag in ("SF", "SP"):
            nouns, later, negated = [], False, False
            continue
        if token.tag.startswith("N") and token.form in LATER_NOUNS:
            later = True
        elif token.tag in ("NNG", "NNP") and not later:
            nouns.append((token.start, token.end))
        elif later and _negating(token):
            negated = True  # "나중에 할 거 없어": 미루기를 부정했다(검수 L-1)
        if token.tag not in ("EC", "EF"):
            continue
        if later and not negated and (k + 1 == len(tokens) or tokens[k + 1].tag != "VX"):
            spans += nouns
        nouns, later, negated = [], False, False
    return spans


def _negating(token) -> bool:
    """부정하는 말: '없다', '아니다', '안·못', 보조 용언 '않다·말다'."""
    return (
        (token.tag == "VA" and token.form == "없")
        or token.tag == "VCN"
        or (token.tag == "MAG" and token.form in NEGATING_ADVERBS)
        or (token.tag == "VX" and token.form in NEGATING_AUX)
    )


def head_noun(label: str) -> str | None:
    """여러 형태소로 된 이름의 끝 명사('지원계좌'→'계좌', '이수시간'→'시간'). 한 형태소 이름이나 끝이 명사가
    아니면 None. 괄호 안 단위는 보지 않는다."""
    text = label.split("(")[0].split("（")[0].strip()
    tokens = _tokens(text)
    if len(tokens) < 2 or tokens[-1].tag not in ("NNG", "NNP"):
        return None
    return tokens[-1].form


def is_adverb(word: str) -> bool:
    """부사로만 된 말('일단', '우선', '그냥'). 열 이름의 다른 말이 아니다."""
    tokens = _tokens(word)
    return bool(tokens) and all(t.tag in ("MAG", "MAJ") for t in tokens)


def has_exclusive(text: str) -> bool:
    """명사 뒤 보조사 '만'("직급만", "일부만", "행에만"). 어느 것만 하라는 말이다. 조사를 건너 본다(4차 검수 M-2)."""
    tokens = _tokens(text)
    for i, t in enumerate(tokens):
        if t.tag != "JX" or t.form != "만":
            continue
        b = i - 1
        while b >= 0 and tokens[b].tag.startswith("J"):
            b -= 1
        if b >= 0 and tokens[b].tag.startswith(("N", "SN", "SL", "SH")):
            return True
    return False
