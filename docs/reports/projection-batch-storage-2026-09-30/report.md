# 결과 단위 검증 공유·배치 저장 비교 — 2026-09-30

이번 변경은 기본 결과 반영 경로의 DB 비용을 줄였다. 동일 조건 8회에서 Worker SQL은 사용자당 281→225회(19.9% 감소), commit 메서드 호출은 48→29회(39.6% 감소)다. 실제 DB commit은 별도 probe에서 결과 묶음당 9→1회로 확인했다.

1명 조건의 결과 반영 시간은 0.774→0.270초(65.1% 감소), 전체 평균 완료 시간은 25.08→24.36초였다.

10명 조건의 결과 반영 시간은 0.403→0.273초(32.4% 감소), 전체 평균 완료 시간은 48.63→48.29초였다.

전체 시간 변화에는 큐·초기화·알림 등 반복별 변동도 섞여 있으므로 그 차이를 모두 이번 변경 효과로 돌리지 않는다. 모델 4회 대기와 4개 실행 슬롯의 큐 대기가 여전히 대부분이며, 운영 처리량이 크게 늘었다는 결론은 아니다. 전체 회귀 664개가 통과했고 44명/176 실행 구간 모두 성공했다.

## 변경과 비교 범위

033의 `eef0bb7`과 034 구현 Git tree `9778a253af4d011c51c0c077c07ed4fe0f6562f0`을 비교한다. 후자는 운영 코드 8개 파일만 stage해서 만든 불변 tree이며, 최종 034 feature commit의 운영 코드와 바이트 단위로 같다. 재현 시 tree가 사라졌으면 최종 034 commit을 사용한다. 이전 리팩토링 전체와 비교한 결과가 아니다.

기본 graph dispatcher에서 한 결과의 메시지·로그·대응 이벤트·원본 사용자 trigger 연결을 짧은 transaction으로 묶는다. 세션 검증과 잠금은 결과 안에서 공유하고, DB session/transaction이 바뀌면 공유 context 재사용을 거절한다. 기존 개별 CRUD 및 custom dispatcher의 계약은 유지한다. Task graph identity 연결, LangGraph checkpoint 및 최종 Run 상태 저장은 별도 경계다. LLM 대기 동안 service DB 연결/transaction을 유지하지 않는다.

기존에는 같은 결과의 일부 이벤트가 먼저 보일 수 있었지만 이제 해당 결과 묶음은 commit 이후 함께 보인다. SSE의 저장된 event 개수·순서·재조회 계약은 유지한다. 여러 graph step 또는 전체 사용자 세션을 한 transaction으로 합치지 않는다.

## 실제 API 측정 조건

- 이전→이후→이전→이후 순서로 1·10명 조건을 각각 2회 측정했다. 총 8회, 44명, 176 실행 구간·모델 호출이다.
- API 1프로세스, Run 동시 실행 4개. Service pool 10/overflow 0, checkpoint pool 1–4, bridge pool 4. PostgreSQL 17 로컬 전용 DB.
- 로컬 HTTP LLM mock은 호출당 5초, 사용자당 4회다. 실제 API/Worker/DB/checkpoint/SSE와 3회 resume를 거쳐 Workflow 승인 대기에서 끝난다. 실제 LLM/Executor/Redis는 호출하지 않았다.
- 단계 사이 think time 0.2초. 사용자 생성은 측정 밖, 세션 생성은 측정 안. 매 trial에 DB·프로세스·pool을 초기화하므로 cold start 비용이 포함된다.
- 동시 시작하는 유한 batch이며 운영의 지속 유입률/한계 처리량이나 SLA를 인증하는 부하테스트가 아니다.

사용자별 시간 합계의 평균을 각 반복에 대해 구한 뒤 두 반복을 평균했다. 단위는 초다.

| 사용자 수 | 전체 완료 | 큐 대기 | LLM | 내부 처리 | 결과 반영 |
|---|---:|---:|---:|---:|---:|
| 1 | 25.083 → 24.362 | 0.882 → 0.620 | 20.082 → 20.074 | 2.023 → 1.227 | 0.774 → 0.270 |
| 10 | 48.629 → 48.288 | 25.455 → 25.035 | 20.025 → 20.025 | 1.045 → 0.872 | 0.403 → 0.273 |

결과 반영은 `persistence.*` span의 합집합이며 내부 처리의 일부다. 둘을 더하지 않는다. 최종 Run 상태 저장, API 접수 및 모든 DB 작업 전체를 의미하지 않는다. 전체 시간에는 표 밖의 접수/알림/think time도 포함된다.

| 사용자 수 | Worker SQL/사용자 | Worker commit 메서드 호출/사용자 | Batch 완료 사용자/초 |
|---|---:|---:|---:|
| 1 | 281 → 225 | 48 → 29 | 0.0399 → 0.0410 |
| 10 | 281 → 225 | 48 → 29 | 0.1784 → 0.1795 |

Worker SQL은 SQLAlchemy cursor 실행 횟수이며 checkpoint/bridge SQL을 포함하지 않는다. commit 호출은 `AsyncSession.commit` 메서드 호출로, 실제 DB COMMIT과 같지 않다. 아래 집중 probe는 engine commit 이벤트를 별도로 측정한다. 작은 반복 수에서 전체 완료 시간의 작은 차이를 통계적으로 확정된 효과로 해석하지 않는다.

