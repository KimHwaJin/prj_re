# Skill·Tool 자산과 공통 Agent의 경계

091 기준 **현재 2.0 Agent 실행 경로**의 규칙이다. 저장소에 있는 Skill·Tool은 예시 자산이며 공통 Agent의 분기 조건이 아니다. Tool 교체·추가·삭제 시 업무 설명과 등록 정보를 변경하고 재배포한다. 특정 함수명·인자명·반환 키에 맞춰 graph·공통 prompt·validator를 수정하지 않는다.

## 책임

| 위치 | 책임 |
|---|---|
| workflow/skills의 Markdown | 언제 사용하고 사용하지 않는지, Tool 조합·선행 조건·결과 기반 판단 지침 |
| workflow/tools의 Python 함수 | 실제 구현, 필요한 함수 내부 import, signature·기본값·docstring·반환 구조 설명 |
| tool_registry.yaml | source/function_name과 AST 생성 메타데이터, 유지보수 담당자가 정하는 availability·입력 연결·사용자 편집 정책 |
| planning/catalog.py | AST로 함수 정보 읽기, 메타데이터 탐색, docstring만 제거한 원문 고정. 함수 import·실행 없음 |
| 공통 Agent·dtest/contracts | 등록 여부, 인자·참조·의존성·승인·정책 검증. 업무 의미는 Skill/docstring에서 읽음 |
| execution/compiler.py·Executor | 승인된 값과 참조 연결로 함수 코드 제출·실행, 관찰과 manifest 수집 |

Tool은 LLM tool calling으로 실행하지 않는다. LLM이 사용하는 read_skill/search_tools는 메타데이터 조회다. 코드 수정 허용 단계에서의 실행별 함수도 기존 승인·수정 정책을 따른다.

## 한 파일의 여러 함수와 등록 ID

공개된 최상위 `def`마다 등록한다. 파일명과 함수명이 같을 필요가 없다. 같은 Python 파일에 여러 Tool을 둘 수 있고 폴더를 나눠 유지보수할 수 있다. 자동 등록하지 않을 보조 함수는 `_`로 시작한다. 제출 함수는 자기 안에 필요한 import를 포함해야 한다. 모듈 전역 import·상수·형제 함수가 자동으로 제출되는 구조가 아니다.

현재 제출 계약은 일반 keyword 호출 가능한 동기 함수다. 데코레이터·async 함수·positional-only·`*args`는 등록 생성 시 거절한다. 명시적인 인자와 keyword-only 인자를 지원하며 기존 `**kwargs` 지원은 유지한다. `**kwargs`에 임의의 인자 정책을 선언하지 않는다. 이는 Agent의 비동기 모델/HTTP 호출과 별개인 Jupyter 제출 함수 규격이다.

처음 등록 시 ID는 함수명이다. 기존 `(source, function_name)` 쌍에 부여한 Tool ID는 생성기가 보존한다. 서로 다른 파일에 같은 함수명이 있으면 고유 ID를 registry에 명시해야 하며 중복을 자동 덮어쓰지 않는다. 함수나 파일을 이름 변경할 때 기존 registry의 source/function_name도 함께 갱신하면 ID를 유지할 수 있다. 삭제 후 재생성하면 해당 항목도 사라진다. Skill index에 사라진 Tool ID가 남아 있으면 기동 검증이 실패하므로 함께 갱신한다. `test_only` 항목은 index에 있어도 실제 후보에서 제외한다.

## 기계적으로 검사할 연결 정책

docstring의 업무 설명은 LLM이 읽는다. 반드시 지켜야 할 입력 출처는 **등록 담당자가** `parameter_bindings`에 선언한다. 자연어와 함수명으로 안전 정책을 추측하지 않는다. 모든 함수에 거대한 별도 schema를 쓰는 방식이 아니며, 제한이 필요한 인자에만 아래 정책을 붙인다.

