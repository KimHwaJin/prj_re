# Workflow 실행 정의 표준2.0 — 확정

2026-10-05, feature/workflow-standard-contract 구현 계약. 사람과 Agent가 같은 의미의 등록 Skill·Tool Workflow를 작성하기 위한 공개 규격이다. 내부 승인·실행 계획의2.0-draft 및 과거1.3과 별개다. 운영 배포 완료를 의미하지 않는다.

공식 [JSON Schema](contracts/workflow-standard/workflow-standard.schema.json), [static 예제](contracts/workflow-standard/workflow_static.example.json), [adaptive 예제](contracts/workflow-standard/workflow_adaptive.example.json), [필드 주석 예제](contracts/workflow-standard/workflow_adaptive.example.jsonc)를 함께 사용한다. [원본 대비 변경표](review/workflow-standard-changes.md)는 별도 문서다.

확정 범위는 실행 정의2.0과 [등록·다중 쿼리·HNSW 검색 계약](workflow-registration-and-search.md)이다. 094에서 등록 시 쿼리별 임베딩·버전별 활성 색인·고유 Workflow 검색·Agent 추천을 구현했다. 실행 JSON은 그대로 유지하며 실제 embedding 모델 품질/운영 성능은 후속 검증이다.

## 목적과 책임

Workflow는 등록 자산만으로 구성한 재사용 분석 정의다. Python 코드·함수 소스 경로·노트북 변수명·특정 실행 ID·실제 입력값·런타임 DataFrame·실행 상태를 담지 않는다. Tool 코드는 배포된 등록 함수에서 얻고 docstring만 제거하여 승인 시 고정한다. 필요한 import는 기존 계약대로 함수 내부에 포함한다.

| 대상 | 책임 |
|---|---|
| 작성자 | Skill/Tool 조합, 입력 정의, 출력 연결, 판단·조건, 목표 산출물 |
| 등록 담당자 | 함수 ID·Skill 소속·파라미터 정책·출력 별칭과 반환 selector |
| 서비스 | 구조/의미 검증, 입력 확정·편집, 사용자 승인, 소스/규칙/정책 고정 |
| Agent | 자연어 입력 확정, 계획 제안, 선언한 경계에서 실행 결과 해석, 보고서 작성 |
| Executor | 제출받은 함수 코드 실행, 노트북·출력·manifest·이벤트 제공 |

## 최상위 필드

```text
workflow_version = "2.0"
workflow
├─ name, user_request, description?
├─ inputs? {name: definition}
├─ steps[] {id, skill, tools[]}
├─ decisions[]?
├─ execution?
└─ expected_outputs[]
```

`?`는 생략 가능이다. 알 수 없는 필드는 거절한다. ID는 소문자 영문으로 시작하고 영문 소문자·숫자·`.`·`_`·`-`를 사용하는150자 이내 문자열이다. 대소문자·별칭을 임의 교정하지 않는다.

| 필드 | 필수 | 의미 |
|---|---|---|
| workflow_version | O | 공개 포맷 버전2.0 |
| workflow.name | O | 1~200자 이름 |
| workflow.user_request | O | 재사용 분석 목표 |
| workflow.description | X | 설명. 생략하면 user_request 사용 |
| workflow.inputs | X | 명명된 입력 정의. 생략하면 빈 객체 |
| workflow.steps | O | 최소1개 Skill 그룹 |
| workflow.decisions | X | 결과 기반 Agent 판단. 생략하면 빈 배열 |
| workflow.execution | X | 실행 정책의 명시적 요구. 생략값은 중앙 정책 사용 |
| workflow.expected_outputs | O | 최소1개 기대 산출물 선언 |

actor는 공개 정의에 없다. 인증된 작성자/등록자와 선택적 원본 Run은 CRUD resource가 관리한다. workflow_version, DB workflow_id, content_sha256, plan_revision, run_id, interaction revision은 서로 다른 값이다.

## 입력 정의와 사용자 편집

