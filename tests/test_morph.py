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
    ],
)
def test_negated_fill_is_read_from_the_auxiliary_verb(text):
    assert morph.negated_fill_spans(text)


@pytest.mark.parametrize("text", ["다 채워줘", "채우고 확인해줘", "넣어 주세요", "말고 채워줘"])
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
    ],
)
def test_work_type_reads_negation_from_morphemes(request_text, expected):
    assert _work_type(request_text) == expected
