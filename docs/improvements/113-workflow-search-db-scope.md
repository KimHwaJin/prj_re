# 113 — Workflow 검색 인증 DB 수명 분리

## 기준과 상태

- 기준: feature/refactor-base, 56ad2cb.
- 작업: feature/workflow-search-db-scope.
- 상태: 구현·검증 완료, 베이스 미병합·미푸시·서비스 미배포.
- 구현 커밋: 9c3408b (검색 인증·회귀·검증 기록).
- 사용자 요청: 전체 구조 리뷰에 이어 다음 개선 작업 진행.

## 확인한 문제

POST /api/v1/workflows/search는 CurrentUserId를 사용했다. 인증의 사용자
SELECT FOR SHARE가 요청 DB 세션에 transaction을 열고, 라우터가 임베딩
응답을 기다리는 동안 그 세션과 연결을 유지했다. 검색 Runtime 자체의
짧은 세션 정책만으로는 인증 의존성이 점유한 연결을 반환하지 못했다.

확인 위치:

- [HTTP 의존성](../../src/dtest/api_service/http/dependencies.py)
- [검색 라우터](../../src/dtest/api_service/http/v1/routes/workflows.py)
- [검색 Runtime](../../src/dtest/infrastructure/workflow_search/retrieval.py)

## 변경과 정책

get_read_user_id / ReadUserId는 로그인·CSRF 검사 후 short_session에서
사용자 활성 여부를 조회한다. 불변 Actor에서 내부 UUID를 얻고 세션 종료를
완료한 뒤 라우터를 실행한다. 이 경로에서는 사용자 공유 잠금을 잡지 않는다.
Workflow 검색에 적용했다. 외부 대기를 포함하는 다른 읽기 API에서도 재사용할
수 있지만 이번 작업에서 전체 CRUD 인증을 일괄 교체하지 않았다.

이 읽기 권한은 접수 시 활성 사용자임을 확인한 snapshot이다. 검색 도중 계정이
비활성화되어도 시작된 읽기는 끝날 수 있으며, 그 snapshot으로 변경 요청을
수행해서는 안 된다. 변경 API는 기존 CurrentUserId의 사용자 공유 잠금과
해당 transaction 수명을 유지한다. SSE는 기존 함수 scope 의존성을 사용한다.

HTTP 경로·요청·응답·쿠키·CSRF 정책 및 검색 알고리즘은 동일하다.
DB schema·추가 설정·Pool 크기·Worker 실행 정책은 변경하지 않았다.

## 검증

일회용 pgvector PostgreSQL17 컨테이너 dtest-search-db-scope-pg-113,
로컬 loopback 임시 포트65325를 사용했다. DB는 agentic_runtime_test와
agentic_checkpoint_test다. 사용자 서비스·기존 DB·Redis·Executor에는
테스트 데이터를 쓰지 않았다. LLM·Executor 호출은 하지 않았다.
검증 후 이번 컨테이너·전용 볼륨 및 임시 연결 설정을 삭제했다.

실제 HTTP 검색에서 embedding double을 Event로 대기시켰다. 인증용
SQLAlchemy pool은 size=1, max_overflow=0이며 사용자 조회는 실제 SQL이다.

| 조건 | 임베딩 대기 중 인증 pool 점유 | 별도 SQL |
| --- | ---: | --- |
| 이전 CurrentUserId를 dependency override로 대조 | 1 | 연결 반납 단언 실패 |
| 변경 ReadUserId | 0 | 같은 pool에서 SELECT 1 완료 |

이전 방식 대조는 /tmp의 test plugin만 사용했다. 서비스 코드를 되돌리거나
실행 서버를 변경하지 않았다. 이 대조의 예상 실패1개는 변경 후 회귀 실패와
구분한다. 시간 단축률이나 실제 LLM 부하에서의 처리량 향상은 측정하지 않았다.

회귀 범위:

- 새 HTTP 경계6개: 조회 전 close, 비활성/잘못된/없는 신원 거절,
  검색 예외 후 close, 변경 요청의 공유 잠금 유지.
- 실제 SSO 쿠키·CSRF 검색 검사: 토큰 누락403 및 유효 요청200.
- 기존 SSO·패키지 경계와 위 테스트: 50개 통과.
- Workflow pgvector·색인·검색 및 새 단일 연결 pool 검사: 11개 통과.
- 이전 의존성 대조: 예상한 pool 점유 단언 실패1개.

실행 방식은 PYTHONPATH=src, pytest -p no:cacheprovider이며 실제 DB 회귀는
DTEST_AGENTIC_TEST_SETTINGS_FILE로 격리 설정을 전달했다. 아래 파일을 실행했다.

- tests/api_service/test_workflow_search_db_scope.py
- tests/api_service/test_sso_auth.py
- tests/api_service/test_package_boundaries.py
- tests/api_service/test_workflow_retrieval_postgres.py

정적 검사는 고정 Ruff0.16.10·ty0.0.84 도구 환경에서 uv run --locked
--no-sync로 실행했다. ty의 라이브러리 해석은 Python3.11 테스트 환경을 사용했다.
전체 Ruff3076·ty759 진단은 기존과 동일하며 새 진단이 없다. Ruff format
--check는 통과했다. 전체 lint/type 통과로 표현하지 않는다.

## 후속

다음은 체크포인트 종류와 Executor 제출 활성화의 설정 정합성 개선이다.
memory + EXECUTOR_SUBMIT_ENABLED=true를 받아들이면서 실행 노드를
생성하지 않는 경로를 시작 시 검증할지 조립을 분리할지 확정해야 한다.
그 뒤 DB 연결/데이터 경로 하드코딩, 과거 그래프 저장 경로, Container와
Runtime 경계, Agent 정책·상태 타입 정리를 순차 진행한다.
