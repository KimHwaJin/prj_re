# 046. Agent API와 Workflow JSON 현재 계약 문서

| 항목 | 내용 |
|---|---|
| 상태 | 문서·예제·schema·관련 회귀/wheel 검증 완료 / 통합·게시 예정 |
| 날짜 | 2026-10-01 |
| 작업 브랜치 | feature/api-workflow-docs |
| 기준 commit | 0e35b625644e904f8e7e0c5497d1d4a9cf07dedd — 045 |
| 문서 commit | fb21bba18cc90b808276d7e9fd0d223ad3638bb3 |

## 문제와 변경

Agent API 계약은 일부 이전 인증/기능 상태 문구를 포함했고, Workflow 설계는 compiler 미구현이라고 남아 있었다. 현재 코드와 일치하는 프론트·현업 DS 개발 안내가 필요했다. 사용자는 문서 완료 후 지금까지 작업을 베이스에 병합하고 새 origin에 푸시하도록 요청했다.

- [Agent API 전체](../public-run-api.md): 8개 operation, 인증·멱등성, 새 요청, 7종 resume, 4종 HITL, Run 상태·결과·SSE·로그·목록·취소·오류.
- [Workflow JSON 작성](../workflow-json-reference.md): 2.0-draft 정의 전체, 등록 Skill/Tool, 입력 binding·조건·decision·편집·정책·산출물·승인 snapshot, 기존 1.3 CRUD의 실제 제한.
- [검증된 schema·예제](../contracts/agent-api/README.md): 요청 9개·HITL 4개·추가 이벤트 3개·Run 전체 응답 2개·OpenAPI 및 public payload schema.
- Workflow 기본/조건부 예제 2개와 legacy 1.3 구조 예제. runtime/schema/현재 자산 참조를 확인한다.
- README·개발 안내·기존 설계·repository 안내의 이전 상태를 현재 계약으로 연결한다. 040~041 문서의 인증 헤더를 변경한다.
- packaged/docs Workflow schema의 title/$comment만 현재 사용 상태로 바로잡는다. 검증 규칙·버전·API 실행 코드는 변경하지 않는다.

실제 자동 OpenAPI의 SSE/302 response content annotation 및 유연한 result/interrupt를 문서에서 구분한다. runtime annotation을 고치거나 완료되지 않은 SSO·Dataset·Workflow CRUD·pgvector·Gaia·HTML/Artifact 기능을 완성으로 표시하지 않는다.

## 검증

- API JSON 예제 18개를 현재 Pydantic 계약으로 검증했다. 요청 9개, 이벤트 7개, Run 응답 2개이며 승인/편집 파라미터 및 등록 자산도 대조했다.
- 새 Workflow 기본/조건부 예제 2개를 현재 schema·compiler 계약·실제 등록 Skill/Tool·함수 인자로 검증했다. legacy 1.3 예제는 기존 Pydantic 구조를 확인했다. 문서 본문의 전체 Workflow 예제도 같은 validator를 통과했다.
- 사용자 식별·SSO·Swagger JavaScript·패키지 경계·계획 Runtime·조건 계약·compiler·계획 수정 관련 pytest **97 passed**, 3 warnings, 6.50초. warnings는 기존 no-checkpointer durability 경고다.
- 변경 파일 37개에 대해 상대 문서 링크, JSON 파일 25개와 본문 JSON 블록 9개, 신규 줄 공백·민감 endpoint 패턴을 확인했다. Workflow schema 두 사본은 동일하며 title/$comment를 제외한 검증 규칙은 기존과 같다.
- wheel 빌드와 격리 검증 통과: source checkout import 없이 OpenAPI 36 paths, mock graph 4 steps, 역할 Agent 12개, 프롬프트·리소스·통합 Workflow 패키지를 확인했다. 테스트 및 제거한 패키지는 wheel에 없다.

API·LLM·DB·Executor의 새로운 부하/E2E 실행은 이번 문서 작업 범위가 아니다. 045에서 완료한 전체 **881개 회귀** 결과는 이전 검증으로 보존하며 이번에 다시 실행한 결과로 표시하지 않는다.

## 통합과 게시

기준 feature/refactor-base에 038~046을 통합하고 origin=KimHwaJin/prj_re에 관련 새 파생 브랜치와 베이스를 게시할 예정이다. 실제 통합·원격 SHA 확인 후 결과를 기록한다. 원본 feature/total_merge_v1 checkout의 사용자 변경, legacy-origin, 컨테이너·DB·Executor는 수정하지 않는다. 강제 push·브랜치 삭제는 수행하지 않는다.
