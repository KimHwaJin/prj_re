# 056 LLM을 제외한 현재 서비스 처리량 분석·개선·설정

| 항목 | 내용 |
|---|---|
| 상태 | 구현·로컬 비교·관련 회귀 완료 / 베이스 병합·origin 게시 확인 |
| 시작일 / 완료일 | 2026-10-03 / 2026-10-03 |
| 브랜치 | feature/service-throughput-tuning |
| 출발 commit | 59d690a2dd07e454443691fb18e34d0829b683fa |
| 배포 | 미배포. 기존 checkout·.env·Compose 유지 |

## 문제와 범위

055까지 실제 기능을 연결했으나 이전 부하 도구는 현재 cookie/CSRF·typed HITL·PlanningRuntime과 맞지 않았다. LLM 호출 횟수/prompt를 변경하지 않고 현재 API/DB/Worker/checkpoint/결과 저장/SSE의 비용과 실행 한도 설정을 비교해야 했다. 모델은 고정 응답과 0/5000ms 지연으로 대체한다. Executor 제출 전 plan_approved까지 측정하며 실제 Executor 이벤트 처리량·메모리 자동 갱신 용량은 이번 대상이 아니다.

## 개선과 실제 변경

1. src/config.py 및 api_service/core/database.py: asyncpg prepared statement cache를 hardcoded0 대신 설정으로 선택한다. 기본0 유지, opt-in100, 범위0~1000. DB 연결별 SQL 실행준비 캐시이며 권한/결과 캐시가 아니다.
2. api_service/services/plan_event_persistence.py: 현재 계획 public events도 이미 획득한 Run barrier를 transaction-local GraphResultBatch로 재사용한다. 기존 lock order, 중복/재전송, log/event/message 원자적 commit·실패 rollback·sequence를 유지한다. 사용자당 Worker advisory lock SQL31→24회.
3. service_auth/sso/runtime.py: 로그인 전용 Redis8연결 상한을 유지하고 BlockingConnectionPool로 제한된시간 대기한다. socket deadline도 유지, owned pool을 runtime이 종료한다. caller-supplied client/Streams BLOCK pool은 그대로다. 실제 포화 회귀는 대기/해제/성공, timeout503, 회복·pool 종료를 확인했다.
4. service_runtime/request_id.py 및 service_bootstrap.py: 기존 request-id HTTP middleware를 pure ASGI로 교체하여 SSE의 불필요한 task/cancellation 경계를 제거한다. 플랫폼/입력 ID 우선·응답 header는 동일하다. 이것만으로 DB 정리 경고는 해결되지 않아 다음 변경을 추가했다.
5. api_service/services/run_stream_service.py: 전체 짧은 SSE frame read를 owned task로 수행하고 DB연결반환 후 취소를 전달한다. pool checkout/SQL await 중 반복취소가 연결을 버리지 않게 한다. auth/generation/cache/cursor/NOTIFY 기능을 유지한다.
6. config.performance.yml 및 [설정 가이드](../service-throughput-settings.md): 한도32·CRUD10/overflow0·checkpoint4·cache100·SSE0.5·loginRedis8 후보 제공. 기존 dev/stg/prd 기본 설정은 자동 변경하지 않는다. 프로세스/Pod별 풀 복제와 잠재 DB예산17개(+Executor/event별pool), 모델/Pod 제약을 설명한다.
7. scripts/benchmarks/service_throughput: 현재 인증·CRUD·계획 편집/승인·SSE harness, SQL/queue/model/CPU/RSS/pool/loop계측, raw 검증·독립 검산기·실패 검증 회귀·재현 안내. loopback 직원 verdict/model fixture만 사용하며 기존 DB를 초기화하지 않고2개 새 UUID DB를 생성/정리한다. 자료는 [상세 근거](../reports/service-throughput-2026-10-03/README.md)·[HTML 보고서](../reports/service-throughput-2026-10-03/report.html)에 남겼다.

Agent source/graph/prompt/skill/tool, Executor, 패키지 의존성, migration 및 API body/response/event 계약을 바꾸지 않는다.

## 측정·검증

