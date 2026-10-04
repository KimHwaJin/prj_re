> 후속 결정(2026-10-04,084): 아래는 2026-09-28 당시 검토 기록이다. Jupyter registry·Redis ping의 현재 계약은 [미사용 API 제거 안내](../infrastructure-api-cleanup.md)이며, 아래 당시 유지/보강 의견과 파일 경로는 현행 API로 적용하지 않는다.

# 기본 CRUD 및 실행 연계 API 검토

작성일: 2026-09-28 · 대상: `feature/runtime-hardening`의 현재 작업 트리

**프로젝트 → 세션 → 메시지라는 자원 구분과 기본 조회 API는 유지할 만하다. 수정 범위의 중심은 메시지 전송의 원자성, 실행 상태에 따른 변경 권한, 공통 사용자 식별·관리자 권한, 재시도 계약이다. 전체 API를 새 이름으로 다시 만드는 것은 권장하지 않는다.**

**최신 합의 반영**: 내부 데모는 운영 문제의 근거/필수 호환 대상에서 제외한다. 사용자 식별은 플랫폼과 독립적인 X-User-Id 헤더로 통일하고 비밀번호·로그인·토큰 발급은 만들지 않는다. DB의 admin/user 역할과 자원 소유권을 적용한다. 사용자 등록·수정·비활성화는 관리자 전용, /users/me는 본인 정보 조회다. 이전 플랫폼 인증 연계 및 body/query 제안은 [확정 인터페이스](../design/platform-user-api-contract-2026-09-28.md)로 대체한다. 구현 완료가 아니다.

현재 API는 단순 저장·조회 기능을 넘어 Agent와 장기 Executor 실행의 진입점이다. 따라서 “테이블마다 CRUD가 있는가”보다 “사용자가 한 번 누른 동작이 일관된 결과로 남는가”를 기준으로 평가했다. 이 보고서는 분석 결과와 제안이며, 애플리케이션 수정 완료를 뜻하지 않는다.

## 최종 결정 반영

[최종 결정 문서](../design/crud-final-decisions-2026-09-28.md)가 아래 초기 권장안보다 우선한다. Message CUD 공개 API 제외, 미종료 작업이 있는 사용자 삭제 거절, 전체 작업에서 동일 공개 run_id, 문자열 공개 사용자 ID/내부 UUID 유지가 확정됐다. Project system_prompt는 삭제하지 않고 매 Agent 실행에 추가한다. Agent가 관리하는 project_memory도 프로젝트 내 여러 세션이 공유하는 별도 프롬프트 컨텍스트로 추가한다. 사용자 등록 body는 role=admin/user를 받는다. 일반 사용자도 템플릿 승격이 가능하며 Executor 성공은 필수 조건이 아니다. 문서/자산 유효성과 실행 검증 여부는 구별한다. 실제 소스 변경 완료가 아니다.

## 항목별 사용자 리뷰 반영 — 최신 범위

이 절은 아래의 초기 분석/권장안보다 우선한다. 초기 코드 관찰은 보존하되, 사용하지 않는 편의 API를 운영 필수 개선으로 확대하지 않는다. 실제 소스 변경은 아직 수행하지 않았다.

| 사용자 검토 번호 | 반영 |
|---|---|
| 1 | 실행 중 세션 이동·삭제 제한 및 상위 프로젝트 삭제 검사 방향 승인. 이름 변경은 별도 허용 가능. |
| 2 | Message 수정·삭제는 사용하지 않는 편의 API. 편집/분기/재실행 기능을 새로 만드는 것은 범위 밖. 공개 제거 또는 내부 진단용 존치 후보로 분류하며 즉시 삭제한 것은 아님. |
| 3 | Message 생성도 운영 클라이언트의 필수 호출이 아님. 정상 대화 입력·HITL·Agent 출력의 영속화는 Runs 접수/내부 실행 경로에서 수행. 별도 Message POST를 먼저 호출하도록 요구하지 않음. |
| 4 | 재시도는 API 응답 유실/timeout 후 같은 생성 요청을 다시 보내는 경우를 뜻함. LLM 추론 재시도와 별개. sessionless Message POST 미사용이면 그 API의 수정 우선순위는 낮추고 제거 시 해당 경로 문제도 없어짐. Runs의 중복 접수 방지 책임은 유지. |
| 5 | 상세 조회가 전체 하위 데이터를 읽고 응답에서 버리는 부분 제거 승인. |
| 6 | 기본 프로젝트 DELETE 거절 방향 승인. 대화 전체 비우기 API 신설은 별도 요구가 있을 때만 검토. |
| 7 | 사용자 삭제 시 소유 프로젝트·세션·메시지를 함께 숨기는 정책 유지. 사용자 비활성화와 데이터를 반드시 별도 기능으로 분리하자는 종전 권고는 철회. 숨김과 실제 실행 종료/물리 삭제는 구분하며 진행 중 작업 처리 정책은 남은 설계 사항. |
| 8 | Agent 실행 인터페이스는 Runs에 집중. 별도 Tasks 공개 API는 필수로 유지하지 않으며 기능 통합 후 제거 후보. |

Message CUD의 별도 제품 사용 사례는 대화 가져오기, 사용자 편집 후 분기/재실행, 메시지 숨김·관리자 정정 등이다. 현재 사용 요구가 확인된 기능은 아니므로 이를 근거로 신규 기능을 만들지 않는다. 내부에서 메시지 행을 생성하는 기능과 공개 CRUD 라우트 필요성은 별개다.

Tasks는 공개 API와 내부 상태 저장을 구분한다. 현재 Task에는 여러 resume Run 연결, 세션 점유, 상태/취소/이벤트가 있다. 공개 기능은 Runs로 합칠 수 있다. 이 상태를 Runs 쪽으로 옮기거나 내부 구현으로 유지하는 선택은 가능하지만, 단순히 테이블/로직부터 삭제하면 같은 기능을 잃는다. 최종 결정으로 공개 run_id는 전체 작업에서 유지하며 구간별 ID는 내부에서 관리한다. checkpoint DB 참조와 Executor 이벤트 바인딩도 이 변경과 함께 검증한다.

