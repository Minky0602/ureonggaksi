# 우렁각시 ERD 검수 초안

작성일: 2026-11-05 · 검수 단위: **PR-D01** · 상태: **단순화안 사용자 확정(2026-11-06)**.

> **2026-11-05 단순화(사용자 승인한 방향):** 제안 모델을 14개 객체·17개 관계에서 11개 객체·13개 관계로 줄였다. 이 앱은 DB 없이 메모리 위주로 동작하는 1인용 데스크톱이므로 관계형 DB 관습인 연결 테이블과 이력 객체를 걷어냈다.
> - ApprovalChange 연결 객체를 없애고 `Approval.approved_change_ids` 목록으로 대체했다.
> - ReviewDecision 이력 객체를 없애고 ChangeItem의 검토 필드로 옮겼다.
> - Verification 객체를 없애고 `ResultPackage.checks` 값 목록으로 옮겼다.
> - Approval은 실행당 현재 승인 0..1개만 둔다.
>
> PRD 요구사항(FR-12/14/16/17)은 모두 유지된다. 이전 14개안은 [이전 검사 기록](../../../../.agent-work/tasks/2026-11-05-erd-review/erd.checks.json)에서 확인할 수 있다.

목적은 기존 코드의 데이터 관계를 설명하고, [PRD v1.4](../../requirements/wooreonggaksi_functional_requirements_v1.4.md)의 MVP를 구현하기 전에 필요한 관계를 합의하는 것이다. 현재 제품은 로컬 Python 객체와 JSON/XLSX 파일을 사용한다. 관계형 DB·테이블·실제 PK/FK 제약은 없다. DB 도입은 이 ERD의 전제가 아니다.

현재 구조와 목표 구조를 별도로 그렸다. 목표 그림의 `(PK: 논리 제안)`은 논리 식별자이며 DB 제약을 구현했다는 뜻이 아니다. `(ref)`는 객체 식별자 참조 제안이다. API·구체 JSON 저장 구조·마이그레이션은 ERD 검수 다음 단계에서 정한다.

## 1. 현재 코드에서 확인한 관계

![현재 Python 객체 상세 관계](current-objects.png)

[확대 가능한 SVG](current-objects.svg) · [편집 가능한 Mermaid](current-objects.mmd).

| 현재 객체 | 근거 | 관계와 제한 |
|---|---|---|
| Block | `src/ureonggaksi/models.py:5-20` | 구역·XML 위치·텍스트·표/행/열·병합 범위. 단독 ID/PK 없음 |
| Document | `models.py:24-27` | path·sha256·blocks. 읽은 문서 하나가 블록 0..N개를 포함 |
| ExtractedValue | `models.py:31-42` | 추출값·상태·문자 범위. source는 Block 또는 None |
| DocumentResult | `models.py:46-54` | path·sha256·values·error. 읽기 실패 시 values가 빈 튜플일 수 있음 |
| Verification | `models.py:58-61` | 원본 대조 결과. source는 Block 또는 None. XLSX 저장 검증 객체가 아님 |
| Rule | `src/ureonggaksi/workflow.py:15-64` | label/cell/regex·위치·방향·그룹·필수 여부 |
| Workflow | `workflow.py:68-110` | 이름·rules·version. 유효 규칙은 1~100개, 이름 중복 거부. version=1은 저장 스키마 버전 |

실제 소스의 전체 dataclass 필드 목록은 Python AST에서 추출하여 [공통 모델](erd.model.json)의 `existing`에 기록했다. 그림은 필드 이름을 보존하고 타입을 생략했다. 타입은 JSON 모델과 Mermaid에 남아 있다. annotation은 런타임의 NULL·타입 제약 보장이 아니다.

현재 그림의 실선은 객체 포함·참조 관계다. 식별/비식별 DB 관계를 뜻하지 않는다. 포함 관계는 컨테이너 안의 슬롯을 설명하며, Python 인스턴스가 다른 컨테이너에 공유될 수 없다는 뜻은 아니다. 복사된 path/sha256/field 문자열에는 FK를 그리지 않았다. 계산 속성·메서드·UI 위젯·Job/Signals는 데이터 관계 범위에서 제외했다.

