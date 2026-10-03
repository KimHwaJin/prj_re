# Checkpoint 저장 비용과 병렬 접근 대조 — 2026-10-04 KST

**같은 pool 상한 4에서 Saver 공통 잠금 대기를 줄였다. 50명 평균 E2E는 56.39→54.78초(2.86% 단축), 저장 함수 누계는 사용자당 3.10→1.26초(59.31% 감소)였다. 저장량은 줄이지 않았다.** 처리량에 영향을 주는 제한 하나를 제거한 로컬 결과이며 전체 지연 해결 또는 운영 용량 확정은 아니다.

## 실제 문제와 변경

기존 `AsyncPostgresSaver` 3.1.2는 pool을 받아도 같은 인스턴스의 `_cursor()`를 `self.lock`으로 직렬화한다. 50명 보조 시험의 함수 시간 누계 중 89.82%가 이 잠금 대기였다. 관측한 잠금 안 최대 동시 진입은 1이었다. pool max4지만 checkpoint pool 실제 size는 1이었다.

`PooledAsyncPostgresSaver`는 같은 lifespan pool과 serializer를 공유하고, **입출력 호출별로 짧게 살아 있는 공식 Saver 객체**에 공개 메서드를 위임한다. 각 Saver의 connection/pipeline 잠금과 SQL은 그대로다. 새 pool이나 새 SQL 구현을 만들지 않는다. 동시 연결은 기존 max_size 이하이며 동일 session의 writer 직렬화는 기존 Command/Session ownership이 담당한다. `setup()`은 기존처럼 서비스 시작 전에 실행한다. `durability="sync"`, receipt·승인 원문·과거 checkpoint는 유지한다.

## 측정 조건과 경계

- 전 runtime: `45530e6`; 후 runtime: `a6212c2`. 현재 분석 Agent 소스 99개 hash 동일. 변경은 saver factory·adapter·그 테스트뿐이다. dependency/prompt/모델 호출 수/API/그래프 순서는 바꾸지 않았다.
- 1프로세스, 총 실행 자리20, CRUD10/overflow0·checkpoint4·Store2·event4·bridge4. cancel0.25초·claim0.25초·SSE fallback0.5초. 전용 PG17.11/Redis7.4.11, 동일 Docker/호스트, CPU quota 없음. 기존 서비스 보존.
- 모든 측정 모델콜5초. 정상 분석4콜(계획 선택/작성·review·report), 후속 질문2개 추가 시6콜. 실제 inference와 Executor Python/학습은 fixture다. 실제 API/SSO cookie·CSRF/DB/checkpoint/Store/create_agent/middleware/Executor HTTP/manifest/Redis/Worker/SSE를 통과했다.
- 기본 1/10/30/50명, 50명 각3회. 전후 각8회·202 사용자 시나리오(후속1/10명 포함), 별도 전 잠금2회·51명. **18회·455 사용자 시나리오 전부 성공**. delay0 smoke1회는 이 모집단에서 제외했다.
- 프로젝트 생성→계획 편집/승인→MULTI submit/continue/finalize→결과 재개→리포트 success SSE까지. 로그인·등록·warmup·기동/종료·Artifact POST 등록은 제외했다. 후속은 같은 session의 근거 설명/보고서 설명이며 파일 재등록이 아니다.
- 유한 burst·생각 시간0. 이전 구조→공통 Worker 효과를 재비교한 시험이 아니다. 이번 전후 모두 공통 Worker다. baseline 전체를 먼저, candidate 전체를 나중에 측정했다. 다른 호스트 부하와 순서 효과를 완전히 통제하지 못했다. 큰 운영 효과/통계적 유의성/HPA 수치로 일반화하지 않는다.

## 전체 사용자 시간

| 사용자 | 전 평균 초 | 후 평균 초 | 평균 단축 | 전/후 반복 |
|---:|---:|---:|---:|---:|
| 1 | 22.36 | 22.32 | 0.18% | 1 / 1 |
| 10 | 24.68 | 24.17 | 2.09% | 1 / 1 |
| 30 | 34.40 | 33.77 | 1.84% | 1 / 1 |
| 50 | 56.39 | 54.78 | 2.86% | 3 / 3 |

50명 batch makespan은 64.27→61.71초였다. 3개 trial 평균의 표본 표준편차는 0.276/0.015초다. 반복 p95는 `results.json`에 각각 남겼고 합친 모집단 p95라고 하지 않는다. 1명 개선은 미미하다.

| 분석+후속 질문2개 | 전 평균 초 | 후 평균 초 | 변화 |
|---:|---:|---:|---:|
| 1 | 33.84 | 33.85 | +0.01초 |
| 10 | 36.06 | 36.43 | +0.37초 |

후속 질문은 각1회다. 10명에서 더 빨랐다고 주장하지 않는다. Store 자동 선호 갱신·다음 질문의 실제 메모리/완료 분석 근거 참조는 전후 모두 검증했다.

## 저장 함수 비용과 자원

