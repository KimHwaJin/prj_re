# 역할별 Agent 선언·실행 문맥·미들웨어

054 기준으로 분석 LangGraph는 새 요청·계획 승인·Executor 대기·결과 판단·재작성·수정을 관리하고, 다섯 내부 역할은 LangChain create_agent로 모델을 호출한다. 업무 실행 builder는 `planning/graph.py` 하나다.

## 파일과 선언

```text
agent_service/
  context.py                  요청별 AgentContext
  factory.py                  공통 create_agent·응답 변환
  runtime/model_factory.py    설정된 OpenAI 호환 모델 생성
  middleware/                 프로젝트 prompt·JSON·탐색·세션 근거·메모리
  agents/analysis/
    planning/graph.py         PlanningState와 새 요청·HITL
    planning/runtime.py       고정 모델/역할 캐시와 context·Store 주입
    planning/catalog.py       Skill/Tool 조회 도구
    execution/                실제 제출·관찰·판단·수정·보고서
    agent_builders/<role>/     agent.py·독립 prompt.md
    workflow/                 기존 업무 자산·1.3 관리/컴파일 지원
```

```python
from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware

def build_agent(model, *, structured_output_mode="prompt_json", store=None):
    return build_role_agent(
        model, name="my_role", system_prompt=load_prompt(__package__),
        tools=[], middleware=[ProjectPromptMiddleware()],
        output_type=MyOutput, decode=json_output(MyOutput),
        structured_output_mode=structured_output_mode,
        max_validation_attempts=3, store=store,
    )
```

load_prompt/MyOutput은 해당 역할의 로더·Pydantic schema를 import한다. 역할별 prompt는 독립 파일로 유지한다. builder는 주입받은 모델·Store를 사용하며 import 시 모델·풀을 만들지 않는다. 노드는 역할을 `await agent.ainvoke(payload, context=context)`로 호출한다. 삭제한 dependencies/ainvoke_typed/설문형 노드로 우회하지 않는다.

## 현재 역할 계약

| 역할 | 반환 schema / 책임 | 호출 구성 |
|---|---|---|
| conversation | Reply: 답변·Skill 선택·등록 자산 계획·선택적 memory_updates | PlanningContract·metadata 탐색·세션 근거·메모리, 의미 검증 최대 3회 |
| plan_revision | RevisionReply: plans/clarification·기존 계획 patch·실행별 함수 | Skill/Tool 및 함수 원문 조회, 세션 근거, 최대 2회 |
| execution_review | ReviewResponse: choices/needs_user_input/message | 제공된 성공 관찰·승인된 decision 검증, 최대 2회 |
| execution_report | ReportResponse: markdown/evidence_steps | 관찰 Step ID·수치 제약, 최대 2회 |
| execution_repair | RepairResponse: can_repair·변경 제안·근거 | 허용 수준에 따른 탐색, 수정 정책 검증, 최대 2회 |

prompt_json은 PromptJsonMiddleware로 출력 schema·의미를 검증한다. provider_json_schema는 명시적 ProviderStrategy이며 가능한 의미 검증은 동일한 재시도 middleware를 거친다. 전송 오류·취소를 출력 재시도로 바꾸지 않는다. 횟수는 역할의 검증 시도 상한이며 metadata 도구 탐색 횟수나 SDK 전송 retry와 다르다.

Conversation의 메타데이터 탐색과 Executor 분석 함수 실행은 구분한다. 계획 승인 전 Executor를 생성하지 않으며, 승인 후 등록 함수의 docstring만 제거한 코드를 제출한다. [실행 규격](../workflow-json-reference.md), [Executor 연결](../agentic-executor-runtime.md)을 따른다.

## 문맥·모델·수명

| 항목 | 보존 방식 |
|---|---|
| user_id/project_id/session_id | 서비스가 소유권을 검증한 뒤 state/context로 전달 |
| system_prompt와 버전 | 신규 사용자 턴에서 읽은 snapshot을 HITL/Executor 재개에도 사용 |
| 모델 pin | Run의 name/revision을 resolve, 진행 중 default 변경으로 교체하지 않음 |
| 세션 분석 근거 | 같은 세션 완료 분석의 원본 관찰·source, 현재 요청으로 새로 bind |
| 프로젝트 메모리 | 공식 runtime.store + 요청별 소유권/버전 정책, 역할별 입력 예산 |

PlanningRuntime은 허용 모델별 역할을 캐시한다. 사용자별 mutable context를 공유 인스턴스에 보관하지 않는다. create_agent 생성에도 같은 공식 Store를 전달하며 bind_context가 매 요청마다 정책과 한도를 주입한다. 원시 Store aput으로 API·Worker 권한/버전 검사를 우회하지 않는다.

ProjectPromptMiddleware는 기본 역할 prompt 뒤에 프로젝트 snapshot을 넣고 재시도에서 중복하지 않는다. 서비스 신규 턴/재개 경계가 DB 접근을 담당하며 Agent는 DB를 직접 조회하지 않는다. snapshot 없는 호환 상태 보완과 공개 API 모델 검증은 기존 서비스 어댑터 책임이다.

내부 역할은 checkpointer=False, 외부 graph는 서비스의 PostgreSQL saver·동일 session thread_id를 사용한다. 고정 LangGraph 버전에서 외부 sync durability 상속을 피하는 RoleAgent의 durability=async를 유지한다. 이 값은 내부 저장을 켠다는 뜻이 아니다. 경고를 전역으로 숨기지 않는다.

Executor API는 runtime 소유 비동기 Client를 빌려 접수 결과까지만 기다린다. 실제 실행 대기는 외부 graph interrupt로 종료한다. 파일/PV 읽기·쓰기는 기존 보호된 run_sync를 사용하며 취소됐다고 진행 중 thread를 버리지 않는다.

## 개발 도구·호환

devtools.analysis.runtime/cli와 langgraph_dev는 외부 호출 없는 mock 계획 실행이다. 시각화는 같은 builder의 전체 실행 분기를 포함하고 Executor 객체는 호출 불가능한 topology 표식이다. 실제 권한·큐·Store·Redis·Executor 검증은 Runs API를 사용한다. 전용 서비스 DB 풀을 여는 개발용 compiled_postgres_graph는 제거했다.

054는 현재 노드 이름·state 필드·runtime 버전을 유지한다. 조건부 분기 목적지는 검사·그림용으로 명시했으며 같은 execution_phase가 같은 노드를 선택한다. 과거 설문형 그래프의 checkpoint는 이미 현재 Runtime과 별개이며 자동 이행을 지원하지 않는다.

[메모리 한도·갱신·역할 정책](../project-memory.md), [Run별 모델 선택](../run-model-selection.md), [현재 공개 API](../public-run-api.md)를 따른다. 새로운 업무 Agent registry·자동 전체 요약·동적 Dataset 연계는 후속이다.
