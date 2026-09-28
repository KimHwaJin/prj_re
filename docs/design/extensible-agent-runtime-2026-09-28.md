# 여러 업무 Agent를 수용하는 API·공통 실행기 구조

작성일: 2026-09-28. 사용자 요구: API 서비스는 공통으로 유지하고 같은 레포에 업무 Agent를 추가하며 API 요청으로 실행할 Agent를 선택한다. Agent 개발 표준을 강제할 수 있다. 아래는 목표 설계이며 코드 이동·구현·마이그레이션을 수행한 결과가 아니다.

**추가 확정:** 공개 run_id는 최초 요청부터 최종 완료까지 유지한다. 공통 Agent 실행 컨텍스트에 Project.system_prompt와 Agent가 관리하는 project_memory를 전달하여 같은 프로젝트의 다른 세션에서도 적용한다. 컨텍스트 버전·동시 갱신·프로젝트 격리와 Workflow 변환/승격 정책은 [최종 결정](crud-final-decisions-2026-09-28.md)을 따른다.

**공개 API 최신 결정:** Agent 실행은 Runs로 집중한다. 별도 Tasks API는 필수 계약이 아니며 필요성이 없으면 기능 통합 후 제거한다. 이 문서의 task는 전체 작업 상태/연결을 설명하는 내부 개념으로 읽는다. 실제 Task 테이블을 유지할지 Runs와 통합할지는 세션 점유·resume·Executor 이벤트·복구 기능의 이전 설계에서 결정한다. Message CUD 공개 라우트도 운영 필수 흐름에서 제외한다.

**결정: 하나의 배포 안에서 공통 API, Agent 개발 계약, 공통 Runtime, 업무 Agent 패키지를 분리한다. Agent별 API 서버·Worker 프로세스·DB 풀을 복제하지 않는다. 공통 Runtime만 실행 소유권·자원·재시도·복구를 관리한다.**

**최신 사용자 정정:** 플랫폼은 일반 객체의 ainvoke() 등을 호출한다. 이는 기존 동작 설명이며 우리 서비스가 `/api/v1/{workflow}/run`이나 src/workflows 서빙 규격을 반드시 따라야 한다는 요구가 아니다. 우리 공통 API·durable 접수·Worker·HITL·상태 계약을 우선 설계한다. 아래 플랫폼 workflows adapter와 목록 재사용은 필요한 경우의 선택적 통합안으로 해석하며 API 대체의 필수 조건으로 삼지 않는다. 기존 Gaia 기동/설정 관련 합의와 특정 서빙 endpoint 준수 의무는 구분한다.

## 현재 코드와 목표의 차이

- `src/app/agents/factory/agent_registry.py`의 AGENT_REGISTRY는 routing/classifier/workflow generator 등 분석 내부 구성요소를 등록한다. 외부 API에서 선택하는 업무 Agent 전체의 실행 계약과는 다르다.
- `RunCreate`에는 업무 Agent 선택 필드가 없다. API 런타임과 이벤트 graph_provider가 특정 분석 graph builder를 직접 사용한다.
- `AnalysisWorkflowState`는 Workflow·Notebook·Executor 상태를 포함한다. 새 Agent에 이 전체 상태를 요구하지 않는다.
- 현재 Task와 Run 처리에는 분석 여부에 따른 분기가 있다. 공통 execution/task 상태와 분석 산출물 저장 정책을 분리한다. 다른 Agent를 추가하려고 runtime에 agent_id별 분기를 계속 넣지 않는다.

## 권장 디렉터리