## 1. 조사 범위와 증거의 수준

- `src/main.py` → `app.api.v1.router`가 등록하는 9개 라우터의 **HTTP operation 45개**를 조사했다. URL 개수가 아니라 method+path 개수다. 기본 prefix `/api/v1` 기준이며 `/health`, `/demo`, `/`는 제외했다.
- users/projects/sessions/messages 21개, workflows 7개, runs/tasks 13개, Jupyter/Redis 4개다. 표에 실행 API를 포함한 이유는 CRUD와의 정합성이 이번 검토의 핵심이기 때문이다.
- 라우터·요청/응답 스키마·서비스·모델·데모 호출 순서를 추적했다. 별도 Gaia 초안 라우터를 현재 등록된 API로 세지 않았다.
- 외부 DB·Redis·LLM·Executor를 호출하거나 실제 사용자를 변경하지 않았다. 실제 `.env`도 읽지 않았다. 배포된 Gaia/gateway의 인증 기능은 이번 검증 대상이 아니다.
- **정적 확인**: 해당 코드 경로에 구현된 조건/쿼리/commit을 확인했다. 실제 장애 발생 횟수나 지연 기여도를 측정했다는 뜻은 아니다.
- **격리 재현**: 실제 라우터/스키마/일부 서비스에 가짜 저장소를 연결했다. PostgreSQL 제약·동시 실행·네트워크 장애를 포함하는 E2E 검증은 아니다.
- [API 원본 목록](crud-api-review-2026-09-28/inventory.json), [격리 검증 결과](crud-api-review-2026-09-28/offline-checks.json), [재실행 스크립트](../../scripts/diagnostics/review_crud_contracts.py).

## 2. 제품 관점의 판정

| 영역 | 판정 | 이유 |
|---|---|---|
| Project | 기본 CRUD 유지 | 사용자 대화의 분류 단위로 적절하다. 기본 프로젝트 삭제 의미·실행 중 하위 자원 처리·프롬프트 적용 계약은 수정한다. |
| Session | 기본 CRUD 유지 | 다른 세션은 독립 실행한다는 요구와 맞는다. 실행 중 이동·삭제 제한, 현재 작업 상태와 입력 가능 여부를 보강한다. |
| Message | 조회 유지, 공개 CUD는 비필수 | 정상 입력·출력 저장은 Runs/내부 서비스가 담당. 사용하지 않는 편의 라우트는 제거 또는 내부 진단용 존치 후보다. |
| User | 공통 헤더·역할 검사 적용 | X-User-Id로 활성 사용자를 식별하고 관리자만 사용자 등록·수정·비활성화한다. /users/me는 본인 조회다. |
| Task / Run | 공개 인터페이스는 Runs 중심 | Tasks API는 기능 이관 후 제거 후보. 현재 Task의 전체 작업 연결·세션 점유·재개 상태는 내부 보존/통합 방식을 별도로 결정한다. |
| Workflow 자산 | 업무 기능으로 유지 | 생성 결과의 보관·재사용에는 필요하다. Agent 목록과는 다른 개념이다. 승격·공유·검증 완료의 뜻을 정해야 한다. |
| Jupyter / Redis | 연계/운영 용도 구분 | Jupyter 생성 주체가 Executor라면 읽기와 점검만 있어도 된다. Redis ping을 일반 사용자 화면 API로 취급할 필요는 없다. |

이 목록에 없는 API를 기계적으로 추가할 필요는 없다. 예를 들어 사용자 전체 목록, Agent 등록·삭제 API, Jupyter 생성 CRUD는 현재 제품 요구로 확인되지 않았다.

## 3. 우선 해결할 계약 문제

### F01. X-User-Id와 관리자 전용 사용자 관리 계약으로 변경

현재 core/auth.py는 사용자 UUID를 Bearer로 받아 존재 여부를 검사한다. users 라우트에는 공통 호출자/역할 검사가 없다. UserService.delete는 프로젝트·세션·메시지까지 soft delete한다. 격리 검증에서 무인증 GET/PATCH/DELETE가 서비스에 도달한 결과는 현재 코드의 사실로 보존한다.

사용자의 최신 요구는 플랫폼과 독립적인 ID 기반 사용이다. 로그인 체계 추가를 개선 항목으로 삼지 않는다. 공통 dependency가 X-User-Id로 등록/활성 여부와 DB 역할을 읽고 소유권을 검사한다. 사용자 등록·수정·비활성화는 관리자 전용, /users/me는 본인 정보 조회로 확정했다. 최초 관리자는 배포 초기화에서 생성하고 마지막 활성 관리자의 삭제·강등을 막는다.

사용자 삭제 시 하위 업무 데이터를 함께 숨기는 정책은 유지한다. 사용자/데이터 숨김만으로 이미 접수한 Agent/Executor 작업이 종료되었다고 표시하지 않는다. 실제 코드 변경은 아직 수행하지 않았다. 자세한 인터페이스는 확정 명세를 따른다.

### F02. 메시지 CRUD가 세션 실행 규칙을 따르지 않는다

**확인한 사실**

- `MessageCreate`는 `user/system/assistant/agent/tool`을 모두 받는다. 실제 스키마 검증에서도 다섯 역할이 허용되었다.
- `MessageService.create:55`는 소유 세션을 `FOR UPDATE`로 읽지만 Task가 pending/running/외부 대기 상태인지 검사하지 않는다.
- `MessageService.update:127`, `delete:149`도 실행 참조 여부나 메시지 역할을 검사하지 않는다. 수정은 메시지 테이블만 바꾸고 이미 저장된 Run 입력/checkpoint를 수정하지 않는다.

**사용자 영향**: 입력창을 잠가도 API로 메시지는 추가할 수 있다. 사용자 호출로 Agent/시스템이 쓴 것처럼 표시할 기록도 만들 수 있다. 과거 입력을 수정하면 화면의 대화와 실제 실행이 참조한 내용이 달라질 수 있다. 이것만으로 해당 내용이 시스템 프롬프트에 주입된다고 단정하지는 않는다.

