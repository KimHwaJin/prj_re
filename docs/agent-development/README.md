# Agent 개발 안내

054 기준으로 현재 API·Worker·로컬 개발 도구는 `analysis/planning/graph.py`의 같은 builder를 사용한다. 실제 역할은 conversation, plan_revision, execution_review, execution_report, execution_repair 다섯 개다. 이전 routing/intent_classifier/skill_selector/workflow_generator/conditional_decider/faq/report_writer와 설문형 그래프는 제거했다. [파일별 책임](../../src/agent_service/agents/analysis/README.md), [서비스 경계](../architecture/service-layout.md), [선언·문맥·미들웨어](agent-runtime-contract.md)를 먼저 읽는다.

## 수정 위치

1. 새 요청·HITL·계획 재작성은 `planning/graph.py`, 실행·관찰·판단·보고서는 `execution/nodes.py`, 오류 수정 연결은 `execution/repair_nodes.py`다. 외부 State는 `planning/graph.py`의 PlanningState다.
2. 역할 선언은 `agent_builders/<role>/agent.py`, 독립 기본 지시문은 같은 폴더 `prompt.md`다. Conversation의 상세 계획 지시문은 `planning_prompt.md`다.
3. 모델 선택·캐시·문맥 주입은 `planning/runtime.py`, 전송용 모델 생성은 공통 `agent_service/runtime/model_factory.py`다. 노드에서 전역 기본 모델을 다시 읽지 않는다.
4. 업무 자산은 계속 **`analysis/workflow/{skills,tools,workflows}/`**에서 관리한다. 새 Runtime이 Agent에 제공하는 조회 도구는 `planning/catalog.py`의 AssetCatalog다. `analysis/tools/catalog.py`와 `workflow/*.py`는 기존 1.3 Workflow 관리·컴파일 지원이다.
5. 공개 API와 Workflow/Executor 계약은 `service_contracts`, HTTP/PV 어댑터는 `integrations/executor`, CRUD·Worker 점유·DB 조립은 `api_service`다. Agent에서 API 구현을 import하지 않는다.

## 역할을 추가·변경하는 방법

`agent_builders/<role>/`에 `agent.py`, `prompt.md`, `__init__.py`를 둔다. 내용이 같아도 다른 역할의 prompt를 공유하지 않는다. 공용 `build_role_agent`로 모델·tools·middleware·출력 schema·검증을 명시한다. 모델/풀 생성, 설정 재로딩, 직접 DB 접근을 builder에 넣지 않는다.

같은 업무 흐름에 새 역할이 필요하면 `PlanningRuntime`에서 해당 builder 선택·모델 pin·Store 주입·요청별 context를 연결하고 실제 노드에서 호출한다. 폴더만 추가한다고 실행되지는 않는다. 이는 다중 업무 Agent registry 추가와 별개다. 새 역할의 메모리 분류는 `runtime/memory_selection.py`에서 검토하고 wheel에 prompt가 포함되는지 검사한다.

현재 역할의 tools는 구분된다. Conversation에는 Skill/Tool 메타데이터 조회가 있으며, PlanRevision은 실행별 수정을 위한 함수 원문 조회도 허용한다. Repair는 허용 수준에 따라 탐색을 켠다. Review/Report는 제공된 관찰을 해석한다. 이 LangChain 도구들은 분석 함수를 실행하는 Tool이 아니다. 분석 함수는 사용자 승인 snapshot에서 Executor로 제출한다.

## 실행·확인

Python 3.11과 잠금 의존성을 사용한다. 실제 서비스는 `python app.py` 또는 `dtest-agent-api`로 실행하며 요청·resume는 [Runs API](../public-run-api.md)를 따른다. SSO·쿠키·CSRF는 [인증 가이드](../sso-authentication.md)를 따른다.

```sh
# 배포 설정/.env/외부 서비스 없이 현재 계획→HITL을 확인
python cli.py --request "데이터의 품질과 이상치를 분석해줘"
python cli.py --interactive

# 현재 계획·MULTI·판단·수정 분기 전체를 .mmd로 출력 (외부 호출 없음)
PYTHONPATH=src python -m devtools.analysis.visualization --output /tmp/analysis-current.mmd

# 현재 builder의 offline mock Studio 진입점
langgraph dev

# 관련 회귀: 실제 DB 테스트는 별도의 전용 테스트 DB 설정 필요
PYTHONPATH=src python -m pytest src/agent_service/agents/analysis/tests -q
```