```text
app.py
config.dev.yml / config.stg.yml / config.prd.yml
logger.yml / pyproject.toml
src/
  common/                         플랫폼 제공
  gaia/                           플랫폼 제공
  lib/                            플랫폼 제공
  middleware/                     플랫폼 미들웨어 연결
  routers/                        플랫폼 get_routers 등록 경계
    __init__.py
    agent_runs.py                 공통 API 라우터 연결
    crud.py                       기존 CRUD 라우터 연결
  workflows/                      플랫폼이 탐색하는 공개 호출 객체
    analysis.py                  공통 접수 adapter → analysis definition
    knowledge_qa.py              공통 접수 adapter → QA definition
  service_bootstrap.py            Gaia 앱·설정·registry·runtime 조립
  api_service/                    공통 HTTP 서비스
    routes/                      CRUD/Run/HITL/상태/SSE
    services/                    인증·소유권 검사·접수·조회
    schemas/                     HTTP 요청·응답
    repositories/                API 전용 CRUD 저장
    tests/
  agent_service/                  Agent 실행 서비스, 별도 서버를 뜻하지 않음
    registry/                    업무 Agent id/version → definition
    runtime/                     supervisor/scheduler/runner/lifecycle
    integrations/                LLM/Executor/Redis/파일 저장 adapter
    agents/
      analysis/
        definition.py            Agent id/version/schema/capabilities
        graph.py                 graph 생성
        state.py                 분석 전용 상태
        schemas.py               입력/출력/HITL 전용 스키마
        settings.py              설정 스키마만, 환경변수 로딩 금지
        nodes/ tools/ prompts/   업무 구현
        tests/
      knowledge_qa/              향후 업무 Agent 예시
        definition.py
        graph.py
        state.py
        schemas.py
        tests/
    tests/contracts/             모든 Agent에 적용하는 공통 계약 시험
  service_contracts/              실행 명령·결과·상태·Agent descriptor·port 계약
  service_infrastructure/         공유 기술 구현; 플랫폼 common/lib와 구별
    configuration/               중앙 설정 해석·검증
    persistence/                 공통 Run/Task/소유권/이벤트 저장
    resources/                   bootstrap이 소유하는 풀/공유 client
```

패키지 이름은 제안이며 실제 Gaia import 경계 확인 후 확정한다. 기존 src/app의 모든 파일을 한 번에 이동하는 것을 선행 조건으로 삼지 않는다. 먼저 서비스 간 계약과 실행 경계를 만들고 내부 구현을 점진적으로 옮긴다. 플랫폼 src/workflows는 Agent 서빙 진입점이고, 분석 Agent가 생성하는 Workflow JSON/PV 산출물과 다른 개념이다.

사용자 추가 제안에 따라 기존 dtest_service 아래 api/agents 배치 대신 api_service와 agent_service를 형제 패키지로 분리하는 것을 권장안으로 갱신했다. 단일 배포·단일 기본 프로세스는 유지한다. 별도 HTTP Agent 서버, HTTP 자기 호출, 별도 pyproject/가상환경/Worker Deployment는 이번 분리의 필수 요소가 아니다.

의존성 규칙:

- api_service는 agent_service의 graph/node/worker 구현을 import하지 않는다. agent_service도 api_service의 라우터·서비스를 호출하지 않는다.
- service_contracts는 양쪽을 import하지 않는 작은 계약 패키지다. 설정값, DB 엔진 생성, 플랫폼 초기화 같은 부작용을 두지 않는다.
- bootstrap만 양쪽 구현을 조립한다. registry에서 공개 가능한 Agent descriptor(입력 스키마/버전/capability/검증 인터페이스)를 API에 주입하고 graph factory와 runtime은 Agent 서비스에 연결한다. API가 Agent를 검증하려고 graph를 초기화하지 않는다.
- 공통 접수 저장소와 실행 상태 저장소는 service_infrastructure가 구현하고 동일 transaction/소유권 규칙을 제공한다. API 측 사용자 명령 접수와 Agent 측 시스템 명령 접수가 같은 규칙을 사용한다. 접수/소유권 업무 규칙을 양쪽에 복제하지 않고 계약으로 노출하는 공통 실행 조정 컴포넌트에 둔다. 해당 컴포넌트가 커지면 독립 모듈로 분리하되 일반 utils 모음으로 만들지 않는다.
- 개발자가 새 업무 Agent를 추가하는 곳은 agent_service/agents/<id>다. 공통 Worker는 agent_service/runtime 아래 하나이며 Agent별 Worker를 추가하지 않는다.
- 패키지를 분리해도 같은 프로세스/설치 환경/DB를 쓰므로 장애·의존성·자원 격리가 자동으로 생기지는 않는다. 공유 자원의 생성·종료 책임은 bootstrap/runtime lifecycle의 단일 소유자에 둔다.

