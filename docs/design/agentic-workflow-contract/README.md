# Workflow JSON 설계 초안

> 이 문서는 036~037 당시 설계·prototype 기록이다. 아래 runtime 미구현 문구는 당시 범위이며 현재 계획·승인·Executor compiler와 API는 구현되어 있다. [현재 구현 계약](../../workflow-json-reference.md)을 우선한다. 기존 Workflow CRUD·pgvector·동적 Dataset 등록·Gaia는 여전히 후속이다.

> 2026-10-01 사용자 식별 변경: 아래 `X-User-Id` 계약은 이전 결정 기록이다. 현재 서비스는 SSO 로그인 쿠키와 변경 요청의 `X-CSRF-Token`을 사용하며, [현재 SSO 계약](../../sso-authentication.md)을 우선한다. Run/계획/데이터 body와 내부 UUID 소유권은 유지한다. Gaia body의 user_id를 검증된 로그인 신원으로 신뢰하지 않는다.

이 문서는 현업 데이터 사이언티스트와 Agent 개발자가 같은 규격으로 Workflow를 정의하도록 하기 위한 계약 초안이다. 등록된 Skill과 Tool을 조합하는 실행 단계, 결과 기반 Agent 판단, 조건부 실행, 입력 연결과 기대 산출물을 정의한다. 실행 중 실제 데이터 객체는 Jupyter 커널에 남기고 JSON에는 참조만 기록한다.

상태는 **설계 및 오프라인 계약 검증 완료, 런타임 미구현**이다. `2.0-draft`는 새 Workflow 정의의 초안 버전이며, 기존 Tool registry의 `2.0` 또는 기존 Workflow 문서 버전과 별개다. 현재 Workflow API나 compiler에 이 JSON을 제출해도 실행할 수 없다. API 이행, pgvector 검색, 승인 화면, 제출 코드 생성과 LangGraph 구현은 후속 작업이다.

## 제공 파일과 검증 범위

| 파일 | 목적 |
|---|---|
| [workflow-definition.schema.json](workflow-definition.schema.json) | JSON Schema Draft 2020-12 기반 구조 검증 |
| [quality-review.repository.json](examples/quality-review.repository.json) | 현재 등록 자산을 참조하는 데이터 품질 분석 정의 예제 |
| [repository-catalog.json](examples/repository-catalog.json) | 실제 카탈로그 일부의 Skill 소속과 함수 signature 확인용 데이터 |
| [full-analysis.fixture.json](examples/full-analysis.fixture.json) | 원천 로드부터 전처리·분석·저장까지 설명하는 목표 계약 예제 |
| [illustration-catalog.json](examples/illustration-catalog.json) | 목표 예제를 검증하기 위한 설명용 카탈로그. 등록된 구현이 아님 |
| [validation.json](validation.json) | 실행한 오프라인 검증 결과와 제한 |
| [검증 스크립트](../../../scripts/design/validate_workflow_draft.py) | 개발자가 초안을 검증하는 도구. 서비스 운영 CLI 또는 관리 API가 아님 |

실제 자산 예제는 함수와 인자, Skill 소속이 현재 레포와 일치하는지 AST·카탈로그로 확인했다. 분석 결과의 정확성, 고객 데이터 접근, Parquet 로드, LLM 판단, Tool 출력과 Executor 통신을 실행해 검증한 것은 아니다. 특히 일부 기존 Tool은 반환값만 생성하고 유용한 셀 출력을 충분히 만들지 않는다. 표준 관찰 계층을 연결하기 전에는 Agent가 해당 결과를 읽을 수 있다고 가정하지 않는다.

목표 예제의 `demo.*` Skill·Tool은 설명용이며 Python 구현이 없다. 현업이 실제 구현과 등록을 완료하기 전에는 추천 풀이나 실행 대상이 될 수 없다. 기존 카탈로그에 없는 결측 대체·분석·데이터 저장 Tool을 이미 구현된 기능처럼 표시하지 않는다.

037에서 Step의 선택적 `parameter_controls`를 추가했다. 고정 파라미터의 편집 가능 여부와 값 schema를 명시하고 객체·시스템 참조 편집은 금지한다. [계획 승인·Executor 제출 계약](../plan-interaction-contract/README.md)에서 화면과 제출 코드의 연결을 개발용 prototype으로 검증했다. 이 문서의 원래 검증 범위와 production 미구현 상태는 유지한다.

## Workflow 정의와 실행 기록

