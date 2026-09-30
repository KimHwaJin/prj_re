# 041. 실행 전 자연어 계획 재작성·자유 코드 계획

| 항목 | 내용 |
|---|---|
| 상태 | 구현·전체 회귀·실제 Executor/모델·wheel 검증 완료 / 베이스 미병합 |
| 시작일 / 완료일 | 2026-10-01 / 2026-10-01 |
| 브랜치 | feature/agentic-plan-revision |
| 기준 commit | d8e6525845d8dca73988edd554a1167c96948c13 — 040에서 분기 |
| 구현 commit | 7035a0edb93ec354a13acf3114e8f800ede84c8c |
| 배포 상태 | 격리 진단 API만 실행 후 종료. 기존 사용자 체크아웃·Compose 설정 유지. 베이스 병합·push·배포 미수행 |

## 문제와 변경

040은 등록 자산 계획 승인과 실행 실패 후 수정을 지원했지만, 실행 전에 모든 후보를 거절하고 자연어로 다른 계획을 요청하려면 취소 후 새 Run이 필요했다. 최초 자유 함수 계획과 추가 질문의 구조화된 resume도 없었다.

| 영역 | 이전 | 이번 구현 |
|---|---|---|
| 사용자 입력 | 계획의 파라미터·Step 제외·승인 | 같은 Run에서 replan + feedback, 추가 질문 answer_clarification |
| Agent 역할 | 최초 conversation / 실행 후 repair | 독립 plan_revision create_agent, 역할 폴더에 선언·prompt 배치 |
| 기억할 내용 | 세션 대화와 현재 계획 | 최초 요청·사용자 편집/제외값·거절/답변 이력·이전 후보를 재작성에 전달 |
| 자유 코드 | 실패 후 허용된 수정만 | 재작성 요청 이후 실행별 custom 함수 제안·검증·승인 snapshot |
| 승인 | 최종 사용자 승인 필수 | 자유 계획만 별도 중앙 설정. 완전한 후보 하나 외에는 확인 유지 |
| Workflow | 등록 Skill·Tool 기반 | 그대로 유지. custom 함수는 전역 registry에 등록하지 않고 승격 가능 여부 구분 |
| API/SSE | edit_plan/approve_plan 및 실행 중 HITL | 같은 POST/SSE에 planning_question, replanning/answered/auto_approved 추가 |
| 제한 | 실행 실패 수정 예산 | 실행 전 사용자 재작성 횟수는 별도 Run 공통 제한 |

최초 승인 전에 Execution을 생성하지 않는다. 자유 계획을 선택·편집·승인하면 기존 compiler·Executor API·Operation·Finalize를 재사용한다. 원래 Tool 파일과 catalogue는 덮어쓰지 않는다. `execution/sources.py`의 함수 구조·signature 검증을 실행 전 자유 계획과 실패 후 수정이 공유한다.

## 실제 연계에서 발견한 지점

최초 prompt_json 실제 모델은 functions가 들어간 후보에서 plans 배열의 닫는 괄호를 누락한 JSON을 두 번 반환했다. 다음 prompt_json 시험은 최종 content가 비어 있었다. 각각 검증 한도 이후 원래 계획 화면을 보존했으며 Executor에 제출하지 않았다. 올바른 JSON 구조 예시와 origin_tool_id/새 함수 규칙을 보완했다. 해당 모델의 prompt_json 모드가 다양한 자유 계획에 안정적이라고 주장하지 않는다.

ProviderStrategy 전환 시험에서는 아래 문제를 차례로 확인했다.

1. 합성 경계의 tools=[]를 실제 gateway가 HTTP 400으로 거절했다. 공통 CompatibleChatOpenAI가 LangChain public bind_tools의 변환 결과는 유지하고 빈 도구 관련 필드만 생략하도록 보완했다.
2. 일반 dict인 definition의 native schema가 빈 객체를 허용해 모델이 definition={}를 반환했다. RevisionReply에 기존 전체 Workflow 구조를 연결하고 내부 recursive reference를 이름 공간에 맞춰 rebasing했다.
3. 실제 native grammar가 uniqueItems/propertyNames를 지원하지 않았다. 생성 schema에서만 해당 keyword를 제외하며 실행 전에는 원래 전체 validator가 중복·이름·의존성·입력값을 검사한다. Workflow 등록 규격은 변경하지 않았다.

4. 모델이 함수 선언만 반환하거나 여러 줄 code 문자열의 JSON escape가 잘못된 경우도 실행 전에 차단했다. 함수 출력은 header와 indent/text 코드 줄로 명확히 분리했다. 서비스는 정해진 들여쓰기로 조립하고 AST를 검증하며, 누락된 코드를 추측으로 채우지 않는다.

5. 전체 definition을 다시 생성하며 수정하지 않은 finish.data를 누락하고 review_mode와 맞지 않는 필드를 넣는 사례를 확인했다. 현재 후보 기반 base_plan_id/patches 경로를 추가하여 변경된 Step·인자만 반영하고 기존 연결·정책·입력 출처를 보존한다. 이전 자유 함수도 동일 후보의 동일 Step/Tool일 때만 이어 사용한다. 완전한 새 definition도 지원하며 조립 뒤에는 전체 검증을 적용한다.

최종 실제 on-prem 재작성 역할은 provider_json_schema로 성공했다. 고정 초기 계획에서 transform만 새 함수로 교체하라는 자연어 요청을 받아 실제 Executor/Jupyter에 제출했고, 입력 [2,4,6]에 각각 +1 후 2로 나눈 값 [1.5,2.5,3.5]와 합계 7.5, Finalize/terminal 완료를 확인했다. 승인 한 번을 포함한 재작성→완료는 36.977초다. 이 한 건으로 다양한 업무의 모델 출력 신뢰성을 보장하지 않는다. prompt_json 및 이전 native 실패 10회도 함께 보존한다.

