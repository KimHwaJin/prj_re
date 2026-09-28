# Agent 개발 안내

현재 구현 기준: `feature/refactor-agent-async-llm`, 개선 기록 006. 분석 Agent 패키지 이동과 LLM 비동기 호출은 완료했다. 공통 Agent registry/실행 계약 및 HTTP·DB·파일 I/O 전체 전환은 남아 있다.

- [현재 분석 Agent의 파일별 역할](../../src/agent_service/agents/analysis/README.md)
- [전체 목표 구조와 이번 단계의 경계](../architecture/service-layout.md)
- [이동·삭제 목록](analysis-layout-inventory.json)
- [변경·검증·남은 작업](../improvements/005-agent-package-layout.md)
- [LLM 비동기 전환·취소 검증](../improvements/006-agent-async-llm.md)

## 지금 분석 Agent를 수정하는 방법

1. `src/agent_service/agents/analysis/graph.py`에서 실행 흐름을 확인한다. 업무 상태는 `state.py`, 노드는 `nodes/`다.
2. LLM 구성요소는 `components/`, 프롬프트는 `prompts/`, 의존성 조립은 `dependencies.py`에서 수정한다. `components/specs.py`는 분석 내부 구성요소의 명세다. API에서 선택할 업무 Agent registry가 아니다.
3. Workflow 해석·추천·코드 생성은 `workflow/`다. Agent가 읽는 카탈로그 도구는 `tools/catalog.py`, Executor로 보낼 Tool 소스는 `resources/executor_tools/`로 구분한다.
4. `tests/`에서 업무 회귀를 실행한다. 서비스 상태·소유권·DB 테스트는 아직 `src/app/test/`에 있다.

Python 3.11과 잠금파일 의존성을 사용한다. 설치 후 CLI는 `dtest-agent`, 소스 체크아웃에서는 `python cli.py --help`다. API는 기존대로 루트 `python app.py`로 실행한다. CLI는 개발용 그래프 직접 실행 도구이며 서비스의 durable queue/세션 소유권을 검증하는 도구가 아니다.

```sh
# 외부 LLM 없이 승인·제출 경계를 확인하는 검증
PYTHONPATH=src python -m pytest \
  src/agent_service/agents/analysis/tests/test_service_load_mock.py \
  src/agent_service/agents/analysis/tests/test_resource_layout.py -q

# 카탈로그 생성은 명시적으로 실행한다. 서버가 시작할 때 다시 쓰지 않는다.
PYTHONPATH=src python -m devtools.analysis.generate_skill_index
PYTHONPATH=src python -m devtools.analysis.generate_tool_registry
```

생성기 기본 출력은 패키지의 `resources/catalogs/`다. `--output`으로 임시 파일에 생성·비교할 수 있다. `tmp/`의 Skill/Tool은 기존과 동일하게 카탈로그 생성에서 제외된다. 예전 Skill/Tool의 누락으로 실패하는 테스트가 남아 있으므로 전체 테스트가 모두 통과한다고 해석하면 안 된다.

## 개발 계약의 목표와 현재 이행 상태

아래는 이후 공통 계약이 구현될 때 적용할 표준이다. 아직 존재하지 않는 `definition.py`를 추가하는 것만으로 새 Agent가 서비스에서 호출되지는 않는다.

| 개발 항목 | 목표 계약 | 현재 상태 |
|---|---|---|
| Agent 등록 | id·호환 버전·스키마·factory를 코드 목록에 등록 | 미구현, 현재 분석 graph 직접 연결 |
| 실행 | I/O 노드는 async, 모델은 실제 ainvoke, 순수 변환은 def 허용 | LLM·Mock 및 소비 노드 전환 완료, 다른 I/O는 이행 중 |
| 설정 | settings 스키마만 선언, bootstrap이 중앙 설정 주입 | 중앙 resolver 완료, AgentSettings 세분화는 후속 |
| LLM | 기본 모델/선택 모델을 서비스가 주입 | 현재 모델 factory 유지, 요청별 모델 고정은 후속 |
| 프로젝트 문맥 | 모든 모델 호출에 system_prompt 적용, project_memory는 프로젝트 범위에서 읽기·갱신 | 공통 context/projection 경계 후속 |
| 파일 | 주입된 ArtifactStore 사용, PV 산출물과 코드 리소스 구분 | artifacts.py의 기존 동기 저장 유지 |
| Executor | 주입된 port를 사용하고 멱등성·접수 결과 보존 | 기존 클라이언트/Worker 경계 유지 |
| 상태 | Agent별 JSON 상태, 공통 결과/대기 projection | 분석 상태만 이동, 공통 projection은 후속 |
| 자원 | Agent가 풀·Worker·lease·세션 잠금을 생성/변경하지 않음 | 기존 서비스 자원 수명 유지 |

새 개발에서 API 라우터나 DB 풀을 Agent 패키지 안에 추가하지 않는다. `asyncio.create_task()`로 추적되지 않는 일을 남기거나, `async def` 안에서 동기 HTTP/LLM을 직접 호출하지 않는다. 기존 동기 부분은 이 규칙을 이미 충족한 코드가 아니라 이행 대상이다.

## 006 이후 호출 방법

```python
class MyComponent:
    async def ainvoke(self, payload):
        response = await self.model.ainvoke(payload)
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

- 이번 이동에서 graph의 노드 이름·edge·state 필드·thread 식별·HITL 응답 의미는 바꾸지 않았다.
- 새 Workflow는 새 `agent_service/agents/analysis/resources/...` 소스 경로를 생성한다.
- 저장된 `app/workflow/tools/...` Tool 경로와 `app/workflow/skills/...` Skill 별칭은 호환 resolver로 읽는다. 옛 코드 디렉터리를 복제하지 않는다.
- 경로 호환은 과거 Tool 내용/버전의 보존을 뜻하지 않는다. 이미 생성된 Notebook 코드·Executor 제출 payload를 임의로 다시 생성하거나 멱등성 키를 바꾸면 안 된다.
- 진행 중인 모든 과거 버전의 DB checkpoint를 검증한 것은 아니다. 별도 Agent 버전·배포 중 재개 호환 검증은 남아 있다.

## Agent 개발자 인계 시 확인할 사항

이동 목록으로 담당 파일을 찾고, 프롬프트/Tool 변경이 생성 카탈로그와 맞는지 확인한다. 카탈로그에 없는 파일을 등록된 기능이라고 설명하지 않는다. graph 노드 이름이나 state 구조 변경은 단순 소스 리팩토링과 달리 기존 checkpoint의 재개 호환을 검토한다. 장기 Executor 대기 중 배포될 수 있으므로 이미지 태그와 Agent 호환 버전은 구분해야 한다.