`FOR UPDATE`는 같은 행을 동시에 수정하지 않도록 순서를 정할 뿐, “지금 채팅을 받아도 되는가”라는 업무 조건을 대신하지 않는다.

**개선 방향**

1. 공개 입력은 사용자 메시지 또는 명시된 HITL 응답으로 제한한다. assistant/system/agent/tool 기록은 내부 서비스가 작성한다.
2. 전송 완료되어 Run에 연결된 입력·Agent 출력은 원본 보존을 기본으로 한다. 편집 기능이 꼭 필요하면 새 버전/분기와 재실행을 제품 기능으로 설계한다.
3. 메시지 숨김, 실행 기록 삭제, checkpoint 초기화를 같은 DELETE로 취급하지 않는다. 단순 UI 숨김이라면 후속 Agent 문맥에서 제외되는지 여부까지 명시한다.
4. 초안 저장이 필요할 때만 별도 초안 계약을 둔다. 메시지 PATCH/DELETE를 관습적인 CRUD라는 이유로 유지하지 않는다.

**우선순위**: 실행기 리팩토링과 함께 수정할 핵심 계약.

### F03. 운영 ‘메시지 보내기’의 접수 단위를 정해야 한다

**확인한 사실**: `MessageService:105`와 `RunService:343`은 각각 commit한다. Run의 `trigger_message_id`도 선택 사항이고, Run 입력 본문은 별도로 저장한다. 참조 검사는 같은 session의 메시지인지 확인하지만 사용자 역할·삭제 여부·내용 일치까지 보장하지 않는다. 내부 데모가 두 API를 차례로 호출한다는 사실은 운영 프론트의 호출 방식이나 장애를 입증하지 않는다. 데모의 수정은 이번 운영 리팩토링의 필수 범위에서 제외한다.

**운영 클라이언트도 두 번의 독립 접수로 구성할 경우 발생 가능한 흐름**

```text
메시지 저장 성공 → Run 접수 실패/네트워크 끊김
화면에는 보낸 대화가 남음 → 실행은 접수되지 않았을 수 있음
사용자가 다시 전송 → 메시지 키와 Run 키가 따로 생성됨 → 중복/연결 누락 관리가 복잡해짐
```

실행되지 않은 메시지를 초안/전송 실패로 표시하는 제품이라면 두 단계도 가능하다. 다만 완료된 사용자 메시지 저장과 실행 접수를 독립시키려면 실패 상태와 복구 절차가 필요하다. 현재 운영 프론트가 이런 문제를 겪고 있다고 판정한 것은 아니다.

**권장 방향**: 기존 Run 생성·Task resume의 공통 접수 서비스에서 사용자 입력 기록 + Task/Run + queued event + 멱등 접수 결과를 **하나의 짧은 DB transaction**으로 저장하고 202를 반환한다. 실제 Agent 실행/Executor 호출은 commit 이후 Worker가 수행한다. 기존 MessageService가 독자 commit하는 구조를 조정해야 하며, 내부에서 CRUD HTTP를 다시 호출하는 방식으로 합치지 않는다.

첫 호출에 agent_id를 받고 Task에 고정한다. resume는 서버가 원래 Agent/버전/checkpoint를 찾아야 한다. 프론트가 메시지 ID와 checkpoint ID를 조립할 필요를 줄인다.

### F04. 세션 없는 메시지 생성은 같은 요청을 재시도해도 새 세션을 만든다

**정적 근거**: `routes/messages.py:53`은 session_id가 없으면 먼저 세션을 생성한다. `MessageService:67`의 중복 조회와 `message_model.py:114`의 유일성 범위는 `(session_id, client_request_id)`다. 새 세션마다 멱등 범위가 달라진다.

**격리 재현 결과**: 같은 사용자·project·본문·Idempotency-Key로 session_id 없는 `POST /messages`를 두 번 호출하자 **세션 2개, 메시지 2개**가 생성되었다. 동시성이 없어도 재현되었다. 기존 세션에 같은 키로 다른 본문을 보내는 대조 확인에서는 충돌 응답 없이 예전 본문이 반환되었다.

**개선 방향**

- 기본 흐름은 세션 생성 → 원자적 Run 접수로 명확하게 둔다. 세션 생성도 모바일/네트워크 재시도를 지원한다면 멱등 키를 받는다.
- sessionless 편의 API가 필요 없다면 호환 기간 뒤 제거한다. 유지한다면 `(사용자, 동작, 키)` 단위로 세션 생성까지 포함한 접수 결과를 기억한다.
- 같은 키/같은 입력은 동일 결과, 같은 키/다른 입력은 명시적 충돌로 처리한다. Run도 `RunService:227`에서 키가 같으면 본문 비교 없이 기존 행을 반환하므로 함께 정리한다.
- 현재 Message 중복 조회는 삭제된 행을 제외하지만 유일 인덱스는 제외하지 않는다. 삭제 뒤 키 재사용의 DB 동작은 추가 검증 대상으로 남긴다.

### F05. 세션·프로젝트 삭제/이동이 장기 작업과 분리되어 있다

**정적 근거**: `SessionService.update:139`는 프로젝트를 이동하고, `delete:170`은 세션/메시지를 soft delete한다. `ProjectService.delete:189`, `UserService.delete:97`도 Task/Executor 상태 검사 없이 하위 데이터를 삭제한다. `tasks.py:76`의 일반 Task 조회는 활성 세션을 요구한다.

**사용자 영향**: 실행 중인 세션을 삭제하면 실행이 실제로 중단됐는지와 상관없이 UI의 조회 경로를 잃을 수 있다. 프로젝트 이동은 작업 시작 시점의 소속/설정과 현재 소속을 다르게 만들 수 있다. 이번 조사에서 실제 1주일짜리 Executor 작업을 삭제하여 시험하지는 않았다.

**권장 기본 정책**: 미종료 작업이 있는 세션의 삭제·이동은 409로 거절한다. 프로젝트 삭제도 하위 세션 조건을 원자적으로 검사한다. 이름 변경처럼 실행 문맥을 바꾸지 않는 편집은 허용할 수 있다. 외부 작업을 포함한 취소가 필요하면 먼저 취소 요청 → 실제 종료 확인 → 삭제로 진행한다. `DELETE` 수신만으로 Executor 취소 성공으로 표시하지 않는다.

