# Workflow 단위 pgvector 검색 진단

운영 API와 분리된 합성 검색 벤치마크다. 한 Workflow에 여러 쿼리 벡터가 연결될 때 개수·정확도·비용을 따로 측정한다. 실행기는 `retrieval_queries` 테이블을 매 조건마다 삭제/생성한다. 전용 로컬 테스트 DB만 사용한다.

## 실행 환경

Python 3.11 이상, numpy, psycopg 3, PostgreSQL 17와 pgvector 0.8 이상이 필요하다. 측정 당시 Python은 프로젝트 `.venv`, numpy 2.4.6, PostgreSQL 17.11, pgvector 0.8.6이었다. 아래 비밀번호는 격리 컨테이너 전용 예시이며 서비스 설정을 읽지 않는다.

```sh
docker run --rm -d --name dtest-workflow-search-093 --cpus 2 --memory 2g \
  -p 127.0.0.1:53607:5432 \
  -e POSTGRES_USER=workflow_search \
  -e POSTGRES_PASSWORD=workflow_search_test_only \
  -e POSTGRES_DB=workflow_search pgvector/pgvector:0.8.6-pg17
```

컨테이너가 준비된 뒤 실행한다. `--output`에는 기존 증거를 덮어쓰지 않는 새 디렉터리를 지정한다. 아래 `$RESULT_DIR`은 실행자가 별도로 설정하는 결과 경로다.

```sh
export WORKFLOW_SEARCH_TEST_DSN='host=127.0.0.1 port=53607 dbname=workflow_search user=workflow_search password=workflow_search_test_only'
.venv/bin/python scripts/benchmarks/workflow_retrieval/run.py --output "$RESULT_DIR" --label main
.venv/bin/python scripts/benchmarks/workflow_retrieval/run.py --output "$RESULT_DIR" --label scale --workflows 1000 --counts 50
.venv/bin/python scripts/benchmarks/workflow_retrieval/run.py --output "$RESULT_DIR" --label partial --counts 500 --active-only-index
.venv/bin/python scripts/benchmarks/workflow_retrieval/verify.py --results "$RESULT_DIR"
.venv/bin/python scripts/benchmarks/workflow_retrieval/build_report.py --results "$RESULT_DIR"
docker stop dtest-workflow-search-093
```

HTML은 Data Analytics의 portable report builder로 `artifact.json`에서 생성했다. 프로젝트 런타임 의존성은 아니며 builder가 없어도 raw 결과/검산/요약을 재생성할 수 있다.

## 입력과 방법

768차원은 실험 가정이다. seed=9305, 단위 정규화 float32 벡터, Workflow별 군집과 서로 인접한 초기 8개 중심을 생성한다. 집중 요청 10개, 두 중심 경계 요청 5개, 가까운 5개 Workflow를 제외하는 필터 요청 5개다. 20/22/24번 Workflow는 비활성이다. main은 100 Workflow × 쿼리 5/50/500개, scale은 1,000 × 50개, partial은 100 × 500개의 활성 행 전용 인덱스다.

| method | 방법 |
|---|---|
| exact | 모든 대상 벡터의 거리를 계산, Workflow별 MIN, TOP5 |
| overfetch_50 / overfetch_200 | 쿼리 후보 50/200개를 검색한 뒤 Workflow별 집계 |
| expand | 후보 50→100→200→1,000개로 확대, 그룹 5개 확보 또는 상한 도달 시 종료 |
| exclude_default | 선택 Workflow 제외, LIMIT1 최대 5회; ef_search=40·iterative_scan=off |
| exclude_iterative | 같은 제외 검색; ef_search=100·strict_order |
| exclude_tuned | 같은 제외 검색; ef_search=500·strict_order |

기타 방법의 ef_search=100·strict_order, max_scan_tuples=20,000·scan_mem_multiplier=2, HNSW m=16·ef_construction=128, jit=off다. 작은 테이블에서 planner가 순차 검색을 선택하면 그대로 기록한다. 방법별 준비 실행 후 순서를 섞어 3회 측정한다. 조건별 인덱스 구축은 한 번이므로 HNSW 재구축 변동을 분리하지 못한다.

## 결과와 검산

`elapsed_ms`는 트랜잭션·SET LOCAL/set_config·검색·호스트 왕복·Python 집계를 포함한다. `search_sql_calls`는 검색 SELECT만 세며 전체 SQL/왕복 수가 아니다. `fetched_group_rows`는 SQL 반환 행 수이며 ANN 내부 후보 탐색량이 아니다. 진단 EXPLAIN은 별도 실행해 timed 결과에 넣지 않는다. 버퍼는 부모·자식을 중복 합산하지 않는다.

`verify.py`는 fixture 지문과 원본 수를 검사하고 NumPy 코사인 거리로 정확한 Workflow TOP5를 재계산한다. fixture 생성은 runner를 재사용하지만 거리 계산/순위/필터/개수/중복/집계는 별도로 검산한다. Recall@5와 ID 전체 순서 일치, 평균·p95·검색 횟수를 재계산하고 raw SHA256을 남긴다. p95는 작은 warm 순차 표본의 기술 통계이며 운영 SLA가 아니다.

합성 테스트는 실제 한국어 의미 적합성, 모델/API/Executor E2E, 동시 부하를 검증하지 않는다. [완료 측정](../../../docs/reports/workflow-retrieval-2026-10-05/README.md)을 참고한다.


## 095: 현재 서비스 검색 구현의 품질·비용 대조

