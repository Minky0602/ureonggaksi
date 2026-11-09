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
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert load_workbook(output)["취합"]["B2"].value == "홍길동"
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2  # Explicit overwrite flag is required.
    output.unlink()
    result = subprocess.run(
        [*command, str(tmp_path / "missing.hwpx")], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert output.exists()  # Valid documents are still exported.
