# 최초 Agent 호출과 checkpoint 복구 계약

031에서 사용자 resume의 복구 원칙을 최초 입력에도 적용했다. 공개 Run 생성 API/body는 그대로이며 내부 Worker 경로에서 동작한다. Agent의 업무 순서·모델 호출·Executor 이벤트 계약은 변경하지 않았다.

## 문제와 판정 기준

LangGraph는 입력만 접수한 상태도 checkpoint로 남길 수 있다. 따라서 checkpoint가 있다는 사실만으로 입력 처리 완료를 판단할 수 없다. 반대로 Agent가 이미 질문 대기나 종료까지 도달했는데 서비스 DB 저장만 실패했다면 최초 입력을 다시 전달해서도 안 된다.

| 확인된 상태 | 처리 |
|---|---|
| 새 Run, 미전송, 현재 Run의 처리 흔적 없음 | 프로젝트 문맥을 읽고 시작 표시를 commit한 뒤 한 번 호출 |
| 같은 Run·입력 receipt, 오류 없이 다음 interrupt/종료 도달 | graph 호출 없이 서비스 저장만 복구 |
| 전송 표시 있음, receipt 없음 또는 입력 checkpoint만 존재 | Task를 recovery_required로 보호 |
| receipt 불일치·후속 노드 오류·중간 진행 상태 | 자동 입력 재전달 없이 Task 보호 |
| 최종 DB commit은 성공하고 응답만 유실 | 이미 저장된 상태를 반환, 중복 최종 이벤트 생성 안 함 |
| 재시도 한도 소진 | 새 입력을 열지 않고 Task 보호 |
| 오류/보호 기록 자체도 저장·확인 불가 | 프로세스 추가 claim과 owner 해제 중단 |

## 정보와 실행 순서

- 접수 metadata에 `_initial_protocol=1`, `_initial_started=false`를 서버가 설정한다. 클라이언트 metadata의 동명 값은 제거한다.
- 내부 Run ID와 user/project/session/input/model selection을 해시해 `initial_request_identity`를 만든다. 매번 달라지는 request ID, 변경 가능한 프로젝트 prompt, 생성된 출력은 해시에서 제외한다.
- 실행 점유 아래 checkpoint를 먼저 확인한다. 이미 같은 명령이 완료됐다면 현재 프로젝트 prompt를 다시 읽지 않고 저장된 checkpoint로 복구한다.
- 새 입력만 프로젝트 문맥을 읽는다. 현재 Worker claim/attempt를 확인하며 짧은 DB transaction으로 `_initial_started=true`를 commit한 후 `ainvoke(..., durability="sync")`를 호출한다.
- 첫 입력 처리 노드가 정상적으로 반환할 때 결과와 `initial_request_receipt`를 같은 상태 변경으로 저장한다. 이 receipt는 입력 처리 증거이며 전체 그래프 완료 증거가 아니다.
- 호출 이후 다시 checkpoint를 읽는다. receipt 일치, task 오류 없음, 남은 task들이 interrupt 대기이거나 그래프 종료인지를 확인한다.
- 메시지 등 서비스 데이터와 최종 Run/Task/event를 저장한다. 이 부분의 실패는 `INITIAL_PROJECTION_FAILED`, `stage=state_projection`으로 기존 재시도/backoff 경로에 기록한다. 다음 시도에서는 Agent를 호출하지 않고 저장을 복구한다.

입력 전송 직전 표시만 저장되고 프로세스가 중단되면, 실제 호출 전이었더라도 자동 재전송하지 않는 보수적 정책이다. 그래프 진행 중 오류도 임의로 `ainvoke(None)`를 호출해 이어가지 않는다. 확인되지 않은 외부 부작용을 반복하지 않기 위해 운영 복구 대상으로 남긴다.

기존 작업이 종료/취소된 세션에서 새로 접수한 Run은 다른 command ID를 가지므로 정상적으로 새 입력을 처리한다. 동일 세션의 활성 Task/실행 점유 제한은 기존 API·Worker 규칙을 그대로 따른다.

## Agent 개발 규칙

```python
from dtest.contracts.initial_request import InitialRequestState
from dtest.agent_service.runtime.initial_request import record_initial_request


class State(InitialRequestState, total=False):
    user_id: str
    project_id: str
    session_id: str
    user_request: str
    model_selection: dict


@record_initial_request
async def receive_request(state: State) -> dict:
    return {"user_request": state["user_request"]}
```

서비스가 전달한 `initial_request_identity` 및 identity에 포함되는 상태 필드를 state schema에 보존한다. `InitialRequestState`는 run_id와 identity/receipt를 포함한다. 첫 노드를 감싸는 helper는 def/async def를 모두 지원한다. 입력 처리 노드가 예외/interrupt로 끝나면 receipt를 만들지 않는다. 노드는 상태를 직접 수정하지 않고 변경 dict를 반환해야 한다. receipt는 업무 코드에서 직접 덮어쓰지 않는다.

분석 Agent는 `AnalysisWorkflowState`와 기존 `receive_request`에 적용했다. API 구현을 Agent에서 import하지 않는다. identity 없는 개발용 직접 graph 호출은 기존 동작을 유지하지만, 서비스의 복구 보장을 제공하는 경로는 Worker가 위 규약으로 호출하는 경로다. 개발용 stream helper를 이 규약으로 바꾼 것은 아니다.

## 경계와 배포 제한

새 DB migration·환경변수·큐·Worker는 없다. 기존 metadata와 추가 checkpoint 필드를 사용한다. 최초 입력/사용자 resume의 snapshot 변환과 저장 예외는 `graph_recovery.py`를 공유한다.

protocol 없는 기존 대기 Run은 실행 이력을 증명할 수 없으므로 자동 이관하지 않고 recovery_required로 보호한다. 새 API/Agent/Worker를 함께 전환해야 하며 구·신 Worker 혼재 안전성은 보장하지 않는다. 배포 전 기존 접수·실행 drain 및 대기 작업 처리 계획이 필요하다. 실제 Kubernetes rollout은 별도 검증이다.

서비스 저장 전체를 단일 transaction으로 바꾼 것은 아니다. 로그/이벤트 사이의 원자성(R3), 강제 프로세스 종료 후 점유 복구(R4/R5), 관리자 복구 API는 후속이다. 외부 HTTP 부작용의 exactly-once 보장이나 과거 간헐적인 user_request 오류의 원인 확정을 의미하지 않는다. 성능 개선 수치는 이번에 측정하지 않았다.
