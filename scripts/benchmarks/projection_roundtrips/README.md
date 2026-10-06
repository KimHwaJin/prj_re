# Result persistence round-trip comparison (033)

This isolates four service changes against `8836df6`. It is the first performance-priority follow-up, not a final capacity/load certification. It reuses the real HTTP, SSE, PostgreSQL/checkpointer and 5-second local model harness in `runtime_profile`.

Use a disposable PostgreSQL 17 database named `identity_test` on localhost. Set `DTEST_BENCH_DATABASE_URL` to its SQLAlchemy asyncpg URL. The harness destroys its public schema before each trial; never run it on application/shared data. Do not overlap any benchmark or PostgreSQL pytest on that DB.

Run these four batches sequentially, with a fresh output path each time:

```sh
python scripts/benchmarks/runtime_profile/run.py --ref 8836df6 --users 1 10 --output /tmp/projection-before-r1
python scripts/benchmarks/runtime_profile/run.py --ref AFTER_REF --users 1 10 --output /tmp/projection-after-r1
python scripts/benchmarks/runtime_profile/run.py --ref 8836df6 --users 1 10 --output /tmp/projection-before-r2
python scripts/benchmarks/runtime_profile/run.py --ref AFTER_REF --users 1 10 --output /tmp/projection-after-r2
python scripts/benchmarks/projection_roundtrips/compare.py \
  --before /tmp/projection-before-r1 --before /tmp/projection-before-r2 \
  --after /tmp/projection-after-r1 --after /tmp/projection-after-r2 \
  --output /tmp/projection-comparison
```

`AFTER_REF` is a fixed Git ref for the completed 033 source. The recorded run used Git tree `88e026542f7fd2dc8251cc5344a5e29c1919e7e5`, captured before the documentation/tests were committed; its four changed production files must match the final 033 commit. Use that commit to reproduce if the unreferenced tree is unavailable. Every source is copied with `git archive`, so ongoing worktree edits cannot affect a running trial. Both variants have the same 0023 DB schema.

Conditions: one API process, four Run slots, service pool 10/overflow 0, checkpoint pool 1–4, bridge pool 4, four non-streaming model HTTP calls per user at 5 seconds each, three resumes ending at Workflow approval, SSE, 0.2-second think time between stages. No Executor/Redis workload, no real model server and no developer `.env` are used. Each trial resets DB and starts fresh processes; graph/pool warm-up is included. Users are provisioned before measurement; session creation is included. This is a simultaneous-start batch, not steady production arrival.

`compare.py` uses the existing time decomposition and independently checks raw user/Run/model times, source refs, SQL counts, model-call counts, process shutdown and message/log row counts. It rejects missing cohorts and failed/censored cases. All eight trial means and normalized gzip raw data are retained; both original and normalized byte hashes are recorded. Model time, queue time and service projection time are separate. The last is the union of `persistence.*` spans after model-time precedence; it does not include final Run-state storage, API admission or all database activity.

Worker SQL counts are SQLAlchemy cursor executions (all statements), excluding checkpointer/bridge SQL. `commit_calls` in HTTP trials count AsyncSession.commit calls and must not be called actual wire COMMITs. The focused probe below independently counts engine transaction commits. Throughput is completed users divided by batch elapsed time; it is not requests/second or a sustainable capacity limit. With two repeats, small timing differences are descriptive, not statistical evidence of an SLA gain.

For focused query budgets and result compatibility, set `DTEST_IDENTITY_TEST_DATABASE_URL` to the same scratch URL, then:

```sh
PYTHONPATH=src DTEST_PROJECTION_REPORT=/tmp/after-queries.json python -m pytest \
  tests/api_service/test_projection_roundtrips_postgres.py -q
```

To measure the baseline with the same probe, copy that test file to a directory outside either source package, use `--import-mode=importlib`, set `PYTHONPATH` to the archived baseline's `src`, and `DTEST_PROJECTION_MEASURE_ONLY=1`. Select `new_log_returns or message_response_keeps or link_graph_task_noop`. This bypasses only the new SQL budgets; stored-result/idempotency/conflict assertions remain active. Running from outside the repository prevents importing the worktree package instead of the selected baseline. The baseline fixture applies its own migrations to the scratch DB.
