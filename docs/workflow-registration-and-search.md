# Workflow 등록·검색 확정 계약

2026-10-05 / 094 / feature/workflow-hnsw-retrieval. 실행 정의는 [공개 Workflow 2.0](workflow-standard.md), 변경 추적은 [W01~W17·R01~R06](review/workflow-standard-changes.md)이다. 이 문서는 등록 envelope, 쿼리 버전, 색인 및 HNSW 검색의 구현 계약이다. 실제 embedding 모델 품질과 운영 환경 성능까지 확정한 문서는 아니다.

## 호출과 권한

기존 SSO 로그인 쿠키와 변경 요청의 CSRF 헤더를 그대로 사용한다. 헤더·로그인 방법은 [SSO 가이드](sso-authentication.md)를 따른다. 후보 candidate는 작성자만 조회·수정·색인·승격·삭제한다. 승격한 template은 로그인 사용자 누구나 조회·검색할 수 있으며 수정·삭제·색인은 작성자만 한다. 검색 결과에는 candidate·삭제된 자산·실패/이전 버전·다른 embedding 모델 공간이 섞이지 않는다.

| API | 요청 | 응답 |
|---|---|---|
| POST /api/v1/workflows | user_queries 필수, document 필수, tags/source_run_id 선택 | 201 WorkflowResource: candidate 등록 및 비활성 벡터 색인 결과 |
| GET /api/v1/workflows | 기존 pagination, q/lifecycle/tag | 목록. q는 이름·설명 문자열 검색이며 벡터 검색이 아님 |
| GET /api/v1/workflows/{id} | 없음 | 상세 metadata와 document |
| PATCH /api/v1/workflows/{id} | 변경할 필드 + 충돌 검사 토큰 | 200 최신 자산, 충돌409, 잘못된 입력422 |
| POST /api/v1/workflows/{id}/promote | 없음 | 201 새 template UUID. 실행 성공 없이 승격, 해당 벡터 활성화 |
| POST /api/v1/workflows/{id}/clone | name/tags 선택 | 201 새 candidate UUID. 원본 출처 및 쿼리 복제, 검색 비활성 |
| POST /api/v1/workflows/{id}/reindex | 없음 | 200 같은 자산의 현재 쿼리 버전 색인 결과. 실패 재시도/모델 공간 변경에 사용 |
| DELETE /api/v1/workflows/{id} | 없음 | 204. soft delete와 검색 벡터 비활성화를 같은 transaction에서 처리 |
| POST /api/v1/workflows/search | query 필수 | 200 WorkflowSearchResult. 개별 쿼리 row가 아닌 고유 Workflow 후보 |

등록 POST는 매번 새 candidate를 만든다. 선택적인 source_run_id는 본인 Run에 대한 출처이며 동일 Run의 root candidate 재등록은 동일 내용이면 기존 자산, 내용·쿼리가 다르면409다. 일반 POST의 Idempotency-Key 계약을 새로 만든 것은 아니다.

## 등록 요청

[실제 JSON 예시](contracts/workflow-standard/workflow.registration.example.json)와 [envelope Schema](contracts/workflow-standard/registration.schema.json)를 제공한다. 예시 실행 정의의 Skill/Tool은 독립 시험용이며 실제 배포 풀의 ID로 바꿔야 한다.

```json
{
  "user_queries": ["이 데이터의 이상치를 분석해 줘", "데이터 품질을 점검하고 결과를 정리해 줘"],
  "document": {"workflow_version": "2.0", "workflow": {}},
  "tags": ["quality"]
}
```

위 document.workflow={}는 설명용 자리표시자로 유효한 실행 정의가 아니다. 실제 요청은 연결한 완전한 예제를 사용한다.

| 필드 | 의미·검증 |
|---|---|
| user_queries | 하나의 Workflow가 해결할 사용자 질문 예시 배열. 1~1000개, 각1~4000문자, 전체256000문자 이하. 바깥 공백 제거 후 같은 문자열 중복·빈 문자열·null 거절. 정규화 후 순서 보존 |
| document | 공개2.0 실행 JSON 객체. 현재 배포된 Skill/Tool/입출력 계약과 의미 검증 후 immutable 파일로 한 번 저장 |
| document.workflow.user_request | 재사용 분석 목표. 검색용 user_queries와 다른 개념으로 유지. 예시 질문으로 덮어쓰지 않음 |
| tags | 선택 배열, 기본[]. 소문자 정규화, 최대20개·각50문자 |
| source_run_id | 선택 본인 소유 Run UUID. 직접 작성은 생략. 권한이나 실행 승인 근거가 아님 |

