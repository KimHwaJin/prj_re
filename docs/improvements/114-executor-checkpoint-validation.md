# 114 — Executor 제출과 영속 체크포인트 설정 정합성

## 기준과 상태

- 기준: feature/refactor-base, cd86b48.
- 작업: feature/executor-checkpoint-validation.
- 구현 커밋: bc9ee9e (설정 검증·서비스 조립·회귀·변경 기록).
- 상태: 구현·관련 검증 완료. 2026-10-08 베이스 통합·origin 게시. 서비스 미배포.
- 요청: 전체 리뷰의 다음 개선 작업 진행.

## 문제와 원인

서비스 Container는 postgres 체크포인트 분기 안에서만 ExecutorClient와
ApiWorkerBridge를 생성했다. GRAPH_CHECKPOINTER=memory와
EXECUTOR_SUBMIT_ENABLED=true를 함께 설정해도 시작 단계에서 거절하지 않았다.
Executor 의존성이 없는 그래프가 만들어져 설정 의도와 실행 기능이 달라졌다.

단순히 memory 분기에서도 Executor를 생성하면 해결되는 문제가 아니다.
Executor 작업은 수일 이상 걸릴 수 있으므로 서비스가 재시작되어도 승인과
실행 대기 상태를 복구할 영속 체크포인트가 필요하다. InMemorySaver는
프로세스 종료 시 상태를 잃어 이 서비스 실행 정책에 적합하지 않다.

## 변경과 정책

Agent 설정에 validate_executor_checkpoint 규칙 하나를 정의했다.
YAML > 환경변수 > 기본값 우선순위와 타입 변환을 적용한 최종 설정에 검증한다.
다음 조합으로 잘못된 설정이 들어오면 ConfigurationError를 발생시킨다.

    EXECUTOR_SUBMIT_ENABLED requires GRAPH_CHECKPOINTER=postgres
    for durable Executor resume

실제 오류 메시지는 한 줄이며 비밀값을 포함하지 않는다. 잘못된 YAML 값을
유효한 환경변수로 대체하거나 Executor 제출을 조용히 끄지 않는다.

| GRAPH_CHECKPOINTER | EXECUTOR_SUBMIT_ENABLED | 서비스 동작 |
| --- | --- | --- |
| memory | false | 인메모리 계획·승인 검토, Executor 제출 없음 |
| postgres | false | 영속 계획·승인 검토, Executor 제출 없음 |
| postgres | true | 영속 체크포인트와 Executor 실행 구성 |
| memory | true | 시작 전 설정 오류 |

검증 위치:

- load_settings: 설정 읽기와 --check-config에서 조합을 확인한다.
- configure: 직접 만든 ServiceSettings를 앱에 설치할 때도 확인한다.
- ServiceContainer.open_graph: 설정 로더를 우회한 조립 경로를 확인한다.

표준 launcher는 설정 오류 시 DB 초기화·앱 생성·백그라운드 Worker 시작 전에
중단한다. Container의 Executor/bridge 생성 조건은 체크포인트 선택 분기
밖으로 옮겨 각각의 책임이 코드에서도 구분되도록 했다. 기존 자원 공유와
AsyncExitStack의 역순 종료는 유지한다.

이 규칙은 서비스의 Executor 제출 정책이다. 개발 도구·단위 테스트에서
build_planning_graph를 직접 호출하고 InMemorySaver와 실행 double을 주입하는
경로는 유지한다. MODEL_PROVIDER=mock 여부와도 별개다. LLM이 mock이어도
서비스에서 Executor 제출을 켜면 postgres를 사용해야 한다.

변경 파일:

- [설정 규칙](../../src/dtest/settings/agent.py)
- [설정 로더와 설치](../../src/dtest/settings/loader.py)
- [서비스 조립](../../src/dtest/container.py)
- [회귀 테스트](../../tests/api_service/test_executor_checkpoint_configuration.py)
- [현재 설정 안내](../configuration-bootstrap.md)
- 환경별 YAML 예제 6개: 기존 값은 유지하고 조합 제한 주석을 추가했다.

## 검증

Python3.11 테스트 환경에서 관련 214개가 통과했다. 새 회귀는 18개다.

- local/dev/stg/prd 모두 잘못된 조합 거절.
- config·환경변수·명시적 dotenv 입력의 최종 타입 값 검증.
- YAML 우선순위 및 잘못된 YAML의 fallback 금지.
- launcher의 일반 시작과 --check-config가 앱 생성 전 거절.
- 정상 3조합의 실제 compiled graph에 execution_select 노드 존재 여부 확인.
- checkpoint·Executor·bridge 자원 생성 조건 및 역순 종료 확인.
- 직접 설치한 설정과 Container 조립 경로의 우회 설정 거절.
- 기존 bootstrap·설정·배포·그래프 수명·개발 도구·실행·repair·계획 수정 회귀.

실행 파일:

    tests/api_service/test_executor_checkpoint_configuration.py
    tests/api_service/test_bootstrap_settings.py
    tests/api_service/test_settings_models.py
    tests/api_service/test_yaml_configuration_files.py
    tests/api_service/test_deployment_configuration.py
    tests/api_service/test_graph_runtime_lifecycle.py
    tests/agent_service/test_devtools_runtime.py
    tests/agent_service/test_agentic_execution.py
    tests/agent_service/test_agentic_repair.py
    tests/agent_service/test_plan_revision.py

PYTHONPATH=src와 pytest -p no:cacheprovider를 사용했다. 정상 postgres 조합의
자원 검증은 InMemorySaver 기반 checkpoint double과 Executor/bridge double을
사용한다. 실제 PostgreSQL·Redis·LLM·Executor 호출이나 부하 측정은 하지 않았다.
기존 Agent 테스트의 checkpointer 없는 durability 경고 6개는 남아 있다.

uv run --locked --no-sync로 Ruff0.16.10·ty0.0.84 전체 검사를 실행했다.
전체 Ruff format --check는 800개 파일 통과. ty는 기존 759개 진단과 동일하고
새 진단이 없다. Ruff는 기존 3076개에서 3075개로 1개 감소했고 새 진단이 없다.
전체 lint/type 통과로 표현하지 않는다. git diff --check도 통과했다.

## 다음 작업

DB 연결 옵션과 Executor 데이터 경로의 하드코딩을 정리한다. CRUD 연결과
LISTEN 연결의 SSL 정책을 함께 검토하고, 노드 안의 고정 Jupyter 데이터 경로를
기존 경로 계산·설정 경계로 옮긴다. 데이터 등록 API 실연계나 모델 호출 수
최적화의 보류 결정은 변경하지 않는다.

## 통합 기록 — 2026-10-08

사용자 요청에 따라 후속115와 함께 feature/refactor-base에 통합한다.
feature/executor-checkpoint-validation의 구현 이력도 origin에 보존한다.
실제 서비스 재기동·배포는 수행하지 않는다. [115 통합 기록](115-windows-postgres-driver-alignment.md)을 따른다.
