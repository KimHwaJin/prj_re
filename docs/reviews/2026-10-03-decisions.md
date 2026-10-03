# 구조 리뷰 합의 사항 (2026-10-03)

- **기준**: `feature/refactor-base` @ `4bfcfd7`
- **성격**: 2026-10-03 리뷰 스레드(원 리뷰 → 개발 응답 → 리뷰어 후속 → 개발 후속 응답)에서 합의된 내용을 구현 기준으로 쓸 수 있게 한 곳에 모은 요약이다. 새로운 제안은 넣지 않았다. 리뷰어 후속 검토에서 나온 보완 한 가지만 D-09에 포함했다.
- **우선순위**: 이 문서와 개별 문서의 내용이 다르면 근거 문서가 우선한다. 근거 문서의 사실관계 정정은 각 문서의 정정란과 응답을 따른다.
- **상태 표기**: 이 문서에서 "합의"는 설계 방향에 대한 합의를 뜻하며, 구현 완료를 뜻하지 않는다.

근거 문서:

- [구조 리뷰](2026-10-03-structure-review.md)
- [실행 Worker 단일화 제안](2026-10-03-worker-execution-unification.md)
- [개발 검토 응답](2026-10-03-review-response.md)
- [리뷰어 후속 의견](2026-10-03-reviewer-followup.md)
- [개발 후속 응답](2026-10-03-followup-response.md)

## 확인 요청표

개발 작업자는 이 요약이 합의 내용과 다른 부분이 있으면 표시한다. 구현을 시작하면 `improvements/NNN` 번호를 기록한다.

| ID | 합의 사항 | 요약 확인 | 구현 기록 |
|---|---|---|---|
| D-01 | 배포·실행 정본 | 확인 | [058](../improvements/058-deployment-config-unification.md) — 구현·로컬 검증 / 미병합·미배포 |
| D-02 | 설정 정책과 예제 정합성 | 확인 | [058](../improvements/058-deployment-config-unification.md) — 구현·로컬 검증 / 사내 배포 미검증 |
| D-03 | Run 접수·실행·취소 분리와 도메인 예외 | 확인 | 미착수 |
| D-04 | 짧은 Unit of Work | 확인 | 미착수 |
| D-05 | 공통 GraphInvocation·상태 반영 (W-02) | 확인 | 미착수 |
| D-06 | 실행 구조: 입구 둘, 공통 스케줄러 하나 | 확인 | 미착수 |
| D-07 | DB 실행 원장 + best-effort 깨우기 신호 | 보완 확인 — 아래 C-01 | 미착수 |
| D-08 | 같은 DB, 한 connection·한 transaction 기록 | 조건부 확인 — 아래 C-02 | 미착수 |
| D-09 | 스케줄러 인수 조건 | 보완 확인 — 아래 C-03 | 미착수 |
| D-10 | 상태 구조화와 checkpoint 저장량 판단 기준 | 보완 확인 — 아래 C-04 | 미착수 |
| D-11 | 보존 원칙 | 확인 | 미착수 |
| D-12 | 정리 작업 범위 | 확인 | 미착수 |

## 진행 순서

1. **배포·설정 정합성**: D-01, D-02
2. **Run 책임 분리와 공통 실행·상태 반영 추출**: D-03, D-04, D-05
3. **공통 명령 스케줄러**: D-06, D-07, D-08, D-09
4. **같은 총한도에서 기능·성능 검증**: 혼합 요청, 결과 폭주, 모델 지연, 중복 이벤트, 프로세스 종료와 재전달, 장기 대기 중 실행 자리 반환
5. **상태·설정·Agent 조립·레거시 정리**: D-10, D-12

F-01 저장량 실측은 5단계 상태 저장 방식을 변경하기 전에 선행한다. 스케줄러 구현의 선행 조건은 아니다.

---

## 배포와 설정

### D-01. 배포·실행 정본 (R-01, R-03)

- 정본은 **단일 컨테이너, `app.py` → `service_bootstrap` 단일 bootstrap**이다.
- 별도 Deployment와 sidecar 예시는 정본에서 제외한다.
- uvicorn을 직접 기동하면 조기 SIGTERM drain 훅을 우회하므로 정본에서 쓰지 않는다.
- readiness와 liveness를 구분하고, 종료 유예 시간을 drain 설정과 맞춘다.
- 정본의 기본 프로세스 수는 1로 하고, 프로세스별 실행 한도와 pool 예산을 명시한다.
  - 멀티프로세스 자체는 버그가 아니다. 다만 백그라운드 루프와 pool이 프로세스 수만큼 배로 늘어난다.
