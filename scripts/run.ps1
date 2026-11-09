$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '먼저 프로젝트 폴더에서 uv sync --extra dev 를 실행하세요.'
}
Push-Location $projectRoot
try { & $pythonPath -m ureonggaksi; exit $LASTEXITCODE }
finally { Pop-Location }
