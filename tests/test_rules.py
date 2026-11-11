"""규칙 v3: 형식·뜻 검사, 지문, 로컬 저장·재사용, v1 가져오기 (PRD FR-04·08, api.md 2.4·4·6)."""

import json

import pytest
from conftest import write_hwpx, write_xlsx

from ureonggaksi.inputs import open_inputs
from ureonggaksi.rules import (
    FieldRule,
    RecordRule,
    RuleError,
    RuleSet,
    RuleStore,
    TargetRule,
    default_store_root,
    rule_hash,
    validate_rule,
)
from ureonggaksi.workflow import Rule, Workflow
from ureonggaksi.xlsx import inspect_target


def make_rule(**overrides) -> RuleSet:
    base = dict(
        record=RecordRule("document", "사업코드"),
        fields=(
            FieldRule("사업코드", "label", query="사업코드"),
            FieldRule(
                "사업비",
                "label",
                query="사업비",
                value_type="money",
                unit="천원",
                target_column="2026 예산",
            ),
        ),
        target=TargetRule("사업취합", 1, (2, 3), "사업코드"),
        work_type="fill",
        policy=(("rounding", "down"),),
    )
    base.update(overrides)
    return RuleSet(**base)


@pytest.fixture
def view(tmp_path, target):
    source = write_hwpx(tmp_path / "a.hwpx")
    inputs = open_inputs([source], target, tmp_path / "out")
    return inputs, inspect_target(inputs)


def test_structural_errors_are_raised_at_construction():
    with pytest.raises(RuleError):
        FieldRule("사업비", "label", query="사업비", value_type="currency")
    with pytest.raises(RuleError):
        FieldRule("사업비", "label", query="사업비", unit="달러")
    with pytest.raises(RuleError):
        make_rule(work_type="merge")
    with pytest.raises(RuleError):
        make_rule(policy=(("rounding", "nearest"),))
    with pytest.raises(RuleError):
        make_rule(fields=(FieldRule("a", "label", query="a"), FieldRule("a", "label", query="a")))
    with pytest.raises(RuleError):
        TargetRule("s", 3, (2, 5), "k")  # 데이터 범위가 헤더 위
    with pytest.raises(RuleError):
        RecordRule("rows")


def test_valid_rule_has_no_issues(view):
    inputs, target_view = view
    assert validate_rule(make_rule(), inputs, target_view) == ()


def test_semantic_issues_are_listed_not_raised(view):
    inputs, target_view = view
    rule = make_rule(
        record=RecordRule("document", "없는항목"),
        fields=(
            FieldRule(
                "사업코드", "label", query="사업코드", value_type="number", target_column="사업명"
            ),
            FieldRule(
                "사업비",
                "label",
                query="사업비",
                value_type="number",
                unit="천원",
                target_column="없는 열",
            ),
            FieldRule("담당자", "label", query="담당자", target_column="사업명"),
            FieldRule("비고", "label", query="비고", target_column="사업코드"),
        ),
        target=TargetRule("사업취합", 1, (2, 3), "사업코드"),
    )
    codes = {(i.path, i.code) for i in validate_rule(rule, inputs, target_view)}
    assert ("record.key_field", "key_field_unknown") in codes
    assert ("fields[1].unit", "unit_not_money") in codes
    assert ("fields[1].target_column", "target_column_unknown") in codes
    assert ("fields[2].target_column", "target_column_duplicate") in codes
    assert ("fields[3].target_column", "target_is_key") in codes


def test_key_field_must_be_text_and_key_column_must_exist(view):
    inputs, target_view = view
    rule = make_rule(
        fields=(
            FieldRule("사업코드", "label", query="사업코드", value_type="number"),
            FieldRule(
                "사업비", "label", query="사업비", value_type="money", target_column="2026 예산"
            ),
        ),
        target=TargetRule("사업취합", 1, (2, 3), "코드"),
    )
    codes = {i.code for i in validate_rule(rule, inputs, target_view)}
    assert {"key_not_text", "key_column_unknown"} <= codes


def test_rule_hash_is_canonical_and_order_sensitive_only_where_meaning_differs():
    a = make_rule()
    b = make_rule(policy=(("rounding", "down"),))
    assert rule_hash(a) == rule_hash(b) and len(rule_hash(a)) == 64
    assert rule_hash(make_rule(policy=(("rounding", "up"),))) != rule_hash(a)
    assert rule_hash(make_rule(work_type="check")) != rule_hash(a)