`inputs`의 키가 입력 이름이며 source=input/name으로 참조한다. 두 호출에서 같은 입력을 쓰려면 같은 name을 명시한다. 같은 함수 인자명이란 이유로 자동 공유하지 않는다.

| 입력 필드 | 필수/기본값 | 의미 |
|---|---|---|
| value_schema | 필수 | JSON Schema object 또는 boolean |
| title | 입력 이름 | 화면 표시명 |
| description | 입력 이름 | 설명 |
| kind | parameter | parameter 또는 data_reference |
| required | true | 최종 승인 때 값 존재 여부 |
| editable | true | 입력 화면에서 수정 가능 여부 |
| default | 없음 | 재사용 기본값. value_schema를 만족해야 함 |

value_schema는 원격 `$ref`/`$dynamicRef`를 포함하지 않는다. 타입·선택지·범위는 이 Schema로 선언한다. 실제 Tool에 별도 파라미터 Schema가 있으면 그 제한도 함께 적용한다. signature/type hint/docstring은 메타데이터와 안내이며 모든 Python 타입의 실행 호환을 보장하지 않는다.

실제 값은 실행 계획 `input_values`에 보관한다. 사용자 요청에서 추출한 값은 기본으로 보여주고, 미정 필수값은 빈칸으로 표시한다. 승인 전에 사용자가 수정할 수 있다. `null`은 공급된 값이고 미정/누락이 아니다. data_reference는 사용자에게 허용된 공개 데이터 ID이며 실제 PVC 경로는 서버가 해결·고정한다.

Tool literal 편집은 등록 parameter_controls에 허용된 범위에서 가능하다. 참조 DataFrame·서버 경로를 literal 편집기로 바꾸지 않는다. 필요한 생산 호출을 제외하면 오류를 표시한다. 독립 호출 제외는 허용한다. 저장된 원본 정의는 이번 편집으로 바뀌지 않는다.

## Skill 그룹과 Tool 호출

| 위치/필드 | 필수 | 의미 |
|---|---|---|
| steps[].id | O | Skill 그룹 ID. 그룹 내 순서 기준이 아님 |
| steps[].skill | O | 등록 Skill ID |
| steps[].tools | O | 최소1개 Tool 호출 |
| tools[].id | O | Workflow 전체에서 고유한 호출 ID |
| tools[].tool | O | 해당 Skill에 속한 등록 Tool ID |
| tools[].description | X | 호출 설명. 생략하면 Tool ID |
| tools[].arguments | X | 함수 파라미터 이름별 binding. 생략하면 빈 객체 |
| tools[].when | X | 조건. 없으면 실행, false면 생략 |
| tools[].parameter_controls | X | 인자별 editable/value_schema. 등록 범위를 좁힐 수 있음 |

같은 Tool을 여러 번 사용해도 호출 ID는 다르다. 그룹 ID는 그룹끼리 고유하고, 호출/판단/산출물 ID는 상호 충돌하지 않는다. 그룹과 호출 배열을 펼친 순서대로 실행한다. 공개 규격에는 병렬 DAG·루프·재귀 호출을 선언하지 않는다. 코드 오류 수정의 시도 반복은 execution 정책에 따른 별도 동작이다.

## 인자 값 출처

| source | 필요한 필드 | 의미 |
|---|---|---|
| literal | value | 실제 JSON 값 |
| input | name | inputs에 정의한 실행 입력 |
| tool_output | call_id, output | 선행 호출의 등록된 출력 별칭 |
| agent_decision | decision_id | decisions에 선언한 결과 기반 판단값 |
| system_context | key | user_id/project_id/session_id/dataset_output_dir 중 서버 제공값 |

툴 출력은 같은 그룹 또는 앞 그룹의 선행 호출만 참조한다. 이미 사용한 Skill/Tool 이름으로 특정 데이터 로드 동작을 자동 삽입하지 않는다. 내부 정규화에서 실제 데이터·판단 의존성과 순서 장벽을 분리한다. 조건부 호출이 생략되어도 그 출력을 요구하지 않는 다음 독립 호출은 계속한다.

