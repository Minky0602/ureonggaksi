from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from ..models import Block
from ..workflow import Rule, WorkflowError

KINDS = {"cell": "지정 셀", "label": "항목명 옆/아래 셀", "regex": "정규식 (본문·셀)"}


class RuleDialog(QDialog):
    def __init__(self, parent=None, *, rule: Rule | None = None, block: Block | None = None):
        super().__init__(parent)
        self.setWindowTitle("추출 항목 편집" if rule else "추출 항목 추가")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)
        self.name = QLineEdit(rule.name if rule else "새 항목")
        self.kind = QComboBox()
        for key, title in KINDS.items():
            self.kind.addItem(title, key)
        self.kind.setCurrentIndex(
            self.kind.findData(
                rule.kind if rule else ("cell" if block and block.kind == "cell" else "label")
            )
        )
        self.query = QLineEdit(rule.query if rule else "")
        self.query.setPlaceholderText("예: 성명  또는  접수번호:\\s*(\\S+)")
        self.section = QLineEdit(rule.section if rule else (block.section if block else ""))
        self.section.setPlaceholderText("빈칸: 전체 구역 · 예: Contents/section0.xml")
        self.table, self.row, self.col, self.group = [QSpinBox() for _ in range(4)]
        for spin in (self.table, self.row, self.col):
            spin.setRange(1, 1_000_000)
        self.group.setRange(0, 1000)
        for name in ("table", "row", "col"):
            getattr(self, name).setValue(
                getattr(rule, name) if rule else max(1, getattr(block, name, 1))
            )
        self.group.setValue(rule.group if rule else 1)
        self.direction = QComboBox()
        self.direction.addItem("오른쪽 셀", "right")
        self.direction.addItem("아래 셀", "below")
        self.direction.setCurrentIndex(1 if rule and rule.direction == "below" else 0)
        self.required = QCheckBox("값이 없으면 확인이 필요한 필수 항목")
        self.required.setChecked(rule.required if rule else True)
        self.fields = {}
        for label, widget in [
            ("엑셀 열 이름", self.name),
            ("추출 방식", self.kind),
            ("항목명 / 정규식", self.query),
            ("구역", self.section),
            ("표 번호", self.table),
            ("행 번호", self.row),
            ("열 번호", self.col),
            ("값의 방향", self.direction),
            ("캡처 그룹", self.group),
        ]:
            form.addRow(label, widget)
            self.fields[widget] = form.labelForField(widget)
        form.addRow("", self.required)
        layout.addLayout(form)
        hint = QLabel(
            "번호는 1부터 시작합니다. 정규식은 블록마다 적용되며, 그룹 0은 전체 일치입니다.\n항목명은 공백·끝의 콜론을 제외하고 정확히 비교합니다. 중복 일치는 자동 선택하지 않습니다."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("항목 저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self.update_fields)
        self.update_fields()

    def update_fields(self):
        kind = self.kind.currentData()
        for widget, show in [
            (self.query, kind != "cell"),
            (self.table, kind == "cell"),
            (self.row, kind == "cell"),
            (self.col, kind == "cell"),
            (self.direction, kind == "label"),
            (self.group, kind == "regex"),
        ]:
            widget.setVisible(show)
            self.fields[widget].setVisible(show)

    def rule(self):
        return Rule(
            self.name.text().strip(),
            self.kind.currentData(),
            query=self.query.text(),
            section=self.section.text().strip(),
            table=self.table.value(),
            row=self.row.value(),
            col=self.col.value(),
            direction=self.direction.currentData(),
            group=self.group.value(),
            required=self.required.isChecked(),
        )

    def accept(self):
        try:
            self.rule()
        except WorkflowError as exc:
            box = QMessageBox(self)
            box.setWindowTitle("규칙 확인")
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(str(exc))
            box.exec()
            return
        super().accept()
