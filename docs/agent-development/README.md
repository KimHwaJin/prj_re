# Agent 개발 안내

현재 구현 기준: `feature/refactor-agent-flow-validation`, 개선 기록 012. 7개 실제 LLM 역할을 create_agent로 통일하고 공통 실행 문맥·프로젝트 system_prompt·JSON 검증 미들웨어를 적용했다. project_memory 자동 요약/저장, 모델 선택 registry, 업무 registry 및 HTTP·DB·파일 I/O 전체 전환은 후속이다. [Agent 선언·문맥·미들웨어 가이드](agent-runtime-contract.md)를 먼저 읽는다.

- [현재 분석 Agent의 파일별 역할](../../src/agent_service/agents/analysis/README.md)
- [전체 목표 구조와 이번 단계의 경계](../architecture/service-layout.md)
- [005 당시 이동·삭제 목록](analysis-layout-inventory.json)
- [012 업무 흐름 회귀·I/O 취소 수명](../improvements/012-agent-flow-validation.md)
- [011 Agent 실행·미들웨어 통일](../improvements/011-agent-middleware.md)
- [010 분석 Workflow 패키지 통합](../improvements/010-unify-analysis-workflow.md)
- [009 기존 Workflow 작업 위치 복원](../improvements/009-preserve-workflow-package.md)
- [008 역할별 선언·프롬프트 구조](../improvements/008-agent-builders-layout.md)
- [변경·검증·남은 작업](../improvements/005-agent-package-layout.md)
- [LLM 비동기 전환·취소 검증](../improvements/006-agent-async-llm.md)

## 지금 분석 Agent를 수정하는 방법

