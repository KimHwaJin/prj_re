# 계획 승인과 Executor 제출 계약 초안

> 2026-10-01 사용자 식별 변경: 아래 `X-User-Id` 계약은 이전 결정 기록이다. 현재 서비스는 SSO 로그인 쿠키와 변경 요청의 `X-CSRF-Token`을 사용하며, [현재 SSO 계약](../../sso-authentication.md)을 우선한다. Run/계획/데이터 body와 내부 UUID 소유권은 유지한다. Gaia body의 user_id를 검증된 로그인 신원으로 신뢰하지 않는다.

이 문서는 Workflow 정의를 프론트의 계획 확인 화면, 사용자 수정·승인 요청, 내부 실행 계획과 Executor 요청으로 연결한다. 프론트는 코드나 전체 Workflow를 다시 보내지 않고 계획 ID·버전과 허용된 수정만 제출한다. 서버는 최종 계획을 검증하고 소스를 고정한 뒤 실행 요청을 생성한다.

상태는 **개발용 계약 prototype 검증 완료, 서비스 미연결**이다. 생성된 예제는 현재 공개 Run API나 SSE에서 실제로 반환되는 형식이 아니다. `scripts/design/plan_contract_prototype.py`는 오프라인 설계 검증 도구이며 production compiler, Agent 노드 또는 운영 CLI가 아니다. LLM·API 서버·DB·Executor 서비스·Gaia를 호출하지 않았다.

## 제공 파일

| 파일 | 의미 |
|---|---|
| [interaction event schema](interaction-event.schema.json) | 계획 interaction의 opened·updated·resolved 구조 |
| [resume request schema](resume-request.schema.json) | 계획 수정·승인 Resume body |
| [최초 계획 화면](examples/public/interaction.opened.json) | Skill·Tool·입력·판단 기준과 실행 정책 표시 |
| [승인 요청](examples/public/approve-plan.request.json) | 이상치 방법을 iqr로 지정하면서 승인 |
| [승인 결과 이벤트](examples/public/interaction.resolved.json) | 실제 승인된 최종 계획 버전과 값 |
| [단계 제외 요청](examples/public/edit-plan.request.json) | 실행 없이 이상치 탐지 단계 제외 |
| [수정된 계획 화면](examples/public/interaction.updated.json) | 제외된 단계와 생성하지 않을 산출물을 표시 |
| [내부 승인 snapshot](examples/internal/approved-plan.snapshot.json) | 공개하지 않는 소스·Skill·입력·정책·수정 이력 고정 |
| [최초 Executor 제출](examples/internal/executor-submit.request.json) | load·profile·statistics 세 단계 제출 |
| [후속 Operation](examples/internal/executor-operation.request.json) | 판단 후 outliers를 sequence 3으로 추가 |
| [Finalize](examples/internal/executor-finalize.request.json) | 마지막 Operation 성공 후 명시적 최종화 |
| [계약과 로컬 Tool 검증](validation.json) | 수행한 probe의 결과와 범위 |
| [Executor 요청 모델 검증](executor-schema-validation.json) | 현재 Executor 모델에서 body를 검증한 별도 결과 |

`examples/public`만 프론트 전달 대상이다. `examples/internal`에는 실행 코드와 저장 경로가 포함되어 있으므로 공개 SSE나 API 응답으로 그대로 전달하지 않는다. 날짜·UUID·이벤트 sequence·Executor version·600초 대기시간은 설명용 값이다. 실제 배포의 모델·커널·시간 설정이나 운영 기본값을 뜻하지 않는다. `/executor-data/demo/quality.parquet`도 실제 Executor에서 확인한 파일이 아니다.

## 계획 화면의 구분

계획에는 Workflow 식별자와 정의 버전, 계획 ID·버전, 목표, 사용하는 Skill, Tool 함수명·설명, 입력, 판단 기준과 기대 산출물을 표시한다. 함수 코드나 내부 커널 변수는 표시하지 않는다. 데이터 참조는 공개 ID로 보여주고 실제 경로는 서버가 해결한다.

