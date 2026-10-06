# Analysis Agent

현재 실행은 `planning/graph.py`의 `agentic-planning-v1` 한 그래프를 사용한다. 사용자 요청과 Executor 결과 입력을 공통 명령 Worker가 처리하고, container가 같은 builder를 조립한다. 이전 설문형 graph/state/nodes와 전용 역할은 054에서 제거했다.

| 위치 | 수정할 책임 |
|---|---|
| state.py | 평면 체크포인트 채널의 수명/타입과 23개 노드별 입력 경계 |
| planning/lifecycle.py | 새 요청 Run 상태 초기화; resume/완료에는 적용하지 않음 |
| planning/graph.py | 새 요청·후속 답변·계획 후보·typed HITL·재작성·승인 연결 |
| planning/runtime.py | 고정된 모델 선택, 역할 인스턴스 캐시, 요청별 AgentContext·Store 정책 주입 |
| planning/catalog.py | 배포된 Skill/Tool 문서·함수 추출·해시, 메타데이터 조회 도구 |
| planning/proposals.py | 재작성·실행별 자유 함수 제안의 검증 |
| planning/testing.py | 명시적 mock provider 응답; 실제 모델 오류의 fallback이 아님 |
| execution/nodes.py | 승인 코드 제출·Redis 이벤트 재개·관찰·판단·최종 완료·보고서 연결 |
| execution/compiler.py | 승인 snapshot 검증·batch 구성·필요한 import를 포함한 등록 함수 제출 |
| execution/repair_nodes.py, repair_policy.py, sources.py | MULTI 오류 수정의 수준·시도·승인·소스 검증 |
| execution/grounding.py, report.py | 후속 답변·보고서의 실제 실행 근거 및 수치 렌더링 |
| agent_builders/conversation/ | 답변·Skill 탐색·계획, prompt.md와 planning_prompt.md |
| agent_builders/plan_revision/ | 실행 전 자연어 재작성·추가 질문·실행별 코드 |
| agent_builders/execution_review/ | 이전 실행 결과를 바탕으로 승인 범위 안의 다음 판단 |
| agent_builders/execution_report/ | 결과 해석문·근거 Step 선택 |
| agent_builders/execution_repair/ | 수정 제안, 실제 적용 허용 여부는 순수 정책에서 검증 |
| workflow/skills/, tools/, workflows/ | 기존 작업자가 계속 관리하는 업무 자산·생성기; 등록 자산 유지 |
| tests/agent_service/ (레포 루트) | 현재 Agent·등록 자산·공개 Workflow 계약 회귀 |

공통 모델 생성은 `dtest/agent_service/runtime/model_factory.py`, create_agent 조립은 `dtest/agent_service/factory.py`, 요청 문맥은 `dtest/agent_service/context.py`, 공통 정책은 `dtest/agent_service/middleware/`에 있다. import만으로 모델·DB·Worker를 시작하지 않는다.

역할별 선언과 독립 프롬프트는 같은 폴더에 둔다. 전체 업무 Agent registry나 등록 API가 아니며 다른 업무 Agent 추가는 후순위다. [개발 안내](../../../../../docs/agent-development/README.md), [역할·미들웨어 계약](../../../../../docs/agent-development/agent-runtime-contract.md), [현재 메모리 정책](../../../../../docs/project-memory.md), [상태 수명과 입력 경계](../../../../../docs/agent-development/analysis-state-lifecycle.md)을 따른다.

개발용 CLI·Studio는 현재 builder의 외부 호출 없는 mock 실행이다. 실제 API·SSO·소유권·DB·Redis·Executor 연계는 서비스 Runs API에서 확인한다. 이전 설문형 checkpoint를 새 계획 checkpoint로 해석하거나 자동 이행하지 않는다.

102에서 미사용 구형 Workflow 컴파일·노트북 생성·데이터 mock·중복 catalog/schema·이전 경로 alias를 삭제했다. 현재 실행 컴파일러는 execution/compiler.py 하나이며 자산 조회는 planning/catalog.py가 담당한다.
