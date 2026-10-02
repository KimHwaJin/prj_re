# Workflow JSON 작성 규격

현업 데이터 사이언티스트와 Agent 개발자가 재사용 가능한 분석 Workflow를 작성하는 현재 코드 기준 안내다. 등록된 Skill·Tool로 E2E 흐름을 구성하고 입력·결과 연결·Agent 판단·조건·산출물·실행 정책을 선언한다. Python 함수 본문과 특정 실행의 ID·데이터 객체·승인 결과를 정의에 넣지 않는다.

2026-10-01의 045까지 반영한 코드에서 확인했다. **현재 Agent 계획·승인·Executor compiler는 `2.0-draft` 정의를 사용하지만, 기존 `/api/v1/workflows` CRUD는 별도 1.3 모델을 사용한다.** 새 JSON을 기존 POST에 제출하여 추천 풀에 등록하는 이행은 아직 끝나지 않았다. 버전 문자열의 draft는 현재 유지된 계약 값이며 compiler 미구현을 뜻하지 않는다.

[Agent 요청·응답](public-run-api.md), [원본 JSON Schema](../src/service_contracts/resources/workflow-definition.schema.json), [기본 예제](contracts/workflow/quality-basic.json) · [필드 주석](contracts/workflow/quality-basic.jsonc), [조건부 예제](contracts/workflow/quality-conditional.json) · [필드 주석](contracts/workflow/quality-conditional.jsonc)를 함께 참고한다. 036 설계 기록보다 이 문서를 현재 구현 안내로 우선한다.

본문의 `jsonc` 예제는 각 필드를 주석으로 설명한다. 저장·등록·검증에는 주석 없는 `.json`을 사용한다. [주석 파일 안내](contracts/field-comments.md), [Workflow schema 주석](design/agentic-workflow-contract/workflow-definition.schema.jsonc)을 참고한다.

## 서로 다른 JSON을 구분하기

| JSON | 용도 | 현재 위치 또는 인터페이스 |
|---|---|---|
| Workflow 정의 | 등록 자산으로 구성한 재사용 가능한 흐름 | schema_version=2.0-draft, workflow_id 문자열 |
| PlanView | 유저가 보는 후보·파라미터·승인 화면 | SSE/interrupt의 payload.plans[] |
| RunRequest | 새 대화 또는 HITL 액션 | POST /sessions/{session_id}/runs |
| approved snapshot | 실제 승인 값·고정한 소스·hash·실행 문맥 | 내부 checkpoint. 공개 Workflow 정의가 아님 |
| Executor 요청 | 함수 코드·인자·Operation·멱등 키 | Agent가 승인 snapshot에서 생성하여 제출 |
| 기존 CRUD document | 이전 재사용 파일과 candidate/template | 1.3 workflow wrapper 또는 WorkflowDefinition |

정의의 workflow_id는 의미 있는 문자열이고 기존 CRUD resource의 workflow_id는 DB UUID다. 두 ID를 같은 필드 값처럼 교환하지 않는다. definition_version, plan_revision, interaction.revision, resume_token도 각각 정의 변경·계획 편집·화면 변경·현재 재개 대상을 식별한다.

Workflow 대상은 등록된 Skill·Tool만의 조합이다. Agent의 실행별 custom 함수·수정 소스가 포함된 계획은 free_code/repair 실행으로 구분하고 workflow_eligible=false다. 실행 성공과 Workflow 등록 적격 여부는 서로 다르다.

## 정의의 전체 형태