| 50명·각3회·사용자당 누계 | 전 | 후 |
|---|---:|---:|
| 저장 함수 외부 계측(aput+aput_writes, ms) | 3103.53 | 1262.91 |
| 공식 Saver 읽기 함수 내부 계측(aget_tuple, ms) | 912.23 | 250.46 |
| blob/중간 쓰기 직렬화 작업(ms) | 1.247 | 1.197 |
| API CPU(초) | 0.4963 | 0.4920 |
| CRUD SQL 회수 | 611.86 | 604.37 |

저장 외부 계측은 기존 Trace가 새 Saver 객체 생성까지 포함한다. 함수별 내부 probe와 외부 호출 수가 정확히 일치하는지도 검사했다. 읽기 내부 probe는 후 버전의 adapter 객체 생성 전에 있는 시간이 포함되지 않으며 E2E에는 포함된다. 함수 누계는 작업끼리 겹치므로 E2E에서 그대로 빼거나 critical path 비율로 해석하지 않는다. DB query만의 실행 시간도 아니다.

직렬화는 실제 `_dump_blobs`/`_dump_writes`가 만든 bytes를 관측하고 재직렬화하지 않았다. JSONB 인코딩·읽기 decode·executor 파일 읽기는 이 수치에 포함되지 않는다. 현재 출력 크기의 msgpack 직렬화 자체는 주 병목이 아니었다.

실제 checkpoint pool size는 전1/후4, 설정 상한은 전후4였다. 후에는 pool 대기도 발생한다. 실제 동시 연결·DB 활동이 증가하는 비용은 있으며 pool 상한 확대나 전역 DB 연결 보장으로 해석하지 않는다. `psycopg_pools` stats는 warmup 포함 누계이고 `usage_ms`는 checkout 동안의 작업/대기 시간으로 DB CPU와 다르다. API CPU와 CRUD asyncpg SQL만 계측하며 DB/Redis/모델/mock CPU와 전체 psycopg SQL 회수는 측정하지 않았다.

## 저장량: 최신 상태와 과거 기록을 분리

기본 흐름은 thread당 checkpoint30개·blob83개·write225개, Saver aput30회·aput_writes34회·aget_tuple21회다. blob key(thread, namespace, channel, version)를 한번만 계산해 여러 checkpoint가 같은 버전을 참조하는 것을 중복 집계하지 않았다.

| 기본 50명 첫 전 trial·사용자당 논리 payload | KiB |
|---|---:|
| checkpoint_json_bytes | 416.65 |
| metadata_json_bytes | 1.31 |
| blob_bytes | 122.61 |
| write_bytes | 135.73 |

50명 각3회의 사용자당 합계는 전 676.42/후 676.21KiB였다. UUID·버전 문자열 길이 등의 작은 차이를 저장량 최적화 성과로 주장하지 않는다. 최신 checkpoint의 JSON+참조 blob은 약64.70KiB다. 최신 연결 write는 별도 필드이며 이 값은 현재 state의 Python heap이나 pending writes를 포함한 전체 decode 메모리가 아니다.

| 주요 channel·기본 사용자당 | blob 버전 | blob KiB | write KiB |
|---|---:|---:|---:|
| public_events | 17 | 28.48 | 28.48 |
| reviews | 4 | 22.57 | 22.57 |
| execution_command | 2 | 17.82 | 17.82 |
| approved_snapshot | 1 | 17.46 | 17.46 |
| interaction_data | 2 | 7.96 | 7.96 |
| plan_views | 3 | 7.35 | 7.35 |
| final_response | 2 | 6.08 | 6.08 |

checkpoint JSON에서는 `versions_seen`과 `channel_versions`의 구성값이 전체 논리 payload의 약52.25%였다(전 50명 별도 잠금 trial). 사용자 분석 출력 자체만 큰 것은 아니다. 이 값은 JSON 구성값 크기이고 JSON key/구두점 비용은 별도이며 원문 JSON 전체 길이와 정확히 합쳐지는 항목은 아니다. 복구용 metadata를 임의로 삭제하지 않았다.

후속 질문2개를 거치면 같은 thread의 checkpoint는30→38개, 저장 누계는 약676→853KiB로 증가했지만 최신 JSON+blob은 약32KiB로 작아졌다. **state reset은 과거 버전을 삭제하지 않는다.** 실행이 없는 후속 답변에서도 이전 `execution_command` blob 8,045bytes가 최신 버전으로 남는 정리 후보를 확인했다. 현재 성능 변경은 이를 제거하지 않았다.

논리 payload 정의는 `octet_length(checkpoint::text)+octet_length(metadata::text)+octet_length(blob)+octet_length(write.blob)`이다. 행 키·index/WAL/페이지/TOAST·Python heap·네트워크 바이트를 포함하지 않는다. PostgreSQL 압축 후 `pg_column_size`와 테이블 실제 용량은 원본에 따로 남겼고 물리 테이블 수치는 warmup도 포함한다. phase별 blob은 최초 참조 checkpoint, write는 연결 parent checkpoint에 귀속한다. 이것은 실행 node의 시간 단계와 같지 않으며 `phases`를 node 비용으로 해석하지 않는다.

