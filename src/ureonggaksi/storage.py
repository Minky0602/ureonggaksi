"""Local paths and atomic writes. No shell, archive extraction or remote URLs."""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path


def local_path(path: str | Path) -> Path:
    path = Path(path).expanduser().absolute()
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("네트워크 공유 경로는 사용할 수 없습니다. 로컬 디스크를 선택하세요.")
    if os.name == "nt":
        import ctypes

        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise ValueError("네트워크 드라이브는 사용할 수 없습니다.")
    resolved = path.resolve()
    if str(resolved).startswith(("\\\\", "//")):
        raise ValueError("네트워크 공유로 연결된 경로는 사용할 수 없습니다.")
    if os.name == "nt":
        if ctypes.windll.kernel32.GetDriveTypeW(str(resolved.anchor)) == 4:
            raise ValueError("네트워크 드라이브로 연결된 경로는 사용할 수 없습니다.")
    return resolved


def atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    path = local_path(path)
    fd, temporary = tempfile.mkstemp(prefix=".ureonggaksi-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp_path = Path(temporary)
    try:
        write(temp_path)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