선택 인자는 생략하면 함수 원래 기본값을 적용한다. 기본값 없는 필수 인자는 명시해야 한다. 편집 정책에서 허용하는 기본값은 승인 계획에 구체화되며 최종 제출값을 snapshot에 고정한다.

### Tool 출력 등록 계약

tool_registry.yaml의 outputs는 출력 별칭→selector map이다.

```json
{"outputs": {
  "data": {"selector": []},
  "metric": {"selector": ["summary", "metric"]}
}}
```

`[]`는 전체 반환값, 문자열은 dict key,0 이상 정수는 list/tuple index다. Python 식·eval·query 코드를 selector로 사용하지 않는다. 명시한 outputs는 생성기가 보존한다. 없는 경우 실제 함수 AST의 반환 key 힌트(또는 전체 result)를 변환하되, 이는 모든 실행 경로의 실제 반환 형태를 증명하지 않는다. 복잡한 동적 반환은 명시 등록과 실제 실행 테스트가 필요하다.

대용량 객체는 Jupyter 커널에 두고 다음 함수에 연결한다. Agent에는 실행 manifest와 제한된 관찰·텍스트 근거를 제공한다. DataFrame 전체를 JSON이나 프롬프트로 전달하지 않는다.

## 조건과 Agent 판단

when은 `op + left + right`, `all[]`, `any[]`, `not` 중 하나다. 값 출처는 위 binding을 사용한다. op는 eq/ne/gt/gte/lt/lte/in/not_in이다. gt 계열은 숫자만 비교한다. eq/ne는 타입이 같은 값을 비교한다. in/not_in은 오른쪽 문자열·배열·객체에 포함되는지를 검사한다.

기계 비교는 모델 없이 평가한다. 의미 해석은 Agent 판단 항목으로 선언하고, boolean 판단값을 when에서 비교할 수 있다.

| decisions[].필드 | 필수 | 의미 |
|---|---|---|
| id | O | 고유 판단 ID |
| after_calls | O | 최소1개 성공한 선행 호출의 ID |
| instruction | O | 결과에 대한 판단 지침 |
| output_schema | O | 판단 결과 허용 값 Schema |

판단 소비 호출보다 근거 호출이 먼저 있어야 한다. 현재 규격은 조건부 호출을 판단의 필수 근거로 쓰는 분기를 거절한다. 필요한 분기 대체 계약 없이 Agent가 근거를 만들어내지 않는다.

Skill Markdown은 업무 지침을 유지하며 승인 시 고정·제공한다. instruction은 이번 Workflow 판단의 초점을 설명한다. 자연어 업무 규칙의 완전한 일치를 기계 검증했다고 주장하지 않는다. Agent는 실제 확보한 근거와 허용 값 안에서 판단하고, 판단 불가/값 검증 실패 시 decision_review HITL로 확인한다.

조건부 출력은 생산자와 소비자에 같은 명시 when을 요구한다. 별도 fallback/coalesce는 이 버전에서 지원하지 않는다. 누락된 출력을 임의 null로 채우지 않는다. 동일 guard로 연결한 소비자는 생산자 생략 시 함께 생략한다.

static/adaptive는 작성 필드가 아니라 서비스 계산 분류다. when 또는 decisions가 있으면 adaptive, 모두 없으면 static이다. 모든 호출이 실행되어도 결과 기반 파라미터 판단이 있으면 adaptive다. JSON에 execution_mode나 execution=conditional을 넣으면 거절한다.

## 실행 정책과 승인

execution에는 mode, repair_level, max_repair_attempts, review_mode, review_interval_tools를 선택적으로 선언한다.

| 필드 | 허용 값/기본 | 의미 |
|---|---|---|
| mode | SINGLE/MULTI. 생략 시 현재 MULTI | Executor 실행 요구 |
| repair_level | 0~4. 중앙 정책 | 오류 수정 권한 |
| max_repair_attempts | 0 이상. 중앙 정책과 권한 | 수정 시도 상한 |
| review_mode | decision_boundary 기본, every_tool/every_n_tools | Agent 결과 검토 경계 |
| review_interval_tools | every_n_tools에서 필수,1 이상 | 검토 간격 |