Workflow는 재사용 가능한 E2E 분석 정의다. 입력값 그 자체, 사용자 ID, 실제 파일 경로, Executor ID, 실행 상태와 승인 내용은 정의에 저장하지 않는다. 등록된 Skill·Tool 밖의 임시 코드나 Tool 수정본이 들어간 실행 계획은 Workflow 승격 대상이 아니다. 판단 지점·조건·리포트 선언은 흐름 메타데이터이며 새로운 Python Tool을 정의하는 기능이 아니다.

실제 Run은 Workflow ID와 정의 버전, 확정된 입력 참조, 수정된 계획 버전, 승인 내용, 결정된 파라미터와 근거, 결과 참조를 보관한다. 사용자 수정으로 원본 Workflow가 바뀌지 않는다. 원본 변경은 새 정의 버전으로 저장하고 진행 중 Run에는 소급 적용하지 않는다.

현업은 계약에 맞는 JSON을 직접 등록·수정할 수 있고, Agent가 만든 정의는 사용자의 명시적 승격으로 추천 풀에 들어간다. 실행 성공은 등록·승격의 필수 조건이 아니다. 구조 검증·등록 자산 확인·실제 실행 성공 이력은 서로 다른 검증 수준이며 버전별로 구분해 표시한다. 기존 API의 source Run 및 승격 조건은 새 계약에 맞춰 별도로 이행해야 한다.

## 정의 필드

| 필드 | 의미 |
|---|---|
| `schema_version` | JSON 계약 버전. 초안에서는 `2.0-draft` |
| `workflow_id`, `definition_version` | 재사용 자산 식별자와 변경 버전 |
| `name`, `description`, `goal`, `tags` | 사람이 이해할 이름·적용 상황·목표·검색 메타데이터 |
| `inputs` | 실행마다 확정할 입력. 값의 JSON Schema, 필수 여부, 편집 가능 여부와 선택적 실제 기본값 |
| `steps` | 등록 Skill·Tool 참조, 파라미터 연결, 의존 관계와 선택적 조건 |
| `decisions` | 확보할 근거, 판단 지침과 판단 결과의 허용 형식 |
| `execution` | Workflow에 명시할 실행 정책. 생략한 항목은 설정에서 해결 |
| `expected_outputs` | 결과·저장 데이터·리포트의 의미와 출처 |

각 객체는 계약에서 정의한 필드만 받는다. Step에 `code`, 임의 함수 본문, 임의 실행 경로를 넣을 수 없다. Step·decision·output ID는 Workflow 안에서 서로 중복하지 않는다. Skill·Tool ID는 레지스트리의 정식 키를 사용한다. 현재 자산 예제는 기존 flat ID를 그대로 참조하며, 향후 여러 파일·함수 등록을 위한 qualified ID도 문법적으로 허용한다. 전체 레지스트리 이행 규칙을 구현한 것은 아니다.

## 입력 연결

각 Tool 인자는 아래 출처 중 하나만 갖는다. 같은 인자에 literal과 이전 출력 등을 함께 넣는 모호한 표현은 거부한다.

| `source` | 추가 필드 | 의미 |
|---|---|---|
| `workflow_input` | `name` | 사용자가 확인한 이번 실행 입력 |
| `literal` | `value` | 정의에 고정된 실제 상수. 임의 코드로 평가하지 않음 |
| `step_output` | `step_id`, `selector` | 같은 Execution의 이전 Tool 반환 객체 |
| `agent_decision` | `decision_id` | 실행 결과를 확인한 Agent 판단값 |
| `system_context` | `key` | 검증된 사용자·프로젝트·세션 정보 또는 데이터 저장 루트 |

`selector: []`는 전체 반환값, `["dataframe"]`은 dict key, `[0]`은 tuple/list 위치다. Tool의 원래 반환 형식을 유지한다. selector를 임의의 Python 표현식으로 평가하지 않고 key/index 접근만 수행한다. 카탈로그의 docstring·실제 반환 계약으로 의미를 확인해야 하며 AST만으로 반환값의 의미나 존재 여부를 보장할 수 없다.

`inputs`의 `kind: data_reference`는 이번 서비스가 검증한 파일·데이터 참조다. 예제는 참조 ID를 string으로 표현한다. 실제 실행에서는 서비스가 접근 범위와 실행 환경 가시성을 확인하고 경로로 해석한다. 이를 그대로 `parquet_path`의 파일 경로로 취급하거나 사용자가 임의의 PVC 경로를 주입하도록 구현하면 안 된다. 참조 ID와 경로 간 실제 resolver는 후속 구현이다.

