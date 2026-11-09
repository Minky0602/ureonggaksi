import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink

from . import __version__
from .models import DocumentResult
from .storage import atomic_write, local_path
from .workflow import Workflow


class ExportError(ValueError):
    pass


def _text_cell(sheet, row, col, text):
    text = str(text)
    if len(text.encode("utf-16-le", errors="surrogatepass")) // 2 > 32767:
        raise ExportError("Excel 셀의 32,767자 한도를 초과했습니다. 원문 또는 규칙을 나누세요.")
    if ILLEGAL_CHARACTERS_RE.search(text):
        raise ExportError("Excel에 저장할 수 없는 제어문자가 포함되어 있습니다.")
    cell = sheet.cell(row, col, text)
    cell.data_type = "s"  # Do not let document strings become formulas, even for headers/paths.
    cell.alignment = Alignment(vertical="top", wrap_text=True)
    return cell


def _append(sheet, values):
    row = sheet.max_row + 1 if sheet["A1"].value is not None else 1
    for col, value in enumerate(values, 1):
        _text_cell(sheet, row, col, value)
    return row


def export_excel(results: Sequence[DocumentResult], workflow: Workflow, path: str | Path):
    if not results:
        raise ExportError("내보낼 결과가 없습니다.")
    if len(results) * len(workflow.rules) > 1_000_000:
        raise ExportError("Excel 출처 시트의 최대 행 수를 초과했습니다. 작업을 나누세요.")
    try:
        path = local_path(path)
        if path.suffix.lower() != ".xlsx":
            raise ExportError("저장 파일의 확장자는 .xlsx여야 합니다.")
        if any(path == local_path(r.path) for r in results):
            raise ExportError("원본 문서에 덮어쓸 수 없습니다.")
        wb = Workbook()
        summary = wb.active
        summary.title = "취합"
        trace = wb.create_sheet("출처")
        meta = wb.create_sheet("실행정보")
        _append(summary, ["파일명", *(r.name for r in workflow.rules), "상태"])
        _append(
            trace,
            [
                "결과 셀",
                "파일명",
                "원본 경로",
                "SHA-256",
                "항목",
                "값",
                "상태",
                "필수",
                "구역",
                "XML 경로",
                "표",
                "행",
                "열",
                "시작(0기준)",
                "끝(미포함)",
                "원문 블록",
                "메시지",
            ],
        )
        for row, result in enumerate(results, 2):
            _text_cell(summary, row, 1, Path(result.path).name)
            by_name = {v.field: v for v in result.values}
            for col, rule in enumerate(workflow.rules, 2):
                value = by_name.get(rule.name)
                text = value.value if value else ""
                target = _text_cell(summary, row, col, text)
                coordinate = f"{get_column_letter(col)}{row}"
                source = value.source if value else None
                trace_row = _append(
                    trace,
                    [
                        f"취합!{coordinate}",
                        Path(result.path).name,
                        result.path,
                        result.sha256,
                        rule.name,
                        text,
                        value.status if value else "error",
                        "예" if rule.required else "아니오",
                        source.section if source else "",
                        source.locator if source else "",
                        source.table if source else "",
                        source.row if source else "",
                        source.col if source else "",
                        value.start if value and source else "",
                        value.end if value and source else "",
                        source.text if source else "",
                        value.message if value else result.error,
                    ],
                )
                target.hyperlink = Hyperlink(
                    ref=coordinate, location=f"'출처'!A{trace_row}", tooltip="원문 출처 보기"
                )
                target.font = Font(color="14675C", underline="single")
                target.comment = Comment(
                    f"출처 시트 {trace_row}행\nSHA-256: {result.sha256}\n"
                    + (source.location if source else "원문 위치 없음"),
                    "우렁각시",
                )
                if value and value.status != "ok":
                    target.fill = PatternFill("solid", fgColor="FFF0D4")
            issues = [
                f"{v.field}: {v.status}{' (선택)' if not v.required else ''}"
                for v in result.values
                if v.status != "ok"
            ]
            _text_cell(
                summary,
                row,
                len(workflow.rules) + 2,
                result.error or (" / ".join(issues) if issues else "정상"),
            )
        config = json.dumps(asdict(workflow), ensure_ascii=False, sort_keys=True)
        for values in [
            ["항목", "내용"],
            ["앱", f"우렁각시 {__version__}"],
            ["실행 시각(UTC)", datetime.now(UTC).isoformat(timespec="seconds")],
            ["워크플로", workflow.name],
            ["워크플로 버전", workflow.version],
            ["워크플로 SHA-256", hashlib.sha256(config.encode()).hexdigest()],
            ["문서 수", len(results)],
            ["안내", "문자 범위는 원문 블록의 Python 문자열 0기준, 끝 미포함입니다."],
            [
                "검증 범위",
                "해시는 변경 감지용이며 전자서명이 아닙니다. 추출 시점의 원문과 위치를 기록합니다.",
            ],
            ["원문 위치", "XML 구조 좌표이며 인쇄 페이지 번호가 아닙니다."],
        ]:
            _append(meta, values)
        _append(meta, ["규칙 이름", "규칙 JSON"])
        for rule in workflow.rules:
            _append(meta, [rule.name, json.dumps(asdict(rule), ensure_ascii=False, sort_keys=True)])
        for sheet in wb:
            sheet.freeze_panes = "B2" if sheet is summary else "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for cell in sheet[1]:
                cell.fill = PatternFill("solid", fgColor="174C43")
                cell.font = Font(color="FFFFFF", bold=True)
            for col in range(1, sheet.max_column + 1):
                sheet.column_dimensions[get_column_letter(col)].width = 25
        trace.column_dimensions["J"].width = 60
        trace.column_dimensions["P"].width = 60
        meta.column_dimensions["B"].width = 90
        try:
            atomic_write(path, wb.save)
        finally:
            wb.close()
    except ExportError:
        raise
    except (OSError, ValueError) as exc:
        raise ExportError(
            "Excel을 저장할 수 없습니다. 파일이 열려 있는지, 경로와 권한을 확인하세요."
        ) from exc