Workflow 명시값→중앙 정책(config 우선, env 다음)→기본값으로 해석한다. 유효한 HITL 변경은 서비스 상한 이내에서 적용한다. 0/false를 누락으로 취급하지 않는다.

SINGLE은 결과 후 판단·조건·Tool 사이 검토·오류 수정을 지원하지 않는다. 해당 동작이 필요하면 MULTI다. static/adaptive를 SINGLE/MULTI로 일대일 매핑하지 않는다. 생략된 정책을 선택하는 자동 최적화는 이번 작업에 포함하지 않는다.

등록 Workflow는 최종 사용자 승인 뒤 제출한다. allow_immediate_execution은 이 공개 규격에서 제거했다. 이전 자유 코드 계획의 별도 서비스 정책과 혼동하지 않는다. 실행 전에 입력·수정한 계획·소스·Skill 지침·정책·자산 revision·hash를 고정한다. 재배포 자산을 승인된 실행에 조용히 섞지 않는다.

MULTI 장기 실행은 Executor에서 수행하며 이벤트로 재개한다. 실행 결과를 기다리는 동안 Agent 실행 슬롯을 계속 차지하지 않는다. 현재 목표 완료 후 Finalize와 terminal을 확인하고 보고서를 작성한다. 추가 계산은 새 Execution이며 기존 커널을 무기한 유지하지 않는다.

## 기대 산출물

각 항목은 id, kind, description, required, source, format이 필수이고 when은 선택이다. kind는 analysis_result/dataset/report, format은 native/parquet/markdown/html/json이다. source는 등록 tool_output 또는 agent_report/evidence_calls다. agent_report는 report kind와 markdown/html만 허용한다.

expected_outputs는 기대사항 선언이다. 파일 저장·등록을 수행할 등록 Tool/외부 API 없이 선언만으로 데이터 파일을 생성하지 않는다. 보고서의 실제 기본 출력은 Markdown이며 HTML renderer, Dataset Registry, POST Artifact 자동 등록은 후속이다. required와 선언이 실행 결과의 존재를 자동 증명하지 않으므로 완료 검사/보고서의 실제 근거를 함께 확인한다.

## 등록·관리 API

현재 서비스의 SSO session cookie와 CSRF를 사용한다. 인증 상세는 [SSO 문서](sso-authentication.md)를 따른다. 본문에 actor를 넣어 권한을 선택하지 않는다.

| API | 요청/동작 |
|---|---|
| POST /api/v1/workflows | `{user_queries: 문자열 배열, document: 공개2.0 JSON객체, tags?:[], source_run_id?:UUID}`. Run 없이 직접 등록 가능. candidate 반환 |
| GET /api/v1/workflows | 접근 가능한 candidate와 공개 template 목록. 기존 q/lifecycle/tag·pagination 유지 |
| GET /api/v1/workflows/{UUID} | metadata + 공개 document + content_sha256 + user_queries/resource_revision/search_revision/index_state |
| PATCH /api/v1/workflows/{UUID} | 쿼리 수정은 expected_resource_revision 필수. JSON 내용은 이 토큰 또는 기존 SHA로 충돌 검사. 색인 결과 포함 |
| POST /api/v1/workflows/{UUID}/promote | 본인 candidate를 새 template UUID로 승격.2.0은 Executor/Run 실행 성공을 요구하지 않음 |
| POST /api/v1/workflows/{UUID}/clone | 새 candidate. name/tags 선택. 원본 ID 연결 |
| DELETE /api/v1/workflows/{UUID} | 작성자 soft delete. 이전 파일 보존 |

candidate 수정·승격·삭제는 작성자가 수행한다. template은 서비스 사용자에게 공개된다. supplied source_run_id는 본인 소유 Run만 허용하며 권한 부여가 아니라 출처다. Run 없는 직접 POST는 매번 새 candidate를 만든다. source_run_id가 있는 root candidate는 기존 동일 Run 유일성 규칙을 유지한다.