### 반복별 변동

| 사용자 수 | 코드 | 반복 | 결과 반영 초 | 전체 완료 초 |
|---|---|---:|---:|---:|
| 1 | before | 1 | 0.462 | 24.419 |
| 1 | after | 1 | 0.301 | 24.562 |
| 1 | before | 2 | 1.086 | 25.746 |
| 1 | after | 2 | 0.239 | 24.163 |
| 10 | before | 1 | 0.420 | 48.226 |
| 10 | after | 1 | 0.296 | 48.442 |
| 10 | before | 2 | 0.386 | 49.031 |
| 10 | after | 2 | 0.249 | 48.135 |

특히 1명 조건의 개선 전 결과 반영 시간은 0.462초와 1.086초로 변동이 컸다. 따라서 평균 65.1% 감소를 안정적으로 재현되는 고정 효과로 기대해서는 안 된다. 10명 조건에서는 전후 두 반복 모두 저장 시간이 감소했지만, 여기서도 운영 환경의 동일 효과를 보장하지 않는다. SQL/commit 감소는 모든 반복에서 동일하게 확인된 구조적 변화다.

## DB 호출 절감 근거

동일한 assistant 메시지 4개와 시작 로그를 반영하는 probe를 이전/이후 소스에서 실행했다. 이후 개별 서비스와 달리 전체 projection 경계를 측정한다.

| 작업 | SQL 이전→이후 | 실제 engine commit 이전→이후 |
|---|---:|---:|
| 새 결과 묶음 | 65 → 44 | 9 → 1 |
| 같은 결과 재처리 | 37 → 16 | 1 → 1 |

신규 저장의 검증/잠금 호출은 28→7회, 로그 조회/삽입 10회, Run→Task 조회 5회, 이벤트·counter 10회, 메시지·leaf 12회다. 즉 SQL 21회 절감은 같은 transaction 안에서 세션 검증을 공유한 결과이며, 저장 데이터 생략으로 만든 수치가 아니다. 아직 로그별 Run→Task 조회와 개별 event sequence 갱신은 남아 있다. 모두 불필요하다고 단정하거나 이번 범위를 계속 확대하지 않는다.

## 잠금과 기능 검증

배치 도입 시 일부 행의 잠금을 더 오래 유지하므로 Run 로그 advisory lock을 모든 새 로그 writer가 공유한다. 기본 배치는 Run 로그 barrier → 사용자/프로젝트/세션 → 로그/Task 순서로 진입한다. 개별 새 로그 저장은 이를 위해 SQL 5→6회로 1회 증가하며, 완성 로그 재처리는 1회 그대로다. HTTP 비교에는 이 비용이 포함된다.

처음 추가한 경합 테스트에서 세션 row lock을 먼저 잡은 새 batch와 개별 writer 사이의 교착을 발견했다. Run 로그 barrier를 먼저 얻도록 수정하고 역순 로그 입력 테스트를 통과시켰다. 이는 작업 중 발견해 수정한 배치 도입상의 문제이며, 기존 서비스 장애 원인으로 주장하지 않는다.

집중 테스트 40개 통과. 중간 오류/취소 rollback, 같은 결과 6개 동시 저장, 권한/프로젝트/세션 확인, 원본 사용자 trigger 연결 rollback, 외부 reader의 부분 결과 비노출, 개별 log writer와의 경합, commit 뒤 batch context 사용 거절, 기존 짧은 DB 세션 동작을 포함한다. 신규 결과 실패 시 기존 checkpoint를 읽어 다시 반영하는 기존 복구 회귀도 전체 suite에서 확인했다. 배포 wheel의 변경 8개 모듈은 소스와 동일하고 test 패키지는 제외된다.

## 원본과 재현

[비교 집계](comparison.json), [각 trial 상세](trials.json), [독립 검산](checks.json), [원본 해시](manifest.json), [변경 전 SQL](before-queries.json), [변경 후 SQL](after-queries.json), [집중 검증](targeted-suite.txt), [전체 회귀](full-suite.txt), [패키지 검증](wheel-check.txt), [소스/실행 조건](validation.json).

기존 `scripts/benchmarks/runtime_profile/run.py`로 `--ref eef0bb7` 및 개선 ref를 각각 `--users 1 10` 조건으로 두 번 실행하고, `scripts/benchmarks/projection_roundtrips/compare.py`에 before/after 경로를 전달한다. 전용 localhost `identity_test` DB만 사용해야 하며 harness가 매 trial public schema를 초기화한다. pytest와 benchmark를 같은 DB에서 동시에 실행하지 않는다. SQL probe 재현은 `test_projection_batch_postgres.py::test_batch_query_cost_and_replay_equivalence`이며 이전 소스에서는 `DTEST_PROJECTION_MEASURE_ONLY=1`로 이후 SQL budget만 생략한다. 데이터·순서·재처리 검증은 생략하지 않는다.

운영 설정, replica/Run 동시 실행 수, Agent 흐름은 변경하지 않았다. 베이스 병합·push·배포는 수행하지 않았다.

검증용 PostgreSQL 컨테이너·볼륨은 정리했다. 기존 서비스 컨테이너와 원본 체크아웃은 변경하지 않았다.
