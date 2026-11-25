"""주 화면 종단 흐름: 파일 → 한 줄 요청 → 미리보기 → [저장] (P05, PRD v1.4 기본 흐름). 합성 자료만 쓴다."""

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import cell, para, table, write_hwpx, write_xlsx
from openpyxl import load_workbook
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel

from ureonggaksi import __version__
from ureonggaksi.errors import ServiceError
from ureonggaksi.rules import RuleStore
from ureonggaksi.ui.flow import FlowWindow

PEOPLE = [("A-01", "가나다", "주무관", "12시간"), ("A-02", "라마바", "팀장", "8시간")]
SHEET = [
    ["발급번호", "이름", "직급", "이수시간"],
    ["A-01", "가나다", None, None],
    ["A-02", "라마바", None, None],
]


def certificate(number, name, title, hours):
    rows = [("성명", name), ("직위", title), ("이수시간", hours)]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    return [para(f"발급번호: {number}") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.delenv("UREONGGAKSI_LLAMA", raising=False)
    monkeypatch.delenv("UREONGGAKSI_MODEL", raising=False)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = FlowWindow(settings, RuleStore(tmp_path / "rules"))
    sources = [write_hwpx(tmp_path / f"doc{i}.hwpx", certificate(*p)) for i, p in enumerate(PEOPLE)]
    target = write_xlsx(tmp_path / "취합표.xlsx", SHEET, sheet="이수현황")
    win.add_sources(sources)
    win.set_target(target)
    yield win
    win.cancel()
    win.pool.waitForDone()
    win.close()
    win.deleteLater()


def idle(win, app):
    deadline = time.monotonic() + 30
    app.processEvents()
    while win.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not win.busy


def ask(win, app, text):
    win.request.setText(text)
    win.invalidate(reset_answers=True)
    win.preview_button.click()
    idle(win, app)


def knows(prompt, options):
    return "직위" if "직위" in options else "없음"


def test_preview_then_save_writes_a_new_file_and_keeps_the_original(window, app):
    before = hashlib.sha256(window.target.read_bytes()).hexdigest()
    assert not window.save_button.isEnabled()
    ask(window, app, "이름 기준으로 이수시간 채워줘")
    assert not window.preview_box.isHidden() and window.prepared.plan is not None
    assert "이수시간" in window.interpretation.text() and "채울 칸 2" in window.counts.text()
    assert window.save_button.isEnabled()
    window.save_button.click()
    idle(window, app)
    package = window.package
    assert package is not None and package.output_path.exists()
    assert "결과:" in window.result.text() and not window.save_button.isEnabled()
    assert hashlib.sha256(window.target.read_bytes()).hexdigest() == before
    sheet = load_workbook(package.output_path)["이수현황"]
    assert [sheet["D2"].value, sheet["D3"].value] != [None, None]
    evidence = json.loads(package.evidence_path.read_text(encoding="utf-8"))
    assert evidence["app_version"] == __version__
    assert "이수시간" in evidence["interpretation"]  # 전에는 늘 null이었다


def test_a_failed_save_says_which_files_were_left_behind(window, app):
    """저장 실패 때 지우지 못한 임시 파일이 있으면 '지웠습니다'라고 하지 않는다."""
    ask(window, app, "이름 기준으로 이수시간 채워줘")
    window.save_button.click()
    idle(window, app)
    left = Path("남은파일.tmp")
    window.show_result(replace(window.package, status="failed", cleanup_failed=(left,)))
    assert "지우지 못" in window.result.text() and str(left) in window.result.text()
    assert "지웠습니다" not in window.result.text()


def test_document_text_is_never_rendered_as_html(window, app, tmp_path):
    """SECURITY.md: 문서 값의 HTML을 그리지 않는다. 주 화면의 모든 글자 칸이 PlainText다."""
    ask(window, app, "이름 기준으로 이수시간 채워줘")
    labels = window.findChildren(QLabel)
    assert labels and all(lbl.textFormat() == Qt.TextFormat.PlainText for lbl in labels)


def test_file_dialogs_do_not_use_the_windows_recent_list(window, monkeypatch):
    """SECURITY.md: Windows 기본 대화상자는 고른 경로를 최근 파일 기록에 남긴다. Qt 대화상자를 쓴다."""
    seen = []

    def fake(*args, options=None, **kwargs):
        seen.append(options)
        return ([], "") if fake.many else ("", "")

    fake.many = True
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(fake))
    window.pick_sources()
    fake.many = False
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake))
    window.pick_target()
    window.pick_model()
    assert len(seen) == 3
    assert all(o is not None and o & QFileDialog.Option.DontUseNativeDialog for o in seen)


def test_the_window_shows_the_version(window):
    assert __version__ in window.windowTitle()


def test_a_question_is_answered_with_a_button(window, app):
    ask(window, app, "이름 기준으로 이수시간 맞는지 채워줘")
    assert window.prepared.question is not None and window.prepared.question.slot == "work_type"
    buttons = [window.choice_row.itemAt(i).widget() for i in range(window.choice_row.count())]
    fill = next(b for b in buttons if b is not None and b.text() == "빈칸 채우기")
    fill.click()
    idle(window, app)
    assert window.prepared.plan is not None and window.save_button.isEnabled()


