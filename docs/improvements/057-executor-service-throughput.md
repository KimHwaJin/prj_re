# 057 Executor 연계까지 서비스 처리량 개선

| 항목 | 내용 |
|---|---|
| 상태 | 구현·로컬 A/B·관련 회귀 완료 / 베이스 병합·origin 게시 확인 |
| 시작일 / 완료일 | 2026-10-03 / 2026-10-03 |
| 브랜치 | feature/executor-service-throughput |
| 출발 commit | cbe520f97089c8e120a561e544ce67e35079048a |
| 배포 | 미배포. 원래 checkout·.env·Compose 유지 |

## 문제와 범위

056은 `plan_approved`까지만 측정했다. 이번에는 승인 → 실제 HTTP 제출 → binding/Inbox/Outbox → Redis 이벤트 → Event Worker 그래프 재개 → Markdown 저장 → SSE 완료까지 서비스 경로를 확인했다.

부하용 Executor는 코드를 실행하지 않는 별도 loopback HTTP fixture다. 실제 Executor/Jupyter는 변경 전후 1건씩 기능 대조했다. 최초 계획 모델만 0/5000ms로 고정하고 review/report는 0ms 응답이다. LLM 호출 횟수·prompt·Agent 흐름은 바꾸지 않았다.

## 문제점과 실제 변경

1. **Router/Outbox의 유휴 대기.** commit 후에도 다음 scan까지 최대 2초를 기다릴 수 있었다. 처리 가능한 event와 command가 commit되면 local `asyncio.Event`로 다음 단계를 깨우고 20ms 동안 신호를 합친다. 다른 Pod·재시작의 주기 scan과 오류 backoff는 유지한다. 모든 진행 Step마다 깨우던 중간안은 불필요한 scan이 늘어 제외했다.
2. **binding보다 결과가 먼저 도착하는 순서.** 결과를 ingest한 scan에 binding이 아직 없으면 유휴 대기로 돌아갈 수 있다. `Store.register`가 transaction을 commit하고 연결을 반환한 뒤 같은 DB·namespace의 local 구독자에게 알린다. 추가 DB/Redis 연결이나 별도 영속 큐를 만들지 않는다.
3. **API에서 Event Worker로 세션을 인계하는 틈.** 빠른 결과가 API Task/owner 반납 전에 도착하면 기존 즉시 Defer가 Redis PEL 재전달을 기다린다. API owner 또는 일반 API Task에만 재확인 대기 예산 1초·최대 50ms 간격을 적용한다. 매 SQL context를 닫은 후 기다리며 개별 SQL 실행 시간은 별도다. owner를 탈취하거나 recovery를 자동 해제하지 않는다. 다른 Event owner·recovery·비활성 리소스는 즉시 유예하며 기한 후 기존 재전달을 유지한다.
4. **누적 public_events의 반복 DB 투영.** `InvocationProjection`을 최초 Run·사용자 resume·Executor resume에 적용했다. 한 호출에서 commit된 동일 내용만 생략한다. 첫 상태·새 호출·변경 payload·최종 receipt 복구는 기존 DB 중복 제거로 보낸다. commit 전에 fingerprint를 전진하지 않고 custom dispatcher/legacy의 전체 전달 계약, `durability=sync`, 최종 snapshot repair를 유지한다.
5. **설정 후보.** opt-in profile에 Agent32, Event ingress4/dispatch4, CRUD10/overflow0, checkpoint4, EW pool4, SSE0.5를 명시했다. Event와 제출 bridge는 서로 다른 풀에 EW pool 상한을 각각 적용한다. 잠재 DB 연결은 25개/프로세스이며 replica 전체 상한이나 실제 모델 32동시 호출을 보장하지 않는다.
6. **현재 흐름에 맞는 진단 도구.** 실제 cookie/CSRF·HTTP·PG·Redis·manifest/checksum·SSE를 실행하고 Run/command 연계, CPU/SQL/queue/Event 단계, 실패 증거 거절, 결과 정리를 검증한다. 원문 압축·hash·독립 검산·보고서·재현 안내를 추가했다. 소유한 UUID DB·Streams·mock 파일만 정리한다.

production 9파일을 변경/추가했다. Agent source/skills/tools/prompts, service_contracts, integrations, 의존성, migration, dev/stg/prd 및 app 진입점은 동일하다. 전체 OpenAPI 38개 path의 hash도 동일하다.

## 측정 결과

평균 초, 최초 모델 5초, 같은 Event 한도4·단일 API 프로세스다. 50명·Agent32는 양쪽 각 2회 평균이고 나머지는 각 1회다.

| 사용자 | Agent16 전→후 | Agent32 전→후 |
|---|---:|---:|
| 1 | 10.101 → 6.765 | 10.041 → 6.736 |
| 10 | 10.775 → 8.706 | 9.919 → 8.648 |
| 30 | 15.818 → 13.184 | 16.574 → 15.656 |
| 50 | 33.684 → 32.010 | 23.622 → 22.686 |


