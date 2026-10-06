# 기능 테스트 콘솔

[demo.html](../../src/api_service/static/demo.html)은 외부 CSS·JavaScript·CDN·프론트 빌드가 필요 없는 독립 HTML이다. 파일을 브라우저에서 직접 열면 샘플로 모든 화면과 조작을 확인한다. 실제 서비스는 API 연결로 시험하며 SSO 쿠키·CSRF와 현재 Runs/SSE 계약을 사용한다. 기존 demo.html은 재사용하거나 수정하지 않았다.

## 일반 서비스에서 열기 — 현재 권장

새 HTML은 `src/api_service/static/demo.html` 한 파일로 관리하고 서비스 패키지에 포함한다. `app.py`로 기존 API·Agent Worker·Executor 이벤트 수신을 시작한 뒤 `/demo`에 접속하면 같은 서버의 API에 자동 연결한다. 별도 HTML 서버·임시 DB·JSON 설정은 필요 없다.

```sh
uv sync --frozen
uv run python scripts/configure.py init --env dev
# 실제 config.dev.yml 수정·DB schema 준비 후
uv run python app.py --env dev
# 기본 주소: http://127.0.0.1:8000/demo
```

PostgreSQL·Redis·Executor·LLM과 Worker 설정은 기존 중앙 설정을 사용한다. [클론 후 로컬 실행 안내](../../docs/service-demo-console.md)를 따른다. 일반 서비스는 사내 SSO 연결을 그대로 사용하며 테스트 직원을 설치하거나 관리자로 로그인시키지 않는다. 사내 SDK adapter가 없으면 실제 로그인은503이다. 아래 별도 진단 도구는 고정 응답/직원 검증 대체 등 격리 실험이 필요할 때만 사용한다.

## 이전 로컬 진단 인스턴스

현재 실제 모델 콘솔: http://127.0.0.1:18102/test-console . **테스트 로그인 + 실제 qwen38-27b-nvfp4 + 실제 Executor** 조합이다. SSO 로그인 버튼은 직원 검증만 로컬 테스트 계정으로 대체한다. 로그인 쿠키·CSRF·사용자/기본 프로젝트 생성·API·PostgreSQL/checkpoint/Store·Agent/Event Worker·Redis·Executor/Jupyter는 실제 구현을 사용한다. 모델 응답은 고정하지 않는다. 화면 상단에서 로그인·모델·Executor 모드를 각각 확인할 수 있다.

이 서버는 전용 임시 DB53603·Workflow 저장소와 Redis consumer group/키 namespace를 사용한다. 종료하면 서버가 소유한 임시 자원은 제거하고 기존 Executor 이력·Compose 서비스·원천 Parquet·공유 이벤트 Stream은 유지한다. 재시작하면 새 DB/사용자로 시작한다. 기존18100/18101 고정 모델 콘솔을 실제 모델로 전환한 것은 아니다.

## 화면별 기능

| 화면 | 제공 기능 |
|---|---|
| 분석 대화 | 프로젝트/세션 선택·페이지 이동, 세션 생성/이름/삭제, 새 Run, 모델 alias, GET/POST SSE, 진행·중간/답변 메시지, 연결 해제·재접속, 취소 요청, 동일 key/body 재전송 |
| 우측 승인 | 여러 계획 선택, Skill/함수명/설명, 선언된 입력·Tool 파라미터 편집, 도구 제외, 실행 mode·오류 수정 수준·횟수, 재작성, 추가 질문·결정값·수정 승인/거절 |
| 결과 | 실제 관찰, Markdown, 승인 계획과 사용자 제외, 실행 중 skip, report Artifact 보류, 현재 Markdown의 브라우저 로컬 저장 |
| 프로젝트 | 목록/상세, 이름과 system_prompt PATCH, 기본 프로젝트 삭제 제한, 단일 project_memory GET/PUT/DELETE·version 충돌 시 편집 내용 보존 |
| 실행 진단 | diagnostics·invocations·logs 페이지 조회/필터, 최근300 SSE 이벤트, 최근80 HTTP 기록 |
| 전체 API | 현재 서버 OpenAPI의 업무 operation 선택, 경로/JSON/schema/Idempotency-Key, 원본 응답. User·Workflow·Message 관리 등 전용 UI가 없는 기능도 직접 호출 |

샘플에는 현재 전체 OpenAPI48operations 중 업무 prefix45개가 들어 있다. 실제 모드에서는 OpenAPI를 다시 읽으므로 API 변경을 정적인 메뉴로 숨기지 않는다. 관리 사용자 API는 서버의 admin 권한을 따른다. 일반 사용자는403이며, 임시 관리자 옵션은 테스트 환경에서만 제공한다. 건강 상태3개 endpoint는 업무 요청 목록에 포함하지 않는다. SSE는 대화 화면에서 시험하고 SSO 로그인은 브라우저 이동으로 처리한다.

