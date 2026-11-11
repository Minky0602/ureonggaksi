"""값 정규화: 글자 → CellValue (PRD FR-10 수용 조건, api.md 2.1·5.1, DIF-027)."""

import pytest

from ureonggaksi.models import CellValue
from ureonggaksi.normalize import normalize

WON = "원"


@pytest.mark.parametrize(
    ("raw", "expected", "transform"),
    [
        ("350만원", "3500000", "단위 만원→원 ×10,000(글자)"),
        ("3,500,000원", "3500000", None),
        ("35,000천원", "35000000", "단위 천원→원 ×1,000(글자)"),
        ("3.5백만원", "3500000", "단위 백만원→원 ×1,000,000(글자)"),
        ("0.35억원", "35000000", "단위 억원→원 ×100,000,000(글자)"),
        ("350만", "3500000", "단위 만원→원 ×10,000(글자)"),  # 금액 종류라 '원'이 빠져도 단위다
        (
            "금3,500,000원(금삼백오십만원)",
            "3500000",
            None,
        ),  # 공문서 표기(행정업무 운영 규정 시행규칙 §2)
        ("３５０만원", "3500000", None),  # 전각 숫자
        ("△1,000원", "-1000", None),  # 회계 음수
        ("(1,000)원", "-1000", None),
        ("0원", "0", None),
    ],
)
def test_money_is_converted_to_won(raw, expected, transform):
    result = normalize(raw, "money")
    assert result.ok and result.value == CellValue("number", expected, WON)
    if transform:
        assert transform in result.transforms


def test_money_without_unit_needs_rule_unit_or_policy_answer():
    assert normalize("3,500,000", "money").reason_code == "ambiguous_unit"
    by_rule = normalize("3,500", "money", unit="천원")
    assert by_rule.value == CellValue("number", "3500000", WON)
    assert "단위 천원→원 ×1,000(규칙)" in by_rule.transforms
    by_answer = normalize("3,500", "money", default_unit="원")
    assert by_answer.value == CellValue("number", "3500", WON) and by_answer.transforms == ()
    # 글자의 단위가 규칙·답보다 우선한다
    assert normalize("350만원", "money", unit="천원").value.value == "3500000"


def test_zero_empty_and_undecided_are_distinguished():
    assert normalize("0", "money", unit="원").value == CellValue("number", "0", WON)
    empty = normalize("   ", "money", unit="원")
    assert (empty.value, empty.reason_code) == (CellValue(), "missing_value")
    assert normalize("미정", "money", unit="원").reason_code == "parse_failed"
    assert normalize("-", "money", unit="원").reason_code == "parse_failed"
    assert normalize("▲1,000원", "money").reason_code == "parse_failed"  # ▲는 뜻이 갈린다


def test_non_integer_won_needs_an_explicit_rounding_policy():
    pending = normalize("1.5원", "money")
    assert (pending.value, pending.reason_code) == (None, "needs_rounding")
    assert normalize("3.4567백만원", "money").value.value == "3456700"  # 정수면 그대로
    assert normalize("3.4567891백만원", "money").reason_code == "needs_rounding"
    # 공공 관행 3가지(DIF-027): 절사·절상·반올림. 음수는 0에서 먼 쪽이 절상이다
    assert normalize("1.5원", "money", rounding="down").value.value == "1"
    assert normalize("1.5원", "money", rounding="up").value.value == "2"
    assert normalize("1.5원", "money", rounding="half_up").value.value == "2"
    assert normalize("1.4원", "money", rounding="half_up").value.value == "1"
    assert normalize("△1.5원", "money", rounding="down").value.value == "-1"
    assert normalize("△1.5원", "money", rounding="up").value.value == "-2"
    assert "원 미만 절사" in normalize("1.5원", "money", rounding="down").transforms
    # 정수면 반올림 정책이 있어도 건드리지 않는다
    assert normalize("3.5천원", "money", rounding="down").value.value == "3500"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234", "1234"),
        ("12.50", "12.5"),
        ("△7", "-7"),
        ("(7)", "-7"),
        ("0", "0"),
        ("+7", "7"),
        ("007", "7"),
        ("１，０００", "1000"),
    ],
)
def test_numbers(raw, expected):
    assert normalize(raw, "number").value == CellValue("number", expected)


