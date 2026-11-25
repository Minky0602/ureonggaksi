"""주 화면: 파일 선택 → 한 줄 요청 → 미리보기 → [저장] → 결과 (PRD v1.4 기본 흐름, P05).

화면은 현재 Prepared 하나만 가진다(api 2.0). 입력·요청·답이 바뀌면 미리보기와 [저장]을 무효로 한다.
서비스가 쓰는 내용 = [저장]을 누를 때 보인 내용(plan_hash)이라, 화면은 상태를 따로 맞추지 않는다.
"""

import os
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..ai import LocalModel, ai_status, resume, stop_all
from ..errors import ServiceError
from ..manual import KINDS
from ..rules import SKIP, RuleStore
from ..service import POLICY_CHOICES, Prepared, commit, examples, prepare
from .jobs import Cancelled, Job
from .theme import STYLE

CHOICE_TEXT = {
    "fill": "빈칸 채우기",
    "check": "값만 대조",
    "down": "버림",
    "up": "올림",
    "half_up": "반올림",
}
KIND_TITLE = {k.kind: k.title for k in KINDS}
# 시스템 다크 모드에서도 읽히도록 페이지 바탕을 밝게 고정한다(글자 색은 theme.STYLE이 정한다)
PAGE_STYLE = "QWidget#page, QScrollArea { background: #F5F7F5; } QFrame#card { background: #FFFFFF; border: 1px solid #D4DFD7; border-radius: 8px; }"
MANUAL_LIMIT = 200  # 직접 처리 목록은 화면에 이만큼만 보이고 나머지는 확인필요 파일에 있다


# Windows 기본 대화상자는 고른 경로를 최근 파일(MRU) 기록에 남긴다. Qt 자체 대화상자를 쓴다(SECURITY.md)
DIALOG_OPTIONS = QFileDialog.Option.DontUseNativeDialog


def _label(text: str = "", name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)  # 문서 값의 HTML을 그리지 않는다(SECURITY.md)
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if name:
        widget.setObjectName(name)
    return widget


