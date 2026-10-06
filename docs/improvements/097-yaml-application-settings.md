# 097 앱 설정을 YAML로 식별·이전

2026-10-06 / feature/yaml-application-settings / 선행096 commit1109112에서 분기.
[현재 실행 안내](../application-configuration.md) · [171개 설정 위치](../application-settings-inventory.md)

## 문제

서비스는 중앙 loader가 있었지만 환경별 YAML이 비어 있고 .env.example·Compose 환경변수·CICD manifest에 앱 설정이 반복돼 있었다. 클론 후 사용자는 어떤 값이 실제로 우선하는지와 migration이 같은 DB를 보는지 별도로 확인해야 했다. 일반 /demo도 .env 입력 중심으로 안내됐다.

## 식별과 변경

- 허용 정식 key171개를 API/Agent/event/SSO/검색 모델과 대조했다. 공통 정책132개, 환경별 실제 연결·모드20개, 선택·파생·호환19개로 분류했다. 이름별 위치·소비 영역·구 별칭·비효력 호환 필드를 별도 목록에 기록했다.
- config.yml에 Worker/DB pool/HITL/메모리/Executor/SSE/검색 정책을 정리했다. 환경별 파일은 실제 주소·모델·SSO·공유 경로를 담당한다. tracked 빈 profile은 주석 있는 example로 대체했다. 실제 profile은 Git·Docker build에서 제외하며 없으면 시작을 거부한다.
- scripts/configure.py의 init과 import-env를 제공한다. 원본 .env를 읽는 행위는 명시적 이전 때만 수행한다. 중앙 loader로 별칭·타입·활성 DB 정합성을 검증하고 원본 보존,0600 원자 저장, 기본 덮어쓰기 금지, false/0 보존을 적용했다. APP_ENV 선택·PYTHON_*·LOCAL_*는 앱 leaf와 구분했다.
- API와 같은 옵션의 scripts/migrate.py로 CRUD/event Alembic→checkpoint setup→공통 command backfill을 조립했다. --check-config는 서버와 같은 summary만 출력하고 접속·DDL을 실행하지 않는다. generic Docker에도 초기화/migration 도구를 포함했다.
- 로컬 helper는 선택 YAML을 같은 loader로 해석해 workspace/config.compose.yml로 생성하고 readonly 마운트한다. DB host/port만 postgres:5432로 변경한다. .env.local에는 인프라 값만 남긴다. 실제 source profile·기존 .env는 변경하지 않는다. APP_ENV selector도 선택 profile대로 전달한다.
- host0600 파일을 UID10001 컨테이너가 읽지 못하는 경계를 고려했다. 로컬 생성본은0640으로 만들고 해당 GID를 컨테이너 보조 그룹에 넣는다. 원본과 인프라 dotenv는0600이다. 외부 Compose는 profile 파일의 그룹 읽기와 APP_CONFIG_GID를 명시한다. 실제 group membership 정책은 배포에서 설정한다.
- generic Kubernetes는 Secret config.prd.yml을 파일로 마운트한다. ConfigMap은 selector만 유지한다. 사내 CICD의 수정 가능한 앱 env는 config.cicd.dev.yml로 옮기고 이미지에 선택 profile로 설치한다. 고정 env/이미지/replica/자원 placeholder는 보존했다. 폐기된 EW_DISPATCH_CONCURRENCY를 제거했다.
- 기존 env로 제어하는 benchmark는 config.diagnostic.yml을 명시한 격리 경로로 유지한다. 정상 배포와 테스트 정책이 서로 가리지 않게 했다. .env.example은 보조 입력만 남겼다. README·demo·bootstrap·Compose·배포 안내를 함께 갱신했다.

## 유지·달라지는 기본값

일반 공통 graph concurrency1·CRUD 최대20·checkpoint 최대4·HNSW 검색 예산을 유지한다. 표준 예제는 기존 .env.example의 INLINE 코드/보고서 제출·300초 Operation/wait·default kernel을 채택한다. dev의 실제 Executor 제출 기본은 false, stg/prd는 true다. 사내 CICD는 기존 port5000·PATH/MANIFEST·TLS값을 유지한다. legacy dotenv 이전은 기존 명시값을 공통/예제 위에 덮어 합치므로 사용자의 명시값을 보존한다. 처리량 향상을 측정·주장하는 작업이 아니다.

