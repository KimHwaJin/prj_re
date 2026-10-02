# 054 이전 설문형 Agent 제거·현재 실행 구조로 개발 도구 통합

| 항목 | 내용 |
|---|---|
| 상태 | 구현·검증·베이스 병합·origin 게시 완료 |
| 시작일 | 2026-10-02 |
| 브랜치 | feature/agent-runtime-cleanup |
| 출발 commit | 2878195647faf2ec752a92502b4d14c621ad5a65 |
| 배포 상태 | 미배포, 사용자 checkout·Docker·.env 유지 |

## 문제와 변경 방향

서비스 API와 Worker는 이미 `PlanningRuntime`의 에이전틱 계획·승인·실행 graph를 사용했다. 반면 루트 analysis/graph.py, CLI, Studio, 시각화와 일부 테스트는 이전 routing→설문→추천→워크플로우 생성 graph를 계속 가리켰다. 현재 모델 생성까지 이전 dependencies.py를 import하여 쓰지 않는 일곱 역할이 서비스 import 경로에 함께 남아 있었다. 개발자가 어느 graph를 수정·실험해야 하는지 불명확했고 wheel에는 양쪽 구현이 함께 포함됐다.

현재 API·Worker 실행 순서와 승인·소유권·체크포인트·메모리 계약을 유지하면서 이전 graph 전용 코드와 역할을 제거한다. 필요한 모델 생성만 공용 위치로 옮기고 개발 도구와 의미 있는 검증을 현재 builder로 맞춘다. 보호 대상은 기존 개발자가 사용하는 **analysis/workflow 패키지 전체**와 등록 Skill/Tool/Workflow 자산이다. Workflow CRUD 1.3 이행은 기존 보류 범위이며 이번 제거에 끼워 넣지 않는다.

## 실제 변경

- 이전 graph/state/context/HITL adapter, nodes/routers/components/testing, 일곱 old agent_builders 역할과 전용 schema, 별도 PG 개발 helper·Studio API lifespan binder·옛 checkpoint 진단을 제거했다. 삭제 경로와 검증 이행은 [목록 JSON](../agent-development/runtime-cleanup-inventory.json)에 기록했다. 호환 import shim은 두지 않는다.
- create_chat_model을 `agent_service/runtime/model_factory.py`로 분리했다. 함수 AST는 원본과 동일하며 모델 설정·캐시·호출 정책을 변경하지 않았다. PlanningRuntime과 현재 모델 시험은 새 경로를 사용한다. Conversation 비교 driver는 현재 경로를 우선하고 명시적으로 선택한 054 이전 snapshot에서만 옛 factory 위치를 탐지하여 기존 전후 비교를 보존한다.
- CLI와 Studio는 같은 현재 builder의 명시적 offline mock을 사용한다. 배포 YAML/.env/DB/Redis/SSO/Executor/project_memory를 자동으로 연결하지 않는다. CLI는 현재 typed HITL로 편집·승인하며 private Tool 코드와 checkpoint 전체를 출력하지 않는다. Studio는 서비스 API 연계 실행기가 아니다.
- 시각화는 실제 실행·조건 판단·오류 수정 노드를 포함한다. 기존 컴파일 graph에서 목적지가 추론되지 않아 빠지던 조건부 연결을 명시했다. 노드·routing 함수·state는 그대로이며 diagram edge 수는 보완 전과 달라진다. [생성된 현재 graph](../agent-development/current-analysis-graph.mmd)를 제공한다.
- middleware·native async·model pin·PG pause/reopen·Executor 접수·receipt·취소/자원 lifetime·패키지 경계 검증을 현재 다섯 역할과 graph로 이행했다. 이전 설문 진행 순서만 검증하던 테스트는 제거하고 실행·판단·수정의 기존 현재 회귀를 유지했다. 테스트 개수의 감소를 기능 개선이나 동일한 커버리지 증명으로 해석하지 않는다.
- [Agent 개발 안내](../agent-development/README.md), [선언·미들웨어 계약](../agent-development/agent-runtime-contract.md), [서비스 구조](../architecture/service-layout.md), 루트 README를 현재 구조로 갱신했다. 과거 보고서와 고정 과거 commit 비교 도구는 당시 근거로 보존하며 현재 Agent 부하 측정과 구분했다.

## 보존한 계약과 제한

analysis/workflow의 README를 제외한 44개 파일은 byte hash로 동일함을 확인했다. 현재 다섯 역할의 6개 prompt도 그대로다. 공개 Run/SSO/Memory/Workflow 요청·응답 validation은 변경하지 않는다. 추가 DB migration·의존성·환경변수는 없다.

현재 `agentic-planning-v1` checkpoint는 재개 검증 대상이다. 이전 설문형 graph의 checkpoint 자동 이행은 지원하지 않으며 과거 외부 import 소비자는 현재 API 또는 builder로 전환해야 한다. 현재 Runtime이 이전 설문형 checkpoint를 이어 실행할 수 있다고 설명하지 않는다.