## 2. MVP 목표 논리 모델 — 제안

### 입력·규칙·원본 레코드

![MVP 입력·규칙 상세 논리 모델](mvp-input-rules.png)

[확대 가능한 SVG](mvp-input-rules.svg).

### 변경계획·승인·결과

![MVP 변경·승인·결과 상세 논리 모델](mvp-output-approval.png)

[확대 가능한 SVG](mvp-output-approval.svg).

전체 11개 객체·13개 관계의 편집 가능한 원본은 [mvp-logical.mmd](mvp-logical.mmd), 필드 타입·근거·선택성은 [erd.model.json](erd.model.json)의 `proposed`에 있다. 두 상세 그림에 반복한 Run/Document/ExtractedField는 같은 객체다. 각 부분도에서 생략한 객체·관계는 [검사 기록](../../../../.agent-work/tasks/2026-11-05-erd-simplify/erd.checks.json)에 열거했다.

### 논리 식별자와 객체 책임

| 객체 | 제안 논리 키 | 책임·요구사항 근거 |
|---|---|---|
| Workflow | workflow_id | 재사용 규칙의 이름·정체성. FR-08 |
| WorkflowVersion | (workflow_id, revision) 복합 키 | 수정 이력·규칙 스냅샷(만든 한 줄 요청 `request`, 작업 종류 `work_type`, 질문 답 `policy` 포함, 2026-11-09 API 3판). schema_version은 저장 형식이며 revision과 구분. FR-07/08/14 |
| Run | run_id | 한 번의 입력 연결·분석·검토·저장 상태. FR-01/18/20 |
| RuleDraft | draft_id | 같은 실행 안의 직접 선택/AI 후보. 사용자 확인 전 실행 규칙 아님. FR-04~07 |
| Document | document_id | 실행에 연결된 source/target 파일과 지문·읽기 상태. FR-01~03/19 |
| SourceRecord | record_id | 한 원본 안의 사업 단위 경계와 문자열 키. FR-09 |
| ExtractedField | field_id | 원문값·정규화값·단위·위치. FR-09/10 |
| ChangeItem | change_id | 대상 셀 하나의 기존값·예상값·선택과 최신 검토 결정(보정값·이유). FR-11~14, FR-12 |
| Evidence | evidence_id | 필드→변환→매칭→셀 변경의 근거. FR-17 |
| Approval | run_id | 실행의 현재 저장 승인. 승인 변경 목록과 입력·규칙·계획·검토·출력 위치 지문을 하나로 묶는다. FR-14 |
| ResultPackage | run_id | 한 실행의 XLSX·JSON(값 검토는 XLSX 없이 JSON과 대조표, Approval 없음. 2026-11-09), 검사 결과 목록(`checks`), 검증·공개 상태. FR-16/17/20 |

엔티티와 속성의 기존 기획 근거는 [개념 데이터 모델 2.1~2.7절](../우렁각시_데이터_모델.md)에 있다. 다음은 이번 초안에서 명시화한 **제안**이며, 현재 구현 사실로 표시하지 않았다.
- `RuleDraft.run_id`, 실행별 Document 연결, `ChangeItem.target_document_id`
- 검토 필드의 ChangeItem 흡수, `Approval.approved_change_ids`
- 작성 중 ResultPackage와 그 `checks`

**단순화로 잃는 것:** 검토 결정을 여러 번 바꿨을 때의 이력과 무효화된 승인의 이력이 남지 않는다. PRD는 최종 사유와 자동/수동 구분만 요구하므로 MVP 범위에서 허용한다. 이력이 필요해지면 그때 객체로 분리한다.

### 관계·선택성

아래 수량은 준비·실패를 포함한 실행 수명 전체의 관계다. 분석·저장 같은 특정 단계의 필수 수량은 후술한 서비스 불변조건으로 제한한다.

