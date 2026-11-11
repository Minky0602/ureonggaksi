# 우렁각시 서비스 API 명세

작성일: 2026-11-06 · 개정: 2026-11-09 · 상태: **3판 수정 사용자 승인 (2026-11-09)** (2판 사용자 승인 2026-11-06)

- **기준 문서:** [데이터 모델(객체 11개)](architecture.md#4-목표-데이터-모델), [요구사항 v1.4](requirements.md)
- **구현 상태:** 이 문서는 목표 계약이다. `service.py`와 아래 함수 대부분은 아직 코드에 없다. 지금 있는 코드는 [architecture.md 3절](architecture.md#3-현재-구현)에 있다.
- **문서 안의 "ERD":** [architecture.md 4절](architecture.md#4-목표-데이터-모델)의 데이터 모델을 가리킨다.
- **문서 안의 번호:** `DIF-숫자`는 팀 내부 검증 기록의 항목이고, `P01`~`R01`·`S01`·`Q01`은 PR 계획의 단계 이름이다. 둘 다 저장소 밖에서 관리한다.
- **3판에서 바뀐 것:** 한 줄 요청용 `prepare`·`commit`, 완료/직접 처리 구분(`area`·`ManualItem`), 고정 자동 처리 기준, 종류별 확인필요 파일, 빈칸 채우기·값 검토, 공통 문제 질문, 최소 변경 저장. 이유는 [8.2절](#82-3판-반영-기록-2026-11-09). 2판 원문은 저장소 밖에 보관한다.

HTTP 서버가 아니라 **Python 함수 계약**이다. 화면(`ui/`)과 CLI는 `service.py`의 함수만 부른다. 함수는 입력을 받아 새 값을 돌려주고, 숨은 전역 상태를 두지 않는다.

## 1. 구조

```
ui/ · __main__.py (CLI)          ← 화면·명령줄. 현재 Prepared(=Plan) 하나의 유일한 소유자
        │ 이 함수들만 호출
service.py                        ← 기본 흐름: prepare · commit   /  자세히: 2절의 세부 함수
        │
core: records · normalize · match · plan(자동 처리 판정) · approve · verify · describe   ← 순수 계산
adapters: hwpx(기존) · xlsx(신규: 읽기는 openpyxl, 쓰기는 최소 변경 patch) · ai(신규) · storage(기존+공개 함수 신규)
```

**두 층 구조:** 기본 흐름은 함수 두 개다. 세부 함수(2.1~2.7)는 그대로 남아 있고 `prepare`·`commit`은 그것들을 정해진 순서로 묶은 것이다(Facade). 그래서 시험은 세부 함수 단위로, 화면은 두 함수로 한다.

```
prepare(요청, 파일, 답) = open_inputs → (저장 규칙 | suggest_rule → validate_rule) → analyze(all) → 자동 처리 판정
                         → 규칙이 서지 않거나 공통 문제가 기준을 넘으면 question만 돌려줌
[저장] → commit(Prepared) = RuleStore.save(같은 규칙이면 생략) → 채우기: approve → save_result / 검토: save_check
```

## 2. 자료 형태와 함수 (계약 본문)

모든 자료는 `@dataclass(frozen=True)`이다. frozen이어도 안의 `dict`·`list`는 수정할 수 있으므로, 공개 자료에는 `tuple`과 `Pairs`(키-값 쌍 튜플)만 쓴다. `Path`는 `storage.local_path`를 통과한 로컬 경로다.

### 2.0 기본 흐름 (FR-05, 07, 08, 14, 21)

```python
@dataclass(frozen=True)
class ManualItem:                # 직접 처리 목록 한 줄. ChangeItem·실패 파일에서 만든 읽기 전용 보기
    kind: ManualKind             # 소명 종류(5.1절). 확인필요 파일과 화면은 이 값으로 묶는다
    where: str                   # "사업취합!D9" / 대상 없음이면 "사업계획_C.hwpx 표2 3행"
    reason: str                  # 쉬운 말: "칸에 이미 다른 값이 있음 (기존 1,000,000 · 제안 3,500,000)"
    action: str                  # 할 일: "맞는 값을 확인해 직접 입력"
    reason_code: str             # 5.1절 표의 코드
    change_id: str | None        # 파일 단위 항목이면 None

QuestionSlot = Literal["key_column", "target_column", "work_type",           # 규칙이 서지 않을 때
                       "default_unit", "rounding", "date_order"]   # 공통 문제일 때
QUESTION_RATIO = 0.30            # 사용자 설정이 아닌 코드 기준값. Q01 정답셋으로 조정한다
SKIP = "__skip__"                # 답: 건너뛰기. 해당 칸은 비워 둔다

@dataclass(frozen=True)
class Question:
    slot: QuestionSlot; text: str
    examples: tuple[str, ...]    # 입력에서 뽑은 예시 최대 2개
    choices: tuple[str, ...]     # 버튼 2~3개. 화면은 직접 입력과 건너뛰기를 함께 둔다
    affects: int                 # 이 답으로 정해지는 칸 수. 규칙이 서지 않는 질문은 0

@dataclass(frozen=True)
class Prepared:
    request: str                 # 사용자가 쓴 한 줄
    answers: Pairs               # 지금까지 받은 (slot, 답)
    inputs: Inputs
    question: Question | None    # 물을 것이 있으면 하나만. 이때 plan은 None, 아래 필드는 비어 있다
    plan: Plan | None
    interpretation: str          # describe_rule(rule): 검증된 RuleSet에서 문장 틀로 만든 한 줄. AI 문장이 아니다
    samples: tuple[ChangeItem, ...]   # 완료 영역 최대 3건. 서로 다른 원본·표기를 우선해 결정적으로 고른다
    warnings: tuple[str, ...]    # 해석 경보. 답할 수 없는 같은 문제가 QUESTION_RATIO 이상일 때(예: 대상 없음)
    manual: tuple[ManualItem, ...]    # 직접 처리 영역 전체(실패 파일 포함)
    output_path: Path            # 기본: 대상 폴더/<대상명>_우렁각시_<YYYYMMDD-HHMM>.xlsx

def prepare(request: str, sources: list[Path], target: Path, *,
            workflow: WorkflowVersion | None = None,     # '최근 작업'을 눌렀으면 AI를 부르지 않는다
            answers: Pairs = (),                         # (slot, 답). SKIP이면 그 문제의 칸은 비워 둔다
            output_path: Path | None = None,
            progress: Progress, cancel: Cancel) -> Prepared
    # 쓰기 없음. 질문에 답하면 UI가 answers를 늘려 다시 부른다(서비스는 대화 상태를 갖지 않는다).
    # question이 생기는 경우(PRD 6.2)만 묻는다:
    #   ① 규칙이 서지 않음: key_column·target_column·work_type 미정 또는 validate_rule 이슈.
    #   ② 공통 문제: 답 하나로 정해지는 원인(default_unit·rounding·date_order)의 칸이
    #      분석 대상 칸(ChangeItem 수)의 QUESTION_RATIO 이상. affects가 큰 것부터 하나씩.
    # 그 외 애매함은 묻지 않고 비워 둔다. 답은 RuleSet.policy에 넣어 저장되므로 재실행 때 다시 묻지 않는다.
    # 예외: INPUT_*, PATH_CONFLICT, AI_UNAVAILABLE(workflow 없이 AI 미준비 → '자세히'의 직접 선택 안내), CANCELLED

def commit(prepared: Prepared, store: RuleStore, *, progress: Progress, cancel: Cancel) -> ResultPackage
    # [저장] 한 번 = 승인. 순서: ① store.save(같은 rule_hash가 있으면 생략) ② 채우기: approve → save_result
    #                                                                       검토: save_check(XLSX 없음)
    # 파일 쓰기 전 실패는 예외, 쓰기 시작 후는 ResultPackage로 돌려준다(5절 원칙 그대로).
    # 규칙은 결과 파일보다 먼저 저장된다. 결과 저장이 실패해도 규칙은 남지만, 사용자가 저장을 결정한 해석이므로 허용한다.

def describe_rule(rule: RuleSet, view: TargetView) -> str
    # 예: "채우기: 사업코드로 행을 찾아 ‘사업비’를 D열(2026 예산)에 원 단위로 넣음"
    #     "검토: 표를 바꾸지 않고 D열(2026 예산)을 문서의 ‘사업비’와 대조함"
```

### 2.1 공통

```python
Pairs = tuple[tuple[str, str], ...]          # 불변 매핑
class Cancel(Protocol):                       # threading.Event 호환
    def is_set(self) -> bool: ...
Progress = Callable[[str, int, int], None]    # (단계명, 현재, 전체)

class ServiceError(Exception):   # 부작용(파일 쓰기)을 시작하기 전의 실패만 예외로 던진다
    code: ErrorCode; message: str; detail: Pairs   # detail에 원문 값을 넣지 않는다

@dataclass(frozen=True)
class CellValue:                 # 값의 의미를 고정한다. 화면 표시는 display()로만 만든다
    kind: Literal["empty", "text", "number", "date", "bool"]
    value: str                   # 정규 표현. number=Decimal 문자열 "3500000", date="2026-11-06",
                                 # text=원문 그대로 "0012"(숫자로 바꾸지 않음), empty=""
    unit: str = ""               # 금액 등의 단위 "원". 대상 셀에는 value만 쓴다
    def display(self) -> str: ...
```

### 2.2 입력 연결 (FR-01, 02, 19)

```python
@dataclass(frozen=True)
class InputFile:
    file_id: str                 # 실행 안의 식별자("src-1"…, "target"). 내용 지문과 분리한다
    path: Path; role: Literal["source", "target"]
    sha256: str | None           # 읽기 실패면 None
    status: Literal["ok", "failed", "excluded", "duplicate"]
    reason: str = ""; duplicate_of: str | None = None   # 같은 내용의 다른 경로

@dataclass(frozen=True)
class Inputs:
    run_id: str                  # 입력이 바뀌면 새 run_id
    sources: tuple[InputFile, ...]; target: InputFile; output_dir: Path
    # 내부 스냅샷: 지문을 계산한 바이트를 메모리에 보관한다(repr·비교 제외).
    # 분석과 사본 작성은 이 바이트만 쓰고, 디스크를 다시 읽지 않는다 → "검사한 파일 = 쓰는 파일"

def open_inputs(sources: list[Path], target: Path, output_dir: Path) -> Inputs
    # 원본 실패는 status="failed"로 돌려준다(→ 직접 처리 목록의 파일 항목). 대상 실패·경로 충돌·한도 초과는 ServiceError.
    # 한도(OQ-04, 제안값·실측 전): 원본 500개, HWPX 64 MiB, XLSX 32 MiB, 스냅샷 합계 256 MiB,
    # XLSX 안 ZIP 항목 2048개·해제 128 MiB·항목 64 MiB, 대상 시트 사용 범위 500,000칸(병합·수식 범위 하나, dimension도 같은 한도),
    # 통합문서 전체 셀 1,000,000, 항목당 XML 요소 2,000,000. 셀 수는 openpyxl이 객체를 만드는 요소(c·row·hyperlink)의 합계,
    # 병합은 면적 합계, conditionalFormatting·dataValidation은 따로 20,000. 한도 검사는 openpyxl에 넘기기 전 ZIP 항목
    # 전부를 XML 파서로 훑어서 한다. 이 검사는 알려진 패턴을 막는 층이며, 로드 자체의 시간·메모리 상한은 P03에서 검토한다.
    # 중복은 바이트 SHA-256이 같을 때다. 스냅샷은 대표(먼저 온 것)에만 있으므로 대표를 제외하면 그 중복도 함께 쓰이지 않는다.
    # 실패 이유 문구에는 경로·errno를 넣지 않는다(경로는 InputFile.path).
def exclude_source(inputs: Inputs, file_id: str) -> Inputs        # 새 run_id
```

### 2.3 미리보기 (FR-03, 자세히 화면)

```python
@dataclass(frozen=True)
class TargetCell:
    address: str; value: CellValue
    blocked: Literal["", "formula", "merged", "protected"]

@dataclass(frozen=True)
class TargetView:
    sheet: str; sheets: tuple[str, ...]; header_row: int
    data_rows: tuple[int, int]; columns: tuple[str, ...]
    cells: tuple[TargetCell, ...]          # 미리보기 행(기본 50행)의 주소·기존값·막힘 여부

def inspect_target(inputs: Inputs, sheet: str | None = None, header_row: int | None = None,
                   data_rows: tuple[int, int] | None = None, preview_rows: int = 50) -> TargetView
    # sheet 기본값은 통합문서의 활성 시트. header_row 기본값은 1~20행 중 가장 넓은 행의 절반 이상 채워지고 글자 칸이 있는 첫 행,
    # data_rows 기본값은 (header_row+1, 마지막 비지 않은 행). 데이터가 없으면 (header_row+1, header_row).
    # blocked는 formula(배열·공유 수식은 범위 전체) > merged(병합의 왼쪽 위가 아닌 칸) > protected(시트 보호 + 칸 잠김) 순서로 하나.
    # 기존값은 openpyxl 타입을 그대로 CellValue로 옮긴다. 수식 칸의 기존값은 파일에 저장된 캐시값이고 없으면 empty.
    # int는 자릿수 그대로(반올림 없음). time·timedelta·오류값("#DIV/0!")은 text다(계약에 그 종류가 없다).
def inspect_source(inputs: Inputs, file_id: str) -> Document        # 기존 hwpx.Document 재사용
```

### 2.4 규칙 (FR-04, 08)

```python
@dataclass(frozen=True)
class RuleSet:                   # 직접 선택·AI·가져오기 모두 이 형식을 만든다
    record: RecordRule           # 사업 레코드 경계(표·헤더 행·키 필드). 상세 형식은 P02
    fields: tuple[FieldRule, ...]   # 기존 Rule(label/cell/regex) + value_type·unit·default_unit
    target: TargetRule           # sheet·header_row·data_rows·key_column·(field, column) 쌍
    # 3판 수정: match_policy 삭제. 키는 글자 그대로 일치만 인정한다(PRD v1.4)
    work_type: Literal["fill", "check"]   # 빈칸 채우기 / 값 검토
    policy: Pairs                # 질문으로 정한 값: default_unit·rounding·date_order. 미정이면 없음
    # 3판: existing_value 선택지 삭제. 기존값이 다르면 항상 직접 처리(PRD 6.1 기준 6)

@dataclass(frozen=True)
class WorkflowVersion:           # ERD Workflow + WorkflowVersion
    workflow_id: str; name: str; revision: int; schema_version: int   # 3
    rule: RuleSet; rule_hash: str; saved_at: str
    request: str = ""            # 이 규칙을 만든 한 줄 요청(최근 작업 표시용). 원문값은 넣지 않는다

@dataclass(frozen=True)
class WorkflowSummary:
    workflow_id: str; name: str; latest_revision: int; saved_at: str

@dataclass(frozen=True)
class RuleIssue:
    path: str; code: str; message: str        # path 예: "fields[1].unit"

def validate_rule(rule: RuleSet, inputs: Inputs, view: TargetView) -> tuple[RuleIssue, ...]

class RuleStore:                 # 로컬 폴더 rules/<workflow_id>/<revision>.json
    def list(self) -> tuple[WorkflowSummary, ...]          # 최근 저장 순. S01 '최근 작업'
    def load(self, workflow_id: str, revision: int | None = None) -> WorkflowVersion
    def save(self, name: str, rule: RuleSet, base: WorkflowVersion | None = None,
             request: str = "") -> WorkflowVersion
        # 같은 rule_hash가 이미 있으면 그 버전을 돌려준다(멱등). 수정이면 revision+1, 이전 revision은 보존
    def delete(self, workflow_id: str) -> None      # 결과 파일은 지우지 않는다
    def import_file(self, path: Path) -> RuleDraft  # 외부 v1·v2 JSON → 확인 전 초안(저장 아님)
```

### 2.5 로컬 AI 후보 (FR-05, 06)

```python
@dataclass(frozen=True)
class SourceScope:               # AI에 넘기는 범위. prepare는 자동으로 정하고, 자세히 화면에서는 사용자가 고른다
    file_id: str; locators: tuple[str, ...]

@dataclass(frozen=True)
class AiSelection:               # 모델 출력 전체. 규칙을 쓰지 않고 주어진 목록에서 고르기만 한다(DIF-009)
    work_type: Literal["fill", "check"] | None
    key_block: int | None; key_column: str | None      # 블록 번호 범위·열 이름 enum으로 스키마 제한
    fields: tuple[tuple[str, int, str], ...]           # (항목 이름, 값 블록 번호, 대상 열 이름)

@dataclass(frozen=True)
class AiStatus:
    ready: bool; runtime: str; model: str
    reason: Literal["", "not_installed", "not_running", "no_model", "low_memory"]

@dataclass(frozen=True)
class RuleDraft:
    draft_id: str; origin: Literal["manual", "ai", "imported"]
    rule: RuleSet | None         # 형식 오류면 None
    questions: tuple[str, ...]   # 모호한 지시에 대한 확인 질문. prepare는 첫 번째 하나만 보여준다
    issues: tuple[RuleIssue, ...]

def ai_status() -> AiStatus
def suggest_rule(instruction: str, scope: tuple[SourceScope, ...], view: TargetView,
                 *, cancel: Cancel, timeout_s: int = 60) -> RuleDraft
    # 범위의 텍스트와 대상 헤더만 모델에 보낸다. 모델은 AiSelection만 낸다(스키마 평탄화 + 파이썬에서 재검증).
    # RuleSet은 build_rule이 코드로 만든다.
    # AI 신뢰도 점수로 규칙·쓰기를 확정하지 않는다. 항상 validate_rule과 [저장]을 거친다.
    # prepare의 자동 범위: 정상 원본 최대 3개의 표 제목·헤더·라벨 칸(상한은 P04에서 실측으로 정함)
def build_rule(selection: AiSelection, documents: tuple[Document, ...], view: TargetView) -> RuleDraft
    # 결정적 코드. 고른 위치로 규칙을 만들고, 다시 추출해 그 위치에 도달하는지 자기 대조한다.
    # 항목명은 여러 문서의 같은 위치에 반복되는 글자만 인정한다(DIF-010). 대조 실패 후보는 버린다.
```

### 2.6 분석 → 자동 처리 판정 → 검토 (FR-09~13, 21)

```python
@dataclass(frozen=True)
class Sample:
    file_ids: tuple[str, ...]; max_records: int = 20

def analyze(inputs: Inputs, rule: RuleSet | WorkflowVersion, *, scope: Literal["all"] | Sample = "all",
            progress: Progress, cancel: Cancel) -> Plan
    # 쓰기 없음. PRD 6.1 기준으로 각 항목의 status·write_selected를 정한다.
    # 차이는 기준 6 하나다. fill: 빈칸 → 쓸 칸, 같은 값 → unchanged, 다른 값 → existing_differs.
    #                       check: 같은 값 → unchanged(일치), 다른 값 → mismatch, 빈칸 → missing_in_sheet. write_selected는 항상 False.
    # Sample은 자세히 화면의 직접 선택 흐름에서만 쓴다. prepare는 항상 "all"이다.

ItemStatus = Literal["confirmed", "needs_review", "failed", "excluded", "unchanged"]   # FR-20
Area = Literal["done", "manual", "excluded"]                                            # FR-21

@dataclass(frozen=True)
class Evidence:                  # ERD Evidence. 원문 위치·자동 추출값·변환 과정
    evidence_id: str; file_id: str; sha256: str; record_key: str
    location: str                # 구역·표·행·열(Block.location)
    raw_text: str; extracted: CellValue
    transforms: tuple[str, ...]  # 예: ("단위 만원→원 ×10000",)

@dataclass(frozen=True)
class TargetCandidate:           # 대상 행 후보(OpenRefine의 candidates)
    row: int; address: str; key_value: CellValue

@dataclass(frozen=True)
class ChangeItem:                # ERD ChangeItem
    change_id: str; record_key: str; field: str
    target_ref: str | None       # 확정된 대상 셀. 대상 없음·복수 후보면 None
    target_candidates: tuple[TargetCandidate, ...]   # 복수 후보일 때만
    previous: CellValue | None   # 대상 확정 전에는 None
    proposed: CellValue          # 자동 계산값(보정해도 지우지 않음)
    corrected: CellValue | None  # 사용자 보정값
    status: ItemStatus; reason_code: str; reason: str   # reason_code는 5.1절 표
    evidence: tuple[Evidence, ...]
    resolved: bool               # 검토 필요 사유가 해결됐는가
    write_selected: bool         # 자동 처리 기준을 모두 만족하면 analyze가 True로 둔다
    review_reason: str | None = None
    # 최종값 = corrected가 있으면 corrected, 없으면 proposed

    @property
    def area(self) -> Area:      # 저장하지 않는 파생 값(ERD 속성 아님)
        if self.status == "excluded": return "excluded"
        if self.status == "unchanged" or self.write_selected: return "done"
        return "manual"

@dataclass(frozen=True)
class PlanCounts:                # 파일·레코드·셀을 따로 센다(PRD 6장)
    files_ok: int; files_failed: int; files_excluded: int; records: int
    cells_write: int; cells_unchanged: int; cells_review: int; cells_failed: int; cells_excluded: int
    rows_without_source: int     # 대상 범위에서 키가 있는데 어떤 문서와도 연결되지 않은 행
    # 화면 표시: 완료 = cells_write + cells_unchanged, 직접 처리 = area=="manual"인 칸 + files_failed + rows_without_source

@dataclass(frozen=True)
class Plan:
    run_id: str; inputs: Inputs; rule: RuleSet; rule_hash: str
    rule_ref: WorkflowVersion | None   # 저장 규칙으로 시작했으면 그 버전. 새 요청이면 None(commit에서 저장)
    scope: Literal["all"] | Sample; items: tuple[ChangeItem, ...]; counts: PlanCounts

def manual_items(plan: Plan) -> tuple[ManualItem, ...]
    # area=="manual"인 항목 + 실패 원본 + 문서가 없는 행. 이유·할 일은 5.1절 표의 문장 틀로 만든다(AI 문장 아님)

def review(plan: Plan, change_id: str,
           action: Literal["select", "deselect", "exclude", "correct", "choose_target", "choose_evidence"],
           *, value: CellValue | None = None, target_row: int | None = None,
           evidence_id: str | None = None, reason: str | None = None) -> Plan
    # 자세히 화면 전용. 새 Plan을 돌려준다. 사전 조건을 어기면 ServiceError(REVIEW_REJECTED).
    # - select: target_ref 확정 + 막힌 셀 아님 + resolved=True일 때만. 기존값 다름 칸의 덮어쓰기 선택도 이것이다
    #   check 계획에서는 거부한다(값 검토는 쓰지 않는다)
    # - deselect: 완료 영역의 칸을 직접 처리로 돌린다
    # - choose_target: 후보 중 하나로 target_ref·previous를 채우고 6.1절 기준을 다시 적용한다
    # - choose_evidence: 같은 셀 충돌에서 사용할 원본 값을 고른다
    # - correct: 이유 필수. 보정값에도 같은 자료형·단위 검사를 다시 적용한다
    # - exclude: 이유 필수
```

### 2.7 승인 → 저장·검증 (FR-14~18, 20, 21)

```python
@dataclass(frozen=True)
class Approval:                  # ERD Approval(실행당 1개)
    run_id: str; approved_change_ids: tuple[str, ...]   # area=="done"이고 write_selected인 칸만
    input_fingerprints: Pairs; rule_hash: str; plan_hash: str; review_hash: str
    output_path: Path; evidence_path: Path; manual_path: Path | None; approved_at: str

def approve(plan: Plan, output_path: Path) -> Approval
    # 예외: PLAN_IS_SAMPLE, NOTHING_TO_WRITE, PATH_CONFLICT
    # manual_path = <출력명>_확인필요.txt. 직접 처리 항목이 0건이면 None

@dataclass(frozen=True)
class Check:                     # ERD의 ResultPackage.checks 항목
    scope: Literal["cell", "file", "preserve", "inputs"]
    change_id: str | None; expected: CellValue | None; actual: CellValue | None
    passed: bool; message: str

@dataclass(frozen=True)
class ResultSummary:
    counts: PlanCounts; excluded_sources: tuple[str, ...]; failed_sources: tuple[str, ...]
    manual: tuple[ManualItem, ...]   # S03 화면의 직접 처리 목록(= 확인필요 파일 내용)
    has_formulas: bool           # True면 화면에 안내: 다시 계산하지 않는 프로그램에서는 수식 칸이 옛값일 수 있음

@dataclass(frozen=True)
class ResultPackage:             # ERD ResultPackage
    run_id: str; status: Literal["completed", "partial", "failed", "cancelled"]
    output_path: Path | None; output_sha256: str | None
    evidence_path: Path | None; evidence_sha256: str | None
    manual_path: Path | None     # 확인필요 파일. 지문은 근거 JSON 안에 기록한다
    checks: tuple[Check, ...]; summary: ResultSummary
    error: ErrorCode | None; cleanup_failed: tuple[Path, ...]   # 지우지 못한 임시·부분 파일

def save_result(plan: Plan, approval: Approval, *, progress: Progress, cancel: Cancel) -> ResultPackage
    # 예외(파일 쓰기 시작 전): APPROVAL_INVALID, PATH_CONFLICT
    # 그 뒤의 실패·취소는 예외 없이 ResultPackage(status="failed"|"cancelled")로 돌려준다.

def save_check(plan: Plan, *, progress: Progress, cancel: Cancel) -> ResultPackage
    # 값 검토 전용. XLSX를 만들지 않는다(output_path=None). 확인필요 파일과 근거 JSON만 3절 ④'·④·⑥'·⑦로 공개한다.
```

### 2.8 결과 다시 열기 (FR-17)

```python
@dataclass(frozen=True)
class SourceState:
    file_id: str; path: Path; status: Literal["ok", "changed", "missing"]   # 이동과 삭제는 구분하지 않고 missing으로 둔다

@dataclass(frozen=True)
class ResultView:
    package: ResultPackage; complete: bool   # JSON이 있고 XLSX(·확인필요 파일) 지문이 일치해야 True
    sources: tuple[SourceState, ...]; changes: tuple[ChangeItem, ...]

def open_result(evidence_path: Path) -> ResultView
```

### 2.9 CLI (FR-14, P06)

화면이 없을 때도 "본 것만 저장"을 지키기 위해 두 단계로 나눈다.

```
ureonggaksi prepare --request "…" --target 표.xlsx --out 계획.plan.json 문서/*.hwpx
    # 미리보기 요약·질문을 출력하고 plan_hash를 표시한다. 질문이 있으면 --answer slot=값으로 다시 실행한다.
ureonggaksi commit --plan 계획.plan.json --plan-hash <값>
    # plan.json의 규칙으로 AI 없이 다시 분석해 계산한 해시가 --plan-hash와 같을 때만 commit한다.
    # 다르면 PLAN_HASH_MISMATCH. 파일을 만들지 않는다.
```

기존 CLI의 `--overwrite` 옵션은 덮어쓰기 금지 정책과 맞지 않으므로 없앤다.

## 3. 저장 절차와 완료 판정

`save_result`의 단계(3판에서 확인필요 파일 ④'·⑥'을 추가):

| 단계 | 하는 일 | 실패하면 |
|---|---|---|
| ① 사전 확인 | `approval.plan_hash == hash(plan)`, 스냅샷 지문 == 승인 지문, 디스크 입력 지문 == 승인 지문, 출력 경로가 모두 없음 | 예외(파일 생성 없음) |
| ② 최소 변경 작성 | **스냅샷 바이트**에서 대상 시트 XML의 작성할 `<c>`만 고치고 나머지 패키지 항목은 내용 그대로 복사해 임시 XLSX 작성(아래) | failed, 임시 파일 정리 |
| ③ 재열기·비교 검증 | 작성 값 대조 + 원본과 패키지 비교(아래) | failed(부분 완료로 낮추지 않음) |
| ④' 확인필요 작성 | 직접 처리 항목이 있으면 임시 TXT 작성(UTF-8) | failed |
| ④ 근거 작성 | 임시 JSON 작성. XLSX·TXT sha256 포함 | failed |
| ⑤ 공개 직전 확인 | 디스크 입력 지문을 다시 계산(FR-14·15) | failed `INPUT_CHANGED` |
| ⑥ XLSX 공개 | 덮어쓰기 없는 공개. 이미 있으면 실패 | failed |
| ⑥' TXT 공개 | 덮어쓰기 없는 공개 | ⑥의 XLSX를 지문 확인 후 삭제 |
| ⑦ JSON 공개 | 덮어쓰기 없는 공개. **JSON이 커밋 기록이다** | ⑥·⑥' 파일을 지문 확인 후 삭제, 못 지우면 `cleanup_failed` |

- **최소 변경 작성(`xlsx.patch`):** openpyxl로 저장하지 않는다(DIF-012). 작성하는 `<c>`의 스타일 속성 `s`는 유지하고, 숫자는 `<v>`, 글자는 인라인 문자열로 쓴다. 없는 `<c>`·`<row>`는 주소 순서에 맞게 끼운다. 통합문서에 수식이 있으면 `workbook.xml`의 `calcPr`에 `fullCalcOnLoad="1"`을 넣는다.
- **③의 비교 검증:** 원본과 사본의 패키지 항목 목록이 같고, 각 항목 내용이 같아야 한다. 허용되는 차이는 대상 시트의 작성한 `<c>`(필요하면 끼운 `<row>`와 `dimension`)와 `workbook.xml`의 `calcPr`뿐이다. 그 밖의 차이가 하나라도 있으면 failed다.
- **확인필요 파일 형식:** 첫머리에 제출·전달용이 아니라는 안내, 결과 파일 이름, `채운 칸 N · 이미 같은 값 K · 안 채운 칸 M · 읽지 못한 파일 F`를 쓴다. 그 아래에 5.1절 종류 순서로 묶음마다 `[번호] 종류 (건수)`, `왜:`, `할 일:`, 항목 목록(주소, 기존값·문서값, 근거 위치)을 쓴다. 값 검토는 첫머리 집계가 `일치 · 불일치 · 표에 빠짐 · 판정 불가 · 문서가 없는 행`이며, 표와 문서 중 어느 쪽이 맞는지는 판단하지 않는다. 근거 JSON에 지문을 남겨 같은 실행의 것인지 확인한다.
- **덮어쓰기 없는 공개:** 기존 `storage.atomic_write`는 `os.replace`를 쓰므로 기존 파일을 덮어쓴다. 결과 공개에는 쓰지 않는다. 신규 함수 `publish_new(temp, final)`로 공개한다. 이 함수는 같은 볼륨에서 `os.link`를 쓰고 대상이 있으면 실패한다. 하드 링크를 지원하지 않는 파일 시스템이면 배타적 생성(`open(final, "xb")`)으로 복사한 뒤 fsync한다. 구체 구현은 P03에서 정한다.
- **취소:** ⑥ 전까지만 받는다. ⑥부터는 끝까지 진행한다.
- **완료 판정:** XLSX와 JSON이 모두 있고 JSON 안의 XLSX(·TXT) 지문이 일치해야 완료다. ⑥과 ⑦ 사이에 강제 종료되어 XLSX만 남으면 완료가 아니다. `open_result`와 화면은 이를 "미완료 결과"로 안내하고 자동으로 지우지 않는다.
- **결과 상태:**

| 상태 | 조건 |
|---|---|
| completed | 모든 필수 검사 통과, 직접 처리 항목·제외 없음. 값 검토에서는 전부 일치 |
| partial | 모든 필수 검사 통과, 그러나 직접 처리 항목(실패 원본 포함) 또는 제외가 있음. 화면 문구: "N칸 완료 · M건은 직접 처리해 주세요" |
| failed | 검사·근거·확인필요·공개 중 하나라도 실패. partial로 낮추지 않는다 |
| cancelled | ⑥ 전에 사용자가 취소함 |

## 4. 무효화·소유·해시

**상태의 소유자:** 화면 컨트롤러가 현재 `Prepared` 하나를 혼자 가진다(승인은 `commit` 안에서 만들고 바로 쓴다). 요청·파일·세부 검토가 바뀌면 새 `Prepared`를 만든다. 서비스는 `plan_hash` 일치로 **"쓰는 내용 = 저장 버튼을 누를 때 보인 내용"**만 보장한다.

| 무엇이 바뀌면 | 결과 |
|---|---|
| 원본 추가·제외·교체 | 새 `run_id`. 다시 `prepare` |
| 요청 수정·질문 답 | 다시 `prepare`(새 해석) |
| `review` 호출(자세히) | 새 Plan. 컨트롤러가 `manual_items`·샘플을 다시 계산한다 |
| 승인 뒤 디스크 파일 변경 | `save_result` ① 또는 ⑤에서 중단 |

**해시 계약:** 정규 JSON(키 정렬, `separators=(",", ":")`, UTF-8, `ensure_ascii=False`)을 SHA-256으로 해시한다.

| 해시 | 포함하는 것 |
|---|---|
| `rule_hash` | `RuleSet` 전체 |
| `plan_hash` | 입력 지문(file_id 순), rule_hash, scope, change_id 순으로 정렬한 각 항목의 (change_id, target_ref, previous, 최종값, status, write_selected, evidence_id 목록), 출력 경로 3개(XLSX·JSON·TXT 또는 null) |
| `review_hash` | change_id 순으로 정렬한 각 항목의 (corrected, review_reason, resolved, write_selected) |

- **값 변환:** CellValue는 `{kind, value, unit}`으로 바꾼다. Decimal은 지수 없는 정규 문자열, 날짜는 ISO 8601이다.
- **경로 변환:** 절대 경로로 바꾼 뒤 `os.path.normcase`를 적용한다.
- **목록 순서:** 순서에 의미가 없는 목록은 id 순으로 정렬한다.
- `interpretation`·`samples`·`manual`은 plan에서 결정적으로 만들어지므로 해시에 따로 넣지 않는다.
- `change_id`는 (record_key, field)에서 결정적으로 만든다. `run_id`는 실행마다 달라지므로 `plan_hash`에 넣지 않는다(CLI가 같은 입력으로 다시 계산해 비교하기 위함).

## 5. 오류 코드

항목 단위 문제(누락·복수 후보·대상 없음·충돌·막힌 셀·기존값 다름)는 예외가 아니라 `ChangeItem.status`·`reason_code`로 돌려주고, 화면에서는 직접 처리 영역으로 보인다.

| 코드 | 시점 | 받는 방식 | 복구 행동 |
|---|---|---|---|
| `PLAN_HASH_MISMATCH` | CLI commit | 예외 | 다시 prepare 후 새 지문으로 commit |
| `INPUT_UNSUPPORTED` · `INPUT_LIMIT` | open_inputs·prepare | 예외 | 다른 파일 선택, 입력 줄이기 |
| `PATH_CONFLICT` | open_inputs·approve·save ① | 예외 | 다시 prepare(새 시각 이름) 또는 이름 변경 |
| `RULE_INVALID` | analyze(자세히) | 예외 | 이슈 위치로 이동. prepare에서는 예외 대신 질문으로 바뀐다 |
| `PLAN_IS_SAMPLE` | approve | 예외 | 전체 분석 후 저장 |
| `REVIEW_REJECTED` | review | 예외 | 화면에서 선택 불가 이유 표시 |
| `NOTHING_TO_WRITE` | approve·commit(채우기) | 예외 | 직접 처리 목록만 보여준다(파일 생성 없음) |
| `RULE_SAVE_FAILED` | commit ① | 예외 | 규칙 폴더 권한 확인. 결과 파일은 만들지 않았다 |
| `APPROVAL_INVALID` | save ① | 예외 | 다시 prepare 후 저장 |
| `INPUT_CHANGED` · `WRITE_FAILED` · `VERIFY_FAILED` · `EVIDENCE_FAILED` · `PUBLISH_FAILED` | save ②~⑦ | `ResultPackage.error` | 사유 표시, 재시도 |
| `CANCELLED` | prepare·analyze·suggest(예외), save(결과) | 위와 같음 | 원본·기존 결과 보존 |
| `AI_UNAVAILABLE` · `AI_TIMEOUT` · `AI_BAD_OUTPUT` | prepare·suggest_rule | 예외 | 재시도, 최근 작업 사용, 또는 자세히의 직접 선택 |

**받는 방식의 원칙:** 파일을 쓰기 시작하기 전에는 예외로 던진다. 쓰기를 시작한 뒤에는 정리 결과까지 담아 결과값으로 돌려준다.

### 5.1 안 채운 칸의 종류와 이유 코드 (FR-21, PRD 6.3절)

`reason`·`action` 문장은 이 표의 틀로 만든다. 화면과 확인필요 파일은 `kind`로 묶고, 내부 코드는 근거 JSON에 남긴다. 새 코드를 추가하면 이 표와 정답셋을 함께 고친다.

```python
ManualKind = Literal["existing_differs", "no_row", "source_conflict", "unreadable_value",
                     "blocked_cell", "unreadable_file", "no_document", "check_diff", "deselected"]
```

| kind (표시 순서) | reason_code | 6.1 기준 | 이유 | 할 일 | 질문 가능 |
|---|---|---|---|---|---|
| `existing_differs` 이미 다른 값이 있음 | `existing_differs` | 6 | 칸에 이미 다른 값이 있음. 기존 값 유지 (기존 · 문서) | 맞는 값을 확인해 직접 고치기 | 아니오 |
| `no_row` 들어갈 행을 못 정함 | `no_target` · `multi_target` | 4 | 같은 키의 행이 없음 / 여러 개임 (후보 주소). 키가 문서와 다르면 파일 오류 의심 | 파일의 키를 확인하거나 맞는 행에 직접 입력 | 아니오 |
| `source_conflict` 원본끼리 값이 다름 | `conflict` | 7 | 같은 칸에 문서마다 다른 값 (원본별 값) | 원본을 확인해 직접 입력 | 아니오 |
| `unreadable_value` 값을 확실히 못 읽음 | `missing_value` · `ambiguous_field` · `ambiguous_unit` · `parse_failed` · `needs_rounding` · `tracked_change` | 2·3 | 값을 찾지 못함 / 여러 곳 / 단위 불명 / 숫자·날짜로 못 읽음 / 반올림 필요 / 변경 추적 남음 | 원문을 확인해 직접 입력 | `default_unit`·`date_order`·`rounding`만 |
| `blocked_cell` 쓰면 안 되는 칸 | `blocked_formula` · `blocked_merged` · `blocked_protected` | 5 | 수식·병합·보호 칸 | 칸 구조를 확인해 직접 입력 | 아니오 |
| `unreadable_file` 파일을 읽지 못함 | `source_failed` | – | 파일을 읽지 못함 (사유) | 파일을 확인해 다시 넣기 | 아니오 |
| `no_document` 문서가 없는 행 | `target_without_source` | – | 이 행의 값을 가진 문서가 입력에 없음 (행 주소·키) | 문서가 빠졌는지 확인 | 아니오 |
| `check_diff` 값 검토 차이 | `mismatch` · `missing_in_sheet` | 6 | 표와 문서가 다름 / 표가 비어 있음 (표값 · 문서값) | 맞는 값을 확인해 직접 고치기 | 아니오 |
| `deselected` 직접 처리로 돌린 칸 | `deselected` | – | 자세히에서 직접 처리로 돌림 | 직접 입력 | 아니오 |

## 6. 파일 형식

| 파일 | 위치 | 핵심 내용 | Schema 작성 |
|---|---|---|---|
| 규칙 `rules/<id>/<rev>.json` | 사용자 로컬 | `schema_version: 3`, name, revision, request, `RuleSet`(work_type·policy 포함), rule_hash. 원본 경로·원문값은 넣지 않는다 | P02 |
| 근거 `<출력명>.evidence.json` | 출력 XLSX 옆 | run_id, 입력 file_id·경로·지문, 규칙 스냅샷, 해석 한 줄, 승인 해시, ChangeItem 전체(근거·보정·area 포함), checks, summary, XLSX·TXT sha256 | P03 |
| 확인필요 `<출력명>_확인필요.txt` | 출력 XLSX 옆(값 검토는 대상 XLSX 옆) | 3절 종류별 형식. 안 채운 칸·차이가 있을 때만 | P03 |
| CLI 계획 `<이름>.plan.json` | 사용자 지정 | request, 입력 경로·지문, RuleSet, 출력 경로, 미리보기 요약, plan_hash | P04 |

기존 v1 규칙(추출만)은 `RuleStore.import_file`로 가져온다. 대상 매핑이 빈 초안이 되고, 사용자가 채운 뒤 저장한다. v2 규칙의 `existing_value`는 읽을 때 무시한다.

## 7. 요구사항 연결

| FR | 함수·자료 | AT |
|---|---|---|
| 01·02 | `open_inputs`, `exclude_source`, `InputFile` | 01, 17 |
| 03 | `inspect_target`(셀 주소·기존값), `inspect_source` | 05 |
| 04 | `RuleSet`, `validate_rule`, `analyze(scope=Sample)` (자세히) | 01 |
| 05·06 | `prepare`(`Question`), `ai_status`, `suggest_rule`, `AiSelection`, `build_rule`, `RuleDraft` | 02, 03, 04, 16, 19, 21 |
| 07 | `describe_rule`, `Prepared.samples`, `Prepared.warnings` | 02, 19 |
| 08 | `RuleStore`(멱등 save, revision 보존, `import_file`), `prepare(workflow=)`, `RuleSet.policy` | 02, 15, 21 |
| 09~11 | `analyze`, `TargetCandidate` | 05, 06, 07 |
| 12·13 | `review`(자세히), `PlanCounts`, `ChangeItem.area` | 07, 08, 12 |
| 14 | `commit` → `approve`, 해시 계약, save ①⑤, CLI 두 단계(2.9절) | 02, 10, 23 |
| 15·16·17 | save ②~⑦(최소 변경 작성·비교 검증), `Check`, `open_result` | 09, 11, 13, 22 |
| 18 | `Cancel`, `Progress`, 덮어쓰기 없는 공개 | 11, 14 |
| 19 | `local_path`, 네트워크 가드, `SourceScope` | 03, 16, 17 |
| 20 | `ItemStatus`, 결과 상태 규칙 | 12, 13 |
| 21 | PRD 6.1 판정(`analyze`), `Area`, `ManualItem`, `ManualKind`, `manual_items`, 5.1절 표, 확인필요 파일 | 08, 18 |
| 22 | `RuleSet.work_type`, `save_check`, `mismatch`·`missing_in_sheet` | 20 |

ERD 11개 객체의 대응(3판에서 객체·관계 변화 없음):

| ERD 객체 | 명세 위치 |
|---|---|
| Workflow·WorkflowVersion | `WorkflowVersion`·`RuleStore` |
| Run | `run_id`·`Inputs` |
| RuleDraft | `RuleDraft` |
| Document | `InputFile` |
| SourceRecord·ExtractedField | `analyze` 내부 산출. `Evidence.record_key`·`extracted`로 노출 |
| ChangeItem·Evidence·Approval·ResultPackage | 같은 이름의 자료 형태 |

`Prepared`·`ManualItem`·`area`는 ChangeItem·InputFile에서 계산하는 화면용 보기라 ERD 객체가 아니다. 확인필요 파일도 ResultPackage의 파생 산출물이다. `Approval.manual_path`는 승인이 묶는 출력 위치의 일부다. `WorkflowVersion.request`는 규칙 스냅샷에 들어가는 설명 속성이다. `RuleSet.work_type`·`policy`도 규칙 스냅샷의 일부다. 값 검토 실행은 Approval 없이 ResultPackage만 남긴다(Run → Approval 0..1 범위 안). 이 차이는 ERD 문서에 적었다.

**참고 설계(재사용 안 함):** 아래 도구는 설계만 참고했고 의존성으로 쓰지 않는다. 출처는 외부 검토가 제공했고(확인일 2026-11-06), 대안 비교에 쓰기 전에 다시 확인한다.

| 도구 | 참고한 것 | 쓰지 않는 이유 |
|---|---|---|
| invoice2data | 규칙(템플릿)과 실행 엔진의 분리, AI 초안 → 샘플 적용 → 저장 | PDF 중심 |
| UiPath Document Understanding | 자동값·확인 여부 구분 | 상용 제품이라 OSS가 아님. 개념만 참고 |
| Grist | 키 매칭 → 변경 미리보기 재계산 | 중복 키 정책(마지막·첫 행 자동 선택)은 공공업무 오입력 위험이 있어 따르지 않는다 |
| OpenRefine | 후보(candidates)와 확정(match)의 분리 | Java 서버형 도구 |

## 8. 검토 반영 기록

### 8.1 2판 반영 기록 (2026-11-06)

외부 검토 7항목과 추가 4항목을 판단했다.

| 검토 의견 | 판단 | 반영 |
|---|---|---|
| 1 대상 미확정·후보 선택 | 수용 | `target_ref: None`, `target_candidates`, `choose_target` |
| 2 XLSX·JSON 묶음 공개 | 수용(방식 조정) | 두 파일의 완전한 원자적 공개는 파일 시스템에서 불가능하다. 그래서 "JSON = 커밋 기록" 방식으로 완료를 판정하고, 덮어쓰기 없는 `publish_new`를 쓴다. `atomic_write`는 결과 공개에 재사용하지 않는다 |
| 3 검사한 파일 = 쓰는 파일 | 수용 | 입력 바이트 스냅샷으로 분석·작성하고, 공개 직전에 디스크 지문을 재확인한다 |
| 4 값·단위·보정 의미 | 수용(최소화) | 작은 `CellValue` 하나로 정리했다. 전체 타입 체계는 만들지 않는다. select 사전 조건과 보정 재검사를 넣었다 |
| 5 미리보기·샘플 적용 | 수용(제한) | `TargetView.cells`는 기본 50행으로 제한한다. `data_rows` 입력을 받는다. `analyze(scope=Sample)`을 추가했다 |
| 6 예외 vs 결과 충돌 | 수용 | 쓰기 시작 전 = 예외, 시작 후 = 결과. partial 조건을 표로 정했다 |
| 7 무효화 책임·해시 | 부분 수용 | 해시 포함 필드와 직렬화 규칙을 정했다. 중첩 dict는 금지했다. 다만 서비스에 상태 객체(세션)를 추가하자는 방향은 **기각**했다. 소유자는 화면 컨트롤러이고, 서비스는 plan_hash로 쓰기 = 승인만 보장한다 |
| 공개 자료형 미정의 | 수용 | `SourceScope`·`WorkflowVersion`·`Evidence`·`Check`·`PlanCounts` 등을 정의했다 |
| 결과 재열람·지문 | 수용 | `open_result`, `ResultPackage.output_sha256`·`evidence_sha256`(ERD 필드 복원) |
| 문서 식별자 | 수용 | `file_id`와 `sha256`을 분리했다. 읽기 실패 시 sha256은 None |
| v1 가져오기 | 수용 | `RuleStore.import_file → RuleDraft` |
| 이동·삭제 구분 | **기각** | 경로만으로 이동과 삭제를 구분할 수 없다. `missing` 하나로 둔다 |

### 8.2 3판 반영 기록 (2026-11-09)

사용자 요구: "복잡한 설정이나 여러 선택지 없이 자연어로 간단히 요청 → 명확한 부분 자동 처리 → 완료/직접 처리 구분". PRD v1.2에 반영했다.

| 결정 | 선택 | 버린 대안과 이유 |
|---|---|---|
| 기본 흐름 | `prepare`·`commit` 두 함수(Facade). 세부 함수는 유지 | 세부 함수를 없애고 큰 함수 하나로 합치기: 시험·자세히 화면이 불가능해진다 |
| 자동 처리 기준 | 고정 규칙 7개(PRD 6.1). `analyze`가 판정 | AI 신뢰도 점수: 재현·설명이 안 되고 모델을 바꾸면 기준이 바뀐다 |
| 승인 | [저장] 한 번 = 승인. 해석 한 줄 + 샘플 2~3건을 그 위에 표시 | 규칙 확인과 저장 승인 분리(2판): 단계가 늘어 요구와 충돌. 대신 해석 오류는 샘플로 잡는다 |
| 해석 한 줄 | `describe_rule`이 검증된 RuleSet에서 문장 틀로 만듦 | AI가 쓴 설명문: 실제 실행될 규칙과 다를 수 있다 |
| 모호한 요청 | 질문 하나, 답은 요청에 이어 붙여 다시 `prepare` | 선택 양식: 요구와 충돌. 서비스의 대화 세션: 2판에서 기각한 상태 객체가 된다 |
| 직접 처리 표시 | 앱 화면 + `<출력명>_확인필요.txt`. 결과 XLSX는 그대로 | 결과 XLSX에 "확인 필요" 시트(사용자와 함께 기각, 2026-11-09): 공식 제출 파일에 시트가 남을 위험. 셀 색칠: 서식 보존 검사와 충돌 |
| 기존값 다름 | 항상 직접 처리. `RuleSet.existing_value` 삭제 | 덮어쓰기 옵션 유지: 선택지가 늘고 오입력 위험 |
| 규칙 저장 | `commit` 첫 단계에서 자동·멱등 저장 | 결과 성공 뒤 저장: 저장 실패를 결과값과 섞어야 한다. 결과 실패 시 규칙이 남는 것은 허용 |
| 규칙 재사용 | `prepare(workflow=)`. AI를 부르지 않음 | 자연어 "지난번처럼" 해석: 어떤 규칙인지 모호하다 |

수정(같은 날, 설계 검토와 사용자 결정 반영):

| 결정 | 선택 | 버린 대안과 이유 |
|---|---|---|
| 질문 | 규칙이 서지 않을 때와, 답 하나로 정해지는 같은 문제가 전체 칸의 30% 이상일 때만(사용자 확정). 나머지는 비워 둔다 | 칸마다 질문, 고정 개수 질문: 피로도가 크고 칸마다 다른 애매함은 답으로 일반화되지 않는다 |
| 안 채운 칸 소명 | 종류별 묶음 + 이유 + 할 일(사용자 제안·확정) | 한 줄 나열(이전 3판): 무엇 때문인지 한눈에 보이지 않는다 |
| 업무 종류 | `work_type: fill / check`. 기준 6과 결과 표시만 다르다 | 엔진 분리: 나머지 기준 6개가 같아 중복이다 |
| 값 갱신 | 자동 없음. 다른 값은 직접 처리(사용자 확정) | 조건부 자동 덮어쓰기: 공공 제출 표에서 가장 위험한 동작이다 |
| 저장 방식 | 최소 변경 작성 + 패키지 비교 검증(사용자 확정, DIF-012) | openpyxl 재저장: 수식 캐시값 등이 사라져 "작성한 칸만 다름"을 지킬 수 없다. xlsxedit: 작은 단일 저자 저장소라 의존성으로 쓰지 않고 참고만 한다. Excel COM: Excel 설치가 필요해 Linux·오프라인 보장에 맞지 않다 |
| 키 일치 | 글자 그대로만 인정, `match_policy`·`key_spacing` 삭제(사용자 확정: 다르면 오류 파일) | 띄어쓰기 무시 옵션: 오류 파일을 정상으로 통과시킨다 |
| 문서가 없는 행 | `no_document`로 보고(문서 출발 분석의 구멍 보완) | 보고하지 않음: 비어 있는 행이 끝난 것처럼 보인다 |
| 규칙 저장 시점 | 결과 파일 작성 전 유지(권고 유지, 사용자 이견 없음) | 결과 성공 뒤 저장: 저장 실패를 결과값과 섞어야 한다 |
| 작업 방향 | HWPX → XLSX 채우기와 양쪽 읽기 대조만(사용자 확정) | XLSX → HWPX 채우기: HWPX 최소 변경 저장이 추가로 필요해 MVP에서 제외 |
| AI 역할 | 모델은 `AiSelection`만, 규칙은 `build_rule`이 코드로(DIF-009) | 모델이 RuleSet 직접 작성: 실측 5/12(2B·4B), 선택 방식은 4B에서 12/12(개발 PC, 사례 12개) |
| CLI 승인 | `prepare`가 낸 plan_hash를 `commit`에 넘겨야 저장(2.9절) | `--yes` 플래그: 내용을 보지 않고도 승인되어 "쓰는 내용 = 승인한 내용"이 깨진다 |

## 9. 남은 결정 (구현 PR에서 확정)

- **S01:** AI 실행기 방식. 11/5 실험은 `llama-completion` 자식 프로세스 + Qwen3.5 2B·4B로 진행했다(DIF-001~011). 사무용 PC 실측과 채택 결정이 남았다. 이에 따라 네트워크 가드 예외 범위가 정해진다.
- **P02:** HWPX 읽기 결함 수정(DIF-013: 변경 추적 표시를 `Block`에 노출, 특수 공백), 레코드 경계 규칙 형식(OQ-03), 입력 한도 수치(OQ-04), 스냅샷 메모리 상한, 6.1 판정 구현과 5.1 이유 코드 시험
- **P03:** `publish_new`의 대체 경로 시험, 근거 JSON의 원문값 마스킹(OQ-05). 기본은 포함 + 저장 전 안내. `xlsx.patch` 세부: 배열 수식 범위 안의 칸 차단, 1904 날짜 기준, 전자서명 포함 파일 거부, 비교 검증의 허용 차이 시험, Excel·한셀 실제 열기 시험
- **P04:** `prepare`의 AI 자동 범위 상한(모델 문맥 길이·속도 실측)과 질문 문장 틀
- **Q01:** `QUESTION_RATIO`(시작값 30%) 조정
