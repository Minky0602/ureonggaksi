# 우렁각시 · ureonggaksi

**HWPX 문서의 데이터를 외부 서버 전송 없이 Excel로 취합하는 오프라인 Windows 앱.** 한 번 정의한 추출 규칙(워크플로)을 JSON으로 저장해 재사용하고, 취합한 값의 원본 파일·XML 위치·문자 범위·SHA-256을 확인합니다.

Python + PySide6 네이티브 앱으로 구현했습니다. 웹서버·로그인·API 키·한글/Excel 설치가 필요하지 않습니다.

## 바로 실행

저장소를 받은 뒤 [uv](https://docs.astral.sh/uv/)를 설치하고 의존성을 한 번 설치합니다. Python 3.12는 `.python-version`에 따라 uv가 맞춥니다.

```powershell
uv sync --locked --extra dev
.\.venv\Scripts\python.exe -m ureonggaksi
```

설치가 끝난 뒤에는 **`ureonggaksi.cmd`를 두 번 클릭**해도 실행됩니다.

개발 의존성 최초 설치에는 인터넷이 필요합니다. 설치 후 앱 실행·추출·검증·저장에는 인터넷이 필요하지 않습니다. 빌드된 `dist/Ureonggaksi` 폴더 전체를 복사하면 Python 없는 PC에서도 실행할 수 있습니다.

## 사용 순서

1. **HWPX 파일 추가**: 최대 500개. 원문 확인 탭에서 구역·표·행·열과 텍스트를 봅니다.
2. **추출 규칙 정의**: 원문 셀 선택→‘선택한 셀을 추출 항목으로’, 또는 추출 규칙 탭→항목 추가.
3. **문서 일괄 취합**: 문서당 한 행, 규칙당 한 열로 취합합니다. 누락/중복/오류는 별도로 표시합니다.
4. **원문 확인**: 결과 값을 클릭하면 추출 당시 원문과 문자 범위가 표시됩니다. ‘원본 다시 검증’은 현재 파일의 해시·원문 위치·값을 다시 비교합니다.
5. **Excel로 저장**: 취합/출처/실행정보 세 시트를 저장합니다. 결과 셀을 클릭하면 해당 출처 행으로 이동합니다.
6. **워크플로 저장**: 규칙을 JSON으로 저장하고 같은 양식의 다음 문서에 다시 사용합니다.

| 추출 방식 | 적합한 양식 | 예 |
|---|---|---|
| 지정 셀 | 동일한 표 구조 | 구역 0, 표 1, 2행 2열 |
| 항목명 옆/아래 | 행 순서가 달라지는 표 | ‘성명’ 오른쪽 셀 |
| 정규식 | 본문 또는 셀의 일정한 문구 | `접수번호:\s*(\S+)` 그룹 1 |

행·열·표 번호는 1부터 시작합니다. 표 번호는 구역별로 매기며 중첩 표도 별도 번호입니다. 항목명은 공백과 끝의 콜론을 빼고 대소문자를 구분하지 않고 비교해서 같아야 합니다. 정규식은 개별 본문 문단/셀 안에 적용합니다. 일치 후보가 둘 이상이면 임의 선택하지 않습니다. 그룹 0은 전체 일치입니다. 파일이나 규칙을 변경하면 이전 결과는 지워집니다.

## 예제와 명령행 실행

```powershell
.\.venv\Scripts\python.exe examples/generate_samples.py
.\.venv\Scripts\python.exe -m ureonggaksi extract --workflow examples/workflow.json --output examples/generated/result.xlsx examples/generated/신청서_홍길동.hwpx examples/generated/신청서_김영희.hwpx examples/generated/신청서_박민수.hwpx
```

합성 예제는 파싱과 추출 체험용 최소 구조입니다. 한글에서 편집/인쇄 가능한 완전한 문서 양식이 아닙니다. GUI에서는 생성된 파일 세 개와 `examples/workflow.json`을 불러오면 됩니다.

CLI 종료 코드: `0` 정상, `1` 일부 필수 값 누락·중복·문서 오류(성공한 결과도 저장), `2` 설정·저장 실패. 기존 파일 덮어쓰기는 `--overwrite`가 필요합니다. 정상 문서의 오류/누락이 다른 문서 처리를 중단하지 않습니다.

## 보안 범위

네트워크 기능·외부 실행을 사용하지 않으며 Python 인터넷 소켓/DNS를 실행 시 차단합니다. ZIP/XML 크기·구조 제한, DTD/엔티티 방어, JSON 검증, 정규식 시간 제한, 네트워크 공유 경로 거부(Windows 기준, Linux의 네트워크 마운트는 구분하지 못함), Excel 수식 방지를 구현했습니다. 문서 내용을 자동 로그/설정에 저장하지 않습니다.

**OS 수준 강제 차단은 망분리/방화벽 정책으로 함께 보장해야 합니다.** 앱이 다른 프로그램의 동기화까지 막지는 못합니다. 실제 민감 문서와 결과는 **OneDrive 등 동기화되지 않는 로컬 폴더**에서 처리하세요. 결과 xlsx에는 원문 블록·원본 경로가 포함되므로 원본과 같은 보안 등급으로 관리해야 합니다. 자세한 데이터 흐름과 제한은 [SECURITY.md](SECURITY.md)를 보세요.

## 검증과 배포

```powershell
./scripts/test.ps1     # pytest, ruff check, ruff format --check
& ./scripts/build.ps1
```

Linux(Ubuntu 24.04에서 확인)에서는 같은 시험을 `scripts/test.sh`로 돌립니다. 화면 시험은 창 없이(offscreen) 돌지만 Qt가 쓰는 시스템 라이브러리가 필요합니다.

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends libgl1 libegl1 libxkbcommon0 libglib2.0-0t64 libdbus-1-3 libfontconfig1
uv sync --locked --extra dev
./scripts/test.sh
```

GitHub Actions가 `main`에 push할 때와 PR마다 Windows와 Ubuntu에서 같은 스크립트를 실행합니다([ci.yml](.github/workflows/ci.yml)).

`dist/Ureonggaksi/Ureonggaksi.exe`를 실행합니다. 배포 시 `_internal`, 라이선스 등을 포함한 **Ureonggaksi 폴더 전체**가 필요합니다. 빌드 산출물은 Git에 넣지 않습니다. 원본 코드와 설치 버전은 `uv.lock`으로 재현합니다.

## 현재 지원 범위

- HWPX 텍스트·표·병합 셀·중첩 표·여러 구역. 원문은 읽기 전용입니다.
- 문서당 한 행의 추출, JSON 규칙 재사용, xlsx 출처/실행정보.
- 해시·XML 구조 경로·문자 범위로 역추적합니다. 해시는 변경 감지이며 전자서명은 아닙니다.
- 원문 뷰는 텍스트·표 구조입니다. 원본 인쇄 레이아웃/페이지 번호, 바이너리 HWP, 암호 문서, OCR, 반복 명단 표의 여러 행 추출은 지원하지 않습니다.
- 저장소의 시험은 합성 자료만 씁니다. 실제 한글이 저장한 공개 예제 48개를 저장소 밖에서 읽어 본 결과 표 구조는 맞았지만, **변경 추적으로 지운 글자를 값으로 읽고 일부 특수 공백을 빠뜨리는 결함**이 있습니다(수정 예정). 실제 업무 양식은 로컬에서 추가 검증해야 합니다.

## 문서

| 문서 | 내용 |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 개발 환경, 시험, 브랜치와 PR 규칙, 배포 빌드 |
| [docs/requirements.md](docs/requirements.md) | 요구사항과 수용 기준. 구현 완료 목록이 아니라 목표다 |
| [docs/architecture.md](docs/architecture.md) | 구조, 데이터 모델, 처리 흐름. 현재와 목표를 구분한다 |
| [docs/api.md](docs/api.md) | 서비스 함수 계약 |
| [SECURITY.md](SECURITY.md) | 보안 범위와 한계 |

Apache License 2.0. 의존성 라이선스는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 참고하세요.
