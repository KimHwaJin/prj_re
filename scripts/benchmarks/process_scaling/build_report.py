"""Build a portable report manifest from validated process-scaling measurements."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('results',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
results=json.loads(a.results.read_text());a.output.mkdir(parents=True,exist_ok=True)
import sqlite3
layouts=['1×4','1×8','2×4','1×16','2×8','4×4']
flat=[]
for r in results:
 flat.append(dict(repeat=r['repeat'],layout=r['layout'],users=r['users'],processes=r['processes'],concurrency=r['concurrency'],slots=r['total_slots'],source_commit=r['source_commit'],
  mean_s=r['per_user']['total_s']['mean'],p95_s=r['per_user']['total_s']['p95'],queue_s=r['per_user']['queue_s']['mean'],
  llm_s=r['per_user']['llm_s']['mean'],internal_s=r['per_user']['internal_s']['mean'],throughput=r['throughput_users_s'],
  peak_runs=r['simultaneous_peak_workers'],cpu_s_user=r['worker_cpu_seconds_per_user'],cpu_cores=r['worker_mean_cpu_cores'],rss_mib=r['api_rss_peak_mib'],db_peak=r['db_connections']['max'],
  pool_p95_ms=r['pool_acquire_ms']['p95'],lag_p95_ms=r['event_loop_lag_ms']['p95'],lock_peak=r['db_lock_waiters']['max'],
  idle_tx_peak=r['db_idle_in_transaction']['max'],sql_p95_ms=r['sql_execution_ms']['p95']))
query="""SELECT layout, users, CAST(CAST(users AS INTEGER) AS TEXT) || '명' AS cohort,
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
 ORDER BY slots, processes, users"""
with sqlite3.connect(':memory:') as db:
 db.row_factory=sqlite3.Row
 fields=list(flat[0]);db.execute('CREATE TABLE benchmark_trials ('+', '.join(f+' '+('TEXT' if f in ('layout','source_commit') else 'REAL') for f in fields)+')')
 db.executemany('INSERT INTO benchmark_trials VALUES ('+','.join('?' for _ in fields)+')',[[row[f] for f in fields] for row in flat])
 rows=[dict(row) for row in db.execute(query)]
for row in rows:
 for field in ('users','repeats','processes','concurrency','slots'):row[field]=int(row[field])
 rows.sort(key=lambda x:(layouts.index(x['layout']),x['users']))
assert {(r['layout'],r['users']) for r in rows}=={(layout,n) for layout in layouts for n in (10,30,50)},'Complete planned matrix required'
(a.output/'aggregation.sql').write_text(query+';\n')
(a.output/'trial-metrics.json').write_text(json.dumps(flat,ensure_ascii=False,indent=2)+'\n')
lookup={(r['layout'],r['users']):r for r in rows}
best=min([lookup[(layout,50)] for layout in ['1×16','2×8','4×4']],key=lambda r:r['mean_s'])
one=lookup[('1×16',50)];base=lookup[('1×4',50)];multi=lookup[('4×4',50)]
reduction=(1-one['mean_s']/base['mean_s'])*100
multi_gain=(1-multi['mean_s']/one['mean_s'])*100
source={'id':'measurements','label':'고정 소스의 실제 API·Worker·PostgreSQL 부하 측정','path':'aggregation.sql','query':{
 'engine':'sqlite','language':'sql','sql':query,'description':'실제 HTTP/SSE 시나리오 완료 시간, Run 생성/시작 시각, 프로세스별 계측 및 pg_stat_activity 표본을 검산·집계한다.',
 'tables_used':['benchmark_trials'],
 'filters':['source=c3534f0','LLM mock=5 seconds per call, 4 calls per user','Workflow approval waiting; no Executor submission','users=10,30,50'],
 'metric_definitions':['mean_s: 사용자별 세션 생성부터 Workflow 승인 대기까지 시간의 평균. 반복이 있으면 trial 평균의 산술평균.',
 'p95_s: 각 trial의 사용자 완료 시간 nearest-rank p95; 반복 간에는 해당 p95들의 평균이며 pooled p95가 아니다.',
 'queue_s: 사용자별 4개 Run의 created_at→started_at 시간 합계의 평균.',
 'throughput: trial의 완료 사용자 수 / 마지막 사용자 완료까지 경과 초. 지속 유입 처리 한계가 아니다.',
 'rss_mib: 같은 표본 시점의 worker+supervisor RSS 합의 trial별 최고치를 반복 간 평균. MiB, 약 1초 간격; instrumentation 포함.',
 'cpu_s_user: 모든 API worker CPU process_time 합 / 완료 사용자 수. supervisor·DB·LLM mock CPU 제외.',
 'db_peak: 전용 DB client backend 수의 trial별 최고 표본을 반복 간 평균; 계측용 연결 제외, active/idle 포함.',
 'pool_p95_ms: SQLAlchemy pool 획득+연결 확인 지연 p95. 순수 queue 대기만은 아니다.'],
 'executed_at':datetime.now(timezone.utc).isoformat()}}
charts=[];tables=[];blocks=[]
def md(id,title,body,source_id=True):
 blocks.append(dict(id=id,type='markdown',body=f'## {title}\n\n{body}',**({'sourceId':'measurements'} if source_id else {})))
def chart(id,title,dataset,y,label,group=False):
 enc={'x':{'field':'layout','type':'nominal','label':'프로세스 × 컨커런시'},'y':{'field':y,'type':'quantitative','label':label}}
 if group:enc['color']={'field':'cohort','type':'nominal','label':'동시 사용자'}
 charts.append(dict(id=id,title=title,subtitle='API 1 Pod 상당의 로컬 프로세스 집합 · LLM 5초 × 4회 · 각 값의 반복 수는 표에 표시',type='bar',dataset=dataset,sourceId='measurements',encodings=enc))
 blocks.append(dict(id=id+'-block',type='chart',chartId=id))
def table(id,title,dataset,columns,sort='users'):
 tables.append(dict(id=id,title=title,dataset=dataset,sourceId='measurements',defaultSort={'field':sort,'direction':'asc'},columns=[{'field':f,'label':label,**({'type':'text'} if f in ('layout','cohort') else {'format':'number'})} for f,label in columns]))
 blocks.append(dict(id=id+'-block',type='table',tableId=id))
title='API 프로세스와 Run 컨커런시 비교 — 2026년 9월 30일'
blocks.append(dict(id='title',type='markdown',body='# '+title))
md('summary','실행 자리와 프로세스 비용을 분리해서 판단',f"50명에서 1×4의 평균 완료 시간은 {base['mean_s']:.2f}초, 1×16은 {one['mean_s']:.2f}초로 {reduction:.1f}% 짧아졌다. 동일 16자리의 평균 완료 시간 최저 조합은 {best['layout']}({best['mean_s']:.2f}초)다. 다만 한두 차례의 로컬 측정에서 작은 차이를 확정 순위로 해석하지 않는다.\n\n4×4의 평균 시간은 1×16보다 {multi_gain:.1f}% 짧았다. 1×16의 API 메모리 최고 표본 평균은 {one['rss_mib']:.0f} MiB, 4×4는 {multi['rss_mib']:.0f} MiB였다. DB 연결 최고 표본 평균은 각각 {one['db_peak']:.0f}개와 {multi['db_peak']:.0f}개였다. 이번 mock 조건에서는 1프로세스에서 컨커런시를 늘리는 방식을 자원 효율 우선 후보로 삼을 근거가 있다. 최대 속도가 중요할 때는 다중 프로세스의 추가 비용도 함께 판단해야 한다. 실제 LLM 한도 및 Kubernetes CPU/메모리 제한을 반영한 운영 권장값 확정은 이번 결과의 범위 밖이다.")
md('definitions','표의 숫자는 무엇을 의미하는가','1×16은 Uvicorn 프로세스 1개에서 서로 다른 세션의 Run을 최대 16개 동시 처리한다는 뜻이다. 전체 완료 시간은 세션 생성부터 3회 resume를 거쳐 Workflow 승인 대기에 도달할 때까지이며, 사용자별 LLM 호출은 4회다. 10·30·50명이 동시에 시작한다.\n\n평균과 p95의 단위는 초, 처리량은 완료 사용자/초다. CPU는 API worker만, RSS는 worker와 supervisor를 합산한다. SQL pool/loop 지표는 밀리초다. CPU·메모리·DB 측정에는 계측 오버헤드가 포함된다. 각 조건의 반복 수를 표에 공개한다.')
md('latency','컨커런시를 늘렸을 때의 완료 시간','같은 1프로세스에서 4→8→16자리를 비교하면 프로세스 복제 없이 실행 대기를 줄이는 효과를 볼 수 있다. 아래 막대는 사용자 수별 평균 완료 시간이며, 실제 모델의 동시 요청 제한이 없는 5초 mock 조건이다. 변화량을 실제 LLM 제공사의 수용량으로 일반화하지 않는다.')
chart('latency-chart','사용자 수별 평균 완료 시간','matrix','mean_s','초',True)
md('decomposition','50명에서 줄어든 시간은 대부분 큐 대기다',f"1×4에서 사용자당 큐 대기 합계는 {base['queue_s']:.2f}초였고 1×16에서는 {one['queue_s']:.2f}초였다. LLM 호출 합계는 각각 {base['llm_s']:.2f}초와 {one['llm_s']:.2f}초로 거의 같다. 즉 이번 차이의 주된 원인은 모델 응답 자체가 아니라 동시에 실행 가능한 자리를 늘려 줄어든 대기다. 4×4에서는 큐 대기가 {multi['queue_s']:.2f}초였으나, 총 풀 크기도 함께 늘었으므로 프로세스 수 하나만의 순수 효과로 분리하지 않는다.")
table('latency-table','전체 조건의 완료 시간과 큐 대기','matrix',[('layout','프로세스×컨커런시'),('users','사용자'),('repeats','반복'),('mean_s','평균 초'),('p95_s','p95 초'),('queue_s','큐 대기 초'),('throughput','완료/초')])
md('equal-slots','같은 실행 자리에서도 프로세스 비용은 달라진다','8자리는 1×8과 2×4, 16자리는 1×16·2×8·4×4를 비교한다. 다중 프로세스는 별도 Python 런타임·Agent 그래프·DB 풀을 가진다. 다음 표는 가장 높은 부하인 50명의 결과다. 총 DB pool 예산을 고정한 비교가 아니라 프로세스당 같은 설정을 복제한 배포 비교라는 점에 유의해야 한다.')
table('equal-table','50명 조건: 같은 자리 수의 시간·CPU·메모리','heavy',[('layout','프로세스×컨커런시'),('slots','총 자리'),('peak_runs','실제 동시 실행 최고'),('repeats','반복'),('mean_s','평균 초'),('throughput','완료/초'),('cpu_s_user','CPU 초/사용자'),('rss_mib','RSS 최고 평균 MiB')],sort='slots')
md('repeat-check','반복 측정으로 본 작은 차이', '50명·총 16자리 조건은 순서를 뒤집어 2회 측정했다. 각 실행의 평균을 아래에 그대로 공개한다. 2×8과 4×4의 시간 순위는 반복에 따라 바뀌었다. 따라서 이 둘의 우열을 확정하기보다 추가 프로세스의 메모리와 DB 비용을 함께 판단한다.')
table('repeat-table','50명·총 16자리의 개별 실행', 'repeated', [('layout','프로세스×컨커런시'),('repeat','반복 번호'),('mean_s','평균 초'),('p95_s','p95 초'),('rss_mib','RSS 최고 MiB'),('db_peak','DB 최고')],sort='repeat')
md('memory','메모리와 DB 연결은 별도 비용이다','프로세스 수를 늘리면 동일한 서비스를 위한 상주 메모리와 독립 풀이 복제된다. 아래 그래프는 50명 조건에서 약 1초 간격으로 측정한 worker+supervisor RSS 합의 최고값이다. 이는 계측 버퍼를 포함한 표본 최고치이며 순간 최대 또는 Kubernetes 메모리 limit 권장값이 아니다.')
chart('memory-chart','50명 조건의 API RSS 최고 표본','heavy','rss_mib','MiB')
md('database','DB 풀과 잠금 경합을 함께 확인','프로세스당 service pool 10/overflow 0, checkpoint 최대 4, bridge 4를 유지했다. 프로세스가 4개면 service pool 상한만 40개가 된다. 아래 DB 연결 수는 전용 DB의 idle/active 연결을 모두 포함하며 계측용 연결은 제외한다. 짧은 lock 대기는 표본에서 누락될 수 있으므로 SQL 실행 지연·pool 획득 지연도 함께 제시한다.')
table('database-table','50명 조건의 DB·이벤트 루프 지표','heavy',[('layout','프로세스×컨커런시'),('db_peak','DB 최고 표본 평균'),('pool_p95_ms','pool p95 ms'),('sql_p95_ms','SQL p95 ms'),('lag_p95_ms','loop p95 ms'),('lock_peak','lock 대기 최고'),('idle_tx_peak','idle tx 최고')],sort='db_peak')
md('methods','측정 설계와 검산','서비스 소스는 c3534f0으로 고정하고 git archive로 독립 복사했다. 실제 Uvicorn shared socket worker들이 같은 PostgreSQL 큐와 checkpoint를 공유한다. 프로세스별 별도 localhost 제어 채널에서 계측을 직접 수집해 한 프로세스의 지표만 전체로 오인하지 않도록 했다.\n\n각 조건마다 DB와 API/모델 프로세스를 새로 시작한다. 사용자 등록은 측정 밖, 세션 생성과 Agent/풀 초기화는 측정 안이다. 단계 간 think time은 0.2초이며 상태 전달은 SSE다. mock Executor도 호출하지 않는다. source ref, Run·모델·stage 개수, 실제 동시 실행 상한, DB의 recovery/소유권 상태, 메시지·로그 건수와 프로세스 종료 상태를 검산했다.')
md('limits','이 결과로 아직 확정할 수 없는 것','로컬 macOS 개발 머신(14 CPU, RAM 24 GiB)과 Docker PostgreSQL(14 CPU, 메모리 약 15.6 GiB)이며 Pod CPU quota·memory limit을 적용하지 않았다. 레포 예제 deployment에는 2 CPU/4 GiB limit이 있지만 실제 플랫폼 CI/CD 템플릿의 값은 미확정이므로 적용 환경과 같다고 보지 않았다. 다른 로컬 서비스와 자원을 공유한다.\n\n주 비교는 조건당 1회이며 추가 반복 조건은 표의 반복 수로 표시한다. 지속 유입·다양한 prompt 길이·실제 LLM rate limit·장기 Executor 작업·실제 공유 PV 지연은 포함하지 않았다. p95는 작은 표본에서 불안정하고, 반복이 있으면 trial별 p95의 평균이다. CPU는 API worker CPU 합만 측정하고 DB·mock·supervisor CPU는 포함하지 않는다. RSS에는 Python 계측 목록이 포함되고, 프로세스 간 공유 페이지가 중복 합산될 수 있어 cgroup working set과 같지 않다. 같은 자릿수에서의 작은 차이는 우열 확정 근거가 아니다.')
md('next','다음 적용 판단','평균 완료 시간뿐 아니라 CPU 초/사용자·메모리·연결 비용이 낮은 조합을 배포 후보로 삼는다. 16은 이번에 시험한 컨커런시의 상한이며 최적값이나 시스템 한계로 확정한 숫자가 아니다. 이번 결과만으로 운영 프로세스 수나 컨커런시를 자동 변경하지 않는다. 확정할 Kubernetes CPU/메모리 제한과 실제 LLM 동시 요청 수용량을 맞춘 뒤 후보를 재검증해야 한다.\n\n사용자 우선순위에 따라 다음 단계는 새 Agent 흐름 검토다. 모델 호출 수와 실행 단위가 바뀌면 이번 용량 측정도 다시 확인한다.')
md('questions','배포 전 남은 질문','Pod의 CPU request/limit과 memory limit은 얼마인가? 실제 LLM이 안정적으로 받는 동시 요청 수는 몇 개인가? 모델 호출이 없는 resume 구간과 호출이 있는 구간을 분리할 필요가 있는가? 플랫폼 scale-out 때 총 DB 연결 수용량을 어떤 범위까지 확보할 수 있는가?',False)
now=datetime.now(timezone.utc).isoformat()
artifact={'surface':'report','manifest':{'version':1,'surface':'report','title':title,'description':'고정된 서비스 코드에서 처리량·지연·자원 비용을 비교한 기술 보고서','generatedAt':now,'blocks':blocks,'charts':charts,'tables':tables,'cards':[],'sources':[source]},'snapshot':{'version':1,'generatedAt':now,'status':'ready','datasets':{'matrix':rows,'heavy':[r for r in rows if r['users']==50],'repeated':[r for r in flat if r['users']==50 and r['slots']==16]},'accessIssues':[]},'sources':[source]}
(a.output/'artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
(a.output/'table.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
(a.output/'report-source-notes.md').write_text('''# Report design notes

Audience: technical. Delivery: portable HTML for local Codex repository workflow. All required sections mapped to title/summary, findings with charts, definitions, methods, limitations, next steps and questions. Primary evidence is validated results.json and raw gzip with source hashes. trial-metrics.json flattens per-trial metrics; aggregation.sql actually computes repeated-trial means in an in-memory SQLite benchmark_trials table. The exact SQL is exposed as chart/table provenance; the preceding Python timing calculations remain in analyze.py. Work record remains the repository improvement log, not a second report surface.

Chart contracts: latency grouped bar (layout × user cohort, seconds, zero baseline) supports concurrency and matched-slot comparison; memory bar (50-user layout, MiB, zero baseline) supports process replication cost. Both use bar because both compare discrete configurations; no time trend or sparse scatter is implied. Exact p95/resources/DB measures use tables for numeric lookup. Chart interpretations and limitations are adjacent markdown blocks. No chart infers production capacity. HTML verification receipt is retained.

Timing means and p95s average trial summaries equally where repeats exist; p95 is explicitly not pooled. Counts, trial order, sample sizes and repeat-level raw results remain in supporting JSON. Cold starts are included; service code is unchanged. db_connections includes all client connections to the scratch DB except the resource collector. RSS is sampled worker+supervisor sum and includes instrumentation, not an independent production memory measurement.
''')
print(a.output/'artifact.json')
