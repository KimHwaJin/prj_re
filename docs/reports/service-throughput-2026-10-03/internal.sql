SELECT CASE WHEN measurement_set LIKE '%stable-shield0-final%' THEN '안정화 대조군 / cache0'
 WHEN measurement_set LIKE '%cache-shield0-final%' THEN 'SQL cache100 추가' ELSE '현재 이벤트 batch 추가 · 최종' END AS condition,
 COUNT(*) AS repeats, AVG(mean_seconds) AS mean, AVG(cpu_seconds_per_user) AS cpu,
 AVG(sql_per_user) AS sql, AVG(worker_advisory_locks_per_user) AS locks
 FROM main.performance_trials
 WHERE measurement_set IN ('service-throughput-stable-shield0-final-20261003',
 'service-throughput-cache-shield0-final-20261003','service-throughput-shield0-20261003')
 AND users=50 AND concurrency=16 AND delay_ms=0 GROUP BY condition ORDER BY mean;
