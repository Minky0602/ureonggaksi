"""Service-level failures raised before any file is written (api.md 5절)."""

from typing import Literal

ErrorCode = Literal[
    "PLAN_HASH_MISMATCH",
    "INPUT_UNSUPPORTED",
    "INPUT_LIMIT",
    "PATH_CONFLICT",
    "RULE_INVALID",
    "PLAN_IS_SAMPLE",
    "REVIEW_REJECTED",
    "NOTHING_TO_WRITE",
    "RULE_SAVE_FAILED",
    "APPROVAL_INVALID",
    "INPUT_CHANGED",
    "WRITE_FAILED",
    "VERIFY_FAILED",
    "EVIDENCE_FAILED",
    "PUBLISH_FAILED",
    "CANCELLED",
    "AI_UNAVAILABLE",
    "AI_TIMEOUT",
    "AI_BAD_OUTPUT",
]

Pairs = tuple[tuple[str, str], ...]


class ServiceError(Exception):
    """부작용(파일 쓰기)을 시작하기 전의 실패. detail에는 원문 값을 넣지 않는다."""

    def __init__(self, code: ErrorCode, message: str, detail: Pairs = ()):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