- `EW_INSTANCE_ID`는 고정값을 주입하지 않는다. 기본 UUID를 쓰거나 process/startup UUID를 포함한다.

### D-02. 설정 정책과 예제 정합성 (R-02, R-08)

- **YAML > env > 기본값 우선순위는 유지한다.** 포트 문제는 우선순위를 뒤집지 않고, 정본 YAML과 배포의 port, probe, Service를 일치시켜 해결한다.
- 재현된 예제 오류를 수정한다.
  - checkpoint alias 충돌
  - `APP_ENV=loadtest`
  - cicd Dockerfile 문법과 누락 의존성
  - manifest 오타
- 검증 범위: `--check-config`에 더해 manifest schema 검사, 이미지 빌드, 기동 smoke 검사를 포함한다.
- 의존성 기준은 pyproject와 lock이다. 폐쇄망 사정에 따라 lock에서 생성한 requirements를 쓸 수 있다.
- 설정 원천 로더는 이미 하나다. 다음을 정리한다.
  - 중복되거나 가려진 기본값과 전역 설정 접근을 정리한다.
  - API, Agent, 이벤트, SSO별 typed snapshot은 유지하되, 공용 계층에는 명시적으로 주입한다.

## 서비스 내부

### D-03. Run 접수·실행·취소 분리 (R-04)

- `RunService.create`의 `_execute_existing` 분기를 제거하고, 접수, 실행, 취소를 명시적인 서비스 인터페이스로 나눈다.
- 외부 모듈이 `RunService` 비공개 메서드를 호출하지 않게 한다.
  - 예: `executor_completion`이 `_lock_run_and_task`, `_finish_run`, `_finalize_state`를 직접 호출하는 경우
- 도메인 예외를 정의하고, HTTP로의 변환은 경계 한 곳에서 한다.

### D-04. 짧은 Unit of Work (R-05)

- UoW는 **짧은 DB 작업 단위**다. 다음 흐름을 유지한다.
  1. DB 접수 commit
  2. 연결 반환
  3. graph 실행
  4. 별도의 결과 저장 commit
- graph, LLM, Executor HTTP를 기다리는 동안 transaction이나 DB 연결을 잡고 있지 않는다.
- SQLAlchemy, psycopg, checkpoint commit을 하나의 원자 단위로 묶는다고 보장하지 않는다.
- 쿼리 소유와 commit 경계를 통일하는 것이 목표다. 모든 쿼리를 한 줄 repository wrapper로 옮기는 것 자체는 목표가 아니다.

### D-05. 공통 GraphInvocation과 상태 반영 (W-02, R-09)

- 두 실행 경로가 공유하는 다음 요소를 공통 모듈로 추출한다.
  - 모델 pin 검증
  - 프로젝트 컨텍스트
  - `submission_scope`
  - 상태 반영
  - status 매핑
- 보존하며 검증할 것:
  - checkpoint receipt
  - 짧은 DB transaction
  - 모델 pin
  - 공개 Run ID
- graph 조립 인자와 수명 소유를 공통화한다. 내장 Worker는 이미 graph와 pool을 공유한다는 점을 전제로 한다.

## 실행 구조

### D-06. 입구 둘, 공통 스케줄러 하나 (W-01, W-03, W-05)

- **입력 경로는 둘로 유지한다.**
  - 사용자 요청: API 접수
  - Executor 이벤트: Redis 수신 → inbox
- **graph 실행은 공통 스케줄러 하나로 일원화한다.** 새 요청, HITL 재개, Executor 결과 재개를 같은 총한도로 배분한다.
- 내부 실행 명령은 user start, user resume, executor event resume으로 구분한다.
- **공개 Run과 내부 실행 명령 원장은 분리한다.** 공개 Run ID와 API 계약은 유지한다.
  - 기존 `ew_commands`를 재사용할지, 확장 범위를 어떻게 할지는 상태·재시도·migration 계약을 확인한 뒤 정한다.
- Executor 이벤트의 수신 책임은 기존 inbox 구조에 그대로 둔다: 영속화, 중복 제거, sequence 검증, DLQ.
- 이벤트 수신부에서는 graph 실행 의존을 제거한다. 별도 Deployment가 아니라 단일 컨테이너 안에서 역할을 경량화한다.
- operation 완료와 execution 완료 이벤트를 무조건 합치지 않는다. 선행 이벤트가 적용된 것을 receipt로 확인한 뒤, 의미가 없어진 이벤트만 건너뛴다.