```jsonc
{
  // 계약 형식 버전. SSE는 1, 새 Workflow는 2.0-draft, legacy 예제는 1.3을 사용한다. 정의 수정 횟수와 구분한다.
  "schema_version": "2.0-draft",
  // 재사용 Workflow 정의를 식별하는 문자열. 기존 Workflow 관리 API의 DB UUID와 구분한다.
  "workflow_id": "quality_basic",
  // 동일 Workflow 정의의 변경 버전. 승인 화면 편집 횟수나 Run ID가 아니다.
  "definition_version": 1,
  // 사용자에게 표시할 Workflow 또는 계획 이름. 고유 식별자는 workflow_id/plan_id로 별도 관리한다.
  "name": "기본 데이터 품질 확인",
  // 해당 정의·입력·Skill·Step·산출물의 의미를 사람이 읽을 수 있게 설명한 문자열.
  "description": "등록된 로드·품질 확인 Tool을 조합한다.",
  // Workflow 또는 계획이 달성하려는 분석 목표.
  "goal": "기본 품질을 확인하고 실제 근거를 설명한다.",
  // 새 Workflow에서는 입력 이름별 정의 객체, PlanView에서는 입력 필드 목록, legacy에서는 실행 입력값 객체다.
  "inputs": {
    // Workflow 입력 이름 dataset. inputs는 정의, input_values는 해당 입력의 최종값이다.
    "dataset": {
      // 사용자 화면의 표시 제목. value_schema 내부에서는 JSON Schema 표시 제목이다.
      "title": "분석 데이터",
      // 해당 정의·입력·Skill·Step·산출물의 의미를 사람이 읽을 수 있게 설명한 문자열.
      "description": "서비스가 접근을 확인한 데이터 참조",
      // 입력 종류. parameter는 일반 값, data_reference는 서버가 접근을 확인한 데이터 참조다. 임의 파일 경로 입력과 구분한다.
      "kind": "data_reference",
      // 업무 입력·산출물에서는 필요한지 나타내는 boolean. JSON Schema에서는 필수 필드 이름 배열이다.
      "required": true,
      // 사용자가 수정할 수 있는지 표시한다. true여도 value_schema 및 실행 정책 검증을 통과해야 한다.
      "editable": true,
      // 입력·편집값을 검증하는 JSON Schema 객체 또는 boolean. 필수 여부 및 값 유효성은 별도다.
      "value_schema": {
        // 허용하는 JSON 데이터 타입. 이 Schema의 입력값 형식을 제한한다.
        "type": "string",
        // 허용 문자열의 최소 길이.
        "minLength": 1
      }
    }
  },
  // 분석 단계 목록. 새 Workflow의 Step은 하나의 등록 Tool을 참조하며 depends_on으로 선후관계를 정한다.
  "steps": [
    {
      // Workflow 내부 Step의 고유 문자열 ID. depends_on·step_output이 참조한다.
      "id": "load",
      // 레포에 등록된 Skill의 고유 식별자. 그 Skill에 속한 Tool을 참조해야 한다.
      "skill_id": "data_load",
      // 레포에 등록된 실행 가능한 Tool의 고유 식별자.
      "tool_id": "data_load",
      // 해당 정의·입력·Skill·Step·산출물의 의미를 사람이 읽을 수 있게 설명한 문자열.
      "description": "등록 데이터 참조를 로드한다.",
      // 먼저 완료되어야 하는 Step ID 목록. 단순 배열 순서와 구분되며 순환 의존성은 허용하지 않는다.
      "depends_on": [],
      // 새 Workflow에서는 실제 함수 인자 이름별 binding. legacy에서는 인자 값 또는 참조가 들어간다.
      "arguments": {
        // Tool 함수 인자 parquet_path의 값/출처 binding.
        "parquet_path": {
          // 값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.
          "source": "workflow_input",
          // 참조할 Workflow 입력 키. 예제 dataset은 inputs.dataset에서 정의한 입력이다.
          "name": "dataset"
        }
      }
    },
    {
      // Workflow 내부 Step의 고유 문자열 ID. depends_on·step_output이 참조한다.
      "id": "profile",
      // 레포에 등록된 Skill의 고유 식별자. 그 Skill에 속한 Tool을 참조해야 한다.
      "skill_id": "data_quality_check",
      // 레포에 등록된 실행 가능한 Tool의 고유 식별자.
      "tool_id": "profile_data",
      // 해당 정의·입력·Skill·Step·산출물의 의미를 사람이 읽을 수 있게 설명한 문자열.
      "description": "기본 구조와 결측 상태를 확인한다.",
      // 먼저 완료되어야 하는 Step ID 목록. 단순 배열 순서와 구분되며 순환 의존성은 허용하지 않는다.
      "depends_on": [
        "load"
      ],
      // 새 Workflow에서는 실제 함수 인자 이름별 binding. legacy에서는 인자 값 또는 참조가 들어간다.
      "arguments": {
        // Tool 함수 인자 data의 값/출처 binding.
        "data": {
          // 값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.
          "source": "step_output",
          // 대상 Step의 ID. 편집 대상으로 지정하거나 이전 결과·의존성·근거를 참조할 때 쓴다.
          "step_id": "load",
          // 이전 Step 반환값에서 추출할 키·인덱스 경로 배열. []는 반환값 전체이며 새 규격은 Python 식을 평가하지 않는다. legacy는 문자열 selector다.
          "selector": []
        }
      }
    }
  ],
  // 실행 결과를 보고 나중에 확정할 판단 목록. 정적 계획과 결과 기반 판단을 분리한다.
  "decisions": [],
  // 새 Workflow/PlanView에서는 실행 정책 객체. legacy Step/Tool에서는 실행 조건 값이다.
  "execution": {
    // SINGLE은 제출 계획을 한 실행 단위로, MULTI는 결과 판단·후속 Operation을 포함한 흐름으로 실행한다. 조건/decision은 MULTI가 필요하다.
    "mode": "MULTI",
    // 허용하는 오류 수정 자율성 수준 0~4. 상세 단계별 권한은 오류 수정 Runtime 문서를 따른다.
    "repair_level": 0,
    // 허용하는 오류 수정 시도 수. 무한 반복을 허용하지 않으며 서버 상한 이하만 가능하다.
    "max_repair_attempts": 0,
    // 실행 결과 이후 확인 정책. decision_boundary/every_tool/every_n_tools을 구분한다. 일반적인 계획 최초 승인을 대체하지 않는다.
    "review_mode": "decision_boundary"
  },
  // Workflow가 기대하는 분석 결과·데이터·보고서 선언. 선언만으로 파일이 생성되는 것은 아니다.
  "expected_outputs": [
    {
      // Workflow 내부 예상 산출물의 고유 문자열 ID.
      "id": "profile_result",
      // 산출물 종류. analysis_result는 분석 반환값, dataset은 저장 데이터, report는 보고서다.
      "kind": "analysis_result",
      // 해당 정의·입력·Skill·Step·산출물의 의미를 사람이 읽을 수 있게 설명한 문자열.
      "description": "기본 품질 결과",
      // 업무 입력·산출물에서는 필요한지 나타내는 boolean. JSON Schema에서는 필수 필드 이름 배열이다.
      "required": true,
      // 값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.
      "source": {
        // 값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.
        "source": "step_output",
        // 대상 Step의 ID. 편집 대상으로 지정하거나 이전 결과·의존성·근거를 참조할 때 쓴다.
        "step_id": "profile",
        // 이전 Step 반환값에서 추출할 키·인덱스 경로 배열. []는 반환값 전체이며 새 규격은 Python 식을 평가하지 않는다. legacy는 문자열 selector다.
        "selector": [
          "profile"
        ]
      },
      // 산출물 표현 형식 또는 Schema 형식 제약. 산출물은 native/parquet/markdown/html/json 등을 선언한다. 선언과 실제 저장 지원은 구분한다.
      "format": "native"
    },
    {
      // Workflow 내부 예상 산출물의 고유 문자열 ID.
      "id": "report",
      // 산출물 종류. analysis_result는 분석 반환값, dataset은 저장 데이터, report는 보고서다.
      "kind": "report",
      // 해당 정의·입력·Skill·Step·산출물의 의미를 사람이 읽을 수 있게 설명한 문자열.
      "description": "실제 실행 근거로 작성한 보고서",
      // 업무 입력·산출물에서는 필요한지 나타내는 boolean. JSON Schema에서는 필수 필드 이름 배열이다.
      "required": true,
      // 값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.
      "source": {
        // 값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.
        "source": "agent_report",
        // 판단 또는 보고서가 근거로 사용할 Step ID 목록. 실제 실행 결과를 사용한다.
        "evidence_steps": [
          "profile"
        ]
      },
      // 산출물 표현 형식 또는 Schema 형식 제약. 산출물은 native/parquet/markdown/html/json 등을 선언한다. 선언과 실제 저장 지원은 구분한다.
      "format": "markdown"
    }
  ]
}
```

