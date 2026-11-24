"""열 머리글 단위로 환산해 쓰기(DIF-079·080). 문서 금액을 '피해추산액(천원)' 열에 천원 단위 숫자로 쓴다."""

import pytest
from conftest import cell, para, table, write_hwpx, write_xlsx

from ureonggaksi.normalize import column_unit, normalize, strip_unit
from ureonggaksi.service import prepare


def _report(number, name, amount, area):
    rows = [("성명", name), ("피해액", amount), ("피해면적", area)]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return [para(f"접수번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


@pytest.fixture
def damage(tmp_path):
    people = [
        ("R-1", "가나다", "4,350,000원", "23.5㎡"),  # 빈칸 → 4350
        ("R-2", "라마바", "12,800,000원", "41㎡"),  # 기존 12800과 같은 값
        ("R-3", "사아자", "3000000", "12㎡"),  # 금액 단위 없음 → 비움
        ("R-4", "차카타", "2,900,500원", "8㎡"),  # 천원으로 나누면 소수
    ]
    sources = [write_hwpx(tmp_path / f"d{i}.hwpx", _report(*p)) for i, p in enumerate(people, 1)]
    sheet = [
        ["접수번호", "성명", "피해추산액(천원)", "피해면적(㎡)", "연락처(휴대폰)"],
        ["R-1", "가나다", None, None, None],
        ["R-2", "라마바", 12800, None, None],
        ["R-3", "사아자", None, None, None],
        ["R-4", "차카타", None, None, None],
    ]
    target = write_xlsx(tmp_path / "대장.xlsx", sheet, sheet="대장")
    return sources, target, tmp_path / "결과.xlsx"


def _items(prepared, field):
    return {i.record_key: i for i in prepared.plan.items if i.field == field}


def test_money_is_written_in_the_column_unit(damage):
    sources, target, out = damage
    prepared = prepare(
        "접수번호 기준으로 피해액이랑 피해면적 채워줘",
        sources,
        target,
        output_path=out,
        ask_many=lambda p, f: {k: "채움" for k in f if k.startswith("[이름]")},
        choose=lambda p, o: "피해추산액(천원)" if "피해추산액(천원)" in o else "없음",
    )
    money = _items(prepared, "피해액")
    assert (money["R-1"].proposed.kind, money["R-1"].proposed.value) == ("number", "4350")
    assert money["R-1"].write_selected
    assert "원→천원" in " ".join(money["R-1"].evidence[0].transforms)
    assert money["R-2"].status == "unchanged"  # 12,800,000원 = 12800천원. 전에는 '다름'으로 봤다
    assert money["R-3"].reason_code == "ambiguous_unit" and not money["R-3"].write_selected
    assert money["R-4"].reason_code == "needs_rounding" and not money["R-4"].write_selected
    area = _items(prepared, "피해면적")
    assert (area["R-1"].proposed.kind, area["R-1"].proposed.value) == ("number", "23.5")


@pytest.mark.parametrize(
    ("raw", "unit", "rounding", "value", "code"),
    [
        ("4,350,000원", "천원", None, "4350", ""),
        ("1,000만원", "만원", None, "1000", ""),
        ("500천원", "원", None, "500000", ""),
        ("4,350,500원", "천원", None, None, "needs_rounding"),
        ("4,350,500원", "천원", "half_up", "4351", ""),
        ("3000000", "천원", None, None, "ambiguous_unit"),
        ("미정", "천원", None, None, "parse_failed"),
    ],
)
def test_money_target_unit(raw, unit, rounding, value, code):
    result = normalize(raw, "money", target_unit=unit, rounding=rounding)
    assert result.reason_code == code
    assert (result.value.value if result.value else None) == value


@pytest.mark.parametrize(
    ("header", "unit"),
    [
        ("피해추산액(천원)", "천원"),
        ("금액 (단위: 천원)", "천원"),
        ("면적(㎡)", "㎡"),
        ("비고", ""),
        (
            "연락처(휴대폰)",
            "휴대폰",
        ),  # 단위가 아니어도 읽지만 금액·'숫자+단위'가 아니면 쓰지 않는다
    ],
)
def test_column_unit_reads_the_last_parenthesis(header, unit):
    assert column_unit(header) == unit


def test_strip_unit_only_takes_a_number_with_that_unit():
    assert strip_unit("23.5㎡", "㎡").value.value == "23.5"
    assert strip_unit("3km", "m") is None
    assert strip_unit("010-1234-5678", "휴대폰") is None


def test_a_bad_target_unit_is_a_caller_error():
    with pytest.raises(ValueError):
        normalize("1원", "money", target_unit="달러")
