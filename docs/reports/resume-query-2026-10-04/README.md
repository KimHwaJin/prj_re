# 073 결과 재개의 조회 비용·동일 트랜잭션 중복 정리

한 표준 사용자 흐름에서 Run/Task 재조회12회가 불필요하게 반복되는 것을 실제 SQL과 트랜잭션·바인딩으로 확인하고 제거했다. 50명3회 평균 완료 16.458→16.226초(1.41% 감소), API CPU 15.784→15.576초(1.32% 감소)다. 효과는 소폭이며 처리량의 큰 병목을 해결했다고 판단하지 않는다. 단순한 중복 제거와 기존 보호 유지에 근거해 이 정리는 유지할 것을 권장한다. 유한 로컬3회 비교이므로 일반적 개선률이나 통계적 유의성은 주장하지 않는다. 현재는 feature/result-resume-query-audit 후보이며 베이스 미병합·미푸시·미배포다.

## 조회가 많아 보인 이유와 판단

| 구간 | 실제 목적 | 판단 |
|---|---|---|
| 준비 단계 Run/Task FOR UPDATE 이후 refresh | 같은 트랜잭션에서 방금 읽고 잠근 같은 행을 재조회 | 각 준비마다2회 제거 |
| Executor 결과 반영의 잠금 후 finalize_state 잠금 | wait·recovery 확인에 읽고 잠근 Run/Task를 같은 트랜잭션에서 다시 SELECT FOR UPDATE | 각 결과 반영마다2회 제거 |
| lock_session의 최초/잠금 후 Session 조회 | 최초 프로젝트를 발견하고 user/project/admission 잠금 후 소속·삭제 상태를 최신 값으로 재확인 | 유지: 세션 이동 경쟁에 필요 |
| User FOR SHARE·Project 소유권 조회 | 사용자/프로젝트 삭제·이동과 동시 실행의 경계 | 유지 |
| Message client_request_id 조회 | 체크포인트/receipt replay에서 최초 메시지 하나를 유지 | 유지 |
| 새로운 invocation/receipt의 공개 이벤트 replay 확인 | checkpoint는 저장됐으나 서비스 저장이 실패한 경우를 복구 | 유지: 다른 트랜잭션 간 중복으로 보이더라도 필요 |
| commit 후 Run refresh | 반환 객체의 서버 시간·commit 이후 필드 수명 | 유지 |

모든 비슷한 SELECT를 중복으로 취급하지 않는다. GraphResultBatch는 이미 한 projection 트랜잭션의 메시지 권한 확인을 공유한다. 실행 단계·DB session·commit·모델 대기 사이로 이 확인 결과를 캐시하지 않았다.

## 실제 변경과 안전 경계

runs/execution.py의 prepare에서 lock_run_and_task가 populate_existing=True·FOR UPDATE로 읽은 후의 refresh(run)/refresh(task)를 제거했다. 새로운 짧은 session이 최신 committed 필드를 읽고, Run/Task 잠금은 준비 commit까지 유지한다. 추출한 plain 값만 그래프에 전달한다.

runs/projection.py의 완료 코드를 모듈 내부 _finalize_locked_state로 정리했다. 일반 완료는 finalize_state에서 최신 행을 잠근 뒤 호출하고, Executor 결과는 기존 wait/recovery 검사에서 이미 잠근 행을 같은 트랜잭션 안에서 전달한다. 중간 commit이나 graph I/O는 없다. 시간 경과에 민감한 lease ownership은 helper에서 다시 확인한다. 취소 확인·recovery 거부·최종 상태/이벤트의 원자 저장·commit 후 refresh는 유지한다.

API·요청/응답·Agent/LLM·checkpoint·schema/migration·메모리·모델 호출 수·worker 한도·DB 풀·환경변수는 그대로다. 진단용 --query-audit는 벤치마크의 선택적 CLI 옵션으로 서비스 배포 설정이 아니다. false면 SQL listener/함수 wrapper를 설치하지 않는다.

## 실제 호출 계측

| 10명 표준 진단 | 이전 | 이후 |
|---|---:|---:|
| prepare 논리 호출 | 30 | 30 |
| Executor 결과 반영 논리 호출 | 30 | 30 |
| prepare 내부 SQL | 210 | 150 |
| Executor 반영 내부 SQL(하위 projection 포함) | 600 | 540 |
| prepare의 같은 transaction/binding 행 재조회 | 60 | 0 |
| Executor 반영의 같은 transaction/binding 행 재조회 | 60 | 0 |
| 권한 경계의 Session 조회 | 280 | 280 |
| Message 중복 확인 조회 | 110 | 110 |

