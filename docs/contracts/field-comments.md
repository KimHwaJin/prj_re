# API·Workflow 필드 주석 안내

필드 의미를 바로 옆에서 읽을 수 있도록 모든 계약 예제와 기계 판독 명세에 `.jsonc` 설명용 사본을 둔다. 각 객체의 필드 앞 `//` 주석은 용도·다른 식별자와의 차이·사용 조건을 설명한다. 문서용 schema `properties`에는 필드 `description`과 형식상 필수/생략 가능 여부도 기록한다. 조건부 필수 여부는 API 업무 계약을 함께 따른다.

- `.json`: 파싱·검증·API 제출에 사용하는 원본. 설명용 ID·token·데이터는 실제 서버 값으로 바꾼다.
- `.jsonc`: 원본의 같은 데이터에 주석을 붙인 읽기용 파일. 그대로 JSON body로 전송하지 않는다.
- schema의 `description`: 문서 annotation. 요청 body에 설명 필드를 추가하라는 뜻이 아니다.
- 실행용 Workflow schema는 LLM 프롬프트에도 포함되므로 원문을 유지한다. 문서용 schema에만 설명을 추가하고 양쪽 검증 규칙의 동일성을 확인한다.

[Agent API](../public-run-api.md)와 [Workflow JSON](../workflow-json-reference.md)의 본문 예제에도 주석을 붙였다. JSONC의 값·키·배열 순서는 원본과 동일하도록 검증한다. 개별 예제는 서로 다른 상황의 설명이며 하나의 실제 실행 기록이 아니다.

## 주석 파일 전체

| 주석 파일 | 설명 대상 |
|---|---|
| [contracts/agent-api/events/decision_review.jsonc](agent-api/events/decision_review.jsonc) | 결과 판단 SSE·값 Schema |
| [contracts/agent-api/events/message.jsonc](agent-api/events/message.jsonc) | Agent 메시지 role/channel/content |
| [contracts/agent-api/events/plan_resolved.jsonc](agent-api/events/plan_resolved.jsonc) | 계획 승인 화면 종료·승인된 공개 계획 |
| [contracts/agent-api/events/plan_review.jsonc](agent-api/events/plan_review.jsonc) | 계획 확인 SSE·PlanView·파라미터 |
| [contracts/agent-api/events/planning_question.jsonc](agent-api/events/planning_question.jsonc) | 추가 질문 SSE |
| [contracts/agent-api/events/repair_review.jsonc](agent-api/events/repair_review.jsonc) | 오류 수정 SSE·권한·변경 단계 |
| [contracts/agent-api/events/run_snapshot.jsonc](agent-api/events/run_snapshot.jsonc) | SSE 현재 Run 상태와 cursor |
| [contracts/agent-api/openapi.snapshot.jsonc](agent-api/openapi.snapshot.jsonc) | API 경로·메서드·헤더/query·요청/응답 Schema·쿠키 인증 정의 |
| [contracts/agent-api/payload-schemas.jsonc](agent-api/payload-schemas.jsonc) | Run·계획·HITL 공통 모델의 모든 필드와 필수 여부 |
| [contracts/agent-api/requests/answer_clarification.jsonc](agent-api/requests/answer_clarification.jsonc) | 추가 질문 응답 |
| [contracts/agent-api/requests/approve_decisions.jsonc](agent-api/requests/approve_decisions.jsonc) | 결과 기반 판단값 확인 |
| [contracts/agent-api/requests/approve_plan.jsonc](agent-api/requests/approve_plan.jsonc) | 계획 선택·최종 승인 |
| [contracts/agent-api/requests/approve_repair.jsonc](agent-api/requests/approve_repair.jsonc) | 수정 제안·권한 상승 동의 |
| [contracts/agent-api/requests/cancel.jsonc](agent-api/requests/cancel.jsonc) | 취소 사유 |
| [contracts/agent-api/requests/edit_plan.jsonc](agent-api/requests/edit_plan.jsonc) | 계획 입력·인자·Step 제외·실행 정책 편집 |
| [contracts/agent-api/requests/reject_repair.jsonc](agent-api/requests/reject_repair.jsonc) | 수정 제안 거절 |
| [contracts/agent-api/requests/replan.jsonc](agent-api/requests/replan.jsonc) | 자연어 계획 재작성 |
| [contracts/agent-api/requests/start.jsonc](agent-api/requests/start.jsonc) | 새 사용자 입력·모델 선택 |
| [contracts/agent-api/responses/user_list.jsonc](agent-api/responses/user_list.jsonc) | 관리자 사용자 요약·권한·활성 상태·페이지 |
| [contracts/agent-api/responses/deleted_user.jsonc](agent-api/responses/deleted_user.jsonc) | 관리자용 삭제 사용자 상세·기본 프로젝트 null |
| [contracts/agent-api/responses/run_list.jsonc](agent-api/responses/run_list.jsonc) | 공개 Run 요약 목록·페이지 |
| [contracts/agent-api/responses/run_logs.jsonc](agent-api/responses/run_logs.jsonc) | Agent 실행 진단 로그·페이지 |
| [contracts/agent-api/responses/run_diagnostics.jsonc](agent-api/responses/run_diagnostics.jsonc) | Run의 내부 Task·세션 전체 작업·점유 진단 |
| [contracts/agent-api/responses/run_invocations.jsonc](agent-api/responses/run_invocations.jsonc) | Run 내부 실행 구간 이력·페이지 |
| [contracts/agent-api/responses/pending.jsonc](agent-api/responses/pending.jsonc) | 접수 대기 Run 전체 상태 |
| [contracts/agent-api/responses/waiting_input.jsonc](agent-api/responses/waiting_input.jsonc) | 사용자 확인 대기 Run·계획 화면 |
| [contracts/workflow/legacy-1.3.jsonc](workflow/legacy-1.3.jsonc) | 기존 Workflow 관리 API의 이전 형식 |
| [contracts/workflow/quality-basic.jsonc](workflow/quality-basic.jsonc) | 기본 Workflow 입력·로드·품질·보고서 연결 |
| [contracts/workflow/quality-conditional.jsonc](workflow/quality-conditional.jsonc) | 결과 판단·조건부 Tool 실행 Workflow |
| [design/agentic-workflow-contract/workflow-definition.schema.jsonc](../design/agentic-workflow-contract/workflow-definition.schema.jsonc) | 새 Workflow의 전체 정의·binding·조건·정책·산출물 규격 |