### D-07. DB 실행 원장 + best-effort 깨우기 신호 (F-02)

- 명령의 상태, session 소유권, 재시도, 완료는 **DB 원장만 기준**으로 삼는다.
- 알림은 작업의 유일한 사본이 아니다. 명령 본문과 command identity를 먼저 DB에 commit한다.
- 알림은 best-effort 깨우기 신호로만 쓴다. 유실되면 시작 시점, 재연결, 실행 완료, 재예약 기한, 제한된 주기 scan으로 발견한다.
- Redis Streams를 신호로 쓰는 경우:
  - graph 실행 완료까지 ACK를 미루지 않는다.
  - DB claim commit과 로컬 실행 인계가 끝나면 ACK한다.
  - ACK는 업무 성공을 뜻하지 않는다.
- **알림 수단으로는 PostgreSQL LISTEN/NOTIFY를 우선 검토한다.**
  - payload에는 식별자나 변경 신호만 넣는다.
  - LISTEN 등록을 완료한 뒤 DB를 다시 조회한다.
  - LISTEN 연결의 수명은 Worker lifespan이 소유한다. 현재 RunStreamHub처럼 SSE 구독자가 있을 때만 유지되는 연결에 의존하지 않는다.
  - replica 수만큼 fan-out이 생기므로 알림 합치기, jitter, 실행 자리가 있을 때만 claim, 빈 scan backoff를 검증한다.
  - Redis consumer group은 비교 대안으로 남긴다. 처리량 비교는 하지 않았다.
- claim 직후 crash: 알림 구조만으로는 복구되지 않는다.
  - 현재 정책(heartbeat가 만료돼도 소유권을 빼앗지 않고 `recovery_required`로 둔다)을 유지하거나, 안전한 takeover를 따로 설계한다.

### D-08. 같은 DB, 한 connection·한 transaction 기록 (F-03)

- 신규 구조는 API 테이블, 내부 명령 원장, inbox를 **같은 PostgreSQL database**에 둔다.
- 다음 두 기록은 각각 **한 connection의 한 transaction**에서 한다. 같은 DB라도 SQLAlchemy와 psycopg가 별도 connection에서 commit하면 원자적이지 않다.
  - 이벤트 처리 완료와 명령 기록
  - 사용자 접수와 명령 기록
- DB가 같은지는 DSN 문자열 비교로 판정하지 않는다. 공통 DB 설정 원천과 배포 계약으로 제한한다.
- 다음 항목은 이 제약의 범위가 아니다.
  - Executor 서비스 자체의 DB
  - LangGraph checkpoint와 Store의 DB 위치
  - connection pool 통합
- unique command, 이벤트 sequence, session 실행 소유권, checkpoint receipt, Executor idempotency는 같은 DB에서도 그대로 유지한다.
- 내부 깨우기 신호를 위해 별도의 durable Outbox를 새로 둘 필요는 없다. 외부 부작용이나 다른 DB로의 전달에는 계속 필요할 수 있다.
- **미결**: `chat_app`과 `agent`를 분리해야 하는 운영상 사유는 아직 확인되지 않았다. 기존 분리 DB를 실제로 이전할 때는 권한, migration, 진행 중 명령 처리 계획을 먼저 확인한다.

### D-09. 스케줄러 인수 조건

개발 응답의 검증 항목에 리뷰어 후속 검토의 보완을 더한 목록이다. 스케줄러 설계 문서와 테스트는 최소한 다음을 다룬다.

1. **session 순서**
   - `created_at` + SKIP LOCKED만으로는 session 단위 FIFO가 보장되지 않는다.
   - claim 조건에 다음 규칙을 둔다: 같은 session에 **자신보다 앞선 미종료 명령이 있으면 claim하지 않는다**. 리뷰어 보완 사항이다.
   - 재예약(`next_attempt_at`)된 명령을 뒤 명령이 추월하지 않는지 검증한다.
