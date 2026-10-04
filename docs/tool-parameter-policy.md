# Tool 사용자 파라미터 작성 가이드

087에서 적용한 현재 코드 기준 규칙이다. 함수는 docstring만 제거하여 Executor에 제출하며, 함수 본문·import·signature를 바꾸지 않는다. 이 정책은 사용자가 확인할 인자와 값의 범위를 선언한다. 실제 컬럼 존재 여부·커널의 라이브러리 가용성까지 검증하는 데이터 스키마 조회 기능은 아니다.

## 어디에 작성하는가

`src/agent_service/agents/analysis/workflow/tools/tool_registry.yaml`의 Tool 항목에 `parameter_controls`를 작성한다. 함수의 signature/docstring·기본값은 AST로 읽고 실행하거나 import하지 않는다. 표시 제목·설명·편집 허용·JSON Schema는 개발자가 선언한다. 생성기는 이 수동 정책과 availability를 보존하며 잘못된 정책을 거절한다.

```yaml
compute_statistics:
  # 기존 source/function_name/signature/inputs 등을 유지한다.
  parameter_controls:
    columns:
      title: 분석할 컬럼
      description: 컬럼명 배열. null이면 Tool이 전체 대상 컬럼을 선택한다.
      editable: true
      value_schema:
        type: [array, 'null']
        items: {type: string, minLength: 1}
        minItems: 1
        uniqueItems: true
```

`parameter_controls` 안의 key는 실제 함수의 인자 이름이다. 각 항목의 title/description/editable/value_schema가 필수다. Python **kwargs를 이유로 임의 이름을 선언하지 않는다. 여기에는 default 필드를 수동으로 넣지 않는다. 서버가 함수의 실제 기본값에서 읽으며, 호출·변수 참조·비 JSON 기본값은 사용자 폼에 자동 노출할 수 없다. false·0·null·빈 문자열은 미입력과 다르다. Python tuple의 literal 기본값은 JSON 배열로 고정한다.

`parameter_controls: {}`는 해당 Tool에 직접 편집할 인자가 없다는 뜻이다. 선언한 Tool에서는 목록 밖 인자를 Workflow가 editable=true로 새로 허용할 수 없다. 기존 정책이 없는 레거시 Tool·실행별 custom 함수는 이전처럼 Workflow의 명시적 편집 규칙을 따른다. 새 Tool은 빈 매핑 또는 필요한 사용자 인자를 명시적으로 선언한다.

현재 `compute_statistics.columns`, `detect_outliers.method/columns`를 등록했다. `data_load`와 `profile_data`는 빈 매핑으로 경로·데이터 객체의 상수 편집을 금지했다. `merge_data` 등 정책을 아직 선언하지 않은 기존 Tool은 Workflow의 편집 규칙을 유지한다.

## Workflow와 계획에서의 적용

| binding | 사용자 조작 |
|---|---|
| literal | 등록 정책과 Workflow가 허용하는 값만 직접 수정 |
| agent_decision | 결과 기반 판단으로 유지. 사용자가 직접 확정하면 literal로 변경 |
| workflow_input | Step 참조 자체는 읽기 전용. 상단 inputs의 editable 필드를 통해 수정 |
| step_output | 앞 단계가 만드는 데이터 객체·출력 연결이므로 읽기 전용 |
| system_context | 사용자·프로젝트·세션 등 시스템 문맥이므로 읽기 전용 |

등록한 선택 인자가 arguments에서 생략되면 실제 JSON 기본값으로 보충하고 승인 snapshot에 고정한다. `compute_statistics.columns=None`은 `columns=null`로 보이며, null은 전체 기본 대상 선택이라는 실제 값이다. 명시한 Agent 값·Workflow input·결과 기반 decision은 기본값으로 덮어쓰지 않는다. 선언하지 않은 Python 인자나 데이터 객체 기본값은 자동 보충하지 않는다.

Workflow `parameter_controls`의 editable=false는 해당 인자를 잠근다. value_schema는 등록 schema와 교집합으로 적용된다. Workflow가 더 넓은 schema를 주더라도 등록 범위를 벗어나지 못한다. literal·상단 input_values·사용자가 바꾸는 step_changes를 검사하며, 결과 기반 판단의 output_schema도 등록 범위와 함께 고정하므로 실행 시 Agent 판단이 범위를 우회하지 못한다.

함수에 Python 기본값이 있어도 분석상 꼭 필요한 값은 Agent가 required=true인 Workflow input으로 명시해야 한다. 값을 모르면 빈 입력으로 남기고 사용자에게 확인한다. 예: 병합 기준 컬럼을 모를 때 임의의 컬럼을 기본값으로 만들지 않는다. 필수 Python 인자의 binding을 누락한 계획은 기존대로 거절한다.

## PlanView와 프론트

Tool parameter에 기존 name/kind/editable/value_schema와 함께 title/description/has_value/origin을 제공한다. title/description은 등록 정책이 있는 경우 표시한다.

- `has_value=true`: literal 값이 존재함. `value=null`도 정해진 값이다.
- `has_value=false`: 결과 기반 결정 또는 입력·출력·문맥 참조. workflow_input의 실제 값은 상단 inputs에서 확인한다.
- `origin=tool_default`: 함수에서 읽어 보충한 기본값.
- `origin=agent`: 계획에 명시된 literal 값. 현재 계획 생성 주체는 Agent다.
- `origin=user`: 사용자가 직접 수정한 값.
- `origin=unresolved`: 결과 기반 판단 또는 직접 값을 갖지 않는 참조.

편집 POST 계약은 그대로다. `step_changes=[{step_id, parameter, value}]`에 수정값을 넣고 최신 plan_revision/resume_token을 사용한다. 실패422는 token을 소비하지 않는다. 성공 편집은 변경이 있으면 revision을 증가시키며 별도 LLM 호출 없이 checkpoint에 저장한다. 승인 결과와 Executor 소스에는 최종값을 반영한다.

HTML 폼의 빈칸은 변경값을 보내지 않는다는 뜻이므로 기존값을 지우지 않는다. 배열은 JSON으로 입력하며 전체 기본 대상으로 되돌리려면 `null`을 보낸다. 미확정 필수값을 빈칸으로 승인하면 API가 거절한다. 함수 코드를 사용자 화면에 표시하지 않는다.

## 재배포와 이행

등록 정책·기본값을 자산 revision에 포함한다. 배포 중 정책이 변경되면 기존 대기 계획을 새 정책으로 조용히 해석하지 않고 기존 자산 변경 보호가 작동한다. 승인 대기 Run은 정책 배포 전 완료시키거나 새 계획을 받는 이행이 필요하다. 실제 운영의 drain/버전별 실행 정책 검증은 별도 후속이다.

DB migration·새 환경변수·추가 모델 호출은 없다. 이미 승인된 snapshot/Executor payload를 이 작업으로 다시 생성하지 않는다.