모르는 값에 `0`, 빈 문자열, 빈 배열을 임의 기본값으로 만들지 않는다. 입력 정의에서 `default`를 생략할 수 있다. 값이 없는데 필수라면 승인 전에 사용자가 확정해야 한다. 실행 결과가 있어야 정할 수 있는 항목은 사용자 누락값 대신 `agent_decision`으로 선언한다.

## 결과 기반 판단과 조건

`after_steps`는 Agent가 판단하기 전에 성공하고 관찰 결과가 확보되어야 하는 단계다. `instruction`은 Skill 지침과 함께 판단 기준을 제공한다. `output_schema`는 결정값의 타입·enum·범위를 제한한다. 부족한 근거를 핑계로 임의 값을 채우지 않고 사용자 확인 또는 재계획으로 전환한다.

예제의 이상치 방법은 `iqr`, `zscore`, `isolation_forest` 중에서 선택한다. 결측 대체 예제의 선택 범위 역시 최초 계획에 포함되며 사용자가 판단 범위를 승인한다. 해당 범위에서의 정상 결과 판단과 코드 오류 수정 수준은 다른 정책이다.

조건은 `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`과 `all`, `any`, `not`으로 표현한다. 좌우 값은 입력 연결 형식을 사용한다. 타입이 맞는 실제 값만 비교하며 Python의 암묵적 형변환이나 `eval`을 사용하지 않는다. DataFrame 전체를 조건 피연산자로 사용하지 않는다.

필요한 값이 없거나 selector가 잘못됐거나 타입이 맞지 않으면 조건을 false로 간주해 조용히 건너뛰지 않는다. 실행을 멈추고 오류·추가 확인·재계획 정책으로 처리한다. false 조건의 Step은 오류 없이 미실행 상태로 기록한다.

이번 초안은 조건부 생산 단계의 출력을 사용하는 단계·산출물에 같은 명시적 조건이 있어야 한다. 복잡한 조건의 논리적 함의까지 추론하지 않으며, 다른 조건으로 같은 안전성을 주장하면 거부한다. 조건부 단계만을 근거로 하는 후속 Agent decision과 여러 분기 결과의 합류는 별도 계약이 필요해서 이 초안에서는 지원하지 않는다.

리포트의 `evidence_steps`는 실행된 근거만 사용한다. 조건 때문에 미실행된 단계는 미실행으로 표시하고 결과를 꾸며내지 않는다. `required: true`인 조건부 산출물은 해당 조건이 true일 때 생성 의무가 있다는 뜻이다.

## 승인 화면과 실행 계획

추천 Workflow와 신규 등록 자산 조합 계획을 합친 후보 수는 설정의 최대값을 따른다. 후보 선택만으로 실행하지 않는다. 사용자가 최종 계획을 확인·수정하고 승인한 뒤 실행한다.

프론트에는 Skill, Tool 함수명, 간단 설명과 파라미터를 제공하며 소스 코드를 보내지 않는다. Agent가 확정한 분석 입력은 값이 채워진 편집 가능 필드, 모르는 필수 입력은 빈칸, 결과 기반 판단은 지침·선택 범위로 구분한다. 시스템 식별자·저장 루트와 반환 객체 연결은 일반 편집 필드가 아니다.

파라미터·단계 제외 같은 단순 편집은 서버에서 검증해 반영하고 추가 LLM 호출 없이 처리한다. 실행된 단계는 읽기 전용이다. 단계 제거로 의존 관계가 깨지면 남은 단계를 조용히 삭제·대체하지 않고 수정 필요 내용을 알려준다. 의미 변경이 필요한 요청은 Agent 재계획 후 새 계획 버전으로 확인한다.

최종 승인 시 계획과 사용하는 Workflow·Skill·Tool 정의, 실행 정책을 고정한다. Tool 소스는 필요한 import를 포함한 함수에서 docstring만 제거해 실행용으로 보관한다. 실제 코드와 해시는 Run별 공유 저장소에 보관하고 DB/checkpoint에는 참조를 둔다. 재배포 후 현재 Tool을 다시 읽어 기존 승인의 코드를 바꾸지 않는다. 커널 프로필의 라이브러리 호환성까지 자동 보존하는 것은 아니다.

## 실행 정책과 수명

