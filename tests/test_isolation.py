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


def _exit_hard():
    import os

    os._exit(3)


class _TwoArgError(Exception):
    def __init__(self, a, b):
        super().__init__(a)
        self.b = b


def _raise_unpicklable():
    raise _TwoArgError("a", "b")


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
    with pytest.raises(ServiceError) as info:  # 작업자 안의 예외는 종류만 ServiceError로 전달된다
        isolation.run(_connect)
    assert "PermissionError" in info.value.message


def test_inspect_target_runs_in_the_worker(tmp_path, target):
    inputs = open_inputs([write_hwpx(tmp_path / "a.hwpx")], target, tmp_path / "out")
    view = inspect_target(inputs)
    assert view.columns == ("사업코드", "사업명", "2026 예산")


def test_dead_worker_is_reported_at_once_not_after_the_timeout():
    """작업자가 결과 없이 죽으면 시간 상한을 기다리지 않는다(검수 M1)."""
    began = time.monotonic()
    with pytest.raises(ServiceError) as info:
        isolation.run(_exit_hard, timeout=60)
    assert info.value.code == "INPUT_LIMIT" and "비정상" in info.value.message
    assert time.monotonic() - began < 15


def test_worker_setup_failure_is_reported_at_once():
    """메모리 상한을 걸 수 없는 환경 등 작업자 준비 실패는 바로 알린다(검수 M2)."""
    began = time.monotonic()
    with pytest.raises(ServiceError) as info:
        isolation.run(_sleep, 0, memory="bogus", timeout=60)
    assert info.value.code == "INPUT_UNSUPPORTED" and "준비" in info.value.message
    assert time.monotonic() - began < 15


def test_unpicklable_exception_does_not_break_later_calls():
    """부모가 재구성할 수 없는 예외도 ServiceError로 바뀌고, 다음 호출은 정상이다(검수 M3)."""
    with pytest.raises(ServiceError) as info:
        isolation.run(_raise_unpicklable)
    assert info.value.code == "INPUT_UNSUPPORTED" and "_TwoArgError" in info.value.message
    assert isolation.run(_sleep, 0) == "done"


def test_real_openpyxl_load_over_the_memory_ceiling_is_input_limit(tmp_path):
    """bytearray가 아니라 실제 openpyxl 로드가 상한을 넘을 때(검수 M1). 사전 검사는 거치지 않는다."""
    from openpyxl import Workbook

    from ureonggaksi import xlsx

    book = Workbook()
    sheet = book.active
    for row in range(1, 60_001):
        sheet.append([f"코드{row}", row, row * 2, "가나다라마바사"])
    path = tmp_path / "big.xlsx"
    book.save(path)
    began = time.monotonic()
    with pytest.raises(ServiceError) as info:
        isolation.run(
            xlsx._inspect, path.read_bytes(), None, None, None, 10, 10_000_000, memory=150 * 1024**2
        )
    assert info.value.code == "INPUT_LIMIT"
    assert time.monotonic() - began < 50


def test_sheet_names_read_only_real_sheets_and_require_their_parts(tmp_path):
    from zipfile import ZipFile

    from conftest import write_xlsx

    from ureonggaksi.xlsx import sheet_names

    path = write_xlsx(tmp_path / "t.xlsx")
    data = path.read_bytes()
    assert sheet_names(data) == ("사업취합",)

    def rewrite(transform):
        out = tmp_path / "x.xlsx"
        with ZipFile(path) as src, ZipFile(out, "w") as dst:
            for item in src.infolist():
                name, payload = transform(item.filename, src.read(item.filename))
                if name:
                    dst.writestr(name, payload)
        return out.read_bytes()

    ghost = rewrite(
        lambda n, p: (
            (
                n,
                p.replace(
                    b"</workbook>",
                    b'<extLst><z:sheet xmlns:z="urn:x" name="ghost"/></extLst></workbook>',
                ),
            )
            if n == "xl/workbook.xml"
            else (n, p)
        )
    )
    assert sheet_names(ghost) == ("사업취합",)
    missing_part = rewrite(lambda n, p: (None, p) if n == "xl/worksheets/sheet1.xml" else (n, p))
    with pytest.raises(ServiceError):
        sheet_names(missing_part)
