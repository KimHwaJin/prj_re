SELECT layout, users, CAST(CAST(users AS INTEGER) AS TEXT) || '명' AS cohort,
 COUNT(*) AS repeats, processes, concurrency, slots,
 AVG(mean_s) AS mean_s, AVG(p95_s) AS p95_s, AVG(queue_s) AS queue_s,
 AVG(llm_s) AS llm_s, AVG(internal_s) AS internal_s, AVG(throughput) AS throughput,
 MAX(peak_runs) AS peak_runs, AVG(cpu_s_user) AS cpu_s_user, AVG(cpu_cores) AS cpu_cores,
 AVG(rss_mib) AS rss_mib, AVG(db_peak) AS db_peak,
 AVG(pool_p95_ms) AS pool_p95_ms, AVG(lag_p95_ms) AS lag_p95_ms,
 MAX(lock_peak) AS lock_peak, MAX(idle_tx_peak) AS idle_tx_peak,
 AVG(sql_p95_ms) AS sql_p95_ms
 FROM benchmark_trials
 WHERE source_commit = 'c3534f0' AND users IN (10, 30, 50)
 GROUP BY layout, users, processes, concurrency, slots
 ORDER BY slots, processes, users;