def test_a_synonym_pair_is_shown_with_examples_and_can_be_dropped(window, app):
    window.choose = knows
    ask(window, app, "이름 기준으로 직급이랑 이수시간 채워줘")
    assert ("pair", "직위", "직급") in window.prepared.unsure
    rows = window.unsure_rows
    first = rows.itemAt(0).layout()
    assert "직급 ← 문서 ‘직위’" in first.itemAt(0).widget().text()
    assert "주무관" in first.itemAt(0).widget().text()
    first.itemAt(1).widget().click()  # 빼기
    idle(window, app)
    targets = {f.target_column for f in window.prepared.plan.rule.fields if f.target_column}
    assert targets == {"이수시간"} and ("drop_field", "직위") in window.answers


def test_changing_the_request_disables_save(window, app):
    ask(window, app, "이름 기준으로 이수시간 채워줘")
    assert window.save_button.isEnabled()
    window.request.textEdited.emit("이름 기준으로 직급 채워줘")
    assert not window.save_button.isEnabled() and window.prepared is None and window.answers == []


def test_a_failing_local_ai_is_reported(window, app):
    def broken(prompt, options):
        raise ServiceError("AI_UNAVAILABLE", "로컬 AI 실행이 실패했습니다(종료 코드 1).")

    def broken_reader(prompt, fields):
        raise ServiceError("AI_UNAVAILABLE", "로컬 AI 실행이 실패했습니다(종료 코드 1).")

    window.choose, window.ask_many = broken, broken_reader
    ask(window, app, "이름 기준으로 이수시간 채워 주세요 ㅎㅎ")  # 말투 해석은 늘 모델을 부른다
    shown = window.warnings.text() + window.question_text.text()
    assert "로컬 AI를 쓰지 못해" in shown or "로컬 AI를 쓰지 못했습니다" in shown


def test_missing_inputs_are_explained(app, tmp_path):
    win = FlowWindow(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat), RuleStore(tmp_path / "r")
    )
    win.preview_button.click()
    assert "먼저 고르세요" in win.message.text() and not win.busy
    win.close()
    win.deleteLater()


def test_a_preview_finishing_after_the_input_changed_is_discarded(window, app, tmp_path):
    """검수 H1: 미리보기 중에 입력이 바뀌면 끝난 미리보기를 보이지도 저장하게 두지도 않는다."""

    def slow(prompt, options):
        time.sleep(0.5)
        return "직위" if "직위" in options else "없음"

    window.choose = slow
    window.request.setText("이름 기준으로 직급이랑 이수시간 채워줘")
    window.preview_button.click()
    assert (
        window.busy and not window.target_button.isEnabled() and not window.add_button.isEnabled()
    )
    other = write_xlsx(tmp_path / "다른표.xlsx", SHEET, sheet="이수현황")
    window.set_target(other)  # 버튼은 꺼져 있지만 코드 경로로도 막히는지 본다
    idle(window, app)
    assert window.prepared is None and not window.save_button.isEnabled()
    assert "이전 미리보기를 버렸습니다" in window.message.text()


def test_a_question_without_buttons_explains_how_to_continue(window, app):
    """검수 M1: 짝을 모두 빼면 버튼 없는 질문 대신 다시 하는 방법과 취소 버튼을 보인다."""
    ask(window, app, "이름 기준으로 이수시간 채워줘")
    window.drop("이수시간")
    idle(window, app)
    assert "열 이름을 그대로" in window.question_text.text()
    buttons = [window.choice_row.itemAt(i).widget() for i in range(window.choice_row.count())]
    undo = next(b for b in buttons if b is not None and b.text() == "답과 빼기 모두 취소")
    undo.click()
    idle(window, app)
    assert window.prepared.plan is not None and window.answers == []


def test_closing_stops_further_model_calls(window, app):
    """화면 검수 M2: 취소(창 닫기) 뒤에는 모델을 더 부르지 않는다. 말투 해석의 두 번 묻기는 동시에
    띄우므로(DIF-061) 취소 전에 이미 나간 두 번까지만 있다."""
    calls = []

    def slow(prompt, fields):
        calls.append(prompt)
        time.sleep(0.5)
        return {}

    window.ask_many = slow
    window.request.setText("이름 기준으로 이수시간 채워 주세요 ㅎㅎ")
    window.preview_button.click()
    deadline = time.monotonic() + 5
    while len(calls) < 2 and time.monotonic() < deadline:  # 동시에 띄운 두 물음
        time.sleep(0.01)
    window.cancel()
    idle(window, app)
    assert len(calls) == 2


def test_a_check_run_reports_a_review_not_a_fill(window, app):
    """R01 검수 M3: 값 검토는 XLSX를 쓰지 않으므로 '채웠다'고 말하지 않는다."""
    ask(window, app, "이름 기준으로 이수시간 맞는지 확인해줘")
    assert window.prepared.plan.rule.work_type == "check"
    window.save_button.click()
    idle(window, app)
    assert window.result.text().startswith("값 검토를 마쳤습니다")
