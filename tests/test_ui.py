import subprocess
import sys
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from ureonggaksi.ui.dialogs import RuleDialog
from ureonggaksi.ui.window import MainWindow
from ureonggaksi.workflow import Rule, Workflow


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    # Close/delete native windows while QApplication is still alive. Signal closures
    # can retain hidden windows beyond a test's local Python references.
    for widget in application.topLevelWidgets():
        if hasattr(widget, "pool"):
            widget.pool.waitForDone()
    application.processEvents()
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def wait_for_idle(window, app):
    deadline = time.monotonic() + 10
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not window.busy


def test_desktop_file_rule_extract_verify_and_invalidation(app, sample):
    window = MainWindow()
    window.show()
    window.add_paths([str(sample)])
    wait_for_idle(window, app)
    assert len(window.documents) == 1
    window.set_workflow(Workflow("명단", (Rule("성명", "label", query="성명"),)))
    window.run_extraction()
    wait_for_idle(window, app)
    assert window.results[0].values[0].value == "홍길동"
    assert window.result_table.item(0, 1).text() == "홍길동"
    window.result_table.setCurrentCell(0, 1)
    app.processEvents()
    window.verify_selected()
    wait_for_idle(window, app)
    assert "검증 완료" in window.evidence_status.text()
    assert window.source_text.toPlainText() == "홍길동"
    window.set_workflow(Workflow("기관", (Rule("기관명", "label", query="기관명"),)))
    assert window.results == ()
    assert not window.export_button.isEnabled()
    window.close()


def test_dialog_initializes_cell_from_selection(app, sample):
    from ureonggaksi.hwpx import read_hwpx

    block = next(b for b in read_hwpx(sample).blocks if b.text == "홍길동")
    dialog = RuleDialog(block=block)
    rule = dialog.rule()
    assert rule.kind == "cell"
    assert (rule.section, rule.table, rule.row, rule.col) == ("Contents/section0.xml", 1, 1, 2)


def test_file_errors_do_not_block_valid_file(app, sample, tmp_path):
    window = MainWindow()
    window.add_paths([str(sample), str(tmp_path / "missing.hwpx")])
    wait_for_idle(window, app)
    assert len(window.documents) == 1
    assert window.file_list.count() == 2
    window.set_workflow(Workflow("명단", (Rule("성명", "label", query="성명"),)))
    window.run_extraction()
    wait_for_idle(window, app)
    assert len(window.results) == 2
    assert window.results[1].error
    window.close()


def test_input_is_plain_text_and_busy_controls_are_locked(app):
    window = MainWindow()
    window.workflow_name.setText('<img src="https://evil.test/x">')
    assert window.workflow_name.text().startswith("<img")
    assert window.evidence_status.textFormat() == Qt.TextFormat.PlainText
    window.set_busy(True)
    assert not window.add_button.isEnabled()
    assert not window.rules_tree.isEnabled()
    window.set_busy(False)
    window.close()


def test_gui_process_exits_cleanly_after_threaded_work(sample):
    code = """
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ureonggaksi.ui.window import MainWindow
from ureonggaksi.workflow import Workflow, Rule
app = QApplication([])
w = MainWindow()
w.show()
w.set_workflow(Workflow('test', (Rule('name', 'label', query='성명'),)))
w.add_paths([sys.argv[1]])
stage = 0
def advance():
    global stage
    if not w.busy:
        if stage == 0:
            stage = 1
            w.run_extraction()
        else:
            timer.stop()
            w.close()
            app.quit()
timer = QTimer()
timer.timeout.connect(advance)
timer.start(30)
QTimer.singleShot(5000, app.quit)
app.exec()
"""
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code, str(sample)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_verification_cannot_be_misapplied_to_new_selection(app, sample, monkeypatch):
    import threading

    import ureonggaksi.ui.window as window_module
    from ureonggaksi.models import Verification

    entered, release = threading.Event(), threading.Event()

    def slow_verify(value):
        entered.set()
        release.wait(3)
        return Verification(True, "검증 완료", value.source)

    monkeypatch.setattr(window_module, "verify", slow_verify)
    window = MainWindow()
    window.add_paths([str(sample)])
    wait_for_idle(window, app)
    window.set_workflow(
        Workflow(
            "시험", (Rule("성명", "label", query="성명"), Rule("기관", "label", query="기관명"))
        )
    )
    window.run_extraction()
    wait_for_idle(window, app)
    window.result_table.setCurrentCell(0, 1)
    window.verify_selected()
    assert entered.wait(1)
    assert not window.result_table.isEnabled()
    assert not window.source_tree.isEnabled()
    window.result_table.setCurrentCell(0, 2)
    release.set()
    wait_for_idle(window, app)
    assert window.source_text.toPlainText() != "홍길동"
    assert "검증 완료" not in window.evidence_status.text()
    window.close()


def test_gui_entrypoint_exits_cleanly():
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-m", "ureonggaksi", "--smoke-test"],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
