# 083 — 프로젝트 기본 CRUD 계약·프롬프트 수명 정리

- 날짜: 2026-10-04
- 브랜치: feature/project-crud-contract
- 기준: feature/admin-user-read-contract / 25e838a (082)
- 상태: 구현·검증 완료, 베이스 미병합·미푸시·미배포

## 문제와 승인된 방향

프로젝트 목록이 상세와 같은 ProjectResource를 사용해 system_prompt 전체를 읽고 반환했다. 생성/수정은 알 수 없는 필드를 조용히 무시했고 일부 PATCH에서 명시적 null을 생략처럼 처리했다. ProjectDeleteResult는 서비스에서 만든 뒤 라우터가 버려서 외부204 응답과 연결되지 않았다. 지침이 새 Run·기존 재개에 적용되는 시점도 API 문서로 명확하게 확인하기 어려웠다.

사용자 승인에 따라 목록/상세를 분리하고 입력 규칙·지침 수명을 확정했다. 기본 프로젝트 이름/삭제 금지, 미종료 작업·실행 점유 삭제 보호, SSO 기본 프로젝트 생성과 소유권을 유지한다. 기존 LangGraph/AgentContext/middleware 동작을 검증했으며 Agent 내부 흐름을 새로 구현한 것은 아니다.

## 최종 계약

- GET /api/v1/projects는 Page[ProjectSummary]다. id/name/is_default/created_at/updated_at의5개 필드만 제공한다. 지침·버전·세션·메시지·메모리는 제외한다. 기존 cursor·기본50/최대200·created_at 양방향·UUID tie-break·날짜[from,to)를 유지한다.
- POST /projects, GET/PATCH /projects/{project_id}는 기존7개 상세 필드 ProjectResource를 유지한다. 요청 project_name과 응답 name, path project_id와 응답 id는 변경하지 않는다.
- 생성은 이름/지침만 허용한다. 알 수 없는 필드·null은422다. 생략한 지침은 빈 문자열, 버전은1이다. 생성 공백 이름의 기존409/default 충돌 정책과 활성 이름 중복409를 유지한다.
- PATCH 생략은 유지, 지침의 빈 문자열은 초기화, 명시적 null/빈 body/알 수 없는 필드는422다. 잘못된 이름·지침을 함께 보내면 부분 반영하지 않는다. 지침 내용이 실제로 변할 때만 버전이 증가하며 이름 변경/동일 내용 재지정에는 증가하지 않는다.
- PATCH OpenAPI도 선택 필드는 문자열·minProperties=1·additionalProperties=false로 표현한다. Python의 미지정 기본값 None을 외부 null 허용으로 표시하지 않도록 필드 schema annotation을 조정했다. 실제 null 거절은 모델 validator가 수행한다.
- GET 목록/상세에 Cache-Control:no-store를 적용한다. 로그인한 활성 사용자 소유의 활성 프로젝트만 접근하며 관리자 역할도 일반 Project 소유권을 우회하지 않는다.
- DELETE는 기본 프로젝트409, 미종료/복구/점유409, 정상 soft delete204·body 없음이다. ProjectDeleteResult와 결과 생성 코드를 제거했다. 하위 Session/Message를 숨기는 transaction·잠금·검사는 유지하고, 다른 서비스도 쓰는 cascade helper는 유지한다.

## 구조와 조회 비용

- schemas/common/project_schema.py에 ProjectCreate/Update/Summary/Resource를 모았다. ProjectResource를 일반 api_schema.py에서 이동하고 라우터/진단 스크립트 사용처를 전환했다. 이전 위치에 재수출·호환 별칭을 남기지 않았다.
- services/project_queries.py는 소유권·활성 조건과5개 열의 SQL Bundle 페이지 투영을 사용한다. ORM 전체·지침·하위 자원을 로드하지 않는다. SQL 구조만 재사용하며 데이터/권한은 매 요청 DB에서 읽는다.
- routes/projects.py는 공통 pagination/인증을 유지하고 요약 query service로 위임한다. memory GET/PUT/DELETE는 기존 별도 정책을 유지한다.
- 목록은 인증1+페이지1의2 SELECT, 최대 limit+1행이다. 전체 COUNT·프로젝트별 추가 조회가 없다. 쿼리 수는 기존 목록과 같고 반환 열/응답 본문을 축소한 것이다. 실제 시간/처리량 향상은 측정하지 않았다.

## 지침의 적용 수명

현재 비동기 API/Worker의 새 Run은 Worker 실행 시작 직전 DB에서 지침/버전을 읽어 checkpoint에 고정한다. HTTP 접수 시 고정하지 않으므로 큐 대기 중 편집한 값이 실행 시 적용될 수 있다. 기존 HITL/Executor 재개는 해당 Run의 snapshot을 유지한다. 영속 receipt를 이용한 완료 복구도 새 지침을 읽지 않는다.

같은 세션의 다음 새 Run은 최신 지침을 읽고 빈 문자열 초기화도 이전 값을 덮어쓴다. 과거 checkpoint에 project_system_prompt 키 자체가 없을 때만 기존 legacy backfill을 수행한다. 이미 빈 문자열이 저장됐으면 다시 읽지 않는다. project_memory의 문서 version/선택/갱신은 별도이며 system_prompt PATCH로 메모리를 쓰지 않는다.

