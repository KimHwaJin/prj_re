# 096 일반 서비스 /demo에 기능 콘솔 통합

2026-10-06 / feature/service-demo-console / 시작 베이스26d5fbf.
[클론 후 실행 안내](../service-demo-console.md) · [진단 도구 안내](../../tools/test-console/README.md)

## 문제

일반 app.py는 예전 demo.html을 제공하고 새 기능 콘솔은 별도 진단 실행기의 /test-console에만 연결되어 있었다. 사용자가 새 레포를 클론하고 기존 DB·Redis·Executor 설정으로 서비스를 띄우려 해도 임시 DB/별도 JSON 실행 방법을 안내해야 했고 실행·설정 경로가 불필요하게 갈렸다.

## 변경

- 새 HTML을 src/api_service/static/demo.html 한 파일로 관리한다. 예전 demo 내용을 교체하고 tools/test-console/index.html 사본을 삭제했다. 개발 테스트·예제 갱신·진단 실행기와 문서 링크도 같은 파일을 사용한다.
- 일반 app.py의 /demo는 같은 서비스의 API·OpenAPI 주소·SSO 복귀 경로와 공개 모드 표시를 주입한다. root_path와 사용자 지정 API prefix를 반영하며 Host 기반 절대 주소를 HTML에 넣지 않는다.
- 화면이 실제 API 모드로 자동 연결되고 로그인은 /demo로 복귀한다. 별도 진단 /test-console은 해당 경로로 복귀한다. mock 모델과 실제 모델을 구분한다.
- 패키지 HTML 읽기는 thread에서 수행하고 프로세스 안에서 캐시한다. 응답은 no-store이며 표시 문자열의 script 탈출을 막는다. 중앙 설정의 비밀정보는 주입하지 않는다.
- 기존 API·Worker·DB·Redis·Executor 설정과 lifespan을 그대로 사용한다. 일반 서비스에 테스트 직원·관리자 로그인·인증 우회를 설치하지 않는다. 플랫폼이 만든 앱의 자체 화면 라우터를 덮어쓰지 않는다.
- 일반 실행·schema 준비·SSO 설정 문서를 제공한다. HTML 때문에 DB를 생성·삭제하거나 migration하지 않는다.

## 검증

- Python 관련 회귀15개 통과: 서비스 /demo·401 인증 유지·비밀정보 비노출·mock/실제 표시·root_path/custom prefix·문자열 탈출·Swagger JS·진단 모드와 /test-console 렌더링.
- Node10개 통과: 기존 SSE/HITL 편집/샘플 동작과 새 same-origin API·OpenAPI·로그인 복귀.
- 깨끗한 소스로 만든 wheel의 api_service/static/demo.html과 api_service/web_console.py가 현재 소스와 byte 단위로 일치함을 확인했다. 레포 밖 wheel 설치 경로에서도 /demo200과 미인증 API401을 확인했다. 기존 package-data static/*.html과 Docker COPY src로 배포 경로에 포함한다. 이미지 빌드·기존 서비스 재기동은 하지 않는다.
- 처음 검증에서 Request의 지연 annotation 해석으로422, 새 테스트의 전역 설정 격리 누락이 확인되어 타입을 모듈 범위로 올리고 테스트 snapshot을 격리한 뒤15개를 다시 통과했다.

실제 브라우저·사내 SDK SSO 왕복·LLM/Executor E2E를 이번 변경에서 다시 실행하지 않았다. 기존 통과 이력을 새 검증으로 재계산하지 않는다. 변경된 경계의 자동 검증과 수동/배포 검증을 구분한다.

## 남은 범위

사내 SSO adapter가 미설정인 일반 서비스 로그인은503이다. 이것은 HTML 연결로 해결할 대상이 아니며 기존 SSO 연동 가이드를 따른다. 격리 실험용 직원 검증 대체는 기존 진단 실행기에만 둔다. 완전 중복 임베딩 검색 대표화·실제 embedding 품질·ML 의존성 용도 분리·Gaia 플랫폼 통합은 이번 범위에 포함하지 않는다.

작업 브랜치에 변경을 기록하고 게시한다. 베이스 병합·운영 배포는 별도 상태로 다룬다.