| 항목 | 화면 처리 |
|---|---|
| Agent가 확정한 입력 | `has_value: true`, `origin: agent`, 해당 값으로 편집 필드 초기화 |
| 모르는 입력 | `has_value: false`, `value` 없음. 필수라면 승인 전에 입력 |
| 명시적 null | `has_value: true`, `value: null`. 값 누락과 구분 |
| 사용자 수정값 | `origin: user`. 수정한 값으로 표시 |
| 이전 반환 객체 | Step 참조로 표시. 일반 값 편집 금지 |
| 실행 후 판단할 파라미터 | `kind: deferred`, 판단 기준·결과 schema 표시. 사용자가 적법한 고정값으로 지정 가능 |
| 시스템 식별자·저장 루트 | 기술 참조로 표시. 실제 경로를 편집값으로 제공하지 않음 |

같은 Workflow 입력을 여러 Tool이 사용하면 화면의 입력 필드를 공유한다. Tool마다 별도의 값을 만들어 서로 달라지게 하지 않는다. 결과 기반 선택을 비워둔 경우 사용자 누락값이 아니라 승인된 Agent 판단 항목이다.

정책 화면에는 확정한 SINGLE/MULTI·수정 수준·수정 횟수와 허용 모드·서비스 상한을 구분해 제공한다. 이 예제는 결과 판단이 있으므로 MULTI만 선택 가능하다. 실제값은 Workflow 정책·설정·허용된 HITL 수정에서 해결하고 서비스 상한을 넘어설 수 없다.

## Tool 파라미터의 편집 선언

입력값은 Workflow `inputs`의 schema와 `editable`을 따른다. Tool의 고정값도 편집을 허용하려면 Step에 `parameter_controls`를 선언한다. 예시는 다음과 같다.

```json
{
  "arguments": {
    "method": {"source": "literal", "value": "iqr"}
  },
  "parameter_controls": {
    "method": {
      "editable": true,
      "value_schema": {
        "type": "string",
        "enum": ["iqr", "zscore"]
      }
    }
  }
}
```

이번에 Workflow draft schema와 검증 도구에 이 선언을 추가했다. 명시된 argument에 대해서만 선언하고 데이터 객체·Workflow 입력 연결·시스템 경로를 Tool 값 편집으로 바꿀 수 없다. 결과 판단 항목은 원래 decision schema 범위에서 사용자 지정이 가능하며, 추가 제약을 선언하면 두 schema를 모두 충족해야 한다. schema 없는 고정 상수의 편집 가능 범위를 값에서 추측하지 않는다.

사용자가 deferred 값을 고정한 뒤에도 허용 범위 내에서 다시 수정할 수 있다. 값을 literal로 바꿨다는 이유로 편집 권한과 schema를 잃지 않는다. 선택한 컬럼의 실제 존재 여부처럼 데이터 확인이 필요한 의미 검증은 실행 전에 관찰 결과로 확인해야 한다.

## 요청 경로와 수정 의미

일반 접수는 `POST /api/v1/sessions/{session_id}/runs`, 즉시 이벤트 구독은 같은 경로의 `/stream`을 사용한다. 헤더는 `X-User-Id`, `Idempotency-Key`, `Content-Type: application/json`이며 POST stream은 `Accept: text/event-stream`을 추가한다. body는 공개 `run_id`, 현재 `resume_token`, `command.resume`을 갖는다.

사용자는 body로 user_id·context·Tool 코드·함수 이름·전체 Workflow를 지정하지 않는다. Gaia body의 user_id는 별도 어댑터 입구에서 처리하며 이 승인 body와 혼합하지 않는다.

| 요청 필드 | 의미 |
|---|---|
| `action` | `edit_plan`은 화면 갱신만, `approve_plan`은 검증된 최종 계획 승인 |
| `plan_id`, `plan_revision` | 사용자가 본 계획 식별자와 버전 |
| `input_values` | 변경할 공개 입력 이름과 실제 값. 생략한 값은 유지 |
| `step_changes` | Step ID·파라미터 이름·새 값. 중복 수정 항목 거부 |
| `excluded_step_ids` | 보내면 제외 목록 전체를 대체. 생략하면 현재 제외 목록 유지 |
| `execution_overrides` | 허용 모드·오류 수정 수준·횟수의 사용자 변경 |

단순 값 변경은 schema·정책 검증을 통과하면 추가 LLM 호출 없이 반영한다. 의미 변화·Tool 교체·깨진 의존성은 이 요청으로 조용히 대체하지 않고 재계획 대상으로 돌린다. 자연어 수정·질문 답변·모든 후보 거절과 자유 코드 승인 action은 이 schema의 범위가 아니며 별도 typed payload로 이어서 정의해야 한다.

## 계획 버전과 승인

