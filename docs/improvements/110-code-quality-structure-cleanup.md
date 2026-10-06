# 110 — 코드 품질·미사용 구현·서비스 책임 정리

## 요청과 기준

사용자 요청에 따라 신규 추천 기능보다 코드 품질, 불필요한 코드·모듈 삭제,
구조 정리를 우선했다. `feature/refactor-base`의 `120c555`에서
`feature/code-quality-structure-cleanup`을 만들었다.

삭제 여부는 Python import만으로 결정하지 않았다. 소스·테스트·개발 도구의
호출, 공개 HTTP 계약, 문서 및 동적 자산 로딩을 확인했다. Python에서 import하지
않는 등록 Tool 함수는 Executor에 코드로 제출하는 자산이므로 유지했다.
등록 Skill Markdown·Tool 함수·Workflow 자산의 실행 내용은 변경하지 않았다.
외부 서비스 계약과 아직 미연동인 계약 초안도 이번 내부 코드 삭제와 구분한다.

## 문제와 변경

### 1. 라우터의 중복 의존성 선언

각 endpoint에서 사용자·관리자·DB·pagination Depends를 반복 선언했다.
의존성의 실제 타입과 HTTP 주입용 기본값이 섞여 있었으며, SSE의 DB 해제 scope를
각 라우터에서 직접 관리했다.

- 인증: `api_service/auth/dependencies.py`의 `LoginDependency`.
- 업무 HTTP: `api_service/http/dependencies.py`의 `DBSession`,
  `StreamDBSession`, `CurrentActor`, `AdminActor`, `CurrentUserId`, `StreamUserId`.
- 목록: `api_service/http/pagination.py`의 `ListQuery`.
- Query/Header는 Annotated 메타데이터로, 실제 선택값은 Python 기본값으로 선언.
- 기존 dependency callable, 캐싱, override, 사용자 행 잠금 및
  SSE `scope="function"` 동작은 유지.

라우터가 ORM model·repository·SQLAlchemy를 직접 import하지 않는 경계를
회귀 테스트에 추가했다. 업무 요청의 인증/잠금과 SSE의 짧은 DB 수명을
같은 별칭으로 혼합하지 않는다.

### 2. HTTP 계층에 남은 실행·CRUD 정책

Run REST와 POST SSE가 하나의 라우터 helper를 공유했고, REST는 동일한
Idempotency-Key 검사까지 한 번 더 수행했다. 메시지 라우터는 기본 프로젝트를
직접 조회하고 필요하면 세션을 생성했다.

- 통합 Run 접수·resume 변환과 입력/키 검증을
  `application/runs/submission.py`의 `submit_request()`로 이동.
- REST·POST SSE가 같은 application 경계를 호출하며, 실패는
  `ApplicationError`를 기존 HTTP error handler에서 변환.
- 메시지의 선택적 세션 생성은
  `application/resources/messages.py`의 `create_from_request()`로 이동.
- 이미 세션이 필요한 내부 메시지 생성은 `create()` 계약을 유지.
- 항상 `(session, False)`만 반환하던 `_resolve_session()` 래퍼 제거.
- 프로젝트 수정 조회는 lock을 확보한 뒤 단일 결과를 `scalar_one()`으로 읽어
  Optional ORM 행으로 취급하던 타입 오류 제거.

DB schema나 메시지 생성 정책을 변경하는 migration은 추가하지 않았다.
메시지 CUD의 기능 확장도 이번 작업이 아니다.

### 3. 사용처가 사라진 구현과 호환 분기

현재 bounded observation 경로는 옛 manifest 해석기의 결과 함수가 아니라
`infrastructure/executor/observations.py`를 사용한다. 필요한 파일 검증 두 함수만
남기고 과거 전체 결과 해석 경로를 제거했다.

삭제한 파일:

