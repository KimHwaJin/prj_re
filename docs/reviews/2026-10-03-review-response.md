# 외부 구조·Worker 리뷰에 대한 개발 검토 응답

- 검토일: 2026-10-03
- 기준: `feature/refactor-base`를 `e8d65a1` → `8867b3a`로 fast-forward. 변경은 외부 리뷰 문서 3개뿐이며 운영 소스는 동일하다.
- 응답 브랜치: `feature/structure-review-response`
- 대상: [구조 리뷰](2026-10-03-structure-review.md), [Worker 통합 제안](2026-10-03-worker-execution-unification.md)
- 범위: 소스 대조·설정 오류 재현·관련 테스트·검토 의견 기록. 기능 수정, Worker 통합, 배포, 기존 DB 변경은 수행하지 않았다.

## 판단

큰 방향은 타당하다. 배포 파일의 불일치, RunService 책임 집중, 레거시·현재 경로 혼재는 실제로 확인된다. 개선 방향을 수용하되, 제안 일부는 이미 확보한 실행 보장이나 사용자 합의를 훼손하지 않도록 바꿔야 한다.

구조 리뷰 15개 중 5개를 수용하고 10개를 부분 수용했다. 부분 수용은 문제를 부정하는 의미가 아니라 제안의 적용 범위·방법·사실관계를 보완한다는 뜻이다. 각 상태는 구현 완료 표시가 아니다.

## 확인 방법과 결과

외부 서비스·실제 DB에 접속하지 않는 설정 로딩과 소스 검사를 실행했다. 출력에는 credential이나 원문 DSN을 포함하지 않았다.

| 확인 항목 | 결과 |
|---|---|
| `SERVER_PORT=5000`, 기본 YAML 발견 활성화 | 실효 포트 8000. YAML 우선 정책과 배포 기대 포트 불일치 재현 |
| `APP_ENV=loadtest` | `ConfigurationError: APP_ENV must select dev, stg or prd` 재현 |
| `deploy/secret.example.yaml`을 명시 설정으로 전달 | `Conflicting aliases for CHECKPOINT_DB_URI` 재현 |
| 원천 없는 통합 기본 설정 | model retries=0, Executor submit=false, input root=/workspace/pv |
| 추적 중인 `src/**/*.py`를 `ast.parse` | `src/routers/chat/router.py:3` 문법 오류 1건 |
| PlanningState AST 필드 수 | 77개 확인 |
| 설정·패키지 경계 테스트 | 49 passed, 2.65s |
| 추적 중인 `docs/reports` 파일 수·현재 디스크 크기 | 373파일, 약 112MiB (`docs` 전체 약 115MiB) |

테스트 명령:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider \
  src/api_service/test/test_bootstrap_settings.py \
  src/api_service/test/test_package_boundaries.py
