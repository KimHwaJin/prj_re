# 사용자 resume와 checkpoint 복구 계약

030에서 도입한 내부 계약이다. 프론트는 기존 `POST /api/v1/sessions/{session_id}/runs/{run_id}/resume`에 `command`와 현재 `resume_token`을 보낸다. 명령 ID·interrupt ID·처리 기록을 직접 만들거나 전달하지 않는다.

## 저장하는 정보

- 내부 Run ID를 명령 ID로 사용한다. 별도 명령 큐나 테이블은 추가하지 않는다.
- 이전 Run의 최종 상태 저장 시 실제 LangGraph interrupt ID를 `metadata._checkpoint_interrupt_id`로 보존한다. 공개 interrupt payload의 모양은 유지한다.
- resume 접수 시 이 값을 새 내부 Run의 `metadata._resume_target`에 복사한다. 같은 명령의 재시도에서는 대상을 다시 선택하지 않는다.
- 호출 직전에 별도 짧은 transaction으로 `metadata._resume_started=true`를 commit한다. 이 표시만으로 입력이 처리됐다고 판단하지 않는다.
- 입력을 받은 노드가 정상 반환할 때 노드의 업무 결과와 `user_resume_receipt`를 함께 반환한다. receipt에는 command ID, interrupt ID, 입력 digest가 들어간다. 이 둘은 같은 LangGraph 노드 결과로 checkpoint에 저장된다.
- session당 사용자 명령 하나만 진행할 수 있는 기존 잠금 규칙에 따라 최신 receipt 하나만 유지한다. 외부 이벤트용 `ew_receipts`는 별도 규약을 그대로 쓴다.

`_resume_started`는 호출했을 가능성에 대한 보수적인 기록이고, receipt는 입력을 받은 노드가 결과를 저장했다는 증거다. receipt만으로 downstream Agent 흐름 전체 또는 외부 Executor 작업이 끝났다고 판단하지 않는다.

## 실행과 복구

1. 세션 점유 아래 최신 checkpoint를 읽고 모델 선택을 검증한다.
2. 같은 command ID의 receipt가 있으면 digest·대상 interrupt까지 일치하는지 검사한다. 그래프가 정상적인 다음 interrupt 또는 종료에 도달했는지도 확인한다.
3. 이 조건을 만족하면 `graph.ainvoke()`를 호출하지 않고 checkpoint 상태로 서비스 DB 반영만 재실행한다.
4. receipt가 없고 아직 호출하지 않은 명령이면, checkpoint의 유일한 interrupt가 고정된 대상인지 확인하고 시작 표시를 commit한다. `Command(resume={interrupt_id: envelope})`, `durability="sync"`로 호출한다.
5. 호출 후 checkpoint를 다시 읽어 receipt와 다음 대기/종료 상태를 검증하고 서비스 DB에 반영한다.
6. 이미 호출했지만 receipt가 없거나, receipt가 다르거나, 다음 노드가 실패한 상태면 자동 재실행하지 않는다. 중단이 확인된 호출은 `UserResumeNeedsRecovery`로 해당 Task만 복구 필요로 표시한다. Task 보호가 commit된 뒤 실행 점유를 반환하며, Task의 recovery flag가 API/이벤트 재진입을 계속 막는다. 다른 세션은 처리할 수 있다.

서비스 반영 오류는 `RESUME_PROJECTION_FAILED`, `stage=state_projection`으로 기록하고 기존 큐/backoff 한도로 재시도한다. 최종 Run/Task/event transaction도 이 예외 처리 범위에 포함한다. 031에서 최초 호출에도 같은 경계를 적용했다. 최종 commit이 실제 성공하고 응답만 유실되었다면 상태를 재조회해 중복 최종 이벤트를 추가하지 않는다. 이때 이미 해제된 Task lease를 다시 요구하지 않고, 아직 보유한 공통 세션 점유 아래 완료 사실을 확인한다.

재시도 소진 시 resume를 단순 ERROR로 끝내고 세션을 새 입력에 열지 않는다. 복구 필요로 남긴다. Task 보호 저장/확인에 실패하거나 실행 종료·외부 제출이 불확실하면 기존 `ExecutionNeedsRecovery` 경로로 프로세스 추가 claim과 세션 점유 해제를 막는다. DB 전체 장애로 복구 표시 자체가 불가능하거나 프로세스가 죽은 경우의 자동 점유 복구까지 제공하는 것은 아니다.

