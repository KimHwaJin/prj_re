# Workflow 코드·업무 자산

기존 app/workflow의 skills·tools·workflows 하위 구성을 보존하면서 분석의 Workflow 처리 코드와 합친 단일 유지보수 패키지다.

```text
agents/analysis/workflow/
  skills/                 Skill 문서·skill_index.yaml·generate_skill_index.py
  tools/                  Executor Tool 소스·tool_registry.yaml·generate_tool_registry.py
  workflows/              기존 workflow_lifecycle.md (정책 문서)
  workflow_compiler.py     Workflow 계획을 실행용 Workflow로 컴파일
  data_load_steps.py       데이터 로드 단계 구성
  rule_based_notebook_generator.py  Tool 소스를 Notebook 코드로 변환
  adaptive_workflow.py     조건부 실행 지원
  execution_notebook_reader.py      실행 Notebook 결과 해석
  paths.py                설치 위치 탐색·저장된 이전 경로 호환
```

## 유지보수 방법

Skill/Tool은 이 디렉터리에서 수정하고 레포 루트에서 생성기를 실행한다.

```sh
python src/agent_service/agents/analysis/workflow/skills/generate_skill_index.py
python src/agent_service/agents/analysis/workflow/tools/generate_tool_registry.py
```

기본 출력은 각각 skills/skill_index.yaml, tools/tool_registry.yaml이다. 기존 --skills-dir/--tools-dir, --output 인자를 유지한다. 검증에는 --output으로 임시 파일을 지정할 수 있다. tmp 자산은 보존하되 기존 정책대로 카탈로그에 자동 등록하지 않는다.

## Agent와의 연결

역할별 선언·독립 프롬프트는 ../agent_builders/에 있다. 현재 Agent의 탐색은 ../planning/catalog.py의 AssetCatalog를 사용한다. ../tools/catalog.py는 기존 1.3 Workflow 관리·컴파일 지원이다. 이 패키지의 tools/는 Executor 실행 코드 생성용 소스다. 모든 Tool을 LLM에 자동 제공하는 구조가 아니다.

새 Workflow는 agent_service/agents/analysis/workflow/{skills,tools}/... 경로를 기록한다. 기존 app/workflow/{skills,tools}/...와 중간 리팩토링의 analysis/resources/{skills,executor_tools}/... 경로도 같은 파일로 연결한다. 자산 사본이나 과거 Python import 패키지를 별도로 유지하지 않는다. 외부 개발 스크립트에서 app.workflow를 import했다면 새 패키지 경로로 수정해야 한다.

경로 호환은 과거 Tool 버전 전체의 보존을 뜻하지 않는다. 이미 제출된 Executor payload를 새 경로로 재생성하지 않는다. 생성 산출물의 공유 PV 경로는 이번 작업에서 바꾸지 않았다. workflows/workflow_lifecycle.md는 기존 정책 문서를 보존한 것으로 모든 단계의 구현 완료를 뜻하지 않는다.

054에서 외부의 이전 설문형 그래프·노드를 제거했지만 이 패키지의 skills/tools/workflows 자산과 기존 Workflow 관리·컴파일 모듈은 보존했다. 새 승인 snapshot의 실제 코드 생성은 ../execution/compiler.py가 맡는다. 094에서 미사용 workflow_recommender.py placeholder는 삭제했다. 등록·색인·HNSW 검색은 api_service/workflows/, Agent의 검색 포트/추천 snapshot 해결은 ../planning/recommendations.py, 호출 범위는 agent_service/middleware/planning_contract.py에서 관리한다. Skill/Tool 자산 패키지는 그대로 유지한다.

사용자에게 노출할 Tool 파라미터는 [파라미터 작성 가이드](../../../../../docs/tool-parameter-policy.md)를 따른다. tool_registry.yaml의 parameter_controls는 수동 정책이며 생성 시 보존·검증된다. 함수 기본값은 Agent 계획 검토에서 AST로 읽어 승인 snapshot에 고정한다.

091의 [Skill·Tool 자산 계약](../../../../../docs/agent-development/skill-tool-contract.md)은 한 파일의 여러 공개 함수, 고유 등록 ID, parameter_bindings, 자산 revision과 현재 2.0/레거시 1.3의 적용 범위를 설명한다. 공통 Agent는 이 디렉터리의 현재 예시 함수명으로 분기하지 않는다.
