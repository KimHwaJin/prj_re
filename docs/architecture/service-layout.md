# 서비스 구조와 이행 경계

2026-09-28 확정 구조. 패키지 분리는 단일 Deployment·단일 컨테이너 Pod 안의 소스 책임 분리이며 별도 HTTP Agent 서버를 추가하는 계획이 아니다.

006 업데이트: 분석 구성요소/LLM 소비 노드는 비동기 호출로 전환했고 `runtime/blocking.py`에 혼합 노드용 임시 동기 작업 종료 경계를 추가했다. 아래 005 이행 경계의 전체 패키지 분리와 HTTP·DB·ArtifactStore 전환은 아직 남아 있다. [검증 기록](../improvements/006-agent-async-llm.md)을 참고한다.

008 업데이트: 역할별 Agent 선언·프롬프트는 `agents/analysis/agent_builders/<role>/`로 모았다. 공통 도구와 역할 프롬프트는 별개 기준으로 관리한다. create_agent·공통 미들웨어를 표준으로 삼되 현재 모든 구성요소가 전환된 것은 아니다. [008 기록](../improvements/008-agent-builders-layout.md)을 참고한다.

010 업데이트: 기존 skills·tools·workflows 패키지 구성을 **src/agent_service/agents/analysis/workflow/** 아래로 통합한다. 009의 원래 경로 고정 해석을 정정했으며, 기존 Workflow 처리 모듈도 이 패키지에 유지한다. [010 기록](../improvements/010-unify-analysis-workflow.md).

## 목표

```text
app.py / config*.yml / logger.yml / pyproject.toml
src/
  common/ gaia/ lib/ middleware/    플랫폼 제공·연결 영역
  routers/                         get_routers 등록 경계
  workflows/                       플랫폼 공개 호출 연계가 필요할 때만
  service_bootstrap.py              설정·풀·구현·수명 조립
  api_service/                     routes / schemas / dependencies / services
  execution_service/               commands / lifecycle / ownership / idempotency / recovery
  agent_service/
    registry.py                    업무 Agent id·호환 버전 등록
    runtime/                       worker / scheduler / runner / supervision / langgraph
    factory.py / models.py         create_agent 공통 조립·모델 선택 (후속)
    middleware/ / memory/          공통 정책·프로젝트 메모리 (후속)
    tools/                         업무 간 실제 공유 도구가 생길 때 추가
    integrations/                  llm / executor (HTTP·Redis 이벤트)
    agents/<id>/                   definition / graph / state / schemas / settings
                                   dependencies / projection / nodes / subgraphs
                                   agent_builders/<role>/agent.py + prompt.md
                                   workflow / tools / tests
                                   workflow/{skills,tools,workflows} (분석 자산과 생성기)
  service_contracts/               Agent·실행·context·저장/연계 port
  service_infrastructure/           configuration / persistence / resources
                                   artifacts / observability
  devtools/                        개발용 CLI·시각화
tests/                             서비스·통합·공통 Agent 계약 검증
scripts/                           운영·진단·부하테스트
docs/                              구조·개발 안내·개선 결과
```

플랫폼 디렉터리는 실제 제공 템플릿을 연결할 때 유지한다. 현재 없는 Gaia 원본을 임의로 작성하지 않는다. `workflows/`의 플랫폼 객체와 분석 Agent가 생성하는 Workflow JSON은 다른 개념이다. 아직 구현이 없는 영역은 빈 패키지/가짜 인터페이스로 만들어 완료 표시하지 않는다.

## 의존성과 책임

- API는 HTTP·사용자 식별·접근 권한·관리 기능을 담당한다. Agent의 graph/node를 직접 import해 실행하지 않는 것이 목표다.
- execution_service는 start/resume/cancel/외부 이벤트 접수와 세션 점유·상태 전이·멱등성·복구 규칙을 공유한다. SQL·HTTP 어댑터가 업무 규칙을 각자 복제하지 않는다.
- runtime은 공통 queue에서 실행 자리를 확보하고 등록 Agent를 실행한다. 새 Agent마다 Worker와 풀을 추가하지 않는다.
- 업무 Agent는 상태·업무 판단·프롬프트·Workflow 해석을 제공한다. LangGraph 세부 상태를 API의 공통 상태라고 가정하지 않는다.
- contracts는 구현을 import하지 않는다. infrastructure는 주입되는 저장·자원 port를 구현하고 bootstrap이 실제 구현을 조립한다.
- Executor integration은 외부 통신/이벤트를 담당한다. 어떤 실행을 재개할 수 있는지는 execution_service가 결정한다. Redis 이벤트 소비를 제거하거나 PostgreSQL만으로 대체하는 계획이 아니다.
- 단일 세션 실행/WAITING_EXECUTOR 잠금과 다른 세션의 독립 실행 원칙은 소스 이동과 무관하게 유지한다.

## 005에서 실제 구현한 부분

005 당시 분석 업무 코드와 리소스를 `agent_service/agents/analysis/`로 집약했다. 009에서 기존 Workflow 하위 패키지를 복원했고, 010에서 자산·색인·생성기·정책 문서를 `src/agent_service/agents/analysis/workflow/`로 통합했다. 체크포인트 풀 factory는 `agent_service/runtime/langgraph/checkpointer.py`, 개발용 직접 실행·시각화는 `devtools/analysis/`, 카탈로그 생성기는 분석의 `workflow/skills/` 및 `workflow/tools/`다. 기존 API/Worker가 새 import를 사용하며 외부 HTTP 경로와 DB migration은 바꾸지 않았다.

**목표 전체의 이행 완료는 아니다.** 다음 경계는 의도적으로 기존 동작을 유지했다.

| 현재 위치/참조 | 후속 목표 |
|---|---|
| app/api, app/services의 CRUD·Run 구현 | api_service 및 execution_service로 책임별 분리 |
| app/agent_run_worker.py, app/core 실행 보호 | 공통 runtime 및 execution_service |
| app/agent_worker, app/worker | 이벤트 integration·공통 접수·실행 adapter로 기능별 분리 |
| app/services/executor_client.py | 비동기 Executor integration/port |
| app/services/workflow_persistence.py | 업무 snapshot 변환과 비동기 저장 port/구현 분리 |
| app/agent_worker/graph_boundary.py | 공통 외부 대기 계약과 LangGraph adapter 분리 |
| 분석의 artifacts.py | ArtifactStore를 주입받는 업무 경로 정책 |
| src/agent_config.py | 중앙 설정 호환 유지 후 공통/분석 설정 스키마 분리 |
| app의 일부 API/서비스가 분석 schema import | 공통 Workflow/Executor 계약과 업무 스키마 분리 |

이 목록을 허용된 임시 의존성으로 보고, 새로운 Agent가 그대로 복제할 표준으로 삼지 않는다. 008에서 역할 선언·프롬프트 구조를 먼저 정리했다. 다음 설계는 create_agent·미들웨어·State/Context/Store 연결이며 I/O 비동기 전환·취소 종료 추적과 공통 registry/접수/실행 경로 통합·슬롯 동시성도 남아 있다.

## 미사용 코드 판정

import 검색뿐 아니라 루트/컨테이너/CLI/LangGraph 진입점, 카탈로그 및 파일 소스 로딩을 확인한다. 이번 삭제는 worker_past와 호출처 없는 팩토리/옛 패키지 재노출 코드에 한정했다. 등록된 Executor Tool은 import되지 않아도 Notebook 소스로 사용되므로 유지했다.

`src/agent_service/agents/analysis/workflow/{skills,tools}/**/tmp/`의 미등록 Skill/Tool은 활성 호출 경로가 아니다. 미사용 recommender prompt는 008에서 docs/agent-development/reference-prompts로 옮겨 설치 리소스에서 제외했다. 일부 실패 테스트가 미등록 Skill을 기대하므로 업무 지원 범위를 결정하기 전에 임의로 삭제하거나 카탈로그에 승격하지 않았다. 현재 WorkflowRecommender는 실제 벡터 검색 구현이 아닌 빈 결과 placeholder다. 이 항목들의 정리 및 기존 실패 테스트 정상화는 Agent 업무 정합성 작업에 남긴다.
