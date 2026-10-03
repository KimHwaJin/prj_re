# 분석 Agent 상태 수명과 노드 입력 경계

분석 그래프의 실행 상태는 `src/agent_service/agents/analysis/state.py`가 선언한다. 책임별 TypedDict를 조립하지만 체크포인트는 기존과 같은 **77개 평면 채널**이다. `planning/graph.py`는 그래프 조립, `planning/lifecycle.py`는 새 요청 초기화를 담당한다.

## 무엇을 언제 보존하는가

| 상태 그룹 | 주요 내용 | 수명/갱신 경계 |
|---|---|---|
| OwnerState | user/project/session ID | 서비스가 소유권을 검증해 전달한다. 역할 응답으로 바꾸지 않는다. |
| RequestState | 새 요청·Run ID·모델 pin·프로젝트 prompt snapshot·초기 입력 identity | 서비스가 새 요청 입력을 구성한다. HITL/Executor 재개는 같은 snapshot을 사용한다. |
| SessionState | history·last_analysis_context·kernel_profile | 다음 요청에도 유지한다. 이력/근거 예산과 소유권 검사는 기존 정책을 적용한다. |
| PresentationState | task/public Run/command ID·현재 이벤트·응답·메모리 갱신 결과 | 새 요청의 receive에서 새로 만든다. 이벤트/서비스 projection 전에는 보존한다. |
| PlanningReviewState | 후보·편집·재작성·승인 snapshot | HITL 편집과 재개 동안 보존한다. 다음 새 요청에서 초기화한다. |
| ExecutionState | 정확한 제출 본문·Operation/버전·단계·관찰·최종 오류 | 제출·대기·재개·완료 projection에서 보존한다. 다음 새 요청에서 초기화한다. |
| RepairState | 후보/승인 수정 snapshot·시도/권한·수정 이력 | repair HITL와 결과 재개 동안 보존한다. 다음 새 요청에서 초기화한다. |
| DeliveryState | initial/user receipt·ew_pending/receipts/sequences | 응답 유실/중복 전달 복구에 필요하다. 현재 Run에서는 유지하고 다음 새 요청에서 초기화한다. |

`last_analysis_context`는 이전 분석의 제한된 실행 근거다. 원본 함수, 전체 manifest, 제출 본문을 대신 보존하는 장기 원장이 아니다. 프로젝트 메모리는 별도의 공식 LangGraph Store이며 이번 변경에서 namespace·읽기·갱신 정책을 변경하지 않았다.

### 새 요청

1. 서비스가 세션 소유권과 새 입력 접수 가능 여부를 확인한다. 기존 WAITING_EXECUTOR 입력 잠금은 유지한다.
2. receive가 `new_request_defaults()`로 모든 Run 상태를 초기화한다.
3. 새 task/public Run/interaction ID와 이벤트를 생성하고, 요청 메시지를 제한된 세션 history에 추가한다.
4. 현재 소유자에게 속하는 이전 분석 근거와 kernel_profile을 유지한다.

이전 `execution_command`, Operation ID/버전, 실행/대기 단계, 제출 단계 목록, 오류, planning_activity_id, routing_result가 새 FAQ 요청에 남지 않는다. service가 전달하는 초기 identity·prompt/model snapshot은 receive가 임의로 재조회하거나 교체하지 않는다.

### HITL/Executor 재개 및 완료

`Command(resume=...)`와 복구용 `ainvoke(None, ...)`는 receive를 거치지 않는다. 중간에 pool/프로세스를 다시 만들어도 승인 원문·정확한 HTTP body·관찰·소비 receipt가 남는다. 최종 report에서도 이를 지우지 않는다. 그래프가 끝나도 서비스 projection이 실패하거나 응답이 유실될 수 있기 때문이다.

**최신 상태 초기화와 과거 checkpoint 삭제는 다르다.** 다음 요청에서 제출 본문을 빈 dict로 덮어써도 이전 blob/version/history는 유지한다. 이번 작업에 pruning·TTL·보존 기간 변경은 없다. 긴 Executor 작업의 finalize와 타임아웃 정책도 바꾸지 않는다.

## 노드는 필요한 필드만 읽는다

