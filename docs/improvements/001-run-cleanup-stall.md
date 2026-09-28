# 001. 취소 감시 정리 중 Run 실행기 정체

| 항목 | 내용 |
|---|---|
| 상태 | 구현·격리 PostgreSQL 검증 완료 / 배포 미수행 |
| 시작일 / 완료일 | 2026-09-28 / 2026-09-28 |
| 브랜치 | feature/refactor-run-cleanup |
| 출발 commit | eaab388 — 003 완료 후 feature/refactor-base에 통합 |
| 구현 commit | 7365a66 — fix: supervise run cleanup and quarantine uncertain execution ownership |
| 배포 상태 | 미배포. 기존 서버·컨테이너·DB 변경 없음 |

**문제와 확인 근거**

그래프가 답변을 만든 뒤에도 취소 감시 coroutine의 종료를 기다리느라 Run 실행기가 다음 작업으로 넘어가지 못했다. HTTP나 heartbeat가 살아 있다는 사실만으로 실행기의 정상 처리를 판단할 수 없었다.

기존 코드는 정상 완료에도 watcher에 `cancel()`을 보낸 후 기한 없이 기다렸다. 현재 설치된 Python 3.11.15·SQLAlchemy 연결 풀 queue로 이 스케줄을 만든 진단에서 수정 전 **20/20 정체**, 한 번 양보하는 대조군은 **0/20 정체**였다. 실제 장애 당시 라이브러리 내부 취소 소실 순간을 직접 관측한 것은 아니며, 이번 수정이 라이브러리 자체를 고쳤다는 의미는 아니다.

검토 중 추가 확인한 문제:

- watcher가 DB 오류로 끝나도 사용자 취소로 처리할 수 있었다.
- heartbeat가 lease를 잃으면 조용히 종료했고 그래프는 계속 실행할 수 있었다.
- token writer의 마지막 flush와 heartbeat 종료에도 기한 없는 대기가 있었다.
- Reconciler가 lease 만료만으로 실행을 재대기/종료했다. 이전 그래프의 종료나 checkpoint 쓰기 권한 박탈을 확인하지 않아 재실행과 겹칠 수 있었다.
- background 종료 기한을 넘겨도 공용 자원 close가 실행됐다.

근거: [기존 정체 분석](../reports/run-delay-diagnosis-2026-09-28.md), [RunService](../../src/app/services/run_service.py), [경합 진단](../../scripts/diagnostics/reproduce_cancel_watcher_race.py).

**실제 변경**

1. 정상 완료 시 watcher·heartbeat에 `asyncio.Event` 중지 신호를 전달한다. DB 조회/연결 반환을 마친 뒤 루프가 종료하며 정상 경로에서 매번 task를 취소하지 않는다. 사용자 취소는 그래프에만 전달하고 실제 종료를 기다린다.
2. watcher·heartbeat DB 작업에는 감시 기한을 적용한다. heartbeat와 token consumer를 그래프와 함께 관찰해 조기 종료/오류를 놓치지 않는다. 감시 오류는 사용자 취소 및 일반 그래프 오류 재시도와 구분한다.
3. [공통 종료 처리](../../src/app/core/execution_lifecycle.py)는 종료 기한 경과를 즉시 기록하고 취소 후 한 차례 더 관찰한다. 반복적인 부모 취소에도 하위 정리를 보호한다. 그래프/감시 작업이 실제로 끝나기 전에는 실행 자원 소유권을 반환하지 않는다. 마지막 token flush가 실패/초과한 실행을 정상 완료로 확정하지 않는다.
4. `tasks.recovery_required`를 [0019 마이그레이션](../../crud_migrations/versions/20260928_0019_task_recovery_guard.py)으로 추가했다. 불확실한 실행은 기존 RUNNING 상태와 세션 잠금을 유지하고 `agent_runs.failure.code=RUN_RECOVERY_REQUIRED`, `retry_scheduled=false`로 표시한다. 최초 원인을 후속 정리 오류로 덮어쓰지 않는다. Task GET에도 `recovery_required`를 노출한다.
5. 복구 대상의 heartbeat 연장·새 점유·상태 전환·늦은 완료를 거절한다. Task 취소로 강제로 세션 잠금을 푸는 것도 거절한다. Run 취소 요청 자체는 기록할 수 있지만 복구 대상을 해제하지 않는다.
6. Reconciler는 만료된 RUNNING lease를 복구 필요로 표시한다. 자동 재대기/terminal 처리는 제거했다. PENDING의 큐 대기 시간은 실행 lease 만료로 취급하지 않는다. 실행 Run이 없는 과거 고아 Task도 잠금을 보존한다. 점유·완료·복구·Task 취소의 잠금 순서를 Run → Task로 맞췄다.
7. 해당 프로세스의 실행 건전성 실패를 고정하고 이후 `claim_one()`을 거절한다. `/service/ready`, 새 `/service/live`는 503을 반환한다. background 종료가 확인되지 않으면 서비스가 관리하는 공용 풀을 닫지 않는다. 복구 기록 task도 종료 관찰 대상에 포함한다.

