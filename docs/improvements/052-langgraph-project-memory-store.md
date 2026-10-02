# 052 프로젝트 메모리 공식 LangGraph Store 전환

| 항목 | 내용 |
|---|---|
| 상태 | 구현·전체 회귀·실제 모델/Store·wheel 검증 완료, 통합·게시 준비 |
| 시작일 | 2026-10-02 |
| 브랜치 | feature/project-memory-langgraph-store |
| 출발 commit | cba215f4adce281ff21f9f62ccd5d0e751bec02d |
| 배포 상태 | 미배포, 기존 Docker·운영 DB 유지 |

## 요청과 설계 판단

사용자는 프로젝트 메모리를 처음부터 LangGraph Store를 사용한 구조로 전환하고 불필요한 기존 구현을 남기지 말라고 요청했다. 051의 전용 ORM 모델/저장 backend/접근 Protocol을 그대로 감싼 BaseStore 어댑터는 만들지 않는다. 실제 저장은 공식 `AsyncPostgresStore`, Agent 접근은 `create_agent(store=...)`와 `runtime.store`로 변경했다.

메모리 내용 선택과 저장 수단은 별개다. 이번 작업은 저장·접근 구조 전환이며 051의 manual 기본값과 선택적 원문 추출 범위를 유지한다. 생성형 요약·분석 결과 자동 공유를 구현했다고 표시하지 않는다. Store에 담을 별도 제품 기능을 새로 만들거나 Workflow JSON/원시 파일/실행 로그 전체를 옮기지 않는다.

