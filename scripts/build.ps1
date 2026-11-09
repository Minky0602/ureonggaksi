$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
Push-Location $projectRoot
try {
    & $pythonPath -m PyInstaller --noconfirm --clean --onedir --windowed --name Ureonggaksi --paths src --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module tkinter scripts/desktop_entry.py
    if ($LASTEXITCODE -ne 0) { throw '실행 파일 빌드 실패' }
    Copy-Item -LiteralPath LICENSE -Destination dist/Ureonggaksi/LICENSE.txt
    Copy-Item -LiteralPath README.md -Destination dist/Ureonggaksi/README.md
    Copy-Item -LiteralPath docs/SECURITY.md -Destination dist/Ureonggaksi/SECURITY.md
    New-Item -ItemType Directory -Path dist/Ureonggaksi/examples -Force | Out-Null
    Copy-Item -LiteralPath examples/workflow.json -Destination dist/Ureonggaksi/examples/workflow.json
    & $pythonPath examples/generate_samples.py
    if ($LASTEXITCODE -ne 0) { throw '합성 예제 생성 실패' }
    Get-ChildItem -LiteralPath examples/generated -Filter '*.hwpx' | Copy-Item -Destination dist/Ureonggaksi/examples
    Copy-Item -LiteralPath docs/THIRD_PARTY_NOTICES.md -Destination dist/Ureonggaksi/THIRD_PARTY_NOTICES.md
    & $pythonPath scripts/collect_licenses.py dist/Ureonggaksi/licenses
    if ($LASTEXITCODE -ne 0) { throw '의존성 라이선스 수집 실패' }
    Write-Output 'dist/Ureonggaksi/Ureonggaksi.exe (배포 시 Ureonggaksi 폴더 전체를 복사하세요.)'
}
finally { Pop-Location }
