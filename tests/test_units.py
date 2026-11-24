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


@pytest.mark.parametrize(
    ("header", "raw"),
    [("비고(1)", "51"), ("비고(1)", "101"), ("구분(2차)", "12차"), ("점수(100)", "1100")],
)
def test_a_parenthesis_with_digits_is_not_a_unit(header, raw):
    """검수 H: 숫자가 든 괄호를 단위로 읽으면 "51"이 5로 잘렸다."""
    assert column_unit(header) == ""
    assert strip_unit(raw, "1") is None


def test_percent_stays_text():
    """검수 M1: 백분율 서식 칸이면 5가 500%로 보인다. 글자로 둔다."""
    assert strip_unit("5%", "%") is None


def test_rounding_happens_once_in_the_column_unit():
    """검수 M3: 4,350,499.5원을 원에서 먼저 반올림하면 4,350,500 → 4351이 된다. 정확한 값은 4350."""
    result = normalize("4,350,499.5원", "money", target_unit="천원", rounding="half_up")
    assert result.value.value == "4350"


def test_existing_text_with_the_same_unit_is_unchanged(tmp_path):
    """검수 M2: 기존 칸 글자 "4회"와 문서 "4회"는 숫자 4로 바꿔도 같은 값이다(대조에서 거짓 불일치 없음)."""
    rows = [("성명", "가나다"), ("방문횟수", "4회")]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    source = write_hwpx(
        tmp_path / "d.hwpx", [para("접수번호: R-1") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]
    )
    sheet = [["접수번호", "방문횟수(회)"], ["R-1", "4회"]]
    target = write_xlsx(tmp_path / "집계.xlsx", sheet, sheet="집계")
    prepared = prepare(
        "접수번호 기준으로 방문횟수가 맞는지 확인해줘",
        [source],
        target,
        output_path=tmp_path / "o.xlsx",
        ask_many=lambda p, f: {k: "채움" for k in f if k.startswith("[이름]")},
    )
    item = _items(prepared, "방문횟수")["R-1"]
    assert item.status == "unchanged", item.reason_code


def test_old_won_text_left_in_a_thousand_won_column_is_not_the_same(tmp_path):
    """검수 r2 H: v0.1.0이 천원 열에 남긴 "4,350,000원" 글자는 문서 원문과 같아도 열 단위로는 다른 값이다."""
    rows = [("성명", "가나다"), ("피해액", "4,350,000원")]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    source = write_hwpx(
        tmp_path / "d.hwpx", [para("접수번호: R-1") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]
    )
    sheet = [["접수번호", "피해액(천원)"], ["R-1", "4,350,000원"]]
    target = write_xlsx(tmp_path / "대장.xlsx", sheet, sheet="대장")
    for request in (
        "접수번호 기준으로 피해액 채워줘",
        "접수번호 기준으로 피해액이 맞는지 확인해줘",
    ):
        prepared = prepare(
            request,
            [source],
            target,
            output_path=tmp_path / "o.xlsx",
            ask_many=lambda p, f: {k: "채움" for k in f if k.startswith("[이름]")},
        )
        item = _items(prepared, "피해액")["R-1"]
        assert item.status != "unchanged" and item.reason_code in ("existing_differs", "mismatch")