일반 그래프 예외에서 하위 실행 종료가 확인되고 복구 플래그가 없으면 기존 재시도 정책은 유지한다. 모든 오류의 자동 재시도를 삭제한 것은 아니다.

**설정 및 관찰 방법**

```yaml
service:
  runtime:
    run_cleanup_timeout_seconds: 5.0
    run_monitor_timeout_seconds: 3.0
```

중앙 설정의 기존 우선순위를 따른다. 환경변수 이름은 각각 `RUN_CLEANUP_TIMEOUT_SECONDS`, `RUN_MONITOR_TIMEOUT_SECONDS`다. 양의 유한값만 허용한다.

- cleanup: 정상 stop 또는 그래프 cancel 뒤 종료를 기다리는 기한. 초과 즉시 복구 표시/건전성 실패, 추가 cancel 뒤 같은 기한으로 다시 관찰한다.
- monitor: 취소 요청 조회와 heartbeat DB 작업 한 차례의 기한. LLM 추론 시간/전체 Run 시간/Executor 실행 기한이 아니다.
- 기본값은 성능 시험으로 확정한 운영값이 아니다. 실제 DB 풀 대기·질의 지연을 고려해 검증해야 한다.
- Run GET의 failure 코드, Task GET의 복구 플래그·failure_reason, `execution_requires_recovery` 로그로 확인한다. 기존 `/health`는 변경하지 않았으며 이 장애를 감지하지 않는다.

**검증 결과**

운영 자원과 분리한 PostgreSQL 17 컨테이너 한 개를 사용했다. API·SQLAlchemy·Alembic·접수·점유·감시·상태 저장은 실제 실행했다. 업무 그래프는 종료/실패/취소/HITL을 제어하는 로컬 대역으로 교체했으며 LLM·Executor·외부 Redis는 호출하지 않았다. 부하/처리량 측정 결과는 아니다.

| 검증 | 결과 |
|---|---|
| SQLAlchemy queue 종료 경합 진단 | 수정 전 20/20 정체 → 수정 후 0/20. 대조군 양쪽 0/20 |
| 실제 watcher + 실제 queue, 20회 반복 | 종료 확인, watcher 취소 횟수 0 |
| 종료·반복 취소·감시 오류·lease 상실·flush 오류·건전성·설정 | 새 오프라인 테스트 18개 통과 |
| API 접수→점유→완료, 실제 취소 API, HITL 재개, 복구 격리·동시 Reconciler | 새 PostgreSQL 테스트 11개 통과 |
| 기존 설정·사용자 식별·PostgreSQL 역할/동시성 포함 집중 검증 | 101개 통과 |
| 전체 회귀 | 194 passed / 19 failed. 003 결과 XML과 실패 이름 대조: 신규 실패 0, 기존 실패 19개 동일 |
| CRUD migration | 격리 DB에서 0017→head→0017→head 및 실제 ORM/API 사용 통과 |
| 기존 환경 배포 / 실제 LLM·Executor / 100명 부하 / Kubernetes 재시작 | 미수행 |

[검증 요약 JSON](../reports/run-cleanup-validation-2026-09-28.json)에 조건·경합 횟수·전체 기존 실패 목록을 남겼다. 테스트용 PostgreSQL 컨테이너는 검증 뒤 제거했다.

