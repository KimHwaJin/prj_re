# 기능 테스트 콘솔

[index.html](index.html)은 외부 CSS·JavaScript·CDN·프론트 빌드가 필요 없는 독립 HTML이다. 파일을 브라우저에서 직접 열면 샘플로 모든 화면과 조작을 확인한다. 실제 서비스는 API 연결로 시험하며 SSO 쿠키·CSRF와 현재 Runs/SSE 계약을 사용한다. 기존 demo.html은 재사용하거나 수정하지 않았다.

## 지금 로컬에서 열기

이번 작업의 테스트 서버는 http://127.0.0.1:18100/test-console 이다. SSO 로그인 버튼을 누르면 **로컬 테스트 관리자**로 로그인하며, 일반 사내 SSO와 실제 LLM은 연결하지 않는다. API·PostgreSQL·Agent/Event Worker·Redis·Executor/Jupyter는 실제 서비스다. 기본 모델 응답은 호출당300ms 고정이다. 현재 Executor 실제 제출이 켜져 있어 계획 승인 후 실제 노트북/실행 기록이 생성된다.

이 서버는 진단 도구가 만든 전용 임시 DB53601과 테스트 Workflow 저장소를 사용한다. 프로세스를 끝내면 임시 DB·Workflow 파일·전용 Redis group/로그인 키를 제거하며, 기존 Executor execution/notebook 이력은 남긴다. 장기 보존용 환경이나 운영 배포가 아니다. 재시작하면 새 사용자/DB에서 시작한다. 기존 Compose 서비스·원천 Parquet·공유 이벤트 Stream은 유지한다.

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
  --local-fixtures --temporary-db --fixture-admin --executor real \
  --executor-shared-root /Users/a10054/SKAX_PROJECT/executor/shared_dir
```

기본 UI/API18100·DB53601. 다른 포트는 --port/--db-port로 지정한다. --executor off는 실제 제출 없이 계획 승인 종료, --executor real은 실제 Executor를 호출한다. 일반 사용자 권한은 --fixture-admin을 빼서 새 서버를 시작한다. --model-delay-ms는 고정 모델 응답 지연만 바꾼다. 모델 fixture에서 일반 답변을 확인하려면 메시지를 `[answer]`로 시작한다. 그 외는 고정 품질 계획으로 연결하며 자연어 판단 품질을 확인한 것으로 해석하지 않는다. 실제 데이터 분석 예시는 `default-nce 품질과 통계를 확인하고 보고서를 작성해줘`다.

다른 로컬 환경에서는 --redis-url/--executor-base-url/--executor-shared-root를 지정한다. 임시 DB는 Docker postgres:17을 사용한다. 준비 완료는 TCP로 확인하여 초기화 중 임시 socket 서버와 구분한다. Ctrl+C로 해당 서버와 전용 자원을 종료한다. 테스트 관리자 옵션은 --local-fixtures --temporary-db에서만 허용한다. 기존 직원 계정이나 실제 DB 권한을 승격하지 않는다.

## 실제 SSO와 모델 환경 연결

기존 서버를 사용할 때 HTML의 API 연결에서 base URL을 지정할 수 있다. 쿠키/CORS 때문에 실제 API 시험에는 같은 origin으로 HTML을 호스팅하는 편이 편하다. file://의 샘플 보기에는 서버가 필요 없다.

진단 앱 실행 도구에 --local-fixtures 없이 private flat JSON 중앙 설정을 넣으면 기존 create_app과 실제 SDK·모델 설정을 사용한다. API·Worker를 함께 실행하는 별도 앱 인스턴스이며 운영 서버의 라우터를 자동 추가하는 기능이 아니다. SSO_PUBLIC_API_ORIGIN/SSO_FRONTEND_ORIGIN·쿠키 정책은 제공한 설정을 유지하므로 호스팅 origin과 맞춰야 하고 SSO_ALLOWED_RETURN_ROOTS에 /test-console을 허용한다. 폐쇄망 SDK 연결 함수가 구현되지 않았다면 실제 로그인은503이다. reverse-proxy root_path와 플랫폼 원본 통합은 이번 시각 검증 범위가 아니다.

```sh
PYTHONPATH=src python scripts/diagnostics/serve_test_console.py \
  --settings-file /tmp/private-service-settings.json
```

로컬 fixture에 기존 전용 DB를 사용하려면 --local-fixtures --settings-file을 쓴다. DATABASE_URL은 loopback agentic_runtime_test, CHECKPOINT_DB_URI는 agentic_checkpoint_test만 허용한다. REDIS_URL/EXECUTOR_BASE_URL은 loopback이어야 한다. 일반 업무 DB를 fixture 대상으로 지정하지 않는다. 이 경우 해당 테스트 DB/Workflow 저장 경로는 사용자가 제공한 설정대로 보존하고 Docker DB를 자동 만들거나 제거하지 않는다. private 파일은0600이며 .env/인증 정보를 Git에 넣지 않는다.

## 확인한 내용과 남은 경계

Node로 실제 inline controller를 실행해 core/샘플7개를 확인했고, 개발용 DOM double+실제 HTTP API/DB/Worker/Executor로11개 시나리오를 확인했다. 일반 사용자403·관리자 접근, 관리자 API6개도 별도 검증했다. 결과는 [086 작업 기록](../../docs/improvements/086-functional-test-console.md)과 [검증 JSON](../../docs/reports/test-console-2026-10-04/result.json)을 따른다.

Mac 잠금으로 실제 브라우저 클릭·레이아웃·다운로드·SSO 브라우저 왕복은 시각 검증하지 못했다. DOM double 검증을 실제 브라우저 검증으로 대체해 보고하지 않는다. 화면은 개발 기능 확인 도구이며 운영 프론트의 모든 UX/성능/접근성 검증을 완료한 것이 아니다. 현재 서버에 없는 Dataset Registry·파일/이미지 입력·보고서 Artifact 등록·Gaia 등의 기능을 화면만으로 구현하지 않는다. Markdown 로컬 저장은 서버 Artifact 등록과 다르다. 실제 Workflow/Message CRUD 전 동작을 이번에 회귀 검증한 것도 아니다.

## 수정과 회귀 실행

index.html의 CSS·markup·console-app script가 화면 소스다. Core에는 SSE framing·typed value·계획 명령·redaction을 두고, 아래 컨트롤러는 인증/요청·Run stream·화면 렌더링·명시적 preview simulator로 나눴다. 생산 애플리케이션에서 이 파일/실행 도구를 import하지 않는다.

```sh
node --test tools/test-console/tests/console.test.cjs
# 별도 진단 앱에서만 실행. 관리자 서버라면 TEST_CONSOLE_EXPECT_ADMIN=1 추가
TEST_CONSOLE_API_URL=http://127.0.0.1:18100/api/v1 \
  node tools/test-console/tests/live-console.cjs
# 공용 계약 수정 후 샘플/OpenAPI만 갱신. 서비스 lifespan/DB는 실행하지 않는다
PYTHONPATH=src python scripts/design/update_test_console_fixtures.py
```

DOM double은 tests/dom-harness.cjs에 있고 개발용 Node 테스트에만 쓴다. 운영 HTML은 Node·bundler·npm package가 필요 없다. 기존 demo의 코드를 옮기거나 복제하지 않았다.