`edit_plan`은 실행을 시작하지 않는다. 바뀐 계획은 새 `plan_revision`으로 만들고 `interaction.updated`를 발행한다. 새 interaction revision과 resume token으로 같은 대기 상태를 갱신한다. 예제의 token은 deterministic fixture이며 실제 서비스는 새 임의 토큰을 발급해야 한다.

`approve_plan`은 단순 수정과 최종 승인을 한 번에 처리할 수 있다. 예제는 화면의 plan revision 1에서 method를 iqr로 지정하고, 서버가 확정한 revision 2를 승인 snapshot과 `interaction.resolved`에 기록한다. 또 승인 버튼을 누르게 만들지 않는다. 화면에 표시한 범위를 넘어선 재계획이 필요하면 승인 완료로 처리하지 않는다.

run_id·세션 소유권·token·계획 버전·필수 입력·데이터 접근 범위·의존성·파라미터 범위·실행 정책을 검증한다. token이나 버전이 오래됐으면 현재 화면 갱신이 필요하다는 오류를 반환하고 실행하지 않는다. HTTP 오류 코드·서비스 오류 응답 모델은 실제 API 연결 때 통일한다.

production에서는 승인 소비, 최종 계획 저장, durable queue 등록을 원자적으로 처리해야 한다. 같은 Idempotency-Key와 같은 body 재시도는 기존 접수 결과를 반환하고 다른 body에는 충돌을 반환한다. prototype의 메모리 기반 consumed 검증은 이 DB 트랜잭션·동시성·멱등성 구현을 대신하지 않는다.

## 단계 제외와 산출물

제외된 Step에 다른 유지 Step이 의존하거나, 필요한 Agent 판단 근거가 사라지면 거부한다. 종속 단계까지 자동 제외하지 않는다. 이미 실행된 단계 수정·제외는 허용하지 않는 것이 실제 서비스의 원칙이며 prototype은 실행 전 편집만 구현한다.

독립된 마지막 단계는 제외할 수 있다. 예제에서 outliers를 제외하면 해당 Step은 `excluded`, outlier_result는 `excluded_by_user`로 보인다. 원래 정의를 지우거나 해당 결과가 생성됐다고 표시하지 않는다. 사용되지 않는 판단 지점은 실행 계획에서 제외하며 리포트는 실제 수행한 근거만 사용한다. 목표를 달성할 수 없는 제외는 Agent 재계획이나 사용자 확인이 필요하다.

## 승인 소스와 제출 코드

서버는 승인된 단계·입력·정책·수정 이력·Skill Markdown과 선택된 Tool 함수를 고정한다. Tool은 필요한 import를 포함한 함수를 추출하고 docstring만 제거한다. 다른 함수 본문·주석·import를 보존하는지 AST로 확인한다. 실제 소스와 생성 코드의 SHA-256도 기록한다. 승인 hash는 실행 일관성 확인용이며 사용자 인증을 대신하지 않는다.

Compiler가 추가하는 것은 함수 호출, 반환 객체 변수 연결, 결과 관찰 출력이다. 파라미터 값은 Python literal로 안전하게 표현하고 이전 객체는 시스템이 만든 변수의 key/index 참조로 연결한다. 등록 함수 자체에 결과 JSON 형식이나 저장 코드를 덧붙이지 않는다.

개발용 관찰 formatter는 DataFrame의 shape·최대 10개 컬럼·최대 5행 preview와 제한된 dict/list 값을 텍스트로 출력한다. 잘린 결과는 truncated로 표시하고 비유한 수치는 명시적으로 표시한다. 전체 데이터를 직렬화하거나 preview를 전체 통계라고 간주하지 않는다. 해당 formatter는 셀 출력 전달 계약을 검증하기 위한 prototype이며, production의 관찰 예산·텍스트 파싱·이미지 참조와 manifest 수집은 후속이다.

## Operation 경계와 Step 번호

최초 제출은 load·profile·statistics를 sequence 0·1·2로 묶는다. 다음 outliers는 앞 결과에 대한 판단이 필요하므로 최초 코드에 섞지 않는다. Operation의 성공 결과와 관찰을 확보한 뒤 Agent가 판단한다.

조건이 true이면 후속 `/executions/{execution_id}/operations`에 outliers를 sequence 3으로 제출한다. false이면 Step을 미실행으로 기록하고 빈 Operation을 제출하지 않는다. 조건이 false일 때 사용하지 않을 method 값까지 Agent에게 요구하지 않는다. 판단이 아직 미확정이면 Finalize하지 않는다.

