# 032 — Agent 로그·대응 이벤트 원자 저장·멱등 복구

- 상태: 구현·격리 PostgreSQL·마이그레이션·회귀·wheel 검증 완료. 베이스 병합·배포 미수행.
- 작업 commit: 이 문서를 포함한 feature commit에 기록한다.
- 브랜치: `feature/log-event-atomicity`
- 출발 commit: `6dcea76` (031 최초 호출 복구)
- 날짜: 2026-09-30

## 문제와 개선 범위

029의 R3: AgentRunLogService가 로그부터 commit하고 대응 agent.event를 별도 commit한다. 이벤트 저장이 실패한 후 같은 key를 다시 호출하면 기존 로그 조기 반환으로 누락 이벤트를 복구하지 못한다.

로그와 이벤트를 하나의 짧은 transaction으로 저장하고, 이벤트에 로그 ID 연결 및 unique 제약을 추가한다. 동일 key의 동시 요청·commit 응답 유실·기존 로그만 있는 데이터의 재처리도 각각 하나로 수렴하도록 검증한다. 체크포인트·Agent 업무 흐름·메시지 전체 transaction 통합은 범위에서 제외한다.

## 검증 계획

- 기존 코드에서 이벤트 저장 장애 후 로그 1개/이벤트 0개를 재현.
- 이벤트 저장/commit 실패 및 취소 시 둘 다 rollback, 재시도 시 각각 1개.
- 동시 동일 key 재처리와 서로 다른 key의 동일 payload를 구분.
- 기존 정상/누락/동일 payload 이력의 migration backfill 및 downgrade/upgrade 확인.
- 기존 최초 호출/resume 복구·SSE·전체 회귀·패키지 검증.

검증은 아래 실제 결과와 연결된 원본 기록을 따른다.


## 구현 내용

- `AgentRunLogService.create`: 로그 key 기준 중복 처리와 연결 이벤트 확인. 불완전한 쌍은 로그 잠금 아래 이벤트를 생성하고 로그/이벤트/순번을 한 transaction으로 commit한다. 저장된 원래 로그 내용이 기준이다.
- `TaskEventService` 및 model: nullable 로그 ID 연결과 unique/FK를 추가한다. 다른 이벤트 및 공개 SSE payload는 유지한다.
- `20260930_0023`: 기존 동일 Run/내용의 로그·이벤트를 발생 개수대로 연결하며 기존 ID/순번/내용을 보존한다. downgrade는 연결만 제거한다.
- 이미 완성된 쌍은 조회만 하는 경로로 기존 재처리 비용을 유지한다. 원자 저장을 위해 매번 로그를 잠글 필요는 없다.

[저장 계약·기존 데이터 처리·배포 제한](../architecture/log-event-persistence.md).

## 검증 결과

- 수정 전 probe 1개 통과(3.86초): 기존 결함을 기대하는 진단으로, 재시도 후 로그 1개/대응 이벤트 0개가 확인됨.
- 장애/동시성 PostgreSQL 회귀 11개 통과(7.34초).
- 기존 데이터 upgrade/downgrade 및 누락 복구 1개 통과(6.16초).
- 전체 소스 회귀 **647 passed, 53 warnings, 2 subtests passed** (259.68초). 경고는 기존 checkpointer 없는 하위 그래프의 durability 설정 관련이다.
- 이후 완성된 로그·이벤트 쌍의 조회 전용 경로를 보완하고 관련 **72개 통과** (71.49초). 이 보완 후 전체 647개를 다시 실행한 것은 아니다. 실제 SQL 계측은 SELECT 1회이며 쓰기/FOR UPDATE가 없다.
- 최초 호출의 checkpoint 이후 실제 로그 이벤트 저장 실패도 질문 대기/즉시 종료 양쪽에서 주입했다. runtime/pool 재생성 후 저장만 복구하며 입력 노드/모델 실행은 각각 1회다.
- wheel 빌드 후 변경 모듈과 최종 소스 일치 및 테스트 패키지 제외 확인. Dockerfile의 crud_migrations 복사 설정을 확인했다. Docker 이미지 빌드/배포는 하지 않았다.

## 제한과 후속

DB migration이 필요한 변경이다. 새 코드 전 0023 적용과 기존 writer drain이 필요하다. 구·신 writer 혼재 안전성은 보장하지 않는다. 기존 동일 내용 기록의 연결은 원래 event_key 복원이 아니라 내용/발생 개수에 기반한 대응이다. 이벤트가 누락된 과거 완료 작업 전체를 자동 스캔하지는 않으며 같은 로그 key 재처리 시 복구한다.

Task가 없는 Run의 로그 단독 저장 정책은 유지한다. 메시지·체크포인트·최종 Run 상태 전체를 한 transaction으로 통합한 것은 아니다. 프로세스 강제 종료 후 owner 복구, 관리자 복구 API, 외부 Executor/Redis 및 Kubernetes 배포 검증은 후속으로 남긴다.

원본 체크아웃/기존 앱 컨테이너는 변경하지 않았다. 베이스 병합·push·배포는 미수행이다.


전용 임시 PostgreSQL 컨테이너·볼륨은 검증 후 제거했다. 코드 테스트와 migration은 이 격리 DB에서만 수행했다. Agent/LLM은 mock이며 이번 신규 테스트에서는 외부 API에 부하를 주지 않았다.

## 검증 기록과 재실행

[검증 집계·소스 hash](../reports/log-event-atomicity-2026-09-30/validation.json), [수정 전 결함 probe](../reports/log-event-atomicity-2026-09-30/before-defect-probe.txt), [전체 회귀](../reports/log-event-atomicity-2026-09-30/full-suite-before-read-optimization.txt), [최종 관련 회귀](../reports/log-event-atomicity-2026-09-30/final-targeted-suite.txt), [wheel 확인](../reports/log-event-atomicity-2026-09-30/wheel-check.txt).

`DTEST_IDENTITY_TEST_DATABASE_URL`은 폐기 가능한 localhost의 `identity_test` DB만 지정한다. fixture가 schema를 재생성하고 migration을 왕복하므로 운영 DB를 사용하지 않는다. 실제 서비스 설정/비밀번호는 기록하지 않았다.

```sh
PYTHONPATH=src python -m pytest src -q --tb=short
PYTHONPATH=src python -m pytest \
  src/api_service/test/test_log_event_atomicity_postgres.py \
  src/api_service/test/test_log_event_migration_postgres.py \
  src/api_service/test/test_initial_request_recovery_postgres.py \
  src/api_service/test/test_user_resume_recovery_postgres.py \
  src/api_service/test/test_run_stream_notifications.py \
  src/api_service/test/test_short_transactions_postgres.py \
  src/api_service/test/test_token_event_buffer_postgres.py -q --tb=short
```

수정 전 결함 probe는 `6dcea76` 기준으로 선택 실행했다. 수정 이후 동일 probe의 결함 기대 assertion이 실패하는 것이 정상이며, 새 회귀 테스트를 정합성 기준으로 사용한다.
