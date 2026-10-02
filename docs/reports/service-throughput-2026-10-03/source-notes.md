# Report source notes

Audience: technical (service engineering measurement and configuration). Delivery: portable HTML in Codex local development runtime; repository Markdown is engineering/audit source. Main question: service throughput improvements excluding LLM changes. Baseline production SHA59d690a; matched control/cohort identifiers in results.json.

Required structure mapping: title/summary; scope before findings so denominators precede values; three findings sections + stability/SSE sensitivity; configuration; methods/verification; limitations; next steps; further questions.

Chart contract: grouped bars, discrete 1/10/30/50 cohorts, common zero origin and seconds, three visible conditions. The condition dimension is encoded separately from cohort. No misleading continuous trend/interpolated points. 50/32 uses two-trial mean; others one trial. Supporting dataset retains repeats, p95 trial, CPU and queue. Tables preserve exact measurements for zero-delay controls and CRUD where a chart would conceal different baselines. Each has adjacent interpretation and explicit sort. Source provenance uses repository paths, no machine-local DSN. No inference of statistical significance.

Independent verification: 377 arithmetic/hash checks. Failed original50zero not included in29 success captures. Warning occurrences are text matches, not distinct connections. Original five-second controls completed but warned; zero-delay stable control isolates cache/batch costs after connection fixes.

Raw source code fingerprints are capture-time inventory: new tests/doc formatting and unused bootstrap import cleanup added later can differ without production semantics changing. Test inventory differences do not establish Agent changes. Current production agent_service tree remains byte-identical to baseline. Generated HTML packaging receipt records browser QA or structural-only limitation.

Canonical chart/table transformations: flow.sql/internal.sql/crud.sql actually executed with SQLite main.performance_trials loaded from results.json fields. This staging table is analytical only, not a deployed service table. Original measurement transformations/PG timing remain in analyze.py/raw. Narratives use README.md provenance (multiple validated source captures and regression evidence).
