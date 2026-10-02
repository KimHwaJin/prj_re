# Current service throughput measurement

This is a **loopback diagnostic**, never installed in the service application.
It exercises production cookie/CSRF, CRUD, PostgreSQL Run queue, session ownership,
LangGraph checkpoints, event persistence and SSE. Only the corporate SDK verdict
and model response are explicit fixtures. No external LLM, Executor, Jupyter,
Phoenix or original developer `.env` is used.

`--settings-file` is a private flat JSON containing a local `database_url` whose
existing DB name is `agentic_runtime_test`. Only its local connection credentials
are borrowed. The runner creates two new UUID-named `service_perf_*` databases,
rejects collisions, migrates/resets **only those owned scratch databases**, then
removes them in `finally`. Redis login keys use a fresh namespace and are removed
by exact namespace scan; no FLUSH, existing Streams/group or container changes.
Do not run another workload or test suite during measurement.

```sh
python scripts/benchmarks/service_throughput/run.py \
  --settings-file /absolute/private/settings.json --output /tmp/fresh-baseline \
  --users 1 10 30 50 --concurrency 4 16 32 --delay-ms 5000
python scripts/benchmarks/service_throughput/run.py \
  --settings-file /absolute/private/settings.json --output /tmp/fresh-tuned \
  --users 1 10 30 50 --concurrency 16 32 --delay-ms 5000 \
  --cache-size 100 --sse-seconds .5
python scripts/benchmarks/service_throughput/run.py \
  --settings-file /absolute/private/settings.json --output /tmp/fresh-internal \
  --users 50 --concurrency 16 --delay-ms 0 --repeat 2 --cache-size 100
python scripts/benchmarks/service_throughput/run.py \
  --settings-file /absolute/private/settings.json --output /tmp/fresh-crud \
  --users 1 10 30 50 --concurrency 16 --delay-ms 0 --scenario crud
python scripts/benchmarks/service_throughput/analyze.py \
  /tmp/fresh-baseline /tmp/fresh-tuned --output /tmp/report-data
```

Each finite cohort starts concurrently, once per trial. This is not a sustained
arrival test or a Kubernetes resource-limit test. Authentication and default
User/Project provisioning are outside measurement; every timed flow creates a
new Project/Session, starts one Run, edits its plan and approves it. Three private
invocations belong to one public Run. Completion means `plan_approved` with
Executor disabled, **not analysis executed**. The approved source snapshot and
normal validation still run. The model fixture makes one call with fixed 0 or
5000ms delay; it bypasses actual create_agent metadata discovery/provider HTTP
and does not measure real model throughput. It is not comparable with the old
four-call legacy benchmark. There is no user think time in this cohort.

CRUD does seven calls: Project create, Session create, Project list, Session
list, Session rename, Session delete, Project delete. Login keys/registration
are excluded consistently. An unmeasured complete warmup precedes every trial;
startup/first pool creation are not throughput results.

The source-only server records worker/model intervals, existing diagnostic
spans, SQL fingerprints/counts, pool acquisition, CPU and loop lag in memory.
It uses pure ASGI instrumentation. HTTP timings end at response headers; SSE
waits include delivery. PostgreSQL and RSS are sampled about every 0.5 seconds
with one excluded monitor connection. Maxima can miss peaks; RSS is not cgroup
memory, and CPU excludes DB/client/Redis/model processes. Pool acquisition can
include connection creation/health checks. SQL durations overlap other spans.
Never sum nested diagnostic spans as independent work.

Queue time uses private invocation created_at→started_at. Worker non-model time
subtracts measured mock intervals from worker intervals per user. The residual
includes CRUD, HTTP delivery, SSE coalescing and work outside the worker interval.
It is **not solely SSE latency**. Throughput is users / finite batch duration,
not stable arrival capacity. p95 is nearest rank within one cohort; repeated
p95s are described as averages of trial p95s, not a pooled p95.

Every successful flow checks 3N single-attempt private invocations, N model
calls, worker IDs and concurrency, 2N interrupts + N success, no remaining owner,
no recovery tasks and monotonically unique SSE sequences. Results/configuration
files are private 0600. `before-validation.json` preserves failed measurements;
never fold them into successful averages. The analyzer archives successful raw
JSON with deterministic gzip and SHA-256, independently sweeps worker intervals,
and verifies expected identities/counts. A source SHA inventory records changed
Python files; private configuration/credentials are not in report data.
