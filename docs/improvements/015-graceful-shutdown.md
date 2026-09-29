# 015 — 새 점유 중단 후 실행 중인 호출을 마무리하는 종료

- 일자: 2026-09-29
- 브랜치: `feature/refactor-graceful-shutdown`
- 출발: 014 `feba2d2` (`feature/refactor-session-ownership`)
- 기준 브랜치: `feature/refactor-base`는 012 `fb89dbc`. 013~015의 베이스 병합은 수행하지 않았다.
- 상태: 구현·격리 PostgreSQL·실제 SIGTERM·패키지 검증 완료 / 원격 push·배포 미수행

## 문제와 변경

014는 실행 종료가 불확실할 때 PostgreSQL 소유권을 남겨 중복 실행을 방지한다. 그런데 기존 `BackgroundRuntime.stop()`은 정상적인 서버 종료에도 모든 background task를 즉시 cancel했다. API Run도 바로 취소되고, 이벤트 Worker의 기존 소비자 drain도 상위 취소 때문에 온전히 사용하지 못했다. 정상 재배포/축소가 불필요하게 복구 필요 세션을 만들 수 있는 구조였다.

이번에는 **정상 완료 유예 → 취소 및 정리 관찰**로 종료를 나눴다. 모든 Worker에 같은 stop event를 전달한다. 유예 시간은 Worker마다 누적하지 않고 서비스의 최초 종료 요청 시점을 기준으로 계산한다. 반복 종료 요청은 유예/정리 시계를 연장하지 않는다.

| 시점 | 동작 |
|---|---|
| 최초 종료 요청 | readiness를 false로 변경하고 API Run/Reconciler/이벤트 Worker에 stop 전달 |
| 정상 완료 유예 중 | 새로운 queue 점유/메시지 처리를 중단하고 이미 시작한 호출의 결과 저장·정리까지 대기 |
| 유예 시간 만료 | 남은 background 실행에 cancel 전달 |
| 취소 후 정리 중 | 소유한 자식 실행·감시·복구 기록 종료를 확인 |
| 정리 완료 | 그래프·DB 등 서비스 자원 종료 |
| 정리 시간도 초과 | 명시적인 shutdown 오류; 아직 사용 중인 서비스 풀을 먼저 닫지 않음 |

정상 완료 유예 중에는 기존 실행의 Task heartbeat와 공통 세션 소유권 감시를 계속 유지한다. HITL/Executor interrupt로 반환된 호출은 결과 저장과 정리 후 실행 자리를 반환한다. **장기 Executor 자체의 완료나 사용자의 다음 응답을 기다리지 않는다.** 다음 checkpoint가 저장됐다는 이유만으로 아직 실행 중인 코루틴의 소유권을 해제하지도 않는다.

정상 종료 중 이미 시작된 API queue 점유 transaction이 완료되면, 그 Run은 확보한 작업으로 보고 남은 유예 시간 안에서 실행한다. 강제 취소/유예 초과로 handoff가 불확실하면 기존 소유권 보존 정책을 따른다. 접수만 된 pending Run은 queue에 남아 다른 실행자가 처리할 수 있다.

## 설정

```yaml
service:
  runtime:
    shutdown_drain_seconds: 20
    shutdown_timeout_seconds: 25
```

- `SHUTDOWN_DRAIN_SECONDS`: 신규. 정상 호출 완료를 기다리는 시간. 기본 20초, 0 이상의 유한한 숫자. 0은 즉시 취소 단계로 이동한다.
- `SHUTDOWN_TIMEOUT_SECONDS`: 기존. 취소 이후 background 정리·복구 기록 종료를 관찰하는 시간. 기본 25초. 이번 루트 서버 launcher에서는 HTTP 연결 종료 대기 한도에도 같은 값을 사용한다.
- 기존 `RUN_CLEANUP_TIMEOUT_SECONDS`/`RUN_MONITOR_TIMEOUT_SECONDS`: 개별 실행 종료 관찰/DB 감시 한도. 계속 적용한다.
- 설정 우선순위는 명시 YAML > env > 기본값이다. YAML에서 키를 생략해야 환경변수로 바꿀 수 있다. `--check-config`에 두 종료 설정을 안전하게 표시한다.

