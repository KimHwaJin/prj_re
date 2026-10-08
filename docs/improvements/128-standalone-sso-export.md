# 128 — 다른 FastAPI용 SSO 독립 이식본

## 기준과 사용자 결정

- 기준: feature/refactor-base,1762285efab46051960f20b4f230f779cb7eb28d.
- 작업: feature/standalone-sso.
- 현재 서비스는 그대로 두고 외부 이식본만 별도 프로젝트로 제공한다.
- 사내 SDK 수정/원문 포함, dtest의 공통 패키지 전환은 하지 않는다.

## 변경

standalone/sso에 독립 pyproject·src/sso·tests·examples·README·SOURCE·CHANGELOG를
작성했다. 디렉토리만 복사하거나 sso-0.1.0-py3-none-any.whl로 설치할 수 있다.
실행 의존성은 FastAPI·Pydantic·Redis client뿐이며 dtest·DB 모델·Agent·Worker
import가 없다. 기존 src/dtest·tests·최상위 pyproject.toml/uv.lock·서비스 YAML
변경0을 유지했다. 우리 서비스는 이식본을 사용하지 않는다.

로그인·query 없는 callback·Redis 인증 세션과 단일 사용 복귀 정보·CSRF·
로그아웃을 추출했다. 이식본에는 GET /auth/session을 추가하여 자체 사용자 관리
API 없이 사용자 ID·csrf_token·expires_at을 읽을 수 있게 했다. namespace와
cookie_name은 필수이며 auto_register는 제거했다. 가입·비활성 계정·기본
프로젝트·관리자 권한은 호스트 UserDirectory.bind와 권한 Depends에서 결정한다.

SDK 연결은 create_sdk_adapter(factory)로 주입한다. 공식 SDK의 생성/import는
호스트의 private 모듈에 두며 요청 view가 확인된 string url·args/cookies.to_dict·
environ HTTP_HOST·서버 callback ORIGIN을 제공한다. 5개 직원 값의 변환은 공개
계약만 포함한다. SDK의 원문·회사 credentials·private import는 포함하지 않는다.
SDK의 실제 Cookie 전달/직원 검증/회사 client callback 규칙은 폐쇄망 확인 대상이다.

attach_sso는 기존 FastAPI 앱에 라우터를 붙이고 lifespan·middleware·전역 예외
handler를 덮어쓰지 않는다. 서비스 종료에서 runtime.close를 호출한다. 자체
Redis client는 종료하고 주입받은 client는 닫지 않는다. Redis 저장소 오류는
SSO 전용 HTTPException으로 plain FastAPI에서도503을 반환한다. 이식본 내부
RedisBackend를 단일 async 명령 계약으로 정리했다.

예제는 SDK 연결 전에도 health/demo가 뜨고 보호 API401·로그인503으로 종료한다.
가짜 인증 성공을 제공하지 않는다. 사용자는 SDK 생성 함수·설정·사용자 연결을
자기 서비스에 맞게 채운다. JSON 예제는 호스트 설정 주입 예시이며 YAML/env
사용을 제한하지 않는다. 사내 SDK 설치는 별도다.

## 검증

- 독립 package Ruff·ty·79자 포맷 모두 통과.
- wheel 빌드 성공. 내용은 sso package·typed marker·배포 metadata뿐이다.
- 별도 /tmp/dtest128-sso-env에 wheel과 FastAPI/Redis/test 도구만 설치했다.
  dtest·LangChain·SQLAlchemy가 설치되지 않은 것을 importlib로 확인했다.
- /tmp/dtest128-portability에 tests/examples만 복사하여 wheel import로 실행:
  68회귀 통과, 실제 Redis 미선택4개 skip. SDK 기본 계약·인코딩 누락 handler
  왕복·callback 만료/재사용·return_to 조작·CSRF·logout·TTL·저장소/SDK 오류·
  timeout·예제 실제 lifespan 기동/종료를 확인했다.
- 동일 wheel 환경에서 실제 로컬 Redis4회귀 통과: 동시 소비 중1개 성공,
  자동 TTL 만료, 다른 앱에서 callback/세션 공유, 자체/주입 client 종료 책임.
  임의 namespace만 사용했고 운영 키·Stream consumer group·FLUSH 변경 없음.
- 기존 서비스의 SSO/사용자/Swagger/설정/부트스트랩209회귀 통과.
- 최상위 uv run --locked --no-sync Ruff/ty/format 실행. 기존 Ruff3073·ty759
  외 추가0, 전체 포맷 통과. root ty에는 독립 source/tests 검색 경로를 지정했고,
  별도 package ty는 자체 project 설정과 wheel 환경으로 추가 진단 없이 통과했다.
  전체 레포의 기존 lint/type 오류는 해결한 것으로 보고하지 않는다.
- 기존 서비스·의존성·private SDK 연결 파일 변경0과 git diff --check 확인.

## 전달과 유지보수

README에 설치·wheel 제작·FastAPI 연결·SDK 생성·사용자 정책·API/설정·CSRF·
프론트·Redis 소유권·실제 Redis 테스트를 정리했다. SOURCE에 추출 기준과 원본
경로 대응·이식본만 변경된 항목을 기록했다. 양쪽은 자동 동기화되지 않으며 후속
수정은 package 버전/CHANGELOG에 기록하고 보안 수정의 양쪽 반영을 검토한다.

베이스와 파생을 병합·푸시한다. 기존 서비스 배포나 실제 회사 SSO 인증은 수행하지
않았다. 이식한 서비스의 폐쇄망 SDK/브라우저/Cookie 최종 검증이 남는다.
