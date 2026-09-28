# 002 — 기동·설정 기반 통합

상태: 로컬 기동·설정 기반 구현 완료 / 실제 Gaia 템플릿 통합 검증 대기

브랜치: feature/refactor-bootstrap-config (고아 기준: feature/refactor-base)
기록일: 2026-09-28  
구현/테스트: 아래 결과 참고 / 배포: 미수행

## 문제

변경 전 로컬 기동은 run.py → src/main.py이고, 설정은 src/config.py 및 Agent/Worker별 로더에 분산되어 있었다. 최종 Gaia 환경은 루트 app.py에서 시작하며 플랫폼이 앱을 조립한다. 이 차이를 그대로 둔 채 CRUD/실행기만 변경하면 뒤에서 기동·설정·자원 수명을 다시 맞춰야 한다.

최종 사용자 인터페이스·CRUD 범위·Run 식별·Project.system_prompt/project_memory·Workflow 승격 정책은 합의되었다. 첫 단계는 이 기능들이 같은 설정과 수명 관리 아래 실행될 기반을 만드는 것이다. 실제 Gaia 원본을 확인하지 않은 부분은 호환 검증 완료로 표시하지 않는다.

## 첫 구현 범위

1. 설정 소비 지점과 현재 적용값의 출처를 항목별로 매핑한다. 비밀 원문은 기록하지 않는다.
2. 선택된 YAML > 환경변수 > 기본값을 따르는 중앙 설정 모델/loader를 만든다. false/0 유지, 잘못된 값의 명시적 오류, 테스트용 입력 격리를 적용한다.
3. 루트 app.py의 얇은 진입점과 서비스 소유 bootstrap을 구성한다. 플랫폼 앱을 받는 결합 경계와 로컬 앱 조립 경계를 분리하되 같은 설정/공통 수명 관리 코드를 사용한다.
4. 앱/라우터 lifespan 합성에서 Worker·공유 자원이 한 번만 시작/종료되도록 구성한다. 플랫폼 core를 임의로 수정하지 않는다.
5. 기존 API·Agent·Worker에 설정 객체를 전달하는 경계를 순차 연결한다. 전환되지 않은 독립 로더가 있으면 명시하고 통합 완료로 표시하지 않는다.

이 묶음에서 모든 패키지 이동, 사용자/Run DB 마이그레이션, 실제 Executor 제출을 한꺼번에 수행하지 않는다. 기존 변경과 실행 모드를 보존하며 초기 검증은 가짜 자원/Worker로 수행한다.

## 완료 기준

- 같은 입력 설정이 API·Agent·Worker에서 동일하게 해석된다. 설정 우선순위·필수값·별칭 충돌을 재현 가능한 테스트로 검증한다.
- import만으로 DB/Redis/LLM/Executor 연결이나 Worker 실행이 발생하지 않는다.
- startup/정상 종료·부분 초기화 실패에서 자원을 중복 기동하거나 남기지 않는다.
- 루트 app.py의 로컬 기동을 확인한다. 실제 Gaia 앱 통합은 원본과 대상 버전 검증이 끝나야 별도 완료 표시한다.
- 기존 기능 호출의 기본 smoke 검증을 수행한다. 진단 중 실서비스의 외부 이벤트를 소비하지 않는다.
- 실제 변경 파일, 실행한 검사, 미검증 제한을 이 문서에 기록한다.

## 구현 결과 — 2026-09-28

구현 commit: `5df9b0e7976be6f1516822ab919db9802a904693` (`refactor: centralize service settings and application lifecycle`). 이전 기준: `745a112738a6a4d272af8a993ef320417e96955e`. 아래는 이 두 commit 사이의 변경만을 설명한다.

