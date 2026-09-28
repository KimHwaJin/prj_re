# 011 — 역할별 create_agent·공통 실행 문맥·프로젝트 지시문

- 날짜: 2026-09-28
- 브랜치: feature/refactor-agent-middleware
- 출발: feature/refactor-unify-analysis-workflow / 3c828c1
- 상태: 구현·오프라인/배포 패키지 검증 완료. 베이스 병합·원격 push·배포 미수행.
- feature/refactor-base에는 006까지 통합되어 있으며 이번 브랜치는 007~010을 포함한다.

## 문제와 개선 방향

008~010에서 역할별 선언과 자산 위치를 정리했지만 Workflow 생성 외의 역할은 직접 LLM 어댑터를 사용했다. 공통 미들웨어·실행 문맥이 없었고 프로젝트 system_prompt를 실제 모델 요청에 연결하지 않았다. 외부 LangGraph의 업무 흐름을 유지하면서 7개 실제 모델 역할을 create_agent로 통일했다.

## 실제 변경

- agent_service/factory.py: 공통 create_agent 조립. RoleAgent는 node payload/응답 변환만 담당한다. 7개 builder에서 역할 프롬프트·도구·미들웨어·출력 규격을 선언한다.
- StructuredLLMAgent, LabelOnlyLLMAgent, SimpleLLMAgent, MarkdownReportAgent와 기존 JsonMessageAgentAdapter를 제거했다. components/interfaces.py에는 async 계약·결과 검증·비모델 placeholder만 남는다.
- AgentContext: 사용자·프로젝트·세션, 프로젝트 지시문·버전, 실제 주입 모델명, 선택적인 project_memory 접근 Protocol. 객체를 JSON 프롬프트에 통째로 섞거나 저장소 객체를 체크포인트에 저장하지 않는다.
- ProjectPromptMiddleware: 각 모델 요청과 JSON 재시도에 프로젝트 system_prompt를 적용한다. 공유 모델·Agent·원본 메시지에 프로젝트 상태를 쓰지 않는다.
- PromptJsonMiddleware: schema 검증 실패만 최대 지정 횟수로 재시도. Skill 선택·조건 결정은 최대 3회, Workflow 생성은 내부 1회와 기존 바깥 노드 최대 3회로 중첩 재시도를 피한다. 전송 재시도는 기존 SDK 설정을 유지한다.
- provider_json_schema는 ProviderStrategy를 명시하고 스키마 오류만 기존 ValueError 계약으로 변환한다. 모델의 bind 결과를 create_agent의 모델로 넘기던 이전 방식은 제거했다.
- 신규 사용자 턴 실행 시 프로젝트 prompt를 사용자/세션/프로젝트로 조회하여 외부 graph에 snapshot을 저장한다. HITL과 장기 Executor 대기 후에도 동일 snapshot을 사용한다. 없는 기존 snapshot은 사용자/Executor 재개 경계에서 한 번 보완한다. 이미 빈 prompt snapshot이 있으면 다시 읽지 않는다.
- 외부 graph 노드·edge·공개 HTTP API·DB 스키마는 유지하며 state에 prompt/버전 JSON 필드만 추가했다. 7개 prompt.md와 Workflow 자산은 원문을 유지했다.
- 역할별 도구는 기존대로 명시적인 빈 목록이다. WorkflowRecommender, file_lookup과 서비스 부하테스트 Mock은 실제 LLM 역할이 아니므로 불필요한 create_agent를 씌우지 않았다. 이들도 context 키워드 계약은 따른다.

## 구현 중 확인한 호환 문제

LangGraph 1.2.11에서 Executor 경계의 durability=sync가 checkpointer=False인 내부 Agent로 상속되면 `_put_checkpoint_fut` 예외가 발생했다. 실제 interrupt → 보고서 Agent 재개 테스트로 재현했다. 내부 호출에 공개 durability=async 인자를 명시해 차단했고 외부의 sync 저장은 유지했다. saver 없는 graph에 durability를 지정했다는 라이브러리 경고는 남는다. 내부 상수를 사용하거나 전역 경고를 숨기지 않는다.

ProviderStrategy의 StructuredOutputValidationError는 기존 ValueError와 상속 관계가 달라 Workflow 노드의 검증 재시도를 건너뛸 수 있었다. 스키마 예외만 변환하고 두 JSON 모드의 실제 create_agent 실행에서 첫 잘못된 응답 → 두 번째 정상 응답을 검증했다.

## 검증

[검증 요약·프롬프트 해시](../reports/agent-middleware-validation-2026-09-28.json).

- 실제 create_agent와 ChatOpenAI를 in-process HTTP MockTransport로 실행하여 7개 역할 × 2개 모드의 요청·응답 확인. 동기 HTTP 사용 시 실패하도록 검증했으며 실제 외부 모델은 호출하지 않았다.
- 집중 검증 53개 통과: 프로젝트 간 동시 문맥 격리, JSON 재시도 제한과 prompt 중복 방지, 실제 task 취소 및 HTTP 취소, 실제 graph HITL 재개·재조립, sync Executor 재개 후 보고서와 이전 snapshot 보완, 서비스의 신규/스트리밍/사용자 resume 경계, 자원 수명 검증.
- 최종 전체 오프라인 회귀 **225 passed / 19 failed / 39 skipped**. 010의 실패 테스트 이름 및 UUID 정규화 후 메시지와 모두 동일. 새 실패 없음.
- 기존 실패 19개를 분류했다: 이전 Workflow 응답 형식 관련 8개, 미등록/부재 Skill 기대 관련 8개, 조건 규칙의 기존 assertion 불일치 3개. 이번 전환을 위해 테스트를 삭제하거나 업무 자산을 임의로 카탈로그에 등록하지 않았다.
- 기존 select_features/split_dataset 수집 오류 2개는 이전과 동일하게 제외했다. PostgreSQL 조건부 테스트 39개 등은 건너뛰었다. 실제 DB 조회·운영 checkpoint migration·LLM 제공자 호환까지 검증했다고 주장하지 않는다.
- 깨끗한 임시 build 경로에서 wheel 생성 후 source checkout을 제외한 python -I 검증: 7개 create_agent 역할/독립 prompt, 통합 자산, OpenAPI 33개, Mock 승인·재개·Executor 요청 6 steps, 이전 자산 사본 없음.
- git diff --check 통과. 실제 DB·Redis·Executor 호출, 컨테이너 변경, 원본 checkout 변경 없음.

주요 재실행 명령:

```sh
PYTHONPATH=src python -m pytest \
  src/agent_service/agents/analysis/tests/test_agent_middleware.py \
  src/agent_service/agents/analysis/tests/test_async_llm.py \
  src/app/test/test_agent_project_context.py \
  src/app/test/test_graph_runtime_lifecycle.py -q

PYTHONPATH=src python -m pytest src/app/test src/agent_service/agents/analysis/tests -q \
  --ignore=src/agent_service/agents/analysis/tests/test_select_features.py \
  --ignore=src/agent_service/agents/analysis/tests/test_split_dataset.py
```

## 남은 작업과 개발자 안내

[Agent 선언·문맥·미들웨어 가이드](../agent-development/agent-runtime-contract.md)를 기준으로 역할을 추가한다. project_memory는 접근 계약만 정의했으며 자동 읽기·요약·저장, 갱신 충돌 처리와 Store 연결은 후속이다. main_model_name에 따른 모델 선택·재개 고정 및 업무 Agent registry도 후속이다. 나머지 HTTP·DB·파일 I/O 전환과 기존 업무 실패 정상화는 이번 완료 범위에 포함하지 않는다.
