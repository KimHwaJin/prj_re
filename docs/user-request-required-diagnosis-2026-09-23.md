# `user_request is required` 원인 분석

> 17:03 KST 추가 조회·재현 결과는 [체크포인트 양쪽 DB 공존 검증](checkpoint-coexistence-diagnosis-2026-09-23.md)을 우선 참고한다. 최근 오류 12개 세션에서는 정상 상태가 `chat_app`에 있고 오류가 `agent`에 있는 반대 방향 사례가 11개 확인됐다. 아래 수치·사례는 이전 조회 시점의 기록이며, `agent`만 항상 정상 저장소라는 뜻이 아니다. 실제 기록 작성 프로세스는 여전히 확정하지 않았다.

- 분석 기준: `feature/load_test_v1`, `dad1d6c`, 2026-09-23 작업 트리와 실행 DB.
- 검증: 소스 추적, 외부 서비스를 호출하지 않는 Graph 재현, PostgreSQL 읽기 전용 조회.
- 실행 코드, 환경 설정, 서비스, DB 데이터는 변경하지 않았다. 이 문서만 추가했다.

## 결론

최신 확인: 사용자는 이전 설정으로 실행 중인 Worker가 없다고 명시했다. 따라서 이전 설정의 Worker를 실제 장애 원인으로 전제하지 않는다. 동일한 설정의 실제 Worker 4개를 추가 검증한 결과도 정상이며, 현재 실제 장애의 원인은 미확정이다. 다른 hostname의 작업 기록만으로 설정 차이를 추론했던 설명은 철회한다.

확인한 표본에서는 **최초 질문이 정상적으로 전달되었고, 별도 저장소에는 빈 상태로 최초 노드를 실행해 실패한 checkpoint가 있었다.** 재개 시 기존 상태를 찾지 못하면 동일 오류가 발생한다는 것은 격리 테스트로 확인했다. 실제 실패 attempt와 기록을 작성한 프로세스의 연결은 아직 확인하지 못했다.

같은 session/thread의 정상 진행 상태는 `agent` DB에 있고, `chat_app` DB에는 `receive_request`로 시작하는 빈 checkpoint와 해당 오류가 있다. 이는 저장소가 분리된 실행 기록이 있다는 증거다. 그러나 실패한 각 Run attempt를 실제 프로세스와 연결하지 못했으므로, 다른 Worker의 설정 불일치가 특정 Run 실패를 일으켰다고 확정할 수는 없다.

조회 시점에는 같은 큐의 Task에 서로 다른 Worker owner 3개가 기록되어 있었으며, 모두 `running`이고 임대 만료 전이었다. 다만 어떤 Worker가 어느 checkpoint DB를 사용하는지까지는 매핑하지 않았다. 현재 로컬 환경에서 로드한 API와 이벤트 Worker의 checkpoint 설정은 모두 `agent`다. 다른 실행 프로세스의 환경 변수, 오래 떠 있는 프로세스, 별도 배포 설정을 함께 확인해야 한다.

### 단일 로컬 프로세스에 대한 추가 확인 및 정정

현재 브랜치의 최초 실행과 재개는 모두 `runtime.open_graph()` → `load_agent_settings()` → `create_checkpointer(database_url=agent_settings.checkpoint_db_uri)`를 사용한다. PostgreSQL 설정과 프로세스 환경이 유지되면 최초 실행/재개를 구분해 checkpoint DB를 바꾸는 분기는 없다. `config.Settings.checkpoint_db_uri`의 기본 DB가 `chat_app`이라는 이유만으로 현재 API Graph가 그 값을 번갈아 사용하는 것도 아니다.

외부 DB·모델 연결을 mock으로 대체하고 실제 `ainvoke_user_turn()`과 `ainvoke_resume()`의 설정 전달 경로를 실행한 결과, 둘 다 `agent`를 선택하고 동일한 thread ID를 전달했다. 이는 설정 선택 경로 검증이며 실제 PostgreSQL 상태 복원까지 검증한 테스트는 아니다.

