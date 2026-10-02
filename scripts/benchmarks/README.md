# 성능 비교 도구의 적용 범위

054 이후 현재 그래프의 모델 답변/근거 비교는 `conversation/run.py`와 [현재 Agent 개발 가이드](../../docs/agent-development/README.md)를 따른다. 이 문서 아래 DB scope·total_refactor·runtime_profile·process_scaling 도구는 017~035의 **고정 과거 commit**을 재현하는 이력 도구다. 원본 결과와 집계 방법을 보존하기 위해 과거 그래프의 단계·호출 횟수를 현재 Agent로 바꾸지 않는다.

이력 도구의 `--source`, benchmark 설정의 `commit`, before/after ref는 해당 보고서의 정확한 commit을 사용한다. 현재 HEAD/feature/refactor-base는 이전 설문형 graph.py·create_llm_dependencies가 없어 대상이 아니다. 새 Agent의 처리량을 이 도구로 측정하거나 이전 4단계 숫자를 현재 결과로 보고하지 않는다. 이력 분석/보고서 JSON은 유지하며, 과거 코드가 필요하면 Git의 해당 commit을 별도 디렉토리에 export한다. 현재 SSO API에 X-User-Id 기반 구형 부하 클라이언트를 그대로 사용하지 않는다.

전체 새 흐름의 SSO·계획 편집·Executor 시나리오를 포함한 종합 부하 harness는 후속 검증 항목이다. 054는 부하 결과를 새로 생산하거나 처리량 향상을 주장하는 작업이 아니다.

# DB connection lifetime A/B benchmark

These scripts compare immutable commits `64ad96f` and `4bb5c2f` through real HTTP and disposable PostgreSQL. They do not launch the existing deployment or use its `.env`. See `docs/reports/db-scope-comparison-2026-09-29/METHOD.md` for workloads, definitions, caveats and the measurement correction.

1. Start a dedicated local PostgreSQL 17 database named `identity_test`, expose it only on loopback, and install the repository Python dependencies (the recorded run used Python 3.11).
2. Set `DTEST_BENCH_DATABASE_URL` to its SQLAlchemy `postgresql+asyncpg://` URL. **Every trial truncates the entire public schema except alembic_version. Never use a shared/application database.**
3. Run `python scripts/benchmarks/compare_db_scope.py --matrix main --repeats 2 --output /tmp/db-ab-main`.
4. After it finishes, run the same command with `--matrix sensitivity --output /tmp/db-ab-sensitivity`. Do not overlap runners on one DB.
5. Aggregate with `python scripts/benchmarks/analyze_db_scope.py /tmp/db-ab-main /tmp/db-ab-sensitivity --output <report-data>`.
6. Build the canonical report JSON with `python scripts/benchmarks/build_db_scope_report.py --data <report-data> --output <report-dir>`. The recorded HTML uses the Data Analytics portable artifact builder; JSON/CSV/Markdown evidence is independently readable without that plugin.
7. Remove the dedicated scratch container/volume after collecting evidence.

`db_scope_server.py` is the instrumented loopback-only server, not a deployment entry point. It uses normal API/Worker/analysis graph/checkpointer code; graph resource construction omits external Executor bindings and uses NullWorkflowStore. Existing ScriptedAgent provides deterministic LLM responses. The server process is restarted and the DB reset per trial. `pilot` and `calibrate` matrices support harness diagnosis and are not included in the final report.

Artifacts retain every attempted scenario, HTTP error count, trial duration, Run state, pool hold/acquisition, model/graph timing and SQL timing. Timing failures are not quietly dropped into successful-response percentiles. Interrupted runs are expected at the selected HITL endpoint; they are not failed runs.

Conversation 비교 driver만 명시적으로 선택한 054 이전 source snapshot에 한해 이전 model factory 위치를 탐지한다. 현재 서비스 import에 이전 graph shim을 제공하는 동작이 아니며, 비교 입력·prompt·factory 의미는 동일하게 유지한다.
