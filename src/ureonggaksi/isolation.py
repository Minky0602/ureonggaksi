"""Run a parser in a separate worker process with a time and memory ceiling (DIF-025).

The pre-scan in xlsx.py blocks known malicious patterns. This module bounds the worst case for
patterns nobody has found yet: the worker is killed after a timeout and cannot allocate past a
memory limit. One worker is reused; a killed worker is replaced on the next call.
"""

import multiprocessing
import os
import threading
from collections.abc import Callable
from multiprocessing.pool import Pool

from .errors import ServiceError

TIMEOUT_SECONDS = 60  # 정상 10만 행 시트 분석 18.7초 실측의 약 3배
MEMORY_BYTES = 2 * 1024**3  # 정상 45만 칸 시트 1회 로드 166 MiB 실측, 두 번 열기 + 여유

_lock = threading.Lock()
_pool: Pool | None = None
_pool_memory: int | None = None


def _limit_memory(limit: int) -> None:
    """작업자 자신에게 메모리 상한을 건다. 넘는 할당은 MemoryError가 된다."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = limit
        ok = kernel32.SetInformationJobObject(
            wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)
        )  # 9 = JobObjectExtendedLimitInformation
        ok = ok and kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(job), wintypes.HANDLE(kernel32.GetCurrentProcess())
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "작업자 메모리 상한을 걸 수 없습니다.")
    else:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _initialize(memory: int) -> None:
    from .offline import install_network_guard

    install_network_guard()  # 작업자도 네트워크를 쓰지 않는다
    _limit_memory(memory)


def _call(func: Callable, args: tuple):
    try:
        return func(*args)
    except MemoryError as exc:
        raise ServiceError(
            "INPUT_LIMIT", "대상 XLSX를 여는 데 메모리 한도를 넘었습니다. 파일을 나눠 주세요."
        ) from exc


def _get_pool(memory: int) -> Pool:
    global _pool, _pool_memory
    if _pool is None or _pool_memory != memory:
        if _pool is not None:
            _pool.terminate()
        context = multiprocessing.get_context(
            "spawn"
        )  # 모든 OS에서 같은 방식(스레드와 fork를 섞지 않는다)
        _pool = context.Pool(1, initializer=_initialize, initargs=(memory,))
        _pool_memory = memory
    return _pool


def shutdown() -> None:
    global _pool, _pool_memory
    with _lock:
        if _pool is not None:
            _pool.terminate()
            _pool.join()
        _pool, _pool_memory = None, None


def run(func: Callable, *args, timeout: float | None = None, memory: int | None = None):
    """func(*args)를 작업자에서 돌린다. 시간·메모리를 넘으면 ServiceError(INPUT_LIMIT).

    func와 인자·결과는 피클할 수 있어야 한다. 작업자가 던진 ServiceError는 그대로 전달된다.
    """
    timeout = TIMEOUT_SECONDS if timeout is None else timeout
    memory = MEMORY_BYTES if memory is None else memory
    with _lock:  # 작업자가 하나라 한 번에 하나씩
        pool = _get_pool(memory)
        pending = pool.apply_async(_call, (func, args))
        try:
            return pending.get(timeout)
        except multiprocessing.TimeoutError as exc:
            # 멈춘 작업자를 죽인다. 다음 호출이 새로 만든다
            global _pool
            pool.terminate()
            _pool = None
            raise ServiceError(
                "INPUT_LIMIT",
                f"대상 XLSX를 여는 데 {timeout:g}초를 넘었습니다. 파일을 나누거나 불필요한 시트를 지워 주세요.",
            ) from exc
