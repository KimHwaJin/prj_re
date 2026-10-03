# 개발 응답에 대한 리뷰어 후속 의견 (2026-10-03)

- **기준**: `feature/refactor-base` @ `fb61227`
- **대상**: [개발 검토 응답](2026-10-03-review-response.md)
- **원 리뷰**: [구조 리뷰](2026-10-03-structure-review.md), [실행 Worker 단일화 제안](2026-10-03-worker-execution-unification.md)
- **범위**: 응답에서 사실관계를 보완한 부분을 코드와 설치된 라이브러리로 재확인하고, 남은 쟁점을 정리한다. 코드는 변경하지 않았다.

## 검토 응답표

| ID | 항목 | 상태 | 의견 |
|---|---|---|---|
| F-01 | 누적 list channel의 checkpoint 쓰기 증폭 측정 (R-06 후속) | 미검토 | |
| F-02 | Redis를 실행 명령 전달 경로가 아닌 깨우기 신호로 한정 (W-01/W-05 후속) | 미검토 | |
| F-03 | 실행 명령 원장과 API DB를 같은 DB로 두는 제약 (W-03 후속) | 미검토 | |

## 총평

응답의 판단은 타당하다. 반려 항목이 없고, 설정 오류 3건을 실제로 재현했으며, 사실관계 보완도 아래와 같이 모두 확인됐다. 원 리뷰에서 틀렸거나 과장된 부분은 응답의 정정을 그대로 받아들인다. 원 문서의 정정란에도 같은 내용을 기록했다.

권장 순서(배포 정합성 → W-02 공통 실행·상태 반영 추출 → 스케줄러)도 원 리뷰와 일치한다. 아래 보존 판단에는 이견이 없다.

- `workflow/` 자산 보존
- 역할별 `agent.py`·prompt 유지
- Git 이력 재작성 제외
- 단일 컨테이너 제약

## 응답의 사실관계 보완 재확인

| 원 항목 | 응답 내용 | 재확인 근거 | 결과 |
|---|---|---|---|
| R-06 | checkpoint saver는 바뀐 channel만 blob으로 저장한다 | `langgraph/checkpoint/postgres/aio.py` `aput`가 `new_versions`에 포함된 channel만 `base.py` `_dump_blobs`로 넘긴다 | 맞음. 원 리뷰의 "단계 수 × snapshot 크기" 공식은 틀렸다 |
| R-07 | 현재 agentic runtime은 `extract_graph_events`를 거치지 않는다 | `graph_event_persistence.py` `persist_state_delta`에서 `agent_runtime == 'agentic-planning-v1'`이면 `persist_plan_events`로 분기한다 | 맞음 |
| R-03 | `EW_INSTANCE_ID` 기본값은 UUID다 | `event_worker_settings.py:42` `default_factory=uuid4`, consumer prefix는 `worker/runtime.py:140` | 맞음. 이름 충돌은 고정값을 주입할 때만 생긴다 |
| R-09 | graph 조립이 완전히 독립된 3벌은 아니다 | `worker_main.graph_context`의 비공유 분기가 `graph_provider.build_agent_graph`를 호출한다 | 맞음 |
| R-11 | Dataset draft는 진단 script에서도 쓴다 | `scripts/diagnostics/validate_dataset_contract.py` | 맞음. 원 리뷰는 `src`만 검색했다 |
| R-12 | AssetCatalog는 함수를 import하지 않고 AST로 읽는다 | `planning/catalog.py`의 `ast.parse` / `ast.get_source_segment` | 맞음 |
| W-03 | API DB와 EW DB가 다를 수 있다 | `service_settings.py`: `EW_DATABASE_URL`의 기본값은 `DATABASE_URL`에서 파생하지만 덮어쓸 수 있다. `deploy/secret.example.yaml`은 `chat_app` / `agent`로 나뉘어 있다 | 맞음. 원 제안은 같은 DB를 전제했다 |
| W 순서 | `created_at` + SKIP LOCKED는 session 단위 FIFO를 보장하지 않는다 | 앞선 명령의 `next_attempt_at`이 미래이면 claim 조건에서 빠져 뒤 명령이 먼저 실행될 수 있다 | 맞음 |
| W "Run 없음" | 이벤트 재개에도 실행 원장(ew_commands)과 Task-Run 연결이 있다 | `executor_completion.py`가 연결된 Task의 최신 Run을 조회한다 | 맞음. 원 제안의 표현이 부정확했다 |
| R-05 | UoW는 짧은 DB 작업 단위여야 한다 | — | 동의. 원 리뷰의 "경계에서만 commit"은 LLM 대기 중 transaction을 유지하라는 뜻이 아니었으나, 그렇게 읽힐 수 있었다 |

## 남은 쟁점

### F-01. 누적 list channel의 checkpoint 쓰기 증폭 (R-06 후속)

