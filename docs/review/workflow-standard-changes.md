# 받은 Workflow 1.0 초안 대비 변경 추적

2026-10-05. 브랜치 feature/workflow-standard-contract. 기준 commit4ecc416. 변경 전 원본과 확정 문서를 구분한다. 원본 파일은 [보존 디렉터리](../contracts/workflow-standard/original-1.0/), 파일별 지문은 [SHA256.json](../contracts/workflow-standard/original-1.0/SHA256.json), 변경 후 작성 문서는 [확정 규격2.0](../workflow-standard.md)이다.

버전은2.0으로 정했다. 배열 index 참조·실행 모드·입력 식별 방식·승인 표시를 바꾸므로1.0과 호환되는 작은 변경으로 표시하지 않는다. 이미 외부에서1.0을 쓰는 경우 명시적인 이행이 필요하다. 자동 번역으로 임의 조건/출력 의미를 만들지 않는다.

| 변경 ID | 원본 위치/규칙 | 변경 후 | 이유 | 구현·검증 |
|---|---|---|---|---|
| W01 | 원본 명세 L31: workflow_version=1.0 | 공개2.0. 내부 승인 계획 버전과 구분 | 파괴적 변경을 명확하게 식별 | standard_schema/normalize; version 거절 시험 |
| W02 | 원본 명세 L33–35: workflow.actor.type/id 필수 | 공개 정의에서 제거. DB created_by_user_id/source_run_id로 작성자·실행 출처 관리 | 재사용 정의에 특정 실행 사용자/Agent 권한을 섞지 않음 | WorkflowService, PostgreSQL 직접 등록·다른 사용자 조회 시험 |
| W03 | 원본 명세 L36–37: workflow.name/user_request | 유지. description 선택 추가 | 이름/목표와 별도 설명 지원. 생략 시 user_request로 보충 | normalize, API metadata/body 일치 시험 |
| W04 | 원본 명세 L38, 54–55: execution_mode static/adaptive 필수. conditional 유무로 구분 | 저작 필드 제거. when/decisions로 static/adaptive 계산 | 모든 Tool이 실행되어도 결과 기반 파라미터 판단은 가능 | classification; conditional 없는 Agent 판단 시험 |
| W05 | 원본 명세 L39, 57: allow_immediate_execution | 저작 필드 제거. 등록 Workflow는 기본 최종 사용자 승인 | 파일의 표시가 승인 권한으로 오해되지 않게 함 | new_review/patch_review/freeze_approval, graph 승인 전 제출 없음 시험 |
| W06 | 원본 명세 L40–43: steps[].skill, tools[].tool | 유지. 각 Skill 그룹과 Tool 호출에 id 추가 | 같은 함수를 여러 번 써도 개별 호출을 식별 | duplicate ID/소속 검증; 편집·제외 시험 |
| W07 | 원본 명세 L52, 67: tool_output.step_index/tool_index | call_id 고정 참조 | 배열 편집으로 데이터 대상이 조용히 바뀌는 문제 방지 | normalize 선행/존재 검사, 출력 참조·삭제 시험 |
| W08 | 원본 명세 L49, 66, 69, 73: input은 위치+인자 이름으로 식별 | inputs의 명명된 정의 + source=input/name | 입력 Schema/기본값/편집과 공유 여부를 명시 | unknown input/기본값/승인 값 시험. 별개 입력에는 별개 이름 사용 |
| W09 | 원본 명세 L61, 63–67: literal/input/tool_output만 지원 | 유지 + agent_decision/system_context | 결과 기반 판단과 서버 실행 문맥을 사용자 입력과 분리 | 판단 대기·허용 값 검증·graph 후속 Operation 시험 |
| W10 | 원본 명세 L44, 53, 56: execution=always/conditional, 조건은 Skill을 따름 | execution 제거. when이 없으면 실행, 있으면 구조화 조건 평가. 의미 판단은 decisions로 선언 | Markdown 조건의 실행 대상/근거를 기계적으로 연결 | condition_value, 조건부 생략 뒤 독립 tail 실행 시험 |
| W11 | 원본 명세 L56: 조건에 쓰는 Tool·판단 시점 불명확 | decisions.after_calls/instruction/output_schema | 반복 호출 중 어느 결과를 근거로 무엇을 정하는지 명시 | 선행 근거·조건부 근거 제한·판단값 시험 |
| W12 | 원본 명세 L67: 등록된 출력 이름 output | 유지 + Tool.outputs 별칭→selector[] 계약 | 전체 반환값/중첩 dict/list를 동일 방식으로 연결 | tool_outputs, AssetCatalog, 두 독립 풀 시험 |
| W13 | 원본 명세 L51: 배열 순차 실행 | 유지. 내부 ordered_call_ids로 순서 장벽 구현 | 순서 의존을 데이터 필수 의존으로 바꾸면 생략이 잘못 전파됨 | compiler.ready_batch; 판단 미확정 tail 대기/false 후 진행 시험 |
| W14 | 원본 명세 L71: 조건부 출력 연결은 생략되어도 유효해야 함 | 생산자와 소비자에 같은 명시 guard 요구. 대체 출력 자동 생성 없음 | 현재 지원하는 분기 의미를 타이트하게 제한 | validate_plan; guard 없는 소비자 거절, 같은 guard 허용 시험 |
| W15 | 원본 명세 L72: 함수 선택 인자 생략 시 기본값 | 유지. Tool 정책이 허용하는 편집용 기본값만 승인 계획에 구체화 | 원래 함수 기본값/null/미정 값을 혼동하지 않음 | materialize_defaults, 코드 제출·기존 회귀 |
| W16 | 원본 명세 L7–25, 29–45: 기대 산출물/실행 정책 없음 | expected_outputs 필수, execution 선택 추가 | E2E 목표·보고서 근거와 재사용 정책을 표현 | 기존 report/execution 검증 재사용; graph Finalize/terminal/report 시험 |
| W17 | 원본 명세 L3–5 (API 규칙은 미정의): CRUD·수정 버전 규칙 없음 | 직접 POST, SHA 기반 낙관적 수정, resource UUID+SHA 파일 경로로 이전 파일 보존, 실행 성공 없이 승격 | 분석가 직접 등록과 이전 승인 내용 보존 | WorkflowService; 격리 PostgreSQL lifecycle 시험 |