이는 schema·현재 등록 자산·signature를 검증한 작성 예제다. 이 문서 작업에서 Tool/Parquet/Executor를 실행한 결과가 아니다. 위 예제와 저장된 JSON의 설명 문자열은 달라도 같은 규격을 따른다.

| 최상위 필드 | 필수 | 의미 |
|---|---|---|
| schema_version | O | 현재 정확히 2.0-draft |
| workflow_id | O | 재사용 정의의 문자열 ID |
| definition_version | O | 1 이상 정수. 원본 수정 버전 |
| name / description / goal | O | 사람이 이해할 이름·적용 설명·목표 |
| tags | 선택 | 중복 없는 검색 메타데이터 문자열 배열 |
| inputs | O | 입력 정의의 이름→객체. 입력이 없으면 빈 객체 |
| steps | O | 등록 Tool 실행 단계. 1개 이상 |
| decisions | O | 실행 결과 후 Agent 판단. 없으면 빈 배열 |
| execution | 선택 | 명시할 실행 정책 객체 |
| expected_outputs | O | 기대 산출물. 1개 이상 |

ID는 영문 소문자로 시작하고 영문 소문자·숫자·밑줄·점·하이픈, 최대 150자다. step/decision/output ID는 서로 중복하지 않는다. 객체는 정의된 필드만 허용하며 code/source_file/function_body 같은 추가 실행 필드는 거절한다.

