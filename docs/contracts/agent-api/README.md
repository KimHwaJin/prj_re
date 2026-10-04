# Agent API JSON 예제와 Schema

[Agent API 문서](../../public-run-api.md)의 완전한 요청·응답·SSE payload 예제다. 2026-10-01 `0e35b62` 코드의 Pydantic 및 계획 편집 validator로 검증했다. 숫자·ID·시각·모델·데이터는 설명용이며 실제 서버 출력 또는 실제 계정이 아니다. 사내 SDK·LLM·DB·Executor를 호출한 예제가 아니다.

## 필드별 주석 읽기

각 `.json` 옆의 `.jsonc`는 중첩 객체·배열 내부 필드까지 한국어 주석이 붙은 설명용 사본이다. 주석을 제외한 데이터는 원본과 같다. 실제 API에는 `.json`을 사용한다. [전체 주석 목록과 갱신 방법](../field-comments.md)을 참고한다.

OpenAPI·payload schema에는 필드 `description`도 추가했다. 이 설명은 문서 annotation이며 서버 모델이나 검증 규칙을 바꾸지 않는다. 현재 코드에서 schema를 다시 추출한 뒤 주석 생성 스크립트로 설명을 갱신한다.

## 기계 판독 파일

- [openapi.snapshot.json](openapi.snapshot.json) · [필드 주석](openapi.snapshot.jsonc): 현재 app의 Run 9개 operation·관리자 진단2개 operation과 login/logout/me 및 프로젝트 memory GET/PUT/DELETE 및 사용자·프로젝트 목록/등록·단건 조회/수정/삭제. scoped 사본은 현재18paths/44models이며 전체 app 경로를 모두 포함하지 않는다. 실제 자동 OpenAPI의 SSE/redirect content annotation 제한을 보존한다.
- [payload-schemas.json](payload-schemas.json) · [필드 주석](payload-schemas.jsonc): RunRequest, RunCancel, PublicRunResource, AgentRunLogResource, RunDiagnosticsResource, RunInvocationResource, UserSummary, UserRead, ProjectCreate, ProjectUpdate, ProjectSummary, ProjectResource, RunEvent, PlanView 및 typed interaction schema. 이 객체의 key별 JSON Schema를 독립 schema로 읽는다.

## 요청

| 파일 | 액션 |
|---|---|
| [project_create.json](requests/project_create.json) · [필드 주석](requests/project_create.jsonc) | 프로젝트 생성 |
| [project_update.json](requests/project_update.json) · [필드 주석](requests/project_update.jsonc) | 프로젝트 지침 초기화 PATCH |
| [start.json](requests/start.json) · [필드 주석](requests/start.jsonc) | 새 요청 |
| [edit_plan.json](requests/edit_plan.json) · [필드 주석](requests/edit_plan.jsonc) | 계획 편집 |
| [approve_plan.json](requests/approve_plan.json) · [필드 주석](requests/approve_plan.jsonc) | 계획 최종 승인 |
| [replan.json](requests/replan.json) · [필드 주석](requests/replan.jsonc) | 자연어 재작성 |
| [answer_clarification.json](requests/answer_clarification.json) · [필드 주석](requests/answer_clarification.jsonc) | 추가 질문 답변 |
| [approve_decisions.json](requests/approve_decisions.json) · [필드 주석](requests/approve_decisions.jsonc) | 실행 결과 판단값 확인 |
| [approve_repair.json](requests/approve_repair.json) · [필드 주석](requests/approve_repair.jsonc) | 수정과 권한 상승 명시 승인 |
| [reject_repair.json](requests/reject_repair.json) · [필드 주석](requests/reject_repair.jsonc) | 수정 거절 |
| [cancel.json](requests/cancel.json) · [필드 주석](requests/cancel.jsonc) | 별도 cancel API body |

Run 요청 JSON은 단독 POST body다. project_create는 POST /projects, project_update는 PATCH /projects/{project_id}에 보내며 [프로젝트 계약](../../project-api.md)을 따른다. 요청 파일들을 배열로 묶어 제출하는 API가 아니다. 새 입력 또는 각 resume에 별도 Idempotency-Key를 적용하고 서버의 실제 ID/token/revision으로 교체한다. default-nce는 설명용 등록 데이터 참조로 실제 ANALYSIS_DATASETS에 같은 참조가 있어야 한다. replan/질문/decision/repair는 각기 다른 대기 화면 예제이며 하나의 실제 화면이 모든 action을 받는다는 뜻이 아니다.

## 응답과 이벤트

