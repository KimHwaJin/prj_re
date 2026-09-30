# Agent 로그와 이벤트 저장 계약

032는 `agent_run_logs`와 대응 `task_events(event_type=agent.event)` 사이의 저장 경계를 수정한다. 동일 Run의 로그 key를 재처리했을 때 로그와 SSE 저장 이벤트가 각각 하나로 수렴하도록 한다. LangGraph checkpoint 저장과 서비스 DB 저장을 하나의 transaction으로 묶는 변경은 아니다.

## 장애 원인과 새 경계

기존 코드는 로그를 먼저 commit하고 이벤트를 나중에 commit했다. 후자가 실패하면 로그만 남았으며, 같은 key로 재호출해도 기존 로그를 발견하고 바로 반환해 누락 이벤트가 복구되지 않았다.

새 경로는 로그의 `(run_id, event_key)` unique key와 이벤트의 `agent_run_log_id` unique 연결을 사용한다. 이미 완성된 쌍은 한 번의 연결 조회로 확인해 쓰기/행 잠금 없이 반환한다. 아직 완성되지 않은 쌍은 로그를 insert/on-conflict 처리하고 해당 로그 행을 잠근 뒤, 연결된 이벤트가 없을 때만 생성한다. 이벤트 순번 할당도 같은 transaction에 들어가며 한 번에 commit한다.

- **commit 이전 오류/취소:** 새 로그·이벤트·순번 증가가 함께 rollback된다.
- **commit 성공 뒤 응답 유실:** 같은 key 재처리 시 이미 저장된 쌍을 반환한다.
- **기존 로그만 존재:** 로그에 저장된 원래 내용으로 누락 이벤트를 만든다. 재시도 요청의 변경된 payload로 덮어쓰지 않는다.
- **동시 같은 key:** DB unique key와 로그 행 잠금으로 하나의 로그·이벤트에 수렴한다.
- **다른 key, 같은 내용:** 별도 로그·이벤트로 유지한다. payload 동일성으로 신규 이벤트를 중복 제거하지 않는다.
- **Run에 Task가 없음:** 기존 정책대로 로그만 저장한다. 이후 Task가 연결된 뒤 같은 key를 재처리하면 이벤트를 만들 수 있다.

`TaskEventService.append/append_for_run`에 내부용 optional `agent_run_log_id`를 추가했다. 일반 토큰/상태 이벤트는 기존대로 NULL이며, 공개 API 및 SSE payload 형식은 바뀌지 않는다. NOTIFY는 DB commit에 맞춰 전달되므로 실패한 transaction의 알림만 별도로 확정되지 않는다.

LLM이나 Executor 응답을 기다리는 동안 이 transaction을 유지하지 않는다. 그래프 결과를 서비스 DB에 반영하는 짧은 구간만 잠근다. 메시지·다른 로그·최종 Run 상태까지 전부 하나의 거대한 transaction으로 바꾼 것은 아니다.

## 마이그레이션: 20260930_0023

`task_events.agent_run_log_id` nullable UUID, `agent_run_logs.log_id` 외래키(CASCADE), unique constraint를 추가한다. 신규 데이터의 명시적 연결은 이 컬럼을 사용하며 payload 내부에 필드를 끼워 넣지 않는다.

이전 이벤트에는 event_key/log_id가 없다. upgrade는 같은 Run 안에서 로그의 기존 이벤트 envelope와 이벤트 payload가 정확히 같은 경우만 연결한다. 같은 내용이 반복되면 로그는 `(created_at, log_id)`, 이벤트는 `(sequence, task_event_id)` 순으로 발생 개수를 맞춰 하나씩 연결한다. 따라서 동일 내용 로그 여러 개를 하나로 합치지 않는다.

이 연결은 **내용과 개수에 기반해 새 대응 관계를 정하는 것**이다. 옛 데이터에 없던 원래 event_key나 실제 생성 시점의 관계를 복원했다고 주장하지 않는다. 기존 이벤트 ID·순번·payload·생성 시각은 유지하고, 대응 로그가 없는 이벤트도 삭제하지 않는다.

누락 이벤트는 migration에서 일괄 생성하지 않는다. 정상 projection이 해당 로그 key를 다시 처리할 때 복구하며, 복구 이벤트에는 현재의 새 sequence가 부여된다. 과거 순번 사이에 삽입하거나 프론트의 이미 처리한 cursor를 되돌리지 않는다. 다시 처리되지 않는 완료 작업의 오래된 누락까지 자동 정비하는 기능은 이번 범위가 아니다.

## 배포와 되돌리기

새 코드 전에 서비스 DB에 `alembic.crud.ini`의 0023을 적용해야 한다. 기존 설정 선택/주입 규칙으로 대상 DB를 확인한 뒤 실행한다. checkpoint DB migration과는 별개다.

```sh
PYTHONPATH=src python -m alembic -c alembic.crud.ini upgrade head
```

DDL와 이력 비교는 배포 시 한 번 수행하며, 기존 데이터량에 따라 잠금·정렬 비용이 있다. 구버전 writer가 backfill 이후 연결 없는 이벤트를 생성하면 새 writer가 이를 다시 연결하지 않으므로 **구·신 writer 혼재는 보장하지 않는다.** 기존 실행/projection을 drain하고 migration 및 코드 전환을 조율한다. 이번 작업에서 운영 DB나 실행 중인 앱에는 적용하지 않았다.

0023 downgrade는 연결 컬럼과 제약만 제거한다. 로그·이벤트의 내용과 순번은 보존한다. 새 코드 실행 중 schema만 downgrade하면 안 된다. 구버전으로 돌아가면 기존의 로그/이벤트 분리 commit 결함도 돌아온다.

## 검증 범위

격리 PostgreSQL에서 저장 단계별 실패/취소, 8개 동시 생산자, 기존 누락·중복 재처리, Task 없는 로그와 나중 연결, unique 제약 및 순번 rollback을 검증한다. 기존 정상/누락/동일 payload 반복/다른 Run/대응 로그 없는 이벤트의 upgrade·downgrade도 검증한다. 최초 호출 checkpoint 재시도와 연계해 Agent/모델 실행 횟수가 늘지 않는지도 확인한다.

프로세스 강제 종료 후 owner 자동 복구, 관리자 복구 API, 외부 Executor/Redis, Kubernetes rollout, 성능 A/B는 이번 범위에서 제외한다.