| 문제 | 변경 | 주요 파일 |
|---|---|---|
| API·Agent·이벤트 Worker가 각각 환경과 파일을 읽음 | 불변 설정 snapshot을 한 번 만들고 기존 소비자에 전달. YAML > 환경변수 > 기본값, 명시적 로컬 dotenv, 별칭 충돌/타입/범위 오류 처리 | service_settings.py, config.py, agent_config.py, event_worker_settings.py |
| API와 이벤트 resume가 서로 다른 checkpoint 설정 이름을 읽음 | 두 이름을 동일 설정의 별칭으로 해석; 충돌 시 시작 실패. 역할별 DB 분리는 유지 | checkpointer_factory.py, worker_main.py |
| 잘못된 Worker 설정을 다른 DB 설정으로 대체함 | 누락에 대해서만 문서화된 파생값 사용; 잘못된 명시값은 오류. Workflow 저장 해제도 명시하도록 변경 | agent_graph_service.py, api_bridge.py, workflow_persistence.py |
| Router와 main에 Worker·종료 책임이 분산 | root app.py → 공통 bootstrap. 기존 lifespan과 합성, 중복 부착 거부, loop 종료 감지, 제한 시간 종료, 모든 자원 cleanup 시도 | app.py, service_bootstrap.py, run.py, main.py, api/v1/router.py, core/database.py |
| 마이그레이션·진단이 런타임과 다른 값을 직접 읽을 수 있음 | 두 Alembic env 및 로컬 bootstrap/inspect와 Run 진단에 snapshot 적용 | migrations/env.py, crud_migrations/env.py, scripts/local/*, run_diagnostics.py |

파일 경로는 저장소 기준이며 `src` 아래 패키지의 기존 위치는 유지한다. 기존 Agent 노드의 업무 처리·Run 상태 전이·사용자/CRUD 계약은 이번 commit에서 교체하지 않았다. root app.py와 src/app 패키지 이름 충돌은 launcher 검색 순서를 지정하여 피했다.

실행법, DB 역할 표, 설정 이관 유의점, Gaia 결합 경계는 [기동·설정 가이드](../configuration-bootstrap.md)에 기록했다. 기본 dev YAML은 Worker를 끈 상태이며 실제 Agent 실행에는 YAML에서 활성화해야 한다. 기존 `.env` 및 로컬 비밀 설정은 새 worktree에 자동 복사하지 않았다.

## 수행한 검증

Python 3.11.15와 기존 checkout의 `.venv`를 사용하고 `PYTHONPATH=src`로 새 worktree 소스를 지정했다. 외부 DB/Redis/LLM/Executor를 연결하지 않았다.

1. 설정·기동 신규 테스트 **38개 통과**: 우선순위와 false/0, 별칭 충돌, 입력 격리, frozen snapshot, 비밀값 출력 방지, 실제 라우터 OpenAPI/health, 기존 lifespan 합성, 부분 startup 실패 정리, background 오류, 종료 기한, cleanup 실패 시 나머지 자원 정리, 로컬 마이그레이션 대상 검증.
2. 아래 기존 테스트 포함 회귀 검사: **131 passed / 19 failed**. 실패 19개는 고아 기준 commit을 임시 디렉토리에 `git archive`로 내보내 비교했으며 모두 기준에서도 실패했다. **새로운 실패 테스트 이름은 0개**다. 전체 green으로 보고하지 않는다.
3. 기준 검사: **92 passed / 20 failed**. 차이 중 38개는 신규 테스트다. 나머지 1개는 기존 mock 경로 테스트가 잘못된 `EXECUTOR_SHARED_INPUT_ROOT`로 설정하던 것을 실제 소비 설정 `MOCK_DATA_ROOT`의 snapshot 주입으로 바꾼 결과다. 제품 결함 1개를 추가로 해결한 것으로 계산하지 않는다. 진단 테스트는 매 테스트 snapshot 격리, LLM mock 테스트는 존재하지 않는 dotenv 파일 대신 명시적 mapping을 사용하도록 조정했다.
4. 전체 수집 시 기존 `test_select_features.py`, `test_split_dataset.py`에서 참조 모듈 자체가 없어 수집 오류 2개가 발생했다. 위 비교 실행에서는 두 파일을 명시적으로 제외했으며 테스트/제품 기능을 삭제하지 않았다.
5. 두 Alembic `upgrade head --sql` 완료: CRUD 17개 revision / Worker 2개 revision의 SQL을 오프라인으로 생성했다. 실제 DB 마이그레이션 성공을 의미하지 않는다.
6. 임시 loopback 포트에서 `python app.py --config <임시파일>` 실제 기동. Worker 3종 모두 비활성 상태에서 `/health`, `/service/ready`, `/openapi.json` HTTP 200 확인. SIGTERM 후 5초 안에 `Application shutdown complete` 확인. 설치된 Uvicorn은 정상 정리 후 SIGTERM을 재전달하므로 프로세스 returncode는 -15다. 처음의 exit 0 단정 검사는 이 동작 때문에 실패하여, Uvicorn 설치 소스를 확인한 뒤 종료 완료 로그와 반환 신호를 함께 검증했다.
7. 코드 commit의 `git diff --cached --check` 통과.

회귀 검사 재현 명령:

```sh
PYTHONPATH=src python -m pytest src/app/test -q --tb=no \
  --ignore=src/app/test/test_select_features.py \
  --ignore=src/app/test/test_split_dataset.py
```

남은 실패 범주는 기존 스킬 목록과 테스트의 불일치(data_cleaning_pipeline, eda_analysis, predictive_modeling, failure_analysis), WorkflowPlan 스키마/fixture 불일치, 조건 도구 검증 기대값, 제출 비활성 산출물 기대값이다. 이번 설정 변경의 성과로 고치거나 숨기지 않았으며 Agent 기능 정합성 작업에서 정리해야 한다.

## 제한과 미수행

- 실제 Gaia 템플릿이 없어 해당 플랫폼 기동 검증은 **대기**다. 이미 조립된 FastAPI를 받는 경계와 가짜 플랫폼 lifespan 합성까지만 검증했다. Gaia core 수정은 없다.
- 기존 Run 취소 감시 정체(001), Run 동시 실행 수/공유 풀/장기 Executor 대기 복구, API·Agent 패키지 분리, 사용자 및 Runs 계약 변경은 미수행이다. bootstrap의 종료 제한이 기존 Run 정체를 해결했다고 해석하지 않는다.
- `/service/ready`는 loop 생존 확인이며 외부 의존성 종합 readiness가 아니다. 종료 기한을 초과한 코루틴의 강제 정리까지 보장하지 않는다.
- 실제 DB 연결/DDL 적용, Docker 이미지 빌드·컨테이너 교체, Kubernetes 배포, 실제 Executor 제출 및 1~100명 부하테스트는 수행하지 않았다. 서버는 smoke 후 종료했다.
- 기존 Compose의 다중 Uvicorn 프로세스와 별도 이벤트 Worker 형태는 남아 있다. 최종 단일 프로세스 Pod/실행 슬롯 구조로 바꾸는 단계는 별도다.
- API DB의 기존 로컬 TLS/statement cache 옵션, wheel packaging 구조, worker_past 및 임시 도구의 독립 로더는 별도 정비 대상이다.
- X-User-Id/role, 안정적인 공개 run_id, system_prompt/project_memory 등의 합의는 설계 그대로 보존했고 아직 구현 완료가 아니다.

## 후속 순서

1. 사용자 기반: 문자열 공개 ID/내부 UUID, role, X-User-Id, /users/me, 관리자 등록·변경, 최초 관리자 초기화와 마이그레이션.
2. Runs/공통 실행기: 전체 작업에서 공개 run_id 유지, 메시지와 접수의 원자성, 세션 점유, 취소 정체(001), 제한된 동시 실행, 복구·장기 Executor 대기. Tasks 공개 기능 이관.
3. CRUD 정책: 미종료 작업에 대한 사용자/프로젝트/세션 삭제·이동 제한, 기본 프로젝트 보호, 단건 조회 비용 감소, Message CUD 공개 라우트 제거. 독립적인 조회 최적화는 앞 단계와 별도로 먼저 적용 가능.
4. Agent 연결: registry, main_model_name, 매 실행의 프로젝트 system_prompt 주입, project_memory 영속 공유/동시 갱신. Workflow 자산 승격은 누구나 가능하고 Executor 성공을 요구하지 않도록 적용.
5. 통합·부하 검증: 초기 호출→여러 HITL→Executor mock/실제 모드 경계→최종 완료, 재시작·재시도·동시 요청 검증. 1~100명 테스트와 전후 분석.

이는 의존성을 고려한 구현 순서다. 아직 수행하지 않은 후속 작업을 진행 중으로 등록하지 않는다. 코드 구조상 독립적인 작은 변경은 별도 묶음으로 완료·기록할 수 있다.
