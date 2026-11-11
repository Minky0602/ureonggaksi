"""Turn document text into a CellValue with a fixed meaning (api.md 2.1, PRD FR-10).

Rules: never guess. Text stays text ("0012"). Money needs a unit (from the text, the rule or a
policy answer). Non-integer won results need an explicit rounding policy (DIF-027: public
documents truncate revenue, round up expenditure and round rates — the app cannot know which).
"""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from typing import Literal

from .models import CellValue

ValueType = Literal["text", "number", "money", "date"]
Rounding = Literal["down", "up", "half_up"]  # 절사 · 절상 · 반올림 (공공 관행 3가지, DIF-027)
DateOrder = Literal["ymd", "dmy", "mdy"]

UNITS: dict[str, Decimal] = {
    "원": Decimal(1),
    "천원": Decimal(1_000),
    "만원": Decimal(10_000),
    "백만원": Decimal(1_000_000),
    "억원": Decimal(100_000_000),
}
# 금액 종류에서는 "350만"처럼 "원"이 빠진 단위도 받는다. 글자 뜻이 금액이라고 규칙이 말했기 때문이다.
UNIT_ALIASES = {"천": "천원", "만": "만원", "백만": "백만원", "억": "억원"}
ROUNDING_MODES = {"down": ROUND_DOWN, "up": ROUND_UP, "half_up": ROUND_HALF_UP}

# 전각 숫자·구두점 → 반각
_FULLWIDTH = str.maketrans("０１２３４５６７８９，．－＋（）", "0123456789,.-+()")
_MONEY = re.compile(
    r"^(?:금)?\s*(?P<sign>[-+△]|\()?\s*(?P<digits>\d[\d,]*(?:\.\d+)?)\s*\)?\s*(?P<unit>억원|백만원|만원|천원|원|억|백만|만|천)?\s*\)?"
    r"\s*(?:\([^)]*\))?$"  # 뒤의 (금…원) 괄호 한글은 읽지 않는다
)
_NUMBER = re.compile(r"^(?P<sign>[-+△]|\()?\s*(?P<digits>\d[\d,]*(?:\.\d+)?)\s*\)?\s*%?$")
_DATE_YMD = re.compile(r"^(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?\s*\.?$")
_DATE_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_DATE_THREE = re.compile(r"^(\d{1,4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,4})$")


@dataclass(frozen=True)
class Normalized:
    value: CellValue | None  # 못 읽었으면 None
    reason_code: str = ""  # 비면 성공. 아니면 api.md 5.1절 코드
    transforms: tuple[str, ...] = ()  # 근거에 남길 변환 설명. 예: "단위 만원→원 ×10000"

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.reason_code


def _clean(raw: str) -> str:
    return raw.translate(_FULLWIDTH).strip()


def _decimal(match) -> Decimal | None:
    digits = match.group("digits").replace(",", "")
    try:
        number = Decimal(digits)
    except InvalidOperation:
        return None
    sign = match.group("sign")
    if sign in ("-", "△", "("):  # △와 괄호는 회계 표기의 음수. ▲는 뜻이 갈려 받지 않는다
        number = -number
    return number


def _number_text(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _apply_rounding(
    amount: Decimal, rounding: Rounding | None, transforms: list[str]
) -> Normalized | Decimal:
    if amount == amount.to_integral_value():
        return amount
    if rounding is None:
        return Normalized(None, "needs_rounding", tuple(transforms))
    rounded = amount.quantize(Decimal(1), rounding=ROUNDING_MODES[rounding])
    transforms.append(
        {"down": "원 미만 절사", "up": "원 미만 절상", "half_up": "원 미만 반올림"}[rounding]
    )
    return rounded


def normalize(
    raw: str,
    value_type: ValueType,
    *,
    unit: str = "",
    default_unit: str = "",
    rounding: Rounding | None = None,
    date_order: DateOrder | None = None,
) -> Normalized:
    """raw 글자를 value_type의 CellValue로 바꾼다.

    unit: 규칙이 밝힌 원문 단위(예: 표 머리의 "단위: 천원"). 글자에 단위가 있으면 글자가 우선한다.
    default_unit: 질문 답(policy)으로 정한 기본 단위. 글자에도 규칙에도 단위가 없을 때만 쓴다.
    """
    text = _clean(raw)
    if not text:
        return Normalized(CellValue(), "missing_value")
    if value_type == "text":
        return Normalized(CellValue("text", text))
    if value_type == "number":
        match = _NUMBER.match(text)
        number = _decimal(match) if match else None
        if number is None:
            return Normalized(None, "parse_failed")
        return Normalized(CellValue("number", _number_text(number)))
    if value_type == "money":
        return _money(text, unit, default_unit, rounding)
    if value_type == "date":
        return _date(text, date_order)
    raise ValueError(f"지원하지 않는 값 종류: {value_type}")


def _money(text: str, unit: str, default_unit: str, rounding: Rounding | None) -> Normalized:
    match = _MONEY.match(text)
    number = _decimal(match) if match else None
    if number is None:
        return Normalized(None, "parse_failed")
    transforms: list[str] = []
    found = match.group("unit")
    if found:
        found = UNIT_ALIASES.get(found, found)
        source = "글자"
    elif unit:
        found, source = unit, "규칙"
    elif default_unit:
        found, source = default_unit, "답"
    else:
        return Normalized(None, "ambiguous_unit")
    if found not in UNITS:
        return Normalized(None, "ambiguous_unit")
    factor = UNITS[found]
    amount = number * factor
    if factor != 1:
        transforms.append(f"단위 {found}→원 ×{factor:,.0f}({source})")
    result = _apply_rounding(amount, rounding, transforms)
    if isinstance(result, Normalized):
        return result
    return Normalized(CellValue("number", _number_text(result), "원"), "", tuple(transforms))


def _date(text: str, date_order: DateOrder | None) -> Normalized:
    for pattern in (_DATE_YMD, _DATE_COMPACT):
        match = pattern.match(text)
        if match:
            return _make_date(*match.groups())
    match = _DATE_THREE.match(text)
    if not match:
        return Normalized(None, "parse_failed")
    a, b, c = match.groups()
    if len(a) == 4:
        return _make_date(a, b, c)
    if date_order is None:
        return Normalized(None, "parse_failed")  # 11/06/26 같은 표기는 순서 답이 있어야 읽는다
    year, month, day = {"ymd": (a, b, c), "dmy": (c, b, a), "mdy": (c, a, b)}[date_order]
    if len(year) == 2:
        year = "20" + year
    return _make_date(year, month, day, transforms=(f"날짜 순서 {date_order}",))


def _make_date(year: str, month: str, day: str, transforms: tuple[str, ...] = ()) -> Normalized:
    try:
        value = dt.date(int(year), int(month), int(day))
    except ValueError:
        return Normalized(None, "parse_failed")
    return Normalized(CellValue("date", value.isoformat()), "", transforms)
