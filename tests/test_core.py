import json
from dataclasses import replace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from conftest import cell, para, table, write_hwpx

from ureonggaksi.extract import extract, verify
from ureonggaksi.hwpx import HwpxError, read_hwpx
from ureonggaksi.workflow import Rule, Workflow, WorkflowError


def workflow(*rules):
    return Workflow("지원서 취합", tuple(rules))


def test_real_zip_extract_and_verify(sample):
    doc = read_hwpx(sample)
    wf = workflow(
        Rule("성명", "label", query="성명"),
        Rule("접수번호", "regex", query=r"접수번호:\s*(\S+)"),
        Rule("기관명", "cell", section="Contents/section0.xml", table=1, row=2, col=2),
    )
    result = extract(doc, wf)
    assert [v.value for v in result.values] == ["홍길동", "A-001", "한빛초등학교"]
    assert all(v.status == "ok" for v in result.values)
    assert all(verify(v).ok for v in result.values)
    assert result.values[0].source.locator.endswith("/tc[2]")
    assert result.values[1].source.text[result.values[1].start : result.values[1].end] == "A-001"


def test_changed_source_and_tampered_value_fail(sample):
    value = extract(read_hwpx(sample), workflow(Rule("이름", "label", query="성명"))).values[0]
    assert not verify(replace(value, value="가짜")).ok
    write_hwpx(sample, [para("변경됨")])
    assert not verify(value).ok


def test_ambiguous_and_missing_are_not_guessed(tmp_path):
    p = write_hwpx(tmp_path / "a.hwpx", [para("ID: 1") + para("ID: 2")])
    values = extract(
        read_hwpx(p),
        workflow(
            Rule("아이디", "regex", query=r"ID: (\d+)"), Rule("없는항목", "label", query="없음")
        ),
    ).values
    assert [v.status for v in values] == ["ambiguous", "missing"]
    assert values[0].value == "" and values[0].matches == 2


def test_merged_cell_right_uses_span(tmp_path):
    xml = (
        "<hp:p><hp:run>"
        + table([[cell("성 명:", 0, 0, colspan=2), cell("김영희", 0, 2)]])
        + "</hp:run></hp:p>"
    )
    p = write_hwpx(tmp_path / "merged.hwpx", [xml])
    assert (
        extract(read_hwpx(p), workflow(Rule("이름", "label", query="성명"))).values[0].value
        == "김영희"
    )


def test_nested_table_does_not_pollute_outer_cell(tmp_path):
    inner = table([[cell("안쪽", 0, 0)]])
    outer_cell = cell("바깥쪽", 0, 0).replace(
        "</hp:subList>", "<hp:p><hp:run>" + inner + "</hp:run></hp:p></hp:subList>"
    )
    p = write_hwpx(
        tmp_path / "nested.hwpx", ["<hp:p><hp:run>" + table([[outer_cell]]) + "</hp:run></hp:p>"]
    )
    cells = [b for b in read_hwpx(p).blocks if b.kind == "cell"]
    assert [b.text for b in cells] == ["바깥쪽", "안쪽"]


def test_spine_order_and_split_runs(tmp_path):
    manifest = '<package><manifest><item id="s1" href="section1.xml"/><item id="s0" href="section0.xml"/></manifest><spine><itemref idref="s1"/><itemref idref="s0"/></spine></package>'
    p = write_hwpx(
        tmp_path / "sections.hwpx",
        [
            para("둘째"),
            "<hp:p><hp:run><hp:t>홍</hp:t></hp:run><hp:run><hp:t>길동</hp:t></hp:run></hp:p>",
        ],
        {"Contents/content.hpf": manifest},
    )
    assert [b.text for b in read_hwpx(p).blocks] == ["홍길동", "둘째"]


def test_hancom_package_root_paths_and_header_spine(tmp_path):
    manifest = '<package><manifest><item id="header" href="Contents/header.xml"/><item id="s" href="Contents/section0.xml"/></manifest><spine><itemref idref="header"/><itemref idref="s"/></spine></package>'
    p = write_hwpx(
        tmp_path / "hancom.hwpx", [para("실제 한글 경로")], {"Contents/content.hpf": manifest}
    )
    assert read_hwpx(p).blocks[0].text == "실제 한글 경로"


def test_cell_retains_internal_blank_paragraphs(tmp_path):
    c = cell("첫째", 0, 0).replace("</hp:subList>", para("") + para("셋째") + "</hp:subList>")
    p = write_hwpx(tmp_path / "blank.hwpx", ["<hp:p><hp:run>" + table([[c]]) + "</hp:run></hp:p>"])
    assert read_hwpx(p).blocks[0].text == "첫째\n\n셋째"


@pytest.mark.parametrize(
    "member,content",
    [
        ("../outside.xml", "x"),
        ("Contents/section1.xml", '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><x>&e;</x>'),
        ("Contents/section1.xml", "<broken>"),
    ],
)
def test_bad_zip_xml_rejected(tmp_path, member, content):
    p = write_hwpx(tmp_path / "bad.hwpx", extra={member: content})
    with pytest.raises(HwpxError):
        read_hwpx(p)


def test_limits_and_duplicate_entries(tmp_path):
    p = write_hwpx(tmp_path / "bomb.hwpx", [para("a" * 100_000)])
    with pytest.raises(HwpxError):
        read_hwpx(p, max_xml_bytes=100)
    with ZipFile(p, "a", ZIP_DEFLATED) as z, pytest.warns(UserWarning):
        z.writestr("Contents/section0.xml", "<x/>")
    with pytest.raises(HwpxError):
        read_hwpx(p)


def test_workflow_roundtrip_and_strict_validation(tmp_path):
    wf = workflow(Rule("이름", "label", query="성명"))
    path = tmp_path / "workflow.json"
    wf.save(path)
    assert Workflow.load(path) == wf
    for data in [
        {"version": 99, "name": "x", "rules": []},
        {"version": 1, "name": "x", "rules": [{"name": "x", "kind": "shell"}]},
        {"version": 1, "name": "x", "rules": [{"name": "x", "kind": "cell", "row": True}]},
    ]:
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(WorkflowError):
            Workflow.load(path)
    with pytest.raises(WorkflowError):
        workflow(Rule("중복", "label", query="x"), Rule("중복", "label", query="y"))


def test_regex_budget_and_unmatched_optional_group(tmp_path):
    p = write_hwpx(tmp_path / "regex.hwpx", [para("a" * 30_000 + "!")])
    result = extract(read_hwpx(p), workflow(Rule("폭탄", "regex", query=r"(a+)+$", group=0)))
    assert result.values[0].status == "error"
    p = write_hwpx(p, [para("a")])
    value = extract(read_hwpx(p), workflow(Rule("선택그룹", "regex", query="a(b)?"))).values[0]
    assert value.status == "missing"