@pytest.mark.parametrize(
    "raw",
    [
        "350만원",
        "약 300",
        "15%",  # 계약에 백분율이 없다. 15로 읽으면 뜻이 바뀐다
        "3,500,00",  # 쉼표 자릿수가 틀리면 자릿수를 추측해야 한다(검수 H-1)
        "12,5",
        "1,2,3",
        "1,000,",
        "1 234",
        "(7",  # 괄호 짝이 안 맞는다
        "-(7)",
        "1e6",
        ".5",
        "1.",
    ],
)
def test_number_refuses_anything_that_needs_guessing(raw):
    assert normalize(raw, "number").reason_code == "parse_failed"


@pytest.mark.parametrize(
    ("raw", "unit", "expected"),
    [
        (
            "3,500(천원)",
            "원",
            "3500000",
        ),  # 괄호 안 단위가 규칙 단위보다 우선(검수 H-2, 1,000배 오류)
        ("3,500 (천원)", "", "3500000"),
        ("금 3,500,000 원", "", "3500000"),
        ("3,500,000.00원", "", "3500000"),
        ("350만 원", "", "3500000"),
        ("1.0원", "", "1"),
        ("0.0원", "", "0"),
    ],
)
def test_money_spelling_variants_are_read_correctly(raw, unit, expected):
    result = normalize(raw, "money", unit=unit)
    assert result.value == CellValue("number", expected, WON), result


@pytest.mark.parametrize(
    "raw", ["3,500,00원", "1,2,3원", "1,000,원", "3천5백만원", "1,000원 이상", "(금삼백오십만원)"]
)
def test_money_refuses_anything_that_needs_guessing(raw):
    assert normalize(raw, "money", unit="원").reason_code == "parse_failed"


def test_long_amounts_keep_every_digit():
    raw = "123456789012345678901234567890원"
    assert normalize(raw, "money").value.value == "123456789012345678901234567890"
    assert normalize("△" + raw, "money").value.value == "-123456789012345678901234567890"


def test_half_up_rounds_away_from_zero_at_point_five():
    assert normalize("2.5원", "money", rounding="half_up").value.value == "3"
    assert normalize("△2.5원", "money", rounding="half_up").value.value == "-3"


def test_invalid_policy_values_are_programming_errors():
    with pytest.raises(ValueError):
        normalize("1.5원", "money", rounding="nearest")
    with pytest.raises(ValueError):
        normalize("1/2/3", "date", date_order="ydm")
    with pytest.raises(ValueError):
        normalize("1", "money", default_unit="달러")


def test_text_keeps_leading_zeros_and_inner_spaces():
    assert normalize(" 0012 ", "text").value == CellValue("text", "0012")
    assert normalize("한빛  초등학교", "text").value == CellValue("text", "한빛  초등학교")
    assert normalize("", "text").reason_code == "missing_value"


@pytest.mark.parametrize(
    "raw",
    ["2026-11-06", "2026.11.06", "2026. 11. 6.", "2026년 11월 6일", "20261106", "2026/11/6"],
)
def test_unambiguous_dates(raw):
    assert normalize(raw, "date").value == CellValue("date", "2026-11-06")


def test_ambiguous_date_order_needs_a_policy_answer():
    assert normalize("11/06/2026", "date").reason_code == "parse_failed"
    assert normalize("11/06/2026", "date", date_order="mdy").value.value == "2026-11-06"
    assert normalize("06/11/2026", "date", date_order="dmy").value.value == "2026-11-06"
    # 두 자리 연도에 세기를 붙이는 것은 추측이다(검수 H-3)
    assert normalize("26/11/06", "date", date_order="ymd").reason_code == "parse_failed"
    assert normalize("99/12/31", "date", date_order="ymd").reason_code == "parse_failed"
    assert normalize("11/06/126", "date", date_order="mdy").reason_code == "parse_failed"
    assert normalize("2026-13-01", "date").reason_code == "parse_failed"
    assert normalize("2026-2-30", "date").reason_code == "parse_failed"
    assert normalize("11월 6일", "date").reason_code == "parse_failed"
    assert normalize("2026-11", "date").reason_code == "parse_failed"
    assert normalize("2026/11-06", "date").reason_code == "parse_failed"  # 구분자 혼용


def test_text_is_kept_verbatim_including_fullwidth_characters():
    """FR-11 글자 그대로 일치: 전각·기호를 바꾸면 키 대조가 어긋난다(검수 H-4)."""
    assert normalize("００１２", "text").value == CellValue("text", "００１２")
    assert normalize("（주）한빛，２", "text").value == CellValue("text", "（주）한빛，２")
