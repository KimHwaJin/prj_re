SELECT scenario, users, architecture, count(*) AS repeats,
 avg(mean_seconds) AS mean_seconds, min(mean_seconds) AS mean_min, max(mean_seconds) AS mean_max,
 avg(p95_seconds) AS trial_p95_mean, min(p95_seconds) AS p95_min, max(p95_seconds) AS p95_max,
 avg(makespan_seconds) AS makespan_seconds, avg(batch_users_per_second) AS batch_users_per_second,
 avg(api_cpu_per_user_seconds) AS cpu_per_user_seconds, avg(crud_sql_per_user) AS crud_sql_per_user,
 avg(user_queue_mean_ms) AS user_queue_mean_ms, avg(event_publish_to_start_mean_ms) AS event_wait_mean_ms,
 avg(event_publish_to_start_p95_ms) AS event_wait_p95_ms, avg(empty_claims) AS empty_claims,
 avg(shared_peak) AS shared_peak, avg(slot_utilization) AS slot_utilization,
 avg(pool_acquire_p95_ms) AS pool_acquire_p95_ms, avg(loop_lag_p95_ms) AS loop_lag_p95_ms,
 avg(rss_peak_mib) AS rss_peak_mib, max(db_connections_peak) AS db_connections_peak,
 sum(event_defer_attempts) AS event_defer_attempts
 FROM trials WHERE followup=0 AND notify='on'
 GROUP BY scenario,users,architecture ORDER BY scenario,users,architecture;