보관함으로 숨기는 기능이 필요하면 실행/감사 기록을 유지하는 archive로 정의할 수 있지만 이번 최소 범위의 필수 신규 API는 아니다. soft delete와 물리 삭제·보존 만료도 구분한다.

### F06. GET 한 건이 하위 데이터를 전부 읽은 뒤 버린다

| 요청 | 내부 작업 | 실제 응답 |
|---|---|---|
| GET project | `ProjectService.read:103`에서 전체 세션과 메시지 수 집계 | `ProjectResource`에는 sessions 없음 |
| GET session | `SessionService.read:104`에서 전체 활성 메시지 조회·변환 | `SessionResource`에는 messages 없음 |

응답 스키마가 하위 데이터를 제외하는 것은 격리 검증에서도 확인했다. 따라서 응답 크기만 작아 보여도 DB와 Python은 불필요한 일을 한다. 특히 오래 사용한 세션에서 비용이 커질 수 있다. **이 쿼리가 앞선 부하테스트 지연의 몇 %였는지는 이번에 측정하지 않았다.**

**개선 방향**: 단건은 메타데이터만 읽는다. 세션/메시지 목록은 기존 페이지 API를 이용한다. 숫자가 꼭 필요한 화면이면 필요한 집계만 별도로 계획한다. 라우트 이름이나 프론트 응답을 바꾸지 않고도 먼저 줄일 수 있는 범위다.

### F07. 기본 프로젝트 DELETE는 프로젝트를 지우지 않고 대화만 지운다

`ProjectService.delete:206`은 기본 프로젝트 행을 유지하고 하위 세션/메시지를 지운다. 내부 결과에는 `project_deleted=False`와 설명이 있지만 라우터는 항상 204를 반환한다.

현재 의도대로 구현된 동작이지만, 제품 계약으로는 “프로젝트 삭제”와 “대화 전체 초기화”를 구별하기 어렵다. 기본 프로젝트 삭제는 409로 거절하는 것이 명확하다. 전체 대화 비우기가 실제 요구일 때만 별도 명령과 확인 UX를 제공한다. 일반 프로젝트 DELETE도 F05의 실행 중 삭제 정책을 따른다.

### F08. Task/Run 상태와 프론트 입력 가능 여부가 일치하지 않는다

- `TaskService.ACTIVE_STATUSES`와 DB의 활성 유일 인덱스는 pending/running만 포함한다. WAITING_INPUT은 업무가 끝나지 않았어도 이 범위 밖이다.
- `RunService:520`은 analysis의 interrupt를 Task WAITING_INPUT으로 묶는다. 사용자 응답 대기와 Executor 이벤트 대기를 공개 Task 상태에서 구별하지 못한다.
- `tasks.py:38`의 is_active는 이 활성 집합으로 계산한다. 이를 “새 메시지를 보내도 되는가”로 해석하면 잘못된 UI가 된다.
- Task resume는 내부 checkpoint_run_id를 metadata에 넣는다. `_interrupted_run:147`은 명시된 ID에는 INTERRUPTED 조건을 붙이지 않는다. Task WAITING_INPUT 검사는 존재하지만, 최신 interrupt 식별자/버전에 대한 조건부 응답 계약은 분명하지 않다.
- Task cancel은 WAITING_INPUT이면 즉시 CANCELED 처리한다(`RunService:644`). 이 경로 자체에는 외부 Executor 중단 확인이 없다.
- 비-analysis 응답에서는 Task를 삭제하는 분기가 있다(`RunService:529`). 여러 Agent에 공통 Task API를 제공하려면 전체 업무 유형의 수명을 일관되게 해야 한다.

**개선 방향**: Task의 공개 상태를 QUEUED/RUNNING/WAITING_USER/WAITING_EXECUTOR/CANCEL_REQUESTED/종료로 분리한다. 세션 응답에 현재 Task와 `can_send/can_resume/can_cancel` 같은 판단 결과를 넣어 프론트를 단순화한다. 이 값은 안내이며, 서버가 접수 시 최신 상태를 다시 원자적으로 검사해야 한다. 서버의 내부 lock_owner/lease/checkpoint는 일반 UI 필수값에서 제외한다.

GET과 SSE는 이미 존재한다. 전체 플로우를 보는 UI에는 Task GET/stream을 기본으로 하고, Run GET/stream은 개별 실행 구간의 기록/진단이 필요할 때 유지한다. 둘을 무조건 중복이라고 삭제하지 않는다. `join`은 현재 단건 GET과 같은 즉시 반환이므로 별도 의미가 없다. 사용처 확인 후 폐기 후보로 둔다.

### F09. Workflow ‘생성됨·실행 검증됨·전체 공개됨’이 충분히 분리되지 않는다

**최종 정책:** 일반 사용자도 승격 가능하며 Executor 성공은 요구하지 않는다. 아래 기존 성공 검사 관찰은 현재 코드 설명이다. 승격 가능 여부와 실제 실행 검증 상태를 분리하도록 변경한다. Workflow는 Agent가 Executor 요청으로 변환하는 자산이며 원문을 그대로 제출한다고 가정하지 않는다.

**정적 근거**

- `WorkflowService.create_candidate:68`은 클라이언트 문서의 형식, 원본 Run의 소유권, `workflow_origin=generated`를 확인한다. 제출한 문서가 그 Run이 생성/실행한 문서와 같은지 비교하는 검사는 없다.
- `promote:134`는 원본 Run SUCCESS와 파일 내부 Workflow READY를 확인하고 새 template을 만든다. 그 문서 버전의 실제 Executor 성공 증거를 직접 검사하는 계약은 아니다. mock/제출 비활성 환경의 성공과 실제 실행 성공을 동일하게 취급해서는 안 된다.
- template은 모든 사용자에게 보인다(`_visibility:48`). promote는 단순 폴더 이동이 아니라 공개 범위를 넓히는 동작이다.
- 반복 promote는 매번 새 UUID를 생성한다. clone은 원본 source_run_id를 유지한다. 다른 사용자의 template을 clone한 뒤 promote하면 원본 Run 소유권 검사에 걸릴 수 있다. 재실행을 요구하려는 정책이라면 그 경로가 명시되어야 한다.

