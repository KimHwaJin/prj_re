# 단일 YAML 앱 설정

2026-10-06 / 100. API·Agent·Worker는 하나의 ServiceSettings snapshot을 사용한다. 설정 우선순위는 **선택한 YAML > 프로세스 환경변수 > 코드 기본값**이며 공통 파일 병합은 없다. 기존 097의 common+profile 규칙을 대체한다.

## 파일 선택

| 선택 | 읽는 파일 | 준비 방법 |
|---|---|---|
| 기본 / APP_ENV=local | config.yml | config.example.yml 복사 또는 configure.py init |
| APP_ENV=dev | config.dev.yml | config.dev.example.yml 복사 |
| APP_ENV=stg | config.stg.yml | config.stg.example.yml 복사 |
| APP_ENV=prd | config.prd.yml | config.prd.example.yml 복사 |

`--env`가 APP_ENV보다 우선한다. development/staging/production은 dev/stg/prd 별칭이다. Dockerfile의 APP_ENV는 **파일 선택만** 한다. YAML 안의 APP_ENV로 선택을 바꾸지 않는다. `--config` 또는 SERVICE_CONFIG_FILE은 명시한 파일 하나를 사용하며 profile 선택은 namespace 등에 계속 적용된다. 선택 파일이 없으면 오류이고 다른 파일이나 .env로 대체하지 않는다.

## 내부 템플릿에 추가하기

[config.service.example.yml](../config.service.example.yml)의 서비스 전용 블록을 기존 내부 config.dev.yml/config.stg.yml/config.prd.yml에 붙여넣는다. 별도 파일로 자동 include하지 않는다. 이 파일에는 기존 템플릿의 PORT, PRIVATE_LLM_*, RECURSION_LIMIT, ACTIVE_MULTI_TURN, SET_MAX_HISTORY, ACTIVE_TRACE, PHOENIX_ENDPOINT/API_KEY를 중복 선언하지 않았다.

각 환경 파일 하나에 연결값과 정책을 모두 둔다. 키는 최상위 대문자다. false·0·빈 문자열도 명시한 YAML 값이며 env로 덮어쓰지 않는다. Secret env를 사용할 항목은 YAML에서 삭제한다. ${...} 치환·자동 dotenv 탐색·os.environ 변경은 없다. service/group 중첩 구조는 더 이상 읽지 않는다. 진단·벤치마크·마이그레이션 도구도 같은 평탄한 형식을 사용하며, 이전 중첩 파일은 오류 메시지에 따라 최상위 키로 옮긴다.

| 기존 템플릿 설정 | 우리 소비처 |
|---|---|
| PORT | FastAPI port |
| PRIVATE_LLM_MODEL_NAME / ENDPOINT / API_KEY | API·Agent 기본 모델; ENDPOINT는 /v1 포함 base URL 그대로 |
| RECURSION_LIMIT | 사용자 시작/재개, Executor 이벤트/복구 및 내부 create_agent 호출의 단계 한도 |
| ACTIVE_MULTI_TURN | 이전 대화 턴의 모델 전달 여부 |
| SET_MAX_HISTORY | 이전 요청 최대 N턴 + 현재 Run; 기본6, 0~100 |
| ACTIVE_TRACE | standalone Phoenix 활성; 플랫폼 부착에서는 플랫폼 tracing 사용 |
| PHOENIX_ENDPOINT / PHOENIX_API_KEY | standalone collector; 플랫폼에서는 템플릿이 초기화 |

Gaia/Cube/API 출력 flags·DEFAULT_WORKFLOW·SERVICE_ID·A2A·S3·플랫폼 인증/관리자 키는 템플릿 소유로 유지하고 우리 인증·SSE·Workflow 추천에 연결하지 않는다. 플랫폼 키 허용 목록은 없다. 모델에 선언된 우리 설정만 선택하고 나머지는 플랫폼 소유로 남긴다. `--check-config`의 `unused_config_keys`에 미사용 YAML 키 이름을 표시한다. 여기에 플랫폼 키가 나오는 것은 정상이며, 우리 설정이 잘못 적혀 이 목록에 나온 경우 해당 철자를 수정한다. 실제 소비 필드의 타입·범위 오류, 중복 YAML 키, 충돌하는 별칭은 시작 오류다. 오류와 --check-config는 비밀값을 출력하지 않는다.

RECURSION_LIMIT은 한 graph invocation의 super-step 한도다. LLM 횟수·시간 제한·전체 Run 수명 제한이 아니다. HITL/Executor 대기 후 새 호출은 새 단계 예산을 받는다. 내부/외부 그래프 각각 설정 한도를 적용하며 계획 수정·복구·Operation 수 제한은 별도로 유지한다.

대화 턴은 하나의 public Run이다. 같은 Run의 계획 수정/질문 답변은 같은 턴에 들어가고 승인 클릭·내부 tool 메시지는 별도 턴이 아니다. 이전 턴을 자를 때 질문/답변 묶음 전체를 제거한다. ACTIVE_MULTI_TURN=false여도 현재 Run의 피드백과 checkpoint/HITL/Executor 재개, 프로젝트 메모리 및 마지막 분석 근거는 보존한다. 이전 대화 차단은 모델에 전달하는 이력에만 적용한다.

## 로컬 직접 실행

