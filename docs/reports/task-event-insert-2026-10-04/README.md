# 072 TaskEvent 저장 왕복 축소 검증

DB 왕복 감소는 확인했지만, 같은 한도에서 처리량 개선을 입증하지 못해 두 후보 모두 채택을 보류한다. 표준50명3회 평균 완료 16.452→17.132초(4.13% 증가), API CPU 15.771→16.242초(2.99% 증가)다. 추가 SQL/ORM 처리의 복잡성을 감수할 근거가 부족하다. 이 브랜치는 구현·증거 보존용이며 베이스 미병합·미푸시·미배포다.

## 무엇을 바꾸었는가

기존 append_for_run은 Run의 Task ID 조회, Task 순번 증가, TaskEvent INSERT의 3문장을 실행했다. events/task_event_write.py에 고정 CTE를 두어 UPDATE의 scalar subquery로 Task를 찾고 같은 문장에서 INSERT한다. 변경되는 UUID·payload는 매 호출 바인딩하며 DB 결과를 캐시하지 않는다. Task row lock은 outer commit/rollback까지 유지한다.

첫 후보 fb0fc38은 전체 TaskEvent를 RETURNING했다. 이 경우 표준50명 평균16.465→16.600초로 개선되지 않았다. JSON payload까지 다시 받는 비용을 줄이기 위해 최종 후보는 DB 생성 ID·Task ID·순번·시간 4필드만 받고 이미 제공한 필드는 ORM committed value로 채운다. commit=True에서는 전체 column을 명시해 refresh한다. [첫 후보 별도18시도·원본](../task-event-full-return-2026-10-04/README.md)을 보존하며 두 후보의 baseline/평균을 섞지 않는다.

pending new/deleted ORM 객체가 있으면 기존 flush/참조 의존 경로로 fallback한다. 로드된 Task의 순번만 동기화하고 server-updated timestamp는 만료하며 다른 pending 변경을 덮어쓰지 않는다. commit=False의 기존 flush 경계도 유지한다. API·Agent·LLM·checkpoint·schema/migration·설정·pool·총한도·첫 payload·log 연결·Run barrier·세션/권한 보호는 변경하지 않았다. 여러 이벤트의 실제 DB write 자체를 줄이거나 한 INSERT로 일괄 쓰는 변경은 아니다.

## 소스와 동등 조건

기준 f229b9ea8b5c0089936995476a9f01e4c545282a은070 src와 동일하고071 후보 구현을 포함하지 않는다. 최종 후보 975edfb3b0c750dfff984386cfa6f1d2147717aa, 브랜치 feature/task-event-atomic-insert다. 고정 Git archive와 실제 실행 source_sha256을 verify.py가 대조한다. 같은 harness/요청/총한도20, CRUD10/checkpoint4/event4/overflow0이다. SSE heartbeat0.5초·취소 scan0.25초·notify on을 유지한다.

실제 CRUD API·PG checkpoint·Redis·HITL·공통 Worker·HTTP Executor 제출 fixture·이벤트 재개·리포트·최종 SSE까지 실행한다. 모델은 delay0 고정 응답이고 Executor는 HTTP fixture이며 실제 Python/모델 추론/데이터 연산 부하는 없다. 표준2 Operation·4 Tool·4 model calls, 보조20 Operation·20 Tool·23 calls·64KiB output 조건이다. 이는 실제 Executor 연산을 포함한 E2E 성능 수치가 아니다.

## 결과

| 조건 | 사용자 | 이전 완료 초 | 후보 완료 초 | 이전 사용자 평균 초 | 후보 사용자 평균 초 | 이전 API CPU 초 | 후보 API CPU 초 |
|---|---:|---:|---:|---:|---:|---:|---:|
| standard | 1 | 2.237 | 1.679 | 2.237 | 1.679 | 0.416 | 0.359 |
| standard | 10 | 4.060 | 4.072 | 3.848 | 3.941 | 3.001 | 3.081 |
| standard | 30 | 9.743 | 9.849 | 9.155 | 9.312 | 9.172 | 9.377 |
| standard | 50 | 16.452 | 17.132 | 15.561 | 16.232 | 15.771 | 16.242 |
| large20 | 1 | 4.186 | 4.298 | 4.186 | 4.297 | 1.418 | 1.455 |
| large20 | 10 | 14.546 | 14.530 | 14.138 | 14.383 | 13.303 | 13.515 |

50명만 전후 각각3회 산술평균이며 나머지는 각1회다. 완료는 cohort 시작부터 마지막 terminal SSE다. 사용자 평균·p95는 각 flow 시간으로 별도 집계하며 p95를 전체 합친 percentile로 표현하지 않는다. API CPU는 reset부터 owner/pool drain까지 process_time이고 PG/Redis/fixture CPU는 포함하지 않는다. 프로파일은 별도2회로 속도 분모에서 제외한다. 50명 전후 완료 범위는 {"before": [16.15448158304207, 16.634146749973297], "after": [16.79043566598557, 17.72681612498127]}다. 단일 측정에서1명 차이가 커도 일반적 개선률로 외삽하지 않는다. 시도 순서 교차와 새 DB를 사용했으며 OS·Docker 부하를 완전 통제한 통계 실험은 아니다.

표준50명 첫 trial worker/event_graph 영역에서는 기존 Run→Task SELECT1,000/sequence UPDATE1,150/TaskEvent INSERT1,150이 CTE1,000/독립 UPDATE150/독립 INSERT150으로 바뀌었다. 두 왕복씩 총2,000회를 줄였지만 논리 sequence UPDATE/TaskEvent INSERT는 각각1,150으로 같다. 보호용 advisory barrier·완성 로그/이벤트 확인·AgentRunLog INSERT의 횟수는 동일하다. 전체 SQL denominator는 sql-purpose.json에 별도 기록한다.