## 입력과 사용자 편집

inputs 각 항목은 title/description/kind/required/editable/value_schema가 필수이고 default는 선택이다. kind는 parameter 또는 data_reference다. value_schema는 JSON Schema 객체 또는 boolean이며 외부 $ref/$dynamicRef를 허용하지 않는다. default가 있으면 해당 schema를 만족해야 한다.

분석 요청에서 확정한 값은 Run의 input_values로 관리한다. Workflow 자체에는 특정 사용자·프로젝트·실행의 입력값을 저장하지 않는다. 유효한 재사용 기본값만 default에 넣는다. 필수 미확정 값은 사용자 승인 전에 입력받고 임의의 0/빈 문자열/null로 채우지 않는다. 실제 null을 허용할 때는 value_schema에 이를 명시한다.

data_reference는 파일 경로가 아닌 서비스에 등록된 데이터 식별자다. 현재 ANALYSIS_DATASETS의 범위·owner 정보와 runtime_path를 확인해 실행 경로로 해석한다. 실제 파일 등록·동적 목록 API는 아직 미연결이다. 프로젝트/세션/유저 범위의 Dataset Registry 계약과 MinIO 원천 저장소는 별개다. 사용자가 임의 PVC 경로를 넣을 수 있는 API로 해석하지 않는다.

## 단계와 Skill과 Tool

steps 각 항목의 필수 필드는 id/skill_id/tool_id/description/depends_on/arguments다. when과 parameter_controls는 선택이다. 한 Step은 하나의 등록 Tool 함수를 호출한다. 같은 Tool을 여러 Step에서 사용할 수 있으나 Step ID는 고유해야 한다.

skill_id는 카탈로그의 Skill 키이고 tool_id는 그 Skill에 실제로 속한 등록 Tool 키다. 파일 이름·UI 표시명·함수명에서 임의 추정하지 않는다. 현재 예제는 data_load/data_quality_check 같은 기존 키를 사용한다. Skill 문서는 여러 Tool의 사용 시점과 조건을 설명하고 Tool docstring은 함수 목적·인자·출력 계약을 설명한다. Python 파일 하나에 여러 함수가 있어도 각 등록 함수를 tool_id로 식별한다.

depends_on은 이전 단계 ID 배열이다. steps 배열 순서만으로 의존 관계를 표현하지 않는다. 다른 Step의 반환값을 참조하려면 생산 Step이 upstream dependency여야 한다. 순환·알 수 없는 ID·근거 확보 전 판단값 사용은 거절한다. DAG 선언이 Tool 병렬 실행을 자동 보장하는 것은 아니다.

