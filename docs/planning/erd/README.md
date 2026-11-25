# ERD

기준일: 2026-11-25 (main `9a5a59a`, v0.1.2)

우렁각시는 데이터베이스가 없다. 아래 ERD는 메모리의 Python 객체와 로컬 파일(규칙 JSON, 결과 XLSX, 근거 JSON)의 관계를 그린 **논리 모델**이다. `(PK)`는 논리 식별자이고 DB 제약이 아니다. `(ref)`는 다른 객체를 가리키는 식별자다.

| 그림 | 내용 | 상태 |
|---|---|---|
| [mvp-logical.svg](mvp-logical.svg), [원본 .mmd](mvp-logical.mmd) | MVP 목표 논리 모델. 객체 11개, 관계 13개 | **구현됨**(일부 제외, 아래 2절) |
| [current-objects.svg](current-objects.svg), [원본 .mmd](current-objects.mmd) | 2026-11-09 기준선에 들어온 프로토타입 객체 7개, 관계 5개 | 코드에 남아 있음. 최신 객체 목록은 [architecture.md 3.2절](../../architecture.md#32-객체) |

## 1. 경과

| 날짜 | 내용 |
|---|---|
| 2026-11-05 | 프로토타입 코드의 객체 관계와 MVP 목표 모델을 따로 그렸다. 목표 모델 초안은 객체 14개, 관계 17개 |
| 2026-11-06 | 단순화안 확정: 14개 → 11개 객체, 17개 → 13개 관계. 1인용 데스크톱이라 DB식 연결 객체와 이력 객체를 걷어냈다(`ApprovalChange` → `Approval.approved_change_ids` 목록, `ReviewDecision` → `ChangeItem`의 검토 필드, `Verification` → `ResultPackage.checks`, 승인은 실행당 0..1개) |
| 2026-11-09 | 속성을 [api.md](../../api.md)(3판)에 맞췄다. 객체, 관계는 그대로 두고 `work_type`, `policy`, `request` 등을 넣었다 |
| 2026-11-10 | 그림을 가로 배치 SVG로 다시 그렸다. 내용은 같다 |
| 2026-11-11 ~ 11-25 | 목표 모델을 P02~P05 PR에서 구현했다([pr-history.md](../pr-history.md)) |

## 2. 목표 모델의 구현 상태

[architecture.md 4절](../../architecture.md#4-목표-데이터-모델)이 같은 모델을 Mermaid로 담고 있다. 객체 11개, 관계 13개, 속성이 이 그림과 같다(2026-11-25 대조).

| ERD 객체 | 코드 (`src/ureonggaksi/`) | 비고 |
|---|---|---|
| Workflow, WorkflowVersion | `rules.py` `RuleStore`, `WorkflowVersion` | 규칙 v3. 같은 규칙은 다시 저장하지 않고, 수정하면 revision을 올린다 |
| Run | `inputs.py` `Inputs`, `analyze.py` `Plan`, `service.py` `Prepared` | 한 클래스가 아니라 셋에 나뉘어 있다 |
| RuleDraft | `rules.py` `RuleDraft` | `run_id` 필드는 없다(논리 참조) |
| Document | `inputs.py` `InputFile` | 프로토타입의 `Document`(읽은 HWPX)와 이름이 겹쳐 API에서는 `InputFile` |
| SourceRecord, ExtractedField | `records.py` `Record`, `FieldValue` | 분석 안에서만 쓴다. 밖에서는 `Evidence.record_key`, `extracted`로 보인다 |
| ChangeItem, Evidence | `analyze.py` `ChangeItem`, `Evidence` | `target_document_id`, `field_id`는 논리 참조. 대상 파일은 실행당 하나이고 근거는 `record_key`로 잇는다 |
| Approval | `save.py` `Approval` | 값 검토(check)에는 없다 |
| ResultPackage | `save.py` `ResultPackage`, `patch.py` `Check` | |

아직 없는 것(architecture.md 4절과 같음): 자세히 화면의 검토(`review`), 결과 다시 열기(`open_result`), 범위 지정(`SourceScope`), 원본 빼기, 칸 직접 처리 돌리기(`excluded`, `deselected`), 날짜 순서 질문(`date_order`), AiStatus의 `not_running`, `low_memory`. 이 가운데 원본 빼기, 직접 처리 돌리기, 날짜 순서, AiStatus 이유는 타입과 함수 일부가 코드에 있지만 서비스와 화면에 연결되지 않았다.

## 3. architecture.md와 다른 점

- **현재 객체 그림이 오래됐다.** `current-objects`는 2026-11-09 기준선의 7개 객체만 담는다. architecture.md 3.2절에는 그 뒤에 생긴 `CellValue`, `InputFile`, `Inputs`, `TargetCell`, `TargetView`와 `Block.tracked_change`가 더 있다. 현재 객체는 3.2절을 기준으로 본다.
- **목표 모델은 같다.** 차이는 표기뿐이다. 이 그림은 키와 참조를 함께 쓰는 속성을 `(PK, ref)`로, architecture.md는 `PK "논리 키"`로 적는다.
- **그림에 없는 계산 결과:** 직접 처리 목록(`ManualItem`), 질문(`Question`), 미리보기(`Prepared`)는 객체에서 계산하는 보기라 ERD에 넣지 않았다. 문서가 없는 행은 `ChangeItem` 없이 개수와 목록으로만 남는다.

## 4. 관계와 지켜야 하는 조건

관계별 수량은 아래와 같다. 실행 수명 전체 기준이고, 특정 단계에서 필요한 수량은 아래 조건으로 제한한다.

| 부모 → 자식 | 부모 1개당 자식 | 자식 1개당 부모 |
|---|---|---|
| Workflow → WorkflowVersion | 0..N | 1 |
| WorkflowVersion → Run | 0..N | 0..1 (새 요청은 저장 전까지 버전 없음) |
| Run → RuleDraft | 0..N | 1 |
| Run → Document | 0..N | 1 |
| Document → SourceRecord | 0..N | 1 (원본 HWPX만) |
| SourceRecord → ExtractedField | 0..N | 1 |
| Run → ChangeItem | 0..N | 1 |
| Document → ChangeItem | 0..N | 1 (대상 XLSX만) |
| ChangeItem → Evidence | 0..N | 1 (쓰는 변경은 1개 이상) |
| ExtractedField → Evidence | 0..N | 1 |
| Run → Approval | 0..1 | 1 |
| Approval → ChangeItem | 1..N | 0..1 (쓸 칸이 0개면 승인 없음) |
| Run → ResultPackage | 0..1 | 1 |

그림의 실선은 부모 키가 자식 키에 들어가는 식별 관계, 점선은 참조다. 막대는 1, 원은 0, 갈퀴는 여러 개다. 조건 8개는 [architecture.md 4.5절](../../architecture.md#45-지켜야-하는-조건)에 있다.

**단순화로 잃은 것:** 검토 결정을 여러 번 바꾼 이력과 무효가 된 승인의 이력은 남지 않는다. 요구사항은 최종 사유와 자동/수동 구분만 요구하므로 받아들였다.

## 5. 파일

- `.mmd`는 편집 원본(Mermaid `erDiagram`)이다. `.svg`는 같은 모델에서 만든 그림이다.
- `current-objects`의 출처 주석은 2026-11-09 코드 기준이다.