Executor Step ID와 논리 Step ID는 다르다. 제출 sequence·receipt의 Step ID·계획 Step ID 매핑을 저장하고 Operation metadata에는 logical_steps를 추적 정보로 넣는다. 이벤트는 해당 Execution이 속한 Run으로 전달한다. 사용자 세션의 가장 최근 Run에 임의 연결하지 않는다.

후속 요청에는 최신 continuation/state.version을 `expected_version`으로 사용한다. 최초 제출의 `operation` wrapper를 후속 API에 보내지 않는다. idempotency key와 정확한 요청 body를 저장해서 재시도마다 값이나 version을 바꾸지 않는다. HTTP 202는 접수이며 성공은 이벤트·상태 확인 후 판단한다.

더 실행할 작업이 없고 마지막 Operation이 성공했으면 Finalize한다. Finalize 이후 terminal 성공을 확인한 뒤 최종 Artifact 등록을 진행한다. 실패 Operation을 Finalize로 성공 처리하지 않는다. 완료 후 추가 분석은 새 Run·Execution·커널을 사용하고 기존 저장 데이터를 재로드한다.

Executor `context.task_id`는 현재 필수 추적 필드이므로 내부 Task 식별자를 전달한다. 프론트에 Task 생성 명령이나 관리 API를 다시 요구하지 않는다. 실제 커널 profile·대기시간·전체 실행 제한은 설정에서 해결한다.

## 실행한 검증

개발 환경에서 다음 명령으로 public/internal 예제를 생성하고 probe를 수행했다.

```sh
python scripts/design/plan_contract_prototype.py --repository-root . --output-dir /tmp/plan-contract-probe
python scripts/design/validate_workflow_draft.py --self-check --repository-root .
```

단계 제외·의존성 보호, 값 범위·읽기 전용 참조, stale token·revision, 중복 승인, 코드·사용자 식별자 주입 거부, 판단 경계, 후속 sequence, 명시적 null, 재편집과 고정 파라미터 제어를 확인했다. 실제 등록된 네 Tool의 생성 코드를 임시 12행·2컬럼 Parquet에서 실행해 데이터 연결과 이상치 후보 index 11, 네 번의 텍스트 관찰 출력을 확인했다. 테스트 데이터 파일명에는 따옴표를 포함해 경로 literal 처리를 확인했다.

실제 Executor 요청 모델은 Executor의 Python 3.12 환경에서 따로 검증했다. Agent 개발 환경의 Python 3.11에 Executor 모듈을 production 의존성으로 추가하지 않았다. 최초 제출·후속 Operation·Finalize의 body가 현재 모델을 통과하고 MULTI 대기시간 누락·잘못된 wrapper·불연속 sequence를 거부하는지 확인했다. 실제 서버의 상태 전이·허용 커널·파일 접근·이벤트 전달을 검증한 것은 아니다.

Executor 모델 검증은 Python 3.12와 Executor checkout이 있을 때 다음과 같이 재현한다. Agent 서비스에 Executor 패키지를 설치하거나 실제 HTTP 요청을 보내지 않는다.

```sh
/absolute/path/to/executor/.venv/bin/python scripts/design/validate_executor_contract_examples.py \
  --executor-root /absolute/path/to/executor \
  --examples-dir docs/design/plan-interaction-contract/examples/internal \
  --output /tmp/executor-contract-validation.json
```

## 남은 범위

prototype은 한 후보의 실행 전 편집, 현재 등록 Tool 예제, MULTI 및 eq/ne 조건 실행만 구현한다. Workflow 초안의 나머지 비교 연산자, 일반 분기 합류·반복, 복수 후보 선택, 실행 중 미실행 단계 편집은 아직 연결하지 않았다. 설정·허용 목록 해결은 fixture이고 실제 데이터 참조 resolver·모델 정책·DB 승인은 구현되지 않았다.

다음은 이 계약을 기반으로 LangGraph의 계획·HITL·실행 준비 경계와 공통 서비스 타입을 구현하는 것이다. API·SSE·데모·부하테스트 클라이언트·활성 문서는 실제 서비스 연결 때 함께 변경해야 한다. Gaia는 기존 어댑터 Graph 방향을 유지하되 템플릿의 호출·반환 계약 확인이 남아 있다. 중간 데이터 등록 API 방식은 여전히 미정이다.