1명 진단도 prepare6회+Executor6회→0을 확인했다. 표준 시나리오는 입력·선택·승인3준비와2 Operation 결과·최종 완료3재개이므로1흐름12회다. 이것을 모든 분석 요청에 고정된 횟수라고 일반화하지 않는다. 같은 SQL 구조·parameter SHA·DB transaction ID·호출 ID를 함께 대조한다. parameters/메시지/코드 값을 원본 계측에 기록하지 않는다. txn ID는 진단용 local ID이고 공개 Run ID가 아니다.

진단은 전후1명/10명4회22흐름이며10명에만 CPU profiler를 켰다. 호출별 wall/SQL wall은 await와 병렬 스케줄링 시간이 포함되어 CPU가 아니다. nested scope 합산도 전체 wall이 아니다. cProfile의 async resume 횟수를 사용자 호출 수로 표현하지 않는다. 논리 호출은 wrapper가 기록한30회로 산정한다. 이4회는 속도 평균에서 제외했다.

## 동일 조건 성능 비교

| 조건 | 사용자 | 이전 완료 초 | 이후 완료 초 | 이전 사용자 평균 초 | 이후 사용자 평균 초 | 이전 API CPU 초 | 이후 API CPU 초 |
|---|---:|---:|---:|---:|---:|---:|---:|
| standard | 1 | 1.709 | 1.719 | 1.709 | 1.719 | 0.382 | 0.360 |
| standard | 10 | 4.040 | 4.014 | 4.020 | 3.481 | 3.072 | 2.966 |
| standard | 30 | 10.117 | 9.978 | 9.560 | 9.528 | 9.597 | 9.481 |
| standard | 50 | 16.458 | 16.226 | 15.549 | 15.455 | 15.784 | 15.576 |
| large20 | 1 | 3.673 | 3.694 | 3.673 | 3.691 | 1.332 | 1.337 |
| large20 | 10 | 14.897 | 14.463 | 14.534 | 14.212 | 13.461 | 13.286 |

18시도424흐름 모두 완료했다. 표준1/10/30은 각1회,50은 전후 각각3회 산술평균이다. 표준 속도12회382흐름,보조20 Operation4회22흐름,프로파일 진단2회20흐름이며 프로파일2회는 속도 분모에서 제외한다. 추가 SQL 원인 계측4회22흐름을 합하면 총22시도446흐름이다.

완료는 cohort 시작부터 마지막 terminal SSE, 사용자 평균·p95는 flow별 시간으로 측정한다. API CPU는 reset부터 owner/pool drain까지 process_time으로 PG/Redis/Executor fixture CPU는 제외한다. 50명 전후 범위는 {"before": [16.254815415944904, 16.836956500075758], "after": [16.207701708190143, 16.2356149579864]}다. 1/10/30과 보조는 단일 측정이므로 안정적인 개선률로 표현하지 않는다.

50명 첫 trial worker/event_graph의 refresh_run·refresh_task·locked_run·locked_task SELECT는 각각150회씩,총600회 감소했다. User/Project/Session·Message 중복 확인·Run advisory barrier·log/event/message INSERT·완성 로그/이벤트 조회 횟수는 동일했다. 이 범위 SQL은 17515→16915회다. 전체 SQL은 SSE/claim polling 횟수 차이도 포함하므로600회만으로 전체 SQL 감소율을 계산하지 않는다. 상세 categories는 sql-purpose.json을 따른다.

측정은 총한도20·CRUD10/checkpoint4/event4·overflow0·LLM delay0·notify on·SSE0.5초·cancel0.25초다. 실제 API·PG checkpoint·Redis·HITL·Worker·HTTP Executor 제출 fixture·결과 재개·리포트·SSE를 실행했다. 모델은 고정 응답이며 실제 Executor의 코드/데이터 연산은 제외한다. capacity를 늘리는 효과와 혼합하지 않았다.

## 남은 CPU 비용

