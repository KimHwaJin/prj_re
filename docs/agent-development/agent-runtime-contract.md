# 역할별 Agent 선언·실행 문맥·미들웨어

011 기준으로 routing, intent_classifier, skill_selector, workflow_generator, conditional_decider, faq, report_writer의 실제 모델 호출은 모두 create_agent를 통과한다. 분석의 LangGraph가 업무 순서·사용자 승인·Executor 대기를 관리한다.

## 수정할 파일

```text
agent_service/
  context.py                 AgentContext, ProjectMemory 접근 Protocol
  factory.py                 create_agent 조립·역할 응답 변환
  middleware/
    project_prompt.py        매 모델 요청에 프로젝트 지시문 추가
    prompt_json.py           JSON 검증·제한된 재시도
  agents/analysis/
    agent_builders/<role>/
      agent.py               해당 역할의 도구·미들웨어·출력 계약 선언
      prompt.md              역할별 독립 기본 프롬프트
    context.py               checkpoint snapshot → AgentContext
    components/interfaces.py async 호출 Protocol·Pydantic 결과 검증
    nodes/                   역할 Agent 호출과 업무 결과 처리
    workflow/                기존 처리 모듈·skills·tools·workflows
```

## Agent를 선언하는 방법

```python
from agent_service.factory import build_role_agent, json_output
from agent_service.middleware import ProjectPromptMiddleware

def build_agent(model, *, structured_output_mode="prompt_json"):
    return build_role_agent(
        model,
        name="my_role",
        system_prompt=load_prompt(__package__),
        tools=[],
        middleware=[ProjectPromptMiddleware()],
        output_type=MyOutput,
        structured_output_mode=structured_output_mode,
        max_validation_attempts=3,
        decode=json_output(MyOutput),
    )
```

`load_prompt`와 `MyOutput`은 해당 역할의 프롬프트 로더와 Pydantic 스키마를 import한다. 도구·미들웨어는 역할 파일에서 명시적으로 선택한다. 공용 tools에 있다는 이유로 자동 노출하지 않는다. 현재 7개 역할의 tools는 빈 목록이며 카탈로그 문서 조회는 기존처럼 바깥 노드가 통제한다.

역할별 prompt.md는 내용이 같아도 별개 파일로 관리한다. 공통 factory에는 역할별 업무 지시문을 넣지 않는다. 모델·DB 풀·Worker를 builder 내부에서 새로 생성하지 않는다.

## 실행 문맥과 상태

노드는 `await agent.ainvoke(payload, context=context_from_state(state))` 또는 같은 context를 전달하는 `ainvoke_typed`를 사용한다. RoleAgent는 payload를 사용자 메시지로 변환하고 create_agent 결과를 역할의 반환형으로 변환한다. 직접 모델을 호출하지 않는다.

| 항목 | 전달·보존 방식 |
|---|---|
| user_id/project_id/session_id | AgentContext로 전달; 자동 프롬프트 삽입하지 않음 |
| 프로젝트 system_prompt·버전 | 서비스가 신규 사용자 턴 실행 시 DB에서 조회 → 외부 graph의 JSON 상태에 snapshot → 매 내부 호출의 AgentContext |
| 실제 모델명 | factory에 주입된 모델에서 가져와 AgentContext.model_name 제공 |
| project_memory 접근 객체 | AgentContext의 선택적 Protocol; 아직 실제 저장소·자동 읽기/요약/쓰기는 미구현 |

프로젝트 지시문은 ProjectPromptMiddleware가 역할 기본 지시문 뒤에 추가한다. 공유 Agent 객체나 원본 메시지를 수정하지 않으므로 프로젝트 간 동시 호출에 섞이지 않는다. JSON 재시도도 미들웨어를 다시 통과하며 같은 지시문을 중복 누적하지 않는다.

신규 사용자 턴을 시작할 때 최신 프로젝트 프롬프트를 읽는다. 같은 턴의 HITL 및 Executor 이벤트 재개는 기존 snapshot을 사용한다. 프로젝트 프롬프트를 수정해도 진행 중인 턴에 즉시 반영하지 않으며 다음 신규 턴부터 적용한다. snapshot이 없는 이전 체크포인트는 서비스의 사용자/Executor 재개 경계에서 한 번 조회해 보완한다. `""`도 유효한 snapshot으로 취급한다.

개발용 graph 직접 실행은 DB 조회를 자동 수행하지 않는다. 프로젝트 정책을 검증하려면 초기 state에 project_system_prompt/project_prompt_version을 넣는다. API는 요청 본문의 값을 그대로 신뢰하지 않고 세션·사용자·프로젝트를 확인해 서버에서 읽는다.

## 응답 규격과 재시도

| 역할 | 반환 계약 | 재시도 책임 |
|---|---|---|
| routing / intent_classifier | 기존 label 검증 → Pydantic 결과 | 잘못된 label은 실패 |
| skill_selector / conditional_decider | Pydantic JSON | prompt_json은 최대 3회, provider_json_schema는 ProviderStrategy |
| workflow_generator | WorkflowPlanOutput | 역할 호출은 1회; 기존 바깥 노드가 JSON·업무 검증을 최대 3회 수행 |
| faq / report_writer | answer/content 문자열 dict | 빈 응답은 실패 |

prompt_json은 tool calling이나 provider JSON Schema 지원을 강제하지 않는다. provider_json_schema는 `ProviderStrategy(..., strict=True)`를 명시한다. 자동 ToolStrategy 전환은 하지 않는다. provider의 스키마 검증 예외는 기존 Workflow 노드가 재시도할 수 있도록 ValueError로 변환한다. 전송 장애는 기존 SDK 설정을 따르며 취소를 응답 검증 재시도로 바꾸지 않는다.

## 체크포인트와 버전 제약

내부 역할 Agent는 `checkpointer=False`로 호출 단위 상태만 갖는다. 영속화는 외부 업무 graph가 담당한다. 사용자 HITL과 Executor 대기를 내부 Agent에 중복 추가하지 않는다.

고정된 LangGraph 1.2.11에서 외부 `durability="sync"`가 영속화 없는 내부 graph에 상속되면 `_put_checkpoint_fut` 예외가 발생한다. 공통 RoleAgent에서 공개 API의 `durability="async"`를 명시해 상속을 차단한다. 내부에 saver가 없으므로 저장 모드 자체는 효과가 없다는 라이브러리 경고가 발생할 수 있다. 외부 graph의 sync 저장 정책은 유지하며 전역 경고 억제나 라이브러리 내부 패치는 하지 않는다. 버전 업그레이드 시 Executor 재개 회귀를 유지하면서 이 우회를 재검토한다.

## 다음 단계

project_memory의 저장소·동시 갱신·근거·요약 정책과 실제 미들웨어 연결은 별도 구현한다. 현재의 호출 단위 메시지에 SummarizationMiddleware를 넣는 것만으로 프로젝트 지식이 추출·저장되지는 않는다. 요청의 main_model_name으로 모델을 선택하고 재개 시 고정하는 registry도 후속이다. 현재는 주입된 기본 모델 정보를 전달한다.

공식 API 참고: [미들웨어](https://docs.langchain.com/oss/python/langchain/middleware/custom), [구조화 출력](https://docs.langchain.com/oss/python/langchain/structured-output), [create_agent](https://reference.langchain.com/python/langchain/agents/factory/create_agent).
