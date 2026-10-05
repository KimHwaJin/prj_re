# Workflow 등록·다중 쿼리 추천 설계 검토

2026-10-05, feature/workflow-retrieval-benchmark. 구현된 계약과 제안을 구분한다. [공개 실행 정의2.0](../workflow-standard.md)은 확정했지만, 이 문서의 다중 쿼리 등록/검색은 미구현 설계다. [측정 보고서](../reports/workflow-retrieval-2026-10-05/report.html), [추적표 R01~R05](../review/workflow-standard-changes.md)를 근거로 한다.

## 현재 구현과 변경안

현행 POST는 document/tags/source_run_id만 받으며 candidate를 만든다. GET의 q는 이름/설명 문자열 필터이고 벡터 추천이 아니다. `WorkflowCandidateCreate`는 추가 필드를 금지하지 않아 현행 서버에 user_queries를 보내도 무시될 수 있다. 요청 성공을 다중 쿼리 저장 성공으로 해석하면 안 된다. 이 단계는 API/DDL/런타임을 바꾸지 않았다.

변경안은 **하나의 Workflow JSON과 여러 검색용 예시 요청**을 함께 등록하는 것이다. 예시 요청마다 벡터를 저장하고 모두 하나의 Workflow resource를 참조한다. 문서를 쿼리 수만큼 복제하지 않는다.

```json
{
  "user_queries": ["제품별 이상값을 점검해 줘", "생산 데이터의 이상치를 분석하고 싶어"],
  "document": {"workflow_version": "2.0", "workflow": "여기에 공개 정의 객체"},
  "tags": ["quality"]
}
```

위 예시는 외부 등록 envelope 설명이며 document.workflow의 문자열은 자리표시자다. 유효한 실행 정의는 [공식 예제](../contracts/workflow-standard/workflow_adaptive.example.json)를 사용한다. source_run_id는 선택적 실행 출처다. workflow.user_request는 재사용 분석 목표이고, user_queries는 그 분석을 찾게 할 여러 요청 표현이다. 둘을 같은 개념으로 합치지 않는다. 이 방식은 등록 예시 쿼리 검색이며 질의 시 가상 문서를 생성하는 HyDE를 구현한 것은 아니다.

## 저장과 수정 경계

| 대상 | 책임·제안 |
|---|---|
| Workflow resource | UUID·작성자/공개 여부·lifecycle·최신 정의 파일·별도 resource revision |
| 정의 파일 | 공개2.0 JSON·content_sha256, 기존 immutable 보존 정책 유지 |
| 검색 쿼리 | query_id·workflow_id·원문·순서/활성 여부·검색 revision |
| embedding | query_id·모델 ID/버전·차원·입력 지문·상태·vector |

물리 테이블/마이그레이션은 아직 확정하지 않는다. 정의 SHA만으로 수정 경합을 제어하면 **쿼리만 바꾼 수정**을 감지하지 못한다. resource revision은 정의/쿼리/검색 상태와 구분하여 도입하고 PATCH/승격/복제의 복사·갱신 규칙을 함께 정해야 한다. 기존 content_sha256은 파일 지문으로 유지한다.

등록된 Workflow와 검색 준비 상태를 분리한다. pending/ready/failed 같은 상태로 embedding 완료 여부를 확인할 수 있게 하고, 실패했다고 등록 자체가 사라지거나 준비되지 않은 벡터를 추천하지 않게 한다. 동기 생성/비동기 접수, HTTP 상태 및 응답 DTO는 모델/생성비용과 기존 명령 처리 구조를 확인한 뒤 확정한다. 한 쿼리 실패/부분 준비/변경 중 이전 revision 사용 정책도 이때 결정한다.

candidate와 검색 가능한 template은 구분한다. 현행 등록은 candidate이며 승격 후 검색 준비가 된 template을 추천 대상으로 삼는 기본안을 유지한다. 실행 성공은 승격 조건이 아니다. 문서 편집 후 기존 벡터를 그대로 활성화하지 않으며 재검증/검색 revision 전환을 원자적으로 처리해야 한다.

## 검색 결과 단위와 보장

검색은 사용자 요청 한 개를 같은 모델로 임베딩한다. 예시 쿼리 벡터에서 검색하되 **서로 다른 Workflow**를 최대 K개 반환한다. 한 Workflow의 예시가 50개 가까워도 결과 50개나 TOP5 한 개로 중복 소진하지 않는 것이 목표다.

정확한 비교 정의는 대상 쿼리를 모두 계산하여 `distance(workflow)=MIN(cosine_distance(query_embedding, registered_query_embedding))`로 그룹 순위를 정하는 것이다. 점수는 유사도 기준이지 실행 적합성/성공 가능성의 확률이 아니다. 적용 가능한 데이터·등록 Skill/Tool·커널·목표/조건은 Agent가 추가 판단하고 사용자 승인으로 이어진다.

