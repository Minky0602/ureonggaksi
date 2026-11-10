"""HWPX 글자 해석: 특수 공백과 변경 추적 (실제 한글 저장본에서 확인한 구조, DIF-013)."""

import pytest
from conftest import cell, table, write_hwpx

from ureonggaksi.extract import TRACKED_CHANGE_MESSAGE, extract
from ureonggaksi.hwpx import read_hwpx
from ureonggaksi.workflow import Rule, Workflow


def raw_para(inner_xml: str) -> str:
    """<hp:t> 안에 표식 요소를 그대로 넣는 문단. conftest.para는 글자를 이스케이프한다."""
    return f"<hp:p><hp:run><hp:t>{inner_xml}</hp:t></hp:run></hp:p>"


def raw_cell(inner_xml: str, row: int, col: int) -> str:
    return (
        "<hp:tc><hp:subList>" + raw_para(inner_xml) + "</hp:subList>"
        f'<hp:cellAddr rowAddr="{row}" colAddr="{col}"/>'
        '<hp:cellSpan rowSpan="1" colSpan="1"/></hp:tc>'
    )


def read_texts(tmp_path, sections):
    return [
        (b.text, b.tracked_change)
        for b in read_hwpx(write_hwpx(tmp_path / "t.hwpx", sections)).blocks
    ]


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
    assert read_texts(tmp_path, [raw_para(inner)]) == [(expected, False)]


def test_highlight_and_title_marks_keep_text(tmp_path):
    inner = '<hp:markpenBegin color="#FFFF00"/>중요<hp:markpenEnd/> 사항<hp:titleMark tag="0"/>'
    assert read_texts(tmp_path, [raw_para(inner)]) == [("중요 사항", False)]


@pytest.mark.parametrize(
    "inner",
    [
        '변경<hp:deleteBegin Id="2" TcId="2"/> 추적<hp:deleteEnd Id="2" TcId="2" paraend="0"/>',
        '<hp:insertBegin Id="1" TcId="1"/>새 글자<hp:insertEnd Id="1" TcId="1" paraend="0"/>',
    ],
)
def test_tracked_change_cell_is_left_blank_and_flagged(tmp_path, inner):
    rows = [
        [cell("성명", 0, 0), raw_cell(inner, 0, 1)],
        [cell("기관명", 1, 0), cell("한빛초", 1, 1)],
    ]
    doc = read_hwpx(
        write_hwpx(tmp_path / "t.hwpx", ["<hp:p><hp:run>" + table(rows) + "</hp:run></hp:p>"])
    )
    by_address = {(b.row, b.col): b for b in doc.blocks if b.kind == "cell"}
    assert (by_address[(1, 2)].text, by_address[(1, 2)].tracked_change) == ("", True)
    assert (by_address[(2, 2)].text, by_address[(2, 2)].tracked_change) == ("한빛초", False)
    # 추출은 추측하지 않고, 왜 비었는지를 알려 준다. 다른 칸은 영향이 없다.
    result = extract(
        doc,
        Workflow(
            "명단", (Rule("성명", "label", query="성명"), Rule("기관명", "label", query="기관명"))
        ),
    )
    name, org = result.values
    assert (name.status, name.value, name.message) == ("missing", "", TRACKED_CHANGE_MESSAGE)
    assert (org.status, org.value) == ("ok", "한빛초")


def test_tracked_candidate_blocks_a_value_even_when_another_candidate_is_clean(tmp_path):
    """항목명이 두 곳이고 한쪽 값 칸만 변경 추적이면, 다른 쪽 값을 고르지 않는다(둘 이상 일치였을 수 있다)."""
    tracked = '<hp:deleteBegin Id="1" TcId="1"/>홍길동<hp:deleteEnd Id="1" TcId="1" paraend="0"/>'
    rows = [
        [cell("성명", 0, 0), raw_cell(tracked, 0, 1)],
        [cell("성명", 1, 0), cell("김철수", 1, 1)],
    ]
    doc = read_hwpx(
        write_hwpx(tmp_path / "t.hwpx", ["<hp:p><hp:run>" + table(rows) + "</hp:run></hp:p>"])
    )
    (name,) = extract(doc, Workflow("명단", (Rule("성명", "label", query="성명"),))).values
    assert (name.status, name.value, name.message) == ("missing", "", TRACKED_CHANGE_MESSAGE)


def test_track_change_mark_outside_text_element_is_detected(tmp_path):
    section = (
        '<hp:p><hp:run><hp:deleteBegin Id="1" TcId="1"/><hp:t>지운 글자</hp:t>'
        '<hp:deleteEnd Id="1" TcId="1" paraend="0"/></hp:run></hp:p>'
    )
    assert read_texts(tmp_path, [section]) == [("", True)]


def test_tracked_change_paragraph_keeps_an_empty_block(tmp_path):
    inner = '접수번호: <hp:deleteBegin Id="1" TcId="1"/>A-001<hp:deleteEnd Id="1" TcId="1" paraend="0"/>'
    assert read_texts(tmp_path, [raw_para(inner), raw_para("담당자: 가나다")]) == [
        ("", True),
        ("담당자: 가나다", False),
    ]


def test_documents_without_track_changes_are_not_flagged(sample):
    assert not any(b.tracked_change for b in read_hwpx(sample).blocks)