`NODE_INPUTS`는 노드 이름마다 읽을 채널 목록을 명시한다. 타입은 전체 상태 선언에서 가져오므로 같은 채널에 다른 타입을 중복 선언하지 않는다. `builder.add_node(..., input_schema=NODE_INPUTS[name])`로 LangGraph가 실제 노드 입력을 제한한다.

| 노드/역할 | 읽는 필드 수 | 의도 |
|---|---:|---|
| receive | 9 | 새 입력·세션 이력/근거·커널만 읽고 Run 상태 초기화 |
| conversation | 13 | 대화와 제한된 이전 분석 근거, 요청별 모델/prompt |
| publish_review / await_review | 11 / 1 | UI 계획 표현 / 현재 HITL payload |
| apply_review / revise_plan | 18 / 22 | 편집·승인·재작성에 필요한 계획과 요청 문맥 |
| execution_select / submit | 13 / 9 | 승인/수정 계획에서 다음 batch 구성 / 저장된 정확한 body 제출 |
| execution_register / wait / receipt | 2 / 2 / 5 | binding / 외부 결과 대기 / 소비 증거 기록 |
| execution_process_event / review | 19 / 18 | 현재 Operation 결과 적용 / 승인 범위의 후속 판단 |
| execution_report | 26 | 승인·수정 정책·실제 결과로 보고서와 세션 근거 생성 |
| execution_repair_propose | 24 | 실패/성공 근거와 현재 허용 범위로 수정 제안 |

다른 decision/repair HITL 및 finalize/cancel 노드도 명시된 입력을 사용한다. 정확한 목록은 `state.py`의 `NODE_INPUTS`를 따른다. 기존에는 모든 노드가 77개 채널을 읽었다.

입력 스키마는 **읽기 경계**다. 노드는 전체 상태에 선언된 채널에 partial update를 쓸 수 있고, 조건부 router는 전체 상태를 본다. 이 기능을 권한 검사나 외부 응답 redaction으로 간주하지 않는다. `ainvoke` 출력과 raw state stream도 자동으로 숨겨지지 않으며, 공개 REST/SSE는 기존 서비스 projection 계약을 사용한다. [LangGraph Multiple schemas](https://docs.langchain.com/oss/python/langgraph/graph-api#multiple-schemas)의 방식이다.

입력 필드 수 감소를 LLM prompt 길이나 전체 읽기 decode 비용 감소로 동일시하지 않는다. 역할별 prompt payload는 기존처럼 명시적으로 조립하며, checkpointer는 전체 snapshot을 읽을 수 있다. 저장 metadata 효과는 실제 PostgreSQL 논리 바이트를 별도로 측정한다.

## 필드나 역할을 추가하는 개발자 안내

1. `state.py`에서 수명이 맞는 그룹에 필드를 선언한다. 이미 선언된 채널의 이름·타입·reducer를 바꾸려면 체크포인트 호환성을 먼저 검토한다.
2. Run 수명 필드는 `new_request_defaults()`에 초기값을 추가한다. 리스트와 dict는 요청마다 새 객체여야 한다. 모든 Run 필드의 초기화 누락을 테스트가 검사한다.
3. 새 노드 또는 역할이 직접/간접 helper에서 읽는 필드를 `NODE_INPUTS`에 명시한다. helper의 요구 사항도 포함해야 한다.
4. 노드는 입력을 수정하거나 전체 상태를 반환하지 않고, 변경할 필드만 반환한다.
5. 새 요청과 같은 Run 재개를 구분해 검사한다. 대기/완료 직후 승인 원문이나 receipt를 지우는 것은 허용하지 않는다.

현재 runtime/version과 기존 노드 이름은 유지한다. 이전 전체 읽기 방식의 checkpoint를 계획·Executor·판단·repair 대기에서 재개하는 PostgreSQL 검사가 있다. 더 오래된 설문형 Runtime의 자동 이행을 지원한다는 뜻은 아니다.

[개선 기록](../improvements/064-analysis-state-lifecycle.md), [검증/저장량 근거](../reports/analysis-state-lifecycle-2026-10-04/README.md), [역할과 미들웨어](agent-runtime-contract.md).