따라서 '현재 코드의 단일 로컬 프로세스에서 DB가 자동으로 바뀌는 것이 원인'이라는 결론은 지지되지 않는다. 저장소 불일치 기록의 생성 주체·실행 버전·재시작 전후 설정을 추가 확인해야 한다. 처음 분석에서 이 구분 없이 원인을 확정적으로 표현한 부분을 정정한다.

별개로 `GRAPH_CHECKPOINTER=memory`이면 호출마다 새 `InMemorySaver()`를 생성하므로 단일 프로세스에서도 재개 시 상태가 유실될 수 있다. 이는 DB가 바뀌는 현상이 아니며, 조회한 현재 로컬 설정은 `postgres`여서 이번 상황에 해당한다고 단정하지 않는다.

## 추가 재현 테스트 결과

사용자가 Swagger, demo, Locust 모두에서 간헐적으로 발생한다고 알려 주어 공통 서버 경로를 실제로 실행했다. 기존 서비스 DB에는 쓰지 않았다. 기존 이미지 `postgres:17`로 별도의 임시 컨테이너를 생성하고, 독립된 CRUD DB 및 checkpoint DB A/B를 사용했다. 모델 응답과 Executor 연계는 테스트 대역으로 대체했다.

| 검증 | 범위 | 결과 |
|---|---|---|
| 같은 PostgreSQL 저장소의 최초 호출/재개 | 실제 `ainvoke_user_turn`, `ainvoke_resume`, 호출마다 새 Graph/connection pool | 성공 |
| 동일 프로세스의 12개 동시 세션 | 실제 Graph 및 PostgreSQL checkpoint | 12/12 성공 |
| 동일 코드·설정의 별도 Worker 프로세스 4개 | 공용 임시 CRUD 큐와 동일 PostgreSQL checkpoint, 20개 대화 × 4단계 | 80/80 성공, 재시도 0회, 모든 대화에서 처리 Worker 교대 확인 |
| HTTP API → CRUD 큐 → 실제 Worker → Graph → checkpoint | 6개 대화, 최초 요청/데이터 선택 재개 총 12회 | 전부 성공, 재개 시 같은 Task 유지 |
| 승인 대기까지 전체 앞단 | 6개 대화, 최초 요청 → 데이터 선택 → 분석 목적 입력 → 후보 선택, 총 24회 | 전부 성공, Workflow 승인 대기 도달 |
| 최초 호출 A, 재개 B | 같은 session/thread, 다른 checkpoint DB | 동일 ValueError 재현, B에서 4회 반복해도 실패 |
| 실패한 요청을 A에서 다시 재개 | 같은 session/thread | 정상 진행 |
| 같은 DB에서 없는 thread로 재개 | PostgreSQL 유지, thread만 변경 | 동일 ValueError 재현 |
| `memory` 모드에서 API 호출별 Graph 생성 | 동일 프로세스·동일 thread | 최초 호출 성공, 재개 시 동일 ValueError |

HTTP API 테스트에서는 실제 `POST /api/v1/sessions/{id}/runs`와 GET, ORM 테이블, `claim_one()` 및 `execute_claimed()`, 메시지/로그 저장까지 실행했다. ASGI 내부 HTTP 호출이며 기존 8000번 서비스에 테스트 요청을 보내지는 않았다. Worker 실행은 테스트가 명시적으로 큐를 점유·처리하도록 호출했다.

추가로 같은 테스트 큐에서 다음 순서를 구성해 간헐적 성공/실패의 발생 원리를 검증했다.

1. Worker A 설정으로 최초 요청 실행 → DB A에 checkpoint 저장.
2. Worker B 설정으로 재개 Run 점유 → DB B에 기존 상태가 없어 `user_request is required`, Run은 재시도 `pending`.
3. Worker A 설정으로 같은 Run 재시도 → 정상 interrupt, `attempt_count=2`, 기존 failure가 null로 정리됨.