계획 화면은 Tool Registry의 사용자 파라미터 정책을 따른다. 허용한 선택 인자가 계획에서 생략되면 서버가 실제 함수의 JSON 기본값으로 보충한다. 예: compute_statistics.columns=null을 표시하고 컬럼 배열로 수정할 수 있다. Agent 설정값·함수 기본값·사용자 수정값·미확정을 구분한다. 모든 Python 인자를 자동 노출하지 않으며 서버 PlanView가 editable로 허용한 값만 수정한다. step reference/system context는 읽기 전용이며 실행 소스를 표시하지 않는다. 값0·false·JSON null과 미입력을 구분하고 정교한 JSON Schema/의존성/정책 검증은 API가 담당한다. 409/422는 화면에 표시한다. 승인 계획의 사용자 제외 목록과 최종 skipped_steps를 구분한다.

세션 availability.allowed_actions가 send_message면 새 입력을, respond_to_interaction이면 현재 승인 응답을 허용한다. GET은 예약이 아니므로 POST409를 받으면 최신 상태를 다시 읽는다. SSE는 sequence로 중복을 제거하고 재접속 cursor를 사용하며 snapshot.cursor로 이미 처리했다고 간주하지 않는다. Session GET을 상시 폴링하지 않고 상태 변화/완료 시 짧은 제한된 확인만 한다. 상태 복원에는 project/session/run ID만 sessionStorage에 보관하고 인증·재개 토큰은 저장하지 않는다.

## 로컬 실행 방법

현재 브랜치의 레포 루트에서 의존성이 설치된 Python을 사용한다. 이 worktree에는 별도 .venv가 없으므로 현재 머신에서는 아래 Python 경로를 쓴다. 기존 Redis6379·Executor8000·Jupyter default kernel과 default-nce Parquet를 사용한다.

```sh
cd /Users/a10054/.codex/worktrees/refactor-bootstrap/dtest-agent
PYTHONPATH=src /Users/a10054/SKAX_PROJECT/dtest-agent/.venv/bin/python \
  scripts/diagnostics/serve_test_console.py \
  --test-login --temporary-db --fixture-admin --model real \
  --model-env /Users/a10054/SKAX_PROJECT/dtest-agent/.env \
  --executor real \
  --executor-shared-root /Users/a10054/SKAX_PROJECT/executor/shared_dir \
  --port 18102 --db-port 53603
```

아래 진단의 `--model-env`는 기존 private model dotenv를 명시적으로 가져오는 호환 옵션이며 일반 앱 설정 방식이 아니다. 새 .env.example은 모델 전체 예제가 아니므로 진단용 실제 모델 값은 따로 제공한다.

`--model-env`는 MODEL_NAME/API_BASE_URL/MODEL_API_KEY와 모델 timeout/retry/temperature/thinking/structured-output 키만 읽는다. 기존 .env의 DB·Redis·SSO·Executor 설정은 가져오지 않는다. model.frodo.com의 사용자 지정 alias(10.250.110.99)는 진단 Python 프로세스의 연결 해석에만 적용하며 /etc/hosts를 바꾸지 않는다. 인증정보·모델 endpoint는 HTML에 주입하지 않는다.

| 선택 | 의미 |
|---|---|
| --test-login | 직원 검증 대체; 격리된 loopback 테스트 DB 필요 |
| --model real | 실제 모델 설정 필수, 고정 응답 설치 안 함 |
| --model fixture | 고정 품질 계획/답변; --test-login 필요 |
| --local-fixtures | 기존 --test-login --model fixture 축약 옵션 |
| --executor real / off | 테스트 로그인에서 실제 제출 켜기/끄기; 기본 off |
| --fixture-admin | 전용 임시 DB의 테스트 관리자; 생략하면 일반 사용자 |
| --model-delay-ms | fixture 모델만 지연 조정; 실제 모델 지연을 바꾸지 않음 |

고정 모델 회귀용 실행은 위 명령에서 `--model real --model-env ...`를 `--model fixture`로 바꾸고 별도 --port/--db-port를 지정한다. 또는 기존 --local-fixtures를 그대로 쓴다. `[answer]`는 고정 모델 답변 경로에만 사용하는 예약 접두사다. 실제 모델에서는 자연어로 요청한다. 예: `default-nce 데이터의 max_val과 x 컬럼에 대해 기본 통계를 계산하고 Markdown 보고서를 작성해줘. 실행 전에 계획을 보여줘.`

CLI 기본 UI/API18100·DB53601이며 현재 실제 모델 인스턴스는 명시적으로18102·53603을 사용한다. 다른 로컬 서비스는 --redis-url/--executor-base-url/--executor-shared-root로 지정한다. 임시 DB는 Docker postgres:17을 사용하고 TCP 준비 후 초기화한다. Ctrl+C로 해당 프로세스와 소유 자원을 정리한다. 테스트 계정 권한은 기존 업무 DB에서 변경하지 않는다.

## 실제 SSO와 모델 환경 연결

기존 서버를 사용할 때 HTML의 API 연결에서 base URL을 지정할 수 있다. 쿠키/CORS 때문에 실제 API 시험에는 서비스의 같은 origin `/demo`를 사용한다. file://의 샘플 보기에는 서버가 필요 없다.

