# 093 다중 쿼리 Workflow 검색 검증·등록 설계 재검토

2026-10-05. 브랜치 `feature/workflow-retrieval-benchmark`, 기준 `f59e68f`. 진단·보고·설계 완료. 추천/등록 API 구현·베이스 병합·원격 push·운영 배포는 미수행이다.

## 문제와 범위

여러 유저 쿼리를 한 Workflow에 연결하고 쿼리 벡터로 추천하되 결과는 Workflow 단위로 반환한다. 사용자는 50개씩 비슷한 쿼리가 등록되면 고정 overfetch 후보 수가 소진된다고 지적했다. 실제 pgvector로 개수·품질·비용을 따로 확인한 뒤 API를 확정하는 단계다.

운영 코드/DDL/환경변수는 바꾸지 않았다. [진단 실행기](../../scripts/benchmarks/workflow_retrieval/README.md), [원본·HTML 보고서](../reports/workflow-retrieval-2026-10-05/README.md), [등록·추천 설계](../design/workflow-retrieval-and-registration.md)를 추가하고 기존 확정 문서를 실행 정의와 미확정 추천 계약으로 구분했다. 초안 원본/Schema는 수정하지 않았다.

## 실측

로컬 격리 pgvector0.8.6/PostgreSQL17.11·CPU2·2GiB, 768차원 합성 벡터를 사용했다. 100 Workflow×5/50/500, 1,000×50, 활성 행 전용100×500을 비교했다. 조건별20요청×3반복×7방법으로 2,100개 원본을 보존했다. 실제 LLM·embedding HTTP·API·Executor·동시 부하는 제외했다.

| 조건/관찰 | 결과 |
|---|---|
| 50개 쿼리/WF, 집중 | 후보50:1WF; 후보200:4WF; iterative 제외:5WF |
| 500개/WF, 집중 | 후보1,000까지 확대:2WF; 기본 제외:1WF; iterative 제외:5WF |
| 5만 벡터, 집중 exact→iterative | 평균128.02→22.02ms, p95 185.46→30.64ms |
| 5만 벡터, 경계 iterative | 전체 인덱스 평균4WF·Recall68%; 활성 인덱스5WF·Recall80% |
| 500벡터 작은 테이블 | exact4.39ms, iterative10.52ms. 순차 scan 선택·반복 왕복 비용 |

집중 요청 평균82.8% 감소는 해당 합성 조건만의 관찰이다. 운영 추천/API의 개선 완료 수치가 아니다. 활성 인덱스/탐색 폭500에서도 정확한 TOP5 누락이 남았다. 5개가 채워진 경우에도 누락/대표거리/순위 오류가 있을 수 있다. 부분 인덱스 인과 효과나 실제 자연어 품질을 보장하지 않는다.

EXPLAIN에서 큰 corpus의 iterative 제외는 HNSW를 사용했고 전량 순차 거리 계산을 피했다. 하지만 다섯 번째 집중 조회에서 이미 선택된 Workflow의2천행을 필터했다. 제외 조건 자체가 인덱스 탐색 비용을 없애지는 않는다.

## 검증

NumPy의 별도 코사인 계산으로 정확한 그룹 TOP5와 필터·중복·거리·개수·평균/p95·Recall·순서를 검산했다. 2,200개 검산 항목 PASS, raw SHA 보존. fixture 생성은 runner 공유이며 HNSW 내부 구축 변동은 통제하지 못했다. 2,200은 pytest 수가 아니다.

portable HTML manifest/출처/구조 검증 PASS. 설치된 Chrome headless 검증은 시간 초과로 미완료이며 시각 검증 완료로 표시하지 않는다. [receipt](../reports/workflow-retrieval-2026-10-05/report-delivery.json)에 기록한다. Python 구문·문서 링크·diff 검증도 완료 기록에 포함한다.

임시 `dtest-workflow-search-093`/localhost53607만 생성해 측정했고 종료/제거했다. 기존 서비스와 원래 사용자 작업 트리는 보존했다. 테스트 검색 테이블은 실 서비스 테이블이나 migration이 아니다.

## 결정과 후속

고정 후보 배수는 해결책으로 채택하지 않는다. 활성 검색용 인덱스와 반복 제외 검색은 근사 후보안으로 유지하지만 정확한 전역 TOP5를 약속하지 않는다. exact fallback은 전량 비용이 있고, 개수 부족만 감지하는 fallback은 5개가 차면서 누락되는 반례를 해결하지 못한다.

정의2.0은 확정 상태를 유지한다. 다중 user_queries 등록, embedding pending/ready/failed, resource/search revision, 검색 응답과 추천 연결은 미구현이다. 정확한 순위가 필수인지·실제 모델/차원·현업 정답 요청·지연 목표를 확인한 후 별도 확정 문서와 API/DDL을 구현한다. 모델 호출 수 최적화/Registry/Artifact/운영 후순위는 유지한다.
