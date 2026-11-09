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
    assert result.returncode == 2  # Explicit overwrite flag is required.
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