R-06의 크기 공식은 틀렸지만, 바뀐 channel만 저장한다는 같은 원리 때문에 다른 문제가 남는다.

- `observations`, `public_events`, `reviews` 같은 channel은 reducer 없이 `[*state[...], new]` 형태로 **리스트 전체를 교체**한다.
- 그러면 바뀔 때마다 리스트 전체가 새 버전 blob으로 다시 직렬화되어 저장된다.
- 이전 버전 blob은 이전 checkpoint가 참조하므로 그대로 남는다.
- 따라서 한 thread에서 누적되는 저장 바이트가 대략 원소 수의 제곱에 비례해 늘 수 있다(추론).

**요청**: 대표 시나리오(새 요청 → 승인 → MULTI 실행 → repair 1회 → 보고서 → 후속 질문 2회)로 다음을 측정한다.

- thread별 `checkpoint_blobs` 바이트 합계
- channel별 버전 수
- 바이트 기준 상위 5개 channel

결과를 보고 결정할 사항:

- 해당 channel을 append reducer(`Annotated[list, operator.add]` 등)로 바꿀지
- 오래된 원소를 별도 저장소로 옮길지
- checkpoint 이력을 얼마나 보존할지

R-06의 상태 구조화(가독성·타입)와는 별개로 판단한다.

### F-02. Redis는 깨우기 신호로 한정 (W-01/W-05 후속)

응답의 권장안은 "PostgreSQL 명령 원장 + Redis 전달"과 "checkpoint·결과 반영 확인 후 ACK"를 함께 둔다. 이 조합에는 다음 우려가 있다.

1. **실행 시간 동안 PEL 점유**: ACK가 graph 실행 구간(LLM review/repair/report 포함) 뒤에 오므로, 그동안 메시지가 pending 상태로 남는다. 그러면 현재 consumer의 lease 갱신, stale claim, 재전달 처리가 그대로 필요하다.
2. **순서 보장 역할 중복**: Redis consumer group은 session 단위로 메시지를 배정하지 않는다. 결국 순서와 직렬화는 원장과 session 소유권이 결정한다. Redis가 순서 보장에 기여하지 못하는데 실행 수명에는 묶이게 된다.
3. **사용자 경로의 의존성 추가**: 현재 사용자 요청 경로는 API → PostgreSQL만 필요하다. 사용자 명령까지 Redis로 전달하면 접수 가용성이 Redis에 묶인다.

**제안**

- 명령의 상태, 순서, 재시도, 소유권은 **PostgreSQL 원장만** 기준으로 삼는다.
- Redis 메시지(또는 PostgreSQL LISTEN/NOTIFY)는 "원장에 실행할 명령이 있다"는 **깨우기 신호**로만 쓴다.
- Worker는 신호를 받으면 원장에서 claim하고, **claim 직후 ACK**한다. 실행 결과는 원장 상태로만 관리한다.
- 신호가 유실되면 주기적 polling으로 복구한다. 신호는 지연을 줄이는 수단이지 정확성의 근거가 아니다.

**기대 효과**

- ACK의 의미가 "전달됨"으로 단순해진다.
- 실행 lease는 원장 한 곳에서만 관리한다. 지금의 Redis lease와 재전달 처리 중 실행 수명과 관련된 부분을 줄일 수 있다.

Executor 이벤트 **수신**(외부 입력의 중복 제거, sequence 검증, DLQ)은 기존 inbox 구조를 그대로 둔다. 이 제안은 그 뒤의 **실행 명령 전달**에만 해당한다.

### F-03. 명령 원장과 API DB를 같은 DB로 (W-03 후속)

응답이 지적했듯이 DB가 다르면 inbox 처리와 명령 기록을 로컬 transaction 하나로 묶을 수 없고, outbox 중계와 수신측 unique command 처리가 필요해진다.

- 설정 기본값은 이미 같은 DB를 쓴다(`EW_DATABASE_URL` ← `DATABASE_URL`).
- 새 실행 구조에서 "**명령 원장, inbox, API 테이블은 같은 DB에 둔다**"를 설계 제약으로 정하면 DB 간 중계 계층이 필요 없다.
- 확인 요청: `deploy/secret.example.yaml`처럼 `chat_app`과 `agent`를 나눠야 하는 실제 운영 사유(권한 분리, 기존 DB 공유 등)가 있는가?
  - 사유가 없다면 제약으로 확정하고, 예제를 단일 DB로 바꾼다.
  - 사유가 있다면 F-02 구조에서 outbox 중계를 둘지 결정한다.

unique command ID, checkpoint receipt, Executor idempotency는 같은 DB를 쓰더라도 유지한다(응답의 W-03 2항에 동의).

## 다음 확인 시점

- 응답표 F-01~F-03 갱신
- W-02(공통 GraphInvocation·상태 반영 추출) 구현이 시작되면 해당 `improvements/NNN` 기록과 함께 변경분을 리뷰한다.
