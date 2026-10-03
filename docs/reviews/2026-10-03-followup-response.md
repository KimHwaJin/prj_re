# 리뷰어 후속 의견 F-01~F-03에 대한 개발 응답

- 날짜: 2026-10-03
- 기준: `feature/refactor-base` @ `aee6407` (이전 `fb61227`에서 문서 4개만 추가·갱신)
- 응답 브랜치: `feature/structure-review-followup`
- 대상: [리뷰어 후속 의견](2026-10-03-reviewer-followup.md)
- 범위: 코드·설치된 saver 대조, 외부 DB 접속 없는 직렬화 실험, 권고 갱신. 운영 소스·설정·migration은 변경하지 않았다.

## 판단

F-01의 측정 필요성과 F-02의 실행 수명 단일화에 동의한다. F-03의 같은 DB 제약도 현재 배포·개발 여건에 적합한 권장안이다. 다만 운영상 DB를 분리해야 하는 사유가 없다는 사실까지 확인한 것은 아니다.

특히 F-02에 따라 이전의 `DB 명령 원장 + Redis 실행 전달 + 실행 완료 뒤 ACK` 권고를 수정한다. DB에 실행 상태와 세션 소유권이 이미 있으므로, 내부 실행까지 Redis PEL 수명에 묶지 않는 편이 단순하다. Redis 기반 전달 자체가 잘못된 것은 아니지만 현재 서비스에서는 두 계층의 실행 복구 책임을 유지할 이점이 충분히 확인되지 않았다.

## F-01: 쓰기 증폭을 측정하되 reducer만 바꾸지 않는다

### 확인한 내용

설치된 BinaryOperatorAggregate의 update는 operator로 합친 전체 값을 self.value에 저장하고, checkpoint는 그 self.value 전체를 반환한다. PostgreSQL saver의 `_dump_blobs` 역시 channel의 현재 전체 값을 직렬화한다. 따라서 일반 `Annotated[list, operator.add]`로 바꾸는 것은 새 원소를 편하게 반환하도록 돕지만, 저장 형식을 자동으로 delta 방식으로 바꾸지는 않는다.

설치된 saver의 실제 직렬화 함수를 이용해 다음 두 방법의 결과를 비교했다.

- 기존 전체 교체: `values = [*values, new_item]`
- append reducer: `BinaryOperatorAggregate(list, operator.add).update([[new_item]])`

각 항목은 `sequence`와 1,024자의 text를 가진 dict이며, 추가할 때마다 새 channel version을 만들었다. 모든 버전의 serialized blob payload가 두 방법에서 완전히 동일했다.

| 누적 항목/버전 수 | 마지막 blob 바이트 | 전체 교체의 누적 blob 바이트 | operator.add의 누적 blob 바이트 |
|---:|---:|---:|---:|
| 1 | 1,044 | 1,044 | 1,044 |
| 10 | 10,431 | 57,375 | 57,375 |
| 50 | 52,153 | 1,329,945 | 1,329,945 |
| 100 | 104,303 | 5,267,420 | 5,267,420 |

이는 **단순 누적 조건에서 직렬화 payload가 제곱에 가까운 증가를 보일 수 있다는 근거**다. 실제 checkpoint_blobs SQL 합계, PostgreSQL TOAST/압축·인덱스·WAL을 포함한 물리 저장량, API 응답시간은 측정하지 않았다. 반복 문자 fixture의 압축률도 실데이터와 다르다. 이 표를 서비스 저장량이나 성능 개선 수치로 사용하면 안 된다.

재현 명령(설치된 프로젝트 Python 환경, DB 접속 없음):

```python
import asyncio
import operator
from langgraph.channels.binop import BinaryOperatorAggregate
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def probe():
    saver = AsyncPostgresSaver(None)  # 직렬화만 호출; 연결/SQL 사용 없음
    for count in (1, 10, 50, 100):
        channel = BinaryOperatorAggregate(list, operator.add)
        values, total = [], 0
        for i in range(count):
            item = {"sequence": i, "text": "x" * 1024}
            values = [*values, item]
            channel.update([[item]])
            version = {"observations": str(i)}
            replace_rows = saver._dump_blobs("synthetic", "", {"observations": values}, version)
            append_rows = saver._dump_blobs("synthetic", "", {"observations": channel.checkpoint()}, version)
            assert replace_rows == append_rows
            total += sum(len(row[-1]) for row in replace_rows)
        print(count, len(replace_rows[0][-1]), total)

asyncio.run(probe())
```

이 코드는 현 설치 버전의 내부 직렬화 동작을 확인하는 진단용이다. 운영 구현에서 saver의 private API에 의존하자는 제안이 아니다.

### 현재 channel을 구분해야 하는 이유