YAML > env > 기본값, explicit --config는 하나의 완성 파일만 사용한다는 계약을 유지한다. Secret env를 사용하려면 대응 YAML leaf를 생략한다. Worker DB split을 자동 수정하거나 데이터를 이동하지 않는다. 같은 DB 파생/활성 정합성 계약은060을 유지한다.

## 검증

- 관련 Python199개 통과: 세 환경의 no-dotenv 초기화·false/0·source 우선순위·덮어쓰기 거부·invalid import 시 원본 보존·정식 alias 이전·secret 비노출·동일 app/migration target·schema 준비 순서·Compose 생성 및 dev/prd selector·CICD 고정값·Secret/ConfigMap mount·모델/세션/SSO·demo·진단 모드.
- Node10개 통과: 기존 화면/SSE/HITL 편집·same-origin 동작 유지.
- local/external Compose config --quiet 통과. 실행·build·daemon 변경 없이 정의만 파싱했다.
- 깨끗한 현재 소스의 offline wheel을 생성했다. settings·configuration_files 모듈과 demo HTML이 소스와 일치하고 private profiles/dotenv가 포함되지 않음을 확인했다.
- git diff --check 통과, 실제 profile과 workspace 생성 설정의 Git ignore를 확인했다.
- 초기 회귀에서 cleanup 테스트의 암묵적 로컬 설정 의존을 확인해 명시적 snapshot으로 격리했다. 최종 review에서 ConfigMap selector 구조와 non-dev Compose selector 전달도 수정해 계약 검증에 포함했다.

재현 명령(저장소 루트, 의존성이 설치된 Python):

```sh
PYTHONPATH=src python -m pytest -q -p no:cacheprovider \
  src/api_service/test/test_yaml_configuration_files.py \
  src/api_service/test/test_bootstrap_settings.py \
  src/api_service/test/test_deployment_configuration.py \
  src/api_service/test/test_service_demo_console.py \
  src/api_service/test/test_model_selection.py \
  src/api_service/test/test_session_settings.py \
  src/api_service/test/test_sso_auth.py \
  src/api_service/test/test_sso_swagger_javascript.py \
  scripts/diagnostics/tests/test_console_modes.py
node --test tools/test-console/tests/console.test.cjs
LOCAL_SHARED_INPUT_ROOT=/tmp LOCAL_CONFIG_GID=20 \
  docker compose --env-file .env.local.example -f compose.local.yaml config --quiet
APP_ENV=stg APP_CONFIG_GID=20 \
  docker compose -f compose.external.yaml config --quiet
```

7개 경고는 checkpointer 없는 Agent test double에서 durability 효력이 없다는 기존 안내다. schema launcher 순서는 mock 검증이며 실제 DB DDL 검증으로 계산하지 않는다. 일반 파일 초기화·target 해석은 temporary 파일로 검증했다. 실제 직원/모델 key·DB 비밀번호를 문서·테스트에 복사하지 않았다.

## 남은 범위와 게시

실제 개인 연결값·SSO SDK adapter·embedding endpoint/모델/차원은 환경 담당자가 채워야 한다. 기존 .env·사용자 checkout·실행 중 Docker는 이번 작업에서 변경하지 않았다. 실제 migration·앱 재기동·CICD/Kubernetes 배포·SSO 왕복·LLM/Executor E2E는 수행하지 않았다. ML 의존성 분리·검색 완전 중복 대표화와 기존 후속 범위는 유지한다.

2026-10-06 사용자 요청으로 구현 commit `5fc444b0e3d5032637d0ec732f220e8ffc0e04c6`를 `feature/refactor-base`에 fast-forward 병합했다. 선행096의 `/demo` commit `110911246dad271afac4942a9b6fab18b1bdbfd7`도 함께 포함한다. origin에 베이스를 게시하며 기존 파생 브랜치는 보존한다. 병합으로 구현 변경은 추가되지 않아 기존 Python199·Node10·Compose·wheel 검증 결과를 유지한다. 실제 재기동·migration·배포는 수행하지 않는다.