parameter_controls는 직접 값/later Agent decision 파라미터의 편집 정책이다.

```jsonc
{
  // Tool 인자 이름별 편집 허용·값 schema. literal/판단 인자의 사용자 편집 규칙을 정의한다.
  "parameter_controls": {
    // Tool 함수 인자 method의 사용자 편집 허용 및 값 Schema.
    "method": {
      // 사용자가 수정할 수 있는지 표시한다. true여도 value_schema 및 실행 정책 검증을 통과해야 한다.
      "editable": true,
      // 입력·편집값을 검증하는 JSON Schema 객체 또는 boolean. 필수 여부 및 값 유효성은 별도다.
      "value_schema": {
        // 허용하는 JSON 데이터 타입. 이 Schema의 입력값 형식을 제한한다.
        "type": "string",
        // 허용하는 값 목록.
        "enum": [
          "iqr",
          "zscore"
        ]
      }
    }
  }
}
```

대응 arguments.method binding이 필요하다. literal의 editable 기본은 false다. agent_decision은 output_schema를 기본 편집 범위로 사용하고 별도 control을 추가하면 교집합으로 제한한다. workflow_input/step_output/system_context를 Tool 직접 파라미터 편집 대상으로 열지 않는다. 입력 변경은 inputs의 editable을 사용한다.

## 인자 연결 방식

arguments는 함수 인자명→binding 객체다. 함수 signature의 필수 인자를 채우고 허용하지 않는 인자명을 추가하지 않는다. 인자마다 아래 출처 하나만 사용한다.

| source | 추가 필드 | 의미 |
|---|---|---|
| workflow_input | name | 승인된 입력값. data_reference는 검증한 경로로 해석 |
| literal | value | 실제 JSON 상수. eval하지 않음 |
| step_output | step_id, selector | 같은 Execution의 이전 함수 반환 객체 |
| agent_decision | decision_id | 실행 근거를 본 Agent/사용자의 판단값 |
| system_context | key | 신뢰된 user_id/project_id/session_id/dataset_output_dir |

selector=[]는 전체 반환값, ["profile"]은 dict key, [0]은 tuple/list 위치다. 문자열 조각 또는 0 이상 정수만 허용한다. `"df.query(...)"` 같은 표현식을 쓰지 않는다. 객체 자체는 Jupyter 커널에 남으며 Agent에 대용량 DataFrame을 복사하지 않는다. selector의 실제 존재 여부·함수 반환값 의미는 signature 검증만으로 증명할 수 없으므로 실제 실행을 확인해야 한다.

선택 인자에 값이 없으면 함수 호출에서 인자를 생략하여 함수 default를 적용한다. 임의 None을 넣어 원래 default를 바꾸지 않는다. 등록 함수는 import를 포함하고 함수 docstring만 제거한 소스로 승인·제출된다. 실행 중 레포 Tool 파일을 덮어쓰는 방식이 아니다.

## 결과 기반 판단과 조건

```jsonc
{
  // 이 결과 기반 Agent 판단의 고유 문자열 ID. agent_decision binding이 참조한다.
  "id": "outlier_method",
  // Agent가 판단하기 전에 결과를 확보해야 하는 Step ID 목록.
  "after_steps": [
    "profile",
    "statistics"
  ],
  // 결과를 읽고 판단값을 확정하는 Agent용 지침.
  "instruction": "품질과 통계 근거에 맞는 이상치 후보 탐지 방법을 선택한다.",
  // Agent 판단 결과값을 검증하는 JSON Schema. 예를 들어 boolean 또는 선택지 enum을 정의한다.
  "output_schema": {
    // 허용하는 JSON 데이터 타입. 이 Schema의 입력값 형식을 제한한다.
    "type": "string",
    // 허용하는 값 목록.
    "enum": [
      "iqr",
      "zscore",
      "isolation_forest"
    ]
  }
}
```

