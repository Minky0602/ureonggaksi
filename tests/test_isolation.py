"""openpyxl 로드를 별도 작업자에서 시간·메모리 상한 안에 돌린다 (DIF-025, AT-17)."""

import pickle
import time

import pytest
from conftest import write_hwpx

from ureonggaksi import isolation
from ureonggaksi.errors import ServiceError
from ureonggaksi.inputs import open_inputs
from ureonggaksi.xlsx import inspect_target


def _sleep(seconds):
    time.sleep(seconds)
    return "done"


def _allocate(megabytes):
    block = bytearray(megabytes * 1024 * 1024)
    return len(block)


def _connect():
    import socket

    socket.create_connection(("127.0.0.1", 9), timeout=1)


def _raise_service_error():
    raise ServiceError("INPUT_UNSUPPORTED", "작업자 안의 오류", (("sheet", "가"),))


def test_service_error_survives_pickling():
    error = pickle.loads(pickle.dumps(ServiceError("INPUT_LIMIT", "문구", (("a", "b"),))))
    assert (error.code, error.message, error.detail) == ("INPUT_LIMIT", "문구", (("a", "b"),))


def test_errors_from_the_worker_keep_their_code():
    with pytest.raises(ServiceError) as info:
        isolation.run(_raise_service_error)
    assert (info.value.code, info.value.detail) == ("INPUT_UNSUPPORTED", (("sheet", "가"),))


def test_timeout_kills_the_worker_and_the_next_call_works():
    with pytest.raises(ServiceError) as info:
        isolation.run(_sleep, 30, timeout=1)
    assert info.value.code == "INPUT_LIMIT" and "1초" in info.value.message
    assert isolation.run(_sleep, 0) == "done"  # 새 작업자


def test_memory_ceiling_turns_into_input_limit():
    with pytest.raises(ServiceError) as info:
        isolation.run(_allocate, 600, memory=300 * 1024**2)
    assert info.value.code == "INPUT_LIMIT"
    assert isolation.run(_allocate, 10) == 10 * 1024 * 1024  # 기본 상한으로 돌아온다


def test_worker_cannot_open_network_sockets():
    with pytest.raises(PermissionError):
        isolation.run(_connect)


def test_inspect_target_runs_in_the_worker(tmp_path, target):
    inputs = open_inputs([write_hwpx(tmp_path / "a.hwpx")], target, tmp_path / "out")
    view = inspect_target(inputs)
    assert view.columns == ("사업코드", "사업명", "2026 예산")