## API 계약과 실행 대상 고정

공개 API는 우리 서비스의 session Run 접수·resume·조회·SSE 계약을 기본으로 설계하고 agent 선택을 추가한다. 플랫폼 경로 사용은 선택 사항이다. 플랫폼 `/api/v1/{workflow}/run`도 연결한다면 workflow를 내부 agent_id로 매핑하여 동일 접수 서비스에 전달한다. 두 경로를 제공하더라도 실행·잠금·멱등 정책을 복제하지 않는다. 플랫폼의 최종 답변 포맷에 맞추기 위해 우리 API의 durable 비동기 접수 의미를 바꾸지 않는다.

### 플랫폼 호출 객체의 역할

```text
플랫폼 run API → workflows/analysis.py의 공개 객체
  → 인증된 호출 context + 입력을 공통 명령으로 변환
  → 공통 접수 서비스 → durable queue
  → agent_service/runtime Worker
  → analysis definition의 실제 graph factory → 업무 graph 실행
```

공개 호출 객체는 Agent별로 공통 adapter를 구성한 얇은 객체다. 실제 구현할 메서드(`invoke`, `ainvoke`, stream 등), export 변수명, 클래스 타입은 플랫폼 로더를 보고 결정한다. 객체가 import될 때 DB pool·LLM·Worker를 만들지 않고, bootstrap이 서비스 의존성을 연결한다. Worker는 공개 접수 adapter를 다시 호출하지 않는다. 실제 graph factory를 호출하여 재접수 루프를 방지한다.

단순히 플랫폼이 객체를 호출한다는 사실은 durable queue/lease/재시도/복구를 제공한다는 의미가 아니다. 플랫폼의 실제 실행 제어가 같은 요구를 충족하면 중복 구현하지 않으며, 단순 graph 호출이면 공통 접수 adapter를 통해 기존 목표를 유지한다. 래퍼를 지원하지 않고 실제 CompiledGraph 타입만 요구하는 경우에는 공식 호출 hook/사용자 라우터 등 지원 경계로 연결하는 대안을 검토한다. 현재 설명만으로 객체 교체가 무조건 가능하다고 확정하지 않는다.

### HTTP·stream 의미 보존

`async def ainvoke()`는 호출자가 await할 수 있다는 메서드 계약이다. 이것만으로 durable background 실행이나 HTTP 202를 제공하지 않는다. 선택적 공개 adapter의 ainvoke는 await submit(command)로 DB 접수 commit을 확인한 뒤 receipt를 반환하도록 만들 수 있다. 실제 graph.ainvoke는 Worker가 실행한다. 플랫폼이 반환값을 최종 답변으로 강제 변환한다면 해당 경로의 응답 adapter가 필요하며, 이를 위해 우리 기본 API를 바꿀 의무는 없다. 요청 안에서 create_task만 생성하고 접수 성공을 반환하는 방식은 durable queue를 대체하지 않는다.

- 플랫폼이 즉시 접수 응답을 지원하면 run 식별자·상태를 해당 계약으로 반환한다. HTTP 202를 adapter 반환값만으로 지정할 수 있다고 가정하지 않는다.
- 플랫폼이 최종 답변을 기다려 포맷팅하면 큐 접수 후 Run 상태/이벤트를 제한된 시간 동안 기다리는 응답 adapter가 필요할 수 있다. 사용자 HITL 또는 외부 실행 대기 상태를 표현할 수 있는지 확인한다. Executor 전체 완료까지 HTTP를 일주일 붙잡는 계약은 채택하지 않는다.
- stream이 graph chunk만 받는다면 durable 실행 이벤트 → 플랫폼 chunk 변환이 필요하다. 응답을 만들기 위해 그래프를 한 번 더 직접 실행하지 않는다.
- 사용자 인증 context, session 식별, idempotency key, resume 대상·interrupt 응답이 공개 객체까지 전달되는지 확인한다. 누락된다면 입력/호출 확장 작업이 필요하다. 인증되지 않은 body user_id만으로 소유권을 인정하지 않는다.
- disconnect 취소와 사용자 취소를 구분하고 플랫폼의 자동 취소 동작을 확인한다. 연결 종료가 이미 접수한 장기 업무를 삭제/중복 접수시키지 않게 한다.