기존 Workflow 관리·컴파일 지원은 남아 있다. 모든 legacy 유틸리티를 삭제했다는 의미가 아니다. 제공 Gaia router 초안의 기존 syntax 문제와 폐쇄망 전체 템플릿 기동은 이번 검증 대상에서 제외한다.

## 검증

[검증 결과 JSON](../reports/agent-runtime-cleanup-verification-2026-10-02.json)에 최종 결과와 경계를 보존했다.

- **전체 API·Agent 회귀 884개 통과**, 413.27초, warning 81개. 실패·skip 없음. 전용 localhost agentic_regression_test와 테스트 Redis를 사용했다. 관련 검증 376개는 별도 28.06초/78경고로 통과했으며 전체 결과와 합산하지 않는다. 이전 959개와 subtest 2개에는 제거된 설문형 테스트가 포함돼 있어 동일한 테스트 집합이 아니다.
- 현재 다섯 역할의 prompt_json/provider_json_schema, 프로젝트 prompt 재시도/격리, 모델 pin, 실제 loopback HTTP, mock 지연, 취소/동기 자원 수명, 현재 계획·실행·판단·수정·메모리·소유권·Worker receipt 회귀를 포함한다. checkpointer 없는 내부 graph의 기존 durability 경고는 숨기지 않았다.
- 실제 PostgreSQL에서 출발 소스의 plan_review checkpoint를 저장·풀 종료 후 새 소스·새 Runtime으로 읽었다. addressed HITL approval과 receipt를 확인했고 4개 Step을 승인해 종료했다. approval 재개에서 모델을 새로 생성하지 않았다. 동일 노드 이름을 유지했고 diagram edge 수는 3→12로 표시가 보완됐다. 전체 실행/repair 그림은 25노드·44연결이다. 이전 설문형 checkpoint를 이행한 시험은 아니다.
- 깨끗한 wheel을 offline build한 뒤 `python -I`로 source checkout 없이 설치 경로에서 확인했다. 역할 5개/prompt, 보존 자산, 현재 mock 4단계 승인, 38개 API path, 이전 graph·역할 제외가 통과했다.
- 임시 loopback Studio 서버를 실제 기동했다. tracing을 끄고 health와 threads/runs/wait HTTP 요청으로 plan_review/initial receipt/4개 Step을 확인했다. Executor 제출은 없으며 서버를 종료했다. mock CLI의 같은 승인 대기와 코드 숨김도 확인했다. Conversation 비교 driver는 현재/출발 source의 --help import를 각각 확인했다.
- 문서화한 12개 API path·28개 schema를 현재 OpenAPI validation과 대조하여 동일함을 확인했다. 패키지 Python 337개 AST와 production import를 검사했으며 이전 graph 절대 import가 없다. 제공 Gaia router 초안은 패키지 검증 대상에서 제외했다.
- Workflow 자산·Compiler 44개와 현재 prompt 6개를 hash로 대조했다. 모델 factory 함수 AST도 원본과 동일하다. 원래 사용자 checkout의 HEAD/status/.env/기존 파일 316개를 대조하여 변경이 없음을 확인했다.

최종 git diff 검사에서 테스트 파일 세 개의 불필요한 EOF 빈 줄을 제거했다. 의미 변경은 없으며 관련 25개를 다시 실행하여 2.24초/18경고로 통과했다. 전체 884개 결과와 합산하지 않는다.

초기 관련 시험의 두 진단 helper 호출에 checkpointer 인자가 빠져 있던 점, fake Executor가 제출 Step ID 목록을 응답하지 않던 점, 전환 진단에서 승인 전 snapshot None을 읽던 점을 수정한 뒤 최종 시험을 통과했다. 기본 sandbox의 localhost socket 제한은 실제 loopback 검증 권한으로 해결했다. 배포 서비스 오류나 성능 개선으로 보고하지 않는다.

실제 LLM/외부 Executor E2E·Phoenix·종합 부하나 성능 A/B는 이번 검증에 포함하지 않는다. 처리량 향상을 주장하지 않는다.

## 후속

현재 Run API를 통한 실제 사용자 흐름의 디테일 검증을 먼저 수행한다. 새 요청→계획 편집/승인→실제 Executor 결과→후속 분석/보고서 및 프로젝트 메모리를 같은 인증 세션에서 연결해 확인한다. Executor Dataset Registry는 외부 구현이 준비될 때 연계하고, 모델 호출 횟수·종합 성능·운영 보완은 기존 후속 목록에 둔다.

## 적용·통합·게시

추가 Alembic migration이나 기존 데이터를 지우는 작업은 없다. 운영 배포와 Docker 재기동은 하지 않는다. 구현 commit: `66ffaedbf4ad3fdc1dcb3c4f3487b2932e77b5d8`. `feature/agent-runtime-cleanup`을 `feature/refactor-base`에 fast-forward 병합하고 두 브랜치를 origin에 atomic push했다. 원격 두 브랜치가 구현 SHA와 일치함을 확인했다. 이 게시 기록은 베이스의 후속 문서 commit으로 남기고 파생 브랜치는 구현 commit을 보존한다. 사용자 checkout·.env는 그대로 유지했다.
