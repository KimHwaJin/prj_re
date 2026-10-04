# FK·인덱스·삭제 정책 검토 — 2026-10-04

현재 프로젝트는 **FK를 일괄 제거하는 방향을 권장하지 않는다.** 실행·대화·명령의 같은 DB 내부 참조에는 실제 보호 효과가 있고, FK 제거가 현재 처리량 병목을 줄인다는 측정 근거는 없다. 성능 후보는 `task_events` 중복 인덱스부터 검토하고, 누락된 역참조 인덱스는 실제 물리 정리·조회 패턴에 맞춰 선택한다.

이번 작업은 검토·격리 DB 확인·문서화다. `src`, 설정, DDL/migration은 변경하지 않았다. 변경안 구현·속도 전후 비교·운영 배포·베이스 병합·원격 push를 수행하지 않았다.

## 검토 범위와 증거

- 검토 소스: `dbfc002`, 브랜치 `feature/foreign-key-review`. 주요 소스 SHA256은 [source-audit.json](source-audit.json).
- API SQLAlchemy 모델의 FK 36개, CRUD+Worker 최신 migration을 적용한 실제 DB의 FK 40개. Worker event ledger 2개와 Workflow 실행 이력 2개가 추가된다. 실제 다른 DB를 쓰는 배포에서 이 40개가 모두 한 DB에 존재한다고 주장하는 수치는 아니다.
- API 36개 FK의 child/parent 컬럼·ON DELETE·deferrable·initially deferred 정의 차이 0개. **전체 테이블/타입/default/인덱스의 완전한 ORM schema diff 결과는 아니다.** `workflows.source_run_id`는 DB에서도 nullable임을 별도 확인했다.
- PostgreSQL 17.11의 새 전용 컨테이너에 CRUD head `20261003_0027`, Worker head `ew_0002` 적용. 기존 애플리케이션·Executor·Redis·데이터베이스는 사용하지 않았다.
- FK 실제 작동·허용 범위·물리 삭제·잠금에 대한 16개 SQL probe를 수행했다. 이는 부하시험/HTTP API 회귀가 아니며, SQL을 직접 쓴 fixture의 결과다. 서비스가 잘못된 연결을 실제로 생성한다는 증거로 해석하지 않는다.
- [inventory.json](inventory.json), [probes.json](probes.json), [supplement.json](supplement.json), [migration.log](migration.log)에 원본 보존.

## 우선 판단

| 순서 | 검토 결과 | 권장 방향 | 성능과의 관계 |
| --- | --- | --- | --- |
| 1 | `task_events(task_id,sequence)`에 UNIQUE와 일반 BTree 인덱스가 동시에 있음 | UNIQUE는 유지하고 일반 `ix_task_events_task_sequence` 제거 후보를 단독 검증 | 이벤트 INSERT마다 중복 인덱스 유지 비용을 줄일 가능성. 개선율은 미측정 |
| 2 | FK 역참조 전체 행 탐색을 지원하는 선두 인덱스가 없는 관계 7개 | 실제 DELETE/정리·조회 계획을 보고 선택 추가 | 지금 API 대부분은 soft delete. 7개 모두 추가하면 오히려 쓰기 비용 증가 가능 |
| 3 | Command·Run·Task·Log의 연결이 같은 Session/Run인지 FK만으로 보호되지 않음 | 공통 접수/claim/저장 경계에서 명시적으로 검증, 기존 경로 테스트 보완 | 정합성 검토 항목. FK 전면 제거/대규모 복합 FK 신설의 근거는 아님 |
| 4 | Log 물리 삭제가 SSE Event를 CASCADE로 제거 | 로그·공개 이벤트의 보존 단위를 함께 정하고 정리 구현에서 지킬 것 | 현재 정상 요청 속도 개선보다 이력 정책 문제 |
| 5 | Workflow의 출처 Run/Workflow에 RESTRICT 적용 | 장기 template 자산과 원본 실행 이력의 수명을 분리할지 후속 결정 | 장기 정리 차단 가능성. 현 soft delete 흐름의 결함으로 단정하지 않음 |
| 6 | `llm_runs`, `workflow_execution_logs` 신규 writer 미발견 | 이력·guard·관계 이행을 확인한 뒤 테이블 단위로 정리 | 사용되지 않는 FK만 지워도 현재 실행 쓰기 비용은 줄지 않음 |

