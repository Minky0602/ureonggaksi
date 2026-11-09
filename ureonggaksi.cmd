@echo off
pushd "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run uv sync --locked --extra dev in this folder first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m ureonggaksi
popd