기본값 20/25는 운영 최적값을 실측해 확정한 것이 아니다. 플랫폼이 주는 Pod 종료 시간 안에 **유예 + 취소 정리 + 자원 종료 여유**가 들어가야 한다. HTTP 연결 종료와 background drain은 루트 launcher에서 동시에 시작하지만, 플랫폼/진입점에 따라 시작 시점이 다를 수 있다. 저장된 배포 YAML의 숫자만으로 실제 폐쇄망 플랫폼 종료 예산을 확정하지 않는다. 이번에는 배포 YAML·기존 Docker 설정·실행 중인 컨테이너를 변경하지 않았다.

## 진입점과 플랫폼 경계

루트 `python app.py`/동일 bootstrap을 사용하는 `run.py`는 `build_server()`의 Uvicorn Server를 사용한다. 기존 Uvicorn 신호 처리를 유지하면서 SIGTERM/SIGINT를 받을 때 `app.state.service_runtime.request_stop()`을 먼저 호출한다. 따라서 HTTP 연결이 종료되기를 기다리는 동안에도 Worker가 새 Run을 계속 점유하지 않는다. 서비스 자원은 기존 FastAPI lifespan 종료에서 닫는다.

별도 `uvicorn main:app` 명령으로 직접 기동하면 이 신호 hook을 사용하지 않는다. 합성 lifespan에서의 정상 drain은 적용되지만, HTTP drain보다 먼저 Worker를 멈추는 동작까지 보장하지 않는다. 기존 Compose의 명시적 다중 Uvicorn 프로세스 명령을 이번에 변경하지 않았으므로 현재 실행 환경에 자동 적용된 것으로 보면 안 된다.

Gaia 플랫폼 원본 `core.py`는 수정하지 않았다. 외부 플랫폼에서 이미 만들어진 앱을 쓰는 경우 기존 초기화와 lifespan을 보존한 뒤 `build_server(app, settings)` 경로를 사용하거나, 플랫폼이 제공하는 종료 hook에서 `app.state.service_runtime.request_stop()`을 호출해야 같은 조기 중단 동작을 얻는다. 신호 handler를 무조건 덮어쓰는 전역 hook을 추가하지 않았다. 실제 Gaia 템플릿 원본과의 연계 시험은 여전히 미완료다.

readiness false는 모든 HTTP 쓰기를 503으로 바꾸는 기능이 아니다. 종료 직전에 접수된 Run은 durable queue에 남을 수 있다. 공개 drain HTTP API나 인증 없는 preStop endpoint는 추가하지 않았다.

## 이벤트 경로

임베디드 이벤트 Worker는 서비스 stop event를 받아 즉시 소비 중단을 요청하고 같은 서비스 유예 시간 안에서 현재 handler를 마무리한다. `EW_SHUTDOWN_SECONDS`로 추가 유예 시간을 중복해서 기다리지 않는다. 정상적으로 끝난 handler의 ACK는 유지하고 이후 새 메시지 조회/처리를 중단한다. 조회가 이미 진행 중이었다면 도착한 메시지를 새 handler로 넘기지 않고 기존 Redis 재전달 경로에 남긴다.

독립 `python -m app.agent_worker.worker_main`은 기존 자체 신호 처리와 `EW_SHUTDOWN_SECONDS` 유예를 유지한다. 이 경로까지 중앙 서비스 종료 설정으로 전환한 것은 아니다.

소비자·handler의 finally 정리는 반복 취소로 중단되지 않도록 보호했다. 유예 초과로 graph가 취소되면 014의 공통 소유권 격리를 유지하며, 작업이 계속 살아 있는데 Redis/DB 풀을 먼저 닫지 않는다. Redis 전달 방식·stream/group은 변경하지 않았다.