이 순서는 이번 검토 내부의 권고다. 기존 처리량 우선순위/Agent·Workflow 후순위/운영 보완 보류를 변경하지 않는다.

## FK가 현재 보호하는 것

Project·Session·Message·Run·Task·Command의 부모가 사라진 상태로 자식이 생기는 것을 막는다. 격리 probe에서 존재하지 않는 User의 Project와 존재하지 않는 Session의 Message는 SQLSTATE `23503`으로 거절됐다. Session의 현재 leaf는 복합 FK여서 **다른 Session의 Message 지정도 거절**한다. 이 보호는 앱 외 배치/마이그레이션/실수로 실행한 SQL에도 적용된다.

현재 서비스의 SELECT 횟수와 FK 검사는 별개다. [074 진단](../sqlalchemy-cost-2026-10-04/README.md)의 Log/Event 410 SELECT·Task 연결 230 SELECT는 서비스가 명시적으로 실행한 조회다. FK를 삭제해도 그 SELECT가 자동으로 사라지지 않는다. 반대로 INSERT/참조 변경/부모 물리 삭제에는 PostgreSQL 내부 참조 검사와 잠금 비용이 있다. 이번 검토는 그 비용의 처리량 비중을 측정하지 않았다.

단일 Session의 활성 Task 제한과 요청 멱등성도 FK가 아니다. `uq_tasks_session_active` 부분 UNIQUE, Run/Task idempotency UNIQUE, 지속 owner/claim 및 transaction 경계가 담당한다. FK를 바꾸면서 이 보호를 제거하면 안 된다.

## 성능 후보: 정확히 중복된 인덱스

실제 카탈로그에는 다음 두 인덱스가 존재한다. 컬럼 순서·BTree 방식·전체 행 대상·포함 컬럼 없음이 같고, 차이는 UNIQUE 여부다.

```sql
CREATE UNIQUE INDEX uq_task_events_task_sequence
  ON task_events USING btree (task_id, sequence);
CREATE INDEX ix_task_events_task_sequence
  ON task_events USING btree (task_id, sequence);
```

UNIQUE 인덱스는 sequence 중복을 막으면서 해당 키 조회도 지원한다. 제거 후보는 **일반 인덱스 한 개**다. FK·UNIQUE·event sequence 할당을 지우는 안이 아니다. 현재 public SSE 쿼리는 Run과 join하므로 이 두 인덱스만 보고 모든 조회가 최적이라고 단정하지 않는다.

후속 구현 시 기존 UNIQUE를 유지한 migration을 만들고, 대표 replay/SSE 질의의 `EXPLAIN (ANALYZE, BUFFERS)` 및 신규·중복·동시 이벤트 저장 회귀를 확인한다. 동일 이벤트 수/동시성/DB 조건에서 profiler off로 비교하고 event 수·순서·중복·CPU·완료 시간·쓰기 비용을 함께 확인한다. 이 검토에서는 인덱스를 drop하지 않았다.

`agent_run_logs(run_id)`와 `(run_id,event_key)`, `workflow_tags(workflow_id)`와 `(workflow_id,tag)`처럼 선두만 겹치는 인덱스는 크기/선택도/질의가 달라질 수 있다. 같은 컬럼 전체가 중복인 위 사례와 동급으로 일괄 제거하지 않는다.

## 역참조 인덱스 검토 7개

기준은 유효한 전체 행 BTree 인덱스의 선두 컬럼이 FK equality 컬럼에 포함되는지다. 이것은 FK의 자식 행을 찾는 경로가 있다는 1차 구조 검사이며, 최적 query plan/실제 비용을 입증하지 않는다. 부분 인덱스는 전체 FK 관계를 덮지 않는 것으로 분류했다.