50명·Agent32의 평균은 **23.622→22.686초, 4.0% 단축**이다. 사용자당 CRUD SQL은 676.11→583.08회(13.8%), Event 투영 SQL은 224→176.42회(21.2%), API CPU는 0.397→0.378초(4.8%)로 줄었다. 정상 조건에서 큰 시간 개선이라고 표현하지 않는다. 모든 주 비교 조건에서 메시지·로그·이벤트의 DB 행 수는 전후 동일했다.

모델 0초·50명·Agent32는 변경 전 39회의 Defer가 발생해 handler 189시도/150성공·평균 38.821초였다. 최종안은 0 Defer·150시도·평균 19.584초로 완료했다. 변경 전 HTTP는 모두 완료됐지만 정상 Event 비용 대조군은 아니다. 재전달 지연 제거와 정상 SQL 비용 개선을 구분한다.

Event8/16 탐색은 공통 투영 전의 중간 후보에서 각각 1회 수행했다. Event4의 2회 평균 23.624초, Event8은 23.091초, Event16은 24.311초였다. 8의 차이가 작아 4를 유지하며, 최종 코드의 sweep으로 표현하지 않는다.

## 대기 수명과 실제 Executor 확인

10명·5초 보류 설정의 20표본에서 Agent/Event 실행은 0이고 모든 psycopg 연결이 pool에 반환됐다. CRUD checkout은 13표본에서 0, 7표본에서 1이었다. 보류 구간의 DB active/idle transaction 표본은 0이었다. checkout owner를 기록한 추가 반복은 20표본 모두 CRUD0이었으나 첫 시험의 7표본은 소유자를 기록하지 않아 정확한 작업을 확정하지 못했다. 모든 checkout이 항상 0이었다고 표시하지 않으며 1주 대기·멀티 Pod 검증으로 확장하지 않는다.

실제 Executor는 변경 전후 각 1건에서 parquet/Jupyter·MULTI2 Operations·continue·명시적 finalize·관찰4개·report ready를 완료했다. 11.495→6.232초였지만 기능 대조이며 대용량 계산 처리량의 개선은 아니다. 자체 실행 결과는 Executor 저장소에 보존했다.

## 검증과 근거

- 26 trial·686 완료 사용자, 최대50명. 압축 원문 26개와 388개의 산술/hash 독립 검산을 확인했다.
- 대조군 0초의 39 Defer 외에는 측정 로그의 GC/traceback/ERROR/maintenance failure/Defer가 0이었다. 최종 측정은 HTTP 성공·attempt1·Command DONE·report ready·owner/recovery/pending/checkout0이었다.
- 전체919 회귀에서916 pass, 기존 fixture interface mismatch 3건이 발견돼 새 필드·인자에 맞췄다. 관련112개(17.86초) 및 최종 capture13개(0.29초)가 모두 통과했다. 신규 capture 항목2개를 포함한 고유 범위는921개이며 중복 실행을 합산하지 않는다. 초기 broad의81 warnings는 checkpointer 없는 fixture의 durability 경고다.
- commit 후 알림·오류 backoff·legacy/custom 전달·실패 시 재투영·DB 연결 반환·owner 비탈취·PG 메시지/log/event 일관성을 확인했다.
- Agent/계약/의존성 등181개 보호 파일은 동일하며 최종 capture12개의 production hash가 현재 소스와 일치한다. 기존 checkout HEAD/status/.env/316개 파일은 동일하다.
- canonical report validation/npm packaging/구조 검증은 통과했다. headless Chromium 부재와 native Chrome 시도 timeout으로 육안·mobile·source dialog QA는 미실행이며 품질 영수증에 기록했다.

[상세 분석](../reports/executor-throughput-2026-10-03/README.md), [HTML 보고서](../reports/executor-throughput-2026-10-03/report.html), [지표](../reports/executor-throughput-2026-10-03/results.json), [검증 영수증](../reports/executor-throughput-2026-10-03/verification.json), [재현](../../scripts/benchmarks/executor_throughput/README.md), [설정](../service-throughput-settings.md).

## 후속과 제약

현재 profile은 로컬 service-only 후보다. 실제 Pod/HPA·모델 한도·후속 답변/보고서·project_memory 읽기/자동 갱신·대용량·초장기·멀티 Pod 인계는 미확정이다. local 알림은 다른 Pod로 전달되지 않으며 주기 scan을 유지한다. Agent32가 항상16보다 빠르지는 않다(30명 결과). SQL/CPU 비용을 줄였지만 모든 경합과 큐를 없앤 것은 아니다.

기존 모델 호출 최적화·Dataset Registry·Workflow CRUD·광범위 운영 보완의 보류를 유지한다. 다음 서비스 성능 범위는 후속 설명/보고서와 memory 경로다.

## 통합·게시

2026-10-03 구현·검증 commit `6f0b1ee4ce657092fbe4bbae24c50db2a1d1e1c3`를 `feature/refactor-base`에 fast-forward 병합하고 베이스·`feature/executor-service-throughput`을 origin에 atomic push했다. 원격 두 구현 SHA 일치를 확인했다. 파생 브랜치는 구현 commit에 보존하고 이 게시 기록은 베이스의 후속 문서 commit에 남긴다. 자동 배포·기존 서비스 재기동은 수행하지 않았다. 원래 checkout HEAD/status/.env/316개 파일과 기존 서비스 환경은 유지한다.