decisions의 after_steps는 판단 전에 실제 실행 근거가 필요한 Step 목록이다. instruction은 Skill 지침과 함께 판단 기준을 주고 output_schema는 값의 허용 범위를 정한다. Agent가 판단할 수 없으면 decision_review를 열어 사용자에게 확인한다. 빈 값으로 조용히 실행하지 않는다. 실행 결과에 따른 정상 판단과 코드 오류 수정의 repair_level은 다른 정책이다.

조건은 when에 작성한다. 조건이 false인 단계는 SKIPPED로 기록하고 조건을 평가할 수 없는 경우 false로 간주하지 않는다.

```jsonc
{
  // 비교 연산자. eq/ne/gt/gte/lt/lte/in/not_in 중 schema가 허용한 값을 쓴다.
  "op": "eq",
  // 조건 비교의 왼쪽 값 binding.
  "left": {
    // 값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.
    "source": "agent_decision",
    // 결과 기반 Agent 판단의 ID. 판단 정의·확인 화면·Tool 인자의 연결에 사용한다.
    "decision_id": "inspect_outliers"
  },
  // 조건 비교의 오른쪽 값 binding.
  "right": {
    // 값을 얻는 방식. workflow_input/literal/step_output/agent_decision/system_context, 보고서에서는 agent_report를 구분한다.
    "source": "literal",
    // 직접 지정한 값·편집값·판단값. 의미와 허용 타입은 해당 입력 또는 파라미터 schema를 따른다.
    "value": true
  }
}
```

op는 eq/ne/gt/gte/lt/lte/in/not_in이고 all/any/not으로 묶을 수 있다. ordered 비교는 숫자, eq/ne는 같은 타입, in/not_in의 우변은 문자열/list/dict로 검사한다. 전체 DataFrame을 조건값으로 넘기지 않는다.

조건부 Step 출력을 사용하는 consumer/output에는 같은 명시적 when이 필요하다. 다른 조건이 논리적으로 동등한지 추론하지 않는다. 조건부 근거만을 사용하는 후속 decision, 복잡한 분기 합류, 일반 반복 루프는 현재 계약에서 지원하지 않는다. 순환 depends_on으로 반복을 흉내 내지 않는다.

[조건부 전체 예제](contracts/workflow/quality-conditional.json) · [필드 주석](contracts/workflow/quality-conditional.jsonc)는 profile/statistics 결과 후 두 decision으로 이상치 실행 여부·방법을 정한다. 두 decision을 사용자에게 묻는 경우 values에는 둘 다 보내야 한다.

## 실행 정책과 승인

| execution 필드 | 의미 |
|---|---|
| mode | SINGLE 또는 MULTI |
| repair_level | 0~4. 자동 수정 권한 |
| max_repair_attempts | 0 이상, 서비스 상한 적용 |
| review_mode | decision_boundary/every_tool/every_n_tools |
| review_interval_tools | every_n_tools에서만 1 이상 필수 |

Workflow 명시값 → 중앙 config/env에서 해석한 기본값 → 기본 상수 순서로 결정하고, 적법한 HITL override로 최종 승인한다. config > env 우선순위는 유지한다. 0/false는 생략이 아니라 명시 값이다. 없는 mode는 현재 MULTI, repair_level 기본은 중앙 정책(기본 0), review_mode 기본은 decision_boundary다. 시도 기본값은 권한과 명시 여부에 따라 정책에서 확정한다.

SINGLE은 결과 후 decision·조건부 단계·Tool 사이 Agent 검토·repair를 지원하지 않는다. MULTI는 근거 확보 후 다음 Operation과 조건·판단을 이어갈 수 있다. review_mode는 실제 모델 호출 빈도와 지연에 영향을 주며 every_n_tools는 판단 경계가 먼저 오면 그 경계에서 검토한다.

| repair_level | 현재 허용 범위 |
|---|---|
| 0 | 실패 전달, 자동 수정 없음 |
| 1 | 실패 Step의 인자 보정 |
| 2 | 함수 이름·인자·default·annotation을 유지한 실행별 구현 수정 |
| 3 | 성공/skip anchor 유지, 등록 자산으로 남은 계획 재작성·확인 |
| 4 | 원래 목표 내 실행별 함수·남은 계획 수정. 승인 수준 이내 자동 처리 가능 |