## 기능·근거 검증

- 18회 전부: HTTP 오류·누락/중복 성공·슬롯 초과 0, 실패한 trial을 결과에서 제외한 경우 0, 모든 Command DONE·한 번의 성공 event handler·4개 관찰·최종 finalize·report ready. 종료 owner/recovery/Inbox/Outbox/CRUD checkout0.
- 저장 행·부모 연결·고유 key·saver 호출 count·serialize bytes/저장 bytes 대조. 원문/gzip SHA와 독립 합계·평균 재계산. 분석 Agent 소스99개 hash 동일.
- 기본 PostgreSQL 회귀55 passed, 설정 부족으로2 skipped. 추가 격리 API 설정/Redis를 마련한 후31 passed·skip0(기본 pool 테스트2개 중복 포함). 앞서 건너뛴 decision/repair API도 이 추가 실행에서 통과했다.
- 추가 실제 PostgreSQL repair1회: repair HITL 후보 저장→pool 종료/재생성→승인→성공한 load 재실행 없이 continue→finalize→중복 receipt 재전달→report·history 조회. 1thread checkpoint29개, 논리 payload559.69KiB. deterministic3개 Tool/역할 mock/로컬 Executor double을 사용한 기능·저장 증거다. HTTP/Redis/SSE/5초 모델의 repair 처리량 모집단에 합치지 않는다.
- 성능 측정 후 `alist()`를 중간에 닫으면 내부 iterator와 연결을 즉시 반환하도록 보완했다. 관련 실제 PG/비동기 회귀 6 passed는 `history-close-regression.txt`에 있다. 측정 runtime은 위 `a6212c2`이며, 이후 보완은 이력 iterator 종료에 한정된다. 측정한 `aget_tuple/aput/aput_writes`는 변경하지 않았고 성능 모집단을 다시 측정하지 않았다.
- 원본 손상 음성 검증은 `capture-validation-tests.txt`: 21 passed, normal capture에 hold 증거가 없어 적용되지 않는 2 skipped. 두 hold 검사기는 이전 062의 `raw/result_burst-10-1-common.json.gz`로 별도 확인해 2 passed였다(`prior-burst-validator-checks.txt`). 이 이전 원본을 이번 연구의 새 hold 성능 증거로 합치지 않는다.
- 실제 자원 정리는 `final-cleanup.json`을 따른다.

## 남은 범위와 다음 선택

복구 기록을 삭제하거나 함수 원문을 hash 참조로 바꾸지 않았다. 장기·다단계/반복 repair의 전체 HTTP 저장량과 처리량, 실제 모델·Executor·Jupyter·PVC/MinIO·1주 작업·Kubernetes/HPA/여러 Pod·quota·지속 유입·오래된 원장은 별도다. 기본 max4가 모든 DB/Pod에 최적이라는 판단도 하지 않는다.

다음은 실행 종료/새 요청 시 남는 state 필드의 수명과 역할별 상태 타입을 정리할 범위를 확정한다. 큰 JSON version metadata는 공식 node input/state 경계로 줄일 수 있는지 먼저 측정하며, 임의 pruning/durability 완화/append reducer 전환을 용량 해법으로 택하지 않는다. 모델 호출 수·prompt·Dataset Registry 실연계·Workflow CRUD·광범위 운영 보완은 계속 보류한다.

## 파일 안내

- `results.json`, `rollup.json`, `raw/`, `manifest.json`, `verification.json`: 전 버전8회와 잠금2회. `after/` 안 같은 파일: 후보8회. `comparison.json`: 전후 요약/소스 audit.
- `repair-checkpoint-rows.json`, `repair-validation.json`: 실제 PG repair 기능·저장 probe. 비교 시간에 합치지 않는다.
- `fixture-source-main/`: 첫 정상 측정에서 실제 쓴 runner/server/probe/model 코드. 이후 잠금 wrapper와 JSON 구성값 query를 추가한 최종 benchmark source hash는 `fixture-source-sha256.json`이다. 보조 계측은 별도 모집단이며 기본 시나리오 동작은 동일하다.
- `regression-final.txt/xml`, `history-close-regression.txt`, `capture-validation-tests.txt`, `prior-burst-validator-checks.txt`, `cleanup-*`, `final-cleanup.json`: 검사/정리 근거.
- `final-verification.json`: 18개 원본 재분석·해시·분석 Agent 99개 소스 동일성·비교 수치·문서 링크의 최종 검산.
- SQL은 각 raw의 `checkpoint_profile.sql`, 재현 옵션은 `scripts/benchmarks/worker_e2e/README.md`. 기존 서비스/원래 checkout/설정 파일을 바꾸지 않았다. 이 브랜치는 베이스 미병합·미푸시·미배포다.