```yaml
read_source:
  source: data_io/functions.py
  function_name: read_source
  parameter_bindings:
    source_file:                     # 실제 함수 인자 이름
      allowed_sources: [workflow_input] # literal 경로로 변경할 수 없음
      input_kind: data_reference     # 참조 Workflow input의 kind
      required: true                 # 기본값이 있어도 명시적 binding 필요
  parameter_controls: {}            # 상수 편집 허용 인자 없음

summarize_records:
  source: analysis/functions.py
  function_name: summarize_records
  parameter_bindings:
    records:
      allowed_sources: [step_output] # 이전 결과 객체를 받음
      required: true
  parameter_controls:
    factor:
      title: 적용 배율
      description: 최종 승인할 계산 배율
      editable: true
      value_schema: {type: integer, minimum: 1, maximum: 10}
```

예제 이름은 규격 설명용이며 공통 코드에서 검사하지 않는다. 정책 key는 실제 함수 인자여야 하고, 미등록 인자·알 수 없는 정책 필드·중복 출처·문자열 형태의 required는 거절한다.

| 필드 | 의미 |
|---|---|
| allowed_sources | literal, workflow_input, step_output, agent_decision, system_context 중 허용 출처 목록. 하나 이상·중복 없음 |
| input_kind | 선택 필드. parameter 또는 data_reference. 지정 시 출처는 workflow_input 하나만 허용 |
| required | 기본 false. true이면 인자 binding 누락 거절. Workflow input 참조라면 최종 승인 시 실제 값도 있어야 함 |

`required=true`여도 계획을 만드는 동안 입력값은 미정으로 둘 수 있다. Workflow input의 required가 false여도 Tool 정책이 실제 값을 요구하면 최종 승인은 거절한다. Python 자체의 필수 인자도 최종 승인 시 참조 입력값이 있어야 한다. 사용자 제외 Step의 Tool 값 요구는 적용하지 않는다. 선택적 참조와 실제 선택 인자는 미입력을 유지할 수 있으며 복구에서도 누락값을 경로나 null로 만들어내지 않는다.

정책 없는 인자의 기존 호출 규칙은 유지한다. Python 필수 인자·Workflow 참조/의존성 검증은 항상 적용된다. `parameter_bindings: {}`는 추가 출처 제한이 없다는 뜻이다. 새 로드 Tool에서 경로 보호를 원하면 해당 인자를 data_reference 정책으로 선언해야 한다. 등록하지 않은 정책을 이름에서 자동 추론하지 않는다.

`parameter_controls`는 [사용자 파라미터 가이드](../tool-parameter-policy.md)를 따른다. 사용자가 직접 값을 확정하면 literal binding이 되므로 editable=true인 인자의 연결 정책은 literal을 허용해야 한다. 둘이 모순되면 기동/생성 단계에서 거절한다. workflow_input 편집은 상단 inputs에서 하며 Step.parameter_controls를 중복 선언하지 않는다. 잘못된 계획의 피드백은 실제 Step/필드/출처와 수정 위치를 알려준다.

선택 인자의 실제 기본값은 AST에서 읽는다. 참조만 허용하는 인자를 literal 기본값으로 자동 채우지 않는다. 사용자 편집·최종 승인·수정 계획·복구가 같은 연결 정책을 적용한다. 등록 함수에서 파생된 custom 별칭은 origin 정책을 상속하며 이름 변경으로 출처/편집 제한이 없어지지 않는다. 원본 없는 완전자율 코드는 별도 허용 수준이며 이 정책을 Python sandbox로 해석하지 않는다.

## 반환 구조와 탐색

read_skill/search_tools는 함수의 실제 signature/docstring과 returns를 제공한다. returns는 AST에서 얻은 반환 annotation과 사전 반환 key의 힌트다. 동적 key·중첩 구조·조건별 반환·전체 데이터 schema를 증명하는 규격이 아니다. Tool 개발자는 docstring에 반환 객체 형태·중요한 key와 출력의 의미·한계를 설명해야 한다. Agent는 이 설명으로 step_output.selector를 정하며 실제 반환값은 Executor 관찰로 확인한다.

