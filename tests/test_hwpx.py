"""HWPX 글자 해석: <hp:t> 안의 특수 공백과 표식 (실제 한글 저장본에서 확인한 구조)."""

import pytest
from conftest import write_hwpx

from ureonggaksi.hwpx import read_hwpx


def raw_para(inner_xml: str) -> str:
    """<hp:t> 안에 표식 요소를 그대로 넣는 문단. conftest.para는 글자를 이스케이프한다."""
    return f"<hp:p><hp:run><hp:t>{inner_xml}</hp:t></hp:run></hp:p>"


def read_texts(tmp_path, sections):
    return [b.text for b in read_hwpx(write_hwpx(tmp_path / "t.hwpx", sections)).blocks]


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        ("한빛<hp:nbSpace/>초등학교", "한빛 초등학교"),  # 줄바꿈 금지 공백
        ("한빛<hp:fwSpace/>초등학교", "한빛 초등학교"),  # 고정폭 공백
        ("A<hp:tab/>B<hp:lineBreak/>C", "A\tB\nC"),  # 기존 동작 유지
        ("접수<hp:nbSpace/><hp:fwSpace/>번호", "접수  번호"),  # 연속 공백은 그대로 센다
    ],
)
def test_special_spaces_are_read_as_spaces(tmp_path, inner, expected):
    assert read_texts(tmp_path, [raw_para(inner)]) == [expected]


def test_space_outside_text_element_is_read(tmp_path):
    section = "<hp:p><hp:run><hp:t>한빛</hp:t><hp:nbSpace/><hp:t>초등학교</hp:t></hp:run></hp:p>"
    assert read_texts(tmp_path, [section]) == ["한빛 초등학교"]


def test_highlight_and_title_marks_keep_text(tmp_path):
    inner = '<hp:markpenBegin color="#FFFF00"/>중요<hp:markpenEnd/> 사항<hp:titleMark tag="0"/>'
    assert read_texts(tmp_path, [raw_para(inner)]) == ["중요 사항"]
