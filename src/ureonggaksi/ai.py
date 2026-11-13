"""Local model runner: pick one option from a list, offline (P04 ②, PRD FR-05·06·19, DIF-006·030).

The model never writes rules or values. `interpret` asks it only for pairs code could not decide, and
the answer is constrained to the given options by a JSON-schema enum (llama.cpp `-jf`), so a malformed
answer cannot happen structurally. We start `llama-completion` as a child process per question — no
HTTP server, no port (DIF-006) — with `--offline` and a local model file.
"""

import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import ServiceError

SYSTEM = (
    "당신은 공공업무 담당자의 한 줄 요청을 읽고, 주어진 선택지 가운데 하나만 고르는 도우미다. "
    "값이나 규칙을 직접 쓰지 않는다. 이름이 달라도 같은 뜻인 것(예: 직급과 직위, 품명과 품목)을 고른다. "
    "뜻이 다르거나 확신이 없으면 '없음'을 고른다."
)
# S01과 같은 실행 조건(결정적 출력). 답이 선택지 하나라 짧게 끊는다
ARGS = (
    "--offline",
    "-no-cnv",
    "--no-display-prompt",
    "--temp",
    "0",
    "--seed",
    "1",
    "-n",
    "64",
    "-c",
    "4096",
)
END_MARK = "[end of text]"  # llama-completion이 생성 뒤 stdout에 붙인다
TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class AiStatus:  # api 2.5
    ready: bool
    runtime: str
    model: str
    reason: Literal["", "not_installed", "not_running", "no_model", "low_memory"]


def ai_status(runtime: Path | None, model: Path | None) -> AiStatus:
    """실행 파일과 모델 파일이 있는지만 본다. 없으면 질문·자세히(직접 선택)로 넘어간다."""
    runtime_text, model_text = str(runtime or ""), str(model or "")
    if runtime is None or not Path(runtime).is_file():
        return AiStatus(False, runtime_text, model_text, "not_installed")
    if model is None or not Path(model).is_file():
        return AiStatus(False, runtime_text, model_text, "no_model")
    return AiStatus(True, runtime_text, model_text, "")


def chat_prompt(question: str) -> str:
    """Qwen 채팅 틀. 생각 단계는 비워 둔다(S01 v2와 같음)."""
    return (
        f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n{question}<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


@dataclass(frozen=True)
class LocalModel:
    """`command`는 실행 파일(시험에서는 [python, 가짜 스크립트])이다. `choose`가 interpret의 Chooser다."""

    command: Sequence[str]
    model: Path
    timeout_s: int = TIMEOUT_SECONDS

    def choose(self, question: str, options: tuple[str, ...]) -> str | None:
        """선택지 하나를 돌려준다. 실행할 수 없거나 시간을 넘으면 예외, 목록 밖 답이면 None."""
        if not options:
            return None
        schema = {"type": "string", "enum": list(options)}
        with tempfile.TemporaryDirectory(prefix="ureonggaksi-ai-") as folder:
            prompt_path, schema_path = Path(folder) / "prompt.txt", Path(folder) / "schema.json"
            prompt_path.write_text(chat_prompt(question), encoding="utf-8", newline="\n")
            schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            cmd = [
                *self.command,
                "-m",
                str(self.model),
                "-jf",
                str(schema_path),
                "-f",
                str(prompt_path),
                *ARGS,
            ]
            try:
                done = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=self.timeout_s,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0,
                )
            except FileNotFoundError as exc:
                raise ServiceError(
                    "AI_UNAVAILABLE", "로컬 AI 실행 파일을 찾지 못했습니다."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise ServiceError(
                    "AI_TIMEOUT", f"로컬 AI가 {self.timeout_s}초 안에 답하지 않았습니다."
                ) from exc
        if done.returncode != 0:
            raise ServiceError(
                "AI_UNAVAILABLE", f"로컬 AI 실행이 실패했습니다(종료 코드 {done.returncode})."
            )
        return parse_choice(done.stdout.decode("utf-8", "replace"), options)


def parse_choice(stdout: str, options: tuple[str, ...]) -> str | None:
    text = stdout.strip()
    if text.endswith(END_MARK):
        text = text[: -len(END_MARK)].rstrip()
    try:
        answer = json.loads(text)
    except ValueError:
        return None
    return answer if isinstance(answer, str) and answer in options else None
