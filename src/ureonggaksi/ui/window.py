from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..export import export_excel
from ..extract import extract, verify
from ..hwpx import HwpxError, read_hwpx
from ..models import DocumentResult, ExtractedValue
from ..storage import local_path
from ..workflow import Workflow, WorkflowError
from .dialogs import KINDS, RuleDialog
from .jobs import Job
from .theme import STYLE

DATA = Qt.ItemDataRole.UserRole
DIALOG_OPTIONS = QFileDialog.Option.DontUseNativeDialog
STATUS = {"ok": "정상", "missing": "누락", "ambiguous": "중복", "error": "오류"}


def label(text, name=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, handler, primary=False):
    widget = QPushButton(text)
    widget.clicked.connect(handler)
    if primary:
        widget.setObjectName("primary")
    return widget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("우렁각시 · 오프라인 문서 취합")
        self.resize(1360, 900)
        self.setMinimumSize(1000, 700)
        self.setStyleSheet(STYLE)
        self.documents = OrderedDict()
        self.errors: dict[str, str] = {}
        self.paths: list[str] = []
        self.rules = []
        self.results = ()
        self.result_workflow = None
        self.busy = False
        self.job = None
        self.cancelable = True
        self.close_pending = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._build()
        self.statusBar().showMessage(
            "준비됨 · 문서 내용은 사용자가 저장하기 전까지 메모리에서만 처리합니다."
        )

    def _build(self):
        shell = QWidget()
        self.setCentralWidget(shell)
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(260)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 25, 20, 20)
        side.setSpacing(10)
        side.addWidget(label("UREONGGAKSI", "eyebrow"))
        side.addWidget(label("우렁각시", "brand"))
        side.addWidget(label("내 문서에서, 내 컴퓨터로.", "muted"))
        side.addSpacing(12)
        side.addWidget(label("01  취합할 문서", "section"))
        self.add_button = button("＋  HWPX 파일 추가", self.choose_files)
        side.addWidget(self.add_button)
        self.file_count = label("아직 추가한 문서가 없습니다.", "muted")
        side.addWidget(self.file_count)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.currentItemChanged.connect(self.select_file)
        side.addWidget(self.file_list, 1)
        self.remove_button = button("선택 문서 제거", self.remove_files)
        side.addWidget(self.remove_button)
        side.addWidget(label("02  재사용할 워크플로", "section"))
        self.workflow_name = QLineEdit("새 워크플로")
        self.workflow_name.setMaxLength(100)
        self.workflow_name.textChanged.connect(self.invalidate_results)
        side.addWidget(self.workflow_name)
        line = QHBoxLayout()
        self.load_button = button("불러오기", self.load_workflow)
        self.save_button = button("저장", self.save_workflow)
        line.addWidget(self.load_button)
        line.addWidget(self.save_button)
        side.addLayout(line)
        side.addSpacing(12)
        self.run_button = button("문서 일괄 취합", self.run_extraction, True)
        side.addWidget(self.run_button)
        self.export_button = button("Excel로 저장", self.save_excel)
        self.export_button.setEnabled(False)
        side.addWidget(self.export_button)
        side.addSpacing(10)
        note = label("외부 전송 기능 없음\n서버 · 로그인 · API 키 불필요", "muted")
        side.addWidget(note)
        root.addWidget(sidebar)

        main = QVBoxLayout()
        main.setContentsMargins(28, 24, 28, 15)
        main.setSpacing(14)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("LOCAL DOCUMENT WORKSPACE", "eyebrow"))
        titles.addWidget(label("반복되는 취합을, 한 번의 규칙으로.", "heading"))
        top.addLayout(titles)
        top.addStretch()
        top.addWidget(label("●  오프라인 전용", "badge"))
        main.addLayout(top)
        main.addWidget(
            label(
                "문서를 확인하고 추출할 항목을 정하세요. 취합한 값은 원문 위치까지 확인할 수 있습니다.",
                "muted",
            )
        )
        self.tabs = QTabWidget()
        self.tabs.addTab(self._document_tab(), "01  원문 확인")
        self.tabs.addTab(self._rules_tab(), "02  추출 규칙")
        self.tabs.addTab(self._results_tab(), "03  취합 결과")

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tabs)
        split.addWidget(self._evidence_panel())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([480, 200])
        main.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.progress_text = label("파일을 추가하면 시작할 수 있습니다.", "muted")
        bottom.addWidget(self.progress_text, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        bottom.addWidget(self.progress)
        self.cancel_button = button("취소", self.cancel)
        self.cancel_button.setEnabled(False)
        bottom.addWidget(self.cancel_button)
        main.addLayout(bottom)
        root.addLayout(main, 1)

    def _document_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        self.document_heading = label("왼쪽에서 HWPX 문서를 추가하세요.", "section")
        layout.addWidget(self.document_heading)
        line = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("원문에서 항목명이나 값을 검색하세요")
        self.search.textChanged.connect(self.filter_source)
        line.addWidget(self.search, 1)
        self.from_source_button = button("선택한 셀을 추출 항목으로", self.add_rule_from_source)
        line.addWidget(self.from_source_button)
        layout.addLayout(line)
        self.source_tree = QTreeWidget()
        self.source_tree.setHeaderLabels(["원문 위치", "내용", "종류"])
        self.source_tree.setColumnWidth(0, 210)
        self.source_tree.setColumnWidth(1, 430)
        self.source_tree.setAlternatingRowColors(True)
        self.source_tree.currentItemChanged.connect(self.select_source)
        layout.addWidget(self.source_tree, 1)
        layout.addWidget(
            label(
                "원문의 텍스트·표 구조를 표시합니다. 한글의 인쇄 레이아웃이나 페이지 번호는 재현하지 않습니다.",
                "muted",
            )
        )
        return tab

    def _rules_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.addWidget(label("취합할 엑셀 열과 추출 방법을 정의하세요.", "section"))
        self.rules_tree = QTreeWidget()
        self.rules_tree.setHeaderLabels(["엑셀 열 이름", "추출 방식", "규칙", "필수"])
        self.rules_tree.setColumnWidth(0, 150)
        self.rules_tree.setColumnWidth(1, 170)
        self.rules_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.rules_tree.itemDoubleClicked.connect(lambda *_: self.edit_rule())
        layout.addWidget(self.rules_tree, 1)
        line = QHBoxLayout()
        self.rule_add = button("＋ 항목 추가", self.add_rule, True)
        self.rule_edit = button("편집", self.edit_rule)
        self.rule_remove = button("제거", self.remove_rule)
        self.rule_up = button("위로", lambda: self.move_rule(-1))
        self.rule_down = button("아래로", lambda: self.move_rule(1))
        for widget in (
            self.rule_add,
            self.rule_edit,
            self.rule_remove,
            self.rule_up,
            self.rule_down,
        ):
            line.addWidget(widget)
        line.addStretch()
        layout.addLayout(line)
        layout.addWidget(
            label(
                "같은 양식은 지정 셀로, 위치가 달라지는 표는 항목명으로 추출하세요. JSON으로 저장하면 다음 취합에 다시 사용할 수 있습니다.",
                "muted",
            )
        )
        return tab

    def _results_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        self.result_heading = label("규칙을 정의한 뒤 ‘문서 일괄 취합’을 실행하세요.", "section")
        layout.addWidget(self.result_heading)
        self.result_table = QTableWidget(0, 0)
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.currentCellChanged.connect(self.select_result)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.result_table, 1)
        layout.addWidget(
            label(
                "값을 클릭하면 아래에 원문이 표시됩니다. ‘원본 다시 검증’으로 현재 파일과 일치하는지 확인하세요.",
                "muted",
            )
        )
        return tab

    def _evidence_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 12, 0, 0)
        top = QHBoxLayout()
        top.addWidget(label("원문 근거", "section"))
        top.addStretch()
        self.verify_button = button("원본 다시 검증", self.verify_selected)
        self.verify_button.setEnabled(False)
        top.addWidget(self.verify_button)
        layout.addLayout(top)
        self.evidence_status = label("원문 항목이나 취합한 값을 선택하세요.", "muted")
        self.evidence_status.setWordWrap(True)
        self.evidence_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.evidence_status)
        self.source_text = QTextEdit()
        self.source_text.setReadOnly(True)
        self.source_text.setAcceptRichText(False)
        self.source_text.setMinimumHeight(80)
        layout.addWidget(self.source_text, 1)
        return panel

    def message(self, title, text):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.exec()

    def set_busy(self, busy):
        self.busy = busy
        for widget in (
            self.add_button,
            self.remove_button,
            self.load_button,
            self.save_button,
            self.run_button,
            self.workflow_name,
            self.rules_tree,
            self.rule_add,
            self.rule_edit,
            self.rule_remove,
            self.rule_up,
            self.rule_down,
            self.from_source_button,
            self.file_list,
            self.result_table,
            self.source_tree,
        ):
            widget.setEnabled(not busy)
        self.export_button.setEnabled(not busy and bool(self.results))
        self.cancel_button.setEnabled(busy and self.cancelable)
        self.verify_button.setEnabled(not busy and self.selected_value() is not None)

    def start_job(self, work, callback, *, cancelable=True):
        if self.busy:
            return
        self.cancelable = cancelable
        self.job = Job(work)
        self.job.signals.progress.connect(self.on_progress)
        self.job.signals.succeeded.connect(callback)
        self.job.signals.failed.connect(lambda text: self.message("작업 확인", text))
        self.job.signals.cancelled.connect(
            lambda: self.progress_text.setText("취소됨 · 진행 중이던 결과는 적용하지 않았습니다.")
        )
        self.job.signals.finished.connect(self.finish_job)
        self.set_busy(True)
        self.pool.start(self.job)

    def on_progress(self, current, total, text):
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.progress_text.setText(text)

    def finish_job(self):
        self.job = None
        self.set_busy(False)
        if self.close_pending:
            QTimer.singleShot(0, self.close)

    def cancel(self):
        if self.job and self.cancelable:
            self.job.stop.set()
            self.cancel_button.setEnabled(False)
            self.progress_text.setText("현재 문서 처리가 끝나면 취소합니다…")

    def closeEvent(self, event):
        if self.busy:
            self.close_pending = True
            self.cancel()
            event.ignore()
            return
        super().closeEvent(event)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "취합할 문서 선택", str(Path.home()), "한글 HWPX (*.hwpx)", options=DIALOG_OPTIONS
        )
        self.add_paths(paths)

    def _cache(self, doc):
        self.documents[doc.path] = doc
        self.documents.move_to_end(doc.path)
        while len(self.documents) > 3 or (
            len(self.documents) > 1
            and sum(sum(len(b.text) for b in d.blocks) for d in self.documents.values()) > 8_000_000
        ):
            self.documents.popitem(last=False)

    def add_paths(self, paths):
        if self.busy or not paths:
            return
        try:
            new = list(dict.fromkeys(str(local_path(p)) for p in paths))
        except ValueError as exc:
            self.message("로컬 파일 선택", str(exc))
            return
        new = [p for p in new if p not in self.paths]
        if not new:
            return
        if len(self.paths) + len(new) > 500:
            self.message(
                "문서 수 확인", "한 번에 최대 500개까지 추가할 수 있습니다. 작업을 나눠 주세요."
            )
            return
        self.invalidate_results()

        def work(job):
            loaded = []
            # Keep only a bounded preview cache; extraction rereads every source.
            for i, path in enumerate(new, 1):
                job.checkpoint()
                try:
                    doc = read_hwpx(path)
                    loaded.append((path, doc, ""))
                except HwpxError as exc:
                    loaded.append((path, None, str(exc)))
                # Only the first 3 document bodies are retained from this import.
                if len(loaded) > 3:
                    old_path, _, old_error = loaded[-4]
                    loaded[-4] = (old_path, None, old_error)
                job.signals.progress.emit(i, len(new), f"문서 확인 {i}/{len(new)}")
            return loaded

        def loaded(items):
            for path, doc, error in items:
                self.paths.append(path)
                if doc:
                    self._cache(doc)
                if error:
                    self.errors[path] = error
                item = QListWidgetItem(("!  " if error else "") + Path(path).name)
                item.setToolTip(error or path)
                item.setData(DATA, path)
                self.file_list.addItem(item)
            self.file_count.setText(f"{len(self.paths)}개 문서 · 오류 {len(self.errors)}개")
            self.progress_text.setText("문서를 선택해 원문을 확인하세요.")
            # Selection after finish avoids starting a nested job while busy.
            QTimer.singleShot(0, lambda: self.file_list.setCurrentRow(0))

        self.start_job(work, loaded)

    def remove_files(self):
        if self.busy:
            return
        for item in self.file_list.selectedItems():
            path = item.data(DATA)
            self.paths.remove(path)
            self.documents.pop(path, None)
            self.errors.pop(path, None)
            self.file_list.takeItem(self.file_list.row(item))
        self.file_count.setText(f"{len(self.paths)}개 문서 · 오류 {len(self.errors)}개")
        self.invalidate_results()
        if not self.paths:
            self.source_tree.clear()
            self.document_heading.setText("왼쪽에서 HWPX 문서를 추가하세요.")
            self.clear_evidence()

    def select_file(self, current, previous=None):
        if current is None:
            return
        path = current.data(DATA)
        if path in self.errors:
            self.document_heading.setText(self.errors[path])
            self.source_tree.clear()
            self.clear_evidence()
            return
        if path in self.documents:
            self.show_document(self.documents[path])
        elif not self.busy:

            def loaded(doc):
                self._cache(doc)
                self.show_document(doc)

            self.start_job(lambda job: read_hwpx(path), loaded)

    def show_document(self, doc):
        self.document_heading.setText(f"{Path(doc.path).name}  ·  {len(doc.blocks)}개 텍스트·셀")
        self.document_heading.setToolTip(f"SHA-256: {doc.sha256}")
        self.source_tree.clear()
        self.clear_evidence()
        parents = {}
        for block in doc.blocks:
            if block.section not in parents:
                section = QTreeWidgetItem([block.section, "", "구역"])
                self.source_tree.addTopLevelItem(section)
                parents[block.section] = section
            parent = parents[block.section]
            if block.kind == "cell":
                key = (block.section, block.table)
                if key not in parents:
                    node = QTreeWidgetItem([f"표 {block.table}", "", "표"])
                    parent.addChild(node)
                    parents[key] = node
                parent = parents[key]
            where = f"{block.row}행 {block.col}열" if block.kind == "cell" else "본문 문단"
            item = QTreeWidgetItem(
                [
                    where,
                    block.text[:250].replace("\n", " ↵ "),
                    "셀" if block.kind == "cell" else "문단",
                ]
            )
            item.setData(0, DATA, block)
            item.setToolTip(0, block.locator)
            parent.addChild(item)
        self.source_tree.expandToDepth(1)
        self.filter_source()

    def filter_source(self):
        query = self.search.text().casefold()

        def visit(item):
            if item.childCount():
                visible = [visit(item.child(i)) for i in range(item.childCount())]
                show = any(visible)
            else:
                block = item.data(0, DATA)
                show = not query or (block is not None and query in block.text.casefold())
            item.setHidden(not show)
            return show

        for i in range(self.source_tree.topLevelItemCount()):
            visit(self.source_tree.topLevelItem(i))

    def select_source(self, current, previous=None):
        if current and (block := current.data(0, DATA)):
            self.show_evidence(block.text, block.location + "\n" + block.locator)

    def clear_evidence(self):
        self.source_text.clear()
        self.source_text.setExtraSelections([])
        self.evidence_status.setText("원문 항목이나 취합한 값을 선택하세요.")
        self.verify_button.setEnabled(False)

    def show_evidence(self, text, status, start=None, end=None):
        self.source_text.setPlainText(text)
        self.source_text.setExtraSelections([])
        self.evidence_status.setText(status)
        if start is not None:
            # Qt cursor positions use UTF-16 units, Python extraction uses Unicode codepoints.
            selection = QTextEdit.ExtraSelection()
            cursor = self.source_text.textCursor()
            cursor.setPosition(len(text[:start].encode("utf-16-le")) // 2)
            cursor.setPosition(
                len(text[:end].encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor
            )
            selection.cursor = cursor
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#F4E59E"))
            selection.format = fmt
            self.source_text.setExtraSelections([selection])
            self.source_text.setTextCursor(cursor)
            self.source_text.ensureCursorVisible()

    def get_workflow(self):
        return Workflow(self.workflow_name.text().strip(), tuple(self.rules))

    def set_workflow(self, workflow):
        self.workflow_name.setText(workflow.name)
        self.rules = list(workflow.rules)
        self.refresh_rules()

    def refresh_rules(self):
        self.rules_tree.clear()
        for rule in self.rules:
            self.rules_tree.addTopLevelItem(
                QTreeWidgetItem(
                    [
                        rule.name,
                        KINDS[rule.kind],
                        rule.description,
                        "필수" if rule.required else "선택",
                    ]
                )
            )
        self.invalidate_results()

    def invalidate_results(self, *_):
        self.results = ()
        self.result_workflow = None
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self.export_button.setEnabled(False)
        self.result_heading.setText("파일이나 규칙을 변경했습니다. 다시 취합하세요.")
        self.clear_evidence()

    def _accept_rule(self, dialog, index=None):
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rule = dialog.rule()
        rules = list(self.rules)
        if index is None:
            rules.append(rule)
        else:
            rules[index] = rule
        try:
            Workflow(self.workflow_name.text().strip() or "새 워크플로", tuple(rules))
        except WorkflowError as exc:
            self.message("규칙 확인", str(exc))
            return
        self.rules = rules
        self.refresh_rules()
        self.tabs.setCurrentIndex(1)

    def add_rule(self):
        self._accept_rule(RuleDialog(self))

    def add_rule_from_source(self):
        item = self.source_tree.currentItem()
        block = item.data(0, DATA) if item else None
        if block is None:
            self.message("원문 선택", "원문에서 추출할 셀이나 문단을 먼저 선택하세요.")
            return
        self._accept_rule(RuleDialog(self, block=block))

    def rule_index(self):
        return self.rules_tree.indexOfTopLevelItem(self.rules_tree.currentItem())

    def edit_rule(self):
        index = self.rule_index()
        if index >= 0:
            self._accept_rule(RuleDialog(self, rule=self.rules[index]), index)

    def remove_rule(self):
        index = self.rule_index()
        if index >= 0:
            self.rules.pop(index)
            self.refresh_rules()

    def move_rule(self, delta):
        index = self.rule_index()
        other = index + delta
        if 0 <= index < len(self.rules) and 0 <= other < len(self.rules):
            self.rules[index], self.rules[other] = self.rules[other], self.rules[index]
            self.refresh_rules()
            self.rules_tree.setCurrentItem(self.rules_tree.topLevelItem(other))

    def load_workflow(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "워크플로 불러오기", str(Path.home()), "워크플로 (*.json)", options=DIALOG_OPTIONS
        )
        if path:
            try:
                self.set_workflow(Workflow.load(path))
                self.tabs.setCurrentIndex(1)
            except (WorkflowError, OSError) as exc:
                self.message("불러오기 확인", str(exc))

    def save_workflow(self):
        try:
            wf = self.get_workflow()
        except WorkflowError as exc:
            self.message("워크플로 확인", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "워크플로 저장", "workflow.json", "워크플로 (*.json)", options=DIALOG_OPTIONS
        )
        if path:
            try:
                wf.save(path)
                self.statusBar().showMessage("워크플로를 저장했습니다.")
            except (ValueError, OSError) as exc:
                self.message("저장 확인", str(exc))

    def run_extraction(self):
        if self.busy:
            return
        try:
            wf = self.get_workflow()
        except WorkflowError as exc:
            self.message("추출 규칙 확인", str(exc))
            return
        if not self.paths:
            self.message("문서 확인", "HWPX 문서를 먼저 추가하세요.")
            return
        self.invalidate_results()
        paths = tuple(self.paths)

        def work(job):
            results = []
            for i, path in enumerate(paths, 1):
                job.checkpoint()
                try:
                    results.append(extract(read_hwpx(path), wf))
                except HwpxError as exc:
                    results.append(DocumentResult(path, "", (), str(exc)))
                job.signals.progress.emit(i, len(paths), f"취합 중 {i}/{len(paths)}")
            return tuple(results)

        def extracted(results):
            self.results = results
            self.result_workflow = wf
            self.show_results()

        self.start_job(work, extracted)

    def show_results(self):
        self.result_table.setColumnCount(len(self.rules) + 2)
        self.result_table.setHorizontalHeaderLabels(
            ["파일명", *(r.name for r in self.rules), "상태"]
        )
        self.result_table.setRowCount(len(self.results))
        for row, result in enumerate(self.results):
            self.result_table.setItem(row, 0, QTableWidgetItem(Path(result.path).name))
            for col, value in enumerate(result.values, 1):
                text = value.value if value.status == "ok" else f"[{STATUS[value.status]}]"
                item = QTableWidgetItem(text)
                item.setData(DATA, value)
                item.setToolTip(value.message or "클릭하면 원문 근거를 표시합니다.")
                if value.status != "ok":
                    item.setBackground(QColor("#FFF0D4"))
                self.result_table.setItem(row, col, item)
            state = result.error or ("정상" if result.ok else "확인 필요")
            self.result_table.setItem(row, len(self.rules) + 1, QTableWidgetItem(state))
        self.result_table.resizeColumnsToContents()
        for col in range(self.result_table.columnCount()):
            self.result_table.setColumnWidth(
                col, min(320, max(140, self.result_table.columnWidth(col)))
            )
        good = sum(r.ok for r in self.results)
        self.result_heading.setText(
            f"{len(self.results)}개 문서 취합 완료  ·  정상 {good}개  ·  확인 필요 {len(self.results) - good}개"
        )
        self.progress_text.setText("취합한 값을 선택해 원문을 검증하거나 Excel로 저장하세요.")
        self.tabs.setCurrentIndex(2)

    def selected_value(self):
        item = self.result_table.currentItem()
        value = item.data(DATA) if item else None
        return value if isinstance(value, ExtractedValue) and value.status == "ok" else None

    def select_result(self, *_):
        value = self.selected_value()
        self.verify_button.setEnabled(value is not None and not self.busy)
        if value and value.source:
            self.show_evidence(
                value.source.text,
                f"{value.field} · {value.source.location}\n"
                f"{value.source.locator} · SHA-256: {value.sha256}\n"
                "추출 당시 기록 · 현재 원본은 다시 검증할 수 있습니다.",
                value.start,
                value.end,
            )
        else:
            self.clear_evidence()
            item = self.result_table.currentItem()
            if item and isinstance(item.data(DATA), ExtractedValue):
                self.evidence_status.setText(item.data(DATA).message)

    def verify_selected(self):
        value = self.selected_value()
        if value is None or self.busy:
            return
        self.progress_text.setText("현재 원본 파일을 다시 읽어 검증합니다…")

        def verified(result):
            if self.selected_value() != value:
                self.progress_text.setText(
                    "선택한 값이 변경되어 이전 검증 결과를 표시하지 않았습니다."
                )
                return
            text = (
                result.source.text if result.source else (value.source.text if value.source else "")
            )
            self.show_evidence(
                text,
                result.message + "\n" + value.sha256,
                value.start if result.ok else None,
                value.end if result.ok else None,
            )
            self.progress_text.setText(
                "원본 검증 완료" if result.ok else "검증 실패 · 원본 또는 추출 값을 확인하세요."
            )

        self.start_job(lambda job: verify(value), verified)

    def save_excel(self):
        if not self.results or self.busy:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "취합 결과 저장",
            "취합결과.xlsx",
            "Excel 통합 문서 (*.xlsx)",
            options=DIALOG_OPTIONS,
        )
        if not path:
            return
        results, wf = self.results, self.result_workflow
        self.progress_text.setText("Excel에 값과 출처를 함께 저장합니다…")
        self.start_job(
            lambda job: export_excel(results, wf, path),
            lambda _: self.progress_text.setText(
                "Excel 저장 완료 · 결과 셀의 링크에서 출처를 확인할 수 있습니다."
            ),
            cancelable=False,
        )
