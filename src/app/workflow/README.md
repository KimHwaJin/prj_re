# Workflow 업무 자산 — 기존 유지보수 위치

이 패키지는 기존 작업자가 계속 편집하는 정식 소스 위치다. Agent 리팩토링을 이유로 다른 패키지로 옮기거나 복사본을 관리하지 않는다.

```text
app/workflow/
  skills/                 Skill 문서와 skill_index.yaml
    generate_skill_index.py
  tools/                  Executor용 Tool Python 소스와 tool_registry.yaml
    generate_tool_registry.py
  workflows/              Workflow 관련 자산·수명주기 정책
    workflow_lifecycle.md
  paths.py                설치 위치 탐색·저장된 경로 호환
```

## 기존 작업 방법

Skill/Tool은 이 디렉터리에서 수정하고 기존 생성 스크립트로 색인을 갱신한다. 레포 루트에서:

```sh
python src/app/workflow/skills/generate_skill_index.py
python src/app/workflow/tools/generate_tool_registry.py
```

`--skills-dir`/`--tools-dir`, `--output` 인자도 그대로 지원한다. 검증할 때는 --output을 임시 경로로 지정하면 원본 YAML을 덮어쓰지 않는다. 기존 정책대로 tmp 아래 문서·소스는 보존하지만 색인에 자동 등록하지 않는다.

## Agent와의 연결

역할별 Agent 선언·프롬프트는 agent_service/agents/analysis/agent_builders에 있다. Agent의 공용 카탈로그 도구와 컴파일러는 이 패키지를 읽는다. 여기의 tools는 Executor 코드 생성용 소스이며 LLM에 자동 제공하는 LangChain 도구 목록이 아니다.

새 Workflow는 기존 app/workflow/skills/... 및 app/workflow/tools/... 경로를 기록한다. 중간 리팩토링에서 기록한 agent_service/agents/analysis/resources/skills/... 및 resources/executor_tools/... 경로도 paths.py와 Agent 카탈로그 resolver가 이 패키지로 연결한다. 같은 자산을 두 경로에 복사하지 않는다.

경로 호환은 과거 Tool 버전 전체의 보존이나 이미 제출된 Executor payload의 재생성을 뜻하지 않는다. 생성된 실행 산출물은 기존 설정의 공유 PV에 저장한다. workflow_lifecycle.md는 원래 정책 문서를 보존한 것이며 문서의 모든 단계가 구현되었다는 뜻은 아니다.