| FK child 컬럼 | 현재 상황 | 판단 |
| --- | --- | --- |
| `agent_commands.invocation_id` | 전체 행 선두 인덱스 없음 | Run 물리 정리나 invocation별 조회를 실제 수행할 때 우선 검토 |
| `agent_commands.session_id` | 미완료 명령만 포함하는 `agent_commands_session_order` 부분 인덱스 있음 | DONE 이력을 포함한 Session 물리 정리/전체 조회 비용과 용량을 보고 결정 |
| `agent_runs.agent_message_id` | 전체 행 선두 인덱스 없음 | Message 물리 정리/역조회가 없으면 긴급 추가 근거 부족 |
| `agent_runs.interpreted_message_id` | 전체 행 선두 인덱스 없음 | 해당 연결의 현재 사용/역조회와 물리 정리 정책을 함께 확인 |
| `project_members.user_id` | PK는 `(project_id,user_id)` | User 기준 전체 member 정리/조회에 `user_id` 선두 인덱스 후보 |
| `tasks.trigger_message_id` | 전체 행 선두 인덱스 없음 | Message 물리 삭제의 RESTRICT 검사 비용 점검. 일반 soft delete 병목과 구분 |
| `workflow_executions.catalog_id` | 전체 행 선두 인덱스 없음 | catalog 물리 삭제의 SET NULL·catalog별 실행 조회가 중요해질 때 검토 |

