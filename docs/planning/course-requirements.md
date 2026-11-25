# 과목 요구사항 대응표

기준일: 2026-11-25 (main `9a5a59a`, Release v0.1.2)

오픈소스기초설계 팀프로젝트 안내 자료의 요구사항과 평가 기준을 이 저장소의 근거에 대응시킨다. 상태는 `충족`, `부분`, `없음`으로 적는다.

## 1. 최소 요구사항

| 요구사항 | 이 저장소의 근거 | 상태 |
|---|---|---|
| 문제 정의와 사용자 시나리오 | [requirements.md](../requirements.md) 2절(사용자와 해결할 문제), 2.1절(대표 시나리오) | 충족 |
| 관련 오픈소스 1개 이상 조사 | [research/open-source-and-models.md](../research/open-source-and-models.md)(후보 비교와 채택, 제외 이유), [development-notes.md 6절](../development-notes.md#6-방향을-바꾼-결정)(채택, 버린 대안: llama.cpp vs Ollama, llama-cpp-python, 4B vs 9B, Kiwi vs 정규식, openpyxl 다시 저장 vs 최소 변경 쓰기), [CONTRIBUTING.md](../../CONTRIBUTING.md) '참고한 1차 자료' | 충족 |
| GitHub/GitLab 저장소 구조 분석 | llama.cpp 릴리스 구성 확인('Latest'에 바이너리 없음, `llama-cli`가 루프백 서버를 띄움), 공개 hwpxlib 표본 49개로 HWPX 구조 시험([development-notes.md 2.4, 4.1, 4.6절](../development-notes.md#2-실제로-실행한-것)) | 부분. 대상 오픈소스 저장소의 디렉터리, 모듈 구조를 정리한 문서는 없다 |
| Linux 환경에서 설치 또는 실행 | CI `ubuntu-24.04` 잡([ci.yml](../../.github/workflows/ci.yml)), [scripts/test.sh](../../scripts/test.sh), [Dockerfile](../../Dockerfile), README 'Docker로 재현(Linux)' | 충족 |
| 원본 기능 또는 예제 재현 | llama.cpp `b12125` `llama-completion` + Qwen3.5-4B GGUF를 실제로 실행해 지연, 메모리, 소켓 0건 측정, Kiwi 오프라인 적재([development-notes.md 2.4절](../development-notes.md#24-로컬-ai-실행)) | 충족 |
| 팀 고유의 수정, 추가, 통합 | 오픈소스를 엮은 앱 전체: 실행기 호출 방식(채팅 프롬프트 직접 조립, JSON 스키마 enum 제약), 모델 몫 축소, 행정 항목 동의어표, 최소 변경 XLSX 쓰기, 프로세스 격리([architecture.md 3.1절](../architecture.md#31-모듈)). 오픈소스 자체를 고치지는 않았다 | 충족 |
| Git 커밋 기반 협업 기록 | PR 42개 병합, 이슈 2개, PR 템플릿, main 보호와 필수 CI 검사([pr-history.md](pr-history.md)) | 부분. 커밋 작성자가 1명이다. 팀장(기획, 문서, 발표)의 기여가 커밋, 이슈, PR로 남아 있지 않다 |
| 실행 결과와 검증 지표 | 지표 S1, S2, C, D 정의와 측정 표([development-notes.md 2.5, 3절](../development-notes.md#25-평가-방법)), 날짜별 흐름([pr-history.md 5절](pr-history.md#5-검증-지표의-흐름)), 시험 979 passed, 1 skipped, CI 시연(`scripts/demo.sh`, 원본 해시 확인) | 부분. 평가셋과 채점 스크립트는 저장소에 없고, Q01 이슈 #13이 열려 있다 |
| 라이선스 및 출처 명시 | [LICENSE](../../LICENSE)(Apache-2.0), [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)(의존성, llama.cpp, Qwen3.5-4B, Kiwi), Release zip에 라이선스 원문 수집([scripts/collect_licenses.py](../../scripts/collect_licenses.py)) | 충족 |
| README와 실행 방법 작성 | [README.md](../../README.md): Windows 실행 파일, 소스 실행, 명령행, Docker, 시연 | 충족 |

## 2. 권장 확장

| 항목 | 근거 | 상태 |
|---|---|---|
| Docker 실행환경 | [Dockerfile](../../Dockerfile): 베이스 이미지 digest 고정, 비루트 사용자, `--network none`으로 시험, 시연. CI `docker` 잡 | 충족 |
| Web/DB 서버, API 연동 | 의도적으로 넣지 않았다. 문서가 PC 밖으로 나가지 않는 것이 요구사항이라 서버를 붙이면 그 보장이 깨진다([architecture.md 1절](../architecture.md#1-한눈에)). API는 로컬 서비스 함수 계약([api.md](../api.md)) | 제외(이유 기록) |
| 공개 데이터 | 업무 문서 대신 합성 자료만 쓴다([examples/](../../examples/)). HWPX 읽기는 공개 hwpxlib 표본으로 시험했다 | 부분 |
| 테스트 코드 | [tests/](../../tests/) 23개 파일, 979 passed, 1 skipped. Windows, Ubuntu CI | 충족 |
| 이슈 관리 | 이슈 #12(P06, 닫힘), #13(Q01, 열림) | 부분. 이슈가 2개뿐이고 계획은 대부분 PR로 관리했다 |
| 배포 URL | GitHub Release v0.1.2: Windows x64 zip과 SHA-256, 태그로 빌드, 스모크 시험([release.yml](../../.github/workflows/release.yml)) | 충족 |

## 3. 오픈소스 활용 수준

**주장: Level 4(수정, 개선).** Level 3 이하는 모두 충족한다.

| 수준 | 근거 |
|---|---|
| Level 2 설치, 실행, 재현 | llama.cpp 공식 릴리스 `b12125`를 태그, SHA-256으로 고정해 실행. README대로 새 체크아웃에서 `uv sync --locked`, CI 3잡, Docker 재현 |
| Level 3 복수 OSS 통합 | llama.cpp + Qwen3.5-4B(요청 해석 보조), Kiwi(조사, 부정 읽기), openpyxl(취합표 읽기), PySide6(화면), defusedxml(XML 방어)을 한 흐름(한 줄 요청 → 미리보기 → 저장)으로 연결 |
| Level 4 수정, 개선 | 팀이 만든 흐름을 수치로 개선: held-out s01b 규칙 완전 일치 32% → 지표를 다시 정의한 뒤 S1(표시 없이 틀린 칸) 0 유지, 미리보기 지연 32~42초 → 21.6~29.2초(동시 실행), XLSX 로드를 60초, 2GiB 상한으로 격리, 배포(Release)까지 개선([pr-history.md](pr-history.md), [development-notes.md 3절](../development-notes.md#3-성능-변화)) |
| Level 5 공유, 기여 | 이 저장소의 문서, PR, 공개 Release는 있다. 사용한 오픈소스 원 저장소에 낸 이슈, PR은 없다. 주장하지 않는다 |

## 4. 평가 기준

| 항목 | 비중 | 근거 |
|---|---|---|
| 문제 정의 | 10% | [requirements.md](../requirements.md) 1~3절 |
| 오픈소스 이해 | 20% | 선택 이유와 버린 대안: [research/open-source-and-models.md](../research/open-source-and-models.md), [development-notes.md 4.1, 6절](../development-notes.md#4-난점). 구조: [architecture.md](../architecture.md) |
| 구현, 재현 | 20% | Release v0.1.2, CI, Docker, 로컬 모델 실제 실행 기록 |
| 개선, 창의성 | 20% | 3절 Level 4 근거, [development-notes.md 4절](../development-notes.md#4-난점) |
| Git 협업 | 10% | PR 42개, 커밋 67개(main), 브랜치, PR 단위 개발. **부족:** 기여자가 1명으로 보인다 |
| 검증 | 10% | held-out 평가와 실측/참고 구분, 시험 980개(Windows에서 1개 skip), 원본 해시 확인 시연 |
| 발표, 문서 | 10% | README, requirements, architecture, api, development-notes, 이 폴더 |

## 5. 프로젝트 수행 5단계

| 단계 | 근거 |
|---|---|
| 문제 정의 | [requirements.md](../requirements.md) 2절 |
| OSS 탐색 | [research/open-source-and-models.md](../research/open-source-and-models.md), [records/](../records/difficulties-and-verification.md), [development-notes.md 6절](../development-notes.md#6-방향을-바꾼-결정), [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) |
| 실행, 재현 | [development-notes.md 2절](../development-notes.md#2-실제로-실행한-것) |
| 수정, 통합 | [architecture.md](../architecture.md), [pr-history.md](pr-history.md) |
| 공유, 발표 | README, Release v0.1.2. 발표 자료와 최종보고서는 저장소에 없다 |

## 6. 개발 과정 기록(실패 → 원인 → 해결 → 기록)

[development-notes.md 4절](../development-notes.md#4-난점)에 9개 난점을 이 순서로 남겼다. 예: `llama-cli`가 실행 중 루프백 포트를 여는 것을 netstat으로 발견 → `llama-completion --offline`으로 교체. 영문 Windows 러너에서만 드러난 출력 인코딩 결함(#43)도 같은 방식으로 고쳤다.

## 7. 아직 없는 것

| 무엇 | 상태 |
|---|---|
| 팀원별 기여 기록 | 커밋 작성자가 1명이다. 팀장의 기획, 문서, 발표 기여를 이슈, PR, 커밋으로 남겨야 한다 |
| 회의록 | 저장소에 없다. 추가 예정 |
| 평가셋, 채점 스크립트 | 저장소에 없다. 측정 결과만 문서에 있다 |
| Q01 검증 지표 이슈 | #13 열림 |
| 최종보고서, 발표 자료 | 저장소에 없다 |
| 사무용 PC 실측 | 개발 PC 한 대에서만 측정했다 |
