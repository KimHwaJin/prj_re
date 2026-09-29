# 016 — Agent 실행과 서비스 DB 트랜잭션 분리

상태: 구현·격리 PostgreSQL·전체 회귀·패키지 검증 완료. 브랜치 `feature/refactor-short-db-transactions`, 부모 `64ad96f` (015). 구현·검증 기록을 같은 작업 commit으로 보존하며 베이스 병합·배포는 미수행이다.

## 문제와 근거

Run 실행 준비의 TaskEventService.append는 commit 뒤 refresh(event)를 수행했다. 이 refresh는 SQLAlchemy의 새 읽기 트랜잭션을 시작한다. 이어 초기 프로젝트 설정 조회와 legacy resume 보완 조회도 실행에 넘긴 같은 세션을 사용했다. 따라서 DB 쿼리 이후 그래프/모델 대기까지 연결을 점유할 수 있었다. 세션 객체의 존재 자체가 문제라는 뜻은 아니다.

스트리밍도 같은 DB 세션을 모든 상태 투영에 재사용했다. 기존 CRUD 내부 refresh 또는 중복 조회가 남긴 읽기 트랜잭션은 다음 그래프 상태/느린 소비자를 기다리는 동안 유지될 수 있었다.

## 범위와 방향

- 시작 이벤트는 실행 준비 트랜잭션에서 저장하고, commit 후 refresh 없이 실행으로 넘어간다. 필요한 ID는 일반 값으로 복사한다.
- 그래프 경계 함수는 열린 DB 세션을 받지 않는다. 초기/legacy resume 컨텍스트는 짧은 전용 세션에서 읽고 닫은 뒤 그래프를 호출한다.
- 각 상태의 메시지/로그/Task 연결은 별도 짧은 세션에서 투영한다. 스트림은 세션을 닫은 뒤 yield/다음 상태 대기를 한다.
- 세션 close 완료를 반복 취소에도 관찰해 미완료 rollback을 버리지 않는다.
- 기존 실행 소유권, 같은 세션 입력 제한, 체크포인트 풀 수명, 사용자 HITL/Executor 대기 정책은 유지한다.
- 복구 기능은 관리자 API로 후속 구현한다. CLI는 추진하지 않는다.

## 검증 계획

격리된 로컬 identity_test PostgreSQL에서 pool_size=1, max_overflow=0, pool_timeout=0.3초로 검증한다. 먼저 열린 읽기 트랜잭션이 두 번째 연결 요청을 timeout시키는 대조 실험을 수행한다. 변경 경로에서는 서로 다른 세션의 Run 두 개가 모델 대기 중일 때 실제 CRUD·Run 상태 조회·취소 API를 실행한다. 초기 호출·HITL resume, legacy snapshot 보완, 스트리밍/느린 소비자, 투영 실패 rollback, 반복 취소 중 close를 확인한다.

검증 결과: 전체 **355 passed, 2 subtests passed, 0 skipped, 43 warnings, 82.80초**. 경고는 기존 checkpoint 없는 그래프의 durability 경고다. 새 PostgreSQL 회귀 12개와 기존 관련 검증을 묶은 집중 실행은 **27 passed, 35.99초**였다.

wheel을 새 임시 디렉터리에서 빌드하고 `python -I scripts/diagnostics/validate_agent_package.py <wheel>`로 소스 checkout 없이 확인했다. API 경로 33개, 역할별 프롬프트 7개, Mock 분석 실행 단계 6개를 검증했고 외부 LLM/Executor 호출은 없었다. 검증용 PostgreSQL 컨테이너/볼륨은 제거했다.