### 목록/등록의 단일 출처

서비스 registry는 실행에 필요한 id/호환 버전/factory/capability lookup의 기준이다. 공개 목록은 우리 API에서 제공할 수 있으며 플랫폼 workflow GET을 반드시 쓸 필요는 없다. 플랫폼 목록까지 노출할 때는 같은 Agent definition을 참조하고 startup에서 이름 중복·미등록·활성화 불일치를 검사한다. 여러 호환 버전을 유지하더라도 현재 선택할 공개 workflow와 기존 task 재개용 구현을 구분한다.

현재 레포의 `src/routers/chat/router.py`에는 `/workflows` 목록과 `/{workflow}/api/completion` 호출 초안이 있지만 제공된 run API와 동일 경로가 아니고 참조된 workflow manager/response formatter 원본은 확인되지 않았다. 이를 폐쇄망 템플릿 동작의 검증 결과로 취급하지 않는다.

- 최초 호출: registry의 등록·활성화·사용 권한 확인 → Agent별 입력 스키마 검증 → 서버가 구현/호환 버전 선택 → task/run에 대상 저장 → 공통 큐 접수.
- 공통 envelope는 session/task/run/agent 식별과 요청 종류를 담당한다. Agent별 업무 입력은 등록된 스키마로 검증한다. 현재 messages와 신규 구조화 입력의 외부 API 이행은 별도 계약으로 명시한다.
- 사용자 resume: 원래 task/interrupt에서 agent_id와 버전을 읽는다. 다른 agent_id로 변경하는 요청은 거절한다. 일반 사용자가 executor_event 종류를 지정해 내부 resume를 실행할 수 없게 접수 경로를 구분한다.
- Executor 이벤트: execution_id의 서버 저장 binding → task/agent/version/checkpoint 매핑 → 공통 시스템 명령 접수. Redis 이벤트의 임의 agent_id나 현재 최신 버전으로 목적지를 정하지 않는다.
- 배포마다 다른 image tag를 Agent 호환 버전으로 사용하지 않는다. 진행 중인 task에 저장된 호환 버전을 처리할 definition을 유지한다. 지원하지 않는 버전은 다른 Agent/버전으로 fallback하지 않는다.
- 같은 idempotency key의 agent_id/업무 입력 변경도 요청 충돌로 판단한다. 요청 hash는 정규화한 실행 계약을 대상으로 한다.

현재 AGENT_REGISTRY 이름과 혼동하지 않도록 업무 Agent registry는 별도 책임/이름으로 둔다. 등록은 코드 allowlist로 명시하고 사용자 입력으로 import 경로를 실행하거나 외부 플러그인을 다운로드하지 않는다. import 시 모델/풀/Worker를 생성하지 않는다.

## Agent 개발 표준

초기 버전은 기존 LangGraph 실행 모델을 공통 adapter로 지원한다. Agent 추가 때문에 Deep Agents, 임의 프레임워크 플러그인, 별도 Agent 서버를 도입하지 않는다. 다른 엔진은 실제 필요가 생겼을 때 같은 실행 계약을 구현하는 adapter로 검토한다.

각 Agent가 제공할 항목:

1. 안정적인 agent_id, 구현/호환 버전, 입력·결과·사용자 응답 스키마, 지원 capability(HITL/Executor 등).
2. 주입된 자원과 checkpointer로 graph를 만드는 factory. 공통 runtime은 Agent definition과 adapter를 호출하며 업무별 node 이름이나 state 필드에 의존하지 않는다.
3. 입력을 Agent state로 바꾸는 변환과, state/interrupt를 공통 결과·메시지·대기 표현으로 바꾸는 projection. checkpoint 성공 후 API DB 반영 실패도 재시도 가능한 안정적인 effect id를 사용한다.
4. 공통 사용자 대기/외부 실행 대기 계약. 업무 질문·form·답변 구조는 Agent별로 정의할 수 있고 공통 envelope에 schema/kind/interrupt 식별자를 넣는다. 프론트에 표현할 수 없는 새 UI 형식은 별도 개발 범위임을 명시한다.
5. 설정 스키마·기본값·필요 자원 profile 선언. 실제 값은 config > env > default 중앙 resolver에서 주입한다. Agent가 전역 실행/LLM/DB 상한을 임의로 높일 수 없다.
6. 공통 계약 시험과 Agent 업무 테스트/fixtures. HITL/Executor 기능은 사용하는 Agent에 한해 해당 시험을 수행한다.