쿼리마다 벡터는 각각 저장하고 Workflow JSON은 쿼리 수만큼 복제하지 않는다. candidate 등록 때 비활성 벡터를 생성하고, template으로 승격하면 추천 검색에 활성화한다. 같은 모델 공간·같은 쿼리의 벡터가 원본/이전 수정 버전에 있으면 재사용한다. 쿼리 원문만 임베딩하며 Tool 코드·전체 Workflow JSON을 임베딩에 붙이지 않는다. 사용자 질문의 생성/증강을 추가 LLM 호출로 자동 수행하지 않는다.

## 응답과 수정 버전

[응답 Schema](contracts/workflow-standard/resource.schema.json)를 참고한다. 기존 UUID·파일·출처·태그·시간 필드에 아래를 추가했다.

| 필드 | 의미 |
|---|---|
| workflow_id | DB 자산 UUID. 수정·색인해도 유지, 승격·복제하면 새 UUID |
| content_sha256 | 실행 JSON 파일 내용 지문. 쿼리만 수정하면 그대로이며 수정 횟수가 아님 |
| user_queries | 저장된 정규화 검색 예시 배열 |
| resource_revision | 작성 필드 변경 횟수 버전, 초기1. 쿼리·JSON·이름·설명·태그 수정과 삭제 시 증가. 색인 작업의 상태 변경은 증가시키지 않음 |
| search_revision | 검색 입력과 실행 정의를 함께 묶는 버전, 초기1. JSON/쿼리 변경·삭제 시 증가. 태그만 수정하면 유지 |
| index_state | not_indexed/pending/ready/failed. ready는 벡터 생성 성공이며 candidate가 검색 가능하다는 뜻은 아님. 검색에 사용하는 모델 공간 변경 후 재색인 필요 |
| index_error | 실패 코드 또는null. embedding_unconfigured, user_queries_required, embedding_transport_failed, embedding_response_invalid, embedding_dimensions_mismatch, embedding_zero_vector. 인증키/HTTP 예외 원문은 반환하지 않음 |
| is_recommendable | template의 검색 허용 상태. candidate는false, 승격 template은true. 실제 검색에는 모델 공간·현재 버전·ready 조건도 적용 |
| document | 상세/생성/수정 응답은 원본 객체, 목록에서는null |
| file_path | 공유 저장소 루트 기준 immutable 파일 상대 경로. 프론트가 직접 파일을 쓰는 용도가 아님 |
| source_workflow_id | 복제/승격의 원본 UUID 또는null |
| source_run_id / created_by_user_id | 실행 출처 및 작성자의 내부 UUID 또는null |
| name/description/goal/schema_version/lifecycle | 표시용 설명, 분석 목표, 공개 규격 버전, candidate/template 구분 |
| tags/created_at/updated_at/deleted_at | 태그와 DB 시각. 삭제 전 deleted_at=null |

쿼리 수정에는 GET의 expected_resource_revision을 반드시 보낸다. 실행 JSON 수정은 이 토큰 또는 기존 expected_content_sha256 중 하나가 필요하다. 둘 다 보내면 둘 다 검사한다. 과거 SHA 기반 클라이언트는 JSON 수정에만 유효하며 쿼리 수정 충돌을 검출할 수 없으므로 새 프론트는 resource_revision을 사용한다.

```json
{
  "expected_resource_revision": 3,
  "user_queries": ["새 검색 예시", "다른 표현의 같은 분석 요청"]
}
```

수정 transaction에서 기존 벡터를 비활성화한다. 임베딩은 DB 연결을 반환한 뒤 수행하며, 결과를 게시할 때 Workflow 행을 잠그고 search_revision을 다시 검사한다. 수정/삭제 이후에 늦게 끝난 이전 작업은 superseded로 무시된다. 등록은 먼저 저장되므로 모델 미설정/호출 실패에도 자산이 사라지지 않는다. 응답의 index_state를 확인하고 설정 보완 후 reindex한다. 최초 실패는201/200의 자산 응답으로 보고하며 존재하지 않는 성공 벡터를 만들지 않는다.

## 검색 응답

요청은 {"query":"현재 사용자 질문"} 하나다. query는 바깥 공백 제거 후1~4000문자다. 후보 수/예산은 서버 중앙 설정을 따르며 요청자가 임의로 확장하지 않는다. [검색 Schema](contracts/workflow-standard/search.schema.json)를 제공한다.

```json
{
  "items": [],
  "diagnostics": {
    "mode": "hnsw",
    "approximate": true,
    "termination": "no_more_ann_candidates",
    "rounds": 2,
    "ann_rows": 64,
    "distinct_candidates": 1,
    "elapsed_ms": 25.5,
    "reranked": true
  }
}
```

