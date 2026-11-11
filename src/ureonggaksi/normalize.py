"""Turn document text into a CellValue with a fixed meaning (api.md 2.1, PRD FR-10).

Rules: never guess. Text stays text ("0012"). Money needs a unit (from the text, the rule or a
policy answer). Non-integer won results need an explicit rounding policy (DIF-027: public
documents truncate revenue, round up expenditure and round rates — the app cannot know which).
Anything that would require inventing information (a digit group, a century, a unit) is refused.
"""

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation, localcontext
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
ROUNDING_LABELS = {"down": "원 미만 절사", "up": "원 미만 절상", "half_up": "원 미만 반올림"}
DECIMAL_PRECISION = 60  # 기본 28자리는 긴 금액의 끝자리를 바꾼다(검수 M-7)

# 전각 숫자·구두점 → 반각. 숫자·금액·날짜에만 쓴다. text는 글자 그대로 둔다(FR-11 글자 일치).
_FULLWIDTH = str.maketrans("０１２３４５６７８９，．－＋（）％", "0123456789,.-+()%")
# 쉼표는 세 자리 묶음에만 허용한다. "3,500,00"·"12,5"는 자릿수를 추측해야 하므로 읽지 않는다(검수 H-1).
_DIGITS = r"(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d+)?"  # 첫 묶음이 0으로 시작하는 "0,500"은 유럽식 소수와 겹쳐 거부
_UNIT = r"(?:억|백만|만|천)\s*원?|원"  # "350만 원"처럼 띄어 쓴 것도 받는다
_MONEY = re.compile(
    rf"^(?:금\s*)?(?P<open>\()?\s*(?P<sign>[-+△])?\s*(?P<digits>{_DIGITS})\s*(?P<close>\))?"
    rf"\s*(?:(?P<unit>{_UNIT})|\(\s*(?P<unit_paren>{_UNIT})\s*\))?\s*(?P<jeong>정)?$"  # "3,500,000원정"
)
_NUMBER = re.compile(
    rf"^(?P<open>\()?\s*(?P<sign>[-+△])?\s*(?P<digits>{_DIGITS})\s*(?P<close>\))?$"
)
# 뒤의 (금삼백오십만원)은 확인용 글자라 읽지 않는다. 한글 숫자만 허용해 "(금액 미정)" 같은 주석은 거부로 남긴다
_HANGUL_AMOUNT = re.compile(r"\s*\(\s*금\s*[영일이삼사오육칠팔구십백천만억조\s]+원?\s*정?\s*\)\s*$")
# 구분자는 같은 것을 두 번 써야 한다("2026/11-06"은 읽지 않는다). "2026. 11. 6."의 끝 점은 허용
_DATE_YMD = re.compile(
    r"^(\d{4})\s*(?:([-./])\s*(\d{1,2})\s*\2|년\s*(\d{1,2})\s*월)\s*(\d{1,2})\s*(?:일|\.)?$"
)
_DATE_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_DATE_THREE = re.compile(r"^(\d{1,4})\s*([-./])\s*(\d{1,4})\s*\2\s*(\d{1,4})$")


@dataclass(frozen=True)
class Normalized:
    value: CellValue | None  # 못 읽었으면 None
    reason_code: str = ""  # 비면 성공. 아니면 api.md 5.1절 코드
    transforms: tuple[str, ...] = ()  # 근거에 남길 변환 설명. 예: "단위 만원→원 ×10,000(글자)"

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.reason_code


def _signed(match) -> Decimal | None:
    """부호·괄호를 해석한 Decimal. 괄호는 짝이 맞아야 하고(검수 L), 부호와 함께 쓰지 않는다."""
    opened, closed, sign = match.group("open"), match.group("close"), match.group("sign")
    if bool(opened) != bool(closed) or (opened and sign):
        return None
    try:
        number = Decimal(match.group("digits").replace(",", ""))
    except InvalidOperation:
        return None
    if sign in ("-", "△") or opened:  # △와 괄호는 회계 표기의 음수. ▲는 뜻이 갈려 받지 않는다
        number = -number
    return number