[planning/graph.py](../../src/agent_service/agents/analysis/planning/graph.py)의 receive는 새 요청에서 observations/reviews를 초기화하고, await_review 등은 public_events를 비운다. reviews는 후보 목록을 교체하며 observations와 동일하게 매 실행 결과를 누적하는 channel이 아니다. 따라서 세 channel 모두가 thread 전체 기간 동안 무한 append된다는 전제로 계산하면 부정확하다.

단순 operator.add 적용 시 기존 `[]` 초기화는 더 이상 목록을 비우지 못한다. 현재처럼 전체 누적 목록을 반환하는 노드에 append reducer를 붙이면 기존 원소가 중복될 수도 있다. reducer 도입은 reset/overwrite·노드 반환·복구 동작을 함께 바꿔야 한다.

### 다음 실측에서 확인할 항목

요청된 대표 시나리오(새 요청 → 승인 → MULTI → repair 1회 → 보고서 → 후속 질문 2회)를 고정하고 단계마다 다음을 측정한다.

- 동일 thread의 `checkpoint_blobs` payload 바이트 합계와 증분, namespace/channel별 버전 수, 바이트 상위 5개 channel.
- `checkpoints`, `checkpoint_writes`도 별도 집계해서 전체 저장 비용을 누락하지 않는다.
- 합성 fixture와 구분해서 실제 Tool 원문·Skill·관찰 크기·이벤트 수·reset 시점을 기록한다.
- checkpoint 저장 시간과 실행 시간 비중을 함께 측정한다. 바이트 증가만으로 현재 처리량 병목이라고 단정하지 않는다.

그 결과로 상태의 근거 데이터 외부화·범위 제한·지원되는 delta 저장 방식·이력 보존 정책을 선택한다. 승인 원문 고정, HITL/Executor 대기, receipt 기반 복구에 필요한 checkpoint를 먼저 삭제하지 않는다. 이 실측은 이번 문서 검토에서 완료하지 않았으며 상태 저장 방식 변경 전에 수행할 후속 작업이다.

## F-02: DB 실행 원장 + 깨우기 신호를 수용

권장 구조:

```text
사용자 요청 ───────────→ DB 내부 실행 명령 ──┐
                                            ├→ 공통 스케줄러 → graph 실행
Executor Redis → Inbox → DB 내부 실행 명령 ─┘
                          │
                          └→ 알림: 명령이 있으니 원장을 확인

명령·세션 점유·재시도·완료: DB가 기준
알림 유실: 시작/재연결/주기 재확인으로 발견
```

### 세부 조건

- 알림은 유일한 작업 사본이 아니다. 본문과 command identity는 먼저 DB에 commit한다.
- Redis Streams를 신호로 사용한다면 graph 실행 완료까지 기다리지 않고, DB claim **commit** 및 로컬 실행 인계가 안전하게 끝난 시점에 ACK할 수 있다. ACK는 업무 성공을 뜻하지 않는다. 순수 wakeup은 원장 재확인을 예약한 뒤 ACK해도 되며, 정확성은 DB 원장에 있어야 한다.
- 다른 프로세스가 이미 claim했거나 실행 자리가 없어 claim하지 못한 신호도 실행 명령과 일대일로 묶어 무기한 PEL에 남길 필요가 없다. 자리 반환 시 재확인과 주기 polling이 남은 DB 명령을 찾는다.
- 자리가 없을 때 명령을 대량 선점하지 않는다. 신호를 합치고 실행 가능 자리만큼 claim한다. 시작·재연결·실행 완료·재예약 기한·제한된 주기 scan이 다음 실행을 깨운다.
- ACK 직후 프로세스가 죽으면 PENDING scan만으로 RUNNING 명령을 복구할 수 없다. DB의 stale 실행 감지, 기존 writer 종료 확인, receipt 기반 결과 반영/재개 절차가 필요하다. 현재 session_execution과 reconciler는 heartbeat 만료만으로 소유권을 탈취하지 않고 recovery_required로 남긴다. 이 정책을 유지하거나 안전한 takeover를 별도로 설계해야 하며, 알림 구조 변경만으로 자동 장애 복구가 완성됐다고 하면 안 된다.
- 외부 Executor 이벤트의 ACK는 별도 의미다. 원본 이벤트를 Inbox에 영속화한 뒤 처리 확인하는 수신 책임과 중복/순서 검증은 유지한다.

### 알림 수단 권고

