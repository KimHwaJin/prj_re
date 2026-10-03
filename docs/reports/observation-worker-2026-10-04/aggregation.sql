SELECT variant, profile, users, COUNT(*) AS repeats,
 AVG(mean_seconds) AS mean_seconds, MIN(mean_seconds) AS mean_min, MAX(mean_seconds) AS mean_max,
 AVG(p95_seconds) AS p95_trial_mean, MIN(p95_seconds) AS p95_min, MAX(p95_seconds) AS p95_max,
 AVG(makespan_seconds) AS makespan_seconds, AVG(batch_users_per_second) AS batch_users_per_second,
 AVG(user_queue_mean_ms) AS user_queue_mean_ms, AVG(event_queue_mean_ms) AS event_queue_mean_ms,
 AVG(api_cpu_per_user_seconds) AS cpu_per_user_seconds, AVG(crud_sql_per_user) AS crud_sql_per_user,
 AVG(saver_write_outer_ms_per_user) AS saver_write_ms_per_user,
 AVG(saver_read_ms_per_user) AS saver_read_ms_per_user, AVG(column_payload_mib_per_user) AS column_payload_mib_per_user,
 AVG(serialization_ms_per_user) AS serialization_ms_per_user,
 AVG(logical_mib_per_user) AS logical_mib_per_user, AVG(observation_write_mib_per_user) AS observation_write_mib_per_user,
 AVG(checkpoints_per_user) AS checkpoints_per_user, AVG(defer_attempts) AS defer_attempts
 FROM trials GROUP BY variant, profile, users ORDER BY profile, users, variant;