Workflow의 명시 정책을 먼저 사용하고, 없는 항목은 통합 설정에서 해결하며 적법한 HITL 수정으로 최종값을 확정한다. 모델은 Run 시작 때 선택하여 재개 동안 유지하고 커널 프로필은 세션 기준으로 유지한다. SINGLE/MULTI 선택은 실제 계획을 실행할 수 있는지 검증해야 한다.

`repair_level`은 0 실패 전달, 1 실행 연결 보정, 2 목적·입출력을 유지한 실행별 Tool 수정, 3 등록 자산 재계획과 승인, 4 목표 범위에서 자율 코드·계획 수정이라는 합의 수준이다. `max_repair_attempts`는 별도의 시도 제한이다. 0은 수정하지 않는다는 뜻이고, 실제 값은 정책 해석에서 확정한다. 수정된 코드가 포함된 실행 계획을 등록 자산만의 Workflow로 자동 승격하지 않는다.

`review_mode: decision_boundary`는 다음 판단이 필요한 지점에서 Agent가 결과를 검토한다. 고정된 단계마다 LLM을 호출하지 않는다. `every_tool`은 각 Tool 후 검토, `every_n_tools`는 설정된 수마다 검토하며 판단 지점이 먼저 오면 거기서 검토한다. `review_interval_tools`는 `every_n_tools`에서만 필요하고 1 이상이어야 한다. 검토마다 모델 지연·비용이 추가된다. SINGLE에서는 Tool 사이에 Agent 검토를 삽입할 수 없다.

반복 목표·최대 반복 횟수와 예산 부족 시 추가 승인이라는 정책은 합의되어 있다. 일반 반복 구조의 JSON 표현과 compile 규칙은 이 초안에 포함하지 않았다. DAG를 순환시키거나 자기 Step 출력을 참조해서 반복을 흉내 내지 않는다.

새 요청의 계획·승인 단계에서는 Execution을 미리 만들지 않는다. 승인된 첫 코드 제출 직전에 생성한다. 같은 분석의 MULTI는 후속 Operation을 추가한다. 목표 완료 및 필요한 결과 수집·Tool 파일 저장 후 Finalize를 호출하고 terminal 결과를 확인한다. Finalize는 접수만으로 성공이 아니고 마지막 Operation 실패 시 성공 종료 수단으로 사용할 수 없다.

완료 후 추가 분석은 새 Run·새 Execution·새 커널에서 저장된 데이터·결과를 활용한다. 기존 커널 변수의 잔존을 가정하지 않는다. 결과 설명이나 리포트 편집만으로 충분하면 새 Execution을 만들지 않는다. 커널 보관·지연 Finalize·Execution 간 커널 재사용은 이번 설계에서 제외했다.

Executor가 실행 중이면 동일 세션 입력을 잠근다. HITL 대기에서는 같은 Run에 대한 입력을 받는다. Worker 실행 자리는 HITL·Executor 대기 동안 장기 점유하지 않는다. 다른 세션의 실행은 허용하되 사용자를 기준으로 전체 세션을 잠그지 않는다.

## 산출물과 메모리

전처리 데이터는 계획에 선언한 등록 Tool로 저장한다. 모든 함수 반환값을 자동 저장하지 않고, 저장 기능 없는 Tool에 임의의 쓰기 코드를 덧붙이지 않는다. `dataset_output_dir`는 USER·PROJECT·SESSION 정책에 맞춰 서비스가 제공하고 기본은 PROJECT다. 고객 MinIO는 공유 원천 저장소이며 우리 첨부·메타데이터 쓰기 영역이라고 가정하지 않는다.

중간 데이터 등록 순서는 생성 구간 완료, 실제 파일 확인, Agent 결과 해석, 재사용 데이터 등록이다. 마지막 학습 실패가 정상 저장된 데이터의 유효성을 자동 취소하지 않는다. 별도 API 도입 또는 기존 Artifact API의 SUCCEEDED 조건 완화는 **미정**이며, 어느 쪽도 이번 초안에서 확정하지 않는다. `expected_outputs`는 산출물의 선언으로 파일 생성이나 API 등록을 자동 보장하는 명령이 아니다.