현재 각 역할의 create_agent는 ProjectPromptMiddleware와 AgentContext로 지침을 호출/재시도마다 받는다. 동시 프로젝트의 공유 Agent 전역 프롬프트를 덮어쓰지 않는다. 검증에는 framework-selection·langgraph-persistence 스킬의 checkpoint/thread/Store 경계를 참고했다. production Agent·Worker·checkpoint 프로토콜 자체는 수정하지 않았다.

## 검증

전용 PostgreSQL17 컨테이너 dtest-project-crud-test-20261004, localhost53599/identity_test만 사용했다. 기존 서비스DB·Redis·Executor·실제LLM·사내SSO SDK를 호출하지 않았다. HTTP 신원 double, SSO의 production cookie/CSRF 경로+SDK/Redis double, 실제 Worker/PG checkpoint와 기존 시험 그래프를 사용했다.

- 주 검증 **118 passed**: 프로젝트 신규11, 기존 읽기 예산13, 삭제 보호/실제PG 경합63, 프로젝트 context13, Agent middleware18이다. 이 중 격리PG는87개, DB 없는 context/middleware는31개다.
- 신규 페이지1/7/200×양방향·동시각 tie-break·205건 분할·기본50·날짜 범위·invalid cursor/limit/sort·정확한5필드/5열·2 SELECT·limit+1·대형 지침 비조회·detail7필드를 확인했다.
- 생성/수정 unknown fields·명시적 null·빈 body·공백 이름·지침 초기화·버전 증가/유지·기본 프로젝트·부분 수정 금지·owner/admin404·soft delete204/no-body/cascade/이력 보존을 검증했다.
- 실제 API→Worker→PG checkpoint의 새 Run→HITL→resume→완료→같은 세션의 다음 Run을 실행했다. 큐 중 지침 수정은 새 Run에 반영, HITL 중 수정은 기존 Run에 미반영, resume의 DB prompt 재조회 금지, 다음 Run의 빈 지침/v5 반영을 확인했다.
- 추가 SSO 회귀 **7 passed, 1 skipped**. 최초 SSO 자동 가입의 기본 프로젝트를 실제 cookie로 새 요약/상세 GET에서 읽고, 기존 가입/소유권/CSRF/역할을 확인했다. 제외1개는 별도 Agent 통합DB/설정 파일이 필요한 기존 Run/resume E2E다. 사내SDK를 실연결한 시험은 아니다.
- 명세 표현 조정 후 관련 HTTP 요청 시험1개를 다시 실행하여 통과했다. 재실행을 중복 합산하지 않은 PG 통과는 **94개(87+7)** 다.
- 전체 src **681 passed, 477 skipped, 74 warnings**. PG opt-in은 위에서 별도 검증했다. 기존 no-checkpointer durability 경고는 유지한다.
- 클린 staging wheel/isolatedPython 검증 통과. 요약/상세·엄격한 request·nonnull/minProperties PATCH schema·삭제DTO 부재·query 모듈·34paths·5개 Agent 역할/리소스/조립, checkout 미참조를 확인했다.
- scoped OpenAPI **18paths/44models**,4개 Project payload schema·요청/응답4예제, **34files·7,516주석·8inline blocks**. 현재 Pydantic 검증, JSON/JSONC 동등성과 문서 annotation 전후 검증 규칙 보존을 확인했다.
- 추가 시험 작성 중 SSO assertion 삽입의 들여쓰기 오류와 선택적 JSONSchema 검사 패키지 미설치로 collection 실패가 각1회 있었다. 시험 파일을 수정하고 추가 dependency 없이 HTTP 시험과 wheel schema 계약 검증을 사용했다. 문서 작성 helper의 syntax 오류는 파일 쓰기 전에 실패했고 정정 후 작성했다. 이후 최종 검증은 위 통과 결과다.
- git diff --check 통과. 새 migration/환경변수/의존성 없음. 이번 임시DB만 종료하고 기존 서비스/원본 checkout을 변경하지 않았다. 실제 배포·SDK 실연결·성능 A/B는 미실행이다.

## 문서와 프론트 이행

[프로젝트 API](../project-api.md), [메모리](../project-memory.md), [CRUD 보호](../crud-lifecycle-policy.md), [Run](../public-run-api.md), [기계 판독 계약·예제](../contracts/agent-api/README.md), [필드 주석](../contracts/field-comments.md)을 갱신했다.

목록에서 지침/버전을 쓰던 프론트는 선택한 프로젝트의 상세 GET으로 바꾼다. 수정하지 않을 필드는 생략하고 초기화할 지침만 빈 문자열로 보낸다. id/name·기본 프로젝트·소유권·DELETE204는 유지한다. 외부 프론트 실이행/사내SDK 연결/실제 모델 지침 준수·Pod 성능은 후속 통합 검증이다. Message CUD·Workflow CRUD·운영 복구·모델 호출 수의 기존 후순위를 유지한다.
