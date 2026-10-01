# Agent API JSON 예제와 Schema

[Agent API 문서](../../public-run-api.md)의 완전한 요청·응답·SSE payload 예제다. 2026-10-01 `0e35b62` 코드의 Pydantic 및 계획 편집 validator로 검증했다. 숫자·ID·시각·모델·데이터는 설명용이며 실제 서버 출력 또는 실제 계정이 아니다. 사내 SDK·LLM·DB·Executor를 호출한 예제가 아니다.

## 기계 판독 파일

- [openapi.snapshot.json](openapi.snapshot.json): 현재 app의 Run 8개 operation과 login/logout/me. 실제 자동 OpenAPI의 SSE/redirect content annotation 제한을 보존한다.
- [payload-schemas.json](payload-schemas.json): RunRequest, RunCancel, PublicRunResource, AgentRunLogResource, RunEvent, PlanView 및 typed interaction schema. 이 객체의 key별 JSON Schema를 독립 schema로 읽는다.

## 요청

| 파일 | 액션 |
|---|---|
| [start.json](requests/start.json) | 새 요청 |
| [edit_plan.json](requests/edit_plan.json) | 계획 편집 |
| [approve_plan.json](requests/approve_plan.json) | 계획 최종 승인 |
| [replan.json](requests/replan.json) | 자연어 재작성 |
| [answer_clarification.json](requests/answer_clarification.json) | 추가 질문 답변 |
| [approve_decisions.json](requests/approve_decisions.json) | 실행 결과 판단값 확인 |
| [approve_repair.json](requests/approve_repair.json) | 수정과 권한 상승 명시 승인 |
| [reject_repair.json](requests/reject_repair.json) | 수정 거절 |
| [cancel.json](requests/cancel.json) | 별도 cancel API body |

각 JSON은 단독 POST body다. 요청 파일들을 배열로 묶어 제출하는 API가 아니다. 새 입력 또는 각 resume에 별도 Idempotency-Key를 적용하고 서버의 실제 ID/token/revision으로 교체한다. default-nce는 설명용 등록 데이터 참조로 실제 ANALYSIS_DATASETS에 같은 참조가 있어야 한다. replan/질문/decision/repair는 각기 다른 대기 화면 예제이며 하나의 실제 화면이 모든 action을 받는다는 뜻이 아니다.

## 응답과 이벤트

- [pending](responses/pending.json), [waiting_input](responses/waiting_input.json): PublicRunResource 전체.
- [plan_review](events/plan_review.json), [planning_question](events/planning_question.json), [decision_review](events/decision_review.json), [repair_review](events/repair_review.json): typed interaction envelope.
- [message](events/message.json), [plan_resolved](events/plan_resolved.json), [run_snapshot](events/run_snapshot.json): 메시지·화면 종료·현재 상태.

events 파일은 SSE data의 JSON이다. 실제 전송 시 저장 이벤트에 id/event/data 행과 마지막 빈 줄을 붙인다. snapshot은 id 없는 event/data로 전송한다. 예제 sequence는 형식 설명용이며 한 E2E 실행의 실제 이벤트 이력은 아니다.

## 검증 범위

9개 요청과 4종 HITL·계획 종료·Run 상태를 실제 Pydantic 계약으로 검증했다. 계획은 레포 등록 Skill/Tool 예제를 사용하여 순수 Workflow validator와 편집/승인 validator도 확인했다. decision/repair action은 해당 화면과 대조했다. 메타데이터와 schema 생성만 수행하며 Tool 실행·파일 로드·기업 인증·성능을 검증한 자료는 아니다.

Schema를 수정하면 current code에서 다시 생성하고 예제를 재검증해야 한다. OpenAPI만으로 SSE 및 유연한 최종 결과 payload를 완전히 복원할 수 없으므로 주 문서와 함께 사용한다.