- [프로젝트 목록](responses/project_list.json) · [필드 주석](responses/project_list.jsonc): ProjectSummary의5개필드·페이지이며 지침을 읽지 않는다.
- [프로젝트 상세](responses/project_detail.json) · [필드 주석](responses/project_detail.jsonc): 지침·버전을 포함한 ProjectResource. 생성/PATCH도 같은 형식이다.
- [관리자 사용자 목록](responses/user_list.json) · [필드 주석](responses/user_list.jsonc): 공개 문자열 ID·권한·활성 여부의 요약 페이지. 기본 프로젝트·로그인 세션 정보는 없다.
- [삭제 사용자 상세](responses/deleted_user.json) · [필드 주석](responses/deleted_user.jsonc): 관리자가 조회하는 UserRead. 조회에서 복구하지 않는다. [사용자 계약](../../user-identity-api.md)을 따른다.
- [Run 목록](responses/run_list.json) · [필드 주석](responses/run_list.jsonc): PublicRunSummary 목록이며 결과/인터럽트/token이 없다. 해당 Run의 단건 조회로 상세를 받는다.
- [진단 로그 목록](responses/run_logs.json) · [필드 주석](responses/run_logs.jsonc): 소유한 공개 Run의 Agent/node별 진단 기록과 페이지 정보. 기본50·최대200개이며 일반 화면 진행은 SSE를 사용한다.
- [Run 내부 진단](responses/run_diagnostics.json) · [필드 주석](responses/run_diagnostics.jsonc): 최신 Task와 세션 전체 점유/차단 원인을 구분한다.
- [Run 실행 구간](responses/run_invocations.json) · [필드 주석](responses/run_invocations.jsonc): 최초 호출·resume의 invocation_id와 같은 공개 run_id를 제공하는 페이지 응답.
- [pending](responses/pending.json) · [필드 주석](responses/pending.jsonc), [waiting_input](responses/waiting_input.json) · [필드 주석](responses/waiting_input.jsonc): PublicRunResource 전체.
- [plan_review](events/plan_review.json) · [필드 주석](events/plan_review.jsonc), [planning_question](events/planning_question.json) · [필드 주석](events/planning_question.jsonc), [decision_review](events/decision_review.json) · [필드 주석](events/decision_review.jsonc), [repair_review](events/repair_review.json) · [필드 주석](events/repair_review.jsonc): typed interaction envelope.
- [message](events/message.json) · [필드 주석](events/message.jsonc), [plan_resolved](events/plan_resolved.json) · [필드 주석](events/plan_resolved.jsonc), [run_snapshot](events/run_snapshot.json) · [필드 주석](events/run_snapshot.jsonc): 메시지·화면 종료·현재 상태.

events 파일은 SSE data의 JSON이다. 실제 전송 시 저장 이벤트에 id/event/data 행과 마지막 빈 줄을 붙인다. snapshot은 id 없는 event/data로 전송한다. 예제 sequence는 형식 설명용이며 한 E2E 실행의 실제 이벤트 이력은 아니다.

## 검증 범위

9개 요청과 4종 HITL·계획 종료·Run 상태를 실제 Pydantic 계약으로 검증했다. 계획은 레포 등록 Skill/Tool 예제를 사용하여 순수 Workflow validator와 편집/승인 validator도 확인했다. decision/repair action은 해당 화면과 대조했다. 메타데이터와 schema 생성만 수행하며 Tool 실행·파일 로드·기업 인증·성능을 검증한 자료는 아니다.

Schema를 수정하면 current code에서 다시 생성하고 예제를 재검증해야 한다. OpenAPI만으로 SSE 및 유연한 최종 결과 payload를 완전히 복원할 수 없으므로 주 문서와 함께 사용한다.

075에서 프로젝트 공유 메모리 조회·전체 수정·초기화를 `/projects/{project_id}/memory` 하나의 경로로 통일했다. section/key 경로와 항목별 응답 모델은 제거했으며 문서 content/version을 사용한다. 기존 Run 요청·응답·SSE envelope 계약은 유지한다. [메모리 필드·설정·예제](../../project-memory.md)를 참고한다.

079에서 Run 목록을 PublicRunSummary로 분리하고 중복 join 경로를 삭제했다. 이 사본도 현재 scoped OpenAPI로 재생성했다. 상세 접수/조회/SSE와 로그 조회 계약은 유지했다.

082에서 관리자 사용자 목록을 추가하고 삭제 사용자 상세 조회를 허용했다. UserSummary와 UserRead 문서 schema는 응답 직렬화 모드로 추출하여 외부 필드 user_id를 유지한다. UserRead의 ORM 입력용 validation_alias public_user_id는 외부 응답 필드가 아니다.

083에서 프로젝트 목록을 ProjectSummary로 분리하고 생성/수정의 알 수 없는 필드·명시적 null을 거절했다. ProjectResource의 공개 id/name은 유지하며 프로젝트 관련 schema를 project_schema.py로 모았다. 문서용 읽기 schema는 serialization mode로 추출한다.


084에서 미사용 Jupyter registry·Redis ping4개 operation을 실제 앱/자동 OpenAPI에서 제거했다. 이 scoped OpenAPI snapshot은 원래 그 경로를 포함하지 않아18paths/44models 그대로이며 해당 schema·예제 변경도 없다. 현재 전체 앱은30paths다. [클라이언트·설정·기존 DB 이행](../../infrastructure-api-cleanup.md)을 따른다. 과거 보고서의34paths는 당시 snapshot을 뜻한다.
