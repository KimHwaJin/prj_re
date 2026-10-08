# 116 — SSO 속성 오류 진단과 내부망 의존성 보존

## 기준과 상태

- 기준: feature/refactor-base, 0a522a2.
- 작업: feature/sso-error-diagnostics.
- 상태: 구현·회귀 검증 완료. 사용자 요청에 따라 베이스 병합·origin 게시.
  서비스 미배포, 실제 내부망 SDK/Windows 오류 재확인은 별도다.
- 요청: AttributeError 진단 코드를 직접 수정하고 머지·푸시. 내부망에서
  조정한 pyproject/uv.lock과 이번 코드 업데이트의 충돌 방지.

## 문제와 변경

기존 sso_sdk_failed는 error_type=AttributeError만 남겼다. SDK가 요청의 args에
접근했는지, 생성 객체의 redirect_url이 없는지, None을 반환했는지 구분할 수 없었다.

SsoRuntime._sdk의 오류 처리에서 AttributeError의 name/obj 메타데이터를 읽어
attribute와 object_type을 기록한다. 비정상 문자열·줄바꿈·128자 초과 값은
unknown으로 치환한다. 객체 값·repr·예외 원문·Cookie·직원 정보는 로그에 넣지 않는다.
기존503 응답, Cookie 미발행, 실패 시 사용자 자동 등록 미진행 정책을 유지한다.

예시 로그:

    sso_sdk_failed error_type=AttributeError attribute=args object_type=Request

이 예시는 코드가 만들어 내는 출력 규격이다. 사용자 SDK의 실제 누락 속성은
Windows에서 새 로그를 받아야 확정한다. SDK 호환 구현을 임의로 바꾸지 않는다.

## 의존성과 내부망 변경 보존

이번 diff에서 pyproject.toml, uv.lock과
src/dtest/infrastructure/sso/company.py의 변경은 모두0이다.
새 라이브러리를 추가하거나 이전 드라이버 버전으로 되돌리지 않는다.
사용자 Windows 소스·패키지를 외부에서 덮어쓰지 않는다.

0a522a2 기준에서 이번 push를 받으면 두 의존성 파일에 원격 변경이 없어
이번 작업 때문에 이 파일들의 merge conflict를 발생시키지 않는다.
이전 미반영 commit·다른 개발자 변경·사용자의 다른 코드 수정까지 포함한
모든 git pull의 무충돌을 보장하지 않는다. merge=ours, skip-worktree, 강제
checkout으로 의존성 변경을 숨기지 않는다.

현재 패키지를 보존해서 실행하려면 uv run --no-sync app.py --env local을 쓴다.
공식 uv 옵션으로 자동 환경 동기화를 생략하며, 새 의존성이 없는 이번 변경에
적용 가능하다. [현재 SSO 안내](../sso-authentication.md)에 기록했다.

## 검증

Python3.11 격리 환경의 관련81개 회귀 통과. 신규7개는 다음 경계를 검증한다.

- 실제 Starlette Request의 args/to_dict 속성 오류가 타입·속성 이름으로 기록됨.
- SDK 오류 후 HTTP503, Redis 로그인 세션 미생성, 응답에 진단 내부 정보 미노출.
- redirect_url/NoneType, 메타데이터 없는 AttributeError의 안전한 처리.
- 줄바꿈/임의 문자열과129자 속성명은 로그에서 제외됨.
- 일반 RuntimeError의 원문 비밀값이 로그/응답에 들어가지 않음.

실행: tests/api_service/test_sso_auth.py, test_company_sso.py,
test_user_identity.py. pytest -p no:cacheprovider, PYTHONPATH=src.
사내 SDK·SSO 서버·실제 Redis/DB 연결은 하지 않았다.
초기 실행은 존재하지 않는 test_sso_users.py를 지정해 테스트가 실행되지 않았다.
올바른 파일 목록으로 위81개를 다시 실행해 통과했다.

uv run --locked --no-sync Ruff/ty/format 전체 검사 실행.
Ruff3075, ty759는 기존과 동일하며 새 진단 없음. 전체 format803개 통과.
전체 lint/type 통과로 표현하지 않는다. git diff --check 통과.

## 통합과 후속

베이스와 파생 브랜치를 origin에 atomic push하고 원격SHA를 대조한다.
앱 재시작 후의 실제 attribute/object_type으로 사내 SDK 요청·생성 경계를
검토한다. 아직 SDK가 FastAPI Request와 호환되는지 확정하지 않았다.
DB 연결/경로 하드코딩 정리는 기존 후속 순서로 유지한다.