- `src/dtest/infrastructure/executor/manifest.py`.
- `scripts/diagnostics/review_crud_contracts.py`: 이전 CRUD 검토 시점의 fake 기반
  실행 스크립트. 현재 API/DB 회귀 검증은 `tests/api_service`에서 수행한다.
  과거 저장 보고서는 삭제하지 않았다.

삭제한 내부 구현:

- `call_io`: 동기/비동기 어댑터 추정 함수. 실제 Executor HTTP는 native async.
  취소 중 소유권을 지키는 `run_sync`는 실제 사용처가 있어 유지.
- `is_graph_persisted_message`, `classify_graph_message` 미사용 wrapper.
- `DeferredGraphEventHandler`: 실제 dispatcher에 등록되지 않는 placeholder.
- `save_new_graph_messages`: 현재 dispatcher와 별개인 미사용 저장 경로.
- `_model_fields`, `_message_create_payload_kwargs`, `_call_message_create`:
  Pydantic v1/다른 MessageService signature를 추측하던 호환 코드.
  현재 MessageCreate/MessageService 계약을 직접 사용한다.
- `RunResource`: PublicRunResource와 별개인 미사용 옛 응답 DTO.
- `WorkflowGeneratorOutput`: 서비스에서 검증에 쓰이지 않던 옛 wrapper DTO.
  사용 중인 WorkflowDefinition과 서비스의 기존 형식 해석은 유지.

실제 파일 검증은 `infrastructure/file_storage/integrity.py`의
`resolve_shared_path()`와 `read_verified_bytes()`로 이동했다.
root 밖 경로, parent traversal, symlink escape, 파일 크기와 SHA256 확인을 유지한다.
`observations.py`가 다른 모듈의 private 함수를 import하던 의존성도 제거했다.

Artifact 제출 DTO와 Workflow 검색 포트 삭제 시도는 자동 승인 검토가
외부 연계/공개 계약 위험을 이유로 거절했다. 해당 계약은 삭제하지 않았다.
Dataset Registry 초안도 외부 구현 대기라는 이유만으로 제거하지 않았다.

### 4. Python과 브라우저 코드 및 개발 도구

- Swagger 로그인/CSRF controller를 `web/static/swagger-auth.js`로 분리.
  패키지 자산으로 포함하고 `importlib.resources`로 읽는다.
  기존 same-origin, CSRF, SSO 복귀와 두 inline script 실행 순서를 유지한다.
- 문서용 긴 설명은 79자 규칙에 맞춰 줄바꿈. HTTP 필드·기본값·상태·security
  계약은 같고 일부 description의 공백/줄바꿈만 달라졌다.
- SSE replay에서 함수 인자 sequence를 loop 변수로 다시 사용하지 않고
  cursor를 명시적으로 관리.
- 사용하지 않는 import 제거 및 import 순서/현행 typing import 정리.
  FK 모델 등록 import는 부작용이 필요해 사유를 명시하여 유지했고,
  execution_repair 패키지의 build_agent 공개 export는 `__all__`로 표시.
- 유지 중인 진단·계약·benchmark 도구의 `src/dtest.agent_service` 같은
  잘못된 filesystem 경로를 현재 `src/dtest/agent_service`로 교정.
- HNSW provisioning 도구의 `settings.api.database_url`을
  `settings.database.database_url`로 교정.

### 5. 운영 설치 의존성

- 실제 import가 없는 aiohttp/requests 직접 의존성 선언 제거.
  requests는 LangSmith 등 전이 의존성으로 설치될 수 있다.
- LangGraph CLI/inmem 개발 서버를 dev 그룹으로 이동.
- 직접 사용하는 LangChain core와 LangSmith는 기존 lock 버전을 그대로 명시.
- 버전 업그레이드 없이 offline lock 갱신: 143→135개 패키지.
  aiohttp 및 관련 7개 패키지가 lock에서 빠졌다.
- Tool 커널용 ML 라이브러리 개발 그룹과 서비스 numpy는 유지.

## 검증