CLI는 typed HITL action JSON을 받으며 현재 plan_id/plan_revision을 출력한다. `approve_plan` 등을 action object로 입력한다. 기본 mock에서는 최종 `plan_approved`까지이고 Executor·DB·Redis·로그인·project_memory 연계는 없다. Studio가 saver를 제공하며 `langgraph_dev.py`는 서비스 Worker/SSO/Redis 바인딩을 함께 띄우지 않는다. 운영 기동이나 장기 실행 검증에 이 도구를 사용하지 않는다.

시각화에서는 LangGraph 분기 목적지를 명시한다. 실행 노드·선택 로직은 동일하지만 과거 다이어그램에서 빠졌던 조건부 연결이 표시된다. [생성한 현재 그래프](current-analysis-graph.mmd)는 코드 생성 결과이며 의미 변경 시 다시 생성한다.

## Skill·Tool 유지보수

```sh
python src/agent_service/agents/analysis/workflow/skills/generate_skill_index.py
python src/agent_service/agents/analysis/workflow/tools/generate_tool_registry.py
```

생성기는 `--output`으로 임시 파일에 비교할 수 있고 기동 시 자산을 다시 쓰지 않는다. tmp 자산·원래 docstring·import 포함 함수와 Skill Markdown을 보존한다. `availability=test_only` Tool은 현재 실제 계획 후보에서 제외된다. [Workflow 유지보수 안내](../../src/agent_service/agents/analysis/workflow/README.md)를 따른다.

등록 함수는 docstring만 제거하고 필요한 import를 포함한 원문을 승인 snapshot에 고정한다. 사용자에게 보여주는 plan_view에는 함수 이름·설명·입력·Skill을 표시하고 코드는 제외한다. 이미 승인된 실행을 현재 배포 Tool로 재생성하지 않는다.

## 문맥·근거·메모리

ProjectPromptMiddleware는 프로젝트 system_prompt snapshot을 매 모델 요청에 넣는다. JSON retry에서도 중복하지 않고 프로젝트 간 요청이 섞이지 않는다. 내부 create_agent는 checkpointer=False, 외부 업무 graph가 HITL/이력/Executor 대기를 저장한다. async 호출·취소 전파와 보호된 run_sync 수명은 유지한다.

Conversation의 첫 판단은 짧은 prompt로 수행하고, 선택한 Skill 조회 뒤 상세 planning_prompt를 사용한다. SessionAnalysisMiddleware는 같은 세션의 완료 근거를 전달한다. `execution/grounding.py`는 실제 근거 ID·값을 확인하고 서버가 수치 표를 렌더링한다. [후속 문맥](../agentic-session-analysis-context.md), [근거 계약](../agentic-answer-grounding.md)을 따른다.

ProjectMemoryMiddleware는 공식 Store의 프로젝트 배경·선호를 별도 참조 메시지로 제공한다. 소유권·버전·출처를 정책으로 검사하고 역할별 입력 예산을 적용한다. Conversation만 선택적 auto_context에서 현재 발언에 근거한 갱신을 제안한다. [현재 메모리 설정·정책](../project-memory.md)을 따른다. Store 전체 자동 요약·Executor 결과 자동 공유는 아직 구현하지 않았다.

## 계약과 보류

[Workflow JSON](../workflow-json-reference.md)의 실행 규격은 2.0-draft이며 기존 관리 CRUD는 1.3이다. [전처리 데이터 Registry](../design/dataset-registry-contract/README.md)는 Executor API 대기다. Gaia 등록 adapter, 첨부/VLM, 다중 업무 Agent registry는 후속이다. 보고서 모델 호출 횟수 최적화·운영 에러 대응은 [후속 목록](../improvements/backlog.md)의 우선순위를 유지한다.

과거 성능 비교 도구는 고정 과거 commit의 설문형 그래프를 재현하는 도구다. 현재 Agent 측정으로 해석하지 않는다. [벤치마크 구분](../../scripts/benchmarks/README.md)을 따른다. 과거 노드/CLI import를 현재 경로로 유지하는 shim은 없다. 054 이전 설문형 checkpoint 자동 이행은 이번 범위에 포함되지 않으며 현재 `agentic-planning-v1` HITL 재개는 별도로 검증한다.
