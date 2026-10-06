# 105. FastAPI 사내 SSO 요청 어댑터·직원 정보 계약

이 문서는 105 당시 구현 이력이다. SDK 내부 요청 형식을 흉내 내던
SsoArgs·SsoRequest는 사용자 피드백에 따라 [106](106-sso-sdk-boundary.md)에서
삭제했다. 현재 SDK 연결 계약은 106과 최신 SSO 가이드를 따른다.

## 문제와 변경

기존 company.py는 인증 확인·로그인 URL 생성 전체가 미구현이었다.
사내 SDK는 Flask 예시의 `request.args.to_dict()`와 Cookie 헤더를 사용한다.
원본 FastAPI Request에는 args가 없으며 직원 계약도 사번·이름만 담고 있었다.

- `SsoRequest`에 원본 헤더와 쿼리 사본을 전달한다. `SsoArgs.to_dict()`는
  사본을 반환한다. Flask 앱·전역 request/session·새 의존성을 추가하지 않는다.
- 원본 쿠키를 `check_day_cookie`로 검증한 뒤 성공할 때만 `get_sso_info`를
  호출한다. 미인증은 None, 장애·잘못된 성공 응답은 오류로 처리한다.
- 다섯 값의 tuple/list를 순서대로 사번·이름·영문 이름·부서·메일에 연결한다.
  `VerifiedEmployee`의 새 필드는 english_name·department·email이다.
  기존 세 번째 위치 인자인 valid_until_epoch 계약도 유지한다.
- 브라우저가 보낸 ORIGIN은 인증 확인 때 제거하고 로그인 URL 생성 때
  서버가 만든 복귀 주소로 덮어쓴다. SDK.redirect_url을 그대로 사용하며
  공통 runtime이 이동 대상 allowlist를 검사한다.
- 동기 SDK는 기존 SyncSsoAdapter의 thread pool에서 실행한다.
- SDK의 실제 import 경로는 외부에서 제공받을 수 없으므로 `_create_sdk`
  한 곳만 폐쇄망에서 채우도록 남긴다. 이 지점은 여전히 미구현이며 설정만
  바꿔도 실제 회사 로그인이 되는 상태는 아니다. 조용히 mock으로 대체하지 않는다.
- SSO 문서의 옛 패키지 경로·중첩 YAML·관리자 초기화 명령을 현재 구조로
  수정하고 회사에서 연결·확인할 부분과 직원 필드 의미를 명시한다.

## 정보 전달 범위

검증된 다섯 값은 UserDirectory.bind에 모두 전달한다. 현재 dtest 사용자 DB는
사번·이름으로 연결/최초 등록하며 영문 이름·부서·메일을 영속화하지 않는다.
User DB schema·users/me 응답·관리 API는 변경하지 않는다. 기존 계정 이름·role은
보존한다. 첫 직원의 일반 사용자·기본 프로젝트 자동 등록 정책도 유지한다.
Redis에는 내부 UUID·CSRF·만료만 저장하며 회사 쿠키·직원 상세 정보는 넣지 않는다.

추가 설정 키·Flask 의존성·DB migration은 없다. 사내 적용은 생성 함수 연결,
SDK 설치, 기존 SSO_ADAPTER_FACTORY·origin/allowlist 설정으로 진행한다.
자세한 연결 예제는 [SSO 가이드](../sso-authentication.md)를 따른다.

## 검증

| 검증 | 결과 |
|---|---|
| SDK 요청 계약·직원 매핑·미인증·장애·공통 로그인 왕복 | 신규 SDK 계약 회귀 통과 |
| SSO 관련 전체 회귀 | 71 통과, 실패·제외 0 |
| 실제 PostgreSQL | 직원 계약 → 일반 사용자·기본 프로젝트 등록, 기존 사용자/권한 정책 통과 |
| 실제 Redis | 로그인 TTL·Streams 공존·독립 로그인 연결풀 통과 |
| 기존 쿠키 기반 Run·resume·GET/POST SSE | 별도 임시 runtime/checkpoint DB에서 통과; Executor 제출 제외 |
| 설치 wheel | checkout import 없이 API 32개 경로·역할 Agent 5개·리소스 구성 통과 |
| 새 계약·SDK 모듈·SDK 테스트 Ruff/ty | 모두 통과 |
| 전체 Ruff format | 통과, 79자 설정 유지 |
| 전체 Ruff | 기존 3520 → 3501 진단, 신규 0; 전체 미통과 |
| 전체 ty | 기존 772 → 772 진단, 신규 0; 전체 미통과 |
| git diff --check | 통과 |

검사는 고정 버전 Ruff 0.16.10·ty 0.0.84로 수행했다. uv는 기존 테스트 도구
환경을 `--locked --no-sync`로 재사용하고 ty에 Python 3.11 의존성 환경을 지정했다.
첫 실제 DB 검증에서 새 테스트의 이름 필드 기대값을 name으로 잘못 작성한 것을
현재 API의 user_name으로 수정했다. 최종 전체 71개는 수정 후 다시 실행한 결과다.

이번에 만든 dtest-sso-pg-105·dtest-sso-redis-105 컨테이너만 사용했다.
identity_test·agentic_runtime_test·agentic_checkpoint_test DB와 임시 Redis로
분리했고 테스트 후 컨테이너를 제거했다. 기존 로컬 Executor·DB·Redis는 변경하지 않았다.
SDK 자체는 테스트 대역이며 실제 회사 SSO·실제 LLM·Executor 실행은 검증하지 않았다.

## 폐쇄망에서 남은 확인

`_create_sdk`에 실제 import/생성을 넣고 SDK 자체의 네트워크 timeout을 설정한다.
확인한 인터페이스 외에 다른 Flask 전용 속성을 사용하는지, get_sso_info의 실제
다섯 값 타입/순서가 일치하는지, 브라우저 로그인 복귀와 쿠키 정책이 맞는지 확인한다.
회사 인증 만료는 현재 다섯 값에 없으므로 서비스의 기존 고정 TTL을 적용한다.
회사 SDK에 별도 만료/프로토콜 검증 규칙이 있다면 공식 규격대로 연결한다.

작업 브랜치는 feature/corporate-sso-adapter이며 리팩토링 베이스에 병합해
origin에 게시한다. 실제 회사 배포·서비스 재기동은 이번 작업에 포함하지 않는다.
