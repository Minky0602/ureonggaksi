$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & ./.venv/Scripts/python.exe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'tests failed' }
    & ./.venv/Scripts/ruff.exe check .
    if ($LASTEXITCODE -ne 0) { throw 'lint check failed' }
    & ./.venv/Scripts/ruff.exe format --check .
    if ($LASTEXITCODE -ne 0) { throw 'format check failed' }
}
finally { Pop-Location }
