# Workflow 업무 자산

기존 작업자의 Skill·Tool·Workflow 문서 작업 영역을 유지하는 단일 자산 패키지다. 실행 엔진과 중복 카탈로그는 두지 않는다.

```text
agents/analysis/workflow/
  skills/       Skill MD·skill_index.yaml·generate_skill_index.py
  tools/        Executor 실행 함수·tool_registry.yaml·generate_tool_registry.py
  workflows/    workflow_lifecycle.md (업무 정책 문서)
  paths.py      설치된 자산 루트 탐색
```

## 유지보수

레포 루트에서 생성기를 실행한다. 생성기는 서비스 시작 시 자산을 다시 쓰지 않는다. `--skills-dir`/`--tools-dir`, `--output`으로 외부 자산 및 임시 출력 검증도 가능하다.

```sh
python src/dtest/agent_service/agents/analysis/workflow/skills/generate_skill_index.py
python src/dtest/agent_service/agents/analysis/workflow/tools/generate_tool_registry.py
```

등록된 함수는 필요한 import를 함수 내부에 포함한다. 실행 코드에는 docstring만 제거한 함수 원문을 제출하며, Tool을 Agent 프로세스에서 import하거나 실행하지 않는다. 현재 예시의 특정 함수명·데이터 유형에 맞춘 서비스 분기는 추가하지 않는다. `availability=test_only` 자산은 실제 계획에서 제외된다. 미등록 tmp 자산은 102에서 삭제했다.

## 현재 실행 코드와의 연결

- `../planning/catalog.py`: 유일한 AssetCatalog. 등록 자산의 AST·문서·정책·해시를 읽고 메타데이터 탐색 도구를 제공한다.
- `../execution/compiler.py`: 승인 snapshot의 등록 함수를 검증하여 Executor 제출 코드를 생성한다.
- `../agent_builders/<role>/`: 역할 선언 및 독립 프롬프트.
- `dtest/application/workflows`: 공개 Workflow CRUD와 색인 정책.
- `dtest/infrastructure/workflow_search`: 임베딩·pgvector HNSW 검색.
- `../planning/recommendations.py`: 검색 포트와 추천 snapshot 해결.

구형 Workflow1.3 컴파일러·중복 schemas/tools/catalog·이전 경로 alias는 102에서 제거했다. 현재 공개 Workflow2.0은 등록 ID와 검증된 참조로 자산을 연결한다. 이전 Python import 경로를 계속 지원하는 호환 패키지는 없다. 이미 제출된 Executor payload를 새로운 자산으로 재생성하지 않는다.

`workflows/workflow_lifecycle.md`는 기존 업무 정책 문서이며 현재 API 명세나 모든 기능의 구현 완료 선언이 아니다. 공개 규격과 유지보수 가이드는 레포 루트의 `docs/workflow-standard.md`, `docs/tool-parameter-policy.md`, `docs/agent-development/skill-tool-contract.md`를 따른다.