기존 DataFrame, JSON 객체, 배열 등의 출력 관찰 처리는 출력 타입 기준이다. 특정 Tool에서 어떤 key가 반드시 나올 것으로 공통 Agent가 가정하지 않는다. 실제 컬럼 존재 여부·Tool 의미의 정확성·Python 코드 보안 격리까지 정적 검증이 보장하는 것은 아니다.

Skill Markdown 파일명이나 폴더 깊이는 탐색 로직의 업무 분기 조건이 아니다. 기존 구조화된 Markdown 생성 규격과 고유 Skill ID를 유지하며 중첩 디렉터리를 지원한다. Skill 목록에서 필요한 문서를 읽고 관련 Tool만 조회한다. search_tools는 이름/docstring의 단어 검색과 최대 15개 결과를 사용하며 vector 서비스가 필요 없다. 18 Skill·100 Tool 등록/조회 회귀도 추가했다. 이는 100 Tool의 실제 모델 품질이나 부하 성능 평가가 아니다.

## 재배포와 버전

생성기는 파일을 import하지 않으며 수동 parameter_controls/parameter_bindings/availability를 보존·검증한다. 새 자산은 registry/index를 생성·검토하고 재배포한다. 프로세스 내 자산 snapshot은 기동 시 읽으며 자동 hot reload하지 않는다.

```sh
python src/dtest/agent_service/agents/analysis/workflow/skills/generate_skill_index.py
python src/dtest/agent_service/agents/analysis/workflow/tools/generate_tool_registry.py
PYTHONPATH=src python -m pytest tests/agent_service/test_asset_independence.py -q
```

source·Skill 문서뿐 아니라 전체 유효 메타데이터/소속/정책을 asset revision에 반영한다. 정책·소속만 바뀐 배포도 변경으로 감지한다. 이미 승인된 함수 코드는 snapshot에 유지한다. 실행 중 복구는 다른 asset revision을 섞지 않으므로 자산 변경 배포와 기존 Run 이행은 별도 검토해야 한다. 심볼릭 링크로 연결한 자산 루트도 동일하게 정규화한다.

## 범위와 남은 작업

현재 2.0의 계획·승인·Executor 실행 경로를 대상으로 한다. 102에서 미사용 기존1.3 관리·컴파일 코드와 특정 데이터 로드 가정·중복 schema/catalog를 삭제했다. 등록 자산은 workflow 패키지에 유지하고 현재 catalog/compiler만 사용한다.

공개 Run/HITL/SSE·Executor 제출 JSON, DB schema, 설정은 변경하지 않았다. MinIO 정책·미구현 Dataset Registry·Artifact 등록·Workflow 검색을 추가한 작업도 아니다. 실제 LLM의 계약 준수, 출력 의미의 정확성과 다양한 실무 자산의 조합 품질은 계속 평가해야 한다.

## 092 공개 Workflow 출력 별칭 계약

[확정 Workflow2.0](../workflow-standard.md)의 tool_output.output은 등록 outputs 별칭을 참조한다. tool_registry.yaml에 `outputs: {data: {selector: []}, metric: {selector: [summary, metric]}}`를 선언할 수 있다. 생성기는 이 수동 매핑을 보존하고 기동 시 검증한다. 없으면 실제 AST 반환 key 힌트/전체 result를 안전한 selector 배열로 변환한다. Tool 이름으로 반환 구조를 추측하지 않는다. 자동 힌트는 실행 경로의 실제 shape를 증명하지 않으므로 동적 반환은 명시 매핑과 실행 시험이 필요하다. 함수 본문·docstring 작성 계약은 그대로다. 출력 매핑도 catalog revision에 포함되어 승인 후 배포 변경을 섞지 않는다.

반환 dict key가 공개 출력 ID 규칙에 맞지 않으면 자동 별칭으로 사용하지 않는다. 사용 가능한 자동 별칭이 없으면 result를 전체 반환값으로 제공하며, 예를 들어 `ROC AUC` key는 `auc: {selector: ["ROC AUC"]}`처럼 명시 등록한다. 실제 반환 key를 조용히 개명하거나 그 이유로 함수 등록을 실패시키지 않는다.
