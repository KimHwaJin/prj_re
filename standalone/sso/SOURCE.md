# 추출 기준과 원본 대응

기준 저장소: KimHwaJin/prj_re.
기준 브랜치: feature/refactor-base.
기준 커밋: 1762285efab46051960f20b4f230f779cb7eb28d.
작업 브랜치: feature/standalone-sso.

| 원본 dtest 경로 | 이식본 |
|---|---|
| api_service/auth/runtime.py | sso/runtime.py |
| api_service/auth/dependencies.py | sso/dependencies.py |
| contracts/auth.py | sso/contracts.py |
| settings/auth.py | sso/settings.py |
| infrastructure/redis/login_sessions.py | sso/storage/sessions.py |
| infrastructure/redis/login_flows.py | sso/storage/login_flows.py |
| infrastructure/sso/adapter.py | sso/sdk/adapter.py |
| infrastructure/sso/request.py | sso/sdk/request.py |
| infrastructure/sso/environ.py | sso/sdk/environ.py |
| infrastructure/sso/company.py의 공개5항목 계약 | sso/sdk/company.py |

사내 SDK 원문은 포함하지 않는다. company.py에서 추출한 것은 저장소에 있는
공개 SDK 계약과5항목 변환뿐이며 폐쇄망에서 작성한 생성/import 코드는 아니다.

## 이식본에서만 달라진 점

- 모든 dtest import 제거. 사용자 DB/기본 프로젝트/Agent 의존성0.
- 공개 import는 from sso. setuptools wheel과 typed marker 제공.
- namespace/cookie_name 필수. auto_register 삭제: 서비스 bind 정책으로 결정.
- GET /auth/session 추가. 원본 /users/me·OpenAPI·로그인 API는 변경하지 않음.
- create_sdk_adapter(factory) 추가. private 생성 함수를 패키지 밖에서 주입.
- SsoError는 HTTPException 기반: plain FastAPI의 기본 handler로503 변환.
  글로벌 handler를 설치하거나 서비스 handler를 덮어쓰지 않음.
- RedisBackend.execute_command를 통해 async 명령 계약을 단일화.
- 설치 경로 값 검증과 사용자 연결의 빈 반환값 거절 추가.
- 독립 회귀·실제 로컬 Redis 테스트·폐쇄망 연결 예제 추가.

기존 src/dtest·최상위 pyproject.toml/uv.lock·서비스 설정은 변경하지 않는다.
이식본은 원본의 자동 동기화 대상이 아니며 후속 수정은 버전별로 따로 기록한다.
