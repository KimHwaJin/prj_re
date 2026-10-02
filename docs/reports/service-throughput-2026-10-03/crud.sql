SELECT b.users,b.mean_seconds AS before,a.mean_seconds AS after,
 b.batch_seconds AS before_batch,a.batch_seconds AS after_batch
 FROM main.performance_trials b JOIN main.performance_trials a ON b.users=a.users
 WHERE b.measurement_set='service-throughput-crud-stable-final-20261003'
 AND a.measurement_set='service-throughput-crud-final-final-20261003' ORDER BY b.users;
