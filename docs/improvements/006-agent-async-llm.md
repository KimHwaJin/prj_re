# 006. Agent·LLM 비동기 호출과 취소 전파

| 항목 | 내용 |
|---|---|
| 상태 | LLM 호출 경로 전환·검증 완료 / 전체 I/O 전환·배포 미완료 |
| 시작일 / 완료일 | 2026-09-28 / 2026-09-28 |
| 브랜치 | feature/refactor-agent-async-llm |
| 출발 commit | 856e008 — 005까지 feature/refactor-base 통합 완료 |
| 구현 commit | a27433d — refactor: await native async LLM calls across analysis graph |
| 배포·migration | 없음. 기존 실행 컨테이너/서비스 DB 변경 없음 |

## 문제

외부 graph.ainvoke를 사용해도 내부 동기 노드는 LangGraph의 executor thread에서 실행됐다. Agent adapter와 모델 호출은 invoke였고 Mock 지연도 time.sleep이었다. 대기 중 스레드를 점유하며, graph coroutine 취소가 실행 스레드 종료를 뜻하지 않았다.

이번에 재현을 테스트로 저장했다. 동기 노드가 Event에서 대기할 때 RunService가 CancellationRequested를 반환한 뒤에도 노드는 살아 있었고, Event를 해제하면 후속 동작을 수행했다. 이 테스트는 여전히 남아 있는 **관리되지 않는 동기 노드 경로의 제한**을 보여주는 characterization이며, 문제가 전역적으로 해결됐다는 테스트가 아니다.

## 변경

- AgentDependencies의 구성요소 계약은 AsyncInvokableAgent. 모든 adapter는 async ainvoke를 제공하며 동기 invoke fallback은 두지 않는다. 타입 검증 helper는 await ainvoke_typed다.
- Simple/LabelOnly/Structured LLM과 Markdown 보고서 adapter는 모델의 ainvoke를 직접 await한다. prompt_json 검증 재시도와 provider_json_schema 의미를 유지하고 CancelledError를 검증 실패로 재시도하지 않는다.
- JsonMessageAgentAdapter는 내부 LangChain Agent의 ainvoke를 await한다. 외부 graph의 checkpointer를 내부 LLM Agent에 상속하지 않는 설정은 유지했다.
- 라우팅·의도 분류·FAQ·파일 조회 placeholder·추천·Skill 선택/Workflow 생성·조건 판단·보고서 노드를 비동기로 전환했다. 실제 입출력 형태, graph node ID/edge/state 필드와 HTTP API는 바꾸지 않았다.
- Mock 지연은 asyncio.sleep으로 바꿨다. 취소 후 응답 생성 함수가 실행되지 않으며, 내부 업무 로직을 흉내 내는 방식은 기존과 같다. WorkflowRecommender placeholder도 같은 비동기 계약을 사용한다.
- 기존 테스트 대역과 그래프 호출을 ainvoke로 이관했다. CLI는 astream/aget_state, PostgreSQL 개발 실행은 AsyncPostgresSaver 경로를 사용한다. asyncio.run은 CLI 최상위 진입점에서만 사용한다.

## 혼합 노드의 동기 I/O

LLM 노드를 async def로만 바꾸면 남은 동기 파일·DB·HTTP 호출이 이벤트 루프에서 실행되는 문제가 생긴다. 따라서 이번에 바뀐 노드 안의 해당 호출은 runtime/blocking.py의 run_sync로 격리했다.

- 카탈로그/Skill 읽기, 소스 경로 조회, Workflow 컴파일 중 리소스 읽기.
- 조건 판단 뒤 Notebook preview 및 기존 store.record_adaptive_round.
- 보고서 소스 파일 생성, 기존 Executor artifact HTTP 제출 및 JSON 저장.

run_sync는 contextvars를 전달한 executor Future를 shield하고, 호출자가 반복 취소돼도 작업 종료까지 기다린 후 취소를 전파한다. 새 동기 작업 제출 전에도 취소 기회를 제공한다. 실제 작업 오류는 회수하며 취소 중 발생한 오류를 정상 결과로 바꾸지 않는다. 시작된 쓰기/HTTP 요청을 되돌리는 기능은 아니다.

