$ErrorActionPreference = 'Stop'
# 빌드 도구 스크립트가 한글 경로를 출력해도 영문 Windows 콘솔(cp1252)에서 멈추지 않게 한다. EXE 스모크 시험은 이 변수를 뺀다
$env:PYTHONUTF8 = '1'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
Push-Location $projectRoot
try {
    & $pythonPath -m PyInstaller --noconfirm --clean --onedir --windowed --name Ureonggaksi --paths src --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module tkinter --collect-data kiwipiepy --collect-data kiwipiepy_model --hidden-import kiwipiepy_model scripts/desktop_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'executable build failed' }
    Copy-Item -LiteralPath LICENSE -Destination dist/Ureonggaksi/LICENSE.txt
    Copy-Item -LiteralPath README.md -Destination dist/Ureonggaksi/README.md
    Copy-Item -LiteralPath SECURITY.md -Destination dist/Ureonggaksi/SECURITY.md
    New-Item -ItemType Directory -Path dist/Ureonggaksi/examples -Force | Out-Null
    Copy-Item -LiteralPath examples/workflow.json -Destination dist/Ureonggaksi/examples/workflow.json
    & $pythonPath examples/generate_samples.py
    if ($LASTEXITCODE -ne 0) { throw 'sample generation failed' }
    Get-ChildItem -LiteralPath examples/generated -Filter '*.hwpx' | Copy-Item -Destination dist/Ureonggaksi/examples
    # 3분 체험용 합성 자료(입력만): 스모크 시험용과 배포 폴더용을 각각 새로 만든다
    & $pythonPath examples/make_demo.py
    if ($LASTEXITCODE -ne 0) { throw 'demo generation failed' }
    & $pythonPath examples/make_demo.py dist/Ureonggaksi/examples/demo
    if ($LASTEXITCODE -ne 0) { throw 'demo generation failed' }
    # 메모장이 어느 Windows에서도 한글을 바로 읽게 BOM 있는 UTF-8로 쓴다(Windows PowerShell 5.1의 utf8)
    Get-Content -LiteralPath packaging/START_HERE.txt -Encoding UTF8 | Set-Content -LiteralPath (Join-Path dist/Ureonggaksi '처음 실행.txt') -Encoding UTF8
    Copy-Item -LiteralPath THIRD_PARTY_NOTICES.md -Destination dist/Ureonggaksi/THIRD_PARTY_NOTICES.md
    & $pythonPath scripts/collect_licenses.py dist/Ureonggaksi/licenses
    if ($LASTEXITCODE -ne 0) { throw 'license collection failed' }
    & $pythonPath scripts/smoke_exe.py dist/Ureonggaksi/Ureonggaksi.exe
    if ($LASTEXITCODE -ne 0) { throw 'executable smoke test failed' }
    Write-Output 'dist/Ureonggaksi/Ureonggaksi.exe (copy the whole Ureonggaksi folder when distributing)'
}
finally { Pop-Location }
