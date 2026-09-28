# 010 — 분석 Workflow 처리 코드·기존 자산 패키지 통합

- 날짜: 2026-09-28
- 브랜치: feature/refactor-unify-analysis-workflow
- 출발: feature/refactor-preserve-workflow-package / 8c3e0b6
- 상태: 구현·오프라인 검증 완료. 베이스 병합·원격 push·배포 미수행.
- 007~009를 포함한 파생 브랜치이며 feature/refactor-base에는 006까지 통합되어 있다.

## 문제와 확정 방향

사용자가 보존하려던 것은 기존 app/workflow의 skills·tools·workflows 구성과 유지보수 방식이었다. 009에서 이를 app/workflow 경로 고정으로 해석한 부분을 정정한다. 기존 Workflow 처리 모듈이 있는 src/agent_service/agents/analysis/workflow 아래로 자산 패키지를 통합한다.

## 실제 변경

- skills/·tools/·workflows/와 색인 생성기, 미등록 tmp 자산을 하위 구조 그대로 이동했다. 기존 컴파일러·Notebook 생성기 등 6개 처리 모듈은 같은 위치에 유지한다.
- workflow/paths.py가 설치 위치와 저장된 경로 호환을 담당한다. analysis/resource_paths.py는 이 모듈을 재노출한다. 분석이 app.workflow에 의존하던 import를 제거했다.
- 새 Workflow의 Skill/Tool 소스는 agent_service/agents/analysis/workflow/{skills,tools}/...를 기록한다. 기존 app/workflow/... 및 005~008의 analysis/resources/... 저장 경로는 같은 원본 파일로 해석한다. 경로 이탈 차단도 유지한다.
- 레지스트리 source_root, 생성기 메타데이터, 컴파일러·데이터 로드·카탈로그의 경로 참조, 패키지 배포 설정과 테스트 import를 갱신했다.
- 원본 24개 중 22개는 7d0cc53과 바이트까지 동일하다. 나머지 2개는 tools/generate_tool_registry.py와 tools/tool_registry.yaml이며 source_root 경로 치환 외 차이가 없다. 핵심 Skill/Tool 19개와 기존 수명주기 문서 내용을 그대로 보존했다.
- app/workflow 소스 사본이나 Python import 호환 패키지를 남기지 않는다. 외부 개발 스크립트가 app.workflow를 import한다면 새 Python 패키지 경로로 변경해야 한다. 저장된 Workflow의 source 문자열 호환과 Python import 호환은 구분한다.
- 현재 개발 가이드·구조 문서·유지보수 안내를 갱신했고 009에는 후속 정정을 명시했다. 역할별 독립 프롬프트, 그래프 노드·edge·state 계약, 공개 API, DB 및 PV 설정은 그대로다.

## 검증과 결과

[자산별 해시와 검증 요약](../reports/workflow-package-unification-2026-09-28.json).

1. 리소스·Mock 집중 테스트: **14 passed**. 새 경로 및 두 과거 접두사의 Skill/Tool 조회, 경로 이탈 차단, 과거 Tool 경로를 저장한 체크포인트의 승인·재개와 Notebook 생성 검증.
2. 전체 오프라인 회귀: **197 passed / 19 failed / 39 skipped**. 009 결과(193/19/39)에 경로 호환 매개변수 사례 4개를 추가했다. 실패 테스트 이름과 UUID/경로 접두사를 정규화한 오류 메시지는 009와 모두 동일하다. 신규 실패 없음.
3. 원래 구현이 없는 select_features/split_dataset 테스트 파일 2개의 기존 수집 오류는 동일하게 제외했다. 전체 테스트 정상화나 PostgreSQL 회귀 완료를 뜻하지 않는다.
4. 두 생성기를 새 파일 경로에서 직접 실행: **3 Skills / 8 Tools**, 임시 생성 결과와 배포 YAML 일치. tmp 제외 정책 유지.
5. 깨끗한 임시 build 디렉토리에서 wheel 생성 후 python -I로 설치 파일만 검증: 원본 자산 24개가 현재 소스와 모두 동일, 이전 app/workflow 및 resources 자산 사본 없음, 역할별 프롬프트 7개와 실제 builder 조립, OpenAPI 33개, Mock 승인·재개·Executor 요청 6 steps 통과.
6. 설치 검증의 첫 실행에서 macOS 임시 경로의 심볼릭 링크 표현 차이 때문에 SOURCE_ROOT 비교가 실패했다. 비교 대상에도 resolve()를 적용한 뒤 동일 wheel에서 통과했다. 런타임의 실제 자산 조회 오류는 아니었다.
7. git diff --check 통과. 외부 LLM·Executor·Redis·DB 호출, 원본 checkout 변경 및 실행 컨테이너 업데이트는 수행하지 않았다.

재실행 명령(프로젝트 의존성이 설치된 Python 3.11 사용):

```sh
PYTHONPATH=src python -m pytest \
  src/agent_service/agents/analysis/tests/test_resource_layout.py \
  src/agent_service/agents/analysis/tests/test_service_load_mock.py -q

PYTHONPATH=src python -m pytest src/app/test src/agent_service/agents/analysis/tests -q \
  --ignore=src/agent_service/agents/analysis/tests/test_select_features.py \
  --ignore=src/agent_service/agents/analysis/tests/test_split_dataset.py

python src/agent_service/agents/analysis/workflow/skills/generate_skill_index.py --output /tmp/skill-index.yaml
python src/agent_service/agents/analysis/workflow/tools/generate_tool_registry.py --output /tmp/tool-registry.yaml
python -I scripts/diagnostics/validate_agent_package.py /path/to/fresh-build.whl
```

## 남은 범위

[Workflow 작업 안내](../../src/agent_service/agents/analysis/workflow/README.md)를 기준으로 유지보수한다. 경로 호환은 과거 Tool 내용의 버전 보존이 아니며 운영 DB의 모든 장기 체크포인트를 검증한 것도 아니다. 이미 제출된 Executor payload를 재생성하지 않는다. create_agent 통일·미들웨어·project_memory 및 나머지 I/O 전환은 후속 작업이다.

## 후속: 이동 전 위치의 로컬 잔여물 정리

사용자의 추가 요청으로 같은 리팩토링 worktree에서 다음 잔여물을 삭제했다.

- 원본 Python 소스가 없는 오래된 bytecode 79개.
- 캐시 삭제 후 비어 있는 디렉토리 64개.
- 이전 패키지의 소스 사본이 포함된 로컬 build/ 산출물 254개.
- 이전 문서 경로의 안내 전용 파일 docs/agent-development/analysis/workflow_lifecycle.md. 정책 원문은 통합된 workflow/workflows/에 유지한다.

src/app/workflow, src/app/agents, src/app/graphs, analysis/resources, analysis/prompts는 실제 파일시스템에서도 더 이상 존재하지 않음을 확인했다. Git에서 추적하지 않는 캐시·빌드 산출물 삭제는 다른 checkout에 전파되지 않는다. 원본 checkout과 실행 중인 컨테이너는 변경하지 않았다.

현재 사용 중인 CLI 진입점, resource_paths 참조 모듈과 저장된 경로 resolver는 실행 코드이므로 유지한다. 이동 이력·검증 보고서도 보존한다. 소스 동작 변경이 없어 전체 테스트를 반복하지 않고 bytecode 생성을 끈 import·자산 경로 점검을 수행했다.