위는 형식 예시다. 발견한 후보가 score threshold/공개2.0 조건을 통과하지 않으면 distinct_candidates와 items 수는 다를 수 있다.

| 필드 | 의미 |
|---|---|
| items[].workflow_id | 고유 Workflow UUID. 같은 Workflow의 여러 쿼리는 한 후보로 반환 |
| items[].content_sha256/resource_revision/search_revision | 이번 검색의 실행 정의 및 작성/검색 버전. Agent 추천은 이 snapshot을 고정 |
| items[].name/document | 현재 파일의 이름/공개2.0 JSON. 코드 소스를 제공하지 않음 |
| items[].similarity | 발견 후보의 모든 활성 쿼리 중 최고 cosine similarity = 1 - 최소 cosine distance. 확률·성공률이 아님 |
| items[].matched_query | 대표 점수를 만든 등록 예시. 동일 점수는 SHA로 결정적 선택 |
| diagnostics.mode/approximate | 항상hnsw/true. 후보 내 점수 재계산 후에도 전역 정확 검색으로 바뀌지 않음 |
| termination | candidate_limit, no_more_ann_candidates, round_limit, timeout, disabled, unconfigured, index_unavailable, embedding_unavailable, database_unavailable |
| rounds | 실제 ANN batch 질의 횟수 |
| ann_rows | 읽은 쿼리 후보 row 합계. 고유 Workflow 수가 아님 |
| distinct_candidates | ANN에서 찾은 고유 Workflow 수. 적용 가능성 판단 후 UI 계획 개수와 다름 |
| elapsed_ms | embedding 입장·호출 및 검색/파일 읽기를 포함한 실제 전체 시간 |
| reranked | 발견 후보에 한정한 점수 재계산 완료 여부 |

검색은 (1) 현재 요청 임베딩, (2) 활성 모델 공간의 partial HNSW index 조회, (3) 찾은 Workflow ID를 제외한 반복 batch, (4) 발견한 Workflow들에 속한 쿼리만 정확한 대표 점수 계산, (5) WORKFLOW_SIMILARITY_SCORE(기본0.93) threshold와 공개 규격 필터 순서다. DB 검색·파일 경로는 같은 DB snapshot을 사용하며 파일은 immutable 이전 버전으로 읽는다.

HNSW index가 없으면 index_unavailable을 반환하며 전량 정확 검색으로 대체하지 않는다. 검색 횟수/시간에 상한이 있고 일부 결과만 나올 수 있다. 후보가20개/5개 나왔다고 전역 최적20개/5개를 찾았다는 뜻이 아니며, 빈 결과나 no_more_ann_candidates도 적합한 자산의 전역 부재를 증명하지 않는다. 후보 안에서의 재정렬은 검색되지 않은 Workflow를 복구하지 못한다. 검색 서비스의 장애·미설정은 정상 결과와 diagnostics로 구분하고 Agent는 신규 계획을 제안할 수 있다.

## Agent 연결과 사용자 승인

기존 create_agent의 planning 선택에 planning_scope=end_to_end/incremental을 추가했다. 전체 E2E 분석일 때 Skill metadata 조회와 함께 검색하고, FAQ·보고서/결과 설명·부분 분석·이미 열린 계획 조정에는 검색하지 않는다. 이는 모델이 명시한 scope에 따른 분기이며 자연어 분류 정확도를 보장한 것은 아니다.

모델은 반환된 workflow_id와 이번 input_values만 선택한다. 서버가 이번 invocation의 ToolMessage에서 고정된 정의를 해결하고 다시 검증한다. 존재하지 않는 ID·중복 추천·추천 ID와 별도 definition을 함께 제출하는 응답은 거절한다. template에 명시된 실행 정책은 유지하고 서비스 상한 및 사용자 HITL 변경을 적용한다. 다른 Tool 조합이 필요하면 신규 계획으로 제안한다. 추천+신규 계획 전체는 MAX_PLAN_CANDIDATES(기본5)를 넘지 않는다.

승인 화면 PlanView와 승인 snapshot에는 catalog_reference(workflow_id, content_sha256, resource_revision, search_revision, similarity)를 함께 기록한다. 승인 화면에서 값을 수정하거나 Tool을 제외해도 원본 출처와 실행별 최종 계획을 구분할 수 있다. 추천 검색을 위해 새 별도 실행 워커·DB 풀·추가 분류 모델을 만들지 않았다. Skill/Tool 이름을 코드에 하드코딩하지 않는다.

