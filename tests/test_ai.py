"""로컬 AI 실행기: 선택지 하나만 고르고, 못 하면 알린다 (P04 ②, PRD FR-05·06·19)."""

import json
import os
import sys
from pathlib import Path

import pytest

from ureonggaksi.ai import LocalModel, ai_status, chat_prompt, parse_choice
from ureonggaksi.errors import ServiceError

FAKE = """
import json, sys, time
args = sys.argv[1:]
schema = json.load(open(args[args.index("-jf") + 1], encoding="utf-8"))
prompt = open(args[args.index("-f") + 1], encoding="utf-8").read()
assert "--offline" in args and "-m" in args
mode = {mode!r}
if mode == "sleep":
    time.sleep(30)
if mode == "fail":
    sys.exit(3)
answer = {{"first": schema["enum"][0], "outside": "주소", "garbage": None}}[mode]
out = "not json" if answer is None else json.dumps(answer, ensure_ascii=False)
sys.stdout.buffer.write((out + " [end of text]\\n").encode("utf-8"))
"""


def fake_model(tmp_path, mode, timeout_s=20):
    script = tmp_path / f"fake_{mode}.py"
    script.write_text(FAKE.format(mode=mode), encoding="utf-8")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    return LocalModel((sys.executable, "-I", str(script)), model, timeout_s)


def test_answer_is_one_of_the_options(tmp_path):
    assert fake_model(tmp_path, "first").choose("직급에 넣을 항목?", ("직위", "없음")) == "직위"


def test_answers_outside_the_options_or_unparsable_are_dropped(tmp_path):
    assert fake_model(tmp_path, "outside").choose("q", ("직위", "없음")) is None
    assert fake_model(tmp_path, "garbage").choose("q", ("직위", "없음")) is None
    assert parse_choice('"직위"\n[end of text]', ("직위",)) == "직위"


def test_failures_are_reported_not_guessed(tmp_path):
    with pytest.raises(ServiceError) as info:
        fake_model(tmp_path, "fail").choose("q", ("a",))
    assert info.value.code == "AI_UNAVAILABLE"
    with pytest.raises(ServiceError) as info:
        fake_model(tmp_path, "sleep", timeout_s=1).choose("q", ("a",))
    assert info.value.code == "AI_TIMEOUT"
    missing = LocalModel((str(tmp_path / "없는.exe"),), tmp_path / "m.gguf")
    with pytest.raises(ServiceError) as info:
        missing.choose("q", ("a",))
    assert info.value.code == "AI_UNAVAILABLE"


def test_status_says_what_is_missing(tmp_path):
    runtime, model = tmp_path / "llama.exe", tmp_path / "m.gguf"
    assert ai_status(None, None).reason == "not_installed"
    runtime.write_bytes(b"")
    assert ai_status(runtime, model).reason == "no_model"
    model.write_bytes(b"")
    assert ai_status(runtime, model).ready


def test_prompt_keeps_thinking_empty():
    assert chat_prompt("질문").endswith("<think>\n\n</think>\n\n")


@pytest.mark.skipif(
    not (os.environ.get("UREONGGAKSI_LLAMA") and os.environ.get("UREONGGAKSI_MODEL")),
    reason="실제 llama.cpp·모델 경로가 있을 때만(로컬 전용)",
)
def test_real_model_picks_the_synonym():
    model = LocalModel((os.environ["UREONGGAKSI_LLAMA"],), Path(os.environ["UREONGGAKSI_MODEL"]))
    question = "요청: 이름으로 찾아서 직급 채워줘\n취합표 열 '직급'에 넣을 문서 항목을 고르세요.\n- 성명\n- 직위\n- 이수시간"
    assert model.choose(question, ("성명", "직위", "이수시간", "없음")) == "직위"
    json.dumps(question)  # 질문은 평문이다(외부 전송 없음)