Agent가 직접 소유하지 않을 것: FastAPI app/일반 Run endpoint, Worker loop, DB pool 생성, 실행 lease/세션 잠금 변경, 환경변수 재로딩, API의 tasks/runs 상태 직접 갱신, 기록되지 않은 외부 변경 작업의 임의 재시도. 읽기 전용 도구나 업무 저장소도 주입된 서비스와 제한된 timeout/자원 계약을 사용한다.

업무 state는 Agent별로 자유롭게 구성하되 런타임 컨텍스트/연결 객체는 직렬화되는 checkpoint state에 넣지 않는다. 공유 graph wrapper에 사용자별 가변 데이터를 저장하지 않는다. 실행 중 background task를 남기지 않고 취소/종료 계약을 따른다. CPU 무거운 작업은 공통 event loop를 점유하지 않도록 승인된 별도 실행 경로/Executor를 사용한다.

## Worker와 자원 배분

```text
사용자 start / 사용자 HITL resume / 검증된 외부 이벤트
                         ↓
             공통 접수·권한·상태 검사
                         ↓
        PostgreSQL 실행 명령 큐(agent/version/task 포함)
                         ↓
          공통 scheduler + 제한된 실행 자리 K개
                         ↓
          registry → 해당 Agent graph 실행/재개
                         ↓
       결과 저장 / WAITING_USER / WAITING_EXECUTOR / 종료
```

- Pod의 실행 자리는 어떤 등록 Agent도 처리할 수 있다. analysis 4개 + QA 4개로 고정 Worker 그룹을 만들어 항상 자원을 복제하지 않는다.
- Pod 전체 활성 실행 상한과 Agent별 동시 실행 상한을 별개로 둔다. 예를 들어 전체 8, analysis 최대 6, QA 최대 4라면 합계 활성 실행은 항상 8 이하이고 10자리가 예약되는 것이 아니다. 숫자는 설명용이며 운영 확정값이 아니다.
- Agent별 상한만으로 공정성이 보장되지는 않는다. scheduler가 실행 가능한 Agent 간 공정하게 선택하고, 상한에 걸린 Agent의 앞선 작업 때문에 다른 Agent가 막히지 않도록 한다. 재개/이벤트 처리도 제한된 우선권과 기아 방지 기준을 둔다. 이미 시작한 LLM 호출을 다른 Agent 요청 때문에 임의로 선점하지 않는다.
- 전체/Agent별 상한은 기본적으로 프로세스(Pod당 기본 1프로세스) 범위다. 여러 Pod에 걸친 모델 provider/token/DB 예산은 별도 전역 한도 문제이며 이 설정만으로 해결했다고 하지 않는다.
- HITL/외부 Executor 대기는 실행 자리를 반환하지만 세션 입력 정책은 그대로 유지한다. 대기중 task 수와 실행중 Run 수를 다른 지표로 집계한다.
- 공통 PostgreSQL 풀과 HTTP 자원을 사용하되 동시성 안전한 graph/Saver wrapper는 실행 자리·Agent 버전별로 관리한다. lazy 생성과 bounded cache로 Agent 종류 × 실행 자리 × pool이 무제한 늘지 않게 한다. 공유 풀과 공유 가변 graph 인스턴스는 같은 의미가 아니다.
- 기존 Redis 이벤트 consumer는 공통 연계 계층으로 유지한다. Agent마다 ingress/dispatch consumer group을 복제하지 않는다. 이벤트를 저장·검증·라우팅하고 공통 Run 경로로 인계한다.
- 한 Agent의 업무 예외는 그 Run의 실패로 격리한다. 동일 프로세스의 CPU 고갈/OOM/비협조적인 코드까지 완전히 격리한다고 약속하지 않는다. 추가 Agent의 의존성도 동일 환경에 설치되므로 버전 충돌 검증이 필요하다.

