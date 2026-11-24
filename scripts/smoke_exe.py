"""빌드한 EXE로 합성 시연 요청을 prepare까지 돌려 본다(쓰기 없음).

빌드 때 빠진 모듈·데이터(예: 형태소 분석기 모델)는 실행해야 드러난다. 창 모드 EXE는 오류가 나면 대화상자를
띄우고 멈추므로 시간 제한으로 실패를 잡는다.
usage: python scripts/smoke_exe.py dist/Ureonggaksi/Ureonggaksi.exe
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent / "examples" / "generated" / "demo"
REQUEST = "발급번호 기준으로 신청인이랑 생일, 이수시간 채워줘"


def _without_python() -> dict[str, str]:
    """Python·uv·가상환경이 없는 PC처럼 보이는 환경. EXE가 몰래 시스템 Python에 기대면 여기서 드러난다."""
    hidden = ("PYTHON", "VIRTUAL_ENV", "UV_")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(hidden)}
    words = ("python", "/uv", "/.venv", "pipx")
    keep = [
        p
        for p in env.get("PATH", "").split(os.pathsep)
        if p and not any(w in p.lower().replace("\\", "/") for w in words)
    ]
    env["PATH"] = os.pathsep.join(keep)
    return env


def main(exe: str) -> int:
    exe = str(Path(exe).resolve())  # 작업 폴더가 바뀌어도 찾게
    with tempfile.TemporaryDirectory(prefix="ureonggaksi-smoke-") as folder:
        work = Path(folder)
        target = shutil.copy(DEMO / "교육이수현황.xlsx", work)
        docs = [shutil.copy(p, work) for p in sorted(DEMO.glob("이수확인서_*.hwpx"))]
        plan = work / "작업.plan.json"
        command = [exe, "prepare", "--request", REQUEST, "--target", target, "--out", plan]
        command += ["--rules-dir", work / "rules", *docs]
        try:
            done = subprocess.run(command, capture_output=True, timeout=120, env=_without_python())
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/F", "/IM", Path(exe).name], capture_output=True)
            print("smoke: EXE가 120초 안에 끝나지 않았습니다(오류 대화상자일 수 있음)")
            return 1
        if done.returncode != 0 or not plan.is_file():
            print(f"smoke: prepare 실패(종료 코드 {done.returncode})")
            print(done.stdout.decode("utf-8", "replace")[-2000:])
            return 1
    print("smoke: prepare 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
