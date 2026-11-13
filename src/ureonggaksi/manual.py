"""Explanations for cells the app did not fill (PRD 6.3, api.md 5.1).

The table below is data: kind, display order, reason template and action. Screen and the
"확인필요" file both read it, so they can never disagree.
"""

from dataclasses import dataclass
from typing import Literal

ManualKind = Literal[
    "existing_differs",
    "no_row",
    "source_conflict",
    "unreadable_value",
    "blocked_cell",
    "unreadable_file",
    "no_document",
    "check_diff",
    "deselected",
]


@dataclass(frozen=True)
class ManualItem:
    kind: str  # ManualKind
    where: str  # "사업취합!D9" / 대상 없음이면 "사업계획_C.hwpx 표2 3행"
    reason: str  # 쉬운 말
    action: str  # 할 일
    reason_code: str  # 5.1절 코드
    change_id: str | None  # 파일 단위 항목이면 None


@dataclass(frozen=True)
class KindInfo:
    kind: str
    title: str
    action: str
    codes: tuple[str, ...]


# 표시 순서 = PRD 6.3절 순서
KINDS: tuple[KindInfo, ...] = (
    KindInfo(
        "existing_differs",
        "이미 다른 값이 있음",
        "맞는 값을 확인해 직접 고치기",
        ("existing_differs",),
    ),
    KindInfo(
        "no_row",
        "들어갈 행을 못 정함",
        "파일의 키를 확인하거나 맞는 행에 직접 입력",
        ("no_target", "multi_target"),
    ),
    KindInfo("source_conflict", "원본끼리 값이 다름", "원본을 확인해 직접 입력", ("conflict",)),
    KindInfo(
        "unreadable_value",
        "값을 확실히 못 읽음",
        "원문을 확인해 직접 입력",
        (
            "missing_value",
            "ambiguous_field",
            "ambiguous_unit",
            "parse_failed",
            "needs_rounding",
            "tracked_change",
        ),
    ),
    KindInfo(
        "blocked_cell",
        "쓰면 안 되는 칸",
        "칸 구조를 확인해 직접 입력",
        ("blocked_formula", "blocked_merged", "blocked_protected", "write_unsupported"),
    ),
    KindInfo(
        "unreadable_file",
        "파일을 읽지 못함",
        "파일을 확인해 다시 넣기",
        ("source_failed", "no_records"),
    ),
    KindInfo("no_document", "문서가 없는 행", "문서가 빠졌는지 확인", ("target_without_source",)),
    KindInfo(
        "check_diff",
        "값 검토 차이",
        "맞는 값을 확인해 직접 고치기",
        ("mismatch", "missing_in_sheet"),
    ),
    KindInfo("deselected", "직접 처리로 돌린 칸", "직접 입력", ("deselected",)),
)
KIND_BY_CODE = {code: info for info in KINDS for code in info.codes}
KIND_ORDER = {info.kind: index for index, info in enumerate(KINDS)}

REASONS = {
    "existing_differs": "칸에 이미 다른 값이 있음. 기존 값 유지 (기존 {previous} · 문서 {proposed})",
    "no_target": "같은 키의 행이 없음 (키 {key}). 키가 문서와 다르면 파일 오류 의심",
    "multi_target": "같은 키의 행이 여러 개임 (키 {key}, 후보 {candidates})",
    "conflict": "같은 칸에 문서마다 다른 값 ({sources})",
    "missing_value": "값을 찾지 못함",
    "ambiguous_field": "같은 항목이 여러 곳에 있음",
    "ambiguous_unit": "단위를 알 수 없음 (문서 {raw})",
    "parse_failed": "숫자·날짜로 읽을 수 없음 (문서 {raw})",
    "needs_rounding": "원 단위로 맞추려면 반올림이 필요함 (문서 {raw})",
    "tracked_change": "변경 추적이 남아 있음",
    "blocked_formula": "수식 칸",
    "blocked_merged": "병합 칸",
    "blocked_protected": "보호 칸",
    "write_unsupported": "이 칸에 그대로 넣을 수 없음 ({detail})",
    "source_failed": "파일을 읽지 못함 ({detail})",
    "no_records": "문서에서 레코드(규칙이 가리키는 표)를 찾지 못함",
    "target_without_source": "이 행의 값을 가진 문서가 입력에 없음 (키 {key})",
    "mismatch": "표와 문서가 다름 (표 {previous} · 문서 {proposed})",
    "missing_in_sheet": "표가 비어 있음 (문서 {proposed})",
    "deselected": "자세히에서 직접 처리로 돌림",
}


def describe(reason_code: str, **values) -> str:
    """5.1절 틀로 이유 문장을 만든다. 틀에 없는 자리는 비운다(AI 문장이 아니다)."""
    template = REASONS.get(reason_code, reason_code)
    try:
        return template.format(
            **{k: (v if v not in (None, "") else "(없음)") for k, v in values.items()}
        )
    except (KeyError, IndexError):
        return template


def kind_of(reason_code: str) -> str:
    info = KIND_BY_CODE.get(reason_code)
    return info.kind if info else "unreadable_value"


def action_of(kind: str) -> str:
    return next((info.action for info in KINDS if info.kind == kind), "직접 입력")


def sort_key(item: ManualItem) -> tuple:
    """종류 순서 → 주소(열 글자, 행 번호) → 코드. 'C10'이 'C3' 뒤에 온다."""
    sheet, _, address = item.where.rpartition("!")
    letters = address.rstrip("0123456789")
    digits = address[len(letters) :]
    row = int(digits) if digits.isdigit() else 0
    return (
        KIND_ORDER.get(item.kind, len(KINDS)),
        sheet,
        len(letters),
        letters,
        row,
        item.reason_code,
    )