공식 Store는 세션을 넘는 JSON 문서 저장소이며 Checkpointer는 실행 상태·HITL·resume을 보존한다. 외부 LangGraph와 내부 역할 create_agent에는 같은 Store를 연결한다. Store namespace는 분리 기준이고 소유권 검사가 아니므로 서비스 정책이 활성 소유자·프로젝트·source Run을 검사한다. [공식 영속성 문서](https://docs.langchain.com/oss/python/langgraph/persistence), [공식 장기 메모리 문서](https://docs.langchain.com/oss/python/langchain/long-term-memory)를 참고했고 설치된 pinned 3.1.2 Store API/SQL schema를 확인했다.

## 실제 변경

| 영역 | 이전 | 현재 |
|---|---|---|
| 저장 backend | project_memories·project_memory_receipts ORM | 공식 Store의 store JSONB 문서와 store_migrations |
| Agent 연결 | AgentContext의 자체 저장 port | compile/create_agent의 store, runtime.store, 요청별 project_memory_policy |
| 미들웨어 | port.read/apply | 공식 Store에 연결한 scoped 정책으로 읽기·갱신 |
| 서비스 코드 | 전용 ProjectMemoryService/BoundProjectMemory | ProjectMemoryPolicy/BoundMemoryPolicy의 소유권·버전·원자 쓰기 정책 |
| 자원 수명 | 기존 SQLAlchemy/asyncpg 풀 | 프로세스별 lazy psycopg Store 풀, Graph/API가 함께 빌리고 drain 후 종료 |
| DB 이행 | head 0024 | 0025가 기존 항목·receipt를 옮긴 뒤 전용 테이블 제거 |
| 관리 API/모드 | GET·PUT·DELETE와 off/manual/auto_context | 외부 계약 그대로 유지 |

제거한 소스는 `api_service/models/common/project_memory_model.py`, `api_service/services/project_memory_service.py`이며 호환 shim이나 이중 쓰기를 남기지 않았다. 공개 Pydantic schema·원문 검증·메모리 미들웨어는 저장 방식과 별개의 유효한 정책이므로 전환해 사용한다. 0024 migration은 이미 게시된 이력이어서 삭제하지 않고 0025에서 이행한다.

`api_service/core/memory_store.py`는 실제 공식 Store의 풀 수명, `service_contracts/memory_store.py`는 namespace·Store 문서 읽기, `api_service/services/project_memory_policy.py`는 애플리케이션 검사를 담당한다. Agent 패키지가 API/ORM을 import하지 않는 경계는 유지한다. 공통 Agent factory와 Conversation/계획 재작성/실행 판단/보고서/repair 선언이 Store를 받을 수 있다.

## namespace·동시성·자원

항목 namespace는 `(dtest, project_memory, 내부 user UUID, project UUID, section)`이고 key는 안정적인 topic key다. receipt는 `(dtest, project_memory_receipts, 내부 user UUID, project UUID)`에 source_id SHA-256 key로 저장한다. 모델의 메모리 조회에 receipt를 포함하지 않는다. 공식 Store에서 반환된 namespace와 value의 section/key도 검사한다.

기본 Store put은 애플리케이션 expected_version을 검사하지 않는다. 따라서 프로젝트 쓰기 barrier와 기존 Worker 점유 token/lease 검사를 정책 계층에 유지한다. 공식 `AsyncPostgresStore(connection)`과 공개 `abatch()`를 사용하여 변경 항목과 receipt를 **동일한 Store 트랜잭션**에 커밋한다. 자체 Store subclass·private batch hook·독자적인 메모리 SQL CRUD는 없다. 별도 CRUD 세션은 권한/lock만 유지하며 Store 저장 완료 전에 반환하지 않는다. 모델 추론 동안에는 어느 DB 연결도 유지하지 않는다.

SQLAlchemy/asyncpg와 공식 Store/psycopg는 driver가 달라 풀을 직접 공유하지 않는다. 새 DB URL은 만들지 않고 기존 DATABASE_URL을 사용한다. 프로세스당 Store 풀은 min=0, max=min(2,DATABASE_POOL_SIZE), timeout=DATABASE_POOL_TIMEOUT_SECONDS다. Pod 연결 예산에 프로세스 수×Store 최대 연결을 추가로 반영해야 한다. Vector/embedding/TTL은 사용하지 않는다. 이 변경의 처리량·지연 A/B는 수행하지 않았으며 성능 향상을 주장하지 않는다.

## 데이터 마이그레이션과 검증

0025는 pinned 공식 schema migration 0..3과 동일한 기본 Store 테이블/열/인덱스·version을 Alembic에서 준비한다. transaction 안의 concurrent index 대신 일반 인덱스를 만든다. 기동 시 store.setup()/자동 DDL은 실행하지 않는다. Alembic autogenerate가 ORM에 없는 공식 Store 테이블을 제거 대상으로 제안하지 않게 제외했다.

기존 메모리의 내용·버전·삭제 표시·출처·갱신 시각과 receipt의 digest·응답을 이행한다. 0025 downgrade는 이 애플리케이션 namespace만 이전 테이블로 복원하고 다른 Store 문서는 유지한다. 0024보다 이전으로 내리면 당시 migration의 메모리 삭제 동작이 적용된다. 혼합 버전의 옛 API/Worker가 옛 테이블에 계속 쓰지 않도록 배포 시 중지/drain→migration→새 버전 기동 순서를 따른다.

수행한 검증:

- 최종 전체 API·Agent 회귀 **937개 + subtest 2개 통과**, 413.43초, warning 99개. warning은 기존 라이브러리 안내이며 실패는 없다. 관련 81개·62개(23.42초)·시각 고정 후 메모리 29개(16.08초)도 통과했으며 숫자를 합쳐 서로 다른 테스트 수로 표시하지 않는다.
- 실제 localhost PostgreSQL: 소유권, 같은 key 경쟁의 한쪽 성공, 다른 key의 동시 보존, 멱등 재전송, 버전·삭제/복원, batch 한도와 원자성, 오래된 Worker claim 거절.
- 실제 공식 Store 첫 Put 이후 예외를 주입하여 항목과 receipt가 모두 rollback되고 같은 요청을 다시 저장할 수 있음을 확인.
- 0025→0024→0025 round-trip에서 활성 항목·삭제 버전·출처·시각·receipt replay를 보존하고 전용 테이블이 사라짐을 확인. 다른 namespace의 문서를 보존.
- API→queue Worker→실제 graph/create_agent→LLM 대역→공식 Store/PG→SSE→다른 세션 참조를 검증. 새 Executor 제출은 없음.
- 모델을 대기시킨 동안 CRUD 연결이 반환되고 다른 API 쓰기가 진행되는 것을 확인. 최종 전체 회귀에서 Store psycopg pool의 사용 중 연결·대기 요청도 0임을 확인했다.
- concurrent Store borrow가 한 번만 초기화되며 active borrower가 있을 때 종료가 기다리고 timeout이면 자원을 보존하는 것을 검증.
- 실제 qwen38-27b-nvfp4+공식 AsyncPostgresStore+전용 로컬 smoke DB: 저장·다음 세션 참조·manual 자동 저장 차단 세 사례 통과. 각각 모델 2/1/1회, 7.929/3.667/4.012초. 새 연결의 Store에서도 메모리를 확인했다. 이 시험은 실제 모델과 실제 DB를 연결했지만 인증 double/직접 Agent 호출이며 queue Worker/Executor/Phoenix 전체 실제 E2E는 아니다. 성능 A/B나 일반 의미 정확성의 보장이 아니다.
- wheel을 source checkout import 없이 확인: 제거한 모델/backend 없음, 새 공식 Store 연결 코드 포함, 12개 역할/prompt, 38개 OpenAPI path, mock 4단계 실행 통과.

초기 전체 회귀에서 Store가 없는 graph 조립 대역과 Store lifespan을 열고 닫지 않은 기존 테스트 fixture가 실패했다. 대역에 표준 Store를 연결하고 실제 API fixture의 Store 시작/종료를 포함하도록 수정했으며 관련 경계를 재검증했다. 표준 InMemoryStore에는 psycopg conn이 없으므로 DB 풀 관측은 conn이 있는 backend에만 적용한다. 이어 실행한 전체 회귀는 936개와 subtest 2개가 통과했으나, 마이그레이션 전후 JSON 시각 문자열 비교 한 건이 실패했다. PostgreSQL의 timestamp JSON이 소수점 끝 0을 생략하는 경우였다. 이행 SQL을 UTC Python isoformat의 정각/6자리 소수 형식으로 맞추고 고정된 .123400/정각 시각으로 검증하도록 변경했다. 보완 후 메모리 관련 29개와 최종 전체 937개 + subtest 2개가 모두 통과했다.

실제 모델/Store 사례의 [공유 가능한 결과 JSON](../reports/langgraph-memory-store-verification-2026-10-02.json)을 보존했다. 인증 정보·사용자 원문·실제 DB URL은 포함하지 않는다.

## 유지한 경계와 후속

원문 추출 범위, manual 기본값, source/version 검사, 64 topic·16000자·항목 1000자 한도와 같은 세션 실행 잠금은 유지했다. Store로 바꿨다고 요약·메모리 품질 정책이나 실행 승인 계약이 자동으로 변경되는 것은 아니다. 생성형 요약·실행 결과 공유 정책·데이터 Registry·Workflow pgvector·운영 정리와 모델 호출 횟수 최적화는 기존 후순위다.

[현재 설정·API·DB 적용 안내](../project-memory.md)를 따른다. localhost 테스트 DB만 변경했고 기존 사용자 checkout/.env·Docker·외부 서비스 DB를 수정하지 않았다. 실제 배포/운영 migration은 수행하지 않았다.

## 통합·게시

최종 검증과 구현 commit 후 기록한다. feature 파생 브랜치는 보존하고 검증한 코드·문서를 feature/refactor-base에 통합하여 origin에 게시한다.