이는 한 테스트 프로세스에서 실행 주체별 설정을 바꾸어 모사한 것이다. 실제 운영 Worker 두 개를 제어한 실험이 아니다. 현재 코드의 공유 큐·재시도 정책만으로 '어떤 실행 주체가 가져가는가에 따른 간헐적 증상'이 만들어진다는 것을 검증한다.

검증 한계: 실제 LLM 출력, 네트워크 장애, 장시간 대규모 부하, Executor는 검증하지 않았다. 승인 대기까지 확장할 때 기존 `PlanWorkflowAgent` 테스트 대역이 현재 스키마에서 금지된 `needs_input`을 반환해 검증 오류가 났다. 제품 코드를 변경하지 않고 테스트 대역을 유효한 `ready` Workflow로 바꿔 앞단 지속성 검증을 완료했다. 해당 테스트는 별도 Workflow 필수 입력 질문 단계의 검증을 포함하지 않는다.

### 실제 실행 환경에서 추가 확인한 사실

- 8000번 포트의 PID 66140은 이 저장소의 `.venv/bin/dtest-agent-api`로 실행되고 있었고, 부모는 `uv`였다.
- 실행 중 Locust의 대상은 `host.docker.internal:8000`이었다. Locust 코드는 같은 session ID를 유지하고 GET으로 interrupted를 확인한 뒤 재개 요청을 보냈다. 검토한 경로에서 세션 ID를 임의로 바꾸는 동작은 없었다.
- 이 PC의 hostname은 `SKCC25N00175`였다. 읽기 전용 DB 조회에서 이와 다른 `gaia:c55cf63a273e` owner의 running Task 및 2026-09-23 16:02:52 KST heartbeat, 유효한 lease가 관측되었다. 이후 조회에서는 running 작업이 없었다. 이 증거는 관측 당시 다른 이름의 실행 주체가 공용 큐에서 활동했음을 뜻하며, 계속 실행 중이라고 단정하지 않는다.
- `chat_app` DB에서도 checkpoint 테이블을 조회한 연결이 관측되었다. 접속 출처는 네트워크 경유로 동일하게 보일 수 있어 해당 연결과 특정 Worker를 직접 매핑할 수는 없었다.

단일 프로세스 내부의 자동 DB 전환은 재현되지 않았으며, 동일 코드·설정의 실제 멀티 Worker 테스트도 정상 동작했다. 이전 설정의 Worker가 없다는 사용자 확인을 반영해 해당 가설은 제외한다. 실제 실패 attempt와 당시 복원된 Graph 상태·오류 발생 위치를 직접 연결해야 하며, 실제 장애 원인은 아직 미확정이다.

추가 멀티 프로세스 테스트는 실제 `claim_one()`/`execute_claimed()`를 각 프로세스가 반복 호출하게 구성했다. Worker 4개가 각각 20개 Run을 처리했고, 20개 세션 모두 두 개 이상의 Worker를 거쳤다. 범위는 최초 질문 → 데이터 선택 → 분석 목적 입력 → 후보 선택 → Workflow 승인 대기까지다. 승인 응답은 보내지 않았으며 Executor 제출·실행은 검증하지 않았다. 모델은 유효한 고정 응답을 반환하는 테스트 대역이다. 테스트 Worker 프로세스는 종료했고 전용 임시 PostgreSQL 컨테이너도 정리했다.

## 실제 데이터 증거

### 오류 Run의 요청 형태

조회 시점에 `failure.message = 'user_request is required'`가 남아 있는 Run은 55건이었다.

| input | command | 건수 |
|---|---|---:|
| JSON null | object | 35 |
| JSON null | string | 20 |

모두 command 기반 재개 요청이다. 재개에서 input이 null인 것은 정상 계약이다. 이 수치는 실시간 조회 결과이며 전체 오류율, 전체 시도 수, 전체 과거 장애 건수를 뜻하지 않는다.

### 같은 session의 저장소 불일치

Session: `7d5259b1-2b9c-4001-baee-5f5b55b37f14`

