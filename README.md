# 우렁각시 (ureonggaksi)

[![CI](https://github.com/Minky0602/ureonggaksi/actions/workflows/ci.yml/badge.svg)](https://github.com/Minky0602/ureonggaksi/actions/workflows/ci.yml)

한글(HWPX) 문서에 적힌 값을 기존 Excel 취합표의 빈칸에 채워 주는 Windows 앱이다. "이름 기준으로 이수시간 채워줘"처럼 한 줄로 요청하면 채울 칸을 미리 보여 주고, 저장하면 원본은 그대로 둔 채 새 XLSX를 만든다. 채운 값마다 어느 파일의 어느 위치에서 읽었는지 근거 파일에 남긴다.

인터넷 연결, 웹서버, 로그인, API 키가 필요 없고 한글이나 Excel을 설치하지 않아도 된다. 앱은 문서를 외부로 보내지 않는다.

오픈소스기초설계 2반 6팀의 2026년 2학기 프로젝트다.

| 팀원 | 역할 |
|---|---|
| 김성윤 | 팀장. 기획, 문서 관리, 발표 |
| 김효민 | 개발. 설계, 구현, 시험, 배포 |

> **상태: 동작하는 시험판.** 합성 자료로만 검증했다. 실제 업무 문서에 쓸 때는 저장 전에 결과를 직접 확인해야 한다. 목표 기능 전체는 [요구사항](docs/requirements.md)에 있다.

## 설치와 실행

### Windows 실행 파일

[Releases](https://github.com/Minky0602/ureonggaksi/releases)에서 `Ureonggaksi-<버전>-windows-x64.zip`을 받아 폴더째 풀고 `Ureonggaksi.exe`를 실행한다. 설치 과정이 없고 관리자 권한, Python, 인터넷도 필요 없다.

코드 서명이 없어서 처음 실행할 때 "Windows의 PC 보호" 창이 뜰 수 있다. 이때는 [추가 정보]를 누른 뒤 [실행]을 누른다. 폴더 안의 `처음 실행.txt`에 합성 예시로 3분 안에 해 볼 수 있는 순서가 있다. 함께 올린 `.sha256` 파일로 받은 zip이 CI에서 만든 것과 같은지 확인할 수 있다. 버전은 창 제목에 나온다.

### 소스에서 실행

[uv](https://docs.astral.sh/uv/)를 설치한 뒤 아래 명령을 실행한다. Python 3.12는 uv가 알아서 맞춘다.

```powershell
git clone https://github.com/Minky0602/ureonggaksi.git
cd ureonggaksi
uv sync --locked
uv run ureonggaksi
```

한 번 설치한 뒤에는 `ureonggaksi.cmd`를 두 번 클릭해도 실행된다. 인터넷은 처음 설치할 때만 쓴다.

## 사용 순서

1. [원본 추가]로 HWPX 문서를, [취합표 선택]으로 채울 XLSX를 고른다.
2. 요청을 한 줄로 쓰고 [미리보기]를 누른다. 예: `이름 기준으로 직급이랑 이수시간 채워줘`
3. 질문이 나오면 버튼으로 답한다. 행을 찾을 기준 열이 정해지지 않았거나, 금액 단위처럼 답 하나로 여러 칸이 정해지는 경우에 묻는다. 어떤 항목을 어느 열에 넣을지 앱이 정하지 못하면 요청에 열 이름을 넣어 다시 미리보기한다.
4. 미리보기를 확인한다.
   - 맨 위에 요청을 어떻게 이해했는지, 몇 칸을 채우는지, 예시 값이 나온다.
   - '확인 필요'에는 이름이 서로 다른 짝이나 모델이 고른 짝이 값 예시와 함께 나온다. 틀린 짝은 [빼기]를 누르면 그 열만 비워 둔다.
   - '직접 처리할 칸'에는 앱이 채우지 않은 칸과 그 이유가 나온다.
5. [저장]을 누르면 취합표가 있는 폴더에 새 XLSX와 근거 JSON을 만든다. 직접 처리할 칸이 있으면 `<결과 이름>_확인필요.txt`도 함께 만든다. [결과 폴더 열기]로 바로 열 수 있다.

### 로컬 AI 켜기 (선택)

문서의 '성명'을 취합표의 '신청인' 열에 넣는 것처럼 이름이 다른 짝을 찾거나, 말투가 섞인 요청을 읽을 때 로컬 모델을 쓴다. 모델이 없어도 열 이름과 같은 항목은 채운다.

- 실행 파일은 [llama.cpp](https://github.com/ggml-org/llama.cpp) 릴리스에 들어 있는 `llama-completion`을 쓴다. `llama-cli`로는 동작하지 않는다.
- 모델은 GGUF 파일을 쓴다. 시험한 모델은 Qwen3.5-4B Q4_K_M(Apache-2.0)이다.
- 화면의 [로컬 AI 지정]에서 두 파일을 고르거나, 환경 변수 `UREONGGAKSI_LLAMA`와 `UREONGGAKSI_MODEL`에 경로를 넣는다.
- 모델은 이 PC에서만 실행되고 네트워크를 쓰지 않는다. 실행에 실패하면 미리보기에 그 사실이 표시된다.
- 같은 질문을 순서를 바꿔 두 번 동시에 묻기 때문에 모델 프로세스가 두 개 뜨고 작업 메모리도 두 배로 든다. 시험한 PC(8스레드 CPU)에서는 모델을 쓰는 요청이 20~30초 걸렸다. 4코어 이하이거나 메모리가 8GB보다 작은 PC에서는 시험하지 않았다.
- 요청의 조사와 부정 표현은 형태소 분석기 Kiwi로 읽는다. 첫 요청 때 한 번 불러오는 데 약 2초가 걸리고, 앱이 켜져 있는 동안 메모리를 약 0.3GB 더 쓴다. Kiwi 모델 때문에 설치 폴더가 약 105MB 커진다.

## 시연 (합성 자료)

교육 이수확인서 3장과 그 값을 채울 취합표를 만든다. 실제 개인정보는 들어 있지 않다.

```powershell
uv run python examples/make_demo.py      # examples/generated/demo/ 에 만든다
uv run ureonggaksi
```

화면에서 `examples/generated/demo`의 HWPX 3개와 `교육이수현황.xlsx`를 고르고, 아래 요청으로 [미리보기]를 누른다.

> 발급번호 기준으로 신청인이랑 생일, 이수시간 채워줘

- 이수시간은 열 이름이 문서 항목과 같아서 바로 채운다.
- 문서의 '성명'은 신청인 열에, '생년월일'은 생일 열에 동의어표로 짝지어 넣는다. 이 둘은 '확인 필요'에 값 예시와 함께 보인다.
- A-003 행의 이수시간 칸에는 문서와 다른 값이 이미 있다. 이 칸은 덮어쓰지 않고 '직접 처리'로 남긴다.
- [저장]하면 원본은 그대로 두고 `교육이수현황_우렁각시_<시각>.xlsx`, 같은 이름의 `_확인필요.txt`, 근거 JSON을 만든다.

로컬 AI가 없어도 이 시연은 끝까지 진행된다.

## 명령행 실행

합성 예제를 만들어 정해 둔 규칙으로 값을 뽑는다. 이 예제 문서는 읽기 시험용 최소 구조라서 한글에서 편집할 수 있는 완전한 양식은 아니다.

```powershell
uv run python examples/generate_samples.py
uv run ureonggaksi extract --workflow examples/workflow.json --output examples/generated/result.xlsx `
  examples/generated/신청서_홍길동.hwpx examples/generated/신청서_김영희.hwpx examples/generated/신청서_박민수.hwpx
```

| 종료 코드 | 의미 |
|---|---|
| `0` | 정상 |
| `1` | 일부 필수 값이 없거나 중복되었거나 문서에 오류가 있음. 성공한 결과는 저장한다 |
| `2` | 설정 오류 또는 저장 실패 |

출력 파일이 이미 있으면 덮어쓰지 않고 오류로 끝낸다.

### 한 줄 요청으로 채우기

화면이 없어도 미리 본 내용만 저장되도록 미리보기(`prepare`)와 저장(`commit`)을 나눴다.

```powershell
uv run ureonggaksi prepare --request "이름 기준으로 이수시간 채워줘" --target 취합표.xlsx `
  --out 작업.plan.json 문서1.hwpx 문서2.hwpx
# 해석, 채울 칸, 예시와 계획 지문을 출력한다. 질문이 있으면 종료 코드 3으로 끝나고 --answer 쓰는 법을 알려 준다.
uv run ureonggaksi commit --plan 작업.plan.json --plan-hash <출력된 지문>
# 파일이나 계획이 prepare 때와 달라졌으면 저장하지 않는다.
```

| 종료 코드 | prepare | commit |
|---|---|---|
| `0` | 미리보기 | 모두 채움 |
| `1` | – | 일부만 채움. 직접 처리할 칸이나 읽지 못한 원본이 남음 |
| `2` | 오류 | 오류, 지문 불일치, 저장 실패. 결과 파일은 남기지 않는다 |
| `3` | 질문 있음. 답하는 법을 출력한다 | – |

시연 자료로도 같은 일을 할 수 있다.

```powershell
uv run ureonggaksi prepare --request "발급번호 기준으로 신청인이랑 생일, 이수시간 채워줘" `
  --target examples/generated/demo/교육이수현황.xlsx --out examples/generated/demo/작업.plan.json `
  examples/generated/demo/이수확인서_홍길동.hwpx examples/generated/demo/이수확인서_김영희.hwpx examples/generated/demo/이수확인서_박민수.hwpx
```

로컬 모델을 쓰려면 `prepare`에 `--runtime`(llama-completion 실행 파일)과 `--model`(GGUF 파일)을 준다. 주지 않으면 모델 없이 해석한다. 저장해 둔 작업 규칙은 `prepare --workflow`로 다시 쓸 수 있다. 화면에서 저장한 규칙을 다시 불러오는 기능은 아직 없다. 소스로 실행할 때는 `uv run ureonggaksi --version`으로 버전을 확인한다.

## Docker로 재현 (Linux)

Linux 컨테이너에서 네트워크를 끊은 채로 같은 시험과 명령행 시연을 돌릴 수 있다. 이미지에는 로컬 AI 모델을 넣지 않았으므로 모델 없이 해석하는 경로를 시험한다. 화면(PySide6)은 offscreen 모드로만 시험한다.

```sh
docker build -t ureonggaksi .
docker run --rm --network none ureonggaksi                  # 시험과 린트 (scripts/test.sh)
docker run --rm --network none ureonggaksi scripts/demo.sh  # 합성 시연: prepare, commit, 원본 해시 확인
```

CI는 PR마다 이 두 명령을 실행한다.

## 지원 범위와 알려진 한계

**지원하는 것.** HWPX의 본문, 표, 병합 셀, 중첩 표, 여러 구역을 읽는다. 문서 한 장이 취합표 한 행이 되는 서식과, 명단 표의 각 줄이 한 행이 되는 서식을 지원한다. 이미 있는 XLSX 취합표의 빈칸만 채우고, 다른 값이 들어 있는 칸은 덮어쓰지 않는다. 원본 문서와 원본 취합표는 읽기만 한다.

**지원하지 않는 것.** 바이너리 HWP, 암호가 걸린 문서, 스캔 이미지(OCR), 원본의 인쇄 레이아웃과 페이지 번호는 다루지 않는다. 수식이 있거나 병합되었거나 보호된 칸에는 쓰지 않고 '직접 처리'로 남긴다. 변경 추적이 남아 있는 칸도 값을 정하지 않고 '직접 처리'로 남긴다.

**한 줄 요청 해석.** 요청에 나온 항목을 취합표 열과 짝지어 채운다. 이름이 같으면 바로 채운다. 이름이 다르면 흔한 행정 항목 동의어표에서 먼저 찾고, 표에 없거나 후보가 여럿이면 로컬 모델이 고른다. 이렇게 지은 짝과 말투가 섞인 요청은 '확인 필요'에 값 예시와 함께 보여 준다. 행을 찾을 기준 열을 요청에 쓰지 않으면 앱이 묻는다.

모델은 "현행 유지"처럼 빼라는 말을 채우라는 뜻으로 잘못 읽거나 동의어를 잘못 고를 수 있다. 저장하기 전에 '확인 필요' 항목의 값 예시를 꼭 확인한다.

**단위.** 열 머리글 끝 괄호에 단위가 있으면(예: `피해추산액(천원)`, `면적(㎡)`) 그 단위에 맞춘 숫자로 바꿔 쓴다. 나누어떨어지지 않거나 단위가 적혀 있지 않은 금액은 비워 두고 이유를 남긴다.

근거 파일의 SHA-256은 파일이 바뀌었는지 확인하는 용도이고 전자서명이 아니다.

## 보안

앱은 실행 중에 Python의 인터넷 연결과 DNS 조회를 막는다. 다만 운영체제 수준의 차단은 아니므로, 반드시 막아야 하는 환경이라면 망분리나 방화벽을 함께 쓴다.

- 민감한 문서와 결과는 OneDrive처럼 동기화되는 폴더가 아닌 로컬 폴더에서 처리한다.
- 결과 XLSX는 앱이 채운 칸만 원본과 다르다. 근거 JSON에는 원문 값과 원본 파일의 전체 경로(사용자 폴더 이름 포함)가 들어가고, `_확인필요.txt`에는 원문 값과 원본 파일 이름이 들어간다.
- `extract` 명령으로 만든 XLSX에는 `출처` 시트가 붙고, 여기에 원본 경로, 해시, 원문이 들어간다.
- 이 파일들은 모두 원본과 같은 보안 등급으로 관리한다.

데이터 흐름과 입력 제한은 [SECURITY.md](SECURITY.md)에 정리했다.

## 문서

| 문서 | 내용 |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 개발 환경, 시험, PR 규칙, 배포 빌드 |
| [docs/requirements.md](docs/requirements.md) | 요구사항과 수용 기준. 구현이 끝난 목록이 아니라 목표다 |
| [docs/architecture.md](docs/architecture.md) | 구조, 데이터 모델, 처리 흐름. 현재와 목표를 나눠 적었다 |
| [docs/api.md](docs/api.md) | 서비스 함수 계약 |
| [SECURITY.md](SECURITY.md) | 보안 범위와 한계 |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | 행동 강령과 신고 방법 |
| [docs/development-notes.md](docs/development-notes.md) | 실제로 해 본 것, 어려웠던 점, 측정 수치, 결정, 교훈 |
| [docs/research/](docs/research/) | 오픈소스와 모델 조사, 선택 이유, 평가 방법과 날짜별 측정 결과 |
| [docs/records/](docs/records/) | 구현하면서 겪은 문제와 검증 기록 (날짜별) |
| [docs/planning/](docs/planning/) | 개발 계획과 실제 PR 진행, 과목 요구사항 대응표, ERD |

## 라이선스

[Apache License 2.0](LICENSE). 의존하는 라이브러리의 라이선스는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 있다. [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)는 원문을 따라 CC BY-SA 4.0이다.