**개선 방향**: 업무상 “검증된 템플릿”이 필요하다면 생성 artifact의 hash/버전과 실제 실행 결과를 연결한다. 검증되지 않은 수동 등록도 허용하려면 출처/검증 상태를 구별한다. 승격은 멱등하게 하고 공개 범위·공유 권한을 명시한다. clone 후 재실행/승격 흐름을 정한다. 공유 PV 사용은 유지하되 파일 공유가 이런 업무 검증을 대신하지는 않는다.

### F10. 받아 주지만 효력이 분명하지 않은 필드와 응답 불일치가 있다

| 항목 | 현재 확인 | 권장 |
|---|---|---|
| Project system_prompt / prompt_version | CRUD 저장·버전 증가는 있으나 조사한 기본 Run→Agent 경로에서 Project 값을 적용하는 연결을 찾지 못함 | 유지 확정. 매 Agent 실행 프롬프트에 적용하고 별도 project_memory도 프로젝트 범위에서 공유. |
| Session settings | 자유 dict로 생성·조회, 기본 실행 경로에서 사용 확인 안 됨 | UI 설정과 실제 Agent 입력 설정을 구분하고 지원 필드만 문서화한다. |
| Message llm_max_retries | 스키마는 받지만 MessageService에서 사용하지 않음 | 공개 입력에서 제거/폐기. 서비스 용량 설정을 사용자가 임의 조절하는 계약은 만들지 않는다. |
| Message 생성 응답 | message_id/삭제 필드 등을 가진 MessageRead + 채워지지 않는 assistant_message/llm_run/agent_run 필드 | 생성·단건·목록의 메시지 표현을 맞추고 실제 접수 결과만 반환한다. |
| Task 목록/Run logs | Task 목록은 limit만 있고 cursor 없음. Task의 Runs와 Run logs는 전체 조회 | 장기 작업/많은 resume에서도 누락·무제한 읽기가 없도록 페이지 계약 적용 |
| 메시지 정렬 | 공통 created_at+UUID cursor, 표시에는 sequence_no 제공 | 대화 순서와 페이지 경계를 sequence_no 기준으로 일치시킨다. |
| 세션 목록 | 생성 시각 정렬만 지원 | 최근 대화순이 필요하면 last_activity_at의 의미/갱신 규칙과 인덱스를 정의한다. |
| 에러/응답 위치 | 같은 409라도 업무 원인 구분 부족, Location의 /api/v1 하드코딩 | SESSION_BUSY, STALE_INTERRUPT, IDEMPOTENCY_CONFLICT 등 안정적 코드; 실제 mount/prefix로 Location 생성 |

이 항목을 모두 운영 장애로 단정하지 않는다. 앞부분은 사용자가 기대하는 기능과 실제 기능의 차이이며, 뒤쪽은 통합·확장 시 비용을 줄이는 일관성 개선이다.

## 4. 합의된 사용 규칙을 CRUD에 적용한 목표 정책

아래는 **권장 목표 계약**이다. 현재 구현된 허용표가 아니다. 기존에 합의한 동일 세션 잠금, 다른 세션 독립 실행, 1주 이상 Executor 대기를 전제로 한다.

| 세션 상황 | 일반 입력/새 실행 | 사용자 resume | 이름 변경 | 이동·삭제 | 조회 |
|---|---|---|---|---|---|
| 미종료 Task 없음 | 허용 | 해당 없음 | 허용 | 허용 | 허용 |
| QUEUED/RUNNING | 거절 | 거절 | 허용 가능 | 거절 | 허용 |
| WAITING_USER | 거절 | 현재 interrupt의 응답만 허용 | 허용 가능 | 기본 거절, 명시적 취소 후 처리 | 허용 |
| WAITING_EXECUTOR | 거절 | 거절, 인증된 내부 완료 이벤트만 재개 | 허용 가능 | 기본 거절, 외부 종료 확인 필요 | 허용 |
| CANCEL_REQUESTED | 거절 | 거절 | 허용 가능 | 종료 확인 전 거절 | 허용 |

- 다른 유휴 세션은 같은 사용자여도 독립 접수한다. 사용자 전체를 잠그지 않는다.
- 같은 세션의 여러 Agent도 이 규칙을 공유한다. Agent 종류를 바꾸어 잠금을 우회하지 않는다.
- 이름은 표시 정보로 취급한다. 프롬프트·실행 입력·프로젝트 이동은 실행 문맥이므로 별도 조건이 필요하다.
- 한 주 동안 DB transaction/Worker 슬롯을 유지하는 구현을 뜻하지 않는다. 잠금은 영속 상태와 짧은 접수 transaction으로 판정한다.
- 삭제 판단과 새 Run 접수가 경쟁해도 둘 다 성공하지 않도록 같은 세션 직렬화 기준을 쓴다. 프로젝트 삭제와 새 세션 생성의 경쟁도 부모 상태를 포함해 검증한다.

## 5. 권장 API 사용 흐름

X-User-Id를 사용하는 관리 API별 규칙과 main_model_name 선택은 [공통 사용자 식별 명세](../design/platform-user-api-contract-2026-09-28.md)에 구체화했다. 사용자 요청에 따라 session_system_prompt 등 나머지 플랫폼 선택 옵션은 이번 범위에서 제외한다.

**아래는 최초 제안 흐름이다. 최신 사용자 결정에 따라 공개 Tasks 경로를 필수로 유지하지 않고 조회·resume·취소·stream을 Runs에 통합하는 방향으로 재정의한다.** 기존 기록의 Task/Run 연결은 내부 상태 책임을 설명하는 참고로 남긴다.

