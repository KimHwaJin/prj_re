# 058 단일 프로젝트 배포와 공통 설정

| 항목 | 내용 |
|---|---|
| 상태 | 구현·관련 회귀·격리 Docker 검증 완료 / 베이스 미병합·미배포 |
| 시작일 / 완료일 | 2026-10-03 / 2026-10-03 |
| 브랜치 | feature/deployment-config-unification |
| 기준 | 353f7a8 + d34ed7d 리뷰 확정·구현 계획 |
| 관련 합의 | D-01/D-02, 다음은 D-03/D-04/D-05 |

## 문제

하나의 bootstrap은 있었지만 실제 배포 예제는 다른 방식을 사용했다. 로컬은 Uvicorn4개 프로세스와 별도 이벤트 Worker, CICD는 같은 Pod 안의 두 컨테이너, 범용 Kubernetes는 두 Deployment였다. direct Uvicorn은 app.py의 조기 SIGTERM drain을 우회했다.

dev YAML은 실행기를 꺼두고 stg/prd는 내장 이벤트 수신과 Executor 제출을 꺼두었다. env true를 넣어도 YAML 우선순위 때문에 반영되지 않았다. 공통 YAML의 port8000은 CICD의 port5000을 가렸다. 부하테스트는 허용되지 않는 APP_ENV=loadtest였다.

Redis·Executor·checkpoint 주소와 group 이름이 예제에서 반복되며 같은 checkpoint 별칭도 query가 달랐다. 고정 EW_INSTANCE_ID가 replica마다 복제될 수 있었다. Worker DB를 API DSN으로 전달하면 asyncpg driver 표기가 psycopg에 그대로 전달되는 경로도 있었다.

CICD에는 잘못된 Docker ENV 구문·오래된 설치 목록·manifest 필드 오타와 구 Ingress API가 있었다. Compose가 참조한 init-databases.sql은 저장소에 누락돼 빈 DB에서는 디렉터리로 mount되어 초기화가 실패했다. 기존 볼륨에서는 드러나지 않았다.

## 변경

- 기동을 app.py 한 컨테이너·한 프로세스로 통일했다. Run Worker, 이벤트 ingress/dispatch, reconciler를 같은 lifespan에서 소유한다. 기존 내장 모드의 공용 graph/checkpointer 경로를 배포 정본으로 사용한다.
- 별도 Worker 배포/sidecar/init container 예제를 제거했다. schema는 서버와 같은 중앙 설정으로 배포 전에 준비한다. 자동 reset은 없다.
- 프로필의 hardcoded false와 공통 hardcoded port를 제거해 설정을 가리지 않도록 했다. YAML > env > 기본값은 유지한다. 이벤트 수신 기본은 Run Worker 활성 여부를 따른다. 실제 제출은 독립 flag로 유지한다.
- REDIS_URL, EXECUTOR_BASE_URL, CHECKPOINT_DB_URI를 공통 주소 정본으로 정했다. EW_REDIS_URL/EW_EXECUTOR_BASE_URL/AGENT_CHECKPOINT_DATABASE_URL은 같은 값만 허용하는 별칭이다. 다른 값을 묵인하지 않는다.
- 기존 EW_DATABASE_URL/WORKFLOW_DATABASE_URL은 DB 자료 이전 없이 override로 보존한다. 공통 API URL의 이벤트 파생 DSN은 psycopg 표기로 변환한다. 서로 다른 DB를 임의로 합치지 않았다.
- group 이름은 namespace에서 파생하며 고정 instance ID는 prefix + startup UUID로 사용한다. 신규 예제에 구 주소·고정 ID를 반복 입력하지 않는다.
- /service/ready는 실제 이벤트 소비자의 준비와 이벤트 DB/Redis, 활성 API 실행기의 tasks 테이블을 확인한다. /service/live와 구분한다. Worker metrics는 /service/metrics로 공용 노출한다. 별도8011 HTTP는 기본 비활성이다.
- 종료 유예70초를 drain20 + 관찰25에 맞췄다. preStop 고정 sleep을 제거했다. port/probe/Service와 Ingress schema·오타를 맞췄다.
- dotenv 직접 의존성을 선언하고 기존 lock에서 runtime requirements를 생성했다. uv.lock의 기존 패키지 버전은 유지했다. 구 requirements의 별도·오래된 설치 목록은 현재 lock 기준으로 교체했다. root Docker는 frozen uv sync, 사내 Docker는 build 시 lock-derived requirements 설치다.
- 로컬 .env는 유지하고 정본 값으로 변환한 .env.local 하나를 생성한다. 업데이트 전에 같은 Compose 프로젝트의 구 event-worker를 drain/제거해 orphan을 남기지 않는다. Redis host를 redis로 선택하면 로컬 Redis도 준비한다. 초기화 SQL을 저장소에 추가했다.

