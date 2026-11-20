# 우렁각시 · ureonggaksi

[![CI](https://github.com/Minky0602/ureonggaksi/actions/workflows/ci.yml/badge.svg)](https://github.com/Minky0602/ureonggaksi/actions/workflows/ci.yml)

HWPX 문서의 값을 외부 전송 없이 Excel로 취합하는 오프라인 Windows 앱이다. 추출 규칙을 JSON으로 저장해 재사용하고, 취합한 값마다 원본 파일, XML 위치, 문자 범위, SHA-256을 남긴다.

웹서버, 로그인, API 키, 한글·Excel 설치가 필요 없다.

> **상태: 프로토타입.** 합성 자료로만 검증했다. 실제 업무 문서에 쓰기 전에 결과를 직접 확인해야 한다. 목표 기능은 [요구사항](docs/requirements.md)에 있다.

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

1. **파일 고르기:** [원본 추가]로 HWPX를, [취합표 선택]으로 채울 XLSX를 고른다.
2. **한 줄 요청:** 예) `이름 기준으로 직급이랑 이수시간 채워줘`. [미리보기]를 누른다.
3. **질문이 나오면 버튼으로 답한다.** 행을 찾을 열(키)·작업 종류를 정하지 못했거나, 금액 단위·반올림처럼 답 하나로 정해지는 문제가 많을 때 묻는다. 어느 항목을 어느 열에 넣을지 정하지 못하면 요청에 열 이름을 넣어 다시 미리보기한다.
4. **미리보기 확인:**
   - 해석 한 줄, 채울 칸 수, 예시가 나온다.
   - '확인 필요'에는 이름이 다르거나 모델이 읽은 짝이 열마다 한 줄씩 값 예시와 함께 나온다. 틀리면 [빼기]를 누르면 그 열만 비워 둔다.
   - '직접 처리할 칸'은 자동으로 채우지 않은 칸과 이유다.
5. **[저장]:** 원본은 그대로 두고 취합표 폴더에 새 XLSX, `확인필요.txt`, 근거 JSON을 만든다. [결과 폴더 열기]로 연다.

### 로컬 AI 켜기(선택)

이름이 다른 짝(문서 '성명' → 열 '신청인')을 짝짓거나 말투가 섞인 요청을 읽을 때 로컬 모델을 쓴다. 없어도 열 이름과 같은 항목은 채운다.

- 실행 파일은 [llama.cpp](https://github.com/ggml-org/llama.cpp) 릴리스의 `llama-completion`이다. `llama-cli`는 안 된다.
- 모델은 GGUF 파일이다. 시험한 모델은 Qwen3.5-4B Q4_K_M(Apache-2.0)이다.
- 화면의 [로컬 AI 지정]으로 두 파일을 고르거나, 환경 변수 `UREONGGAKSI_LLAMA`·`UREONGGAKSI_MODEL`에 경로를 넣는다.
- 모델은 질문마다 이 PC에서 실행되고 네트워크를 쓰지 않는다. 모델 실행이 실패하면 미리보기에 그 사실이 나온다.

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

출력 파일이 이미 있으면 덮어쓰지 않고 오류로 끝낸다.

### 한 줄 요청으로 채우기(두 단계)

화면 없이도 "본 것만 저장"을 지키도록 미리보기와 저장을 나눈다.

```powershell
uv run ureonggaksi prepare --request "이름 기준으로 이수시간 채워줘" --target 취합표.xlsx `
  --out 작업.plan.json 문서1.hwpx 문서2.hwpx
# 해석·채울 칸·예시와 계획 지문을 출력한다. 질문이 있으면 종료 코드 3과 --answer 쓰는 법을 출력한다.
uv run ureonggaksi commit --plan 작업.plan.json --plan-hash <출력된 지문>
# 파일이나 계획이 prepare 때와 달라졌으면 저장하지 않는다.
```

로컬 모델을 쓰려면 prepare에 `--runtime`(llama-completion 실행 파일)과 `--model`(GGUF)을 준다. 없으면 모델 없이 해석한다.

## 지원 범위와 알려진 한계

**지원:** HWPX 텍스트, 표, 병합 셀, 중첩 표, 여러 구역. 문서당 한 행 추출. 원문은 읽기만 한다.

**미지원:** 바이너리 HWP, 암호 문서, OCR, 원본 인쇄 레이아웃과 페이지 번호, 반복 명단 표의 여러 행 추출.

**알려진 결함:** 실제 한글이 저장한 문서에서 변경 추적으로 지운 글자를 값으로 읽고, 일부 특수 공백을 빠뜨린다.

**한 줄 요청 해석:** 요청에 나온 항목을 취합표 열과 짝지어 채운다. 이름이 같은 짝은 바로 채운다. 이름이 다른 짝(문서 '성명' → 열 '신청인')과 말투가 섞인 요청은 로컬 모델이 읽어 채우고, 미리보기에 '확인 필요'로 값 예시와 함께 보인다. 틀린 짝은 미리보기에서 빼면 그 열만 비워 둔다. 행을 찾을 키 열을 말하지 않으면 묻는다. 모델은 빼라는 말("현행 유지")을 채우라는 뜻으로 잘못 읽거나 동의어를 잘못 고를 수 있으니, 저장 전에 '확인 필요' 항목의 값 예시를 확인한다.

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