```text
X-User-Id로 활성 사용자 확인 → Project 선택/생성 → Session 생성/선택
  → POST /sessions/{session_id}/runs
      사용자 입력 + agent_id + Idempotency-Key
      한 transaction: Message + Task/Run + queued event + 접수 결과
      202: message_id / task_id / run_id / 상태 조회·stream 위치
  → GET /tasks/{task_id} 또는 GET /tasks/{task_id}/stream
  → WAITING_USER이면 POST /tasks/{task_id}/resume
      interrupt_id + 예상 상태 버전 + 응답 + Idempotency-Key
      동일 Task/Agent에서 새 Run 구간 접수
  → WAITING_EXECUTOR이면 사용자 입력을 잠근 채 상태만 관찰
  → 최종 종료 확인 후 같은 세션의 다음 입력 허용
```

처음부터 sessionless 편의 호출을 필수로 만들지 않는다. 남겨야 한다면 세션 생성까지 하나의 멱등 접수 결과로 관리한다. 기존 사용자 전송용 Message POST는 이 표준 흐름과 충돌하지 않도록 사용처 전환 후 축소한다.

필요한 추가 계약은 많지 않다.

- X-User-Id 공통 사용자 식별, 관리자 전용 사용자 관리, GET /users/me 본인 정보 조회.
- 선택 가능한 Agent의 읽기 전용 목록/상세. 코드 registry에서 공개 가능한 Agent만 보여준다. HTTP 등록 API를 만드는 것은 아니다.
- 기존 Session/Task 응답의 현재 작업·입력 가능 여부·대기 이유·다음 조회 권고 시간.
- 기존 Run/resume의 명시적 입력 스키마·멱등 접수·interrupt 버전. 임의 metadata로 제어값을 받는 영역을 줄이고 Agent별 사용자 입력은 검증된 스키마로 확장한다.

SSE는 DB 상태 조회를 완전히 없애지 않는다. 현재 SSE의 조회 비용 개선은 기존 런타임 설계에 따라 수행하며, 이번 CRUD 검토에서 별도 SSE 제품을 추가하지 않는다.

## 6. 리팩토링 순서와 완료 기준

| 순서 | 작업 묶음 | 완료 시 확인할 사용자 결과 |
|---|---|---|
| 1 | 공통 사용자 헤더·역할 + 상태별 허용표 + 공개 DTO 확정 | 활성 사용자와 역할을 확인하고 누구의 어떤 동작이 언제 허용되는지 일관되게 판단 |
| 2 | 공통 접수 서비스, 메시지/Task/Run 원자 저장, 재시도 결과 | 전송 재시도 시 메시지·세션·실행 중복 없음; 같은 키의 다른 요청은 충돌 |
| 3 | 새 런타임의 세션 점유와 CRUD 변경 조건 연결 | 실행·외부 대기 중 입력/삭제/이동 거절; 다른 세션은 접수 가능 |
| 4 | 불필요한 전체 조회 제거·페이지·상태 응답 개선 | 단건 메타 조회가 대화 길이에 비례해 모든 메시지를 읽지 않음 |
| 5 | Workflow 승격/공유·사용되지 않는 필드·구 API 전환 | 검증/공개 의미가 명확하고 문서·프론트·부하 스크립트가 같은 계약 사용 |

4의 불필요한 쿼리 제거는 앞 단계와 독립적으로 먼저 할 수 있다. 단, DTO/조회 쿼리 변경과 상태 머신 변경을 한꺼번에 섞어 효과를 판단하기 어렵게 만들지 않는다.

기존 API 호환이 필요하면 구 라우트는 같은 접수 서비스로 연결하는 adapter로 유지한다. 구·신 구현이 각자 메시지나 Run을 쓰는 이중 쓰기는 피한다. 호환 응답을 위한 query/DTO 변경과 DB schema migration이 필요한 상태·멱등 기록 변경을 구분해서 배포한다. 운영 프론트와 검증에 사용하는 부하 스크립트를 전환하고 실제 사용처 확인 전 API를 바로 삭제하지 않는다. 내부 demo의 호환은 필수 운영 요구로 삼지 않는다.

**구현 후 필요한 통합 검증**

1. 사용자 헤더 누락·미등록·비활성 사용자·일반 사용자의 관리 기능 호출·자원 비소유자의 접근이 거절되는지. ID 소유 증명은 이번 요구 범위가 아님.
2. 같은 입력의 직렬/동시 재시도, 같은 키의 다른 본문, commit 직후 응답 유실 후 재시도.
3. 동일 세션 두 접수/접수와 삭제/프로젝트 삭제와 세션 생성의 경쟁. 다른 세션은 정상 접수되는지.
4. 두 탭에서 과거 interrupt에 응답했을 때 최신 단계가 잘못 진행되지 않는지.
5. WAITING_EXECUTOR 중 입력/삭제/취소 동작과 Pod 교체 후 조회·재개. 외부 취소 미지원이면 UI가 지원하는 것처럼 표시하지 않는지.
6. 생성·조회·목록 메시지 표현, 기존/신 클라이언트 호환과 에러 코드.
7. 메시지 10개/다수인 세션의 단건 조회 SQL·읽은 행 수, 페이지 누락/중복, 로그 페이지.
8. Workflow 원본 문서 불일치·mock 성공·승격 재시도·타 사용자 template clone 후 재실행/승격.

이는 후속 작업의 완료 조건이다. 이번에 실제 PostgreSQL/부하 테스트까지 통과했다는 기록이 아니다.

## 7. 이번에 수행한 검증

명령: `.venv/bin/python scripts/diagnostics/review_crud_contracts.py`

| 검증 | 관찰 결과 | 범위 |
|---|---|---|
| 라우터 목록 추출 | 9개 그룹, 45 operation | 실제 등록 대상 파일의 AST |
| 사용자 라우트 무인증 접근 | GET 200 / PATCH 200 / DELETE 204 | 실제 라우터, DB/service 대체 |
| 활성 사용자 UUID를 Bearer로 전달 | 인증 함수에서 허용 | 실제 인증 함수, 사용자 저장소 대체 |
| 공개 Message 역할 | 다섯 역할 전부 허용 | 실제 Pydantic 스키마 |
| sessionless 동일 키 두 번 | 세션 2개 + 메시지 2개 | 실제 라우트/MessageService, 저장소·세션 생성 대체 |
| 기존 세션 동일 키 다른 본문 | 충돌 없이 기존 메시지 반환 | 위와 동일 |
| 상세 응답의 하위 데이터 | sessions/messages 제외 | 실제 response schema; 쿼리 비용은 정적 확인 |

