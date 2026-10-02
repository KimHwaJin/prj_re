# Analysis Agent

데이터 선택 → 분석 문맥 → Workflow 후보 → 사용자 승인 → Executor 요청/이벤트 → 보고서 흐름의 업무 패키지다. 루트 API·Worker·DB 풀을 소유하지 않는다.

| 위치 | 역할 |
|---|---|
| graph.py | 그래프 조립. 노드 이름/edge는 checkpoint 호환 경계 |
| state.py | JSON으로 저장하는 분석 상태 |
| nodes/, routers/ | 노드와 조건 분기. routers는 HTTP 라우터가 아님 |
| context.py | 체크포인트의 프로젝트 prompt snapshot을 AgentContext로 변환 |
| dependencies.py | 주입할 LLM 구성요소 생성. 현재 AgentSettings 호환 유지 |
| agent_builders/ | 역할별 agent.py/build_agent 및 독립 prompt.md 7개 |
| components/interfaces.py | async 호출 계약·Pydantic 결과 검증. 직접 LLM 어댑터 제거 |
| workflow/ | Workflow 컴파일·데이터 준비·조건 결정·코드 생성·결과 해석 |
| schemas/ | 분석 입출력/Workflow/Skill 스키마 |
| tools/catalog.py | Agent가 읽는 Skill/Tool 카탈로그 도구 |
| workflow/skills/ | 기존 작업자가 관리하는 Skill 문서·색인·생성기; tmp 보존 |
| workflow/tools/ | 기존 Executor Tool 원본·레지스트리·생성기; tmp 보존 |
| workflow/workflows/ | 기존 Workflow 자산·수명주기 정책 |
| resource_paths.py | workflow.paths를 참조하는 얇은 경계. 원본 자산 복제 없음 |
| artifacts.py | 기존 분석 산출물 경로/동기 파일 저장. 공통 ArtifactStore 전환 전 |
| testing/mock_dependencies.py | 서비스 부하테스트에서도 쓰는 명시적 Mock 모델 공급자 |
| tests/ | 분석 업무 및 리소스/HITL 회귀 |

패키지 import만으로 모델·DB 풀·Worker를 생성하지 않는다. 모델을 만드는 함수와 실제 실행은 서비스 조립 또는 명시적 개발 실행에서 호출한다. 프롬프트·카탈로그는 읽기 전용 배포 리소스이고 생성 파일은 설정된 출력 경로/PV에 둔다.

현재 `definition.py`, `projection.py`, 업무 Agent registry는 아직 구현하지 않았다. 006에서 구성요소·LLM 호출은 `await ainvoke()`로 전환했고 그래프 호출도 `ainvoke`/`astream`을 사용한다. HTTP·파일·Workflow DB 전체 전환은 남아 있으며, 이번에 비동기로 바꾼 혼합 노드의 기존 I/O만 `run_sync`로 종료를 추적한다. [개발 안내](../../../../docs/agent-development/README.md)가 현재와 목표 계약을 구분한다.

008에서 역할별 선언과 프롬프트를 함께 배치했다. 프롬프트는 동일한 내용이어도 역할마다 별개 파일로 유지한다. tools/catalog.py는 분석 공용이며 모델에 자동 노출하지 않는다. 011에서 7개 LLM 역할을 공통 create_agent로 통일하고 프로젝트 prompt/JSON 미들웨어를 적용했다. 011 당시 project_memory는 접근 계약만 정의했다. 현재 052에서는 공식 LangGraph Store를 create_agent/runtime.store에 연결하며 051의 현재 사용자 원문 추출 범위는 유지한다. 생성형 자동 요약은 후속이다. [현재 메모리 계약](../../../../docs/project-memory.md)을 따른다.

010에서 기존 app/workflow의 skills·tools·workflows를 이 패키지의 workflow/ 아래로 통합했다. [Workflow 유지보수 안내](workflow/README.md)에서 자산·생성기와 기존 처리 모듈의 역할을 확인한다.

[Agent 실행 문맥·미들웨어 가이드](../../../../docs/agent-development/agent-runtime-contract.md)에서 역할 추가 방법과 snapshot/retry 경계를 확인한다.

025: API 코드는 `api_service`, 공유 Executor/Workflow 규격은 `service_contracts`, HTTP·manifest 어댑터는 `integrations/executor`로 분리했다. `schemas/workflows/workflow_format.py`는 `service_contracts/workflow_definition.py`로 이동했으며 이전 파일은 제거했다. 업무 흐름·역할별 프롬프트·`workflow/{skills,tools,workflows}`는 그대로 유지한다. HTTP는 024의 native async 경로이며 파일/PV/WorkflowStore는 기존 소유권 보호 스레드 경계를 사용한다.