2. **checkpoint 준비**: 제출 결과가 Executor 대기 checkpoint보다 먼저 도착하면 eligibility 판단과 재예약으로 처리한다. 현재의 1초 handoff를 대체하되, 준비 상태 검증 자체는 없애지 않는다.
3. **멱등**: unique command ID로 enqueue 중복을 막는다. graph와 외부 부작용의 멱등은 checkpoint receipt와 Executor idempotency로 보장한다.
4. **알림 유실**: 알림 없이도 polling과 재확인으로 명령을 발견한다.
5. **claim 직후 crash**: `recovery_required`로 전환되고, 자동 탈취가 일어나지 않는다. takeover를 설계하는 경우에는 기존 writer가 종료됐는지 확인하는 절차를 포함한다.
6. **알림 fan-out**: replica가 여러 개일 때 claim 경합과 빈 조회가 제한된다.
7. **실행 자리 반환**: HITL이나 Executor 대기 중에는 실행 자리를 반환한다. 결과가 며칠 뒤에 오더라도 새 명령으로 재개한다.
8. **공통 총한도**: 새 요청, HITL, Executor 결과가 같은 한도 안에서 공정하게 배분된다.

## 상태와 정리

### D-10. 상태 구조화와 checkpoint 저장량 판단 기준 (R-06, F-01)

- `PlanningState`를 역할별 타입으로 구조화하고 이름을 정리한다(예: AnalysisState). 목적은 가독성과 타입 안정성이며, 저장량 최적화가 아니다.
  - 큰 nested channel 하나로 합치면 작은 변경에도 묶음 전체가 새 버전으로 저장될 수 있다.
- saver는 바뀐 channel만 저장하지만, 누적 list channel은 바뀔 때마다 전체가 다시 직렬화된다.
  - append reducer(`operator.add`)로 바꿔도 checkpoint에는 누적 전체 값이 저장되므로 **용량 해법이 아니다**.
  - 현재 `observations`와 `reviews`는 새 요청마다, `public_events`는 대기 지점마다 초기화된다. 따라서 증가 구간은 한 실행 안의 연속 operation에 한정된다.
- 저장 방식을 바꾸기 전에 **F-01 실측**을 먼저 한다.
  - 대표 시나리오: 새 요청 → 승인 → MULTI → repair 1회 → 보고서 → 후속 질문 2회
  - 측정 항목:
    - `checkpoint_blobs` 합계와 증분
    - channel별 버전 수
    - 바이트 상위 channel
    - `checkpoints`, `checkpoint_writes`
    - 저장 시간 비중
- 함수와 Skill 원문을 hash 참조로 바꾸려면, 재배포 후에도 원문을 찾을 수 있는 불변 저장소와 revision 보존 계약이 먼저 있어야 한다.
- `execution_enabled`에 따라 topology가 바뀌는 문제는 다음 둘 중 하나로 해결한다.
  - topology를 고정하고 실행 허용 정책을 분리한다.
  - runtime version별 재개 호환 검사를 둔다.

### D-11. 보존 원칙

- `analysis/workflow`와 그 아래 Skill, Tool, Workflow 작성 자산 및 위치는 유지한다.
- 역할별 `agent.py`와 독립 prompt 배치를 유지한다.
  - 실제로 API와 공유하는 계약만 `service_contracts`에 둔다.
  - 내부 역할 응답 schema는 해당 역할 근처에 둘 수 있다.
- 운영 부하테스트용 mock 주입은 테스트 provider 경계로 정리하되, 없애지는 않는다.
- 단일 컨테이너 배포 제약을 유지한다.
- Git 이력을 재작성하거나 force push하지 않는다.
- 기존 benchmark 원본은 이관 위치와 hash 검증 전에 삭제하지 않는다.
- Dataset Registry draft는 보류된 기능 계약이므로 폐기하지 않는다. 계약 문서나 검증 도구 영역으로 옮길지는 따로 판단한다.

### D-12. 정리 작업 범위 (R-07, R-10~R-15)

- **projection**: 현재 agentic 경로의 projection 계약(`public_events`, envelope)을 명시한다. 남은 state 의존과 레거시 분기(`workflow_recommender`, `workflow_generator` 등)를 정리한다.
- **Agent 조립**: 역할 레지스트리와 의존 방향을 정리한다. 캐시 키는 role, model revision, variant가 섞이지 않게 하고, 같은 초기화 절차를 쓴다.
- **레거시**: 실제로 쓰이지 않는 1.3 실행 코드, stub, 중복 compiler를 제거한다. import 도달률은 삭제 비율이 아니다. Tool 원문은 AST로 읽으므로 import 분석에 잡히지 않는다.
- **자산 패키징**: tmp 파일과 생성 도구의 역할을 정리한다. `__init__.py`를 제거한다면 wheel에 원문이 포함되는지와 registry 상대 경로를 검증한다.
- **죽은 코드와 의존성**:
  - `src/routers/chat`: 플랫폼 참고 예제라면 docs로 옮긴다.
  - `python-dotenv`를 직접 의존성으로 선언한다.
  - 불필요한 의존성과 루트 파일을 정리한다.