def test_save_is_idempotent_and_revisions_preserve_history(tmp_path):
    store = RuleStore(tmp_path / "rules")
    first = store.save("사업비 취합", make_rule(), request="사업비를 예산 열에 넣어")
    again = store.save("다른 이름", make_rule())
    assert (again.workflow_id, again.revision) == (
        first.workflow_id,
        1,
    )  # 같은 지문은 새로 저장하지 않는다
    second = store.save("사업비 취합", make_rule(work_type="check"), base=first)
    assert (second.workflow_id, second.revision) == (first.workflow_id, 2)
    assert store.load(first.workflow_id, 1).rule.work_type == "fill"  # 이전 revision 보존
    assert store.load(first.workflow_id).revision == 2
    assert [s.latest_revision for s in store.list()] == [2]
    # 앱을 다시 연 것처럼 새 RuleStore로 읽어도 같다
    reopened = RuleStore(tmp_path / "rules")
    assert reopened.load(first.workflow_id).rule == make_rule(work_type="check")
    assert reopened.list()[0].name == "사업비 취합"


def test_rule_file_contains_no_paths_or_document_text(tmp_path):
    store = RuleStore(tmp_path / "rules")
    version = store.save("명단", make_rule(), request="성명을 명단에 채워")
    path = tmp_path / "rules" / version.workflow_id / "1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3 and data["rule_hash"] == version.rule_hash
    text = path.read_text(encoding="utf-8")
    assert "hwpx" not in text.lower() and str(tmp_path) not in text and "홍길동" not in text


def test_tampered_file_is_refused(tmp_path):
    store = RuleStore(tmp_path / "rules")
    version = store.save("명단", make_rule())
    path = tmp_path / "rules" / version.workflow_id / "1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rule"]["work_type"] = "check"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuleError):
        store.load(version.workflow_id)


def test_delete_removes_only_the_rule(tmp_path):
    store = RuleStore(tmp_path / "rules")
    version = store.save("명단", make_rule())
    result = tmp_path / "결과.xlsx"
    result.write_bytes(b"keep")
    store.delete(version.workflow_id)
    assert store.list() == () and result.read_bytes() == b"keep"
    with pytest.raises(RuleError):
        store.load(version.workflow_id)


def test_import_v1_workflow_becomes_a_draft_not_a_saved_rule(tmp_path):
    v1 = tmp_path / "old.json"
    Workflow(
        "명단", (Rule("성명", "label", query="성명"), Rule("기관명", "label", query="기관명"))
    ).save(v1)
    store = RuleStore(tmp_path / "rules")
    draft = store.import_file(v1)
    assert draft.origin == "imported" and draft.rule is not None
    assert [f.name for f in draft.rule.fields] == ["성명", "기관명"]
    assert all(f.value_type == "text" and f.target_column == "" for f in draft.rule.fields)
    assert {i.code for i in draft.issues} == {"key_field_missing", "target_missing"}
    assert store.list() == ()  # 저장되지 않았다


def test_import_rejects_garbage_and_accepts_v3(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    store = RuleStore(tmp_path / "rules")
    assert store.import_file(bad).rule is None
    version = store.save("명단", make_rule())
    exported = tmp_path / "rules" / version.workflow_id / "1.json"
    assert store.import_file(exported).rule == make_rule()


def test_default_store_root_is_under_the_user_app_data_folder():
    root = default_store_root()
    assert root.parts[-2:] == ("ureonggaksi", "rules")


def test_target_view_fixture_sanity(view):
    _, target_view = view
    assert target_view.columns == ("사업코드", "사업명", "2026 예산")


def test_big_rule_file_is_refused(tmp_path):
    store = RuleStore(tmp_path / "rules")
    version = store.save("명단", make_rule())
    path = tmp_path / "rules" / version.workflow_id / "1.json"
    path.write_text(path.read_text(encoding="utf-8") + " " * 1_048_576, encoding="utf-8")
    with pytest.raises(RuleError):
        store.load(version.workflow_id)


def test_write_xlsx_helper_is_available_for_rules_tests(tmp_path):
    assert write_xlsx(tmp_path / "t.xlsx").exists()