리포트 기본은 Markdown이며 선택적으로 같은 내용을 HTML로 렌더링한다. 기존 POST Artifact의 notebook Markdown 추가 기능을 활용하는 방향이다. 기존 API는 전체 Execution SUCCEEDED 이후 사용 가능하므로 최종 리포트 등록은 terminal 성공 확인 후 수행한다. 보고서 생성·등록 실패는 이미 성공한 Executor 코드를 재실행하는 이유가 아니다. Run 최종 상태와 산출물 전달 상태의 구체적인 분류는 공개 이벤트 계약에서 이어서 정의한다.

세션 범위 데이터의 스키마·수치·결론도 기본적으로 세션 내에서 사용한다. 명시적으로 공유한 정보만 프로젝트 공통 메모리에 반영한다. 세션 맥락은 기존 대화·Run·결과 참조를 조립하며 별도의 session memory 저장소를 추가하지 않는다. `project_memory`는 프로젝트 공유 가능한 배경·근거·선호를 부분별로 업데이트하고 `system_prompt`는 각 Agent 호출에 적용한다.

## API와 Gaia 연결 결정

새 요청과 HITL 재개는 `/api/v1/sessions/{session_id}/runs`에서 `input` 또는 `command.resume`으로 구분한다. 입력은 텍스트·이미지·파일 참조를 담는 `input.content`로 확장하는 방향이다. 파일 업로드는 별도이며 Run에 파일 본문이나 대용량 base64를 보관하지 않는다. 현재 text-only 모델은 이미지 해석이 가능하다고 가정하지 않는다.

일반 POST는 접수 JSON, POST `/runs/stream`은 접수와 SSE 연결, GET `/runs/{run_id}/stream`은 기존 Run 구독·재접속 역할이다. X-User-Id와 Idempotency-Key를 사용하고 접속 종료를 작업 취소로 취급하지 않는다. 공개 SSE는 message·activity·interaction·artifact·run 계열로 분리하고 내부 Graph state·Tool 코드를 노출하지 않는다.

Gaia 기본 라우터는 수정하지 않는다. 등록할 Gaia 어댑터 compiled graph가 `message`, `user_id`, `session_id`, 지원되는 모델 필드를 공통 실행 서비스 입력으로 변환한다. 실제 분석 Graph·Run 관리·Worker는 공용이다. Gaia의 반환 형식·스트리밍·재개 방식은 실제 템플릿 호출 코드를 확인하기 전까지 호환 검증 완료로 표시하지 않는다.

## 오프라인 검증 방법

레포 루트에서 개발 환경의 Python으로 실행한다. 검증 스크립트는 이미 설치된 `jsonschema_rs`와 repository 확인 시 `PyYAML`을 사용한다. 새 서비스 의존성을 추가하지 않는다.

```sh
python scripts/design/validate_workflow_draft.py --self-check --repository-root .
python scripts/design/validate_workflow_draft.py docs/design/agentic-workflow-contract/examples/quality-review.repository.json --catalog docs/design/agentic-workflow-contract/examples/repository-catalog.json --repository-root .
```

검증은 구조, 중복 ID, 등록 Tool·Skill 소속·인자, 입력 참조, 의존 관계와 순환, Agent 판단 근거 순서, 조건부 출력 보호와 실행 정책 조합을 확인한다. 실제 반환값의 selector 존재 여부, 타입 변환, 파일 권한·저장소 접근, 승인 토큰, 실행 결과 정확성은 런타임 검증 사항이다. 값의 내장 JSON Schema는 외부 참조를 허용하지 않는다.

## 후속 구현과 미정 항목

1. 이 초안으로 승인 화면·typed HITL·Executor 제출 계획의 실제 payload를 작성하고 round trip을 확인한다.
2. 반복·복잡한 분기 합류, 데이터 참조 resolver와 카탈로그 다중 함수 등록을 구체화한다.
3. Workflow POST·수정·승격·버전·JSON 저장·pgvector 검색을 이행한다.
4. Agent middleware, 결과 관찰 계층과 목표 LangGraph를 구현한다. 일반 조건 평가와 동적 판단을 분리한다.
5. 통합 Run POST·POST/GET stream, Gaia 어댑터, 테스트·데모·부하테스트 클라이언트와 활성 문서를 함께 이행한다.
6. 중간 데이터 등록 API의 방식은 별도 논의한다. 실제 Gaia 템플릿과 Executor 연계는 E2E로 검증한다.

이전 Workflow 정의나 checkpoint를 새 계약이라고 재해석하지 않는다. 기존 문서와 실행 코드의 상태를 보존한 설계 초안이므로 런타임 교체 전 버전·진행 중 Run 이행 계획이 필요하다.
