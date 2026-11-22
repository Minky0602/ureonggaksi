# 우렁각시 개발 안내 (코딩 에이전트용)

- 사람용 안내는 `CONTRIBUTING.md`, 구조는 `docs/architecture.md`, 함수 계약은 `docs/api.md`, 요구사항은 `docs/requirements.md`, 개발 기록(실행한 것·난점·측정)은 `docs/development-notes.md`다. 설계가 바뀌면 앞의 네 문서를 먼저 고친다.
- 변경은 PR 단위로 한다. 범위와 완료 조건을 먼저 정하고, 실행한 시험과 결과를 PR 본문에 남긴다. 커밋·PR 제목은 `type: 설명`(한국어, 명사형)이고 AI 표기를 넣지 않는다. 병합은 CI가 초록이고 독립 검증을 통과한 뒤에만 한다.
- Python 3.12+, PySide6 Widgets. 네트워크/웹서버/원격 API/원격 폰트/텔레메트리를 추가하지 않는다.
- 원본 HWPX와 대상 XLSX는 읽기 전용. 문서 본문을 로그, 설정, 자동 저장에 남기지 않는다. 실제 업무자료와 모델 파일을 저장소에 넣지 않는다.
- 핵심: `hwpx.py`(제한된 ZIP/XML 파싱), `inputs.py`·`xlsx.py`(입력 묶기·취합표 읽기), `interpret.py`·`synonyms.py`·`morph.py`·`ai.py`(한 줄 요청 해석·로컬 모델), `analyze.py`(판정·계획), `patch.py`·`save.py`(최소 변경 쓰기·승인·저장), `service.py`·`cli.py`(prepare·commit), `ui/flow.py`(주 화면). 전체 표는 `docs/architecture.md` 3.1절.
- 설치: `uv sync --locked --extra dev`. 잠금 파일과 다르면 실패하게 해서 모두 같은 버전을 쓴다.
- 실행: `.venv/Scripts/python -m ureonggaksi` (Windows: `scripts/run.ps1`).
- 테스트와 정적 검사: Windows `scripts/test.ps1`, Linux `scripts/test.sh`. CI(`.github/workflows/ci.yml`)가 같은 스크립트를 돌리고, `Dockerfile` 컨테이너(네트워크 없음)에서도 시험과 `scripts/demo.sh`를 돌린다.
- 실제 화면은 Windows에서 실행 확인. 자동 UI 테스트는 Qt offscreen으로 파일 선택→한 줄 요청→미리보기→저장을 확인한다.
- 완료 조건: 시험·ruff 통과(Windows·Linux·Docker CI 3잡 초록), 결과 XLSX는 작성한 칸만 원본과 다름(패키지 비교 검증), 원본·취합표 불변, 승인 지문이 다르면 저장하지 않음, 악성 ZIP/XML·수식 방어 시험 통과. 로컬 모델이 바뀌는 변경은 합성 평가셋으로 재측정한다.
- 런타임은 오프라인. 초기 개발 패키지 설치와 wheel 준비만 명시적으로 인터넷을 사용한다.
- 실제 한글 생성 HWPX로 호환성 확인 전에는 모든 HWPX 호환 또는 원본 페이지 렌더링을 보장한다고 쓰지 않는다.
- 한 줄 요청 해석·로컬 모델 짝 선택·기존 XLSX 빈칸 채우기는 구현돼 있다(합성 자료로만 검증). `docs/requirements.md`와 `docs/architecture.md` 4~5절의 목표 설계를 구현된 기능으로 쓰지 않는다.
