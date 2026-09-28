# 005. 분석 Agent 패키지 집약과 리소스 경로 정리

| 항목 | 내용 |
|---|---|
| 상태 | 1차 구조 이동·검증 완료 / 전체 구조 이행·비동기 전환·배포 미완료 |
| 시작일 / 완료일 | 2026-09-28 / 2026-09-28 |
| 브랜치 | feature/refactor-agent-layout |
| 출발 commit | 7d0cc53 — 004를 feature/refactor-base에 fast-forward 반영 후 분기 |
| 구현 commit | 9fcdb8c — refactor: isolate analysis agent code and packaged resources |
| 배포·migration | 수행하지 않음. 기존 실행 컨테이너·서비스 DB 변경 없음 |

## 문제와 방향

분석 Agent 구현이 app/agents, app/graphs, app/services, app/workflow, app/schemas에 흩어져 있었다. Agent 개발자가 업무 코드·프롬프트·Tool을 수정할 때 서비스 인프라와 분석 전용 코드의 경계가 불명확했다. AGENT_REGISTRY는 업무 Agent 등록이 아닌 내부 구성요소 명세였고, 사용하지 않는 이전 Worker와 팩토리도 같은 소스 트리에 있었다.

Tool은 import되는 것만이 아니라 카탈로그 경로로 파일을 읽어 Notebook 코드에 포함되는 것도 있었다. 저장된 Workflow에는 기존 app/workflow/tools 경로가 남을 수 있으므로 단순 파일 이동만 하면 승인 후 재개 때 실패할 수 있었다. pyproject는 기존 app 패키지만 찾고 일부 루트 모듈 위치도 실제 src 배치와 달랐다.

사용자 승인 구조에 따라 분석 업무 패키지를 먼저 집약하고, 동작 변경이 큰 공통 실행 계약·비동기 I/O 작업은 이후 단계로 분리했다. [목표와 현재 구조](../architecture/service-layout.md), [개발 안내](../agent-development/README.md), [파일별 이동·삭제·잔여 의존성](../agent-development/analysis-layout-inventory.json)이 인계 기준이다.

## 변경

- 94개 기존 파일을 분석 패키지·공통 checkpoint factory·개발 도구·문서로 이동했다. 그래프/state/nodes, 내부 LLM components/prompts, Workflow 해석·컴파일·추천·코드 생성, 스키마, Skill/Tool 리소스와 업무 테스트를 함께 이동했다.
- API/Run 그래프와 Executor 이벤트 그래프의 import, LangGraph dev 설정, CLI, Mock Executor와 진단 도구의 참조를 갱신했다. 루트 app.py와 cli.py 진입점은 유지했다.
- 내부 AGENT_REGISTRY/AgentType을 COMPONENT_SPECS/ComponentType으로 바꿔 향후 업무 Agent registry와 구분했다. 새 Agent를 API로 선택할 수 있는 registry를 구현한 것은 아니다.
- 등록 Skill/Tool YAML은 resources/catalogs에, 문서는 resources/skills에, Executor 코드 소스는 resources/executor_tools에 배치했다. 생성기는 devtools.analysis로 옮겼다.
- resource_paths에서 설치 패키지 기준으로 경로를 찾는다. 새 경로와 저장된 옛 Tool 경로를 같은 배포 리소스로 해석하고 경로 이탈을 거절한다. 옛 Skill 문서 경로 별칭도 유지했다. 임의의 옛 Python import 전체를 재노출하는 호환 패키지는 만들지 않았다.
- demo_artifact_store는 실제 그래프에서 사용하므로 삭제하지 않고 analysis/artifacts.py로 이동했다. 기존 설정명/파일 쓰기 동작은 유지했다. ArtifactStore의 관리되는 비동기 파일 I/O는 아직 구현하지 않았다.
- 호출처가 없는 worker_past 11개 파일, 중복 팩토리 3개, 옛 package re-export 2개를 제거했다. 미등록 tmp Skill/Tool과 recommender 프롬프트는 활성 기능으로 승격하지 않고 남은 업무 정합성 항목으로 기록했다. 모든 미사용 코드 정리가 끝났다고 표시하지 않는다.
- setuptools를 실제 src 레이아웃에 맞추고 새 패키지·프롬프트·YAML을 포함했다. 설치 CLI/API 진입점을 실제 모듈로 연결하고 테스트 패키지는 wheel에서 제외했다. 의존성 추가·버전 변경은 없다.

## 검증

[검증 JSON](../reports/agent-layout-validation-2026-09-28.json)에 실패 목록과 조건을 기록했다.

