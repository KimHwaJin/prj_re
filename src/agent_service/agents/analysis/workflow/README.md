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
  workflow_recommender.py  기존 추천 경계 (검색은 아직 placeholder)
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

054에서 외부의 이전 설문형 그래프·노드를 제거했지만 이 패키지의 skills/tools/workflows 자산과 기존 Workflow 관리·컴파일 모듈은 보존했다. 새 승인 snapshot의 실제 코드 생성은 ../execution/compiler.py가 맡는다. 검색 placeholder인 workflow_recommender.py가 현재 pgvector 추천을 구현한 것으로 해석하지 않는다.
