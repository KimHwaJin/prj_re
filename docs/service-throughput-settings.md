# 서비스 처리량 설정과 적용

LLM 호출 횟수·prompt·분석 graph·Executor 계약은 유지한다. 현재 cookie/CSRF,
CRUD, Run 큐·Worker·checkpoint·결과 저장·SSE를 고정 mock 모델로 측정한
서비스 자체의 설정 후보다. 실제 모델 수용량이나 Kubernetes Pod 한계를
확정한 운영 용량 설정으로 해석하지 않는다.

[설정 파일](../config.performance.yml)은 opt-in이며 기존 dev/stg/prd 설정과
운영 기본값을 자동 변경하지 않는다. 선택한 환경 파일의 `service` 아래에
필요한 항목을 병합하거나 다음처럼 명시적으로 선택한다.

```sh
APP_ENV=dev SERVICE_CONFIG_FILE=/absolute/project/config.performance.yml python app.py --check-config
APP_ENV=dev SERVICE_CONFIG_FILE=/absolute/project/config.performance.yml python app.py
```

기존 DATABASE_URL/CHECKPOINT_DB_URI, SSO origin·사내 SDK, MODEL/Executor와
데이터 설정은 배포 환경에서 제공해야 한다. 자동 `.env` 로딩은 없다.
YAML > env > 기본값 순서라 환경변수만 바꾸어 YAML 값을 덮어쓸 수 없다.
설정 선택/서버 실행 명령은 로컬 진입점 기준이며 실제 Gaia 템플릿은 별도
통합 검증 대상이다. `--check-config`는 외부 서비스 접속·배포 검증이 아니다.

| 항목 | 후보 | 의미 |
|---|---:|---|
| API 프로세스 | 1 | Run 슬롯과 DB 풀 복제 비용을 피함. 이번에 여러 프로세스 재비교하지 않음 |
| Agent command concurrency | 32 | 사용자 시작·승인·Executor 결과를 합친 프로세스별 graph 총한도. HITL/Executor 대기는 슬롯을 점유하지 않음 |
| CRUD pool / overflow | 10 / 0 | 실제 DB 연결 상한을 제한. Run 32개가 연결 32개를 계속 들고 있는 구조가 아님 |
| asyncpg statement cache | 100 | 연결당 prepared statement cache. 코드에서 0으로 고정하던 것을 설정으로 선택 |
| checkpoint pool | 1~4 | 슬롯 수만큼 키우지 않음 |
| SSE coalescing | 0.5초 | SQL polling 주기가 아니라 변경 후 조회·전달 합침 간격 |
| queue/cancel checks | 각각 0.25초 | 취소 감지 지연·유휴 SQL을 유지 |
| 로그인 Redis pool | 8 | 풀 포화 시 최대 설정 시간만 기다림. Streams BLOCK pool과 분리 유지 |

statement cache는 API의 asyncpg CRUD 연결에만 적용하며 checkpointer,
공식 Store와 Executor의 psycopg 풀은 별개다. 기본값은 호환을 위해 0이고
이 성능 profile만 100을 명시한다. 캐시는 요청별 결과나 사용자 권한을
저장하는 캐시가 아니며 SQL 수·트랜잭션·소유권 검사를 생략하지 않는다.

057 측정 당시 Agent32/Event4는 별도 자리였다. 060 현재 profile은 **전체 graph32**와 Event ingress4·EW pool4·periodic0.2/idle2초를 사용한다. 과거 합계36의 측정치를 공통32의 검증 결과로 사용하지 않는다. Event 수신은 AGENT_WORKER_ENABLED를 기본으로 따르며 EVENT_WORKER_ENABLED로 명시적으로 끌 수 있다. [공통 Worker 설정·이행](agent-command-worker.md)을 따른다.
Executor 제출 bridge와 Event Worker는 별도 풀 객체이며 EW_POOL_SIZE를 각각 적용한다.
[Executor 연계 측정](reports/executor-throughput-2026-10-03/README.md)을 참고한다.

이 profile의 잠재 최대 DB 연결은 CRUD10 + checkpoint4 + official Store2
+ SSE LISTEN1 + Event4 + bridge4 =25개/프로세스다. 활성화/수요에 따라 실제
연결은 다르며 checkout을 반환해도 idle physical connection은 남을 수 있다.
056은 Executor/Store 문맥 경로를 사용하지 않았고057은 Event/bridge를 사용하되
memory 모델 문맥/자동 갱신을 포화시키지 않았다. profile을 선택하지 않은 기본
EW_POOL_SIZE는 별도 값이므로25를 모든 설정의 최대치로 해석하지 않는다.
여러 DB URL이 같은 PostgreSQL 인스턴스라면 모두 합산하며 replica/프로세스
증가로 전체 pool이 복제된다. pooler 없는 전체 DB연결의 강한 상한은 미보장이다.

32는 시험한 후보이지 무제한 처리 보장이나 실제 모델 동시 요청 권한이
아니다. 실제 모델의 허용량·Pod CPU/memory 제한과 DB 전체 예산에 맞춰
16 또는 32를 선택해야 한다. 모델 호출 한도 분리 개발은 이번 범위에서
추가하지 않았다. 자체 서비스 수용량과 외부 모델 수용량을 혼동하지 않는다.
