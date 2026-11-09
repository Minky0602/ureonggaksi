# 우렁각시 개발 안내 (코딩 에이전트용)

- 사람용 안내는 `CONTRIBUTING.md`, 구조는 `docs/architecture.md`, 함수 계약은 `docs/api.md`, 요구사항은 `docs/requirements.md`다. 설계가 바뀌면 이 문서들을 먼저 고친다.
- 변경은 PR 단위로 한다. 범위와 완료 조건을 먼저 정하고, 실행한 시험과 결과를 PR 본문에 남긴다. push, PR 생성, 병합은 사람이 한다.
- Python 3.12+, PySide6 Widgets. 네트워크/웹서버/원격 API/원격 폰트/텔레메트리를 추가하지 않는다.
- 원본 HWPX는 읽기 전용. 문서 본문을 로그, 설정, 자동 저장에 남기지 않는다. 실제 업무자료와 모델 파일을 저장소에 넣지 않는다.
- 핵심: `src/ureonggaksi/hwpx.py`(제한된 ZIP/XML 파싱), `workflow.py`(JSON 규칙), `extract.py`(추출/검증), `export.py`(엑셀), `ui/`(화면).
- 설치: `uv sync --locked --extra dev`. 잠금 파일과 다르면 실패하게 해서 모두 같은 버전을 쓴다.
- 실행: `.venv/Scripts/python -m ureonggaksi` (Windows: `scripts/run.ps1`).
- 테스트와 정적 검사: Windows `scripts/test.ps1`, Linux `scripts/test.sh`. CI(`.github/workflows/ci.yml`)가 같은 스크립트를 돌린다.
- 실제 화면은 Windows에서 실행 확인. 자동 UI 테스트는 Qt offscreen으로 원문 선택→규칙→추출→검증을 확인한다.
- 완료 조건: 다중 HWPX 추출, workflow JSON 재사용, 엑셀 값/출처 연결, 원본 변경 감지, 악성 ZIP/XML·수식 방어 테스트 통과.
- 런타임은 오프라인. 초기 개발 패키지 설치와 wheel 준비만 명시적으로 인터넷을 사용한다.
- 실제 한글 생성 HWPX로 호환성 확인 전에는 모든 HWPX 호환 또는 원본 페이지 렌더링을 보장한다고 쓰지 않는다.
- 로컬 자연어 후보 생성과 기존 XLSX 취합은 아직 구현되지 않았다. `docs/`의 목표 설계를 구현된 기능으로 쓰지 않는다.
