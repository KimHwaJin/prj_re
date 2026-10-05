# 다중 쿼리 Workflow 검색 검증 — 2026-10-05

주요 결과·시간표·그래프·한계는 [HTML 보고서](report.html)에 정리했다. 운영 성능 시험이 아니라 검색 구조의 합성 검증이다. [등록·추천 설계 검토](../../design/workflow-retrieval-and-registration.md), [작업 기록](../../improvements/093-workflow-retrieval-benchmark.md)과 함께 읽는다.

## 증거 목록

| 파일 | 의미 |
|---|---|
| artifact.json | HTML을 만드는 전체 보고서 정의·데이터·SQL/지표 출처 |
| report-source-notes.json | 독자·구성·차트 목적·요약 지문 |
| report-delivery.json | HTML 생성/구조/브라우저 검증 receipt. 미검증 항목도 보존 |
| summary.json | 조건/방법별 평균·p95·그룹 수·Recall·순서·검색 SELECT 집계 |
| verification.json | NumPy 독립 거리 계산과 원본/집계 검산 결과·지문 |
| main-results.jsonl | 100 Workflow × 5/50/500개, 1,260개 측정 |
| scale-results.jsonl | 1,000 Workflow × 50개, 420개 측정 |
| partial-results.jsonl | 100 Workflow × 500개, 활성 행 전용 인덱스, 420개 측정 |
| *-environment.json | DB/pgvector/numpy 버전, 차원, 개수, seed, 검색 설정 |
| *-corpus-*.json | 벡터 SHA256·행/인덱스 크기·구축 시간·실제 요청 벡터 |
| *-plans-*.json | 집중/경계/필터 첫 요청의 방법별 EXPLAIN ANALYZE BUFFERS |
| queries.sql | 정확 집계·제한 후보 집계·제외 검색 SQL |

상세 재현은 [실행기 안내](../../../scripts/benchmarks/workflow_retrieval/README.md)를 따른다. 원본 벡터는 결정적 fixture로 재생성하여 SHA와 대조한다. 2,100개 측정 기록·2,200개 검산 항목 통과는 pytest 테스트 수가 아니다.

격리 컨테이너 `dtest-workflow-search-093`(CPU2·2GiB), localhost:53607의 `workflow_search`만 사용했다. 기존 API/Executor/DB/Redis는 유지했고 서비스 `.env`나 DDL을 변경하지 않았다. 임시 컨테이너는 측정 후 종료/제거했다. HNSW 내부 구축 변동·공존 컨테이너의 자원 영향은 통제하지 못했다.

## 보고서 검증 상태

canonical manifest/데이터/출처와 HTML 구조 검증은 통과했다. 설치된 Chrome을 통한 그래프 렌더링 검증은 시간 초과로 완료하지 못했다. 이 환경에서는 브라우저 시각 검증 완료로 표시하지 않는다. receipt에 최종 구조 검증과 Chrome 시도 결과를 함께 남긴다. 핵심 결과는 summary와 raw JSONL에서도 확인할 수 있다.
