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