최초 Run `6a5208c5-e125-4c12-86e5-620f13ec6fd0`에는 길이 17의 사용자 텍스트가 있으며, 1회 실행으로 데이터 선택 대기에 도달했다. 재개 Run `c71a5f0f-7637-4ae4-a969-4854bf280c15`는 4회 시도 후 해당 오류로 끝났다.

| 항목 | `agent` DB | `chat_app` DB |
|---|---|---|
| checkpoint 시각, 한국시간 | 15:34:22 | 15:34:33 |
| Graph step | 4 | 0 |
| user_request | 길이 17의 원문 존재 | 없음 |
| 다음 실행 분기 | `wait_for_data_selection` | `receive_request` |
| checkpoint 오류 기록 | 해당 오류 없음 | `ValueError('user_request is required')` |

`chat_app` 쪽 checkpoint의 inline 상태에는 `branch:to:receive_request`만 존재하고, 채널 버전도 `__start__`, `branch:to:receive_request`뿐이다. 정상 진행 상태의 복원으로 볼 수 없다.

## 코드에서 이어지는 실패 경로

1. 최초 API 입력의 사용자 메시지는 `user_request_from_messages()`가 읽고 `build_graph_input()`이 `user_request`로 Graph에 전달한다. 최초 입력이 비어 있으면 이 API 경계는 다른 문구의 HTTP 422를 반환한다.
2. 세션 ID가 LangGraph `thread_id`다. 따라서 최초 호출과 재개는 같은 thread의 같은 저장소를 읽어야 한다.
3. API 프로세스는 기본적으로 PostgreSQL Run Worker를 함께 시작한다. Worker의 `claim_one()`에는 배포 환경이나 Worker 그룹 필터가 없고, 공유 DB의 실행 가능한 pending Run을 점유한다. 요청을 받은 프로세스가 반드시 실행하는 구조가 아니다.
4. 최초 요청이 `agent`에 checkpoint를 남긴 후, 다른 저장소를 보는 실행 경로가 재개 요청을 처리하면 정상 상태를 찾을 수 없다.
5. `ainvoke_resume()`는 checkpoint와 대기 중 interrupt를 사전 검증하지 않고 `Command(resume=command)`를 실행한다.
6. 빈 상태에서 `receive_request()`가 실행되면 `user_request`와 `messages`가 모두 없어 정확히 이 ValueError가 발생한다.
7. `_is_retryable()`은 HTTPException이 아닌 예외를 재시도 대상으로 취급한다. 따라서 이 상태 문제도 기본 설정에서 최초 시도 + 재시도 3회로 증폭된다.

관련 코드 위치:

- `src/app/graphs/nodes/routing.py:58`: 오류가 발생하는 최초 노드.
- `src/app/services/agent_graph_service.py:186`: 최초 메시지 검증.
- `src/app/services/agent_graph_service.py:232`: 최초 Graph 입력 구성.
- `src/app/services/agent_graph_service.py:259`: session 기반 thread ID.
- `src/app/services/agent_graph_service.py:318`: 검증 없이 실행하는 resume 경계.
- `src/app/services/agent_graph_service.py:101`: API checkpointer가 받는 DB 설정.
- `src/app/agent_run_worker.py:22`: 환경 구분 없는 공유 큐 점유.
- `src/app/api/v1/router.py:33`: API 수명주기에서 Worker 기동.
- `src/app/services/run_service.py:58`: 예외 재시도 판정.
- `src/app/services/run_service.py:431`: 실패한 실행을 pending으로 되돌리는 처리.

## 오프라인 재현 결과

기존 Graph와 테스트용 의존성을 사용해 모델·Executor·실행 DB를 호출하지 않고 비교했다.

| 조건 | 결과 |
|---|---|
| 정상 최초 입력 | 데이터 선택 interrupt 도달 |
| 같은 Graph와 saver, 같은 thread에서 `Command(resume="mock")` | 다음 분석 정보 interrupt 도달 |
| 새로운 빈 saver, 같은 thread에서 동일 resume | `routing.py:70`에서 동일 오류 |