검증 중 Run과 Task를 두 SQL로 읽던 테스트가 복구 commit 전후의 값을 섞어 읽을 수 있었다. 상태 검증 helper를 한 JOIN SELECT로 변경해 같은 statement snapshot을 검사한다. 애플리케이션의 복구 플래그와 Run failure는 같은 transaction에서 기록한다.

재실행 방법(폐기 가능한 localhost의 **identity_test** DB 전용; fixture가 public schema를 초기화하므로 실제 서비스 DB 사용 금지):

```sh
PYTHONPATH=src python scripts/diagnostics/reproduce_cancel_watcher_race.py
export DTEST_IDENTITY_TEST_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:<port>/identity_test'
PYTHONPATH=src python -m pytest \
  src/app/test/test_run_cleanup.py src/app/test/test_run_cleanup_postgres.py \
  src/app/test/test_bootstrap_settings.py src/app/test/test_user_identity.py \
  src/app/test/test_user_identity_postgres.py -q
PYTHONPATH=src python -m pytest src/app/test -q --tb=short \
  --ignore=src/app/test/test_select_features.py \
  --ignore=src/app/test/test_split_dataset.py
```

두 수집 오류 파일의 제외 사유와 기존 실패는 [002](002-bootstrap-configuration.md), [003](003-user-identity.md)에 기록되어 있다. 이 항목의 수정 성과로 기존 오류를 숨기지 않는다.

**운영 한계·후속 단계**

- **중간 안전장치다. 자동 복구까지 완성한 상태가 아니다.** Python에서 취소를 무시하는 coroutine을 강제로 죽이지 않는다. 두 관찰 기한 뒤에도 살아 있으면 소유자가 계속 기다리고 건전성은 실패 상태다. “모든 함수가 10초 내 반환”을 보장하지 않는다.
- 격리된 Run은 프로세스 재시작만으로 재실행되지 않는다. 이전 실행 종료·Executor 접수 여부·checkpoint 일관성을 확인하고 복구하는 절차와 도구는 후속 구현이다. flag만 임의로 지우는 복구 SQL/API는 제공하지 않는다.
- checkpoint에 실행 세대별 쓰기를 차단하는 fencing은 아직 없다. 그래서 lease 만료를 자동 재실행 근거로 쓰지 않는다. CRUD의 늦은 완료 거절을 checkpoint/외부 부작용의 exactly-once 보장으로 해석하면 안 된다.
- 현재는 감시/flush 실패에도 보수적으로 해당 프로세스의 새 실행 점유를 막는다. 일시적 DB 장애가 곧바로 처리량 감소로 이어질 수 있다. 공유 풀/실행 소유권 정비 후 실패 범위와 복구 정책을 더 세밀하게 나눠야 한다.
- DB 장애로 복구 표시 저장 자체가 실패할 수 있다. 이 경우 로컬 건전성·로그는 먼저 실패하고 Reconciler가 저장소 접근 회복 후 만료 lease를 표시한다. 저장 성공을 무조건 보장하지 않는다.
- `/service/live`를 실제 Kubernetes liveness probe에 연결하고 grace period와 프로세스 종료를 검증해야 한다. 현재 배포 probe는 바꾸지 않았다. 플랫폼이 소유한 별도 lifespan 자원의 종료 동작도 실제 Gaia에서 검증해야 한다.
- 배포 시 0019를 먼저 적용하고 이전 Reconciler/Worker가 함께 실행되지 않게 전환해야 한다. 이전 코드는 복구 플래그를 모르므로 혼합 버전의 자동 복구는 안전하지 않다. rollback도 실행을 멈추고 복구 대상 확인 후 수행해야 하며, flag 삭제가 실행 안전성 복구를 뜻하지 않는다.
- 다음 단계: 그래프·checkpoint 풀을 프로세스 수명으로 정리하고 실행 소유권/fencing 기반을 마련한 뒤 제한된 동시 슬롯을 도입한다. 이번에는 슬롯 수, 공개 Run ID 통합, WAITING_EXECUTOR, Redis 이벤트 경로, Agent 업무 로직을 변경하지 않았다.
