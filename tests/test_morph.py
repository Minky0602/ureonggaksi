"""형태소로 읽는 기능어: 조사 떼기와 하지 않는 채우기 (DIF-064)."""

import pytest

from ureonggaksi import morph
from ureonggaksi.interpret import _unknown_words, _work_type


@pytest.mark.parametrize(
    "word, stem",
    [
        ("평가는", "평가"),  # 정규식은 '가'를 조사로 먹어 '평'이 됐다(3차 검수 H-1)
        ("단가만", "단가"),
        ("경로는", "경로"),
        ("시도는", "시도"),
        ("계좌는", "계좌"),
        ("그대로", "그대로"),  # '로'로 끝나도 부사다
        ("신청서", "신청서"),
        ("생일요", "생일"),  # 낱말 하나만 보면 '요'를 명사에 붙인다(Kiwi PR 검수 M3)
        ("성함은요", "성함"),
        ("생일만요", "생일"),
        ("필요", "필요"),
        ("조치필요", "조치필요"),  # 한 토큰인 '필요'의 '요'는 떼지 않는다(2차 검수 M-4)
        ("사업개요", "사업개요"),
        ("주소지는요", "주소지"),
        ("같은", ""),  # 용언·어미뿐인 말은 이름이 아니다(검수 L2)
        ("않아도", ""),
        ("에서", ""),
    ],
)
def test_strip_particles_keeps_the_last_syllable_of_the_word(word, stem):
    assert morph.strip_particles(word) == stem


@pytest.mark.parametrize(
    "text",
    [
        "다 채우진 마",
        "채워주지 마",
        "안 채워도 돼",
        "입력하지 않아도 돼",
        "전부 넣지 마",
        "못 넣어",
        # Kiwi PR 검수 H1: 보조 용언이 끼거나 '안 되다'·명사형 '하다'로 하는 부정
        "채워 놓지 마",
        "넣어 두지 마",
        "넣어도 안 돼",
        "채워선 안 돼",
        "채워주면 안 돼",
        "채우는 건 안 돼",
        "입력하시면 안 됩니다",
        "입력은 하지 마",
        "채우는 거 하지 마",
        "채우려 하지 마",
        "입력 안 해도 돼",
        "입력 금지",
        "채우지 못하게 해",
        "채우시진 마세요",
        "적지 마",
        "옮기지 마",
        "옮겨 쓰지 마",  # 2차 검수 L-2: '쓰다'는 보조 용언으로 건너뛴다
        "채워 버리지 마",
        "채워 드리지 마세요",
        "채워 놓지 말 것",  # 2차 검수 M-2: '-지 말 것'
        "입력은 하지 말 것",
        "직급은 채우지 않는 게 좋겠어",  # 2차 검수 M-1: '않는 게'·'채로'는 꾸밈말이 아니다
        "직급은 채우지 않는 걸로 하고",
        "직급은 안 채운 채로 두고",
        "직급은 채우지 않은 채로 두고",
    ],
)
def test_negated_fill_is_read_from_the_auxiliary_verb(text):
    assert morph.negated_fill_spans(text)


@pytest.mark.parametrize(
    "text",
    [
        "다 채워줘",
        "채우고 확인해줘",
        "넣어 주세요",
        "말고 채워줘",
        "못 채운 칸 채워줘",  # 관형형은 칸을 꾸민다(검수 M1)
        "채우지 않은 칸 채워줘",
        "지난번에 못 채운 거 채워줘",
        "입력되지 않은 칸 채워줘",  # 피동(검수 M2)
        "이름은 키로 쓰지 말고 채워줘",  # '쓰다'는 사용하다일 수 있다
        "채우고 말 거야",  # '-고 말다'(검수 L3)
        "결국 다 채우고 말았어",
        "넣고 확인은 안 해도 돼",  # 부정은 확인에 걸린다
        "입력되지 않게 확인해줘",  # 피동 '입력되다'는 채우기가 아니다
        "채워줘, 확인은 하지 마",  # 채우기 뒤 다른 말을 넘어 먼 부정에 걸리지 않는다
        # 2차 검수 H-1: '-고 안 되면'은 조건이다
        "이수시간 채우고 안 되면 알려줘",
        "채워 주고 안 되면 비워 둬",
        "채우고 안 되는 건 비워 둬",
        "채워 주시고 안 되는 칸은 그냥 두세요",
        "입력하고 안 되면 비워 둬",
        "채우다가 안 되면 멈춰",
        "채우거나 안 되면 비워 둬",
        "채워 주되 안 되는 칸은 비워 둬",
        "채워도 안 되는 칸은 비워 두고 나머지 채워줘",  # '안 되는 칸'은 칸을 꾸민다
        "채울지 말지 애매한 칸은 비워 두고 채워줘",  # 2차 검수 L-1
        "입력 금지된 칸 빼고 채워줘",
    ],
)
def test_plain_fill_is_not_negated(text):
    assert morph.negated_fill_spans(text) == []


