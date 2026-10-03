SELECT CASE WHEN capture_set IN ('baseline5','baseline-repeat') THEN 'before' ELSE 'after' END AS phase,
 users, slots, count(*) AS repeats, avg(mean_seconds) AS mean_seconds,
 min(p95_seconds) AS p95_min, max(p95_seconds) AS p95_max,
 avg(batch_seconds) AS batch_seconds, avg(cpu_seconds) AS cpu_seconds,
 avg(crud_sql) AS crud_sql, avg(event_sql) AS event_sql, avg(event_wait_ms) AS event_wait_ms,
 avg(event_handler_ms) AS event_handler_ms, avg(queue_ms) AS queue_ms, avg(loop_p95_ms) AS loop_p95_ms
FROM trials WHERE capture_set IN ('baseline5','baseline-repeat','common5','common-repeat')
GROUP BY phase, users, slots ORDER BY users, slots, phase;