| 부모 → 자식 | 부모 1개당 자식 | 자식 1개당 부모 | 관계 근거 |
|---|---|---|---|
| Workflow → WorkflowVersion | 0..N | 1 | 새 규칙의 첫 버전 저장 전 허용, FR-08 |
| WorkflowVersion → Run | 0..N | 0..1 | 준비 중 버전 미선택 허용, 분석 시 1개 필수 |
| Run → RuleDraft | 0..N | 1 | 후보를 현재 실행의 선택 범위에 연결하는 제안 |
| Run → Document | 0..N | 1 | 준비 중 파일 미선택 허용, 파일 연결은 실행별로 분리 |
| Document → SourceRecord | 0..N | 1 | source 역할 입력에서 사업 경계 탐색 |
| SourceRecord → ExtractedField | 0..N | 1 | 레코드 내 필드와 원문 위치 유지 |
| Run → ChangeItem | 0..N | 1 | 분석·검토 결과는 셀 단위 |
| Document → ChangeItem | 0..N | 1 | target 역할 문서의 셀만 대상이 됨 |
| ChangeItem → Evidence | 0..N | 1 | 미완성 계획은 0개 가능, 쓰기 가능한 변경에는 1개 이상 필수 |
| ExtractedField → Evidence | 0..N | 1 | 하나의 원본 필드가 여러 변경을 뒷받침 가능 |
| Run → Approval | 0..1 | 1 | 현재 유효한 승인만 둔다. 무효화되면 버리고 다시 승인받는다 |
| Approval → ChangeItem | 1..N | 0..1 | `approved_change_ids` 목록 참조. 쓰기 0건이면 승인·파일 생성 없음 |
| Run → ResultPackage | 0..1 | 1 | 실행당 1묶음 제안. 재시도/재승인은 새 실행 정책을 API 단계에서 확정 |

목표 그림의 실선은 부모 식별자가 자식 논리 키에 포함된 식별 관계, 점선은 비식별 참조 제안이다. 확정/미확정 상태를 선 스타일로 구분하지 않는다. 검은 막대·원·갈퀴가 1/0/N을 표시하며 관계선에는 이름을 반복하지 않았다.

### API 명세에 반드시 이어갈 불변조건

1. 준비 완료 후 분석하려면 source HWPX 1개 이상, target XLSX 정확히 1개, 확인된 규칙 버전 1개가 있어야 한다.
2. 모든 참조는 같은 Run 범위여야 한다. ChangeItem의 대상은 target 역할 문서이고 SourceRecord의 문서는 source 역할이어야 한다.
3. 여러 원본이 같은 셀에 같은 값을 제안하면 변경 1개·근거 여러 개로 묶고, 다른 값이면 충돌로 검토한다.
4. 쓰기 목록에는 근거가 있고 해결된 일반 셀 변경만 포함한다. 제외·변경 없음·미해결·수식/병합/보호 셀은 포함할 수 없다.
5. `Approval.approved_change_ids`는 해당 Run의 선택 변경만 담는다. 파일 지문·규칙·계획·검토·출력 위치가 바뀌면 기존 승인은 무효다.
6. 수동 보정은 원문값을 지우지 않으며 ChangeItem의 검토 필드에 이유를 남긴다. 모델 후보 확인과 최종 저장 승인은 구분한다.
7. ResultPackage가 존재해도 작성/검증 중·실패 상태이면 정상 결과로 공개하지 않는다. XLSX·JSON 및 필수 검증이 모두 통과해야 한다.
8. `checks`의 각 항목은 change_id 또는 전체 파일 범위를 명시한다. ‘확인됨’은 검증 범위 내 일치이며 행정적 사실의 진실성 보증이 아니다.

## 3. 저장·보존과 미결

규칙은 로컬 JSON, 분석·후보는 메모리 중심, 결과는 사용자가 선택한 XLSX+JSON으로 유지하는 방향이다. SQL DB, 자동 영구 작업 복원, 사용자/권한/조직 결재 테이블은 제안하지 않는다. 이는 같은 개인 사용자가 작업을 끝내는 제품이다.

상세 JSON Schema, 키 생성 규칙, 승인 해시의 정규 직렬화 방식, 오류 enum, 취소 경계, 재시도 시 새 Run 여부는 **API 명세 단계의 미결**이다. 근거 마스킹·보존·삭제 범위는 PRD OQ-05를 유지한다. 원본 문서/프롬프트 전체를 규칙·일반 로그에 저장하지 않는다.