본문 수정은 새 파일·새 content_sha256을 만들고 DB가 최신 파일을 가리킨다. 기존 파일은 이전 승인/로그의 근거로 보존한다. 같은 JSON 내용의 SHA는 같을 수 있으므로 SHA는 수정 횟수가 아니라 내용 지문이다. 임베딩은 내용 변경 시 기존 항목을 superseded/inactive로 전환하고 새 search_revision에 대해 DB 연결 밖에서 재색인하고 게시 전 버전을 다시 검사한다.

DB resource UUID가 재사용 자산 ID다. 공개2.0 파일은 `{workflow_id}.{content_sha256}.json` 경로로 저장하여 특정 내용의 이전 revision을 찾을 수 있다. 경로는 서버가 관리한다. 공개 JSON에 resource UUID를 강제로 복사하지 않는다. normalize(workflow_id=..., definition_version=...)를 사용하는 caller가 내부 identity를 지정할 수 있다. 지정하지 않으면 정의 내용 지문으로 내부 ID를 만든다.

원본1.0은 자동 수용하지 않는다. 인덱스·입력·조건·출력 정보를 명시적으로 이행해야 한다. 기존1.3 CRUD compatibility는 보존하며 과거 성공/READY guard를 유지한다. 신규 작성 기준은 이2.0 문서다.

검색/reindex API와 상태·예산·배포는 [등록·검색 확정 문서](workflow-registration-and-search.md)를 따른다.

## 검증과 현재 지원 경계

1. JSON Schema 구조·버전 검증.
2. Skill/Tool 등록·소속·함수 인자·출력 이름·선행 참조 검증.
3. 입력/판단 Schema·조건부 소비·실행 정책 검증.
4. 공통 승인 UI에서 실제 입력·데이터 접근·제외/편집·서비스 상한 검증.
5. 승인 snapshot으로 Executor 제출·이벤트 재개·Finalize·terminal/report.

구조 검증은 함수 정확성·런타임 selector 존재·파일 존재·업무 해석·커널 라이브러리 호환을 보장하지 않는다. 실제 의미와 실행 테스트가 필요하다. Skill/Tool을 특정 예제 이름에 맞추는 분기는 없다.

이번 구현에는 표준 Schema/정규화/출력 계약/순차 실행/승인 연결과 직접 등록·본문 수정·승격이 포함된다. pgvector 검색과 등록 template의 실제 대화 추천 자동 연계, Agent 내부 계획을 공개 JSON으로 역변환하는 exporter, 기존1.3/받은1.0 일괄 마이그레이션, HTML·Artifact·Dataset 외부 API 연계는 후속이다. 이 기능들을 구현 완료로 간주하지 않는다.

검증 및 배포 여부는 [092 작업 기록](improvements/092-workflow-standard-contract.md)을 확인한다.

반환 dict key가 공개 출력 ID 규칙에 맞지 않으면 자동 별칭으로 사용하지 않는다. 사용 가능한 자동 별칭이 없으면 result를 전체 반환값으로 제공하며, 예를 들어 `ROC AUC` key는 `auc: {selector: ["ROC AUC"]}`처럼 명시 등록한다. 실제 반환 key를 조용히 개명하거나 그 이유로 함수 등록을 실패시키지 않는다.

이미 저장한 동일 내용으로 되돌아갈 때는 그 내용의 파일을 재사용한다. DB commit이 실패해도 현재 파일이나 재사용한 과거 파일을 삭제하지 않는다. 내용 SHA는 변경 횟수가 아니며 같은 내용으로 되돌리면 같아진다. 별도 revision 목록 API는 현재 제공하지 않는다.

함수 인자명은 Workflow ID 규칙과 다르다. 실제 Python signature를 따르며 X, _Factor 같은 이름도 허용한다. 식별자가 아닌 이름이나 Python 예약어는 승인 전에 거절한다. 인자 출처·편집 정책은 해당 실제 이름으로 등록한다.
