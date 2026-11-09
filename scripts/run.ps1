$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run uv sync --locked --extra dev in the project folder first.'
}
Push-Location $projectRoot
try { & $pythonPath -m ureonggaksi; exit $LASTEXITCODE }
finally { Pop-Location }
