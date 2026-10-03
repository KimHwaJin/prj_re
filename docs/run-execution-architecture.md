# Run 실행 구조와 개발 인수인계

## 책임과 진입점

HTTP의 공개 Run ID는 전체 사용자 작업을 가리킨다. 내부 invocation ID는 새 요청 또는 사용자 resume의 한 번의 그래프 호출을 가리킨다. 이 구분과 API/SSE 계약은 이번 구조 변경에서 유지한다.

| 위치 | 책임 | 진입점 |
|---|---|---|
| `services/public_run_service.py` | 공개 Run 조회, 승인 토큰·interaction 검증, HTTP 요청 조정 | `PublicRunService.create/resume/cancel` |
| `runs/admission.py` | 세션 잠금, 멱등키 확인, Task·pending invocation·queued 이벤트 원자 기록 | `enqueue` |
| `runs/commands/` | 내부 원장 접수·순서·claim·결과·기존 작업 이행 | [명령 Worker 안내](agent-command-worker.md) |
| `runs/execution.py` | 점유 검증, 실행 준비, observer 소유, 결과·실패 기록 조정 | `execute_claimed` |
| `runs/monitoring.py` | 그래프와 사용자 취소·heartbeat·token observer 경쟁 및 종료 확인 | `run_cancellable` |
| `runs/cancellation.py` | 취소 요청 기록, 대기 중 Task의 안전한 종료 | `cancel_task` |
| `runs/graph_invocation.py` | 공통 모델·프로젝트 context, submission scope, 그래프 호출·중간 projection | `GraphInvocation` |
| `runs/protocols/initial.py` | 최초 입력 started marker와 entry receipt 검증 | `GraphInvocation.user_turn` 통해 호출 |
| `runs/protocols/user_resume.py` | interrupt 주소와 답변 receipt 검증 | `GraphInvocation.user_resume` 통해 호출 |
| `runs/protocols/executor.py` | execution binding·sequence·event receipt 검증 | `GraphInvocation.executor_event` 통해 전달·결과 반영 |
| `runs/projection.py` | checkpoint 결과를 Run·Task·상태 이벤트로 반영 | `finalize_state`, `synchronize_executor_completion` |
| `runs/repository.py` | Run → Task 행 잠금 순서, 세션 소유 확인, 관계 연결 | `lock_run_and_task`, `require_session` |
| `runs/requests.py`, `runs/policy.py`, `runs/errors.py` | 요청 hash·모델 선택, 재시도 판정, application 오류 | 책임별 함수/예외 |
| `services/agent_graph_service.py` | compiled graph/checkpointer/Store lifespan, 사용자 호출의 입력·config 조립 | `runtime.open_graph`, `ainvoke_user_turn/resume` |

모듈은 호출 의미를 감추는 거대한 facade를 두지 않는다. API는 admission/cancellation을, Worker는 execution을 직접 사용한다. HTTP 예외 응답 변환은 `core/problems.py`와 앱 조립에서 담당한다. Agent 노드에는 API DB 의존을 추가하지 않는다.

## DB 수명

1. 접수 transaction에 Task, invocation, queued 이벤트, 내부 명령을 함께 저장한다.
2. 공통 Worker가 실행 자리가 있을 때만 내부 명령과 세션 소유권을 같은 transaction에서 점유한다. 사용자 입력일 때 기존 Run/Task도 함께 점유한다.
3. 실행 준비 transaction에서 identity·모델·프로젝트 ID 등 평범한 값을 복사하고 `started/resumed` 이벤트를 저장한다.
4. 준비 session을 닫은 뒤 그래프를 실행한다. observer와 각 서비스 projection/checkpoint I/O는 자신만의 짧은 DB 작업을 사용한다.
5. 그래프와 observer 종료를 확인한 뒤 새로운 session에서 결과/재예약/격리를 기록한다.

이는 API DB와 LangGraph checkpoint가 하나의 transaction이라는 뜻이 아니다. receipt로 이미 소비된 입력을 확인하고 서비스 projection만 복구한다. `started`만 있고 receipt가 없다면 입력을 무조건 재전송하지 않는다.

## 공통 GraphInvocation

세 경로의 전달 규칙은 서로 다르므로 protocol을 분리한다. 그래프 실행·중간 상태 projection·submission effect 추적은 공통으로 사용한다.

- 최초 입력 stream이 이전 Run의 상태를 echo할 수 있으므로 entry receipt가 일치한 state부터 projection한다.
- 사용자 resume은 저장된 interrupt ID를 주소로 사용한다. 오래된 승인과 Executor 전용 interrupt에 대한 사용자 resume을 거절한다.
- Executor 결과는 command/event/execution identity, sequence와 checkpoint receipt를 검증한다.
- `durability="sync"`를 유지한다. invocation 완료 시점의 checkpoint와 API 상태 반영은 별개의 commit이며 receipt replay로 결과 저장을 복구한다.
- 프로젝트 prompt/kernel snapshot은 입력 준비 또는 legacy checkpoint backfill에서 읽고 DB 연결을 반환한다. project_memory의 Store/middleware 정책은 변경하지 않는다.
- 취소 감시가 소유한 Executor 제출 tracker를 중첩 호출에서도 공유한다. HTTP 제출이 진행된 뒤 취소/예외가 발생하면 불명확한 외부 결과를 일반 재시도로 바꾸지 않는다.

## 현재 단계와 다음 단계

060에서 두 입력을 DB `agent_commands`로 모으고 한 Agent Worker가 공통 총한도로 실행하도록 전환했다. 외부 Redis 이벤트 수신·Inbox/routing은 별도 background 책임이며 직접 graph를 호출하지 않는다. 공개 Run ID를 내부 command ID로 대체하지 않는다. [원장·Worker·설정·DB 이행](agent-command-worker.md)을 따른다.

다음 단계는 Worker 전용 깨우기 신호와 조회 비용 정리다. 기존 Redis 실행 모듈 파일은 자동 승인 검토의 삭제 거절로 남았지만 새 bootstrap에서 구성하지 않는다.

HITL 또는 Executor 대기에 도달하면 현재 invocation의 실행 소유권을 반환한다. 공개 업무는 아직 미완료이고 `WAITING_EXECUTOR` 세션 입력 잠금은 유지한다. 외부 Executor의 장기 작업 동안 Agent 실행 자리를 유지하지 않는다.