## Agent 개발 규칙

공용 코드는 `dtest/contracts/user_resume.py`, `dtest/agent_service/runtime/user_resume.py`에 있다. Agent가 API/DB 구현을 import하지 않는다.

```python
from agent_service.runtime.user_resume import (
    record_user_resume,
    user_interrupt,
)
from dtest.contracts.user_resume import UserResumeState


class State(UserResumeState, total=False):
    answer: str


@record_user_resume
async def ask(state: State) -> dict:
    answer = user_interrupt({"kind": "question"})
    return {"answer": answer}
```

- 서비스에서 재개하는 사용자 HITL 노드는 `record_user_resume`로 감싸고 `user_interrupt`를 사용한다. 분석 Agent의 `request_human_input`은 이 공통 helper를 사용하도록 연결했다.
- `def`/`async def` 양쪽을 지원한다. 현재 Workflow 후보 선택처럼 `run_sync` 안에서 실행되는 노드도 receipt를 노드 결과로 반환한다.
- 노드 한 번에 사용자 응답 하나를 받고 dict 상태 변경을 반환하는 규약이다. 여러 사용자 interrupt를 한 노드 안에서 연속 처리하거나 동시 여러 사용자 interrupt를 받는 계약은 이번에 지원하지 않는다.
- 입력을 받은 노드가 실패하거나 다시 interrupt하면 완료 receipt를 만들지 않는다. 다른 노드에서 나중에 receipt를 따로 기록하지 않는다.
- resume envelope는 공용 helper가 벗겨준다. 도메인 검증/프롬프트/사용자 메시지는 기존 `command` 값만 받는다.
- node는 state를 직접 수정하지 않고 변경 dict를 반환한다. `user_resume_receipt`를 업무 코드에서 덮어쓰거나 삭제하지 않는다.
- 개발 도구의 직접 LangGraph 호출은 기존 raw resume를 사용할 수 있지만, 서비스의 자동 복구 계약을 제공하는 경로는 서버 envelope를 사용하는 위 규약이다.

현재 분석 Agent의 데이터 선택, 분석 문맥, 추가 정보, Workflow 후보 선택, Workflow 승인, 다음 사용자 요청 노드에 적용했다. 노드 이름·edge·업무 순서·모델 호출 횟수는 변경하지 않았다.

## 이전 버전과 배포 범위

DB schema migration은 없다. 기존 JSON metadata와 additive checkpoint state 필드를 사용한다. 그러나 기존 저장된 사용자 대기 Run에 interrupt ID가 없으면 임의로 최신 질문과 연결하지 않고 복구 필요로 처리한다. 배포 전에 이전 사용자 대기를 종료하거나 검증된 운영 복구 절차가 필요하다. 이미 Executor를 기다리는 작업의 이벤트 receipt 계약은 변경하지 않았다.

새 API와 새 Agent runtime을 함께 배포해야 한다. 구버전 Worker가 새 resume를 가져가면 새 보호 규약을 따르지 않으므로, **구·신 Worker 혼재 중 안전한 resume 처리는 이번에 보장하지 않는다.** 배포 시 실행 접수/Worker drain 및 버전 전환을 조율해야 한다. Kubernetes rollout 실증은 별도다.

029의 R1과 사용자 resume에 해당하는 R2 경로를 수정했다. 최초 사용자 호출의 최종 반영 실패는 [031 최초 호출 복구 계약](initial-request-recovery.md)에서 처리했다. 로그/이벤트 개별 commit(R3), 프로세스 강제 종료 owner 정리(R4/R5), 관리자 복구 API는 후속 범위다. receipt는 외부 호출을 포함한 전체 시스템의 exactly-once 보장을 의미하지 않는다.

031에서 snapshot 변환과 `GraphProjectionError`는 `api_service/services/graph_recovery.py`로 공통화했다. `UserResumeNeedsRecovery`는 공통 `InvocationNeedsRecovery`의 하위 예외이며, 세션 단위 보호와 프로세스 보호의 구분은 유지한다.
