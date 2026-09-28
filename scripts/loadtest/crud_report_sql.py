"""Independent SQLite aggregation of raw ramp evidence for report provenance."""
import json
from pathlib import Path
import sqlite3


def build_sql_evidence(root, stages, resources, memory_mib):
    db=sqlite3.connect(root/'evidence.sqlite')
    db.row_factory=sqlite3.Row
    db.executescript('''
      DROP TABLE IF EXISTS http_stats;
      DROP TABLE IF EXISTS container_samples;
      DROP TABLE IF EXISTS database_samples;
      CREATE TABLE http_stats(users INTEGER, seconds REAL, endpoint TEXT, requests INTEGER, errors INTEGER,
        avg_ms REAL, p50_ms REAL, p95_ms REAL, p99_ms REAL, max_ms REAL);
      CREATE TABLE container_samples(users INTEGER, sample_at TEXT, service TEXT, cpu_pct REAL, memory_mib REAL);
      CREATE TABLE database_samples(users INTEGER, sample_at TEXT, connections INTEGER, active INTEGER, lock_waits INTEGER, idle_tx INTEGER);
    ''')
    for s in stages:
        for r in s['stats']['stats']:
            if r['method']!='POST' or r['name'] not in ('/api/v1/projects','/api/v1/projects/:id/sessions'):continue
            endpoint='프로젝트 생성' if r['name']=='/api/v1/projects' else '세션 생성'
            db.execute('INSERT INTO http_stats VALUES(?,?,?,?,?,?,?,?,?,?)',(s['users'],s['elapsed_seconds'],endpoint,r['num_requests'],r['num_failures'],r['avg_response_time'],r['median_response_time'],r['response_time_percentile_0.95'],r['response_time_percentile_0.99'],r['max_response_time']))
        for r in resources:
            if r['users']!=s['users'] or r['phase']!='measure' or 'error' in r or not s['started_at']<=r['at']<=s['finished_at']:continue
            d=r['database'];db.execute('INSERT INTO database_samples VALUES(?,?,?,?,?,?)',(s['users'],r['at'],d['connections'],d['active'],d['lock_waits'],d['idle_in_transaction']))
            for c in r['containers']:
                for service,label in [('api','API'),('postgres','PostgreSQL'),('locust','Locust')]:
                    if c['Name']==f'dtest-agent-loadtest-{service}-1':
                        db.execute('INSERT INTO container_samples VALUES(?,?,?,?,?)',(s['users'],r['at'],label,float(c['CPUPerc'].rstrip('%')),memory_mib(c['MemUsage'])))
    queries={
      'stages':'''SELECT users, CAST(users AS TEXT)||'명' AS user_label,
        SUM(requests) AS requests, SUM(errors) AS errors, ROUND(MAX(seconds),3) AS seconds,
        ROUND(SUM(requests)/MAX(seconds),3) AS rps,
        SUM(errors)*1.0/SUM(requests) AS failure_rate,
        MAX(CASE WHEN endpoint='프로젝트 생성' THEN requests END) AS project_requests,
        MAX(CASE WHEN endpoint='세션 생성' THEN requests END) AS session_requests,
        MAX(CASE WHEN endpoint='프로젝트 생성' THEN p95_ms END) AS project_p95_ms,
        MAX(CASE WHEN endpoint='세션 생성' THEN p95_ms END) AS session_p95_ms,
        ROUND(SUM(avg_ms*requests)/SUM(requests),3) AS http_avg_ms
        FROM http_stats GROUP BY users ORDER BY users''',
      'endpoints':'''SELECT users, CAST(users AS TEXT)||'명' AS user_label, endpoint, requests, errors,
        ROUND(avg_ms,3) AS avg_ms, p50_ms, p95_ms, p99_ms, max_ms
        FROM http_stats ORDER BY users, endpoint''',
      'cpu':'''SELECT users, CAST(users AS TEXT)||'명' AS user_label, service,
        ROUND(AVG(cpu_pct),2) AS cpu_avg_pct, MAX(cpu_pct) AS cpu_max_pct, COUNT(*) AS samples
        FROM container_samples GROUP BY users,service ORDER BY users,service''',
      'resources':'''WITH d AS (
        SELECT users, COUNT(*) AS samples, MAX(connections) AS db_connections_max,
          MAX(active) AS db_active_max, SUM(CASE WHEN lock_waits>0 THEN 1 ELSE 0 END) AS lock_wait_samples,
          MAX(idle_tx) AS idle_in_transaction_max
        FROM database_samples GROUP BY users
      ), c AS (
        SELECT users, MAX(CASE WHEN service='API' THEN cpu_pct END) AS api_cpu_max,
          MAX(CASE WHEN service='PostgreSQL' THEN cpu_pct END) AS postgres_cpu_max,
          ROUND(MAX(CASE WHEN service='API' THEN memory_mib END),2) AS api_memory_max_mib
        FROM container_samples GROUP BY users
      ) SELECT d.*, c.api_cpu_max,c.postgres_cpu_max,c.api_memory_max_mib
        FROM d JOIN c USING(users) ORDER BY users'''
    }
    result={};sources=[]
    for dataset,query in queries.items():
        result[dataset]=[dict(row) for row in db.execute(query).fetchall()]
        path=root/f'{dataset}.sql';path.write_text(query+';\n')
        sources.append({'id':dataset+'_sql','label':dataset+' · SQLite 원시 관측 집계','path':str(path),
            'query':{'engine':'SQLite','language':'sql','sql':query,'description':'Locust 원시 HTTP 통계 및 측정 구간의 Docker·DB 관측을 정규화한 evidence.sqlite에서 집계.',
                     'tables_used':['http_stats'] if dataset in ('stages','endpoints') else ['container_samples','database_samples'],
                     'filters':['프로젝트/세션 생성 POST만 포함','목표 인원 도달 후 안정화 15초 제외','자원은 단계 측정 시작~종료 사이 표본만 포함']}})
    db.commit();db.close()
    return result,sources