- **저장소**: 새로 생기는 대용량 원본은 외부 artifact로 보관한다.
- **테스트**:
  - 공용 fixture는 conftest로, 직접 호출하는 helper는 명시적인 harness 모듈로 옮긴다.
  - PostgreSQL과 Redis marker를 추가한다.
  - 테스트 파일끼리 구현에 의존하는 결합을 먼저 제거한다.
  - 디렉토리 전면 이동이나 unittest 일괄 전환은 필수가 아니다.

## 미결 항목

| 항목 | 담당 | 비고 |
|---|---|---|
| F-01 대표 시나리오 저장량 실측 | 개발 | D-10 상태 저장 방식 변경 전 |
| `chat_app` / `agent` DB 분리의 운영 사유 확인 | 개발·운영 | D-08 |
| `ew_commands` 재사용·확장 범위 | 개발 | D-06 스케줄러 설계 시 |
| LISTEN/NOTIFY와 Redis consumer group 비교 | 개발 | D-07, 필요할 때만 |
| 폐쇄망 환경의 uv 사용 가능 여부 | 운영 | D-02 의존성 설치 방식 |

## 이후 리뷰 방식

문서상의 구조 논의는 이 합의로 마무리한다. 이후 리뷰는 구현 변경분과 해당 `improvements/NNN` 기록을 대상으로 하며, 위 D-xx 항목과 D-09 인수 조건을 기준으로 확인한다.

## 개발 확인 및 구현 시 적용할 보완 (2026-10-03)

- 검토 기준: `353f7a8`. 원격 변경은 합의 문서와 인덱스 2개뿐이며 운영 소스는 동일하다.
- 결과: 8개 항목 확인, 3개 보완 확인, 1개 조건부 확인. 아래 C-01~C-04를 포함하면 구현 기준으로 사용할 수 있다. 원 리뷰 본문은 보존했다.
- 검토 범위: 이전 응답과의 대조, 명령 claim/Run 종료 반영/세션 소유권 반환/상태 reset 코드 확인. 이번 작업은 문서 변경뿐이고 테스트·부하·배포를 새로 실행하지 않았다. F-01 실제 DB 측정도 미실행이다.
- 기존 진행 순서를 유지한다. 추가 구조 논의를 반복하기보다 D-01/D-02부터 구현 변경분으로 검토하는 것이 적절하다.

### C-01. D-07의 ACK는 실행 명령의 완료와 분리

`claim commit + 안전한 실행 인계 후 ACK`는 사용할 수 있는 한 방식이다. 순수 wakeup으로 정의한 메시지라면 원장 재확인을 예약한 뒤 ACK하는 방식도 앞선 후속 응답에 포함돼 있다.

다른 프로세스가 이미 claim했거나 현재 실행 가능한 명령이 없는 경우, 신호 하나에 대응하는 claim을 반드시 성공시킬 때까지 PEL에 남기지 않는다. slot 반환·재예약 기한·주기 scan이 DB 원장을 다시 확인한다. 해당 규칙은 내부 wakeup에 대한 것이며, 외부 Executor 이벤트를 Inbox에 영속화하기 전 ACK해도 된다는 의미가 아니다.

실제 선택안은 scheduler 구현에서 하나로 명시하고 `중복 신호`, `다른 Pod가 이미 점유`, `slot 없음`, `ACK 전후 crash`를 확인한다. LISTEN/NOTIFY를 선택하면 이 내부 신호 ACK 자체가 없다.

### C-02. D-08은 신규 구조의 권장 전제이며 기존 DB 이전 승인은 아님

API·내부 명령·Inbox를 같은 database에 두는 방향은 권장한다. 다만 운영상 분리 사유가 없는지는 미확인이고, 현재 명령·이벤트가 있는 DB를 즉시 이전하는 것까지 승인·완료됐다는 뜻은 아니다. 같은 서버의 다른 database도 한 로컬 transaction으로 묶을 수 없다.