`quality.py`는 실제 `api_service.workflows.retrieval.WorkflowSearch`를 호출한다. 임베딩 HTTP 대신 고정 벡터를 공급하며, 테스트용 최소 검색 테이블과 불변 JSON 파일만 만든다. 등록 API·인증·Agent·Executor·실제 텍스트 정확도는 이 시험에 포함되지 않는다. 서비스 코드와 기본 설정은 변경하지 않는다. 정확한 전량 거리 계산은 오프라인 정답에만 쓰며 런타임 fallback을 추가하지 않는다.

아래 전용 DB만 허용한다. 실행기는 **workflow_quality:53609의 workflows/workflow_embeddings 테이블을 삭제·재생성**한다. 기존 서비스 DB를 지정하지 않는다. 컨테이너는 앞선 093과 분리하고, 모든 측정은 같은 DB에서 순차 실행한다.

```sh
docker run --rm -d --name dtest-workflow-quality-095 --cpus 2 --memory 2g \
  -p 127.0.0.1:53609:5432 \
  -e POSTGRES_USER=quality -e POSTGRES_PASSWORD=quality-local-only \
  -e POSTGRES_DB=workflow_quality pgvector/pgvector:0.8.6-pg17 \
  postgres -c shared_preload_libraries=pg_stat_statements -c track_io_timing=on

export WORKFLOW_QUALITY_TEST_DSN='host=127.0.0.1 port=53609 dbname=workflow_quality user=quality password=quality-local-only'
# 준비 완료 후 실행. RESULT_DIR는 이전 증거를 덮어쓰지 않는 새 경로로 지정한다.
PYTHONPATH=src .venv/bin/python scripts/benchmarks/workflow_retrieval/quality.py --output "$RESULT_DIR/m16" --index-m 16
PYTHONPATH=src .venv/bin/python scripts/benchmarks/workflow_retrieval/verify_quality.py --results "$RESULT_DIR/m16"
PYTHONPATH=src .venv/bin/python scripts/benchmarks/workflow_retrieval/quality.py --output "$RESULT_DIR/m32" --index-m 32
PYTHONPATH=src .venv/bin/python scripts/benchmarks/workflow_retrieval/verify_quality.py --results "$RESULT_DIR/m32"
PYTHONPATH=src .venv/bin/python scripts/benchmarks/workflow_retrieval/quality_boundary_probe.py --corpora "$RESULT_DIR/m32/corpora.json" --output "$RESULT_DIR/boundary-probe.json"
PYTHONPATH=src .venv/bin/python scripts/benchmarks/workflow_retrieval/quality_probe.py --output "$RESULT_DIR/duplicate-probe.json"
PYTHONPATH=src .venv/bin/python scripts/benchmarks/workflow_retrieval/verify_quality.py --duplicate-probe "$RESULT_DIR/duplicate-probe.json" --boundary-probe "$RESULT_DIR/boundary-probe.json" --probe-verification "$RESULT_DIR/probe-verification.json"
docker stop dtest-workflow-quality-095
```

`--index-m`은 진단 DB의 인덱스만 다르게 생성하는 대조 옵션이다. 현재 운영 설정을 읽거나 변경하지 않는다. 서비스의 생성 설정은 여전히 m16/ef_construction128이며, 조회 설정은 중앙 WorkflowSearchSettings를 사용한다.

기본은 인덱스 2회 독립 생성·요청 3회 반복이다. 3차원 동일 벡터50/100/500개와 768차원100 Workflow×50/500개 작은 차이의 벡터를 시험한다. HNSW 서버 그래프의 랜덤성은 seed로 고정하지 못하지만 벡터 데이터의 SHA와 순차 m16/m32 대조를 기록한다. 실제 서비스 조회의 ef/batch/rounds/scan/memory는 한 필드씩 비교하고, 고예산 프로필은 복합 대조로 별도 표시한다.

`quality_probe.py`는 3차원 동일 벡터에 대해 인덱스 생성 전/후 삽입, 입력 순서, m16/m32, 조회 설정과 Workflow 제외 필터를 교차한다. 50/100/500/1000개를 비교하며 LIMIT5000은 전체2001행보다 커서 고정 반환 개수 탓을 분리한다. 원래 질문 이력을 삭제하는 구현은 아니며 완전히 같은 벡터의 축약은 진단 대조군만 제공한다.

각 m에서 timed 순차1,323건과 EXPLAIN65개를 구분한다. 별도24요청 burst를 동시1/4/10, pool4/overflow0에서 비교한다. DB 실행 시간·공유 버퍼 hit/read·SQL 수는 pg_stat_statements로 측정한다. 이를 CPU 시간/CPU 사용률이나 지속 처리량으로 환산하지 않는다. `work_mem`과 후보/스캔 한도도 기록한다.

`verify_quality.py`는 벡터 지문, NumPy 코사인 정답, Workflow 고유성/활성 필터/점수/Recall, 원본 개수/요약 통계/예산, 실제 HNSW 실행계획, 동시 SQL 수와 원본 SHA를 검산한다. [095 결과·한계](../../../docs/reports/workflow-hnsw-quality-2026-10-06/README.md)를 따른다.

`quality_boundary_probe.py`는 m32 마지막768차원500개 데이터가 남아 있는 상태에서 boundary-2의 매우 큰 탐색 예산/직접 SQL을 별도 조사한다. 큰 자원 설정은 진단용이며 운영 권장값이 아니다. 모든 query profile에서 같은 품질을 가정하지 말고 각 요청의Recall을 함께 확인한다.
