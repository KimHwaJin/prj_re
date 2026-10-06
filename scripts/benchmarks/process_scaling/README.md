# API process × Run concurrency comparison (035)

Fixed application source: `c3534f0`. This changes benchmark instrumentation only.
User traffic uses Uvicorn's actual shared socket and `workers=N`; it is not a
round-robin emulation using unrelated API ports. Each worker has an additional
localhost control listener for metrics so collection cannot accidentally read
only the worker selected by a keep-alive connection.

Use a disposable local PostgreSQL 17 database named `identity_test`. The runner
rejects non-local hosts and other database names, but **drops public schema** at
each trial. Do not run pytest or another benchmark concurrently on that DB.
A fresh output directory is required. A Linux/macOS `ps` executable is required
for current RSS sampling. No psutil installation or production dependency change
is needed. Run with the repository's Python 3.11 virtual environment.

```sh
export DTEST_BENCH_DATABASE_URL=postgresql+asyncpg://postgres:local-test-only@127.0.0.1:55233/identity_test
python scripts/benchmarks/process_scaling/run.py \
  --ref c3534f0 --users 10 --layouts 2x4 --output /tmp/scaling-smoke
python scripts/benchmarks/process_scaling/run.py \
  --ref c3534f0 --users 10 30 50 \
  --layouts 1x4,1x8,2x4,1x16,2x8,4x4 --output /tmp/scaling-matrix
python scripts/benchmarks/process_scaling/analyze.py \
  /tmp/scaling-matrix --output /tmp/scaling-analysis
```

For additional repeats use a new output directory and `--repeat 2`. Pass all
finished measurement directories to `analyze.py`; exclude the startup smoke
from the matrix (it deliberately duplicates one layout/user/repeat key).

All users create a session, run, resume three times and finish at Workflow
approval waiting. Each user makes four actual calls to a local HTTP model mock,
each delayed five seconds. User creation is outside the timing window; session
creation, graph/pool initialization and 0.2-second think times are included.
Every trial starts a fresh DB/API/model. External LLM, Executor and Redis are
not called. Environment inheritance is restricted to OS basics and explicit
scratch settings, without loading the developer's `.env`.

Each process retains service pool 10/overflow 0, checkpoint max 4 and bridge 4.
Thus multiple processes replicate the pools. These are equal per-process
settings, not equal aggregate DB connection budgets. No Pod CPU/memory quota is
applied. The report must not present the local result as a production limit.

Per-process instrumentation comes from `runtime_profile/server.py`, loaded as a
module. Its direct CLI behavior is unchanged. `runtime_profile/analyze.py` now
uses the configured total slot count; old traces without it still default to 4.
Per-worker snapshots use the same host monotonic clock. The analysis checks all
registered workers, invocation identity/counts, per-process and simultaneous
slot limits, health, PostgreSQL state, output row counts, and process shutdown.

Metrics:

- End-to-end seconds are per user; p95 is nearest rank within that cohort.
- Throughput is completed users / batch elapsed seconds, not a steady arrival
  capacity. When repeated, report values average trial means/p95s; p95 is not
  pooled across trials.
- Queue time sums the four Run created_at→started_at intervals per user.
- CPU is the sum of API worker process_time deltas; supervisor, model, DB and
  client CPU are excluded. Average cores = CPU seconds / workload elapsed.
- RSS is sampled using `ps` at ~1 second and summed at the same sample instant.
  Worker and supervisor values are separated. Shared pages may be counted more
  than once and instrumentation buffers are included; this is not cgroup memory.
- DB activity is sampled every ~0.25 seconds with a dedicated excluded monitor
  connection. It includes active and idle connections; sampled maxima can miss
  short peaks. Lock-wait samples are not an exhaustive lock trace.
- Pool acquisition includes connection checkout/health handling, not only queue
  wait. Query time includes driver/network and server/lock time. Loop lag is
  sampled per worker at ~0.1 second.

Focused analyzer checks use an actual successful smoke capture:

```sh
DTEST_SCALING_CAPTURE=/tmp/scaling-smoke/flow-10-p2-c4-r1/raw.json \
  python -m pytest scripts/benchmarks/process_scaling/test_scaling_analysis.py -q
```

The durable report generator requires the complete 18-condition matrix, retains
repeat counts, and executes its saved SQLite aggregation against trial metrics:

```sh
python scripts/benchmarks/process_scaling/build_report.py \
  /tmp/scaling-analysis/results.json --output /tmp/scaling-report
```

`artifact.json` is the canonical report input. Package it using the installed
Data Analytics `skills/build-report/scripts/deliver_portable_artifact.mjs`
script; `report.html` is generated output. Keep the validation/verification
receipt, raw gzip hashes, method notes and the source ref with the report.
