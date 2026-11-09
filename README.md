# 우렁각시 · ureonggaksi

[![CI](https://github.com/Minky0602/ureonggaksi/actions/workflows/ci.yml/badge.svg)](https://github.com/Minky0602/ureonggaksi/actions/workflows/ci.yml)

HWPX 문서의 값을 외부 전송 없이 Excel로 취합하는 오프라인 Windows 앱이다. 추출 규칙을 JSON으로 저장해 재사용하고, 취합한 값마다 원본 파일, XML 위치, 문자 범위, SHA-256을 남긴다.

웹서버, 로그인, API 키, 한글·Excel 설치가 필요 없다.

> **상태: 프로토타입.** 검수 전 코드이므로 실제 업무 문서에 쓰기 전에 로컬에서 검증해야 한다. 기존 XLSX에 값을 채우는 기능과 로컬 AI 규칙 후보 생성은 아직 구현되지 않았다. 목표 기능은 [요구사항](docs/requirements.md)에 있다.

## 설치와 실행

[uv](https://docs.astral.sh/uv/)를 설치한 뒤 실행한다. Python 3.12는 uv가 맞춘다.

```powershell
git clone https://github.com/Minky0602/ureonggaksi.git
cd ureonggaksi
uv sync --locked
uv run ureonggaksi
```

설치 뒤에는 `ureonggaksi.cmd`를 두 번 클릭해도 실행된다. 인터넷은 처음 설치할 때만 필요하다.

## 사용 순서

1. **HWPX 파일 추가:** 최대 500개. 원문 확인 탭에서 구역, 표, 행, 열과 텍스트를 본다.
2. **추출 규칙 정의:** 원문 셀을 선택해 추출 항목으로 만들거나 추출 규칙 탭에서 항목을 추가한다.
3. **일괄 취합:** 문서당 한 행, 규칙당 한 열로 취합한다. 누락, 중복, 오류는 따로 표시한다.
4. **원문 확인:** 결과 값을 클릭하면 추출 당시 원문과 문자 범위가 나온다. `원본 다시 검증`은 현재 파일의 해시, 위치, 값을 다시 비교한다.
5. **Excel 저장:** `취합`, `출처`, `실행정보` 세 시트를 저장한다.
6. **워크플로 저장:** 규칙을 JSON으로 저장해 같은 양식의 다음 문서에 다시 쓴다.

| 추출 방식 | 적합한 양식 | 예 |
|---|---|---|
| 지정 셀 | 동일한 표 구조 | 구역 0, 표 1, 2행 2열 |
| 항목명 옆/아래 | 행 순서가 달라지는 표 | `성명` 오른쪽 셀 |
| 정규식 | 본문이나 셀의 일정한 문구 | `접수번호:\s*(\S+)` 그룹 1 |

- 행, 열, 표 번호는 1부터 시작한다. 표 번호는 구역별로 매기며 중첩 표도 별도 번호다.
- 항목명은 공백과 끝의 콜론을 빼고 대소문자를 구분하지 않고 비교한다.
- 정규식은 문단이나 셀 하나 안에서 적용한다. 그룹 0은 전체 일치다.
- 일치 후보가 둘 이상이면 임의로 고르지 않는다.
- 파일이나 규칙을 바꾸면 이전 결과는 지워진다.

## 명령행 실행

합성 예제를 만들어 추출한다. 예제는 파싱 체험용 최소 구조이며 한글에서 편집할 수 있는 완전한 양식이 아니다.

```powershell
uv run python examples/generate_samples.py
uv run ureonggaksi extract --workflow examples/workflow.json --output examples/generated/result.xlsx `
  examples/generated/신청서_홍길동.hwpx examples/generated/신청서_김영희.hwpx examples/generated/신청서_박민수.hwpx
```

| 종료 코드 | 의미 |
|---|---|
| `0` | 정상 |
| `1` | 일부 필수 값 누락, 중복, 문서 오류. 성공한 결과는 저장한다 |
| `2` | 설정 또는 저장 실패 |

기존 파일을 덮어쓰려면 `--overwrite`를 지정한다.

## 지원 범위와 알려진 한계

**지원:** HWPX 텍스트, 표, 병합 셀, 중첩 표, 여러 구역. 문서당 한 행 추출. 원문은 읽기만 한다.

**미지원:** 바이너리 HWP, 암호 문서, OCR, 원본 인쇄 레이아웃과 페이지 번호, 반복 명단 표의 여러 행 추출.

**알려진 결함:** 실제 한글이 저장한 문서에서 변경 추적으로 지운 글자를 값으로 읽고, 일부 특수 공백을 빠뜨린다.

SHA-256은 변경 감지용이며 전자서명이 아니다.

## 보안

앱은 Python의 인터넷 소켓과 DNS를 실행 시 차단하지만 OS 수준의 차단은 아니다. 강제 차단이 필요하면 망분리나 방화벽을 함께 적용한다.

- 민감 문서와 결과는 OneDrive 등 동기화되지 않는 로컬 폴더에서 처리한다.
- 결과 XLSX에는 원문 블록과 원본 경로가 들어가므로 원본과 같은 보안 등급으로 관리한다.

데이터 흐름과 입력 제한은 [SECURITY.md](SECURITY.md)에 있다.

## 문서

| 문서 | 내용 |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 개발 환경, 시험, PR 규칙, 배포 빌드 |
| [docs/requirements.md](docs/requirements.md) | 요구사항과 수용 기준. 구현 완료 목록이 아니라 목표다 |
| [docs/architecture.md](docs/architecture.md) | 구조, 데이터 모델, 처리 흐름. 현재와 목표를 구분한다 |
| [docs/api.md](docs/api.md) | 서비스 함수 계약 |
| [SECURITY.md](SECURITY.md) | 보안 범위와 한계 |

## 라이선스

[Apache License 2.0](LICENSE). 의존성 라이선스는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 있다.
