"""입력 연결: 파일별 읽기 상태·지문·중복·경로 충돌·한도 (PRD FR-01·02, api.md 2.2)."""

import shutil

import pytest
from conftest import write_hwpx, write_xlsx

from ureonggaksi import inputs as inputs_module
from ureonggaksi.errors import ServiceError
from ureonggaksi.inputs import exclude_source, inspect_source, open_inputs


def test_three_sources_and_one_target_are_listed_with_fingerprints(tmp_path, target):
    sources = [
        write_hwpx(tmp_path / f"문서{i}.hwpx", [f"<hp:p><hp:run><hp:t>{i}</hp:t></hp:run></hp:p>"])
        for i in range(3)
    ]
    result = open_inputs(sources, target, tmp_path / "out")
    assert [s.file_id for s in result.sources] == ["src-1", "src-2", "src-3"]
    assert all(s.status == "ok" and s.sha256 and len(s.sha256) == 64 for s in result.sources)
    assert (result.target.file_id, result.target.role, result.target.status) == (
        "target",
        "target",
        "ok",
    )
    assert result.output_dir == (tmp_path / "out").resolve()
    # 지문을 계산한 바이트가 그대로 보관된다("검사한 파일 = 쓰는 파일")
    assert result.snapshot("target") == target.read_bytes()
    assert result.snapshot("src-1") == sources[0].read_bytes()


def test_duplicate_content_is_reported_once(tmp_path, target):
    original = write_hwpx(tmp_path / "a.hwpx")
    copy = tmp_path / "copy" / "b.hwpx"
    copy.parent.mkdir()
    shutil.copy(original, copy)
    result = open_inputs([original, copy, original], target, tmp_path / "out")
    statuses = [(s.file_id, s.status, s.duplicate_of) for s in result.sources]
    assert statuses == [
        ("src-1", "ok", None),
        ("src-2", "duplicate", "src-1"),
        ("src-3", "duplicate", "src-1"),
    ]
    assert [s.file_id for s in result.active_sources] == ["src-1"]
    assert "src-2" not in result.snapshots


def test_broken_source_is_marked_failed_not_blank_and_can_be_excluded(tmp_path, target):
    good = [
        write_hwpx(tmp_path / "a.hwpx"),
        write_hwpx(tmp_path / "b.hwpx", [para_text("다른 문서")]),
    ]
    broken = tmp_path / "c.hwpx"
    broken.write_bytes(b"not a zip")
    result = open_inputs([*good, broken], target, tmp_path / "out")
    failed = result.sources[2]
    assert (failed.status, failed.sha256) == ("failed", None)
    assert failed.reason  # 이유가 특정된다
    assert [s.status for s in result.sources[:2]] == ["ok", "ok"]

    narrowed = exclude_source(result, "src-3")
    assert narrowed.run_id != result.run_id
    assert [s.status for s in narrowed.sources] == ["ok", "ok", "excluded"]
    assert [s.file_id for s in narrowed.active_sources] == ["src-1", "src-2"]
    with pytest.raises(ServiceError) as info:
        inspect_source(narrowed, "src-3")
    assert info.value.code == "INPUT_UNSUPPORTED"


def para_text(text):
    return f"<hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>"


def test_inspect_source_reads_from_the_snapshot_not_the_disk(tmp_path, target):
    source = write_hwpx(tmp_path / "a.hwpx")
    result = open_inputs([source], target, tmp_path / "out")
    source.write_bytes(b"changed on disk after inspection")
    doc = inspect_source(result, "src-1")
    assert doc.sha256 == result.sources[0].sha256
    assert any(b.text == "홍길동" for b in doc.blocks)


def test_hwp_and_other_extensions_fail_per_file(tmp_path, target):
    hwp = tmp_path / "old.hwp"
    hwp.write_bytes(b"\xd0\xcf\x11\xe0")
    result = open_inputs([hwp], target, tmp_path / "out")
    assert result.sources[0].status == "failed"
    assert "HWPX" in result.sources[0].reason