```sh
uv sync --frozen
uv run python scripts/configure.py init --env local
# config.yml의 DB/Redis/LLM/Executor/SSO 연결값 수정
uv run python app.py --check-config
uv run python scripts/migrate.py --check-config
# 새 환경의 schema 준비; 실제 대상 확인 후 실행
uv run python scripts/migrate.py
uv run python app.py
```

config.yml이 이미 있다면 init 없이 편집한다. 생성은 기존 파일을 덮어쓰지 않는다. --overwrite는 명시적인 전체 교체다. --check-config는 설정만 검증하며 연결 성공이나 migration 완료를 보장하지 않는다. 실제 로컬·환경 파일은 Git/일반 Docker build context에서 제외하고 예제만 공유한다. /demo와 /docs는 같은 앱에서 제공한다. 로컬 예제 PORT는8000, 배포 예제는 템플릿과 같은5000이다. SSO origin·프록시·컨테이너 port도 함께 맞춘다.

이전 dotenv는 필요할 때 한 번 `uv run python scripts/configure.py import-env --env local --input .env --output /tmp/config.imported.yml`로 옮긴다. 원본은 변경하지 않는다. 로컬 환경에서는 --local-env-file도 명시적으로 사용할 수 있으며 프로세스 env보다 낮은 순위다. `AGENT_HISTORY_MESSAGE_LIMIT`은 폐기했으므로 먼저 SET_MAX_HISTORY(턴)로 판단해 바꾼다. 메시지 개수를 기계적으로 턴 값으로 복사하지 않는다.

## 배포와 연결 역할

- 로컬 Compose는 scripts/local.py가 **선택한 파일 하나**를 읽고 DB host를 postgres로 바꿔 private workspace/config.compose.yml을 만든다. .env.local은 Compose 인프라 전용이다. APP_ENV 기본은 local이다.
- 외부 Compose는 실제 config.stg.yml을 마운트한다. 예제 컨테이너 port는5000이다. 다른 PORT면 port mapping·healthcheck도 수정한다.
- 일반 Kubernetes 예제는 Secret에 **전체** config.prd.yml을 마운트한다. 해당 예제는 기존 Service에 맞춰 PORT=8000이다. 로컬 config.yml은 병합하지 않는다.
- 사내 CICD는 cicd/basic/dev/config.dev.example.yml을 이미지의 config.dev.yml로 설치한다. 변경 불가 manifest env의 DB/Redis/model key/checkpoint/EW DB는 그 YAML에서 생략해 기존 주입을 유지했다. 실제 템플릿 공급 시 전체 환경 파일로 교체한다. config.cicd.dev.yml이라는 별도 공통/profile 파일은 제거했다.

DATABASE_URL은 CRUD·공통 명령/Inbox·Workflow 검색·프로젝트 Store 대상이다. EW_DATABASE_URL은 보통 생략해 같은 대상에서 파생하며 실행 활성 상태에서 다른 DB는 거절한다. CHECKPOINT_DB_URI는 LangGraph checkpoint 대상이다. DB 주소를 합쳐도 드라이버/수명별 pool을 같은 객체로 만들지는 않는다. REDIS_URL은 Executor Streams와 SSO에 함께 사용하되 namespace/key 영역을 구분한다. Executor 실행 kernel 및 PV 경로는 실제 Executor와 맞춘다.

Embedding은 채팅 모델 설정을 재사용하지 않는다. WORKFLOW_EMBEDDING_BASE_URL/MODEL/DIMENSIONS를 함께 넣고 실제 corpus로 threshold·index를 검증한다. SSO SDK는 비공개이므로 SSO_ADAPTER_FACTORY를 사내에서 연결한다. 미설정은 로그인503이며 테스트 인증으로 바뀌지 않는다. 추가 설정 전체·삭제/별칭 목록은 [설정 분류표](application-settings-inventory.md)를 따른다.

## 설정 코드의 책임

| 파일 | 책임 |
|---|---|
| src/service_settings.py | 환경 선택, 소스 우선순위, 공통 값 전달, 프로세스 snapshot 설치 |
| src/service_runtime/settings_sources.py | YAML/env 읽기, 모델 필드에서 별칭 추출, 타입 검증과 비밀값 없는 오류 |
| src/service_runtime/settings_snapshot.py | API·Agent·Worker·SSO·검색 설정 객체와 안전한 진단 요약 |
| src/service_runtime/runtime_settings.py | 앱 수명·진단·모델 목록 등의 타입·기본값 |
| src/config.py | API 설정 필드·별칭·기본값·검증 |
| src/agent_config.py | Agent 설정 필드·별칭·기본값·검증; 입력은 native typed value |
| src/event_worker_settings.py | Executor 이벤트 Worker 필드·검증 |
| src/service_auth/sso/settings.py | SSO 필드·검증 |
| src/service_runtime/workflow_search_settings.py | 임베딩·HNSW 검색 필드·검증 |
| src/service_runtime/settings_migrations.py | 폐기한 우리 설정에 대한 이행 오류 안내 |

새 설정은 해당 모델에 필드를 선언하면 된다. `AGENT_KEYS`, `EXTRA_KEYS`, `GROUPS`, 플랫폼 허용 목록과 별도 별칭 테이블에 추가하는 과정은 없다. 기본 이름은 대문자 필드명이며 Worker·SSO·검색에는 해당 접두어를 붙인다. 특별한 키나 이전 이름은 그 필드의 `validation_alias`로 선언한다. 환경변수 컬렉션만 JSON으로 해석하고 YAML의 dict/list/bool/int는 문자열로 변환하지 않는다.