다른 DB에 접속하는 상황의 핵심인 '동일 thread의 기존 상태가 없는 저장소'만으로 현상을 재현했다. 잘못된 직접 Graph 입력도 같은 오류를 만들 수 있으나, 조회한 실제 오류 Run들은 재개 요청이므로 이번 장애의 우선 원인은 checkpoint 불일치다.

## 조치 우선순위

### 1. 실행 프로세스의 checkpoint 저장소 통일

같은 `DATABASE_URL`의 Run 큐를 소비하는 모든 API/Run Worker에 대해 실제 프로세스 환경을 확인한다. 로컬 `.env` 파일 확인만으로 끝내면 안 된다.

| 경로 | 사용하는 설정 | 이번 정상 상태의 저장소 |
|---|---|---|
| API 및 PostgreSQL Run Worker의 Graph | `CHECKPOINT_DB_URI` | `agent` |
| 별도 Agent 이벤트 Worker | `AGENT_CHECKPOINT_DATABASE_URL` | `agent` |

서버 주소·DB·schema/search_path 및 thread 규칙까지 같아야 한다. `AGENT_CHECKPOINT_DATABASE_URL`만 고쳐서는 API Graph 설정이 바뀌지 않는다. `EW_DATABASE_URL`이나 Redis namespace도 PostgreSQL Run 큐를 분리하지 않는다.

의도하지 않은 개발 프로세스가 공용 작업을 가져가고 있다면 해당 프로세스의 `AGENT_WORKER_ENABLED=false` 적용 또는 종료가 필요하다. 변경한 설정은 해당 실행 프로세스에 다시 적용해야 한다. 개발·부하테스트·공용 환경은 CRUD 큐를 분리하거나 명시적인 환경 필터를 구현해야 한다.

### 2. resume API 경계의 상태 검증

`ainvoke_resume()`에서 `aget_state(config)`로 기존 checkpoint와 재개 가능한 interrupt를 검증한다. 상태가 없으면 명시적인 `CHECKPOINT_NOT_FOUND` 등 도메인 오류와 HTTP 409로 처리해 잘못된 재개를 중단한다. 상태가 있지만 대기 중이 아닌 경우도 구분한다.

재개 payload에 `user_request`를 억지로 추가하는 것은 해결책이 아니다. 기존 승인/선택 상태를 복원하지 못한 채 새 실행으로 흐를 위험이 있다. 정상 checkpoint를 삭제해서 초기화하는 것도 복구 방법으로 사용하지 않는다.

### 3. 재시도 및 관측 보완

checkpoint 누락·재개 상태 불일치 같은 확정적 오류는 자동 재시도하지 않는다. 각 시도에 run ID, session/thread ID, Worker ID, 코드 revision, 비밀정보를 제외한 checkpoint 저장소 식별자를 남긴다. 종료 시 현재 lock owner만 지워지는 구조로는 사후에 어느 Worker가 실패했는지 추적하기 어렵다.

### 4. 재검증 기준

설정 통일 후 새 테스트 세션에서 최초 질문 → 데이터 선택 → 추가 입력 → workflow 승인까지 반복한다. Worker가 교대해도 상태가 유지되어야 한다. 동일 부하 조건으로 `user_request is required` 재발 여부와 오류별 재시도 횟수를 확인한다. 기존 실패 세션은 올바른 저장소의 checkpoint 및 Task 상태를 대조한 후 개별적으로 복구한다.

## 별도로 확인된 후속 오류

다른 표본 세션의 정상 `agent` checkpoint에는 Executor의 `HTTP 422 / INVALID_EXECUTION_SPEC / PATH Python Step source does not exist.`도 기록되어 있다. 이번 `user_request` 오류와는 별개이며, checkpoint 문제를 해결한 후에도 실행 단계에서 막힐 수 있다. 생성 소스 경로가 Executor 실행 환경에서 접근 가능한지 별도 검증해야 한다.