## 검증

새 테스트는 다음 파일에 기록했다.

- `src/app/test/test_graceful_shutdown.py`: 서비스 종료 시계, 반복 stop/cancel, 유예 초과, 설정 검증, Redis 소비자/이벤트 runtime drain, 실제 Uvicorn 자식 프로세스 SIGTERM.
- `src/app/test/test_graceful_shutdown_postgres.py`: 실제 queue/Run/Task/공통 소유권을 사용하는 PostgreSQL 검증.

실제 서버 자식 프로세스에는 SIGTERM 1회와 연속 2회를 보내 `started → draining → finished → closed` 순서를 확인했다. 서버가 실제로 시작된 뒤 신호를 보내며 임시 포트만 사용한다. 처음에는 테스트에서 설정상 금지된 포트 0을 넣어 기동 전에 실패했다. 서비스 포트 검증은 유지하고 테스트 서버 객체의 bind 포트만 0으로 지정해 수정했다.

PostgreSQL 검증에서는 3개 Run 중 2개가 실행 중일 때 drain을 시작했다. 성공·사용자 HITL·Executor 대기 각각에서 실행 중인 두 Run은 정상 반환하고, 세 번째 Run은 점유하지 않은 pending 상태를 유지하는지 확인했다. 정상 호출의 세션 토큰은 반환되고 recovery_required는 false였다. 유예를 넘긴 무기한 대기 호출은 취소되어 토큰/복구 필요 상태가 보존되는 것을 확인했다. 점유 commit 이후 handoff 중에 stop이 들어오는 경우도 유예 시간 안에서 정상 완료했다.

전체 회귀 결과는 **343 passed, 2 subtests passed, 0 skipped**, 67.85초다. 43개 경고는 기존 LangGraph의 checkpointer 없는 내부 Agent durability 경고다. 신규 종료 검증 18개를 포함하며, 이전 DB 의존 테스트도 모두 실행했다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
DTEST_IDENTITY_TEST_DATABASE_URL=<격리된 로컬 identity_test DB URL> \
python -m pytest src/app/test src/agent_service/agents/analysis/tests -q
```

checkout 밖에서 wheel을 새로 만들고 `python -I scripts/diagnostics/validate_agent_package.py <wheel>`로 격리 검증했다. OpenAPI 33개 경로, 7개 역할 Agent/프롬프트, mock graph 6개 실행 step 및 workflow 자산을 확인했다. [검증 요약 JSON](../reports/graceful-shutdown-validation-2026-09-29.json)을 참고한다.
 테스트용 DB는 별도 `postgres:17-alpine` 컨테이너의 identity_test이며 기존 DB/서비스에 migration이나 재기동을 수행하지 않았다.

## 범위와 남은 제한

- 실제 LLM/Executor 호출, 실제 Redis 서버의 종료 경합, 실제 Kubernetes SIGTERM/SIGKILL·스케일인·롤링 배포는 시험하지 않았다. Redis 테스트는 실제 소비자 코드에 제어 가능한 클라이언트를 연결한 것이다.
- 유예 시간 안에 반환한 호출을 정상 정리하도록 개선한 것이며, 모든 작업이 종료 전에 반드시 완료된다는 보장은 아니다.
- SIGKILL·노드 장애·종료 기한 강제 초과는 코드가 마무리할 기회를 주지 않을 수 있다. 이 경우 014의 토큰 보존/운영 복구 제한이 남는다. 자동 복구·checkpoint별 원자적 fencing은 이번 범위 밖이다.
- 취소를 무시하는 실행을 Python에서 강제로 안전하게 종료하지 않는다. 서비스 자원 반환보다 실행 소유권 보존을 우선한다.
- Agent 업무 로직·프롬프트·workflow 자산은 변경하지 않았다. 이번 항목은 처리량 향상이나 운영 최적 동시성 수치를 주장하지 않는다.

이 기록과 구현은 같은 작업 commit에 포함한다. 베이스 병합·원격 push·배포는 별도 단계다.
