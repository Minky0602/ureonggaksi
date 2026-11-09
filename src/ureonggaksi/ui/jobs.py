import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class Cancelled(Exception):
    pass


class Signals(QObject):
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()


class Job(QRunnable):
    def __init__(self, work: Callable):
        super().__init__()
        self.signals = Signals()
        self.stop = threading.Event()
        self.work = work

    def checkpoint(self):
        if self.stop.is_set():
            raise Cancelled

    @Slot()
    def run(self):
        try:
            result = self.work(self)
            self.checkpoint()
        except Cancelled:
            self.signals.cancelled.emit()
        except Exception as exc:
            # Do not persist content or tracebacks. Domain errors are user-safe.
            self.signals.failed.emit(
                str(exc)
                if isinstance(exc, (ValueError, OSError))
                else "처리 중 오류가 발생했습니다. 문서와 규칙을 확인하세요."
            )
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