여기서 assert 성공은 **문제 있는 현재 동작을 재현했다는 뜻**이다. 제품 요구 충족이나 보안 검사 합격을 의미하지 않는다. 실제 서비스 코드·DB·컨테이너 설정은 변경하지 않았다.

## 8. 전체 API별 판정

모든 경로의 prefix는 `/api/v1`이다. 아래 표는 현재 API 목록이며, 제안한 `/users/me`, `/agents`를 이미 구현된 것처럼 포함하지 않는다. 출처는 라우터 파일의 handler 시작 줄이다.

### users

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| POST | `/users` | 운영 경계 변경 | 관리자 전용 등록; 사용자와 기본 Project 원자 생성. F01 | [users.py:15](../../src/app/api/v1/routes/users.py#L15) |
| GET | `/users/by-name/{user_name}` | 공개 폐기 후보 | 개발 로그인 기능; 사용자 이름을 인증 수단으로 쓰지 않음. F01 | [users.py:21](../../src/app/api/v1/routes/users.py#L21) |
| GET | `/users/{user_id}` | 축소/대체 | 일반 사용자는 /users/me; 대상 사용자 조회 권한 구분. F01 | [users.py:26](../../src/app/api/v1/routes/users.py#L26) |
| PATCH | `/users/{user_id}` | 권한 변경 | 관리자만 수정; 마지막 관리자 강등 보호. F01 | [users.py:31](../../src/app/api/v1/routes/users.py#L31) |
| DELETE | `/users/{user_id}` | 재설계 | 관리자 전용 비활성화; 데이터 삭제·진행 작업 정책 분리. F01/F05 | [users.py:40](../../src/app/api/v1/routes/users.py#L40) |

### projects

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| POST | `/projects` | 유지·보강 | 개인 분류 단위로 적절; 재시도 및 실제 적용되는 프롬프트 계약 명시. F10 | [projects.py:21](../../src/app/api/v1/routes/projects.py#L21) |
| GET | `/projects` | 유지 | 소유자 필터와 cursor 사용 유지; 화면 요구에 맞는 정렬 | [projects.py:33](../../src/app/api/v1/routes/projects.py#L33) |
| GET | `/projects/{project_id}` | 유지·내부 수정 | 응답에서 버리는 하위 세션/메시지 집계 제거. F06 | [projects.py:52](../../src/app/api/v1/routes/projects.py#L52) |
| PATCH | `/projects/{project_id}` | 유지·계약 보강 | 이름과 실행 설정 구분; prompt_version의 적용 시점 명시. F10 | [projects.py:61](../../src/app/api/v1/routes/projects.py#L61) |
| DELETE | `/projects/{project_id}` | 유지·조건 변경 | 기본 프로젝트는 보호; 하위 미종료 Task가 있으면 거절. F05/F07 | [projects.py:72](../../src/app/api/v1/routes/projects.py#L72) |

### sessions

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| POST | `/projects/{project_id}/sessions` | 유지·보강 | 명시적 생성 경로; 재시도 시 같은 세션 반환을 지원할지 계약화. F04 | [sessions.py:36](../../src/app/api/v1/routes/sessions.py#L36) |
| GET | `/projects/{project_id}/sessions` | 유지·응답 보강 | 최근 활동순/현재 Task 요약으로 세션별 추가 조회를 줄임. F08/F10 | [sessions.py:49](../../src/app/api/v1/routes/sessions.py#L49) |
| GET | `/sessions/{session_id}` | 유지·내부 수정 | 전체 메시지 로딩 제거; 현재 Task와 입력 가능 여부 제공. F06/F08 | [sessions.py:71](../../src/app/api/v1/routes/sessions.py#L71) |
| PATCH | `/sessions/{session_id}` | 유지·조건 변경 | 이름 변경과 프로젝트 이동 분리 검증; 실행 중 이동 거절. F05 | [sessions.py:82](../../src/app/api/v1/routes/sessions.py#L82) |
| DELETE | `/sessions/{session_id}` | 유지·조건 변경 | 미종료 Task 확인; 외부 취소와 삭제를 동일하게 취급하지 않음. F05 | [sessions.py:94](../../src/app/api/v1/routes/sessions.py#L94) |

### messages

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| POST | `/messages` | 통합/폐기 후보 | 원자적 접수로 흡수하거나 세션 생성까지 멱등 보장. F03/F04 | [messages.py:37](../../src/app/api/v1/routes/messages.py#L37) |
| POST | `/sessions/{session_id}/messages` | 공개 역할 축소 | 정상 전송은 Run 접수에서 원자 저장; 초안 기능이 있을 때만 별도 쓰기 유지. F02/F03 | [messages.py:78](../../src/app/api/v1/routes/messages.py#L78) |
| GET | `/sessions/{session_id}/messages` | 유지·보강 | sequence cursor·실패 표시·불명 세션 처리 일관화. F10 | [messages.py:98](../../src/app/api/v1/routes/messages.py#L98) |
| GET | `/messages/{message_id}` | 유지 | 단건 조회는 적절; 생성/목록 DTO와 일치. F10 | [messages.py:125](../../src/app/api/v1/routes/messages.py#L125) |
| PATCH | `/messages/{message_id}` | 현재 형태 재설계 | 실행 입력 불변 원칙; 편집이 필요하면 버전/분기·재실행. F02 | [messages.py:134](../../src/app/api/v1/routes/messages.py#L134) |
| DELETE | `/messages/{message_id}` | 현재 형태 재설계 | UI 숨김·문맥 제외·원본 기록 삭제 의미를 구분. F02 | [messages.py:146](../../src/app/api/v1/routes/messages.py#L146) |

### runs

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| POST | `/sessions/{session_id}/runs` | 유지·접수 계약 변경 | 사용자 입력/Task/Run/event 원자 저장, agent_id 검증, 멱등 충돌 검사. F03/F08 | [runs.py:32](../../src/app/api/v1/routes/runs.py#L32) |
| GET | `/sessions/{session_id}/runs` | 유지 | 실행 구간 이력용; 프론트 현재 상태의 기본 조회와 역할 구분 | [runs.py:47](../../src/app/api/v1/routes/runs.py#L47) |
| GET | `/sessions/{session_id}/runs/{run_id}` | 유지 | 개별 실행 구간 상태; checkpoint 내부값을 사용자 제어값으로 요구하지 않음 | [runs.py:54](../../src/app/api/v1/routes/runs.py#L54) |
| GET | `/sessions/{session_id}/runs/{run_id}/logs` | 유지·보강 | cursor·상한·사용자 공개 로그 필드 검토. F10 | [runs.py:62](../../src/app/api/v1/routes/runs.py#L62) |
| GET | `/sessions/{session_id}/runs/{run_id}/join` | 폐기 후보 | 현재 GET 단건과 동일한 즉시 반환; 사용처 확인 후 정리. F08 | [runs.py:82](../../src/app/api/v1/routes/runs.py#L82) |
| POST | `/sessions/{session_id}/runs/{run_id}/cancel` | 역할 축소/통합 후보 | 일반 사용자는 Task 전체 취소 기본; 구간 취소를 별도 제공할 이유가 있으면 유지. F08 | [runs.py:87](../../src/app/api/v1/routes/runs.py#L87) |
| GET | `/sessions/{session_id}/runs/{run_id}/stream` | 선택 유지 | 개별 구간 관찰용; Task stream과 수명·용도를 구분. F08 | [runs.py:92](../../src/app/api/v1/routes/runs.py#L92) |

### tasks

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| GET | `/sessions/{session_id}/tasks` | 유지·보강 | limit 외 cursor 추가; 현재 Task는 Session 응답에도 제공. F08/F10 | [tasks.py:51](../../src/app/api/v1/routes/tasks.py#L51) |
| GET | `/tasks/{task_id}` | 유지·공개 DTO 변경 | 전체 사용자 작업 상태·대기 이유·capabilities 중심. F08 | [tasks.py:89](../../src/app/api/v1/routes/tasks.py#L89) |
| GET | `/tasks/{task_id}/runs` | 유지·보강 | 여러 resume 기록을 페이지로 제공. F10 | [tasks.py:98](../../src/app/api/v1/routes/tasks.py#L98) |
| POST | `/tasks/{task_id}/resume` | 유지·입력 계약 변경 | 현재 interrupt/예상 버전 검증, 원 Agent 고정, 응답 기록 원자 저장. F03/F08 | [tasks.py:113](../../src/app/api/v1/routes/tasks.py#L113) |
| POST | `/tasks/{task_id}/cancel` | 유지·의미 보강 | 전체 작업 취소 기본 경로; 외부 중단 확인 전 CANCEL_REQUESTED. F08 | [tasks.py:143](../../src/app/api/v1/routes/tasks.py#L143) |
| GET | `/tasks/{task_id}/stream` | 유지·표준 관찰 경로 | 최초 실행부터 resume·Executor 대기까지 전체 작업 관찰. F08 | [tasks.py:153](../../src/app/api/v1/routes/tasks.py#L153) |

### workflows

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| POST | `/workflows` | 업무 전용·계약 변경 | 생성 산출물 원본으로 등록; 임의 문서는 별도 출처/검증 상태. F09 | [workflows.py:51](../../src/app/api/v1/routes/workflows.py#L51) |
| GET | `/workflows` | 유지 | candidate/template 공개 범위 명시; Agent 목록과 구분. F09 | [workflows.py:56](../../src/app/api/v1/routes/workflows.py#L56) |
| GET | `/workflows/{workflow_id}` | 유지·보강 | 문서 버전·검증/출처 제공; 내부 파일 경로 공개 필요성 검토. F09 | [workflows.py:79](../../src/app/api/v1/routes/workflows.py#L79) |
| PATCH | `/workflows/{workflow_id}` | 유지·의미 보강 | 표시 metadata 수정과 실행 문서 변경 구분; 공개 template 정책. F09 | [workflows.py:84](../../src/app/api/v1/routes/workflows.py#L84) |
| POST | `/workflows/{workflow_id}/promote` | 유지·계약 재설계 | 실제 검증 근거·공개 권한·재시도 중복 방지. F09 | [workflows.py:89](../../src/app/api/v1/routes/workflows.py#L89) |
| POST | `/workflows/{workflow_id}/clone` | 유지·후속 흐름 보강 | 소유자 변경 후 재실행/승격 가능한 출처·버전 흐름. F09 | [workflows.py:94](../../src/app/api/v1/routes/workflows.py#L94) |
| DELETE | `/workflows/{workflow_id}` | 유지·보강 | 신규 추천 제외와 기존 실행 참조 보존 구분; template 삭제 권한 명시 | [workflows.py:99](../../src/app/api/v1/routes/workflows.py#L99) |

### jupyter_servers

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| GET | `/jupyter-servers` | 연계 조회 유지 | 사용자가 선택해야 하는 경우 공개; 생성 주체가 외부이면 생성 CRUD 불필요 | [jupyter_servers.py:42](../../src/app/api/v1/routes/jupyter_servers.py#L42) |
| GET | `/jupyter-servers/{server_id}` | 연계 조회 유지 | 소유자 기준·민감 접속정보 제외 계약 유지 | [jupyter_servers.py:57](../../src/app/api/v1/routes/jupyter_servers.py#L57) |
| POST | `/jupyter-servers/{server_id}/health` | 용도 한정·보강 | 실제 점검과 캐시 갱신 명령; 반복 호출 제한/캐시 정책 검토 | [jupyter_servers.py:62](../../src/app/api/v1/routes/jupyter_servers.py#L62) |

### redis

| Method | Path (prefix 생략) | 판정 | 개선 포인트 | 출처 |
|---|---|---|---|---|
| GET | `/redis/ping` | 운영 경로로 분리 | 사용자 기능과 구분; 플랫폼 health/readiness와 역할을 정함 | [redis.py:16](../../src/app/api/v1/routes/redis.py#L16) |