def _number_text(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


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
    rounding·date_order·unit에 허용 밖 값이 오면 ValueError(호출자의 실수).
    """
    if rounding is not None and rounding not in ROUNDING_MODES:
        raise ValueError(f"반올림 방법이 유효하지 않습니다: {rounding}")
    if date_order is not None and date_order not in ("ymd", "dmy", "mdy"):
        raise ValueError(f"날짜 순서가 유효하지 않습니다: {date_order}")
    for name, value in (("unit", unit), ("default_unit", default_unit)):
        if value and value not in UNITS:
            raise ValueError(f"{name}이 유효하지 않습니다: {value}")
    if not isinstance(raw, str) or not raw.strip():
        return Normalized(CellValue(), "missing_value")
    if value_type == "text":
        return Normalized(
            CellValue("text", raw.strip())
        )  # 전각·기호 그대로. 글자 일치 대조에 쓰인다
    text = raw.translate(_FULLWIDTH).strip()
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        if value_type == "number":
            match = _NUMBER.match(text)
            number = _signed(match) if match else None
            if number is None:
                return Normalized(None, "parse_failed")
            return Normalized(CellValue("number", _number_text(number)))
        if value_type == "money":
            return _money(text, unit, default_unit, rounding)
    if value_type == "date":
        return _date(text, date_order)
    raise ValueError(f"지원하지 않는 값 종류: {value_type}")


def _money(text: str, unit: str, default_unit: str, rounding: Rounding | None) -> Normalized:
    text = _HANGUL_AMOUNT.sub("", text)
    match = _MONEY.match(text)
    number = _signed(match) if match else None
    if number is None:
        return Normalized(None, "parse_failed")
    transforms: list[str] = []
    found = match.group("unit") or match.group("unit_paren")
    if found:
        found = re.sub(r"\s+", "", found)
        found, source = UNIT_ALIASES.get(found, found), "글자"
    elif unit:
        found, source = unit, "규칙"
    elif default_unit:
        found, source = default_unit, "답"
    else:
        return Normalized(None, "ambiguous_unit")
    factor = UNITS[found]
    amount = number * factor
    if factor != 1:
        transforms.append(f"단위 {found}→원 ×{factor:,.0f}({source})")
    if amount != amount.to_integral_value():
        if rounding is None:
            return Normalized(None, "needs_rounding", tuple(transforms))
        amount = amount.quantize(Decimal(1), rounding=ROUNDING_MODES[rounding])
        transforms.append(ROUNDING_LABELS[rounding])
    return Normalized(CellValue("number", _number_text(amount), "원"), "", tuple(transforms))


def _date(text: str, date_order: DateOrder | None) -> Normalized:
    match = _DATE_YMD.match(text)
    if match:
        year, _, month_a, month_b, day = match.groups()
        return _make_date(year, month_a or month_b, day)
    match = _DATE_COMPACT.match(text)
    if match:
        return _make_date(*match.groups())
    match = _DATE_THREE.match(text)
    if not match:
        return Normalized(None, "parse_failed")
    a, _, b, c = match.groups()
    if date_order is None:
        return Normalized(None, "parse_failed")  # 11/06/2026 같은 표기는 순서 답이 있어야 읽는다
    year, month, day = {"ymd": (a, b, c), "dmy": (c, b, a), "mdy": (c, a, b)}[date_order]
    return _make_date(year, month, day, transforms=(f"날짜 순서 {date_order}",))


def _make_date(year: str, month: str, day: str, transforms: tuple[str, ...] = ()) -> Normalized:
    if len(year) != 4:
        return Normalized(
            None, "parse_failed"
        )  # 두 자리 연도에 세기를 붙이는 것은 추측이다(검수 H-3)
    try:
        value = dt.date(int(year), int(month), int(day))
    except ValueError:
        return Normalized(None, "parse_failed")
    return Normalized(CellValue("date", value.isoformat()), "", transforms)