Agent에 넘기는 후보 정의는 완전한 후보 단위로 WORKFLOW_SEARCH_CONTEXT_MAX_CHARS 예산에 맞춘다. 현재 배포 자산과 맞지 않는 정의는 invalid_templates, 문맥 예산에서 빠진 정의는 context_omitted_templates로 Tool 결과에 구분한다. 정의를 반만 잘라 추천하지 않는다.

## 배포·이행

1. Python 의존성을 uv.lock에 맞춰 설치한다(pgvector Python0.4.2). PostgreSQL 서버에 pgvector>=0.8.0이 필요하다. 로컬 Compose/진단 DB 이미지는 PostgreSQL17을 유지한 pgvector/pgvector:0.8.6-pg17로 변경했다. 기존 실행 중 컨테이너는 자동 변경하지 않았다.
2. 기존 서비스 쓰기를 멈춘 이행 구간에서 alembic -c alembic.crud.ini upgrade head 실행. 0029는 ARRAY 벡터를 native vector(float32)로 변환하고 과거 임베딩을 inactive/superseded 이력으로 보존한다. 기존 벡터 이력을 사용자 질문 임베딩으로 재해석하지 않는다. 기존 자산의 user_queries는[]이며 명시적으로 PATCH해 보완해야 한다.
3. 중앙 설정에 WORKFLOW_EMBEDDING_BASE_URL, MODEL, DIMENSIONS를 모두 제공한다. key는 secret으로, 가중치 버전은 MODEL_REVISION으로 주입. chat model 설정을 embedding 모델로 추정하지 않는다. 설정 우선순위는 config>env>기본값이며 별도 dotenv 로더를 두지 않는다.
4. PYTHONPATH=src python tools/provision_workflow_index.py 실행. 배포 DDL 권한으로 CREATE INDEX CONCURRENTLY를 수행한다. 모델 공간별 차원 cast와 partial index를 생성하고 실패한 invalid index는 같은 이름으로 재생성한다. API 요청이 DDL을 실행하지 않는다.
5. 기존 template 작성자는 user_queries PATCH/reindex 후 ready를 확인한다. 모델/차원/base URL/revision 변경 시 새 index 생성과 재색인이 필요하다. 다른 모델 설정의 Pod가 동시에 색인을 게시하는 롤아웃은 지원 계약으로 보장하지 않으므로 설정을 통일하고 재색인 구간을 분리한다. 과거 공간 벡터 이력은 삭제하지 않는다.
6. 실제 임베딩 기반 검색 품질·동시 부하를 측정하고 threshold/ef_search/scan budget/context 후보 수를 조정한다. [중앙 설정 예시](../config.yml)의 각 주석을 따른다. HNSW vector expression index는 현재1~2000차원만 지원하며 더 큰 모델은 halfvec 등 별도 검토가 필요하다.

현재 기본값: embedding 동시2·HTTP batch32·전체 embedding 대기15초, 검색 후보20·batch64·최대8회·DB/파일 전체2초, ef_search200·scan20000·메모리배수2·Agent 후보문맥64000문자다. embedding15초는 검색2초에 포함되지 않고 elapsed_ms에는 둘 다 포함된다. 검색 예산의75% 시점부터 새 반복을 시작하지 않아 후보 재정렬 여유를 둔다. 총 시간 상한에 걸리면 완료되지 않은 점수를 임의로 반환하지 않는다. max_scan_tuples는 pgvector의 근사 scan 예산이지 DB의 엄격한 row 처리 상한이 아니다. 모든 숫자는 운영 검증 전 초기값이다.

마이그레이션 downgrade는 여러 검색 버전/모델 공간 때문에 과거 고유 키로 돌아가며 벡터 이력이 사라질 경우 명시적으로 거절한다. extension과 SDK-owned Store/checkpoint 데이터는 자동 삭제하지 않는다.


## 2026-10-06 검색 설정 검증 후속

[095 품질·비용 근거](reports/workflow-hnsw-quality-2026-10-06/README.md)에서 동일 벡터 집중의 누락과 설정 대조를 검증했다. m32는 500개 완전 중복에 효과가 있지만 1,000개와 768차원 경계 요청에서는 누락해 기본값으로 채택하지 않았다. 현재 공개 계약·설정·벡터 저장 방식은 유지한다.

완전히 동일한 벡터의 검색 대표화는 원문과 이력을 보존하는 후속 설계 후보이며 아직 구현하지 않았다. 실제 모델의 자연어 품질과 threshold는 미검증이다. 후보 내 재정렬이나 충분한 결과 개수로 전역 정확성을 보장한다고 설명하지 않는다.