## 식별자와 버전을 구분하기

| 필드 | 무엇을 식별하거나 갱신하는가 |
|---|---|
| run_id | 사용자 요청부터 결과까지 이어지는 공개 실행 |
| invocation_id | 최초 실행·각 resume마다 만들어지는 내부 실행 구간 |
| session_id | 대화 세션 |
| checkpoint_run_id / task_id | 내부 상태 저장·업무 연결 |
| workflow_id / definition_version | 재사용 정의와 그 변경 버전; 기존 관리 API의 DB UUID는 별도 |
| plan_id / plan_revision | 특정 계획 후보와 편집 버전 |
| interaction_id / revision | 현재 HITL 확인 화면과 화면 버전 |
| resume_token | 현재 재개 대상 확인; 로그인 토큰이 아님 |
| proposal_sha256 | 사용자에게 승인받은 특정 수정안 |
| sequence / Last-Event-ID | 저장 SSE 이벤트 순번과 처리 완료 위치 |
| snapshot.cursor | 상태 snapshot의 조회 기준; 모든 이벤트 처리 완료를 의미하지 않음 |

## 갱신 방법

예제 JSON이나 schema를 수정한 뒤 레포의 다음 스크립트를 실행한다. 서버 Pydantic 모델을 바꾼 경우 먼저 최신 OpenAPI/payload schema 사본을 추출해야 한다. 이 스크립트는 서버 모델 추출·실행·배포를 하지 않는다.

```sh
python scripts/design/annotate_public_contracts.py
```

스크립트는 설명 등록이 없는 새 필드가 발견되면 실패한다. 같은 철자의 필드도 문맥에 따라 구분하고, schema 설명 추가 전후의 검증 규칙과 JSONC 주석 제거 전후의 데이터를 대조한다. 주석 목록에 새 설명을 추가한 뒤 해당 API·Workflow 예제와 검증을 함께 갱신한다. 주요 문서의 JSONC 블록도 같은 설명으로 갱신한다.