동일3이벤트 portable probe 신규SQL16→10, replay4→4, commit1→1이다. 축소 RETURNING에 payload가 없음을 실제 SQL로 검증했다. SQL 절약을 CPU·시간 절약으로 간주하지 않는다.

## CPU와 기능 검증

| 10명 profiler 진단 | main SQLAlchemy self CPU 초 | main service self CPU 초 | main 전체 CPU 초 | offload CPU 초 |
|---|---:|---:|---:|---:|
| before-standard10-profile | 2.581 | 0.508 | 7.092 | 0.191 |
| after-standard10-profile | 2.789 | 0.491 | 7.323 | 0.180 |

exclusive self CPU이며 nested cumulative 시간을 합하지 않는다. profiler 자체 비용이 있어 이2회는 원인 후보 진단용이다. 이번 진단에서 main SQLAlchemy self CPU는2.581→2.789초로 줄지 않았다. 왕복 감소에도 SQLAlchemy/async 실행·checkpoint·API 처리 비용은 남아 있다. 이 측정으로 특정 한 함수가 전체 병목이라고 확정하지 않는다.

최종 PostgreSQL/API/Worker 회귀161개(skip0), 최종 portable probe1개가 통과했다. concurrent mixed writer·bigint 순번·Unicode/nested payload·insert 실패/rollback·taskless/missing Run·다른 Run/세션 binding·pending ORM new/delete/dirty·expire_on_commit·첫 payload/replay·HITL 재시작·SSE·CRUD 소유권/잠금·토큰 저장 경계를 포함한다. 첫 후보 최종147개와 최종161개는 다른 검증 범위로 합쳐 unique test 수를 주장하지 않는다.

축소 반환 첫 회귀는 commit 후 sticky load_only로 일부 필드가 만료되는 DetachedInstanceError2건(145pass/2fail)이 발생했다. 전체 columns의 명시적 refresh로 수정했고 regression-generated-initial.log에 실패를 보존했다. 첫 후보 초기 SQLAlchemy strategy 오류와 테스트 rollback PK 오류도 별도 보고서/로그에 남겼다. 개발 실패를 성능 성공률 분모에 혼합하지 않는다.

최종18시도424흐름 모두 완료(오류0), 독립 검산 1,694개 통과다. 첫 후보까지 합하면36성능시도848흐름이며 모든 시도를 보존했다. 최종 표준 속도12시도382흐름/보조4시도22흐름/profiler2시도20흐름이다. 원본에는 HTTP201/201/202/202/202·3private invocations(2interrupt/1success)·attempt1·Executor fixture receipt·관찰/리포트·owner/pool/outbox/inbox/commands drain 검증을 포함한다.

## 증거·재현·후속

attempts.json은 전체 시도,raw/*.json.gz는 원본 SHA,measurements.json/comparison.json은 개별/평균,sql-purpose.json은 SQL 목적별 원본 집계,profiles.json은 CPU,source-audit.json/environment.json은 고정 소스/환경,projection-probe.json과 regression.log는 기능 증거다. cleanup.json은 최종 시험 자원 정리 증거다.

```sh
python docs/reports/task-event-insert-2026-10-04/verify.py
```

단일 trial 재현 예시는 아래와 같다. 데이터베이스·Redis는 새 시험 전용 localhost 주소로 채우고, source-root는 baseline 또는 고정 후보 checkout으로 지정한다. trial마다 새 output을 사용한다.

```sh
python scripts/benchmarks/worker_e2e/run.py \
  --database-url '<dedicated localhost postgres URL ending /postgres>' \
  --redis-url '<dedicated localhost Redis URL>' \
  --source-root '<fixed checkout>' \
  --source-commit 975edfb3b0c750dfff984386cfa6f1d2147717aa \
  --output '<new output directory>' \
  --users 50 --concurrency 20 --delay-ms 0 \
  --observation-profile standard --trial-index 1
```

1/10/30은 users를 바꾸고,50은 trial-index1/2/3을 전후 각각 반복한다. 보조는 observation-profile large20에 users1/10, CPU 진단은 standard/users10에 --cpu-profile을 추가하여 속도 집계와 분리한다. 전후 순서는 attempts.json을 따른다.

verify.py는 고정 소스·같은 harness·cohort/오류/drain·SQL denominator와 산술을 독립 검산한다. 실제 재실행은 고정 candidate/baseline 소스 및 scripts/benchmarks/worker_e2e/run.py와 새 전용 localhost PG/Redis가 필요하다. 기록의63372/63373 서비스는 시험 종료 후 제거했다. 테스트 사용자 데이터 외 기존 서비스/.env·원래 checkout은 변경하지 않았다. 기존18개 컨테이너를 보존하고 생성한2개 컨테이너와 전용 anonymous volume만 제거했다.

두 후보를 다음 runtime 기본값으로 승계하지 않는다. 다음 성능 작업은 f229b9e(070 runtime+071 기록)에서 분기하고072 문서/검증만 가져간다. 다음은 재개1회 안의 권한·상태·메시지 투영 조회를 목적별로 대조해 실제 중복 여부와 CPU 비용을 먼저 확인한다. 같은 트랜잭션에서만 안전하게 재사용할 수 있는 경우에 한해 후보를 만들고 동등 측정한다. SQL 수 감소만으로 재차 채택하지 않는다. 실제 Pod/HPA·지속 유입·원격 DB 지연·replica DB 예산·RSS는 미검증이다. 모델 호출 수·Registry·Workflow CRUD·광범위 운영·066/067 보류를 유지한다.