pgvector HNSW는 쿼리 행을 근사 검색한다. 고정 overfetch 배수나 LIMIT 이후 GROUP BY로 전역 그룹 TOPK를 보장하지 못한다. [공식 filtering/iterative 문서](https://github.com/pgvector/pgvector#iterative-index-scans)에서도 필터 후 후보 부족을 반복 탐색으로 완화하되 탐색 한도가 존재한다. strict_order는 발견한 후보의 정렬이며 전역 완전성 보장이 아니다.

## 실측으로 남긴 판단

| 관찰 | 설계 영향 |
|---|---|
| 50개 쿼리/WF: 후보50→1WF, 후보200→4WF | 고정 넉넉한 후보 수를 계약상 해결책으로 쓰지 않음 |
| 500개/WF: 최대1,000개 후보도 2WF | bounded 확대에도 부족할 수 있음을 명시 |
| 기본 제외 검색도 1WF 또는 빈 결과 | WHERE 제외만으로 충분하다는 설명 철회 |
| iterative 제외: 집중 요청 5WF, 5만 벡터 평균128.02→22.02ms | 근사 검색 후보안으로 유지. 모든 행 거리 계산을 피한 HNSW 실행 확인 |
| 경계 요청 Recall68%; 활성 인덱스도80% | 개수 확보와 정확한 TOP5를 구분. 그룹5개 확보만으로 품질 검증하지 않음 |
| 작은500행: exact4.39ms, iterative10.52ms | 반복 왕복과 corpus 크기 고려. exact가 항상 느리다고 단정하지 않음 |

활성 검색 행 전용 인덱스+반복 제외 검색은 **검토 후보**다. 이미 선택한 Workflow의 행도 인덱스 탐색 후 필터되므로 비용은 남는다. query limit/time budget, ef/iterative 상한은 실제 모델·데이터·동시성으로 다시 측정하고 운영값을 정한다. 50개 등록을 임의 금지하여 이번 결함을 감추지 않는다. 개수/길이 제한이 필요하면 저장·임베딩 비용·검색 예산의 명시적 정책으로 정한다.

정확한 전역 TOP5가 필수라면 exact 검색이 기준이며 비용을 감수하거나 별도의 검증된 검색 설계를 마련해야 한다. 결과 개수 부족 때만 exact로 fallback하는 정책은 **5개가 차면서 올바른 Workflow가 누락되는 경우**를 해결하지 못한다. 근사 추천이 허용되면 실업무 쿼리의 품질 목표와 탐색 예산을 함께 정한다. 이번 합성 실험만으로 어느 요구가 필요한지 결정하지 않는다.

## 향후 검색 응답에 필요한 정보

중복 없는 workflow_id/정의 revision, 매칭 query_id와 발견한 거리, requested_k/returned_count, 검색 방식 approximate/exact, 적용한 탐색 상한·fallback 여부를 추적할 수 있게 한다. 원문 예시 노출 여부는 권한과 화면 필요를 검토한다. 공통 envelope·필드명은 다음 구현 시 확정한다.

빈 결과가 ANN 탐색 한도로 발생할 수 있으므로 “검색 가능한 Workflow 없음”과 “근사 탐색에서 후보를 못 찾음”을 혼동하지 않는다. 제한 도달을 확인하지 못하면 exhausted라고 단정하지 않고 unknown으로 처리한다. count=K 역시 search_complete/정확함을 의미하지 않는다. 자연어 적합성 평가는 vector Recall과 별도다.

## 다음 구현 전 확인

1. 사용자가 HNSW 사용을 선택했다. 정확한 전역 TOP5를 운영 계약으로 보장하지 않고, 실제 추천 품질 목표와 검색 예산을 정한다.
2. 실제 embedding endpoint/모델/차원과 입력 길이·운영 corpus/유입량/검색 지연 목표.
3. 현업 예시 쿼리→정답 Workflow 평가군, 검색 Recall·추천 다양성·지연 동시 측정.
4. pending/ready/failed·revision·권한·POST/PATCH/승격/복제의 계약 확정.
5. 변경 추적표를 갱신하고 별도 확정 API 문서/Schema를 만든 후 구현·Agent 추천 연결·E2E 검증.

정의2.0의 스킬/툴 호출 구조는 이번 검색 실험으로 바꾸지 않는다. 실제 임베딩/추천이 미구현임을 최종 API 문서에 계속 표시한다.


## 사용자 결정: HNSW 사용 — 구현 방향

2026-10-05 후속 대화에서 사용자는 전량 거리 계산을 기본으로 하자는 제안을 채택하지 않고 HNSW 사용을 선택했다. 등록 여러 쿼리→하나의 Workflow, Workflow 단위 중복 없는 결과 요구는 유지한다. 아래는 다음 구현 설계이며 아직 운영 코드/DDL에 반영하지 않았다.

### 검색 흐름

1. 사용자 요청을 등록과 같은 embedding 모델/버전으로 한 번 변환한다. 모델·차원이 다른 벡터는 같은 검색에 섞지 않는다.
2. 검색 가능한 template의 활성·ready·현재 검색 revision 행을 대상으로 HNSW를 사용한다. 비활성/구버전 행이 검색용 인덱스를 독점하지 않도록 해당 검색 projection과 부분 인덱스를 유지한다. 원본/과거 embedding 기록은 별도로 보존한다.
3. 반복 인덱스 탐색을 켜고 소규모 후보 batch를 얻어 Workflow ID로 집계한다. 이미 찾은 Workflow는 다음 조회의 WHERE에서 제외한다. 예를 들어 A의 쿼리50개가 발견돼도 후보 Workflow는 A 하나이며, 다음 조회는 A를 제외한다. 제외 행의 ANN 탐색/필터 비용이 없어지는 것은 아니다.
4. Workflow 후보 수 목표 또는 총 검색 예산에 도달할 때까지 반복한다. 후보 목표는 반환 수보다 여유 있게 설정하지만 **서로 다른 Workflow 개수** 기준이다. 고정 쿼리 row overfetch로 중복 문제를 해결했다고 간주하지 않는다. batch 행 수·후보 Workflow 목표·최대 조회 수·총 시간·ef_search·iterative 한도는 중앙 config에서 설명과 함께 관리하고 실측으로 정한다.
5. 발견한 Workflow 후보들에 한해 등록 쿼리 전체와 거리를 다시 계산하고 Workflow별 MIN으로 정렬한다. 예를 들어 후보10개×쿼리50개면 그500개가 재정렬 대상이다. 전체 catalog의 전량 검색은 하지 않는다. 이 단계는 발견한 후보 내 대표 점수/순위를 정확하게 만들 뿐, HNSW가 놓친 Workflow를 복원하지 못한다. 후보 내 재정렬은 093에서 아직 측정하지 않은 추가 설계다.
6. 중복 없는 Workflow 후보를 Agent에 전달한다. Agent는 데이터·자산·목표에 대한 적용 가능성을 확인하고, Workflow 추천과 새 계획을 합쳐 기존 설정(예:최대5개) 이내로 사용자에게 제시한다. 검색 후보 수와 화면 제안 수를 구분한다.

### 부족한 결과와 비용 관리

실제 검색 설정은 단순히 ef_search를 크게 올려 고정하지 않는다. strict_order 및 relaxed_order+후정렬, 구축 설정 m/ef_construction과 필터/부분 인덱스를 같은 실제 데이터에서 비교한다. 검색 확대는 조회 횟수·전체 시간 한도 안에서 수행한다. 후보가 목표 수만큼 발견돼도 글로벌 Recall이 확인됐다는 뜻은 아니다.

예산 안에 다섯 Workflow를 못 찾으면 확보한 개수만 반환한다. 빈 결과를 "적합한 Workflow가 존재하지 않는다"고 단정하지 않는다. Agent는 기존 신규 Skill/Tool 조합 계획 제안 경로를 이용할 수 있다. 검색 상태는 target_reached/budget_exceeded/no_candidates_found 등 관측 가능한 종료 사유와 found_count를 기록하며, no_candidates_found는 catalog 소진/전역 부재의 증명이 아니다. 총 시간 한도를 넘긴 조회는 해당 읽기 트랜잭션을 종료/정리하고 정책에 맞는 부분 결과/실패 상태를 반환한다. 무제한 반복이나 자동 전량 검색으로 지연을 숨기지 않는다.

등록/PATCH는 user_queries별 원문·embedding 지문·query_id·검색 revision을 관리한다. 현재 Workflow마다 활성embedding 하나만 허용하는 유일 인덱스와 ARRAY(Float) 저장은 다중 쿼리 HNSW에 맞게 migration해야 한다. pgvector VECTOR 타입과 모델 차원 확인, 상태 전환/권한/삭제의 검색 가시성 동기화가 구현 범위다. 임베딩 생성은 DB 연결을 점유한 채 수행하지 않는다. 같은 검색에서 후보/정의 revision이 섞이지 않도록 검색 snapshot과 revision 확인을 맞춘다.

### 검증과 문서 확정 순서

다중 쿼리 등록/PATCH/승격/복제 계약과 DDL을 확정하고 HNSW 검색·후보 재정렬을 구현한다. 실제 embedding 데이터로 후보 누락률·Workflow 수·대표 점수/순위·지연·DB 비용·동시 부하를 검증한다. 전체 거리 계산은 오프라인/격리 검증의 비교 기준으로 유지한다. 093의 합성68%를 운영 품질이나 고정 상한으로 쓰지 않는다. 품질 목표에 미달하면 검색/구축 설정과 데이터·필터 원인을 조정하여 다시 확인하며 미검증 상태를 완료로 표시하지 않는다.

원본 대비 W01~W17과 R01~R05는 이력으로 유지하고 R06에 이번 결정을 기록한다. 공개 실행 정의2.0을 변경하는 결정은 아니며, 최종 등록/추천 API 문서는 실제 구현 계약이 확정될 때 별도로 완성한다.
