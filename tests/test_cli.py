import os
import subprocess
import sys
from pathlib import Path

from openpyxl import load_workbook

from ureonggaksi.workflow import Rule, Workflow


def test_cli_batch_and_error_exit(sample, tmp_path):
    wf = tmp_path / "workflow.json"
    Workflow("명단", (Rule("성명", "label", query="성명"),)).save(wf)
    output = tmp_path / "result.xlsx"
    command = [
        sys.executable,
        "-m",
        "ureonggaksi",
        "extract",
        "--workflow",
        str(wf),
        "--output",
        str(output),
        str(sample),
    ]
    # The child's stream encoding follows the caller's environment on Windows (cp949 by
    # default, UTF-8 when PYTHONIOENCODING is set). Pin both ends so the test reads what
    # the CLI wrote.
    pipe = {
        "capture_output": True,
        "encoding": "utf-8",
        "env": {**os.environ, "PYTHONIOENCODING": "utf-8"},
    }
    result = subprocess.run(command, **pipe)
    assert result.returncode == 0, result.stderr
    assert load_workbook(output)["취합"]["B2"].value == "홍길동"
    result = subprocess.run(command, **pipe)
    assert result.returncode == 2  # 덮어쓰지 않는다(api 2.9)
    assert "출력 파일이 이미 있습니다" in result.stderr
    output.unlink()
    result = subprocess.run([*command, str(tmp_path / "missing.hwpx")], **pipe)
    assert result.returncode == 1
    assert output.exists()  # Valid documents are still exported.


def test_help_survives_korean_windows_pipe():
    # A Korean Windows pipe or redirected file is cp949. Help text must be writable there:
    # one character outside cp949 used to end `--help > file` with exit code 1.
    for args, expected in ((["--help"], "우렁각시"), (["extract", "--help"], "--workflow")):
        result = subprocess.run(
            [sys.executable, "-m", "ureonggaksi", *args],
            capture_output=True,
            encoding="cp949",
            env={**os.environ, "PYTHONIOENCODING": "cp949"},
        )
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "ureonggaksi", *map(str, args)],
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _files(tmp_path):
    from conftest import cell, para, table, write_hwpx, write_xlsx

    def doc(number, name, hours):
        rows = [("성명", name), ("이수시간", hours)]
        body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
        return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]

    sources = [
        write_hwpx(tmp_path / "a.hwpx", doc("A-01", "가나다", "12")),
        write_hwpx(tmp_path / "b.hwpx", doc("A-02", "라마바", "8")),
    ]
    target = write_xlsx(
        tmp_path / "표.xlsx",
        [["이름", "이수시간"], ["가나다", None], ["라마바", None]],
        sheet="현황",
    )
    return sources, target


def test_prepare_then_commit_writes_only_what_was_previewed(tmp_path):
    """prepare가 낸 지문으로만 commit한다. 결과 XLSX에는 미리보기한 칸만 달라진다(FR-14)."""
    sources, target = _files(tmp_path)
    plan, out, rules = tmp_path / "p.plan.json", tmp_path / "결과.xlsx", tmp_path / "rules"
    common = ("--target", target, "--out", plan, "--output", out, "--rules-dir", rules)
    shown = _cli("prepare", "--request", "이름 기준으로 이수시간 채워줘", *common, *sources)
    assert shown.returncode == 0, shown.stderr
    assert "채울 칸 2" in shown.stdout and not out.exists()
    digest = next(
        line.split(": ", 1)[1] for line in shown.stdout.splitlines() if line.startswith("계획 지문")
    )
    wrong = _cli("commit", "--plan", plan, "--plan-hash", "0" * 64, "--rules-dir", rules)
    assert wrong.returncode == 2 and not out.exists()
    saved = _cli("commit", "--plan", plan, "--plan-hash", digest, "--rules-dir", rules)
    assert saved.returncode == 0, saved.stderr
    sheet = load_workbook(out)["현황"]
    assert [sheet["B2"].value, sheet["B3"].value] == [
        "12",
        "8",
    ]  # 해석은 값 종류를 추정하지 않는다(글자)


def test_commit_refuses_when_files_changed_after_prepare(tmp_path):
    sources, target = _files(tmp_path)
    plan, out, rules = tmp_path / "p.plan.json", tmp_path / "결과.xlsx", tmp_path / "rules"
    shown = _cli(
        "prepare",
        "--request",
        "이름 기준으로 이수시간 채워줘",
        "--target",
        target,
        "--out",
        plan,
        "--output",
        out,
        "--rules-dir",
        rules,
        *sources,
    )
    digest = next(
        line.split(": ", 1)[1] for line in shown.stdout.splitlines() if line.startswith("계획 지문")
    )
    from conftest import write_xlsx

    write_xlsx(target, [["이름", "이수시간"], ["가나다", None], ["사아자", None]], sheet="현황")
    refused = _cli("commit", "--plan", plan, "--plan-hash", digest, "--rules-dir", rules)
    assert refused.returncode == 2 and not out.exists()