이전 실패는 reports의 별도 JSON으로 보존한다. 개인 config/API key/Phoenix 원문 trace·모델 작성 소스를 저장소에 넣지 않는다. 공개 화면은 검증 실패 안내만 받으며 세부 검증 오류는 private checkpoint에 남긴다. 모델 전송 오류는 기존 실패 경계를 따르고 검증 실패나 성공으로 바꾸지 않는다.

## 검증 결과

전체 API·Agent 회귀 **727개 통과**, 64 warnings, 304.17초. 이전 040의 712개에 신규 15개가 포함됐다. 아래 단위/API 시험은 이 총계에 포함되며 중복 합산하지 않는다. [기계 판독 결과](../reports/agentic-plan-revision-verification-2026-10-01.json)를 참조한다.

- 새 단위/모델 middleware: 14개 통과. 실제 compiled graph로 질문→답변→계획·saver 재구성·실행·Finalize·terminal, 승인 필요/생략·미확정 입력·후보 여러 개·등록 자산 선택, 기존 후보 차이 변경·인자/정책/이전 소스 보존, 코드/서명/자산/정책·schema 검증·비동기 HTTP·전송 오류를 검사한다.
- API/격리 PostgreSQL: 신규 1개 통과. stale/빈 feedback/직접 code/다른 화면 action은 token 미소비, 멱등 replay·새 token·이전 후보 승인 거절·공통 횟수 한도·typed SSE·소스 비노출 확인. 이 시험의 saver 재구성은 동일 InMemorySaver를 사용하며 process kill 시험이 아니다.
- 실제 Compose Executor/Jupyter: 등록 자산 재계획, 자유 함수 승인, 질문 후 자유 함수 승인, 등록 함수 본문 변경, 자유 함수 설정 자동 승인. 실제 API/Worker/DB/Postgres checkpointer/Redis 경로를 사용하며 최초 계획·등록 함수/정해진 수정 역할은 명시적인 fixture다.
- --real 시험은 최초 계획을 고정하고 재작성 역할만 실제 on-prem 모델로 호출한다. 실제 생성 코드의 계산 결과까지 확인하며 일반 업무 E2E/의미 평가/처리량 측정과 구분한다.

최초 전체 회귀는 기존 비동기 모델 시험의 대역 주입 지점이 이전 ChatOpenAI에 남아 5개가 실패했다. 공통 CompatibleChatOpenAI 생성 경로에 대역을 주입하도록 수정했으며 동기 HTTP 금지·취소·JSON 전략 검증은 유지했다. 해당 관련 25개 재검증 후 전체 suite를 다시 통과했다. production 코드에 테스트 전용 우회는 추가하지 않았다.

| 실제 Executor 시험 | 재작성→최종 완료 | 확인 |
|---|---:|---|
| 등록 Tool 재계획 | 4.602초 | 사용자 승인·실제 계산·terminal |
| 새 자유 함수 | 3.563초 | 사용자 승인·실제 계산·terminal |
| 추가 질문 후 자유 함수 | 4.012초 | 사용자 승인·실제 계산·terminal |
| 등록 함수 본문 수정 | 4.076초 | 사용자 승인·실제 계산·terminal |
| 자유 함수 자동 승인 설정 | 3.711초 | 사용자 승인 0회·snapshot 설정 근거·terminal |

위 다섯 시험은 모델 역할이 명시적인 fixture다. 실제 모델 재작성 한 건(36.977초)과 구분하며 성능 비교/처리량 수치가 아니다. 배포 wheel을 checkout import 없이 풀어 OpenAPI 34개, 새 역할 포함 12개 prompt/production builder, 기존 등록 자산과 mock graph를 확인했다. 패키지 검증은 외부 서비스를 호출하지 않는다.

## 인터페이스와 완료 기준

[개발·API·설정 안내](../agentic-plan-revision.md)를 따른다. 새 plan_review는 새 plan_id와 증가한 interaction revision을 사용한다. 질문 kind와 resolved resolution별 프론트 처리가 필요하며 서버가 프론트 패널까지 구현한 작업은 아니다.

중앙 설정은 AGENT_FREE_PLAN_ENABLED=true, AGENT_FREE_PLAN_REQUIRE_APPROVAL=true, AGENT_MAX_PLAN_REVISIONS=5가 기본이다. 승인 설정 false는 완전한 자유 후보 하나만 자동 승인하며 근거/설정 값을 snapshot과 SSE에 남긴다. registered 계획 승인, 후보 선택, 필수 입력/추가 질문을 대신하지 않는다. 자유 코드와 실행 후 repair_level/시도 제한은 독립이다.

## 남은 범위와 해석 한계

AST/서명/JSON schema 검증은 코드의 업무 정답·모든 데이터 접근·부작용을 완전히 증명하는 sandbox가 아니다. 자유 코드 선택의 필요성과 사용자의 자연어 목표 충족은 별도 업무 시나리오 평가 대상이다. 승인하지 않은 화면에 숨겨진 Python을 직접 수정·제출하는 API를 추가하지 않았다.

PVC dataset catalogue/scope/metadata, project_memory, pgvector Workflow CRUD/검색·승격, Gaia adapter, 첨부/VLM, Artifact 등록 시점, 실제 미사용 legacy 정리는 후속이다. 다중 사용자 처리량·Kubernetes·process kill·주 단위 실행 검증은 수행하지 않았다. 원래 사용자 체크아웃을 보존하고 파생 브랜치에만 구현/기록을 커밋한다.