**임시 이행 경계**다. 기본 executor를 사용하며 전용 풀/제출량 제한, 파일 원자적 교체, 전체 I/O 작업 등록, 네이티브 비동기 HTTP/DB를 구현한 것은 아니다. 이 helper 밖에 남아 있는 기존 동기 graph 노드까지 보호하지 않는다. I/O가 영원히 멈추면 helper 자체는 실제 종료를 기다리므로 서비스의 종료 감독·복구 필요 정책이 적용되어야 한다. 이를 파일 timeout이나 강제 thread 종료로 설명하면 안 된다.

## 검증

아래는 006 완료 시점의 검증 기록이다. 이후 [007](007-remove-azure.md)에서 Azure 지원과 전용 테스트 5개를 제거했으며 당시 검증 수치는 보존한다.

[검증 JSON](../reports/agent-async-llm-validation-2026-09-28.json)과 다음 테스트에 기록했다.

- analysis/tests/test_async_llm.py: 16개. 기존 동기 스레드 문제 재현, 실제 FAQ 노드의 Run 취소 전파/후속 제출 차단, 두 호출의 동시 모델 대기, Mock 지연 취소, 구조화 출력 재시도 중 취소, 반복 취소를 받는 혼합 노드의 실제 작업 종료 및 context 전달.
- OpenAI 호환 및 Azure 설정 factory로 실제 LangChain 모델을 생성했다. plain/prompt_json/provider_json_schema/nested_agent/cancel 각 모드를 HTTPX MockTransport로 검증했다. 동기 HTTP transport는 호출되면 실패하도록 했다. 모든 경우 비동기 경로를 사용했고 전송 대기 중 취소도 transport 종료까지 전파됐다. 외부 서버의 실제 추론 중단을 입증한 것은 아니다.
- app/test/test_async_llm_postgres.py: 2개. 실제 분석 graph/Mock LLM의 승인 대기 저장 후 PostgreSQL 풀·graph 재생성 및 승인 재개, 실제 중첩 LangChain Agent/async-only 모델과 외부 AsyncPostgresSaver/HITL의 공존을 확인했다.
- 전체 회귀: **237 passed / 19 failed**. 005의 219 passed에 신규 18개가 추가됐다. 기존 실패 19개는 이름뿐 아니라 실행 ID를 정규화한 오류 메시지도 동일함을 대조했다. 기존 select_features/split_dataset 소스 누락에 따른 수집 오류 2개는 이전과 동일하게 명시 제외했다.
- 설치 wheel을 소스 체크아웃과 분리해 API OpenAPI 생성 및 Mock 분석 graph 승인/재개/Executor 요청 생성(6 steps)을 확인했다. 루트 cli.py --help도 통과했다. 실제 외부 LLM·Executor·Redis는 호출하지 않았다.

Python 3.11.15, 기존 lock 의존성, PostgreSQL 17-alpine의 별도 localhost identity_test를 사용했다. 검증용 dtest-refactor-async-llm-test 컨테이너는 제거했다. 운영 부하·다중 Pod·실제 Gaia 검증은 수행하지 않았다.

```sh
PYTHONPATH=src python -m pytest src/agent_service/agents/analysis/tests/test_async_llm.py -q
# 폐기 가능한 localhost identity_test에만 설정한다.
export DTEST_IDENTITY_TEST_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:<port>/identity_test'
PYTHONPATH=src python -m pytest src/app/test src/agent_service/agents/analysis/tests -q \
  --ignore=src/agent_service/agents/analysis/tests/test_select_features.py \
  --ignore=src/agent_service/agents/analysis/tests/test_split_dataset.py
```

## 개발자 이관 및 다음 단계

직접 호출은 await agent.ainvoke, 그래프는 await graph.ainvoke / async for graph.astream으로 바꾼다. 동기 invoke만 가진 대역/외부 adapter는 새 계약을 구현해야 한다. compiled_postgres_graph는 async with로 사용한다. [개발 안내](../agent-development/README.md)를 갱신했다.

이번 테스트의 동시 모델 대기는 LLM 계층이 이벤트 루프를 양보함을 보여준다. Worker가 여러 Run을 동시에 claim하게 바뀐 것은 아니며 처리량 개선 수치를 측정하지 않았다.

다음은 Executor HTTP·Workflow DB의 네이티브 비동기 전환과 주입 경계, 이후 전용 ArtifactStore와 남은 동기 노드의 종료 추적이다. 이미 접수된 외부 요청의 결과 복구/멱등성·전체 checkpoint fencing·lease 만료 자동 복구·LLM HTTP client 자원 수명 통합도 별도 요구다. LLM 대기 취소가 외부 추론/Executor 작업을 서버에서 중단시킨다는 보장은 하지 않는다.