진단 앱 실행 도구에 --local-fixtures 없이 private flat JSON 중앙 설정을 넣으면 기존 create_app과 실제 SDK·모델 설정을 사용한다. API·Worker를 함께 실행하는 별도 앱 인스턴스이며 운영 서버의 라우터를 자동 추가하는 기능이 아니다. SSO_PUBLIC_API_ORIGIN/SSO_FRONTEND_ORIGIN·쿠키 정책은 제공한 설정을 유지하므로 호스팅 origin과 맞춰야 하고 SSO_ALLOWED_RETURN_ROOTS에 /test-console을 허용한다. 폐쇄망 SDK 연결 함수가 구현되지 않았다면 실제 로그인은503이다. reverse-proxy root_path와 플랫폼 원본 통합은 이번 시각 검증 범위가 아니다.

```sh
PYTHONPATH=src python scripts/diagnostics/serve_test_console.py \
  --settings-file /tmp/private-service-settings.json
```

로컬 fixture에 기존 전용 DB를 사용하려면 --local-fixtures --settings-file을 쓴다. DATABASE_URL은 loopback agentic_runtime_test, CHECKPOINT_DB_URI는 agentic_checkpoint_test만 허용한다. REDIS_URL/EXECUTOR_BASE_URL은 loopback이어야 한다. 일반 업무 DB를 fixture 대상으로 지정하지 않는다. 이 경우 해당 테스트 DB/Workflow 저장 경로는 사용자가 제공한 설정대로 보존하고 Docker DB를 자동 만들거나 제거하지 않는다. private 파일은0600이며 .env/인증 정보를 Git에 넣지 않는다.

## 확인한 내용과 남은 경계

089에서 진단 설정12개·Node core/화면9개·고정 모델+실제 HTTP/DB/Worker/Executor12개 회귀를 통과했다. 별도로 **내장 브라우저에서 실제 LLM**으로 계획→파라미터 편집→승인→Executor 통계/보고서와 POST SSE 후속 설명을 확인했다. HITL 새로고침·수정값/revision 복원·SSE 해제/재접속·세션 전환·로그아웃/재로그인 복원도 확인했다. [089 작업 기록](../../docs/improvements/089-real-model-test-console.md)과 [상세 검증 결과](../../docs/reports/test-console-real-model-2026-10-05/README.md)를 따른다. 086/087의 DOM double 검증과 이번 실제 브라우저 검증은 구분한다.

Chrome/네이티브 UI는 Mac 잠금·탭 timeout으로 완료하지 못했지만 내장 브라우저 DOM/화면은 검증했다. 사내 SSO SDK·실제 플랫폼/Gaia·remote kernel·전체 Workflow/Message CRUD·부하/접근성 전수 검증은 범위 밖이다. Dataset Registry·보고서 Artifact 등록·파일/이미지 입력을 화면만으로 구현하지 않는다. Markdown 로컬 저장은 서버 Artifact 등록과 다르다.

실제 모델 보고서는 사용자가 최종 대상에서 뺀 x를 원래 목표로 설명하는 사례가 남아 있다. 다음 Agent 기능 검토에서 승인된 최신 계획을 기준으로 해석하는지 확인해야 한다. 최소 Markdown 렌더러는 헤딩/텍스트 중심이며 표·inline Markdown은 원문으로 표시한다.

## 수정과 회귀 실행

`src/api_service/static/demo.html`의 CSS·markup·console-app script가 화면 소스다. Core에는 SSE framing·typed value·계획 명령·redaction을 두고, 아래 컨트롤러는 인증/요청·Run stream·화면 렌더링·명시적 preview simulator로 나눴다. 일반 서비스는 패키지 HTML과 공용 renderer를 사용한다. 진단 실행 도구와 개발용 DOM double은 생산 앱에서 import하지 않는다.

```sh
node --test tools/test-console/tests/console.test.cjs
# 고정 모델을 켠 별도 진단 앱에서만 실행. 실제 모델 콘솔에는 이 고정 시나리오를 사용하지 않는다.
# 관리자 서버라면 TEST_CONSOLE_EXPECT_ADMIN=1 추가
TEST_CONSOLE_API_URL=http://127.0.0.1:18100/api/v1 \
  node tools/test-console/tests/live-console.cjs
# 공용 계약 수정 후 샘플/OpenAPI만 갱신. 서비스 lifespan/DB는 실행하지 않는다
PYTHONPATH=src python scripts/design/update_test_console_fixtures.py
```

DOM double은 tests/dom-harness.cjs에 있고 개발용 Node 테스트에만 쓴다. 운영 HTML은 Node·bundler·npm package가 필요 없다. 기존 demo의 코드를 옮기거나 복제하지 않았다.


## 094 Workflow 계약 갱신

샘플 OpenAPI도 다중 user_queries 필수·수정 resource_revision·검색/reindex 경로·색인 상태와 추천 PlanView.catalog_reference를 포함한다. 전체 API 화면에서 등록/승격/검색/재색인과 실패 상태를 확인할 수 있다. 실제 embedding 모델은 서버 중앙 설정으로 주입하고 HNSW index를 먼저 생성해야 한다. 콘솔에 인증키·embedding endpoint를 주입하지 않는다. [확정 계약](../../docs/workflow-registration-and-search.md)을 따른다.
