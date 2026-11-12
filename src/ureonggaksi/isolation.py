"""Run a parser in a separate worker process with a time and memory ceiling (DIF-025).

The pre-scan in xlsx.py blocks known malicious patterns. This module bounds the worst case for
patterns nobody has found yet: the worker is killed after a timeout and cannot allocate past a
memory limit. Each call gets a fresh worker that exits afterwards, so no memory or broken state carries over.
"""

import multiprocessing
import os
from collections.abc import Callable
from multiprocessing.connection import wait

from .errors import ServiceError

TIMEOUT_SECONDS = 60  # 정상 10만 행 시트 분석 18.7초 실측의 약 3배
MEMORY_BYTES = 2 * 1024**3  # 정상 45만 칸 시트 1회 로드 166 MiB 실측, 두 번 열기 + 여유


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


def _worker(conn, func: Callable, args: tuple, memory: int) -> None:
    """작업자 본체. 결과·오류를 파이프로 한 번 보낸다. 피클할 수 없는 예외도 ServiceError로 바꿔 보낸다."""
    try:
        _initialize(memory)
    except Exception as exc:  # noqa: BLE001 - 어떤 이유든 작업자를 준비하지 못한 것이다
        conn.send(
            (
                "error",
                ServiceError(
                    "INPUT_UNSUPPORTED", f"작업자를 준비하지 못했습니다({type(exc).__name__})."
                ),
            )
        )
        return
    failure = None
    try:
        result = func(*args)
    except ServiceError as exc:
        failure = exc
    except MemoryError:
        failure = "memory"  # except 블록을 벗어나 traceback이 잡던 객체를 놓은 뒤 보낸다
    except Exception as exc:  # noqa: BLE001 - 원문은 넘기지 않고 종류만 알린다
        failure = ServiceError(
            "INPUT_UNSUPPORTED", f"대상 XLSX를 읽을 수 없습니다({type(exc).__name__})."
        )
    if failure == "memory":
        failure = ServiceError(
            "INPUT_LIMIT", "대상 XLSX를 여는 데 메모리 한도를 넘었습니다. 파일을 나눠 주세요."
        )
    try:
        conn.send(("error", failure) if failure is not None else ("ok", result))
    except Exception as exc:  # noqa: BLE001 - 결과를 보낼 수 없다(피클 불가 등): 프로그램 오류로 알린다
        try:
            conn.send(
                (
                    "error",
                    ServiceError(
                        "INPUT_UNSUPPORTED",
                        f"작업 결과를 전달하지 못했습니다({type(exc).__name__}).",
                    ),
                )
            )
        except Exception:  # noqa: BLE001 - 이것도 못 보내면 부모가 '비정상 종료'로 본다
            pass
    finally:
        conn.close()


def run(func: Callable, *args, timeout: float | None = None, memory: int | None = None):
    """func(*args)를 새 작업자 프로세스에서 돌린다. 호출마다 작업자를 새로 만들고 끝나면 없앤다.

    - 시간을 넘으면 작업자를 죽이고 ServiceError(INPUT_LIMIT, "N초").
    - 작업자가 결과 없이 끝나면(메모리 한도에서 죽음 등) 바로 ServiceError(INPUT_LIMIT, "비정상 종료").
    - 작업자가 던진 ServiceError는 그대로 전달된다.
    func와 인자·결과는 피클할 수 있어야 한다.
    """
    timeout = TIMEOUT_SECONDS if timeout is None else timeout
    memory = MEMORY_BYTES if memory is None else memory
    context = multiprocessing.get_context(
        "spawn"
    )  # 모든 OS에서 같은 방식(스레드와 fork를 섞지 않는다)
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, func, args, memory), daemon=True)
    try:
        process.start()
    except OSError as exc:
        receiver.close()
        sender.close()
        raise ServiceError("INPUT_UNSUPPORTED", "작업자 프로세스를 시작하지 못했습니다.") from exc
    sender.close()  # 부모 쪽 송신 끝을 닫아야 작업자가 죽었을 때 EOF를 받는다
    try:
        ready = wait([receiver, process.sentinel], timeout)
        if receiver in ready:
            try:
                kind, payload = receiver.recv()
            except (EOFError, OSError):
                kind, payload = "died", None
        elif ready:
            kind, payload = "died", None
        else:
            raise ServiceError(
                "INPUT_LIMIT",
                f"대상 XLSX를 여는 데 {timeout:g}초를 넘었습니다. 파일을 나누거나 불필요한 시트를 지워 주세요.",
            )
        if kind == "ok":
            return payload
        if kind == "error":
            raise payload
        raise ServiceError(
            "INPUT_LIMIT",
            "대상 XLSX를 여는 작업이 비정상으로 끝났습니다(메모리 한도 초과 등). 파일을 나눠 주세요.",
        )
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        receiver.close()