| 검증 | 결과 |
|---|---|
| 이동 전 오프라인 전체 회귀 | 177 passed / 19 failed / 37 skipped (DB 미지정) |
| 이동 직후 동일 조건 회귀 | 177 passed / 19 failed / 37 skipped, 실패 동일 |
| 최종 격리 PostgreSQL 포함 회귀 | 219 passed / 19 failed, 신규 실패 0 |
| 추가 리소스/경로 검증 | 5 passed |
| 제외 없이 테스트 수집 | 기존 미구현 Tool 대상 2개 수집 오류만 동일 |
| graph/state AST 비교 | import를 제외한 graph 조립·state 정의 동일 |
| wheel 빌드 및 소스와 분리 실행 | 통과, 새 리소스 포함·옛 패키지/테스트 미포함 |
| 설치 상태의 API/Mock Agent | OpenAPI 33개 경로, 승인·resume 후 Executor 요청 6단계 생성 |
| 잠금파일 검증 | uv lock --check --offline 통과 |
| 루트 진입점 | cli.py --help, app.py --check-config 통과 |

추가 테스트는 카탈로그 재생성 결과 일치, 등록 Tool·프롬프트 파일 존재, 옛/새 Tool 경로 및 이탈 거절, 옛 Skill 별칭, 저장된 옛 Tool 경로를 가진 실제 분석 그래프의 승인 후 재개를 확인한다. 마지막 검증은 InMemorySaver에 승인 대기 상태를 보존한 뒤 graph 객체를 새로 만들어 수행했다. 모든 과거 PostgreSQL checkpoint 또는 장기 Executor 작업의 배포 간 호환을 입증한 것은 아니다.

PostgreSQL 17-alpine의 폐기 가능한 localhost identity_test DB를 사용했고 검증 컨테이너 dtest-refactor-layout-test는 제거했다. 실제 PostgreSQL 회귀에는 기존 API/사용자/소유권/풀/HITL 시험이 포함된다. 외부 LLM·Executor·Redis 호출, 운영 부하테스트, 기존 컨테이너 재배포는 수행하지 않았다.

전체 회귀의 19개 실패는 기준 브랜치와 이름을 대조해 동일함을 확인했다. 카탈로그/Workflow 스키마/기존 fixture 기대값 등의 정합성 문제이며 이번 이동에서 숨기거나 제거하지 않았다. select_features와 split_dataset Tool 소스가 없는 기존 테스트 2개는 이전과 동일하게 전체 실행에서 명시적으로 제외했다. 따라서 전체 테스트가 정상이라고 보고하지 않는다.

```sh
# 반드시 폐기 가능한 localhost identity_test에만 설정한다.
export DTEST_IDENTITY_TEST_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:<port>/identity_test'
PYTHONPATH=src python -m pytest src/app/test src/agent_service/agents/analysis/tests -q \
  --ignore=src/agent_service/agents/analysis/tests/test_select_features.py \
  --ignore=src/agent_service/agents/analysis/tests/test_split_dataset.py

# 의존성이 설치된 환경에서 오프라인 wheel 검증
python -c 'from setuptools.build_meta import build_wheel; build_wheel("/tmp/dtest-wheel")'
python -I scripts/diagnostics/validate_agent_package.py /tmp/dtest-wheel/dtest_agent-0.1.0-py3-none-any.whl
uv lock --check --offline
```

## 남은 작업과 다음 우선순위

**동기 I/O의 취소 종료 추적은 해결되지 않았다.** 이전 격리 실험에서 그래프 asyncio Task는 취소돼도 LangGraph가 실행한 동기 노드 스레드는 살아 있고 후속 동작을 할 수 있음을 확인했다. 001의 Task 종료 확인과 004의 graph 차용 횟수가 이 스레드의 실제 종료까지 보장하지 않는다는 제한을 해당 기록에도 추가했다. 이번 패키지 이동으로 그 문제가 해결됐다고 해석하면 안 된다.

다음 작업은 새 위치에서 Agent/모델의 실제 ainvoke, 비동기 Executor HTTP·Workflow DB, 관리되는 ArtifactStore를 구현하고 잔존 동기 작업의 종료를 검증하는 것이다. 이 종료 경계를 검증한 다음 공통 API/이벤트 실행 접수·소유권 경로와 제한된 동시 실행을 진행한다.

공통 Agent definition/registry/projection, project system_prompt 매 호출 적용·project_memory·모델 선택 계약, API/execution/infrastructure의 최종 패키지 분리도 남아 있다. 분석 패키지의 app.services 및 app.agent_worker 참조 10개 파일은 이동 목록의 transitional_dependencies에 기록했다. Agent 개발자가 새 업무 패키지에 복제할 표준이 아니다.

기존 미등록 Tool/Skill과 실패 테스트의 지원 범위를 정리해야 전체 미사용 코드 정리·업무 회귀 정상화를 완료할 수 있다. 실제 Gaia 템플릿, 공유 PV, Kubernetes 배포 검증도 미수행이다.