1. `src/agent_service/agents/analysis/graph.py`에서 실행 흐름을 확인한다. 업무 상태는 `state.py`, 노드는 `nodes/`다.
2. 역할별 Agent는 `agent_builders/<role>/agent.py`의 `build_agent()`에서 수정한다. 기본 프롬프트는 같은 폴더의 `prompt.md`이며 내용이 같아도 다른 역할의 파일을 참조하거나 합치지 않는다. `dependencies.py`는 공유 모델과 각 builder의 결과를 연결한다. 기존 `components/specs.py`는 제거했다. 이 builder 패키지는 API에서 선택할 업무 Agent registry가 아니다.
3. **src/agent_service/agents/analysis/workflow/**에서 Workflow 관련 코드를 관리한다. 기존 skills/·tools/·workflows/ 하위 구조와 생성기를 유지하고, 같은 패키지의 Python 모듈에서 해석·컴파일·코드 생성을 담당한다. Agent가 호출하는 LangChain 카탈로그 도구는 analysis/tools/catalog.py다.
4. `tests/`에서 업무 회귀를 실행한다. 서비스 상태·소유권·DB 테스트는 아직 `src/app/test/`에 있다.

Python 3.11과 잠금파일 의존성을 사용한다. 설치 후 CLI는 `dtest-agent`, 소스 체크아웃에서는 `python cli.py --help`다. API는 기존대로 루트 `python app.py`로 실행한다. CLI는 개발용 그래프 직접 실행 도구이며 서비스의 durable queue/세션 소유권을 검증하는 도구가 아니다.

```sh
# 외부 LLM 없이 승인·제출 경계를 확인하는 검증
PYTHONPATH=src python -m pytest \
  src/agent_service/agents/analysis/tests/test_service_load_mock.py \
  src/agent_service/agents/analysis/tests/test_resource_layout.py -q

# 카탈로그 생성은 명시적으로 실행한다. 서버가 시작할 때 다시 쓰지 않는다.
python src/agent_service/agents/analysis/workflow/skills/generate_skill_index.py
python src/agent_service/agents/analysis/workflow/tools/generate_tool_registry.py
```

생성기 기본 출력은 통합 패키지의 `src/agent_service/agents/analysis/workflow/skills/skill_index.yaml`, `src/agent_service/agents/analysis/workflow/tools/tool_registry.yaml`이다. `--output`으로 임시 파일에 생성·비교할 수 있다. `tmp/`의 Skill/Tool은 기존과 동일하게 카탈로그 생성에서 제외된다. 예전 Skill/Tool의 누락으로 실패하는 테스트가 남아 있으므로 전체 테스트가 모두 통과한다고 해석하면 안 된다.

## 역할별 선언 패키지

```text
analysis/
  agent_builders/
    routing/                 agent.py + prompt.md + __init__.py
    intent_classifier/       agent.py + prompt.md + __init__.py
    skill_selector/          agent.py + prompt.md + __init__.py
    workflow_generator/      agent.py + prompt.md + __init__.py
    conditional_decider/     agent.py + prompt.md + __init__.py
    faq/                     agent.py + prompt.md + __init__.py
    report_writer/           agent.py + prompt.md + __init__.py
  tools/catalog.py           분석 업무의 공용 도구
  schemas/                   그래프와 구성요소가 공유하는 업무 계약
  components/interfaces.py   async 호출 계약·Pydantic 결과 검증 (직접 LLM 어댑터 제거)
  dependencies.py            공유 모델 → builder → 그래프 의존성 연결
```

builder는 주입받은 모델을 사용하며 모델·DB 풀·Worker를 import 시점에 만들지 않는다. 7개 역할 모두 공통 factory의 create_agent를 사용한다. 각 builder에 tools, middleware, 출력 규격을 명시하고 RoleAgent는 node payload/결과 변환만 담당한다. 공통 JSON 모드는 검증 미들웨어 또는 명시적인 ProviderStrategy를 사용한다.

전용 스키마나 도구가 생기면 해당 역할 폴더에 추가한다. 여러 노드와 Agent가 쓰는 Workflow/승인/조건 결과 스키마는 공통 schemas에 유지했다. 공용 tools에 있다는 이유로 모든 Agent에 자동 제공하지 않는다. 현재 read_skill_documents는 graph 노드가 직접 호출하며 Workflow 모델에 제공하는 tools는 빈 목록이다. 이 정책을 바꾸는 것은 별도 동작 변경이다.

여러 업무 Agent가 실제 공유하는 도구가 생길 때 agent_service/tools로 올린다. 현재는 분석 카탈로그만 확인되어 analysis/tools를 유지했고 빈 공용 패키지는 만들지 않았다. Executor용 Python 소스(src/agent_service/agents/analysis/workflow/tools)는 LLM에 제공할 LangChain Tool과 다른 개념이다.

프롬프트 로더는 파일 내용의 strip/개행 정규화/공통 템플릿 합성을 하지 않는다. 역할별 기본 prompt.md 7개는 원문을 유지한다. 프로젝트 system_prompt는 ProjectPromptMiddleware가 각 모델 요청에 별도로 추가하며, project_memory 자동 요약/저장은 아직 구현하지 않았다.

실제 LLM을 호출하지 않는 WorkflowRecommender와 file_lookup placeholder에는 가짜 builder나 prompt를 만들지 않는다. 기존 미사용 recommender prompt는 [참고 자료](reference-prompts/workflow_recommender_prompt.md)로 옮겼다.

새 prompt가 wheel에 포함되는지 반드시 확인한다. 패키지 이동 후 로컬 build/에는 삭제한 소스가 남을 수 있으므로 깨끗한 빌드 디렉토리에서 빌드한다. `scripts/diagnostics/validate_agent_package.py`는 설치 파일만 사용해 7개 프롬프트, 실제 builder 조립, Mock 승인/재개를 검증한다.

## 개발 계약의 목표와 현재 이행 상태

아래는 이후 공통 계약이 구현될 때 적용할 표준이다. 아직 존재하지 않는 `definition.py`를 추가하는 것만으로 새 Agent가 서비스에서 호출되지는 않는다.

| 개발 항목 | 목표 계약 | 현재 상태 |
|---|---|---|
| Agent 등록 | id·호환 버전·스키마·factory를 코드 목록에 등록 | 미구현, 현재 분석 graph 직접 연결 |
| 실행 | I/O 노드는 async, 모델은 실제 ainvoke, 순수 변환은 def 허용 | LLM·Mock 및 소비 노드 전환 완료, 다른 I/O는 이행 중 |
| 설정 | settings 스키마만 선언, bootstrap이 중앙 설정 주입 | 중앙 resolver 완료, AgentSettings 세분화는 후속 |
| LLM | 기본 모델/선택 모델을 서비스가 주입 | 기본 모델 factory 및 실제 모델명 context 제공, 요청별 모델 선택 registry는 후속 |
| 프로젝트 문맥 | 모든 모델 호출에 system_prompt 적용, project_memory는 프로젝트 범위로 관리 | prompt snapshot·미들웨어 적용 완료, memory 접근 Protocol만 정의 |
| 파일 | 주입된 ArtifactStore 사용, PV 산출물과 코드 리소스 구분 | artifacts.py의 기존 동기 저장 유지 |
| Executor | 주입된 port를 사용하고 멱등성·접수 결과 보존 | 기존 클라이언트/Worker 경계 유지 |
| 상태 | Agent별 JSON 상태, 공통 결과/대기 projection | 분석 상태만 이동, 공통 projection은 후속 |
| 자원 | Agent가 풀·Worker·lease·세션 잠금을 생성/변경하지 않음 | 기존 서비스 자원 수명 유지 |

새 개발에서 API 라우터나 DB 풀을 Agent 패키지 안에 추가하지 않는다. `asyncio.create_task()`로 추적되지 않는 일을 남기거나, `async def` 안에서 동기 HTTP/LLM을 직접 호출하지 않는다. 기존 동기 I/O는 native async 전환 대상이다. 분석 graph에 연결할 때는 `add_io_node`로 등록하여 취소가 진행 중인 스레드 작업을 남긴 채 완료되지 않도록 한다. 순수 계산·상태 변환과 I/O 없는 interrupt는 일반 노드로 둘 수 있다. 이미 async인 노드에서는 동기 I/O를 직접 호출하지 않고 기존 `run_sync` 경계를 유지한다. 이 방식은 이벤트 루프 정지를 피하고 작업 수명을 관리하지만, 스레드 사용량·I/O 종료 시간의 상한을 보장하지는 않는다.

## 006 이후 호출 방법

```python
class MyComponent:
    async def ainvoke(self, payload, *, context=None):
        response = await self.role_agent.ainvoke(payload, context=context)
        return response

result = await component.ainvoke(payload)
state = await graph.ainvoke(graph_input, config)
async for update in graph.astream(graph_input, config):
    ...
```

`InvokableAgent`/`invoke_typed` 대신 `AsyncInvokableAgent`/`await ainvoke_typed`를 사용한다. 동기 invoke fallback은 없다. 개발용 `compiled_postgres_graph`도 `async with`로 연다. CLI 최상위의 asyncio.run 외에는 노드/모델 내부에 새 event loop를 만들지 않는다.

테스트 대역도 async ainvoke를 구현하고 AsyncMock을 사용한다. 지연은 asyncio.sleep이며 CancelledError를 잡아 정상 결과나 검증 재시도로 바꾸지 않는다. 임의의 모델은 ainvoke 메서드가 있어도 내부에서 sync fallback을 사용할 수 있으므로, 새 provider는 실제 비동기 전송과 취소 전파를 검증해야 한다.

현재 혼합 노드에 사용한 `runtime.blocking.run_sync`는 기존 동기 작업의 이행용이다. 이벤트 루프 밖에서 실행하고 취소 후에도 실제 완료를 기다리지만 전용 ArtifactStore/비동기 DB/HTTP를 대체하는 최종 표준이 아니다. 사용하지 않는 다른 동기 노드까지 보호하지 않으며, 이미 시작한 외부 부작용을 되돌리지 않는다.

## checkpoint 및 리소스 호환

- 그래프 노드 이름·edge·thread 식별·HITL 응답 의미는 유지한다. 011에서 JSON 상태에 project_system_prompt/project_prompt_version snapshot 필드를 추가했다.
- 010 이후 새 Workflow는 `agent_service/agents/analysis/workflow/{tools,skills}/...` 경로를 생성한다.
- 기존 `app/workflow/{tools,skills}/...`와 005~008의 `agent_service/agents/analysis/resources/...` 저장 경로는 같은 원본 자산으로 연결한다. assets 사본이나 symlink를 두지 않는다.
- 경로 호환은 과거 Tool 내용/버전의 보존을 뜻하지 않는다. 이미 생성된 Notebook 코드·Executor 제출 payload를 임의로 다시 생성하거나 멱등성 키를 바꾸면 안 된다.
- 진행 중인 모든 과거 버전의 DB checkpoint를 검증한 것은 아니다. 별도 Agent 버전·배포 중 재개 호환 검증은 남아 있다.

## Agent 개발자 인계 시 확인할 사항

이동 목록으로 담당 파일을 찾고, 프롬프트/Tool 변경이 생성 카탈로그와 맞는지 확인한다. 카탈로그에 없는 파일을 등록된 기능이라고 설명하지 않는다. graph 노드 이름이나 state 구조 변경은 단순 소스 리팩토링과 달리 기존 checkpoint의 재개 호환을 검토한다. 장기 Executor 대기 중 배포될 수 있으므로 이미지 태그와 Agent 호환 버전은 구분해야 한다.

## 기존 Workflow 패키지 유지 원칙

[analysis/workflow 작업 안내](../../src/agent_service/agents/analysis/workflow/README.md)를 따른다. 사용자가 유지하려던 것은 skills·tools·workflows의 패키지 구성이다. 009의 app/workflow 위치 고정 해석은 010에서 정정했다. 원본 자산은 이 패키지 한 곳에서 관리하고, 005·009 기록은 당시 이력으로 보존한다.

### Run별 LLM 선택

역할 Agent 생성은 공통 dependencies 카탈로그 경로를 사용한다. 노드에서
`context_from_state(state)`를 `ainvoke(..., context=...)`로 전달하면 Run에 고정된
모델과 프로젝트 프롬프트를 함께 사용한다. 개별 노드에서 전역 기본 모델을
새로 읽거나 모델 클라이언트를 생성하지 않는다.
[API·설정·장기 Run 정책](../run-model-selection.md)을 참고한다.