## 4. 생성·검사 기록

- 사용 스킬: 로컬 `erd` 스킬. 물리 스키마가 없으므로 물리 FK 검사 도구를 적용하지 않았다.
- 실행(현재): 프로젝트 루트에서 `.\ureonggaksi\.venv\Scripts\python.exe .agent-work/tasks/2026-11-05-erd-simplify/generate_erd.py`. 이 스크립트는 최초 생성 스크립트(`2026-11-05-erd-review/`, 이동 전 경로 기준이며 보존)를 복사한 것이다. 출력 경로를 `ureonggaksi/docs/planning/erd`로 고쳤고 단순화된 제안 모델을 담았다. 재실행 결과에서 `existing` 모델은 이전과 같았다.
- 재사용: 기존 Python AST 파서로 현재 dataclass 필드를 추출하고, 설치된 Graphviz 2.44.1의 `dot`으로 PNG/SVG를 렌더링했다. 모델링을 위한 새 런타임 패키지를 설치하지 않았다.
- 출처·라이선스: Python 3.12.9의 표준 [ast](https://docs.python.org/3.12/library/ast.html)를 사용하며 [해당 버전 LICENSE](https://github.com/python/cpython/blob/v3.12.9/LICENSE)를 참조한다. Graphviz는 설치 버전 2.44.1의 [COPYING](https://gitlab.com/graphviz/graphviz/-/raw/2.44.1/COPYING)이 가리키는 [EPL-1.0](https://gitlab.com/graphviz/graphviz/-/raw/2.44.1/epl-v10.txt)을 확인한다. 실행 도구의 코드를 복사·수정·제품에 번들하지 않고, CLI를 이용해 자체 모델의 그림을 생성했다. 최신 웹페이지의 라이선스를 과거 설치 버전에 자동 대입하지 않았다.
- 편집 원본: `erd.model.json`, `current-objects.mmd`, `mvp-logical.mmd`. SVG/PNG는 같은 모델에서 생성했다.
- 내부 일관성: 현재 7개 객체·5개 관계, 제안 11개 객체·13개 관계의 객체 이름·키 필드·참조 대상·근거 존재 검사를 통과했다. 이는 업무 의미의 자동 검증이 아니다.
- 시각 검사: 최종 PNG 3개를 열어 한글, 상자 밖 객체명, 논리 키 구획, 관계 수 기호, 주요 속성의 가독성을 확인했다. 타입은 그림에서 생략하고 모델/원본에 보존했다.
- 렌더러 한계: 직각 경로 모드에서 관계 수 장식이 뒤바뀌는 문제를 확인하여 **꺾은선/대각선 경로**로 렌더링했다. 일부 공통 출발점의 선이 가까우므로 개별 선택성은 위 관계표와 함께 확인한다. 원본 모델의 관계는 바꾸지 않았다.
- Mermaid: `mmdc`가 없어 실제 Mermaid 문법 렌더링은 **미검증**이다. Graphviz 렌더링 성공을 Mermaid 문법 검증 성공으로 표기하지 않는다.
- 실행·생성·검사 상세는 [최초 작업 기록](../../../../.agent-work/tasks/2026-11-05-erd-review/review-record.md)과 [단순화 검사 JSON](../../../../.agent-work/tasks/2026-11-05-erd-simplify/erd.checks.json)에 있다.
- 단순화 후 시각 검사: `mvp-output-approval.png`를 열어 7개 객체, 관계 기호, 한글 가독성을 확인했다.

## 5. 이번 검수와 다음 단계

검수할 부분은 **단순화로 잃는 이력 2가지(검토 결정 이력, 무효 승인 이력)를 MVP에서 버려도 되는지**, **규칙 버전/실행의 분리**, **실제 DB 없는 논리 참조**다.

요청한 PR별 검수 순서에 따라 이 ERD를 확인받은 다음 **PR-D02 API 계약 명세**를 작성한다. API 계약 초안도 검수받은 뒤 제품 구현 PR을 하나씩 착수한다. 전체 단계는 [개발 현황과 PR 계획](../우렁각시_개발현황과_PR계획.md)에 있다.