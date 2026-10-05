# 094 HNSW 구현 검증

[검증 결과·시험 목록](verification.json), [확정 계약](../../workflow-registration-and-search.md), [변경 추적](../../review/workflow-standard-changes.md), [작업 기록](../../improvements/094-workflow-hnsw-retrieval.md).

실제 PostgreSQL17.11/pgvector0.8.6, 전용 임시 DB53608, CPU2/메모리2GiB에서 검증했다. Python3.11.15. 임베딩은3차원 double, Agent chat transport는 고정 응답 double이다. 실제 Executor/Redis 이벤트·사내 모델 품질/부하는 시험하지 않았다. 기존 Executor·DB·Redis·API는 재기동하지 않았다.

| 검증 | 결과 |
|---|---|
| Agent/API/진단 회귀 | 800 passed, 492 opt-in skipped |
| 관련 기능·격리 PostgreSQL | 27 passed. 위 회귀와 일부 중복 |
| 독립 HTML 콘솔 | 9 passed |
| 배열→native vector·deactivate 이행 | 통과, 과거 검색 복합 텍스트를 사용자 질문으로 추정하지 않음 |
| 모델 index 생성 명령 2회 | 같은 유효 index 1개 유지 |
| downgrade→upgrade 왕복 | 기존 배열 값 보존, inactive 이력 유지 |
| wheel | build·설치형 import/OpenAPI/배포 자산 확인, 삭제된 recommender stub 제외 |
| 문서/예제 | 실제 class 생성5개 Schema 일치, 등록 예제 유효, 받은1.0 파일4개 SHA 일치 |

## 후보 집중 사례

고유 Workflow3개, 앞2개에 동일 벡터 쿼리50/500개씩, 마지막에1개를 등록했다. candidate private 벡터는 검색에서 제외했다. quota3/batch2/ef1000/scan20000/8회/3초/threshold0 설정으로 query 중복과 ANN 종료를 검증했다. 운영 기본값/093 벤치마크의768차원 corpus와 다른 시험이다.

| 앞2개 Workflow당 쿼리 수 | 발견 Workflow | 종료 | 이번 smoke 시간 |
|---|---|---|---|
| 50 | 3/3 | candidate_limit, 3회, 쿼리 후보5개 | 56.41ms |
| 500 | 1/3 | no_more_ann_candidates, 2회, 쿼리 후보2개 | 30.67ms |

500 사례의1/3은 의도한 완전성 결과가 아니다. HNSW가 반환하지 못한 후보는 후보 안 재정렬로 복구할 수 없다. 시험의 합격 조건은 고유 자산·활성 필터·점수 재계산·예산·진단의 구현 계약이며 모든 자산 탐색/자연어 추천 정확도의 합격 조건이 아니다. 값은 해당 수집 회차의 관찰이며 반복 재현되는 속도/Recall 보장이나 운영 수치로 사용하지 않는다. 전체 정확 검색 fallback은 구현하지 않았다. 실제 모델에서의 쿼리 중복 정도·그룹 Recall과 ef_search/scan/memory/후보 정책의 조정을 후속 검증으로 남긴다.

실제 모델 설정이 없으므로 query count/시간 측정으로 모델 품질 개선률을 계산하지 않는다. FAQ/부분 분석 no-search, 서로 다른 세션의 추천 상태 분리, 추천 ID만 선택·수정 정책 유지, 사용자 편집 후 승인 snapshot 고정은 실제 create_agent/LangGraph 경로에 double transport와 InMemorySaver를 연결해 시험했다. 추천 계획의 PostgreSQL checkpoint 재시작·실제 Executor까지의 E2E 검증은 이번 범위가 아니다.

## 검증 중 발견과 수정

등록 쿼리 envelope로 바뀌면서 기존 content-only 테스트에 잘못 추가된 query 수정 토큰을 정리했다. native vector의 DB 반환이 numpy float32임을 확인해 cache를 Python float로 정규화했다. source cache가 있는 promotion은 HTTP를 호출하지 않으므로 지연 경쟁 시험은 새 쿼리 변경을 통해 실제 대기 경로를 검증한다. 기존 venv에 새 의존성을 설치하지 않아 자식 프로세스2건이 실패했던 검증 환경은 임시 venv로 해결했고 최종800건이 통과했다. downgrade의 제거된 중복 index 정리도 왕복 시험으로 수정했다. 중간 실패를 최종 결과로 섞지 않는다.

전체 src compile 시 기존 HEAD의 src/routers/chat/router.py에 `import typing import List, Optional` SyntaxError가 있음을 확인했다. 이 제공 템플릿의 Gaia 경로는 이번 변경 범위가 아니며 기존 파일을 수정하지 않았다. 현재 패키지로 배포하는 API/Agent/계약/런타임만 compile 및 wheel import 검증했다. 이 확인을 Gaia 전체 통합 검증으로 표시하지 않는다.