@pytest.mark.parametrize(
    ("name", "content"),
    [("t.xlsx", b"garbage"), ("t.xls", b"x"), ("missing.xlsx", None)],
)
def test_target_failure_blocks_the_whole_run(tmp_path, name, content):
    source = write_hwpx(tmp_path / "a.hwpx")
    target = tmp_path / name
    if content is not None:
        target.write_bytes(content)
    with pytest.raises(ServiceError) as info:
        open_inputs([source], target, tmp_path / "out")
    assert info.value.code == "INPUT_UNSUPPORTED"


def test_target_or_output_equal_to_a_source_path_is_a_conflict(tmp_path, target):
    source = write_hwpx(tmp_path / "a.hwpx")
    with pytest.raises(ServiceError) as info:
        open_inputs([source], source, tmp_path / "out")
    assert info.value.code == "PATH_CONFLICT"
    with pytest.raises(ServiceError) as info:
        open_inputs([source], target, target)
    assert info.value.code == "PATH_CONFLICT"
    with pytest.raises(ServiceError) as info:
        open_inputs([source], target, source)
    assert info.value.code == "PATH_CONFLICT"


def test_output_dir_may_be_the_target_folder(tmp_path, target):
    source = write_hwpx(tmp_path / "a.hwpx")
    assert open_inputs([source], target, tmp_path).output_dir == tmp_path.resolve()


def test_limits_are_explained_before_analysis(tmp_path, target, monkeypatch):
    source = write_hwpx(tmp_path / "a.hwpx")
    monkeypatch.setattr(inputs_module, "MAX_SOURCES", 1)
    with pytest.raises(ServiceError) as info:
        open_inputs([source, source], target, tmp_path / "out")
    assert info.value.code == "INPUT_LIMIT"
    monkeypatch.setattr(inputs_module, "MAX_SOURCES", 200)
    monkeypatch.setattr(inputs_module, "MAX_SNAPSHOT_BYTES", target.stat().st_size + 10)
    with pytest.raises(ServiceError) as info:
        open_inputs([source], target, tmp_path / "out")
    assert info.value.code == "INPUT_LIMIT"
    big = write_xlsx(tmp_path / "big.xlsx")
    monkeypatch.setattr(inputs_module, "MAX_TARGET_BYTES", 10)
    with pytest.raises(ServiceError) as info:
        open_inputs([source], big, tmp_path / "out")
    assert info.value.code == "INPUT_LIMIT"


def test_each_open_gets_a_new_run_id_but_the_same_content_fingerprint(tmp_path, target):
    source = write_hwpx(tmp_path / "a.hwpx")
    first = open_inputs([source], target, tmp_path / "out")
    second = open_inputs([source], target, tmp_path / "out")
    assert first.run_id != second.run_id
    assert first.sources[0].sha256 == second.sources[0].sha256  # 내용 지문은 식별자와 분리된다


def test_failure_reasons_do_not_leak_paths_or_errno(tmp_path, target):
    missing = tmp_path / "없는파일.hwpx"
    result = open_inputs([missing], target, tmp_path / "out")
    reason = result.sources[0].reason
    assert result.sources[0].status == "failed"
    assert "Errno" not in reason and str(tmp_path) not in reason


def test_path_spelling_variants_still_count_as_the_same_file(tmp_path, target):
    source = write_hwpx(tmp_path / "a.hwpx")
    variant = tmp_path / "sub" / ".." / "A.HWPX"  # ..·대소문자(Windows)
    (tmp_path / "sub").mkdir()
    with pytest.raises(ServiceError) as info:
        open_inputs([source], target, variant)
    assert info.value.code == "PATH_CONFLICT"


def test_excluding_the_representative_leaves_duplicates_inactive(tmp_path, target):
    """대표 원본을 제외하면 그 중복도 함께 쓰이지 않는다(스냅샷은 대표에만 있다). 계약의 알려진 한계."""
    original = write_hwpx(tmp_path / "a.hwpx")
    copy = tmp_path / "b.hwpx"
    shutil.copy(original, copy)
    result = exclude_source(open_inputs([original, copy], target, tmp_path / "out"), "src-1")
    assert [s.status for s in result.sources] == ["excluded", "duplicate"]
    assert result.active_sources == ()