def test_unknown_words_keep_whole_nouns():
    words = _unknown_words("발급번호 기준으로 평가는 빼고 단가만 채워줘", ["발급번호"])
    assert "평가" in words and "단가" in words


@pytest.mark.parametrize(
    "request_text, expected",
    [
        ("발급번호 기준으로 다 채우진 마", None),  # 정규식 FILL_NEGATED 밖의 꼴(3차 검수 H-4)
        ("발급번호 기준으로 이수시간 입력하지 않아도 돼", None),
        (
            "직급 채워줘, 이수시간은 안 채워도 돼",
            None,
        ),  # 채우기와 하지 않는 채우기가 함께 있으면 묻는다
        ("발급번호 기준으로 이수시간 채워줘", "fill"),
        ("발급번호 기준으로 채우진 말고 확인해줘", "check"),
        ("안 채운 칸 채워줘", "fill"),  # 정규식의 관형형 오탐도 고쳤다(검수 M1)
        ("채우지 않은 칸만 채워줘", "fill"),
        ("못 채운 칸 채워줘", "fill"),
        ("입력되지 않은 칸 채워줘", "fill"),
        ("발급번호 기준으로 이수시간 채워 놓지 마", None),
        ("이름 기준으로 이수시간 채우고 안 되면 알려줘", "fill"),
        ("직급은 채우지 않는 게 좋겠어, 이수시간만 채워줘", None),  # 정규식 쪽도 같은 기준
        ("직급은 안 채운 채로 두고 이수시간 채워줘", None),
    ],
)
def test_work_type_reads_negation_from_morphemes(request_text, expected):
    assert _work_type(request_text) == expected


def test_unknown_words_drop_the_polite_ending_and_function_words():
    words = _unknown_words("발급번호 기준으로 생일요 같은 거 채워줘", ["발급번호"])
    assert "생일" in words and "같은" not in words and "생일요" not in words


def test_kiwi_is_built_once_even_when_first_called_from_many_threads(monkeypatch):
    """검수 L1: 처음 부를 때 여러 작업자가 동시에 와도 한 번만 적재한다."""
    import threading
    import time

    built = []
    real = morph.Kiwi

    def slow(*args, **kwargs):
        time.sleep(0.2)
        built.append(1)
        return real(*args, **kwargs)

    morph._kiwi_once.cache_clear()
    monkeypatch.setattr(morph, "Kiwi", slow)
    threads = [threading.Thread(target=morph._kiwi) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1


@pytest.mark.parametrize(
    "request_text, expected",
    [
        # 3차 검수 H-1: '안 될 것 같아'·'안 되는 거야'는 금지다
        ("직급은 채워선 안 될 것 같아", None),
        ("직급은 넣어도 안 될 것 같아요", None),
        ("채우시면 안 될 거예요", None),
        ("직급 채우는 건 안 되는 걸로 해", None),
        ("직급은 입력해서는 안 될 듯", None),
        ("직급은 채워선 안 되는 거야", None),
        # 3차 검수 H-2: 부사가 끼어도 금지다
        ("이수시간은 채우면 절대 안 돼", None),
        ("넣어서는 절대 안 됩니다", None),
        ("직급은 채우면 진짜 안 돼", None),
        # 3차 검수 M-1: 과거형 관형형 + 것(축약)은 칸을 꾸민다
        ("못 채운 걸 채워줘", "fill"),
        ("못 넣은 건 넣어줘", "fill"),
        ("안 채운 게 있으면 채워줘", "fill"),
        ("채우지 않은 걸 채워줘", "fill"),
        # 3차 검수 M-2: 띄어쓰기를 생략해도 꾸밈이다
        ("채우지 않은칸 채워줘", "fill"),
        ("안채운칸 채워줘", "fill"),
        ("채우지않은칸채워줘", "fill"),
        # 3차 검수 M-3: '-면 안 될까요?'는 부탁이다
        ("이수시간 채워 주시면 안 될까요?", "fill"),
        ("넣어 주시면 안 되나요?", "fill"),
        ("채워주면 안 돼?", "fill"),
        ("이수시간 채우면 안 될까요?", None),  # 묻는 말: 금지로 확정해 점검으로 넘기지 않고 묻는다
        # 3차 검수 L
        ("입력 금지 칸 빼고 채워줘", "fill"),
        ("입력 금지인 칸 빼고 채워줘", "fill"),
        ("이수시간 채우고 쓰지 마", "fill"),
        ("채워도 안 되면 비워 둬", "fill"),
        ("채워서 안 되면 비워 둬", "fill"),
        ("채우고 안 돼서 물어봐", "fill"),
    ],
)
def test_work_type_third_review_cases(request_text, expected):
    assert _work_type(request_text) == expected


@pytest.mark.parametrize(
    "word, stem", [("주소지요", "주소지"), ("비고요", "비고"), ("불필요", "불필요")]
)
def test_strip_particles_polite_ending_on_names(word, stem):
    assert morph.strip_particles(word) == stem