| 10명 profiler 진단 | main 전체 CPU 초 | main SQLAlchemy self CPU 초 | main service self CPU 초 | offload CPU 초 |
|---|---:|---:|---:|---:|
| before-standard10-profile | 6.873 | 2.554 | 0.485 | 0.177 |
| after-standard10-profile | 6.760 | 2.550 | 0.484 | 0.241 |

SQL 원인 계측을 끈 별도 profiler2회의 exclusive self CPU다. SQLAlchemy 자체 CPU는2.554→2.550초로 거의 같고 main service 자체는0.485→0.484초다. 이 일부 조회 제거로 CPU의 큰 범주가 사라지지는 않았다. nested cumulative 시간을 합산하거나 하나의 함수가 전체 병목이라고 단정하지 않는다. 다음 분석은 남은 실행 준비/ORM 로딩을 원본 프로파일에서 구분하는 것이며, 더 큰 효과는 아직 가설이다.

## 소스·회귀·실패 기록

기준 55103856cbf960a3d263148cc2c429a6ef53c9f0의 src는070과 동일하다. 보류한071/072 runtime은 제외하고 기록만 가져왔다. 최종 후보 2c79e5949c5f8d5761a83db8fb3bce439acc2f1a다. 같은 harness와 고정 Git archive·실제 source_sha256을 검산한다. 운영 소스 변경은 runs/execution.py·runs/projection.py 두 곳뿐이며 신규 실제 PG 회귀를 별도 추가했다.

관련 회귀158개(skip0), 강화한 신규 경계6개 재검증이 통과했다. unique 테스트 수를164개로 합산하지 않는다. 최신 committed metadata·stale owner 거부·Executor wait 미반영 defer·recovery 거부·실제 PostgreSQL55P03 잠금 timeout·terminal replay·event flush 후 실패의 state/event 원자 rollback을 확인한다. 기존 API/HITL/SSE·프로세스 재개·권한/세션 이동·잠금·동시 Run·메시지/이벤트 idempotency 검증도 함께 수행했다.

초기 신규 테스트6건은 fixture 오류로 실패했다. 무작위 checkpoint UUID가 실제 AgentRun FK를 충족하지 않았고, 외부 user_id 문자열을 내부 UUID로 사용했다. 존재하는 Run ID와 DB 내부 user UUID로 수정했다. smoke-initial.log에 실패를 보존한다. 성능22회는 수정·검증 후의 고정 소스로 모두 완료했으며 개발 실패를 성공률 분모에 섞지 않는다.

## 증거·판단·후속

독립 검산 1,846개 통과. attempts.json/diagnostics.json은 전체 시도,raw/*.json.gz는 SHA가 있는 원본,query-audit.json은 동일 transaction·binding 증거,sql-purpose.json은 목적별 SQL,profiles.json은 main/offload exclusive CPU,source-audit.json/environment.json은 소스·환경,regression.log/lock-boundary.log는 기능 증거다. cleanup.json은 시험 자원 정리 기록이다.

```sh
python docs/reports/resume-query-2026-10-04/verify.py
```

전용 localhost PostgreSQL63372·Redis63373을 새로 준비하고 scripts/benchmarks/worker_e2e/run.py에 database-url·redis-url·고정 source-root/source-commit·새 output·users1/10/30/50·concurrency20·delay-ms0을 지정하면 재실행할 수 있다. 표준은 observation-profile standard·50명 trial-index1/2/3,보조는 large20/users1/10이다. 기본 query audit off이며 --query-audit 진단을 별도 수행한다. 실행 순서는 attempts.json을 따른다.

소폭 비용 절감 및 명확한 중복 제거로 후보 유지를 권장한다. 이 결과로 전체 병목 해소나 production 처리량 향상을 보장하지 않는다. 실제 Pod/HPA·지속 유입·원격 PG 지연·replica DB 예산·RSS는 미검증이다. 다음 성능 검토는 모델 호출 수를 건드리지 않고 이미 확보한 CPU 프로파일의 SQL 실행 준비·ORM 결과 생성 비용을 구분해 더 큰 비용을 먼저 선택하는 것이다. 근거 없이 필요한 조회를 캐시하거나 보호를 제거하지 않는다. 모델/Registry/Workflow CRUD/광범위 운영·066/067/071/072 보류는 유지한다.

기존18개 서비스와 원래 checkout/.env를 보존했다. 생성한2개의 전용 컨테이너와 anonymous volumes만 정리했다. 아직 베이스 병합·원격 push·배포는 수행하지 않았다.
