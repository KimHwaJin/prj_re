# 102. DTEST 서비스 구조와 책임 경계 정리

## 문제

API 패키지가 HTTP·DB·Worker·Agent 조립·SSO·추천까지 포함했고, 최상위에 설정/공용 패키지가 흩어져 있었다. API Settings에 DB 풀과 Worker 실행 옵션이 혼재했고, 라우터가 목록 SQL과 색인 정책을 직접 처리했다. 패키지 이동 후 과거 경로만 검사하는 테스트가 빈 디렉토리를 통과시키는 문제도 있었다.

## 변경

- 단일 `src/dtest` 아래 API·Agent·Worker를 분리했다.
- 업무 정책/Run 저장을 application, DTO/enum/port를 contracts, 외부 자원 구현을 infrastructure로 이동했다.
- container에 실제 Agent/Store/checkpoint/Executor 조립을 모았다. bootstrap은 프로세스 수명을 담당한다.
- 설정 source는 기존 YAML 우선순위를 유지하고 DB·Redis·명령 Worker·파일 저장 타입을 분리했다.
- 업무 계층에서 FastAPI 오류와 LangChain callback 상속을 제거했다. API 오류 adapter와 Agent callback adapter를 추가했다.
- 목록 조회/Workflow 색인 처리와 공용 pagination·identity·enum을 HTTP 패키지에서 분리했다.
- Agent 테스트를 루트 tests/agent_service로 이동했다. wheel은 dtest 패키지만 설치하며 새 위치의 정적 화면·JSON schema·프롬프트·Skill/Tool 자산을 포함한다.
- 특정 모델 서버·모델명·기본 DB 비밀번호를 서비스 기본값에서 제거했다.

## 이동 추적

| 기존 | 신규 |
| --- | --- |
| api_service/api | dtest/api_service/http |
| api_service/models, repositories | dtest/infrastructure/database |
| api_service/schemas | dtest/contracts/resources |
| api_service/resources, runs, workflows | dtest/application/resources, runs, workflows |
| api_service/workers | dtest/worker_service |
| api_service/infrastructure | dtest/infrastructure/database, memory |
| api_service/search, workflows file store | dtest/infrastructure/workflow_search, file_storage |
| agent_service | dtest/agent_service |
| service_contracts | dtest/contracts |
| integrations/executor | dtest/infrastructure/executor |
| service_auth | dtest/api_service/auth, infrastructure/sso, redis, contracts/auth |
| service_bootstrap.py | dtest/bootstrap.py |
| service_settings.py, config.py, agent_config.py | dtest/settings/loader.py, api.py, agent.py |
| event_worker_settings.py | dtest/settings/events.py |
| service_runtime | dtest/lifecycle.py, infrastructure/observability, contracts/model_selection.py |
| Agent 내부 tests | tests/agent_service |

## 검증과 범위

검증은 격리된 PostgreSQL17/pgvector0.8.6·Redis7로 진행했다. 전체 API907개 항목 실행에서894개가 통과했고, callback/조회 adapter 이동에 따라 실패한13개 항목은 주입 위치를 수정한 관련15개 재실행에서 모두 통과했다. Agent419개 전부 통과, 실제 브랜치의 Agent·구조·설정480개 통과, 추가 인프라 경계·binding 알림13개 통과를 확인했다. 최종 설치 wheel에서도 source checkout import 없이 API32개 경로·역할별 create_agent5개·프롬프트·Skill/Tool·JSON schema 로딩을 검증했다. 경고는 checkpointer 없는 내부 역할 Agent에 durability 옵션이 적용되지 않는다는 기존 LangGraph 안내다. 테스트 전용 컨테이너·볼륨은 삭제했다. 부하 성능의 개선 수치를 주장하는 작업이 아니라 유지보수·조립 경계 정리다. 외부 Frodo·실제 LLM·Executor에는 이 작업의 회귀 테스트를 제출하지 않는다. 데이터 스키마 변경·기존 DB 삭제도 수행하지 않는다.

사용자의 구체 삭제 승인 후 미사용 레거시 파일/전용 테스트/샘플의 물리 삭제를 완료했다. 현재 작업 브랜치는 feature/dtest-service-structure이며 별도 worktree에서 검증한다.

상세 구조와 개발 안내는 [서비스 구조](../architecture/service-layout.md)를 따른다.

## 서비스 설치와 Tool 검증 의존성

분석/학습 라이브러리 7개(pandas, pyarrow, scikit-learn, xgboost, scipy, matplotlib, seaborn)는 서버 필수 의존성에서 `tool-validation` 개발 그룹으로 옮겼다. `uv sync --locked --no-dev`로 설치하는 서비스는 이 그룹을 요구하지 않는다. 로컬 회귀/Tool 실행 검증을 위한 기본 dev 그룹에는 포함한다. 실제 Tool의 라이브러리는 Executor/Jupyter 커널에서 제공한다. HNSW 임베딩 처리에 쓰는 numpy는 서버 의존성에 남긴다. 버전 변경 없이 uv.lock을 함께 갱신했다.