재현 명령: 전용 로컬 `identity_test` DB URL을 `DTEST_IDENTITY_TEST_DATABASE_URL`로 전달하고 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest src -q`를 실행한다. fixture는 해당 전용 DB 스키마를 초기화하므로 애플리케이션 DB를 사용하지 않는다. 새 집중 검증 파일은 `src/app/test/test_short_transactions_postgres.py`다.

[기계 판독용 검증 결과](../reports/short-db-transactions-validation-2026-09-29.json)에 조건·검증 목록·제한을 기록했다.

## 실제 변경 경계

- `src/app/services/run_service.py`: 시작 이벤트를 `commit=False`로 작성한 뒤 실행 준비를 명시적으로 commit한다. 프로젝트/Task/checkpoint/trigger ID는 그 전에 일반 값으로 보존한다. 그래프에 `db`를 전달하지 않는다.
- `src/app/services/agent_project_context.py`: `read_project_snapshot`이 짧은 세션을 소유한다. legacy checkpoint의 `aupdate_state`보다 먼저 조회 세션을 닫는다. Executor 이벤트의 기존 조회 경로도 같은 helper를 사용한다.
- `src/app/services/agent_graph_service.py`: 초기·resume·stream 호출은 일반 실행 값과 선택적 `session_factory`를 받는다. 기본 factory는 기존 프로세스 공용 풀을 사용한다.
- `src/app/services/graph_crud_persistence.py`: 상태별 Task 연결·메시지·로그 저장은 `_persist_state`가 소유한 짧은 세션에서 수행한다. 마지막 관계 변경을 commit하고 세션을 닫은 뒤 반환/yield한다.
- `src/app/core/database.py`: `short_session`은 실패 시 남은 트랜잭션을 close로 rollback하며, 반복 취소에도 close 작업의 종료를 관찰한다.
- `scripts/diagnostics/checkpoint_coexistence.py`: 내부 함수의 제거된 positional db 인자만 정리했다. 과거 진단 스크립트 전체 재실행/현행화는 이번 검증에 포함하지 않았다.

Worker/RunService의 coordinator 세션 객체는 남아 있어도 실행 준비 commit 이후 그래프 대기 중에는 트랜잭션/연결을 소유하지 않는다. 같은 객체를 유지하는 것과 연결을 계속 점유하는 것을 구분한다. 최종 상태 저장에서는 다시 Run→Task 순서로 잠그고 소유권·취소 상태를 읽는다.

이번 변경은 전체 이벤트를 하나의 원자적 transaction으로 바꾸지 않는다. 기존 메시지/로그 handler의 개별 commit 및 멱등 저장 정책은 유지한다. 일부 결과 저장 후 장애가 발생했을 때의 재처리·checkpoint/CRUD 간 원자성은 별도 문제다. 전용 저장 세션을 닫기 전 최종 commit으로 기존 handler가 남긴 관계 변경이 유실되지 않게 한다.

## 동시성 테스트 보완

최초 전체 실행은 351 통과 / 1 실패였다. 기존 100ms 고정 대기 benchmark가 `peak == slots`를 요구했는데, 4자리 설정에서 관측 peak가 2였다. 20개 Run은 모두 정상 완료했다. DB 점유/실행 준비 속도에 따라 100ms 호출이 다음 자리 점유 전에 끝날 수 있으므로 해당 조건은 동시성 지원 여부를 판정하는 안정적인 근거가 아니다.

benchmark는 완료 수와 상한 초과 여부를 검사하고, 별도 제어 가능한 대기 테스트로 1·2·4자리가 실제로 모두 채워지는지 확인한다. 동일한 연결 1개 풀에서 슬롯 수만큼 RUNNING, 추가 1건은 PENDING인지 확인하고, 중간 CRUD 및 정상 stop/drain도 검증한다. 고정 시간 benchmark 수치를 개선 성과로 사용하지 않는다.

## 남은 제한

- 운영 DB 풀 크기·전역 연결 상한·레플리카 수는 변경하지 않았다. 연결 1개로 운영하라는 권장도 아니다. DB 쿼리 자체가 느리거나 동시 요청이 더 많으면 연결 대기는 여전히 발생할 수 있다.
- 체크포인터/Executor bridge는 별도 기존 풀을 유지한다. 이번 작은 풀 시나리오의 그래프는 InMemorySaver와 모델 대기를 제어하는 LangGraph 노드를 사용한다. 실제 외부 LLM/Executor 및 Kubernetes 부하는 검증하지 않았다. 전체 회귀에는 기존 실제 PostgreSQL checkpointer 테스트도 포함한다.
- 그래프 대기 경계의 연결 점유를 제거한 것이며 서비스 전체 CRUD의 SQL 최적화·파일 저장 방식까지 변경한 것은 아니다.
- SSE endpoint를 새로 만들거나 공개 HTTP API 명세를 바꾸지 않았다. 내부 graph streaming helper의 DB 수명을 정리했다.
- 장기 Executor/HITL 대기 중 같은 세션의 입력 제한, 불확실 종료 시 소유권 보존, 수동 복구 필요 정책을 유지한다. 운영 복구 관리자 API는 후속 작업이다.
- 새 환경변수/DB migration은 없다. 기존 로컬 서비스·컨테이너 설정, 원래 checkout은 수정하지 않았다. 베이스 병합·push·배포는 미수행이다.