PostgreSQL의 FK 선언은 자식 쪽 인덱스를 자동으로 만들지 않는다. 다만 기존 PK/UNIQUE 인덱스로도 참조 검사를 지원할 수 있다. [PostgreSQL 17 FK 문서](https://www.postgresql.org/docs/17/ddl-constraints.html#DDL-CONSTRAINTS-FK).

`sessions(current_leaf_message_id,session_id)`는 별도 `current_leaf_message_id` 인덱스가 없지만 FK 검사 조건의 `session_id`가 Session PK이므로 후보가 최대 1행이다. 이를 단순 누락으로 분류하지 않았다. `llm_runs`의 Message 참조와 `task_events.agent_run_log_id`는 이미 UNIQUE 인덱스가 있으므로 중복 추가할 필요가 없다.

## 소속 일치: FK와 서비스 검증의 경계

실제 SQL로 아래 잘못된 연결을 만들어도 **현재 FK는 거절하지 않았다.** 두 ID가 각각 존재하기 때문이다.

| 직접 SQL로 확인한 연결 | FK가 놓치는 조건 |
| --- | --- |
| Session과 그 Project의 owner가 서로 다름 | owner 관계 일치 |
| Run의 Task가 다른 Session 소속 | `Run.session_id == Task.session_id` |
| Task root/checkpoint가 다른 Session Run | root/checkpoint의 소속·역할 |
| Event의 Task와 Run이 서로 다른 분석 작업 | 이벤트의 공통 실행 소속 |
| Event와 연결 Log가 서로 다른 Run | `Event.run_id == Log.run_id` |
| Command의 Session과 invocation의 Session이 다름 | `Command.session_id == Run.session_id` |

현재 접수는 같은 소스로 Command/Run/Task를 구성하며 trigger Message는 Session equality를 검사한다. 그러나 [claim.py](../../../src/api_service/runs/commands/claim.py)의 대기·선행 Command 판별은 Command.session을 쓰고 실제 Run owner는 Run.session을 쓴다. Run/Task를 잠근 뒤 세 소속을 명시적으로 대조하는 검사는 보이지 않는다. 다른 내부 writer/이행이 잘못된 행을 넣으면 두 기준이 어긋날 여지가 있다. **공개 API에서 이 불일치를 만들 수 있다는 재현은 아니다.**

권장안은 이미 읽고 잠근 행 사이의 일치를 공통 경계에서 확인하고, 잘못된 내부 행 fixture를 넣어 claim/저장을 거절하는지 검증하는 것이다. 전 테이블에 복합 FK를 추가하면 중복 UNIQUE/인덱스·순환 연결·이행 비용도 커지므로 지금 자동으로 확대하지 않는다. 서비스 관계 검사만으로 모든 DB 직접 변경을 차단할 수 있는 것은 아니며, DB까지 강하게 보호할 관계는 별도로 선택한다.

## soft delete·물리 삭제·순환 참조

현재 User/Project/Session 서비스는 `delete_yn`/`deleted_at`으로 숨기며 하위 Message/Session 등을 명시적으로 함께 숨긴다. Workflow도 `deleted_at`을 기록한다. `ON DELETE`는 실제 DELETE 시의 동작이므로 숨김 처리에는 실행되지 않는다. probe에서 Session만 숨겨도 자식 수는 그대로였다. FK는 부모가 숨김 상태인지도 판단하지 않는다.

물리 삭제를 직접 실행했을 때의 확인 결과:

| 삭제 대상 | 실제 동작 | 정책 판단 |
| --- | --- | --- |
| 참조된 trigger Message | `23503` 거절 | 실행 입력 근거 보호 유지 |
| AgentRunLog | 연결 TaskEvent까지 CASCADE; Task.last_event_sequence는 그대로 | 공개 SSE 보존 기간 내 Log만 선행 purge하지 않을 것. 단순 sequence gap이 언제나 오류라는 뜻은 아니지만 재생 원본이 실제 사라짐 |
| Task | Run은 남고 task_id=NULL, TaskEvent 삭제 | 운영 중 실행/HITL Task 임의 삭제 금지. 과거 taskless Run 읽기 호환과 정상 이력 보존 정책을 구분 |
| 공개 root Run | resume invocation 삭제, Task root/checkpoint=NULL | 공개 Run 전체 이력 묶음을 보존·정리 단위로 고려 |
| Workflow가 참조한 출처 Run | `23503` 거절 | 장기 template와 실행 이력의 수명을 분리할지 후속 결정 |

`TaskEventService.append`의 “FAQ 후 임시 Task hard-delete” 주석은 과거 호환 설명이다. 현재 소스에서 서비스의 Task hard delete 경로는 발견하지 못했고, `test_old_taskless_resume_cannot_bypass_executor_wait`는 구버전 행을 직접 재구성하며 **새 invocation은 Task history를 보존한다**고 명시한다. 이 주석을 현재 동작이라고 해석하거나 기존 taskless 호환을 검토 없이 제거하지 않는다.

Session↔Message, Task↔Run, public root/self, Workflow source/self의 순환·자기 참조는 실제 의미가 있다. 현재 admission은 Task 생성/flush→Run 생성/flush→Task root/checkpoint 연결→같은 transaction commit 순서를 사용한다. `post_update=True` 선언만으로 매 요청의 추가 UPDATE 비용을 확정할 수 없으며 실제 FK 비용과 ORM 관계 작업을 구분한다. `RESTRICT` 삭제 동작과 초기 deferred 참조 검사의 시점도 별개다.

Workflow 출처는 장기 보존 정책을 먼저 정한 뒤 snapshot+nullable `SET NULL` 같은 방식으로 바꿀 수 있다. 감사·역추적 계약이 줄어드는 변경이므로 이번 검토에서 FK만 제거하지 않았다. root/checkpoint FK는 CRUD Run 행만 보호하며 다른 DB의 LangGraph checkpoint/blob 존재는 보장하지 않는다.

## 레거시/서비스 경계

`llm_runs`와 `workflow_execution_logs`는 모델·관계는 있지만 `src`에서 신규 INSERT/writer는 발견하지 못했다. 전자는 resource_lifecycle의 미완료 LLM guard에도 포함된다. 기존 DB의 저장량/구버전 producer/외부 배치를 조사하지 않았으므로 “사용하지 않아 즉시 DROP 가능”으로 판단하지 않는다. 데이터 보존·읽기·guard 이행 후 테이블 전체를 정리하는 대상이며, FK만 삭제하는 성능 개선 대상이 아니다.

Worker migration의 `ew_*` 관계와 `workflow_executions`/`workflow_adaptive_history`는 ORM 모델 목록에 포함되지 않는다. 이 테이블들은 실제 DDL과 plain SQL repository 구현을 기준으로 검토했다. repository 구현의 존재가 현재 PlanningRuntime의 모든 요청에서 호출된다는 뜻은 아니다.

`session_executions.session_id`는 별도로 호스팅된 Agent Session도 점유에 참여하게 하려는 의도로 FK가 없다. Executor의 외부 execution_id, LangGraph Store/checkpoint의 참조도 CRUD DB FK로 억지로 묶는 대상이 아니다. 이 경계의 수명/고아 정리는 애플리케이션 계약·복구 정책으로 다룬다.

## 격리 검증 결과와 한계

16개 probe 모두 예상 SQLSTATE/행 변화와 일치했다. parent User child INSERT transaction을 열어 둔 상태에서 다른 연결의 non-key 이름 UPDATE는 성공했고 물리 DELETE는 lock_timeout=100ms에 `55P03`이었다. FK가 parent 삭제/키 변경과 경합할 수 있다는 확인이며 일반 요청의 지연 비중/100명 처리량을 뜻하지 않는다. [PostgreSQL 잠금 문서](https://www.postgresql.org/docs/17/explicit-locking.html#LOCKING-ROWS).

초기 inventory 도구가 Alembic version table 이름을 두 번 잘못 추정해 수정했고, 초기 probe fixture는 필수 log payload 누락(`23502`), 다음 fixture는 이미 사용 중인 Log/Event UNIQUE 위반(`23505`)으로 실패했다. source/schema는 바꾸지 않고 fixture만 수정한 뒤 최종 16개 확인을 통과했다. 실패 원본과 [수정 기록](diagnostic-repairs.json)을 보존했다. 이 진단 도구 오류를 서비스 회귀로 집계하지 않는다.

[executed/](executed/)에는 당시 실행한 스크립트를 원문 보존했다. 경로/port가 해당 격리 환경에 고정된 감사 자료이며, 운영 DB에서 그대로 실행하는 설치 도구가 아니다. 재현은 새 테스트 DB를 만들고 스크립트의 test DSN·작업/output 경로를 그 DB로 바꾼 다음 `setup.py`(두 Alembic head)→`inventory.py`→`probes.py` 순서로 실행한다. private-config 원문은 첨부하지 않았고 test-only 자격정보만 스크립트에 존재한다. 기존 DB 조사/production migration 적용을 수행하지 않았다.

각 schema probe는 transaction rollback으로 끝나며 잠금 probe의 전용 committed User도 삭제했다. 이후 격리 컨테이너만 제거하고 기존 18개 서비스를 보존했는지는 [cleanup.json](cleanup.json)으로 확인한다. 저장 증거 검산은 `python docs/reports/foreign-key-review-2026-10-04/verify.py`로 재실행할 수 있다.

후속 성능 작업은 중복 인덱스 단일 후보 또는 기존 074 SQL 구조 재사용 후보 중 하나를 선택하여 같은 조건으로 검증하면 된다. FK 일괄 제거·인덱스 7개 일괄 추가·소속 복합 FK 대량 신설을 한 작업에 섞지 않는다.

## 전체 40개 관계 판정

“유지”는 현 계약에서 FK를 보존한다는 뜻이며 모든 CASCADE/보존 정책이 최종 확정됐다는 뜻은 아니다. “테이블 전체 후속 검토”는 FK만 먼저 삭제하지 않고 테이블/이력/guard 이행을 함께 검토한다는 뜻이다. 구조 검사 인덱스 “있음”은 최적 계획을 보장하지 않는다.

| 번호 | 참조하는 컬럼 | 참조 대상 | 물리 DELETE 동작 | 전체 행 선두 인덱스 | 판정·이유 |
| --- | --- | --- | --- | --- | --- |
| 1 | `agent_commands.invocation_id` | `agent_runs.run_id` | CASCADE | **없음(7개)** | 유지·인덱스 조건부 — 없는 invocation 실행 금지. user 명령은 같은 session 검증 별도. 완료 명령 정리/Run 물리 삭제 비용 확인 후 인덱스 검토. |
| 2 | `agent_commands.session_id` | `sessions.session_id` | CASCADE | **없음(7개)** | 유지·인덱스 조건부 — 명령의 Session 실체 보호. active 전용 부분 인덱스만 있어 DONE 이력 정리는 별도 판단. |
| 3 | `agent_run_logs.run_id` | `agent_runs.run_id` | CASCADE | 있음 | 유지·정리 정책 — 실행 없는 Log 방지. Run 삭제와 로그 수명 일치; 공개 Event의 보존도 함께 검토. |
| 4 | `agent_runs.agent_message_id` | `messages.message_id` | SET NULL | **없음(7개)** | 유지·인덱스 조건부 — 표시 Message 연결은 선택값. Message 물리 정리 전 역참조 비용 점검. |
| 5 | `agent_runs.interpreted_message_id` | `messages.message_id` | SET NULL | **없음(7개)** | 유지·인덱스 조건부 — 결과 해석 Message 연결은 선택값. 현재 쓰기 여부/이력 필요성도 함께 점검. |
| 6 | `agent_runs.session_id` | `sessions.session_id` | CASCADE | 있음 | 유지 — 실행 소속의 핵심. Session 숨김은 FK가 처리하지 않음. |
| 7 | `agent_runs.trigger_message_id` | `messages.message_id` | RESTRICT | 있음 | 유지 — 실행 근거 Message 보호. 접수 코드가 같은 Session인지 별도 검사. |
| 8 | `agent_runs.public_run_id` | `agent_runs.run_id` | CASCADE | 있음 | 유지·정리 정책 — public root 존재 보호. root 물리 삭제 시 resume도 삭제; 공개 Run 전체 보존 단위로 관리. |
| 9 | `agent_runs.task_id` | `tasks.task_id` | SET NULL | 있음 | 유지·정리 정책 — 여러 invocation과 Task 연결. Task 삭제 시 Run은 남고 연결만 NULL; 구버전 taskless 이력과 호환. |
| 10 | `ew_commands.namespace,event_id` | `ew_inbox.namespace,event_id` | NO ACTION | 있음 | 유지 — 명령의 원본 수신 이벤트 보호. namespace까지 포함한 복합 FK. |
| 11 | `ew_outbox.namespace,command_id` | `ew_commands.namespace,command_id` | NO ACTION | 있음 | 유지 — outbox의 원본 명령 보호. 물리 정리는 outbox→command→inbox 순서로 설계. |
| 12 | `jupyter_servers.created_by_user_id` | `users.user_id` | SET NULL | 있음 | 유지 — 등록자 선택 참조. 서버 자산을 직원 계정의 물리 수명과 분리. |
| 13 | `llm_runs.assistant_message_id` | `messages.message_id` | CASCADE | 있음 | 테이블 전체 후속 검토 — 현재 쓰기 경로 미발견. 기존 이력 유지 여부 결정 전 FK만 제거하지 않음; UNIQUE 인덱스가 FK 탐색도 지원. |
| 14 | `llm_runs.session_id` | `sessions.session_id` | CASCADE | 있음 | 테이블 전체 후속 검토 — resource_lifecycle의 미완료 LLM 검사에 여전히 참여. 모델 제거와 guard 이행을 함께 검토. |
| 15 | `llm_runs.trigger_message_id` | `messages.message_id` | RESTRICT | 있음 | 테이블 전체 후속 검토 — 기존 입력 이력 보호. 현재 hot-path 병목으로 입증되지 않음. |
| 16 | `messages.session_id` | `sessions.session_id` | CASCADE | 있음 | 유지 — 대화 소속의 핵심; 단독 고아 Message 방지. |
| 17 | `project_members.project_id` | `projects.project_id` | CASCADE | 있음 | 유지 — Project 구성원 관계의 실체 보호; 초기 owner member 행 생성에 사용. |
| 18 | `project_members.user_id` | `users.user_id` | CASCADE | **없음(7개)** | 유지·인덱스 조건부 — PK(project_id,user_id)의 두 번째 컬럼. User 물리 정리 시 user_id 선두 인덱스 검토. |
| 19 | `projects.user_id` | `users.user_id` | RESTRICT | 있음 | 유지 — 프로젝트 소유자 실체 보호. User 비활성화/숨김은 서비스 정책. |
| 20 | `sessions.current_leaf_message_id,session_id` | `messages.message_id,session_id` | RESTRICT | session PK로 1행 제한 | 유지 — 같은 Session의 leaf Message라는 조건까지 실제 보장. session_id PK로 child 후보 1행 제한. |
| 21 | `sessions.project_id` | `projects.project_id` | RESTRICT | 있음 | 유지 — Project 실체 보호. 소유자 일치는 이 단일 FK로 보장되지 않음. |
| 22 | `sessions.user_id` | `users.user_id` | RESTRICT | 있음 | 유지 — User 실체 보호. Project owner/Session owner 정책은 별도 검증. |
| 23 | `task_events.agent_run_log_id` | `agent_run_logs.log_id` | CASCADE | 있음 | 유지·CASCADE 정책 검토 — Log/Event 원자 연결. Log 물리 삭제가 공개 이벤트까지 제거하는 정책은 별도 확정. |
| 24 | `task_events.run_id` | `agent_runs.run_id` | CASCADE | 있음 | 유지 — 이벤트의 실행 실체 보호. Task·Log와 같은 Run인지 별도 검증. |
| 25 | `task_events.task_id` | `tasks.task_id` | CASCADE | 있음 | 유지·중복 인덱스 검토 — 이벤트의 Task 실체 보호. 동일(task_id,sequence) 중복 일반 인덱스가 별도 성능 후보. |
| 26 | `tasks.checkpoint_run_id` | `agent_runs.run_id` | SET NULL | 있음 | 유지·정리 정책 — checkpoint 기준 Run의 실체 보호. LangGraph checkpoint 본문 존재를 보장하는 FK는 아님. |
| 27 | `tasks.root_run_id` | `agent_runs.run_id` | SET NULL | 있음 | 유지·정리 정책 — 공개 root 기준 연결. checkpoint와 현재 값이 같아도 의미는 달라 단순 통합 금지. |
| 28 | `tasks.session_id` | `sessions.session_id` | CASCADE | 있음 | 유지 — 분석 작업과 Session 소속 보호. 동일 세션 활성 제한은 FK 아닌 별도 unique/owner 정책. |
| 29 | `tasks.trigger_message_id` | `messages.message_id` | RESTRICT | **없음(7개)** | 유지·인덱스 조건부 — 분석 시작 근거 보호. Message 물리 정리의 역참조 비용 확인 후 인덱스 검토. |
| 30 | `workflow_adaptive_history.execution_id` | `workflow_executions.execution_id` | CASCADE | 있음 | 유지 — 한 execution의 적응 이력 구성요소; 실행 삭제 시 함께 정리하는 현재 정책. |
| 31 | `workflow_embeddings.workflow_id` | `workflows.workflow_id` | CASCADE | 있음 | 유지 — 원본 Workflow 없는 임베딩 방지. 원본 삭제와 파생 검색 자료의 수명 일치. |
| 32 | `workflow_execution_logs.agent_run_id` | `agent_runs.run_id` | SET NULL | 있음 | 테이블 전체 후속 검토 — 현재 신규 writer 미발견. 감사 이력은 원본 Run 삭제 시 NULL로 분리되도록 정의. |
| 33 | `workflow_execution_logs.message_id` | `messages.message_id` | SET NULL | 있음 | 테이블 전체 후속 검토 — 화면 Message 삭제 후에도 감사 본문을 남기려는 선택 참조. |
| 34 | `workflow_execution_logs.task_id` | `tasks.task_id` | SET NULL | 있음 | 테이블 전체 후속 검토 — Task 물리 수명과 감사 본문의 수명 분리. |
| 35 | `workflow_execution_logs.workflow_id` | `workflows.workflow_id` | RESTRICT | 있음 | 테이블 전체 후속 검토 — 감사 이력이 Workflow 물리 삭제를 제한. 후속 Workflow 이행에서 테이블 단위 판단. |
| 36 | `workflow_executions.catalog_id` | `workflow_catalog.catalog_id` | SET NULL | **없음(7개)** | 유지·인덱스 조건부 — catalog 삭제 후 실행 snapshot은 보존. plain SQL 구현 존재; ORM 목록에 없다고 삭제 대상 아님. |
| 37 | `workflow_tags.workflow_id` | `workflows.workflow_id` | CASCADE | 있음 | 유지 — 원본 Workflow에 종속된 검색 태그. 원본 없는 태그 방지. |
| 38 | `workflows.created_by_user_id` | `users.user_id` | SET NULL | 있음 | 유지 — 등록자/권한/감사 선택 참조. 전역 template 접근 범위와는 별개. |
| 39 | `workflows.source_run_id` | `agent_runs.run_id` | RESTRICT | 있음 | 유지·보존 정책 검토 — Workflow 자산이 출처 Run 물리 정리를 막음. 원본 snapshot/출처 보존 정책 확정 후 SET NULL 등 검토. |
| 40 | `workflows.source_workflow_id` | `workflows.workflow_id` | RESTRICT | 있음 | 유지·보존 정책 검토 — 복제/승격 출처 보호. 장기 자산과 원본 물리 정리 수명의 분리 여부를 후속 결정. |