## 검증

[원본 결과와 재현 방법](../reports/deployment-config-2026-10-03/README.md).

| 확인 | 결과 |
|---|---|
| settings/bootstrap/종료/shared graph/package 경계/Redis 실행 경계/wakeup/Store 회귀 | 105개 통과,6.04초 |
| uv lock --check --offline | 성공, 기존 package 버전 유지 |
| root Dockerfile 최종 이미지 | 빌드 성공, 검증 전용 tag 사용 |
| local/external/loadtest Compose config | 3개 성공, API·migration 정본 주소 일치 |
| 공식 Kubernetes1.34.1 strict schema | 8개 document 성공, CI placeholder는 검사 fixture로 치환 |
| 공통 DB 파생·기존 분리 DB | 둘 다 빈 DB migration 이후 app.py 한 프로세스 기동 |
| health/ready/live/OpenAPI·통합 metrics | 모두200, Run 계약 경로 존재 |
| cookie 없는 X-User-Id 호출 | 401, SSO 우회 없음 |
| 격리 Redis 중단→복구 | ready503/live200 → ready200 |
| idle SIGTERM | 공통 DB1.789초 / 분리 DB1.596초, exit0 |

SIGTERM 숫자는1초 drain/5초 관찰의 격리 idle 검사다. 운영 기본45초 예산이나 처리량 개선 수치가 아니다. 장기 실행 중 종료는 기존 관련 회귀의 유예/취소/정리 테스트로 검증했다.

## 제한과 다음 작업

사내 base 이미지·SDK·실제 Gaia core·사내 망·PV·CI stage·Kubernetes 정책/버전은 실제 배포 검증이 필요하다. kind kubectl 검증은 만료 인증서로 실패했으므로 공식 schema 검사와 구분해 기록했다. 사내 manifest의 PATH/MANIFEST 사용에는 Executor와 같은 공유 PV mount가 실제로 제공돼야 한다. 현재 사내 예제의 timezone mount만으로 공유 PV가 준비된 것은 아니다.

기존 실행 컨테이너, 원본 checkout/.env, 실제 DB 자료는 변경하지 않았다. 재배포 시 이전 Worker 종료와 schema 사전 준비가 필요하다. 기존 Locust/접수 HTTP mock을 최신 SSO·완료 E2E와 동일하다고 주장하지 않는다.

기존4프로세스에서1프로세스로 바꾸면 같은 per-process 동시성의 총 Run 한도는 줄어든다. 총한도를 유지할 때는 명시적으로 새 동시성을 정하고 검증해야 한다. 기본값 전환을 처리량 향상으로 주장하지 않는다.

이번 작업은 배포·설정 정합성이다. 논리적 실행기는 아직 둘이며 Run/dispatch 슬롯과 pool은 별개다. 공통 DB 명령 원장·총 실행 한도 통합, 실제 데이터 이전과 성능 개선은 미구현이다. 다음에는 Run 접수/실행/취소와 공통 GraphInvocation·상태 반영을 분리한다.