## 승인 후 삭제 완료 범위

자동 승인 검토에서 요구한 범위 확인 후 사용자가 삭제를 승인했다. 아래 구형 소스·자산·테스트 총36개 파일을 삭제했다. 삭제 전 원본은87dad64와 별도 Git archive에 보존했다. 현재 테스트 두 파일은 새 AssetCatalog와 생성된 등록 목록을 검증하도록 정리하여 예시의 특정 이름·고정 개수에 대한 의존도 제거했다.

| 대상(현재 경로) | 정리 내용 |
| --- | --- |
| src/dtest/agent_service/agents/analysis/workflow/adaptive_workflow.py, data_load_steps.py, rule_based_notebook_generator.py, workflow_compiler.py, execution_notebook_reader.py | 현재 planning/graph와 execution/compiler가 사용하는 경로와 별개인 구형 컴파일·노트북·데이터 mock·결과 reader 제거 |
| src/dtest/agent_service/agents/analysis/tools, schemas | 위 구형 구현에 연결된 중복 catalog/schema와 규약 제거 |
| src/dtest/agent_service/agents/analysis/resource_paths.py | 정리 후 불필요해지는 이전 자산 경로 re-export 제거; 현재 workflow/paths를 직접 사용 |
| src/dtest/agent_service/agents/analysis/workflow/skills/eda/tmp, tools/data_io/tmp, tools/eda/tmp | 현재 레지스트리에 등록되지 않은 임시 자산 제거 |
| tests/agent_service/test_workflow_compiler.py, test_adaptive_notebook_prefix.py, test_execution_results.py, test_mock_data_io.py, test_workflow_condition_contract.py, catalog_fixtures.py | 폐기한 구현 전용 테스트·fixture 제거; 현재 Agent/등록 자산/공개 Workflow 표준 회귀 유지 |
| tests/agent_service/test_resource_layout.py | 폐기한 schema/catalog 전용 경로 검증만 정리; 현재 자산 생성기/배포 자산 검증 유지 |
| src/dtest/settings/agent.py, runtime.py, models.py | 해당 레거시에서만 참조하는 data_mock/demo/구 데이터 파일명 매핑·mock_data_root 설정 정리; 현재 명시적 MODEL_PROVIDER=mock은 유지 |
| src/routers | 서비스에 등록되지 않고 기존 import도 성립하지 않는 플랫폼 샘플 제거; 제공받는 실제 Gaia core는 대상 아님 |

등록된 workflow/skills, workflow/tools, workflow/workflows 패키지와 현재 execution/compiler·Agent 역할·공개 Workflow2.0 규격은 삭제 대상이 아니다. 삭제한 Python 모듈에 대한 현재 src/tests/설치 검증기의 참조를 모두 제거했다. 미사용 설정은 모델·로더·YAML 예시에서 제거했고, 현재 개발 문서와 wheel 검증기도 새 경로로 통일했다.

## 삭제 후 최종 검증

- Agent 전체377개 + 설정·계층 경계·프로세스 수명주기173개 =550개 통과. 이전419개 Agent 테스트에서 폐기한 구현 전용42개가 제거되었으며 현재 Agent 기능 회귀는 유지했다.
- EventWorkerSettings 명명 정리 후 관련 설정·계층 경계·이벤트 알림65개를 재실행하여 모두 통과했다. 위550개와 중복하므로 합산하지 않는다.
- 최종 소스로 새 wheel을 만들어 checkout import를 차단한 설치 검증을 통과했다. API32개 경로·create_agent 역할5개·현재 계획 승인·프롬프트·Skill/Tool·공개 JSON schema·demo 자산이 정상이며 구형 엔진·중복 schema/catalog·tmp·테스트가 wheel에 재포함되지 않았다.
- src/tests에 삭제된 모듈·구 설정·이전 경로 alias에 대한 Python 참조가 없고 git diff --check도 통과했다.

최종 구성은 src/dtest 하나다. 공개 Run/HITL/Executor 계약·DB 스키마 변경 없이 유지보수 책임 경계를 정리한 작업이며, 이 삭제 검증으로 새 부하 성능 개선 수치를 주장하지 않는다. 이번 삭제 후 검증은 외부 LLM/Executor 호출 없이 진행했다. 기존 실서비스 데이터와 원본 Workflow 표준1.0 자료를 수정하지 않았다.

사내 CICD의 pip 설치 목록 requirements.txt도 동일한 pyproject.toml·uv.lock에서 `uv export --offline --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt`로 재생성했다. Tool 검증 전용 ML/plot 라이브러리7개가 production 설치 목록에 남지 않도록 일치시켰다.