## 세션 정책과 checkpoint 격리

기존 사용자 전제를 그대로 적용한다: 같은 세션에서 활성 실행 또는 WAITING_EXECUTOR가 있으면 다른 Agent를 지정해도 새 일반 입력은 거절한다. WAITING_USER에서는 해당 task가 요구한 응답만 받는다. 같은 사용자의 다른 idle 세션은 실행 가능하다. 업무가 종료된 idle 세션에서 다른 Agent를 선택하는 것은 허용하는 방향으로 제안한다.

**서비스 session_id와 Agent checkpoint 식별을 구분해야 한다.** 서로 다른 Agent state를 같은 session_id의 같은 checkpoint 공간에 그대로 쓰지 않는다. 새 task/Agent conversation에 안정적인 checkpoint 참조를 발급하여 agent_id/호환 버전/session/task와 함께 저장하고 모든 resume가 그것을 따른다. 대화 문맥 공유는 공통 메시지 저장소에서 명시적으로 가져오며 다른 Agent의 graph state를 그대로 읽지 않는다.

이 설계는 현재의 `thread_id=session_id` 전제에 영향을 준다. 기존 checkpoint 키를 일괄 변경하지 않고 기존 analysis 실행은 legacy 매핑으로 유지한다. `session_id_from(config)`, binding 검증, 최초 호출/resume/event 경로를 함께 수정·시험한 뒤 신규 매핑을 사용한다. task마다 checkpoint를 나눌 경우 이전 task와의 대화 연속성은 메시지/명시적 memory 계약으로 제공한다. agent_id 문자열 하나를 붙이는 변경만으로 호환이 확보됐다고 판단하지 않는다.

## 설정과 추가 개발 절차

선택된 환경 config 안에서 공통 runtime 및 Agent별 설정 영역을 둔다. runtime의 전체 실행 한도/DB/Redis/checkpoint 연결은 공통 소유, agents.<agent_id>에는 enabled, model profile, 전용 업무 옵션, 허용된 실행 상한을 둔다. 상위 공통 한도를 Agent 설정이 덮어쓸 수 없다. 각 영역도 config > env > default 규칙을 따른다.

새 업무 Agent 추가의 정상 변경 범위:

1. agents/<id> 패키지 구현 및 definition/schema 등록.
2. registry에 명시적으로 추가하고 환경 설정에 활성화/자원 profile 반영.
3. 공통 계약 시험과 업무 fixture 검증, 동일 패키지 환경에서 의존성 검증.
4. 기존 배포로 함께 배포. 프론트가 표준 envelope를 지원하면 agent_id 선택으로 호출.

공통 API/Worker 핵심에 업무별 분기를 추가해야 한다면 공통 계약이 부족한지 검토한다. 새 기능이 표준에 없거나 별도 사용자 UI가 필요할 때는 API/프론트 변경이 필요할 수 있으므로 모든 Agent가 항상 무수정 추가된다고 보장하지 않는다.

## 초기 구현 검증

기존 analysis와 작은 테스트 전용 두 번째 Agent로 확장 구조를 검증한다. 후자는 실제 신규 제품 기능 개발이 아니라 계약 fixture다. 서로 다른 state 스키마, 같은/다른 세션, Agent별 상한, resume 대상 변조, 이벤트 목적지, 버전 미지원, checkpoint 격리 및 설정 주입을 시험한다. Agent 하나 추가 시 공통 Worker/API 실행 코드를 수정하지 않고 정상 흐름과 장애 복구가 동작해야 한다.

이 요구사항은 새 공통 실행기를 특정 analysis graph에 결합하지 않도록 초기 설계에 반영한다. 구현 순서는 Gaia 통합/설정 → Agent 계약/registry와 기존 analysis adapter → 공통 소유권/실행기/복구 → 두 Agent 계약 시험 → 동시성/부하 검증이다.

참고: [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [graph migrations](https://docs.langchain.com/oss/python/langgraph/graph-api#graph-migrations). thread별 state 보존과 중단된 graph의 버전 호환 제약을 기반으로 하며 레포 분리·registry·자원 정책은 이 프로젝트를 위한 설계 제안이다.
