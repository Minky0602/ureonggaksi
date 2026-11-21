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
    with pytest.raises(ServiceError) as caught:
        fake_model(tmp_path, "garbage").choose("q", ("직위", "없음"))
    assert caught.value.code == "AI_BAD_OUTPUT"
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


# ---------- 여러 칸을 한 번에 묻기(ask_fields, DIF-048·050) ----------

FAKE_FIELDS = """
import json, sys
args = sys.argv[1:]
schema = json.load(open(args[args.index("-jf") + 1], encoding="utf-8"))
mode = {mode!r}
open({log!r}, "w", encoding="utf-8").write(args[args.index("-n") + 1])
answer = {{k: p["enum"][-1] for k, p in schema["properties"].items()}}
if mode == "outside":
    answer = {{"[이름] 없는이름": "채움", **{{k: "없는열" for k in answer}}}}
out = json.dumps(answer, ensure_ascii=False)
if mode == "cut":
    out = out[: len(out) // 2]
if mode == "list":
    out = "[1, 2]"
sys.stdout.buffer.write((out + " [end of text]\\n").encode("utf-8"))
"""


def fields_model(tmp_path, mode):
    script, log = tmp_path / f"fields_{mode}.py", tmp_path / "n.txt"
    script.write_text(FAKE_FIELDS.format(mode=mode, log=str(log)), encoding="utf-8")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    return LocalModel((sys.executable, "-I", str(script)), model, 20), log


def test_ask_fields_reads_every_name_and_sizes_the_answer_length(tmp_path):
    """M4: 이름이 많아도 답이 잘리지 않게 생성 상한을 이름 수에 맞춘다."""
    fields = {f"[말] 낱말{i}": ("이수시간/채움", "이수시간/제외", "없음") for i in range(9)}
    model, log = fields_model(tmp_path, "ok")
    assert model.ask_fields("q", fields) == dict.fromkeys(fields, "없음")
    assert int(log.read_text(encoding="utf-8")) >= 9 * len('"[말] 낱말0": "이수시간/제외", ')


@pytest.mark.parametrize("mode", ["cut", "list"])
def test_ask_fields_reports_cut_or_non_object_answers(tmp_path, mode):
    model, _ = fields_model(tmp_path, mode)
    with pytest.raises(ServiceError) as caught:
        model.ask_fields("q", {"[이름] 직급": ("채움", "제외")})
    assert caught.value.code == "AI_BAD_OUTPUT"


def test_ask_fields_drops_names_and_answers_outside_the_lists(tmp_path):
    model, _ = fields_model(tmp_path, "outside")
    assert model.ask_fields("q", {"[이름] 직급": ("채움", "제외")}) == {}


def test_stop_all_ends_a_running_model_call_quickly(tmp_path):
    """창을 닫으면 도는 모델 프로세스를 끝내 질문당 최대 60초를 기다리지 않는다(화면 검수 M2)."""
    import threading
    import time

    from ureonggaksi.ai import stop_all

    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    slow = LocalModel((sys.executable, "-I", str(script)), model, 60)
    outcome = []

    def call():
        try:
            slow.choose("q", ("a", "없음"))
        except ServiceError as error:
            outcome.append(error.code)

    worker = threading.Thread(target=call)
    started = time.monotonic()
    worker.start()
    time.sleep(1.0)
    stop_all()
    worker.join(10)
    assert not worker.is_alive() and outcome == ["AI_UNAVAILABLE"]
    assert time.monotonic() - started < 10


def test_a_call_started_after_stop_all_is_ended_at_once(tmp_path):
    """취소 순간 아직 띄우는 중이던 물음도 등록하면서 끝낸다(PR #29 검수). resume 뒤에는 다시 돈다."""
    import time

    from ureonggaksi.ai import resume, stop_all

    slow = fake_model(tmp_path, "sleep", 60)
    stop_all()
    started = time.monotonic()
    with pytest.raises(ServiceError) as caught:
        slow.choose("q", ("a", "없음"))
    assert caught.value.code == "AI_UNAVAILABLE" and time.monotonic() - started < 10
    resume()
    assert fake_model(tmp_path, "first").choose("q", ("a", "없음")) == "a"
