# Analysis Agent

데이터 선택 → 분석 문맥 → Workflow 후보 → 사용자 승인 → Executor 요청/이벤트 → 보고서 흐름의 업무 패키지다. 루트 API·Worker·DB 풀을 소유하지 않는다.

| 위치 | 역할 |
|---|---|
| graph.py | 그래프 조립. 노드 이름/edge는 checkpoint 호환 경계 |
| state.py | JSON으로 저장하는 분석 상태 |
| nodes/, routers/ | 노드와 조건 분기. routers는 HTTP 라우터가 아님 |
| dependencies.py | 주입할 LLM 구성요소 생성. 현재 AgentSettings 호환 유지 |
| components/ | LLM adapter·Workflow 생성·보고서 작성, specs는 내부 구성요소 명세 |
| prompts/ | Markdown/Python 프롬프트. recommender prompt는 현재 미사용 참고 자료 |
| workflow/ | Workflow 컴파일·데이터 준비·조건 결정·코드 생성·결과 해석 |
| schemas/ | 분석 입출력/Workflow/Skill 스키마 |
| tools/catalog.py | Agent가 읽는 Skill/Tool 카탈로그 도구 |
| resources/skills/ | 등록 Skill 문서; tmp는 생성기에서 제외 |
| resources/executor_tools/ | Executor용 코드 생성에 읽히는 Python 소스; tmp는 미등록 |
| resources/catalogs/ | 생성된 skill_index와 tool_registry |
| resource_paths.py | 설치 위치와 무관한 리소스 탐색, 저장된 옛 경로 호환 |
| artifacts.py | 기존 분석 산출물 경로/동기 파일 저장. 공통 ArtifactStore 전환 전 |
| testing/mock_dependencies.py | 서비스 부하테스트에서도 쓰는 명시적 Mock 모델 공급자 |
| tests/ | 분석 업무 및 리소스/HITL 회귀 |

패키지 import만으로 모델·DB 풀·Worker를 생성하지 않는다. 모델을 만드는 함수와 실제 실행은 서비스 조립 또는 명시적 개발 실행에서 호출한다. 프롬프트·카탈로그는 읽기 전용 배포 리소스이고 생성 파일은 설정된 출력 경로/PV에 둔다.

현재 `definition.py`, `projection.py`, 업무 Agent registry는 아직 구현하지 않았다. 동기 invoke·HTTP·파일·Workflow DB 접근도 다음 단계에서 전환한다. [개발 안내](../../../../docs/agent-development/README.md)가 현재와 목표 계약을 구분한다.