서비스 상한 초과는 사용자 승인으로도 허용하지 않는다. 불확실성·정책 상승은 별도 확인한다. 시도 제한은 별도이며 자동 수정 코드가 포함되면 Workflow 대상에서 제외한다. [수정 상세](agentic-execution-repair.md).

화면의 PlanView는 정의에서 만든 projection이다. 입력 기본값과 사용자 편집을 반영하고 승인 때 소스·자산 revision·입력·실행 문맥·정책·hash를 고정한다. 원본 Workflow 정의는 사용자 편집으로 바뀌지 않는다. 재배포 뒤 자산 변경을 기존 승인에 조용히 적용하지 않는다.

현재 후보 수는 MAX_PLAN_CANDIDATES(기본 5)이며 pgvector 추천 풀 연계는 아직 끝나지 않았다. 이 값이 기존 Workflow CRUD의 template 검색이 이미 연결됐다는 뜻은 아니다.

## 산출물과 데이터와 보고서

expected_outputs 각 항목은 id/kind/description/required/source/format이 필수이고 when은 선택이다. kind는 analysis_result/dataset/report, format은 native/parquet/markdown/html/json이다. source는 step_output 또는 agent_report(evidence_steps)다. agent_report는 report+markdown/html 선언만 허용한다.

이는 기대 결과 선언이며 파일 쓰기·Dataset API 등록·Artifact 등록 명령이 아니다. 전처리 데이터를 저장하려면 실제 저장 기능이 있는 등록 Tool이 계획에 포함되어야 한다. 모든 함수 반환값을 자동 Parquet로 저장하지 않는다. system_context.dataset_output_dir도 실제 실행 문맥에 주입된 경로가 있어야 사용할 수 있으며 현재 Dataset Registry 자동 경로 정책 완성으로 보지 않는다.

현재 보고서 Runtime은 Markdown을 작성하여 Run 결과로 제공한다. schema가 html 선언을 허용하지만 HTML 파일 렌더링·Artifact 자동 등록은 아직 구현되지 않았다. 최종 report.artifact_registration은 deferred다. Executor의 기존 POST Artifact notebook Markdown 옵션 연계는 후속이다. 보고서 해석 실패는 이미 끝난 Tool을 다시 실행하는 이유가 아니며 근거만 제공하는 evidence_only로 분리한다.

MinIO는 모두가 공유하는 원천 저장소로 취급한다. 우리가 관리할 전처리 데이터의 USER/PROJECT/SESSION 범위 및 등록/조회는 별도 Dataset 계약이며 Executor 구현을 기다리고 있다. JSON에 특정 파일명·임의 경로를 넣어서 이 권한 경계를 대체하지 않는다.

MULTI는 목표 실행 완료 후 Finalize와 terminal 확인으로 커널을 종료한다. 완료 후 추가 계산은 새 Run·새 Execution·새 커널이며 저장된 데이터/결과를 사용한다. 설명·보고서 편집만이면 새 Executor 실행이 없다. 프로젝트 system_prompt는 Agent 문맥에 적용하고 project_memory 저장·조회·선택적 현재 사용자 원문 추출은 [052 공식 Store 계약](project-memory.md)을 따른다. 실행 결과의 자동 공유·생성형 요약은 후속이다.

## 기존 Workflow CRUD와 1.3 형식

현재 `/api/v1/workflows`는 이전 WorkflowDefinition을 검증한다. [legacy-1.3 구조 예제](contracts/workflow/legacy-1.3.json) · [필드 주석](contracts/workflow/legacy-1.3.jsonc)는 이 모델의 구조 설명용이며 신규 저작용 권장 규격이 아니다. schema_version wrapper가 있으면 document.workflow를 해석하며 raw definition도 받는다. 현재 service는 wrapper 버전을 WorkflowGeneratorOutput으로 엄격히 검증하지 않고 DB schema_version에 보관하므로 임의 버전 값으로 새 모델이 선택되는 것은 아니다.

