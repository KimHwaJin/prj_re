SELECT CAST(users AS TEXT) || '명' AS cohort, users,
 CASE WHEN measurement_set LIKE '%baseline5-v3-v3%' THEN '변경 전 · 16'
 WHEN concurrency=16 THEN '변경 후 · 16' ELSE '변경 후 · 32' END AS condition,
 AVG(mean_seconds) AS mean, AVG(p95_seconds) AS trial_p95_mean,
 AVG(cpu_seconds_per_user) AS cpu, AVG(queue_seconds_per_user) AS queue, COUNT(*) AS samples
 FROM main.performance_trials
 WHERE measurement_set IN ('service-throughput-baseline5-v3-v3-20261003',
 'service-throughput-final5-final-20261003','service-throughput-final5-repeat-final-20261003')
 AND delay_ms=5000 GROUP BY users,condition ORDER BY users,condition;