def _button(text: str, handler, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.clicked.connect(handler)
    if primary:
        widget.setObjectName("primary")
    return widget


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


class FlowWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, store: RuleStore | None = None):
        super().__init__()
        self.setWindowTitle(f"우렁각시 {__version__}")
        self.setStyleSheet(STYLE + PAGE_STYLE)
        self.resize(1080, 820)
        self.settings = settings or QSettings()
        self.store = store or RuleStore()
        self.pool = QThreadPool(self)
        self.job: Job | None = None
        self.busy = False
        self.sources: list[Path] = []
        self.target: Path | None = None
        self.answers: list[tuple[str, str]] = []
        self.prepared: Prepared | None = None
        self.package = None
        self.choose = self.ask_many = None  # 로컬 모델(없으면 모델 없이 해석)
        self.generation = 0  # 입력이 바뀔 때마다 오른다. 끝난 미리보기가 지금 입력의 것인지 가린다
        self._build()
        self.load_model()

    # ---------- 화면 ----------

    def _build(self) -> None:
        body = QWidget()
        body.setObjectName("page")
        page = QVBoxLayout(body)
        page.setContentsMargins(28, 22, 28, 22)
        page.setSpacing(10)
        page.addWidget(_label("우렁각시", "brand"))
        page.addWidget(
            _label(
                "원본 문서의 값을 이미 있는 취합표의 빈칸에 채웁니다. 원본은 바꾸지 않고 새 파일로 저장합니다.",
                "muted",
            )
        )

        files = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(_label("원본 문서(HWPX)", "section"))
        self.source_list = QListWidget()
        self.source_list.setMaximumHeight(110)
        left.addWidget(self.source_list)
        row = QHBoxLayout()
        self.add_button = _button("원본 추가", self.pick_sources)
        self.clear_button = _button("비우기", self.clear_sources)
        row.addWidget(self.add_button)
        row.addWidget(self.clear_button)
        row.addStretch()
        left.addLayout(row)
        right = QVBoxLayout()
        right.addWidget(_label("취합표(XLSX)", "section"))
        self.target_label = _label("아직 고르지 않았습니다.", "muted")
        right.addWidget(self.target_label)
        self.target_button = _button("취합표 선택", self.pick_target)
        right.addWidget(self.target_button)
        right.addStretch()
        files.addLayout(left, 3)
        files.addLayout(right, 2)
        page.addLayout(files)

        page.addWidget(_label("무엇을 할까요?", "section"))
        ask = QHBoxLayout()
        self.request = QLineEdit()
        self.request.setPlaceholderText("예: 이름 기준으로 직급이랑 이수시간 채워줘")
        self.request.textEdited.connect(lambda _: self.invalidate(reset_answers=True))
        self.request.returnPressed.connect(self.preview)
        ask.addWidget(self.request, 1)
        self.preview_button = _button("미리보기", self.preview, primary=True)
        ask.addWidget(self.preview_button)
        page.addLayout(ask)
        model_row = QHBoxLayout()
        self.model_label = _label("", "muted")
        model_row.addWidget(self.model_label, 1)
        self.model_button = _button("로컬 AI 지정", self.pick_model)
        model_row.addWidget(self.model_button)
        page.addLayout(model_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        page.addWidget(self.progress)

        self.message = _label("", "muted")  # 오류·질문 없는 안내
        page.addWidget(self.message)

        self.question_box = QFrame()
        self.question_box.setObjectName("card")
        question = QVBoxLayout(self.question_box)
        self.question_text = _label("", "section")
        question.addWidget(self.question_text)
        self.choice_row = QHBoxLayout()
        question.addLayout(self.choice_row)
        self.question_box.hide()
        page.addWidget(self.question_box)

        self.preview_box = QFrame()
        self.preview_box.setObjectName("card")
        preview = QVBoxLayout(self.preview_box)
        self.interpretation = _label("", "section")
        self.counts = _label()
        self.samples = _label("", "muted")
        preview.addWidget(self.interpretation)
        preview.addWidget(self.counts)
        preview.addWidget(self.samples)
        self.unsure_title = _label("확인 필요 — 값을 보고 틀리면 [빼기]", "section")
        preview.addWidget(self.unsure_title)
        self.unsure_rows = QVBoxLayout()
        preview.addLayout(self.unsure_rows)
        self.warnings = _label("", "muted")
        preview.addWidget(self.warnings)
        preview.addWidget(_label("직접 처리할 칸", "section"))
        self.manual = QTreeWidget()
        self.manual.setHeaderLabels(["종류", "위치", "이유"])
        self.manual.setRootIsDecorated(False)
        self.manual.setMinimumHeight(160)
        self.manual.setColumnWidth(0, 190)
        self.manual.setColumnWidth(1, 160)
        preview.addWidget(self.manual)
        self.manual_more = _label("", "muted")
        preview.addWidget(self.manual_more)
        self.preview_box.hide()
        page.addWidget(self.preview_box)

        save = QHBoxLayout()
        self.result = _label("", "muted")
        save.addWidget(self.result, 1)
        self.open_button = _button("결과 폴더 열기", self.open_folder)
        self.open_button.hide()
        save.addWidget(self.open_button)
        self.save_button = _button("저장", self.save, primary=True)
        self.save_button.setEnabled(False)
        save.addWidget(self.save_button)
        page.addLayout(save)
        page.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.setCentralWidget(scroll)

    # ---------- 입력 ----------

    def pick_sources(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "원본 문서", "", "한글 문서 (*.hwpx)", options=DIALOG_OPTIONS
        )
        self.add_sources([Path(p) for p in paths])

    def add_sources(self, paths: list[Path]) -> None:
        for path in paths:
            if path not in self.sources:
                self.sources.append(path)
                self.source_list.addItem(path.name)
        self.invalidate(reset_answers=True)

    def clear_sources(self) -> None:
        self.sources.clear()
        self.source_list.clear()
        self.invalidate(reset_answers=True)

    def pick_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "취합표", "", "Excel 통합 문서 (*.xlsx)", options=DIALOG_OPTIONS
        )
        if path:
            self.set_target(Path(path))

    def set_target(self, path: Path) -> None:
        self.target = path
        self.target_label.setText(path.name)
        self.invalidate(reset_answers=True)

    def pick_model(self) -> None:
        runtime, _ = QFileDialog.getOpenFileName(
            self,
            "llama-completion 실행 파일",
            "",
            "실행 파일 (*.exe);;모든 파일 (*)",
            options=DIALOG_OPTIONS,
        )
        if not runtime:
            return
        model, _ = QFileDialog.getOpenFileName(
            self, "GGUF 모델 파일", "", "GGUF (*.gguf)", options=DIALOG_OPTIONS
        )
        if model:
            self.settings.setValue("ai/runtime", runtime)
            self.settings.setValue("ai/model", model)
            self.load_model()

    def load_model(self) -> None:
        """환경 변수(UREONGGAKSI_LLAMA·UREONGGAKSI_MODEL)가 먼저, 없으면 [로컬 AI 지정]으로 고른 경로."""
        runtime = os.environ.get("UREONGGAKSI_LLAMA") or self.settings.value("ai/runtime", "")
        model = os.environ.get("UREONGGAKSI_MODEL") or self.settings.value("ai/model", "")
        status = ai_status(Path(runtime) if runtime else None, Path(model) if model else None)
        if status.ready:
            local = LocalModel((str(runtime),), Path(model))
            self.choose, self.ask_many = local.choose, local.ask_fields
            self.model_label.setText(f"로컬 AI: 사용 ({Path(model).name})")
        else:
            self.choose = self.ask_many = None
            self.model_label.setText(
                "로컬 AI: 사용 안 함 — 열 이름과 같은 항목만 짝짓습니다. [로컬 AI 지정]으로 켤 수 있습니다."
            )
        self.invalidate()

    def invalidate(self, reset_answers: bool = False) -> None:
        """보이는 미리보기가 입력과 맞지 않게 되면 저장할 수 없다."""
        if reset_answers:
            self.answers.clear()
        self.generation += 1
        self.message.setText("")
        self.prepared = None
        self.package = None
        self.save_button.setEnabled(False)
        self.preview_box.hide()
        self.question_box.hide()
        self.result.setText("")
        self.open_button.hide()

    # ---------- 미리보기 ----------

    def preview(self) -> None:
        if self.busy:
            return
        if not self.sources or self.target is None:
            self.message.setText("원본 문서와 취합표를 먼저 고르세요.")
            return
        self.invalidate()
        request, sources, target = self.request.text().strip(), list(self.sources), self.target
        answers, choose, ask_many = tuple(self.answers), self.choose, self.ask_many

        def guarded(job: Job, call):
            """취소(창 닫기) 뒤에는 모델을 더 부르지 않는다."""
            if call is None:
                return None

            def run(*args):
                if job.stop.is_set():
                    raise Cancelled
                return call(*args)

            return run

        def work(job: Job):
            try:
                return prepare(
                    request,
                    sources,
                    target,
                    answers=answers,
                    choose=guarded(job, choose),
                    ask_many=guarded(job, ask_many),
                    progress=lambda _stage, done, total: job.signals.progress.emit(done, total, ""),
                    cancel=job.stop,
                )
            except ServiceError as error:
                return error

        self.message.setText("문서를 읽고 요청을 해석하는 중입니다…")
        generation = self.generation

        def done(prepared) -> None:
            if (
                generation != self.generation
            ):  # 그사이 입력이 바뀌었다: 지난 입력의 미리보기는 보이지 않는다
                self.message.setText(
                    "입력이 바뀌어 이전 미리보기를 버렸습니다. 다시 [미리보기]를 누르세요."
                )
                return
            self.show_prepared(prepared)

        self._run(work, done)

    def show_prepared(self, prepared) -> None:
        if isinstance(prepared, ServiceError):
            self.message.setText(prepared.message)
            return
        self.prepared = prepared
        self.message.setText("")
        if prepared.question is not None:
            self.show_question(prepared.question)
            return
        plan = prepared.plan
        counts = plan.counts
        manual = [i for i in plan.items if i.area == "manual"]
        self.interpretation.setText(prepared.interpretation)
        if plan.rule.work_type == "check":
            self.counts.setText(f"일치 {counts.cells_unchanged} · 직접 확인 {len(manual)}")
        else:
            self.counts.setText(
                f"채울 칸 {counts.cells_write} · 이미 같은 값 {counts.cells_unchanged} · "
                f"직접 처리 {len(manual)} · 읽지 못한 파일 {counts.files_failed}"
            )
        self.samples.setText(
            "\n".join(
                f"예: {i.target_ref} ← {i.proposed.value} ({i.field})" for i in prepared.samples
            )
        )
        self.show_unsure(prepared)
        self.warnings.setText("\n".join(f"참고: {w}" for w in prepared.warnings))
        self.warnings.setVisible(bool(prepared.warnings))
        self.manual.clear()
        for item in prepared.manual[:MANUAL_LIMIT]:
            self.manual.addTopLevelItem(
                QTreeWidgetItem([KIND_TITLE.get(item.kind, item.kind), item.where, item.reason])
            )
        more = len(prepared.manual) - MANUAL_LIMIT
        self.manual_more.setText(
            f"외 {more}건은 저장 뒤 확인필요 파일에 있습니다." if more > 0 else ""
        )
        self.preview_box.show()
        self.save_button.setEnabled(True)

    def show_unsure(self, prepared: Prepared) -> None:
        """열마다 한 줄: 값 예시로 바로 판단하고, 틀리면 [빼기]. 확인하지 않고 저장해도 된다."""
        _clear(self.unsure_rows)
        for kind, name, column in prepared.unsure:
            row = QHBoxLayout()
            if kind == "pair":
                shown = ", ".join(examples(prepared, name)) or "예시 없음"
                row.addWidget(_label(f"{column} ← 문서 ‘{name}’    예: {shown}"), 1)
                row.addWidget(_button("빼기", lambda _=False, n=name: self.drop(n)))
            elif kind == "key":
                row.addWidget(_label(f"{column} 열의 행을 문서의 ‘{name}’ 값으로 찾습니다"), 1)
            else:
                row.addWidget(_label(f"‘{name}’은(는) 빼라는 말로 읽고 비워 둡니다"), 1)
            self.unsure_rows.addLayout(row)
        self.unsure_title.setVisible(bool(prepared.unsure))

    def show_question(self, question) -> None:
        _clear(self.choice_row)
        if not question.choices and question.slot not in POLICY_CHOICES:
            # 버튼으로 답할 수 없는 질문: 서비스 문구(로컬 AI 실패 안내 포함)를 그대로 보이고 되돌릴 길만 준다
            self.question_text.setText(question.text)
            if self.answers:
                self.choice_row.addWidget(_button("답과 빼기 모두 취소", self.restart))
            self.choice_row.addStretch()
            self.question_box.show()
            return
        self.question_text.setText(question.text)
        for choice in question.choices:
            self.choice_row.addWidget(
                _button(
                    CHOICE_TEXT.get(choice, choice),
                    lambda _=False, c=choice: self.answer(question.slot, c),
                )
            )
        if question.slot in POLICY_CHOICES:
            self.choice_row.addWidget(
                _button("건너뛰기", lambda _=False: self.answer(question.slot, SKIP))
            )
        self.choice_row.addStretch()
        self.question_box.show()

    def answer(self, slot: str, value: str) -> None:
        if self.busy:
            return
        self.answers.append((slot, value))
        self.preview()

    def restart(self) -> None:
        if self.busy:
            return
        self.answers.clear()
        self.preview()

    def drop(self, field: str) -> None:
        if self.busy:
            return
        self.answers.append(("drop_field", field))
        self.preview()

    # ---------- 저장 ----------

    def save(self) -> None:
        prepared = self.prepared
        if self.busy or prepared is None or prepared.plan is None:
            return
        store = self.store

        def work(job: Job):
            try:
                return commit(prepared, store, cancel=job.stop)
            except ServiceError as error:
                return error

        self.message.setText("저장하는 중입니다…")
        self._run(work, self.show_result)

    def show_result(self, package) -> None:
        if isinstance(package, ServiceError):
            self.message.setText(package.message)
            return
        self.package = package
        self.message.setText("")
        self.save_button.setEnabled(False)  # 같은 미리보기로 두 번 저장하지 않는다
        check = self.prepared is not None and self.prepared.plan.rule.work_type == "check"
        if package.status in ("failed", "cancelled") and package.cleanup_failed:
            head = "저장하지 못했습니다. 만들다 만 파일 일부를 지우지 못했습니다. 직접 지워 주세요."
        elif package.status in ("failed", "cancelled"):
            head = "저장하지 못했습니다. 만든 파일은 지웠습니다."
        elif check:
            head = "값 검토를 마쳤습니다. 다른 값과 빈칸은 확인필요 파일에 있습니다."
        elif package.status == "completed":
            head = "저장했습니다. 모두 채웠습니다."
        elif package.manual_path:
            head = "저장했습니다. 직접 처리할 칸은 확인필요 파일에 있습니다."
        else:
            head = "저장했습니다. 읽지 못한 원본이 있습니다."
        lines = [head]
        for title, path in (
            ("결과", package.output_path),
            ("확인필요", package.manual_path),
            ("근거", package.evidence_path),
        ):
            if path:
                lines.append(f"{title}: {path}")
        if package.error:
            lines.append(package.error)
        lines += [f"지우지 못한 파일: {path}" for path in package.cleanup_failed]
        self.result.setText("\n".join(lines))
        self.open_button.setVisible(bool(package.output_path or package.evidence_path))

    def open_folder(self) -> None:
        path = self.package and (self.package.output_path or self.package.evidence_path)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    # ---------- 작업 ----------

    def _run(self, work, done) -> None:
        resume()  # 앞서 취소로 막아 둔 로컬 AI 실행을 다시 허용한다
        job = Job(work)
        self.job = job
        self._busy(True)
        job.signals.succeeded.connect(done)
        job.signals.failed.connect(self.message.setText)
        job.signals.finished.connect(lambda: self._busy(False))
        self.pool.start(job)

    def _busy(self, busy: bool) -> None:
        self.busy = busy
        self.progress.setVisible(busy)
        # 작업 중에는 입력을 바꾸는 모든 것을 끈다(보인 미리보기 = 지금 입력, 검수 H1)
        for widget in (
            self.preview_button,
            self.request,
            self.add_button,
            self.clear_button,
            self.target_button,
            self.model_button,
            self.question_box,
            self.preview_box,
        ):
            widget.setEnabled(not busy)
        if busy:
            self.save_button.setEnabled(False)
        elif self.prepared is not None and self.prepared.plan is not None and self.package is None:
            self.save_button.setEnabled(True)

    def cancel(self) -> None:
        """창 닫기: 작업을 멈추고 도는 로컬 AI 프로세스를 끝낸다(질문당 최대 60초를 기다리지 않는다)."""
        if self.job is not None:
            self.job.stop.set()
        stop_all()