표의 L은 보존한 [workflow_spec_1.0.md](../contracts/workflow-standard/original-1.0/workflow_spec_1.0.md)의 1부터 시작하는 줄 번호다. 필드 경로는 해당 원본 Schema와 대조할 수 있다. W16/W17은 초안에 없던 추가 사항으로, 수정한 기존 필드와 구분한다.

위 항목은 구현했다. Skill의 자연어 지침과 Workflow의 업무적 타당성이 완전히 일치하는지는 JSON Schema로 증명하지 않는다. Skill은 그대로 Markdown에 두고 승인 시 함께 고정하며 Agent에 제공한다. 조건의 존재·출처·선행 근거·허용 판단값·실행 소속은 코드로 검증한다.

## 이행 방법

1. 원본 Step/Tool 호출마다 고정 ID를 부여한다.
2. index 참조를 당시 대상 호출 ID로 바꾼다. 재정렬 후 index에서 추측하지 않는다.
3. 사용자 입력마다 명명된 inputs와 value_schema를 정의한다. 반복 데이터 요청을 자동 병합하지 않는다.
4. Tool 출력 이름의 실제 반환 selector를 등록한다. 기존 함수 본문은 바꾸지 않는다.
5. conditional의 실제 Skill 규칙을 확인하고 when/decisions로 명시한다. LLM으로 알 수 없는 조건을 만들어 채우지 않는다.
6. 기대 산출물과 필요한 실행 정책을 선언하고 현재 catalog로 의미 검증한다.
7. 승인 UI·실행 회귀 후 공개2.0으로 등록한다. 이미 승인한1.3/내부 계획은 별도로 보존한다. 함수 인자명에는 Workflow ID의 소문자 규칙을 적용하지 않고 실제 Python signature를 사용한다.

## 확인 가능한 검증 위치

- [표준 계약·실행 회귀](../../src/agent_service/agents/analysis/tests/test_workflow_standard.py)
- [PostgreSQL API 회귀](../../src/api_service/test/test_workflow_standard_postgres.py)
- [작업 완료·검증 기록](../improvements/092-workflow-standard-contract.md)

변경 전 정의는 이 파일이 아니라 보존 원본이다. 변경 후 작성/개발 기준은 확정 문서다. 추적표는 확정 규격을 대신하지 않는다.

## 092 이후 등록·추천 논의 추적 — 093

W01~W17은 위 구현 이력을 유지한다. 아래는 새 검색 요구·실측에 따른 제안이며 실행 정의2.0 수정/신규 API 구현 완료를 뜻하지 않는다. 상세는 [설계 검토](../design/workflow-retrieval-and-registration.md), [측정](../reports/workflow-retrieval-2026-10-05/report.html)를 따른다. 받은 원본 byte는 그대로 보존한다.

| ID | 이전 정의/설명 | 변경·추가 방향 | 이유·근거 | 상태 |
|---|---|---|---|---|
| R01 | W03의 user_request는 분석 목표, W17 POST는 document 중심 | envelope user_queries[]로 여러 검색용 요청을 한 resource에 연결. 실행 JSON 목표는 유지 | 사용자가 다중 요청→한 Workflow를 요구 | 제안·미구현 |
| R02 | 쿼리 row TOPK 뒤 중복 제거/고정 넉넉한 후보 수 | Workflow 단위 반환. 고정 배수로 그룹 개수/완전성 보장 금지 | 50개/WF 후보200도4WF, 500개/WF 후보1000도2WF | 반례 실측 완료·검색 정책 미확정 |
| R03 | 제외 WHERE로 후보를 반복 검색하면 충분할 수 있다는 논의 | iterative/활성 인덱스 후보 유지, 개수와 정확한 전역TOP5 분리 | 기본 제외1WF; iterative 경계Recall68%, 활성80% | 검증 완료·근사 허용 조건 결정 필요 |
| R04 | W17 정의 SHA로 수정 경합 감지 | 파일 SHA 유지+별도 resource/search revision·embedding 상태 검토 | 쿼리만 수정하면 정의SHA는 바뀌지 않음 | 제안·미구현 |
| R05 | 문서를 전체 Workflow 계약 확정으로 읽을 여지 | 실행 정의/현행CRUD와 미확정 추천·등록 계약을 명시 분리. 최종 검색API 문서는 구현 시 별도로 확정 | 실제 모델/차원·품질 목표·응답 상태 미정 | 문서 반영 완료·최종검색API 미작성 |
