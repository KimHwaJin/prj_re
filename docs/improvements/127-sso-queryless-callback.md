# 127 — SDK 수정 없는 SSO 복귀 처리

## 기준과 결정

- 기준: feature/refactor-base, cd97282.
- 작업: feature/sso-queryless-callback.
- 제약: 사내 SDK 소스를 수정할 수 없음.
- 원인: SDK handler의 URL 값에 query를 그대로 넣으면 & 뒤의 복귀 정보가
  handler API의 별도 파라미터로 분리됨.126의 SDK 수정 제안은 적용하지 않는다.

## 서비스 변경

로그인 시작 API와 프론트 버튼은 그대로다. 직원 미검증이면43자 난수 flow_id를
생성하여 /auth/login/sso/callback/{flow_id}를 SDK ORIGIN으로 전달한다.
callback은 query/fragment가 없다. 검증된 return_to/target/expires_at은 기존
Redis client/pool의 별도 flow key에 저장한다. SDK URL은 재작성하지 않는다.
SDK 반환/URL 검증 성공 후 저장하므로 SDK 오류에는 flow를 생성하지 않는다.

새 GET callback은 Redis context를 Lua GET+DEL로 원자적으로 한 번 소비한다.
미존재/만료/재사용은 SDK 호출 전에400. 저장소 장애/잘못된 record는503이다.
직원 미검증은401로 멈추고 다시 SSO로 보내지 않는다. 직원 검증 성공 후에만
기존 사용자 연결/등록·서비스 세션 발급·최종 UI/docs 이동을 실행한다.
return_to/target query 조작은 저장값을 덮어쓰지 못하며 경로를 다시 검증한다.

SDK가 handler200 이후 이번 서버 생성 callback을 정확히 반환하는 경우는
그 callback 이동만 허용한다. 다른 임의 API URL은 기존 origin allowlist로
검증한다. 이 이동이나 flow_id는 인증 증명이 아니며 SDK 직원 검증을 유지한다.
과거 query sso_callback=true의 미인증 가드는 남지만 새 복귀에는 사용하지 않는다.

SSO_LOGIN_FLOW_TTL_SECONDS는 기본300,60~1800초로 중앙 SsoSettings/YAML/env
우선순위를 따른다. 인증 세션 TTL·Run/HITL 보존과 별개다. 같은 Redis/namespace를
쓰는 여러 API 인스턴스가 공유한다. 추가 Worker·DB 테이블·Redis 연결풀은 없다.
EVAL 권한을 사용하며 Redis6.2 GETDEL을 요구하지 않는다. 회사 Cookie/프로필은
flow에 저장하지 않고 Redis key에는 flow_id를 hash한다.

company.py·pyproject.toml·uv.lock은 변경0이다. 사내 SDK·private 생성 연결과
사용자가 설치한 내부망 패키지를 보존한다. 실제 SDK 소스 수정은 하지 않는다.

## 검증

- 실제 HTTP SDK double 왕복6개: HTTPS 프로젝트·HTTP localhost demo/docs 각각
  정상 SDK/사용자 확인한 인코딩 누락 handler를 그대로 구현한 SDK2종.
  callback에 query가 없고 handler가 받은 URL이 보존됨, 최종302·서비스
  Cookie·프로필 경계를 확인. 회사 네트워크와 실제 SDK는 사용하지 않았다.
- SDK가 정확한 callback을 바로 반환해도 인증되지 않음: 미검증 callback401,
  users/me401. 임의 다른 API URL은502.
- callback query로 복귀 정보 변경 불가, 만료/모르는/잘못된 flow400,
  재사용400, 저장소 장애503/복구, 잘못된 record·NX 충돌을 확인.
- 설정 YAML 우선순위·TTL 범위 및 OpenAPI의 public callback/auth 구분 검증.
- SSO/신원/Swagger/설정/부트스트랩 관련209회귀 통과.
- 로컬 executor Redis에서 랜덤 test namespace만 사용한 실제7회귀 통과.
  동시2개 소비 중1개만 성공, 자동 TTL 만료, 로그인/flow 키 분리, API1에서
  시작 후 API2에서 callback·세션 공유·재사용 거절을 확인. 기존 Streams와
  로그인 연결풀 회귀3개도 포함. 운영 키 삭제·FLUSHDB·서비스 그룹 변경 없음.
- uv run --locked --no-sync Ruff/ty/format 전체 실행. Ruff3073·ty759 기존 진단
  외 추가0, 전체 format 통과. git diff --check·보호3개 파일 변경0 확인.

## 통합과 폐쇄망 확인

베이스·파생을 병합·푸시한다. 서비스 미배포이며 실제 회사 인증 성공은 미확인이다.
사용자는 기존 SDK를 유지하고 pull 후 앱을 재시작한다. 새 TTL 설정은 기본값으로
동작하므로 필수 YAML 변경은 없다. /demo 로그인 버튼에서 시작하고 돌아오는
주소가 callback/{flow_id}인지 확인한다. 최종 /demo 및 users/me200이면 성공이다.
401이면 query 누락/반복 이동은 차단됐지만 Cookie·직원 검증이 남아 있다.
400은 만료/재사용이므로 새 로그인으로 시작하며503은 저장소/SDK를 확인한다.
회사 client_id의 허용 callback 경로 규칙과 Cookie/ticket 처리 계약은 폐쇄망에서
검증한다. 임시키·Cookie·토큰값·사내 SDK 소스를 외부로 공유하지 않는다.