F-03의 같은 DB를 전제로 하면 **PostgreSQL LISTEN/NOTIFY를 우선 검토**한다. 명령 본문은 DB에서 읽고, NOTIFY payload에는 간단한 식별자 또는 변경 신호만 넣는다. PostgreSQL은 transaction 안의 NOTIFY를 commit 후 전달하며, 새 LISTEN 연결은 등록을 완료한 뒤 DB를 재조회해야 초기 경합을 놓치지 않는다. [NOTIFY 공식 문서](https://www.postgresql.org/docs/current/sql-notify.html), [LISTEN 공식 문서](https://www.postgresql.org/docs/current/sql-listen.html)

현 [RunStreamHub](../../src/api_service/services/run_stream_service.py)도 알림 + 주기 재확인 패턴을 사용한다. 다만 SSE 구독자가 있을 때만 연결이 살아 있으므로 그대로 Worker 알림 수명에 의존하면 안 된다. 알림 기반 시설을 공통화하더라도 Worker lifespan이 연결을 유지하도록 해야 한다. 연결 공유를 구현하기 전부터 DB 연결 추가가 0개라고 계산하지 않는다.

LISTEN/NOTIFY는 listener 전체에 전달되므로 replica 수에 따라 불필요한 동시 claim 조회가 늘 수 있다. 알림 합치기·작은 jitter·slot이 있을 때만 claim·empty scan backoff를 검증한다. Redis 소비 그룹은 신호 분배를 제어할 여지가 있으므로 비교 대안으로 남긴다. 이번 검토에서 두 방식의 처리량을 비교하지 않았고 어느 쪽이 빠르다고 확정하지 않는다.

### 가용성 표현 보완

리뷰의 “현재 사용자 경로는 PostgreSQL만 필요”는 실행 예약 부분에 한정해서 읽어야 한다. 실제 HTTP 경로는 이미 Redis 기반 SSO session을 조회한다. 또한 이전 Outbox 안에서도 API가 Redis 발행 성공을 기다리지 않고 DB commit으로 접수를 끝내면 접수 자체가 곧바로 Redis 발행에 묶이는 것은 아니다. 다만 발행을 유일한 실행 전달 경로로 삼으면 예약된 작업의 실행 진행이 Redis에 의존한다. 새 권고는 이 추가 실행 의존성과 PEL 실행 수명을 줄이는 데 의미가 있다.

## F-03: 같은 DB를 신규 구조의 권장 전제로

현재까지의 사용자 설명·레포에서 API와 이벤트 명령 원장을 반드시 다른 DB에 둬야 한다는 요구는 확인하지 못했다. 따라서 신규 구조는 API 테이블·내부 명령·Inbox를 같은 PostgreSQL database에 두는 안을 권장한다. 운영 정책 부재를 확인한 것으로 해석하지 않으며, 기존 분리 DB를 실제 이전할 때는 권한·migration·진행 중 명령 처리 계획을 먼저 확인한다.

같은 DB여도 SQLAlchemy와 psycopg가 각각 별도 connection에서 commit하면 원자적이지 않다. 이벤트의 처리 완료와 명령 기록, 사용자 접수와 명령 기록은 각각 **한 connection의 한 transaction**에서 처리해야 한다. 이름·host alias가 다른 DSN 문자열을 단순 비교해 DB 동일성을 확정하지 않고, 공통 DB 설정 원천과 검증 가능한 배포 계약으로 제한한다.

이 제약은 다음을 뜻하지 않는다.

- Executor 서비스 자신의 DB를 옮기거나 합친다는 뜻이 아니다.
- LangGraph checkpoint/Store까지 반드시 같은 DB로 옮긴다는 뜻이 아니다. 같은 DB를 쓰더라도 별도 saver transaction은 실행 원장과 자동으로 원자화되지 않는다.
- 모든 라이브러리를 하나의 DB pool로 강제한다는 뜻이 아니다. DB 배치와 connection pool 수명은 다른 결정이다.

unique command, 이벤트 sequence, session 실행 소유권, checkpoint receipt, Executor idempotency는 유지한다. PostgreSQL 명령이 확실히 남고 알림만 best-effort라면 내부 깨우기 신호를 위해 durable Outbox를 새로 둘 필요는 없다. 외부 부작용·다른 DB로의 신뢰성 있는 전달에는 Outbox가 여전히 필요할 수 있다.

## 진행 순서와 검토 완료 범위

기존 순서인 배포 설정 정합성 → W-02 공통 GraphInvocation/상태 반영 → 공통 명령 스케줄러를 유지한다. 스케줄러 설계의 기본안을 DB 원장 + wakeup으로 갱신한다. F-01 대표 저장량 측정은 상태 저장 최적화의 선행 작업이며 scheduler 구현의 필수 선행 조건은 아니다.

이번에 수행한 것은 원격 문서 반영, 후속 3개 항목 코드 대조, installed channel/saver 직렬화 비교, PostgreSQL 공식 알림 의미 확인이다. 대표 시나리오 DB 측정·Redis 장애·claim 직후 crash·다중 Pod 알림 fan-out·부하 검증은 미실행이다. 운영 코드는 바꾸지 않아 직전 49개 회귀를 이번 작업에서 재실행하지 않았다.