주 비교29회, 성공 사용자856건. 1/10/30/50명·한도16/32·모델0/5초, 단일 로컬 API process, CRUDpool10/overflow0, checkpoint1~4. warmup/login은 밖, userthinktime0, 유한 일제 유입. 실제 모델HTTP/create_agent/자동memory비용과 Executor실행은 제외한다. 대부분조건1회이며50명 최종32·0초최종은2회씩 측정했다.

- 동일한도16·모델5초·50명 평균24.130→23.930초(0.8%): 큐15.370→15.368초. 큰 큐 개선으로 포장하지 않는다. 10명은7.438→7.621초로 약간 느렸고30명은12.000→11.004초. 변경 전은 요청 완료했지만 SSE GC 경고가 있어 정상 비용 대조군과 구별한다.
- 한도32 최종50명은13.357/13.433초(평균13.395초), 한도16최종대비44.0%단축. 이는 실행 한도 설정의 효과다. 실제모델32동시수용·운영Pod용량 보장은 아니다.
- 모델0초·50명·한도16 안정화 대조군(cache0)11.733초 → cache100 10.258초 → batch 최종2회평균9.997초. 대조군대비14.8%시간·15.7%API CPU감소. 최종CPU평균0.207초/사용자. advisory31→24, SQL401.02→389.33회/사용자. 반복수 차이·순차측정의 한계를 명시한다.
- CRUD7호출·50명은 안정화대조군0.811→0.623초(23.2%). SQL47.06/사용자로 동일, 각1회. original전체 개선율이 아니다.
- SSE0.1은1명전달을빠르게했으나50명집중흐름을더느리게하여0.5유지. 풀 크기를 슬롯 수만큼 키우지 않는다.
- 원본0초50명 실패시험은45명완료/5HTTPStatusError, 쓰기503과GC경고. success평균에서 제외한다. 에러detail미캡처이므로503전체독점원인단정하지않는다. 최종측정은HTTP성공, owner0/recovery0/retry0, CRUDcheckedout0 및 GC/traceback/error0.
- 관련 SSO/PG/Redis·계획편집·log/event·SSE·동시성·graph수명·project memory·분석검증 **182 passed, 3 warnings, 108.67초**. warnings는 체크포인터 없는 기존fixture의 durability경고다.
- 설정 신규4개를 포함한42개 통과. 최종 middleware/capture/설정 검증54개도 통과했다(1.53초, 중복 합산 금지).
- 현재OpenAPI38개path의전체schema hash가출발소스와동일함을확인했다. Agent/계약/의존성/migration등181개보호파일동일, 변경Python17개AST·Markdown126개링크·새자료의private설정제외를확인했다. 원래checkout HEAD/status/.env/기존316개파일동일, 임시service_perf DB잔여0.
- 결과/압축raw29개에 대한377산술·hash독립검산 통과. 잘못된 Worker/model수·중복·잔여owner·HTTP실패·retry capture 거절 검증.
- HTML canonical report는 validation/package/payload동일성/semantic fallback 구조검증 통과. 설치된Chromium headless가없어 browser/desktop/mobile/source dialog QA는 미실행이다. 별도브라우저를설치하지않았다.

## 제한·후속

로컬모델mock기준의 설정후보다. Podquota/HPA/멀티Pod·modelHTTP·실제Executor/Streams결과·후속답변·auto_context/Store·대용량·초장기수용량까지 완료로 표현하지 않는다. 단일process잠재DB예산과전체replica/다른서비스예산은별개이며 중앙pooler를 새로추가하지않는다. 추가 유의미한 service-only 통합부하와실제Pod제약검증은 [후속 목록](backlog.md)에 남긴다. LLM호출횟수최적화·Registry·Workflow CRUD·광범위 운영 보완은 기존보류다.

## 통합·게시

2026-10-03 구현·검증 commit `2822ed58887803ab9aa822a1b48ef0e31daaf7ae`를 `feature/refactor-base`로 fast-forward 병합하고 베이스·`feature/service-throughput-tuning`을 origin에 atomic push했다. 원격 두 구현 SHA 일치를 확인했다. 파생 브랜치는 구현 commit에 보존하고 이 게시 기록은 베이스의 후속 commit에 남긴다. 배포·기존 서비스 재기동은 하지 않았다. 원래 checkout HEAD/status/.env/기존316개 파일은 동일하다.