def test_prepare_with_a_question_exits_3_and_tells_how_to_answer(tmp_path):
    sources, target = _files(tmp_path)
    shown = _cli(
        "prepare",
        "--request",
        "이름 기준으로 이수시간",
        "--target",
        target,
        "--out",
        tmp_path / "p.plan.json",
        "--rules-dir",
        tmp_path / "rules",
        *sources,
    )
    assert shown.returncode == 3 and "--answer work_type=" in shown.stdout


def _digest(out):
    return next(
        line.split(": ", 1)[1] for line in out.stdout.splitlines() if line.startswith("계획 지문")
    )


def test_plan_file_never_overwrites_inputs_or_existing_files(tmp_path):
    """--out이 대상 XLSX이거나 이미 있으면 쓰지 않는다(검수 H1)."""
    sources, target = _files(tmp_path)
    before = target.read_bytes()
    same = _cli(
        "prepare",
        "--request",
        "이름 기준으로 이수시간 채워줘",
        "--target",
        target,
        "--out",
        target,
        "--rules-dir",
        tmp_path / "r",
        *sources,
    )
    assert same.returncode == 2 and target.read_bytes() == before
    existing = tmp_path / "있음.plan.json"
    existing.write_text("keep", encoding="utf-8")
    again = _cli(
        "prepare",
        "--request",
        "이름 기준으로 이수시간 채워줘",
        "--target",
        target,
        "--out",
        existing,
        "--rules-dir",
        tmp_path / "r",
        *sources,
    )
    assert again.returncode == 2 and existing.read_text(encoding="utf-8") == "keep"


def test_check_work_commits_and_a_tampered_request_is_refused(tmp_path):
    """검토 작업도 prepare가 정한 경로로 저장한다(검수 M1). 계획 파일의 요청문을 바꾸면 거부한다(검수 M2)."""
    import json

    sources, target = _files(tmp_path)
    plan, out, rules = tmp_path / "c.plan.json", tmp_path / "검토.xlsx", tmp_path / "r"
    shown = _cli(
        "prepare",
        "--request",
        "이름 기준으로 이수시간 확인해줘",
        "--target",
        target,
        "--out",
        plan,
        "--output",
        out,
        "--rules-dir",
        rules,
        *sources,
    )
    assert shown.returncode == 0, shown.stderr + shown.stdout
    digest = _digest(shown)
    record = json.loads(plan.read_text(encoding="utf-8"))
    tampered = tmp_path / "t.plan.json"
    tampered.write_text(
        json.dumps({**record, "request": "다른 요청"}, ensure_ascii=False), encoding="utf-8"
    )
    assert (
        _cli("commit", "--plan", tampered, "--plan-hash", digest, "--rules-dir", rules).returncode
        == 2
    )
    saved = _cli("commit", "--plan", plan, "--plan-hash", digest, "--rules-dir", rules)
    assert saved.returncode in (0, 1), saved.stderr  # 표에 값이 없으면 직접 확인이 남는다(1)
    written = [
        line.split(": ", 1)[1] for line in saved.stdout.splitlines() if line.startswith("저장: ")
    ]
    assert (
        not out.exists() and written and all(Path(p).exists() for p in written)
    )  # 검토는 XLSX를 만들지 않는다


def test_a_broken_plan_file_is_an_error_not_partial(tmp_path):
    plan = tmp_path / "b.plan.json"
    plan.write_text(
        '{"format": "ureonggaksi-plan/1", "plan_hash": "x", "sources": 5}', encoding="utf-8"
    )
    assert (
        _cli("commit", "--plan", plan, "--plan-hash", "x", "--rules-dir", tmp_path / "r").returncode
        == 2
    )


def test_a_copy_at_another_path_is_a_different_approval(tmp_path):
    """같은 내용의 다른 경로로 바꾼 계획은 거부한다. 근거에 승인하지 않은 경로가 남지 않는다(검수 M-b)."""
    import json
    import shutil

    sources, target = _files(tmp_path)
    plan, out, rules = tmp_path / "p.plan.json", tmp_path / "결과.xlsx", tmp_path / "r"
    shown = _cli(
        "prepare",
        "--request",
        "이름 기준으로 이수시간 채워줘",
        "--target",
        target,
        "--out",
        plan,
        "--output",
        out,
        "--rules-dir",
        rules,
        *sources,
    )
    copy = tmp_path / "사본.xlsx"
    shutil.copy(target, copy)
    record = json.loads(plan.read_text(encoding="utf-8"))
    moved = tmp_path / "m.plan.json"
    moved.write_text(
        json.dumps({**record, "target": str(copy)}, ensure_ascii=False), encoding="utf-8"
    )
    assert (
        _cli(
            "commit", "--plan", moved, "--plan-hash", _digest(shown), "--rules-dir", rules
        ).returncode
        == 2
    )
    assert not out.exists()