- 분리된 PostgreSQL17/pgvector0.8.6와 Redis7에서 Agent·API 전체 회귀:
  **1,340 통과, 2 skip**, 674.69초. 실제 외부 LLM/Executor 대신 local double을
  사용하는 회귀이며 사용자 운영 DB/Streams에는 연결하지 않았다.
- 전체 테스트 수집 후 추가한 파일 검증8건/추가 package 경계1건을 포함한
  관련 재검증: **54 통과**. 이 숫자는 전체 회귀와 겹쳐 합산하지 않는다.
- 초기 sandbox 실행은830통과/485skip/27실패였다. 27실패는 로컬 TCP bind
  제한이었고 해당 HTTP/종료/Windows runner 테스트를 허용 환경에서 재실행하여
  **49 통과**했다. 이후 전체 회귀에서도 모두 통과했다.
- 최종 wheel 설치·스모크 검증 통과. 실행 의존성 lock에서 남은 패키지의 버전이
  하나도 바뀌지 않았음을 별도로 비교했다.
- `git diff --check` 통과. 등록된 Tool 함수 원본이 Git 기준과 일치함을 확인.
- 실제 Windows3.11.9/사내 SSO SDK/운영 Executor·LLM 및 Kubernetes 부하를
  검증한 것은 아니다. 기존 장기 실행/배포 이행 검증을 대체하지 않는다.

정적 검사 기준은 AGENTS.md의 ty·Ruff, line-length79다. 설치되어 있는 고정 버전
검사 도구를 `uv run --locked --no-sync`로 실행했다. lint rule이나 검사 대상에
새 전역 ignore/exclude를 추가하지 않았다.

| 항목 | 변경 전 | 변경 후 |
| --- | ---: | ---: |
| 전체 Ruff 진단 | 3,500 | 3,079 |
| src/dtest Ruff 진단 | 1,033 | 632 |
| 전체 ty 진단 | 772 | 759 |
| src/dtest ty 오류 | 142 | 134 |

동일 filename·rule·message 기준으로 새 Ruff/ty 진단은 없다.
전체 포맷 검사: 796파일 통과. API 패키지에 남은 Ruff1건은 외부 사내 SDK 예외를
응답에 노출하지 않기 위한 SSO 경계의 broad exception 처리다.
전체 lint/type 검사가 통과했다고 표시하지 않는다.

OpenAPI 전체를 변경 전후 비교했고 description을 제외한 경로·필드·기본값·
응답 상태·security를 포함한 JSON이 동일하다. 처음 Annotated 전환 직후에는
설명까지 포함한 전체 JSON이 동일했다. description 줄바꿈은 이후 포맷 정리다.

개발 패키지 없는 별도 환경에 wheel을 `uv sync --locked --no-dev --no-editable
--offline`로 설치했다. source checkout/PYTHONPATH를 사용하지 않고 `/tmp`에서
API, `/demo`, Swagger JS 자산, `/openapi.json`, 미인증401, mock 계획→HITL 승인
실행을 확인했다. aiohttp, langgraph_cli, langgraph_api, pandas, sklearn가 없는
환경에서도 통과했다. 실제 사내 SSO SDK·LLM·운영 Executor 실행 검증은 아니다.

## 남은 품질 정리

기존 긴 문자열/주석, TypedDict 상태·Optional 행·SDK Protocol/return typing 등
소스의 134개 타입 오류와 lint 진단이 남아 있다. tests/scripts에도 기존 오류가
남는다. 다음 정리는 Agent 상태/반환 타입 및 application 조회 결과 타입 경계를
좁히고, 불필요한 호환 코드가 추가로 확인되면 삭제하는 방향이다.
검사 전체 exclude나 Any 확대만으로 통과시키지 않는다.

이 작업을 처리량 개선이나 운영 배포 완료로 해석하지 않는다. 서비스 구조를
그대로 늘리는 신규 Worker/DB pool/API는 추가하지 않았다.
