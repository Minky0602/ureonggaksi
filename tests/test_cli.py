import os
import subprocess
import sys

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