신규 구조의 정본은 공통 DB 원천을 사용하도록 설계할 수 있다. 실제 전환 전에 기존 API/EW 대상 DB, 권한, 미처리 명령과 Inbox, migration 실행 위치를 확인한다. checkpoint/Store·Executor 자체 DB·모든 pool의 통합은 이 결정에 포함하지 않는다.

### C-03. D-09의 순서 기준은 내부 실행 명령이며 공개 Run의 완료가 아님

추월 금지 규칙의 방향은 수용한다. 다만 다음 수명을 구분해야 한다.

- **공개 Run/Task**: HITL 또는 Executor 대기에서도 전체 업무는 미완료다.
- **내부 실행 명령**: 그래프가 다음 대기 지점까지 실행되고 checkpoint·API 상태가 반영돼 소유권을 반환하면, 그 명령의 실행 구간은 완료된다.

예:

```text
명령 1: 새 요청 → 계획 제시 → HITL 저장 → 명령 1 완료
        공개 Run은 사용자 입력 대기
명령 2: 사용자 승인 → Executor 제출 → 대기 상태 저장 → 명령 2 완료
        공개 Run은 Executor 대기, 동일 세션의 새 사용자 입력은 잠김
명령 3: Executor 결과 → 그래프 재개 → 다음 대기 또는 최종 결과 → 명령 3 완료
```

`앞선 미종료`를 공개 Run의 SUCCESS/ERROR 여부로 판단하면, 같은 Run을 이어가는 명령 2와 3이 앞선 Run 완료를 기다리면서 진행할 수 없게 된다. 이는 현재 코드의 재현된 버그가 아니라 새 scheduler 구현에서 피해야 할 해석이다. 현재도 RunService는 interrupt 상태를 반영하고 session_execution.run_owned는 해당 호출 종료 후 실행 소유권을 반환한다.

인수 조건을 다음처럼 구체화한다.

1. 순서 키는 session별 내부 명령 순서를 명시적으로 나타내야 한다. `created_at` 동률/수신 순서만으로 원본 이벤트 sequence를 대체하지 않는다. 구체적인 sequence 할당 방법은 scheduler schema 설계에서 정한다.
2. 같은 session의 **앞선 내부 명령이 처리 중 또는 재시도 대기이면** 뒤 일반 실행 명령은 claim하지 않는다. 선행 명령을 확인할 때 next_attempt_at이 미래인 행을 제외하지 않는다. 다른 session의 작업은 계속 처리할 수 있어야 한다.
3. HITL/Executor 대기 checkpoint까지 저장한 내부 명령은 완료로 취급한다. 다음 승인/결과 명령의 허용 여부는 현재 대기 종류와 identity 검증으로 판단한다. 명령 완료가 사용자 세션 잠금 해제를 뜻하지 않는다.
4. 중복/오래된 이벤트의 무시, 영구 거절, 취소는 원장에 명시적인 처리 결과로 남겨 선행 명령이 무의미하게 뒤 명령을 영구 차단하지 않게 한다. recovery_required 소유권은 예외로, 기존 writer 안전성을 확인하기 전 자동 해제하지 않는다.
5. 취소·복구 같은 제어 행위를 앞으로 같은 큐에 넣는 경우에는 선행 작업을 중단/해소할 수 있는 별도 처리 규칙을 둔다. 현재 취소 경로까지 이번 확인만으로 새 FIFO 큐에 편입시키지는 않는다.

### C-04. D-10의 reset은 과거 checkpoint 저장량을 줄이지 않는다

본문의 `증가 구간은 한 실행 안의 연속 operation에 한정`은 저장량 전체에 대한 설명으로 쓰면 부정확하다.

- observations는 새 요청에서 초기화되고 operation 결과를 처리할 때 누적된다.
- reviews는 후보 계획·편집·재제안으로 교체된다. operation 결과만으로 변경되는 목록이 아니다.
- public_events는 특정 대기/재개 처리에서 초기화되거나 교체되며, 계획·HITL·보고서 처리 중에도 새 버전이 생긴다.
- 현재 state에서 리스트를 비워도 이전 checkpoint/blob 버전이 삭제되지는 않는다. 따라서 동일 thread의 과거 저장량은 여러 요청과 재개에 걸쳐 누적된다.

정확한 기준은 `channel마다 전체 값을 다시 저장하는 구간과 reset/교체 시점이 다르므로, channel·요청·실행 구간별 증가를 측정하고 thread 전체의 보존된 버전 바이트를 함께 집계한다`이다. 앞선 reducer 비교 실험과 F-01 대표 시나리오 측정 계획은 유지한다.
