# 086 기능 테스트 HTML 콘솔

- 날짜: 2026-10-04
- 브랜치: feature/functional-test-console
- 기준: feature/api-contract-flow-verification / 635a4f8 (085)
- 상태: 구현·자동 검증 완료, 실제 브라우저 시각 검증 미완료. 베이스 미병합·미푸시·미배포

## 문제와 요청

사용자가 현 기능을 직접 확인할 새 화면을 요청했다. 별도 프론트 서비스가 필수는 아니며 기존 demo는 재사용할 필요가 없다는 결정에 따라, 현재 API와 typed HITL 계약을 쓰는 독립 HTML을 만든다. 운영 UI 개발/새 API 설계·Agent 로직 변경·기존 demo 이행은 이번 범위가 아니다.

## 변경

- src/api_service/static/demo.html: 외부 의존성 없는 단일 HTML. 파일을 직접 열면 서버 호출 없는 샘플, 실제 모드는 cookie/CSRF·현재 POST 접수/GET SSE/POST SSE를 사용한다. 샘플과 실제를 계속 구분하고 모델/SSO fixture 여부를 표시한다.
- 프로젝트/세션·지침·단일 메모리 문서, 네 HITL 종류·선언된 파라미터·도구 제외·승인 정책, 결과 Markdown·승인 계획·사용자 제외/실행 skip, diagnostics/invocations/logs, 실제 OpenAPI 기반45개 업무 operation 요청 도구를 제공한다. User·Workflow·Message 등의 관리 동작은 원래 API·권한을 사용한다.
- 동일 요청 재전송은 정확한 key/body를 유지한다. SSE frame은 chunk/CRLF/multiline을 처리하고 sequence를 중복 제거한다. snapshot.cursor는 durable cursor로 쓰지 않는다. 선택 변경은 이전 연결을 끊고 늦은 응답을 구분한다. 새 입력/HITL 액션은 Session availability를 따른다. 상시 상태 폴링을 추가하지 않았다.
- 메모리409에서 로컬 편집을 보존하고 최신 서버 문서를 별도로 보여준다. 로그인/페이지 복귀에는 project/session/run ID만 보관한다. 쿠키·CSRF·resume token은 저장/trace에 노출하지 않는다. 외부 데이터는 textContent/DOM으로 렌더링하고 임의 HTML/코드를 실행하지 않는다.
- scripts/diagnostics/serve_test_console.py: 같은 origin으로 진단 앱에만 HTML을 붙이는 loopback 도구. 기존 create_app/lifespan을 그대로 쓰며 명시적 fixture 옵션에서만 SDK 직원 결과·모델 transport를 교체한다. 임시 DB는 독립 PostgreSQL17/TCP 준비 확인·전용 namespace·Workflow 저장소를 사용한다. 테스트 관리자 bootstrap은 임시 fixture DB에만 허용하고 기존 production 인증을 변경하지 않는다. 정상 종료 시 전용 자원을 정리한다.
- scripts/design/update_test_console_fixtures.py는 공용 JSON 예제와 현재 create_app의 OpenAPI로 샘플을 갱신한다. 생명주기를 실행하거나 DB/Executor를 호출하지 않는다. 이전 demo·운영 router/config/schema/Agent/Worker/Tool·배포 이미지는 수정하지 않았다.

## 검증

- Node core/preview **7 passed**: chunked SSE·heartbeat·multiline JSON·snapshot, 0/false/null/미입력, 편집 정책·plan revision, 비밀값 redaction, 네 HITL과 입력 잠금, 샘플 편집/승인, 현 API 목록.
- 실제 HTML inline controller를 개발용 DOM double에서 실행하고 실제 HTTP/PG/Redis/Worker/Executor와 연결하여 **11 passed**: 쿠키·CSRF·기본 프로젝트, 프로젝트 지침/Store 메모리, 세션/SSE/HITL, 파라미터 편집·Run 유지, 실제 Executor 성공/finalize·사용자 도구 제외 표시·리포트/입력 해제, 정확한 key/body 재전송, 재접속, POST SSE 후속 답변, OpenAPI·권한, 메모리 충돌 보존, 페이지 복귀 선택 복원. 일반 사용자 모드에서403, 관리자 모드에서 사용자 목록 접근을 각각 확인했다.
- 별도 실제 관리자 HTTP **6 passed**: fixture admin 로그인·사용자 목록·role 포함 등록·수정·soft-delete/삭제 상세·Workflow 목록. 관리 UI의 실제 브라우저 form 클릭 검증으로 보고하지 않는다.
- Python/JavaScript 문법·git diff --check 통과. 관리자/일반 임시 서버 종료에서 기존 Compose를 유지하고 해당 DB만 제거됨을 확인했다. 초기 PG 준비 검사와 종료 signal 처리의 개발 도구 문제를 수정했다. 기존 no-checkpointer durability 경고는 유지하며 운영 Agent를 변경하지 않았다.
- 실제 브라우저 도구는 Mac 잠금/탭 연결 문제로 실패하여 **픽셀·반응형 화면·브라우저 클릭·다운로드·SSO 브라우저 왕복은 미검증**이다. SDK와 모델 응답도 fixture다. 전체src suite는 production 변화가 없어 반복하지 않았다. 성능·처리량 개선 측정이 아니다.

[화면과 실행 안내](../../tools/test-console/README.md), [검증 결과 JSON](../reports/test-console-2026-10-04/result.json)을 참고한다. 최신 테스트 서버는18100에 사용자 확인용으로 유지한다. 실제 Executor 제출·테스트 관리자 모드이며 임시 DB는53601이다. 앱 종료 시 전용 DB/Workflow/Redis 자원은 정리되고 Executor 실행 이력은 남는다. 기존 source checkout은 변경하지 않았다.

## 후속

Mac에서 화면을 직접 열어 레이아웃과 실제 브라우저 동작을 확인한다. 실제 사내SDK·실제 모델 품질·플랫폼 proxy/root_path 이행은 별도다. Dataset Registry·이미지/파일·보고서 Artifact 등록·Workflow CRUD 새 규격·메시지 정리·모델 호출 최적화 등의 기존 보류를 완료 처리하지 않는다. 085의 파라미터 노출 범위는 서버의 현 계약을 그대로 표시하며 다음 정책/구현 검토 항목으로 유지한다.
