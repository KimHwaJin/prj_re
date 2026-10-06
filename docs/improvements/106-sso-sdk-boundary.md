# 106. 사내 SDK 내부 요청 모방 제거

## 사용자 피드백과 변경

사내 라이브러리의 args/to_dict 구현은 SDK 내부 책임이라는 피드백을 반영한다.
105에서 넣었던 SsoArgs·SsoRequest를 둘 다 삭제했다. 별도 Flask 요청 객체를
만들거나 원본 Request의 query·headers를 수정하지 않는다.

SDK 연결 함수의 서비스 측 계약을 다음처럼 바꿨다.

```python
def _create_sdk(
    request: Request, return_url: str | None = None
) -> CompanySdk: ...
```

- 인증 확인: 원본 FastAPI Request와 return_url=None을 전달한다.
- 로그인 URL 생성: 원본 Request와 공통 runtime이 만든 복귀 주소를 전달한다.
- 폐쇄망 연결 함수는 공식 SDK 생성·복귀 주소 설정 방식으로 구현한다.
  원본 query의 임의 ORIGIN을 서버 복귀 주소 대신 사용하지 않는다.
- SDK 내부 요청 가공·Flask 객체 형태는 서비스에서 추측하지 않는다.
- 직원 다섯 값 매핑·미인증/장애 구분·동기 SDK의 thread pool 실행·쿠키/CSRF와
  사용자 최초 등록·Redis 저장 정책은 유지한다.
- 테스트는 사내 SDK의 내부 args 형태 대신 원본 요청 전달과 명시적인 복귀
  주소 계약을 검증하도록 바꿨다. 직원 정보·실패 처리·로그인 왕복도 확인한다.

공식 SDK 연결 함수는 여전히 미구현이다. 원본 Request를 전달하는 서비스 계약이
SDK의 FastAPI 직접 지원을 입증하는 것은 아니다. 실제 연결은 폐쇄망에서 공식
사용법으로 채운다. 현재 요청 인터페이스를 SDK가 받는지에 대한 추측 코드는 없다.
[현재 SSO 연결 안내](../sso-authentication.md)를 따른다.

## 검증

- SDK·인증·Swagger 단위/HTTP 회귀: 60 통과.
- 실제 임시 PostgreSQL·Redis 및 쿠키 Run·resume·GET/POST SSE를 포함한
  관련 전체 회귀: 72 통과, 실패·제외 0. 실제 SDK·LLM·Executor 실행은 제외.
- 변경 SDK 모듈·테스트의 Ruff·ty 통과. 전체 포맷·diff 공백 검사 통과.
- 전체 lint·타입의 기존 진단은 남아 있다. 신규 진단은 추가하지 않았다.
- 추가 의존성·설정·DB migration은 없다. packaging 경로도 변경하지 않았다.

이번 검증에서 만든 dtest-sso-pg-106·dtest-sso-redis-106만 사용하고 제거했다.
기존 컨테이너와 DB는 변경하지 않았다. 105는 당시 구현 이력으로 보존하고
현재 안내에는 삭제 사실과 새 연결 계약을 반영했다.

작업 브랜치는 feature/sso-sdk-boundary이며 feature/refactor-base에 병합해
origin에 게시한다. 사내 SDK 연동·배포 검증은 폐쇄망에서 남아 있다.
