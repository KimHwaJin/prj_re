# 089 테스트 로그인·실제 LLM 분리 및 브라우저 검증

날짜: 2026-10-05. 브랜치 feature/real-model-test-console, 088의 b69ca9f에서 분기. 구현·로컬 검증 완료, 베이스 미병합·미푸시·운영 미배포.

## 문제와 변경

기존 --local-fixtures가 직원 검증과 모델 fixture를 함께 설치해 HTML 콘솔에서 실제 LLM을 시험할 수 없었다. --test-login과 --model fixture/real/configured를 분리했다. 기존 --local-fixtures는 축약 옵션으로 유지한다. 실제 모델 설정은 private source에서 allowlist만 읽고 fixture 설정/누락/잘못된 endpoint를 거절한다. 모델 alias는 진단 프로세스에만 적용한다. 별도 모델 검증 도구도 같은 loader를 사용한다.

HTML에 인증·모델·Executor의 공개 모드 설명을 주입한다. key·모델 endpoint는 주입하지 않으며 다른 API 주소로 연결하면 이전 서버의 모드 라벨을 재사용하지 않는다. 실제 모드에서는 고정 응답/fixture client를 설치하지 않는다.

실제 브라우저에서 Run/Session 목록 상태 잔존과 POST SSE 접수 시 새 입력 잠금·종료 표시 문제를 확인했다. 상세/SSE의 현재 Run과 Session 조회를 해당 목록에 반영하고, 접수된 로컬 실행을 즉시 입력 잠금에 포함했다. POST SSE 접수 후 상태를 조회하고 terminal 종료를 표시한다. 상시 폴링은 추가하지 않는다.

변경: scripts/diagnostics/model_connection.py·serve_test_console.py·verify_api_contract_flow.py·verify_real_model_parameters.py, src/api_service/static/demo.html·테스트·README. 진단/개발 화면만 변경하며 서비스 API·Agent graph·Tool·Executor 규격·DDL은 유지한다.

## 검증과 결과

[상세 보고서](../reports/test-console-real-model-2026-10-05/README.md).

- pytest 진단12개·Node9개·고정 모델 실제HTTP/Executor12개 통과.
- 실제 내장 브라우저+qwen+DB/checkpoint/Store/Worker/Redis/Executor로 분석1개·후속 설명3개 success.
- max_val/x를 max_val로 수정·dependency422 수정값 보존·정상 제외 편집/복원·동일 Run 승인·실제 통계 max_val만 확인.
- HITL 새로고침/revision·SSE 해제/재접속·실행 중 복귀·POST SSE 입력 잠금/완료·세션 전환·로그아웃/재로그인 복원 확인.
- 실제 모델 콘솔18102/임시DB53603은 사용자 확인용으로 유지. 회귀용18103/53604는 정리. 기존 콘솔과 Compose·Executor 이력은 유지.

## 후속

보고서가 사용자가 최종 대상에서 뺀 x를 원래 목표로 설명하는 사례를 발견했다. 실행은 max_val만 수행됐으며 원인은 미확정이다. 최종 승인 계획과 보고서/후속 해석 정합성을 다음 Agent 기능 검토로 기록했다. 최소 Markdown 표 렌더링은 사용성 후속이다. 사내SDK·Gaia/배포·Registry/보고서Artifact·기존 성능/운영/모델 호출 수 보류는 유지한다. 단일 사용자 시험을 처리량 개선으로 해석하지 않는다.