```

외부 리뷰의 Agent 테스트 314개 통과는 리뷰 작성자의 결과이며 이번 검토에서 재실행한 수치가 아니다. 이번에도 이미지 빌드·Kubernetes 배포·PostgreSQL/Redis 통합 테스트·부하테스트는 수행하지 않았다. manifest 지적은 정적 대조이며 실제 플랫폼 적용 결과로 표현하지 않는다.

## 배포와 설정

### R-01 / R-02: 우선 수정이 필요한 실제 불일치

[bootstrap](../../src/service_bootstrap.py)의 시작 경로와 배포 예제가 일치하지 않는다. 루트 app.py는 custom DrainServer를 쓰지만 uvicorn 직접 기동은 SIGTERM 시점에 Worker와 SSE에 먼저 종료를 알리는 훅을 거치지 않는다. FastAPI lifespan 자체가 사라지는 것은 아니다.

단일 컨테이너 Pod가 사용자 전제이므로 정본은 app.py 기반 단일 프로세스로 우선 정리하고, 별도 Deployment·sidecar 예시는 정본에서 제외하는 것이 맞다. readiness/liveness를 구분하고 컨테이너 종료 유예 시간도 실제 drain 설정과 맞춰야 한다.

YAML > env > 기본값은 사용자와 합의한 정책이다. 포트 문제를 해결하기 위해 우선순위를 뒤집지 않고, 정본 YAML과 배포 port/probe/Service를 일치시켜야 한다. loadtest는 유효 profile과 별도 테스트 config 파일로 표현할 수 있다.

`--check-config`는 타입·별칭·profile 검증에 유효하지만 Dockerfile 문법, manifest 오타, 누락 의존성, 실제 probe 경로까지 검증하지 않는다. 설정 검증에 manifest schema 검사·이미지 빌드·기동 smoke 검사를 추가해야 한다. 폐쇄망에서 uv 사용 가능 여부는 확인되지 않았으므로 pyproject/lock 기준으로 생성한 requirements를 사용하는 방식도 가능하다.

### R-03: 프로세스 수는 배수이며 자동으로 오류가 되는 것은 아님

현재 queue claim은 PostgreSQL SKIP LOCKED와 세션 소유권으로 프로세스 간 점유를 조정한다. 따라서 web worker 4개를 사용했다는 사실 자체가 중복 실행 버그의 증거는 아니다. 다만 실행 자리·풀·유지보수 루프가 4배가 되므로 정본 기본값 1과 프로세스별 예산 명시가 적절하다.

`EW_INSTANCE_ID` 기본값은 UUID다. 문제는 `.env.example`과 cicd에서 고정값을 주입하는 경우다. consumer 이름은 instance prefix와 slot index로 만들어지므로 같은 설정의 프로세스들은 같은 이름을 사용한다. Pod 이름만으로는 한 Pod의 멀티프로세스를 구분하지 못하므로 process/startup UUID까지 포함하거나 기존 UUID 기본값을 사용해야 한다. 실제 충돌 부하·중복 실행은 이번에 재현하지 않았다.

## 서비스 내부 구조

### R-04 / R-05: 책임을 나누되 DB 연결 반환 경계를 유지

RunService.create의 `_execute_existing` 분기와 외부의 비공개 메서드 호출을 제거하는 방향을 수용한다. 접수·실행·취소와 실행 결과 반영을 명시적인 서비스 인터페이스로 나누고 도메인 오류를 HTTP 경계에서 변환하는 것이 적절하다.

UoW는 짧은 DB 작업 단위여야 한다. 전체 route/worker 호출이 끝날 때까지 같은 transaction을 유지하면 LLM·Executor HTTP 대기에 DB 연결이 묶여 기존 성능 개선이 되돌아간다. `DB 접수 commit → 연결 반환 → graph 실행 → 별도 결과 저장 commit`을 유지한다. SQLAlchemy/psycopg 및 checkpoint commit 전체를 하나의 UoW가 원자적으로 묶는다는 보장도 두지 않는다.

repository를 두는 경우 쿼리 책임과 도메인 트랜잭션 경계를 명확히 하되, 모든 쿼리를 한 줄 wrapper로 옮기는 작업 자체를 목표로 삼지는 않는다.

### R-06: 상태 구조와 실제 저장량을 구분

77필드, 수동 초기화, observations 누적, 승인 원문과 수정 snapshot 존재는 확인했다. 이름을 AnalysisState로 정리하고 역할별 타입을 정의하는 것은 유효하다.

하지만 `단계 수 × 승인 snapshot 크기`를 실제 DB 증가량으로 단정할 수 없다. 설치된 `langgraph-checkpoint-postgres==3.1.2`의 AsyncPostgresSaver.aput은 복합 channel 값을 blob으로 분리하고 new_versions에 포함된 channel만 저장한다. 변경되지 않은 승인 snapshot 전체가 매 단계 새 blob으로 저장된다는 설명은 맞지 않는다. 누적 observations/public_events처럼 실제로 변경되는 큰 channel의 재직렬화와 저장량은 별도 측정해야 한다.

평면 필드를 하나의 큰 nested channel로 합치면 작은 변경에도 큰 묶음 전체가 새 버전으로 저장될 수 있다. 구조화는 가독성과 타입 목적이고, 저장량 최적화는 checkpoint/blob 바이트·변경 channel·이력 유지 정책을 측정해서 판단한다.

함수/Skill 원문은 승인 당시 코드를 고정하는 역할이 있다. hash만 남기려면 해당 원문을 재배포 후에도 찾을 수 있는 불변 저장소·revision 보존·접근 계약이 먼저 필요하다. 현재 배포 레포의 최신 소스로 대체 조회하면 승인 내용이 바뀔 수 있다.

execution_enabled에 따라 node 존재 여부가 달라지는 문제는 타당하다. 고정 topology와 실행 허용 정책을 분리하거나 runtime version별 재개 호환 검사를 설계해야 한다.

### R-07: 공개 이벤트 경로가 이미 있음

[GraphEventDispatcher.persist_state_delta](../../src/api_service/services/graph_event_persistence.py)는 `agentic-planning-v1`이면 `persist_plan_events`로 분기하고 기존 extract_graph_events를 우회한다. 현재 경로에는 public_events와 envelope가 이미 있다.

따라서 새 이벤트 체계를 처음부터 만드는 작업보다는 현재 projection 계약을 명시하고, 남은 상태 의존과 레거시 분기를 정리하는 작업이 맞다. API가 project_id, 실행 상태 등 Agent state에 의존하는 부분까지 모두 해소됐다는 의미는 아니다.

### R-08 / R-09 / R-10: 역할 구분과 중복을 분리해서 정리

- 설정: 원천 로더는 이미 한 곳이다. API 기본값을 공유 기본값으로 선택하고 Agent에 넘기므로 지적된 실효값 차이는 재현된다. 중복 기본값과 전역 접근은 정리하되 API/Agent/이벤트/SSO별 typed 설정 객체를 유지하는 것 자체는 문제가 아니다.
- 연결: SQLAlchemy와 psycopg 풀을 무조건 한 풀로 합치지 않는다. driver·라이브러리 수명과 전용 LISTEN 연결을 고려해야 한다. 같은 DB를 사용하는 Event/bridge 풀의 재사용 가능성부터 평가한다.
- lease: 메시지 처리권·session graph 실행권·Task 실행 상태는 보호 대상이 다르다. 공통 실행 경로에서 필요성이 없어지는 것만 제거한다.
- graph 조립: 내장 Event Worker는 이미 API runtime의 graph/model/checkpoint 자원을 공유한다. 독립 경로의 graph_context는 graph_provider를 호출하므로 리뷰의 세 위치가 완전히 독립된 compile 구현 세 개라는 뜻은 아니다. 조립 중복과 수명 소유를 공통화하는 방향은 수용한다.
- Agent 조립: 역할 레지스트리와 의존 방향 정리는 적절하다. 사용자와 합의한 역할별 agent.py·독립 prompt 배치는 유지한다. 내부 역할 응답 schema는 해당 역할 근처에 둘 수 있고, 실제로 API와 공유하는 계약만 service_contracts에 둔다.
- 캐시: map 하나로 합치는 것보다 role/model revision/variant가 서로 섞이지 않는 키와 동일 초기화 절차가 중요하다. 운영 부하테스트에 사용하는 mock 주입 기능은 테스트 provider 경계로 정리할 수 있으나 제거를 목표로 삼지 않는다.

## 레거시·자산·테스트

### R-11 / R-12 / R-13

실제 미사용 실행 코드·stub·중복 compiler 정리를 수용한다. 다만 다음 구분이 필요하다.

- 사용자가 보존을 요청한 `analysis/workflow`와 그 아래 Skill/Tool/Workflow 작성 자산은 유지한다. 패키지 전체를 legacy로 옮기거나 없애는 안은 채택하지 않는다.
- import 도달률 40%는 그 자체로 삭제 비율이 아니다. Tool 함수는 import해서 호출하는 것이 아니라 소스를 읽어 Executor에 제출하므로 import 분석에 잡히지 않는 정상 자산이 있다. 외부 리뷰의 정확한 도달률 산출은 이번에 독립 재현하지 않았다.
- AssetCatalog는 AST로 함수·docstring을 읽고 코드를 추출한다. `__init__.py`가 있다는 것만으로 API가 pandas 분석 함수를 import·실행하는 것은 아니다.
- 자산을 비패키지 데이터로 바꾸려면 `.py` 원문이 wheel에 포함되는지와 registry 상대 경로를 검증해야 한다. generators를 devtools로 옮길 때 작성자 가이드·진단 script도 함께 바꿔야 한다.
- Dataset Registry draft는 Executor 구현을 기다리는 합의된 계약 초안이다. 테스트 외에도 `scripts/diagnostics/validate_dataset_contract.py`에서 사용한다. 현재 Agent가 쓰지 않는다는 이유로 폐기하지 않고 계약 문서·검증 도구 영역으로 재배치할지 판단한다.
- `src/routers/chat/router.py`는 문법 오류가 있지만 현재 패키지 설치 대상에는 포함되지 않는다. 현 서비스의 즉시 기동 실패로 표현하지 않는다. 플랫폼 참고 예제라면 docs로 옮기고 실제 Gaia adapter 경로와 구분한다.
- 직접 사용하는 python-dotenv의 pyproject 선언 누락은 수정 대상이다. 원문 requirements.txt에는 존재하지만 현재 pyproject 직접 의존성으로는 없다.

### R-14 / R-15

원본 benchmark artifact는 향후 저장 위치를 분리하는 방향이 좋다. 기존 데이터는 이미 보고서의 재현 근거이므로 먼저 이관 위치와 hash·접근을 확인해야 한다. 현재 파일을 삭제해도 과거 Git 객체 크기가 즉시 줄어들지는 않는다. 이력 재작성·force push는 별도 작업이다.

공용 pytest fixture는 conftest로, 직접 호출하는 setup helper는 명시적인 harness 모듈로 나누면 된다. PostgreSQL/Redis marker와 단위/통합 구분을 추가한다. unittest 혼용이나 패키지 내부 테스트 배치만으로 오류라고 판단하지 않고, 다른 테스트 파일의 구현에 의존하는 결합을 먼저 제거한다.

## Worker 통합 제안

W-01/W-02의 방향은 수용한다. 공통 GraphInvocation과 상태 반영을 먼저 추출하고, 두 입력 경로가 동일 실행 스케줄러에 명령을 전달하게 한다. 별도 Deployment는 현재 사용자 배포 제약에 맞지 않으므로 단일 컨테이너 내부 구성으로 설계한다.

### 구현 전에 보완할 전제

1. **같은 PostgreSQL 제품과 같은 DB transaction은 다르다.** `service_settings.load_settings`는 DATABASE_URL과 EW_DATABASE_URL이 다른 것을 허용하고 secret 예제도 chat_app/agent로 나뉜다. 서로 다른 DB의 inbox와 agent_runs를 일반 로컬 transaction 하나로 원자 commit할 수 없다. 동일 DB/동일 transaction으로 명령을 기록하거나, Outbox 전달과 수신부 unique command ID를 사용해야 한다.
2. **원자 commit만으로 단 한 번의 enqueue가 자동 보장되지는 않는다.** 동일 event/command에 대한 unique 제약·중복 판정과 transaction 경계가 모두 필요하다. enqueue 중복 제거와 graph/외부 API 실행 부작용의 멱등은 별개다. checkpoint receipt와 Executor idempotency는 유지한다.
3. **created_at + SKIP LOCKED는 strict session FIFO 보장이 아니다.** 잠긴 선행 행이나 next_attempt_at이 미래인 선행 명령을 건너뛸 수 있다. 이벤트 sequence와 실행 가능 상태, session별 선행 명령 여부를 확인해야 한다. 현재 API admission이 요청을 제한한다는 보장만으로 앞으로 들어올 이벤트 명령의 순서까지 보장할 수 없다.
4. **공통 큐가 checkpoint 준비를 대신하지 않는다.** 제출 결과가 binding/Executor 대기 checkpoint보다 먼저 도착할 수 있다. 현재 1초 ownership handoff 구현은 새 scheduler의 eligibility·재예약으로 대체할 수 있지만 준비 상태 검증 자체를 없애면 안 된다.
5. **공개 Run이 없는 실행이라는 표현을 보완한다.** Event resume마다 새 agent_runs attempt 행을 만들지는 않지만 ew_commands 상태·실패 시도, checkpoint receipt, Task/Run 연결은 있다. 현재 코드도 연결된 Task 아래의 최신 Run을 찾는다. 진단 모델이 두 갈래라는 지적은 타당하지만 실행 원장이 전혀 없거나 무관한 최신 Run을 임의 선택하는 구조는 아니다.
6. **operation 완료와 execution 완료를 무조건 합치지 않는다.** operation 관찰·실패·증거가 후속 판단과 보고서에 필요하다. 선행 이벤트가 적용됐음을 receipt로 확인한 뒤 obsolete 이벤트만 건너뛰어야 한다.

### 권장 구현 선택

- 공개 Run ID와 API 계약은 유지한다.
- 내부 실행 명령은 user start / user resume / executor event resume를 구분한다.
- PostgreSQL은 명령 상태·세션 소유권의 기준이고 Redis Streams는 전달 경로로 사용할 수 있다. 사용자 접수의 DB 원자성이 PostgreSQL polling queue를 필수로 만들지는 않는다.
- 내부 명령 원장을 공개 Run과 분리하는 안을 우선 검토한다. 기존 ew_commands 재사용·확장 범위는 상태/재시도/마이그레이션 계약을 확인한 뒤 결정한다.
- 공통 실행 한도와 공정한 배분을 설계한다. 두 consumer를 Redis로 바꾸기만 하고 각 한도를 유지하면 현재 자리 분리 문제가 남는다.
- ACK는 해당 그래프 실행 구간의 checkpoint/결과 반영이 확인된 뒤 처리한다. HITL·Executor 대기에서는 실행 자리를 반환하고, 며칠 뒤 결과는 새 명령으로 재개한다.
- 내장 두 Worker는 이미 graph/pool을 공유하므로 통합 효과를 무조건 모델 인스턴스/체크포인트 풀 절반 감소로 계산하면 안 된다. 주효과는 실행 배분과 제어 경로 일원화다.

## 권장 순서

1. **정본 배포·설정 정합성(R-01~03)**: 오류를 재현한 항목부터 좁게 수정하고 단일 프로세스 기준을 명시한다. 실제 폐쇄망 배포는 별도 확인 대상이다.
2. **Run 접수/실행·공통 결과 반영 분리(R-04, R-05 일부, W-02)**: transaction과 checkpoint 경계를 보존한다. 현재 projection 경로의 레거시 의존도 이 범위에서 줄인다.
3. **공통 실행 스케줄러와 Redis 전달(W-03/W-05)**: 동일 session 순서·명령 멱등·DB/Redis 발행 일관성을 먼저 설계하고 구현한다. 새 요청/HITL/Executor 결과를 같은 총한도로 관리한다.
4. **동일 총한도에서 기능·성능 검증**: 혼합 요청, 결과 폭주, review/report 모델 지연, 중복 이벤트, 프로세스 종료·재전달, 장기 대기 자리 반환을 확인한다. 이때 모델 호출 수 최적화는 기존 합의대로 별도다.
5. **상태·설정·Agent 조립·레거시 정리**: R-06~15를 실제 경로와 체크포인트 호환성을 기준으로 나눈다. 무관한 파일 이동과 대용량 근거 이관을 Worker 통합의 선행 필수조건으로 두지 않는다.

향후 구현 항목은 improvements에 별도 번호로 기록한다. 이번 응답은 검토이며 기능 구현 완료나 성능 향상 증거가 아니다.