| API | 현재 동작 |
|---|---|
| POST /workflows | source_run_id/document/tags. 접근 가능한 source Run의 workflow_origin=generated 요구 |
| GET /workflows | q는 이름/설명 부분 문자열 검색. lifecycle/tag와 cursor page 지원. pgvector 아님 |
| GET /workflows/{UUID} | metadata + 저장 JSON document |
| PATCH /workflows/{UUID} | 생성자의 name/description/tags만 수정. document 교체 불가 |
| POST /workflows/{UUID}/promote | 본인 candidate·source Run SUCCESS·기존 definition READY 요구, 새 template UUID 생성 |
| POST /workflows/{UUID}/clone | 새 candidate UUID로 복제 |
| DELETE /workflows/{UUID} | 생성자가 soft delete. JSON 원본은 보존 |

POST body는 `{ "source_run_id": "기존 실행 UUID", "document": "1.3 객체", "tags": [] }`에서 document를 실제 JSON 객체로 바꿔 보낸다. 문자열 파일명/JSON 문자열을 넣는 형식이 아니다. 사용자 쿠키·CSRF를 사용한다. candidate는 생성자만, template은 서비스 사용자에게 공개한다. 등록/승격은 admin 전용이 아니지만 위 기존 guard가 남아 있다.

resource 필드는 workflow_id/name/description/goal/schema_version/lifecycle/file_path/content_sha256/source_run_id/source_workflow_id/created_by_user_id/is_recommendable/tags/document/created_at/updated_at/deleted_at이다. 목록·PATCH는 document=null이고 상세·생성·승격·복제는 document를 읽어 반환한다. file_path는 저장 루트 기준 상대 JSON 파일명이다.

현재 기존 API의 성공 검사 대상은 AgentRun SUCCESS이고 Executor SUCCEEDED를 별도로 조회하는 guard는 아니다. 합의한 신규 규격에서는 현업 직접 JSON 등록·본문 수정·버전 관리·Executor 성공 없이 승격·pgvector 추천을 지원해야 한다. 이 이행은 아직 후순위이며, 새 2.0-draft 정의를 POST하여 자동 변환되는 기능은 없다.

JSON은 WORKFLOW_STORAGE_ROOT에 UUID.json으로 저장하고 DB에는 상대 경로·checksum·metadata·tags를 저장한다. 다중 Pod는 같은 공유 PV를 연결해야 한다. Repo 정적 Tool/Skill 자산과 런타임 Workflow JSON 저장소를 구분한다.

## 검증 방법과 구현 근거

레포 개발 환경에서 다음 명령은 구조·자산·signature를 확인하며 실제 Tool을 실행하지 않는다.

```sh
python scripts/design/validate_workflow_draft.py docs/contracts/workflow/quality-basic.json --catalog docs/design/agentic-workflow-contract/examples/repository-catalog.json --repository-root .
python scripts/design/validate_workflow_draft.py docs/contracts/workflow/quality-conditional.json --catalog docs/design/agentic-workflow-contract/examples/repository-catalog.json --repository-root .
```

신규 runtime validator는 service_contracts.workflow_validation.validate(document,catalog)이며 packaged schema를 읽는다. 설계 도구는 같은 구조의 docs schema와 AST를 함께 확인한다. 두 schema의 검증 부분을 동일하게 유지한다. 구조/schema 통과는 데이터 권한·실제 selector·Tool 정확성·모델 해석·파일 존재·라이브러리 호환·실행 성공을 보장하지 않는다.

- [Workflow schema](../src/service_contracts/resources/workflow-definition.schema.json), [자산·의존성 검증](../src/service_contracts/workflow_validation.py)
- [승인·편집](../src/service_contracts/plan_review.py), [공개 계획 projection](../src/service_contracts/plan_projection.py)
- [계획 생성 Runtime](../src/agent_service/agents/analysis/planning/graph.py), [Executor compiler](../src/agent_service/agents/analysis/execution/compiler.py)
- [기존 CRUD 모델](../src/service_contracts/workflow_definition.py), [기존 CRUD 서비스](../src/api_service/services/workflow_service.py)
- [설계 당시 기록](design/agentic-workflow-contract/README.md), [Dataset 등록·조회 초안](design/dataset-registry-contract/README.md)
