$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & ./.venv/Scripts/python.exe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw '테스트 실패' }
    & ./.venv/Scripts/ruff.exe check .
    if ($LASTEXITCODE -ne 0) { throw '정적 검사 실패' }
    & ./.venv/Scripts/ruff.exe format --check .
    if ($LASTEXITCODE -ne 0) { throw '포맷 검사 실패' }
}
finally { Pop-Location }
