"""Build the canonical portable report from validated comparison summaries."""
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
rows=json.loads((a.data/'summary.json').read_text())
comparisons={r['name']:r for r in json.loads((a.data/'comparisons.json').read_text())}
stages=json.loads((a.data/'stage-breakdown.json').read_text())
validation=json.loads((a.data/'validation.json').read_text())
R=lambda name,label:next(r for r in rows if r['name']==name and r['label']==label)
f=lambda x,d=1:'—' if x is None else f'{x:,.{d}f}'
sec=lambda x:f(x/1000,2) if x is not None else '—'
reduction=lambda before,after:(before-after)/before*100 if before else 0
m0,m1=R('mixed-100','before'),R('mixed-100','after')
w0,w1=R('flow-100','before'),R('flow-100','after')
t0,t1=R('tight-slow10','before'),R('tight-slow10','after')
c0,c1=R('crud-100','before'),R('crud-100','after')
hold_gain=reduction(m0['worker_hold_seconds_per_run'],m1['worker_hold_seconds_per_run'])
crud_gain=reduction(m0['background_crud_server_p95_ms'],m1['background_crud_server_p95_ms'])
flow_gain=reduction(w0['scenario_mean_ms'],w1['scenario_mean_ms'])
source={'id':'measurements','label':'격리된 로컬 HTTP·PostgreSQL A/B 측정 및 재현 가능한 집계','path':'data/summary.json',
 'query':{'engine':'python','language':'python','description':'scripts/benchmarks/analyze_db_scope.py로 raw-trials.jsonl.gz의 요청·풀·Run 실측을 집계. 예비 측정 제외.',
 'tables_used':['public.agent_runs','public.tasks','public.session_executions','public.messages','public.agent_run_logs'],
 'filters':['before=64ad96f; after=4bb5c2f','조건별 AB/BA 2회','성공 응답 분위수와 실패 수 분리','실제 외부 LLM·Executor 호출 없음'],
 'metric_definitions':['p95/p99=성공 요청의 pooled nearest-rank percentile','Worker connection-seconds/Run=worker checkout→checkin 누적초 / Run 행 수','queue_ms=started_at-created_at','execution_ms=(completed_at 또는 interrupted updated_at)-started_at','완료율=완료 시나리오 / 시도 시나리오']}}
code_source={'id':'code','label':'016 변경 직전/직후 소스 비교','path':'sources/code-diff.patch'}
source['query']['sql']='''-- Actual db_evidence() queries. These retrieve Run/status evidence only.
-- HTTP/pool/model timers come from db_scope_server.py instrumentation;
-- analyze_db_scope.py combines both sources. SQL alone does not produce timing percentiles.
SELECT run_id,session_id,status,created_at,started_at,completed_at,updated_at,attempt_count,failure FROM agent_runs ORDER BY created_at;
SELECT status,count(*) FROM agent_runs GROUP BY status;
SELECT count(*) FROM tasks WHERE recovery_required;
SELECT count(*) FROM session_executions WHERE token IS NOT NULL;
SELECT count(*) FROM messages;
SELECT count(*) FROM agent_run_logs;'''
source['query']['engine']='PostgreSQL + Python instrumentation'
source['query']['language']='SQL + Python'
now=datetime.now(timezone.utc).isoformat()
title='Agent DB 연결 수명 개선 — 직전·현재 버전 부하 비교'
manifest={'version':1,'surface':'report','title':title,'generatedAt':now,'description':'응답시간·연결 점유량·큐 대기·오류를 구분한 기술 검토','blocks':[],'charts':[],'tables':[],'cards':[],'sources':[source,code_source]}
datasets={}
def md(id,body,src='measurements'):
 b={'id':id,'type':'markdown','body':body}
 if src:b['sourceId']=src
 manifest['blocks'].append(b)
def chart(id,title,ds,x,y,unit):
 manifest['charts'].append({'id':id,'title':title,'type':'bar','dataset':ds,'sourceId':'measurements','layout':'full',
 'encodings':{'x':{'field':x,'type':'ordinal','label':'비교 조건'},'y':{'field':y,'type':'quantitative','label':unit},'color':{'field':'version','type':'nominal','label':'버전'}},'yAxisTitle':unit,'valueFormat':'number'})
 manifest['blocks'].append({'id':id+'-block','type':'chart','chartId':id,'layout':'full'})
def table(id,title,data,columns,sort):
 datasets[id]=data
 manifest['tables'].append({'id':id,'title':title,'dataset':id,'sourceId':'measurements','layout':'full','density':'dense',
 'defaultSort':{'field':sort,'direction':'asc'},'columns':[{'field':k,'label':label,'type':typ} for k,label,typ in columns]})
 manifest['blocks'].append({'id':id+'-block','type':'table','tableId':id,'layout':'full'})
md('title','# '+title,None)
md('summary',f'''## 결론: 연결 낭비는 줄었고, 전체 처리속도는 별도로 봐야 한다

**이번 변경의 직접적인 성과는 LLM을 기다리는 동안 서비스 DB 연결을 계속 점유하던 경로를 제거한 것이다.** 100명 초기 Agent·CRUD 혼합 조건에서 Worker의 Run당 연결 점유량은 **{f(m0['worker_hold_seconds_per_run'],3)} → {f(m1['worker_hold_seconds_per_run'],3)} connection-seconds**, **{f(hold_gain)}% 감소**했다. 같은 조건의 백그라운드 CRUD 서버 p95는 **{f(m0['background_crud_server_p95_ms'])} → {f(m1['background_crud_server_p95_ms'])}ms**다.

반면 전체 4단계 HITL 시나리오 평균은 **{sec(w0['scenario_mean_ms'])} → {sec(w1['scenario_mean_ms'])}초**다. 모델 대기·실행 자리 4개에 대한 큐 대기·폴링 발견 지연은 이 변경으로 없어지지 않는다. 따라서 연결 점유 감소율을 그대로 처리량 향상률이라고 말할 수 없다.

연결을 4개로 제한하고 LLM 1회 대기를 5초로 늘린 민감도 실험에서는 **직전 {t0['complete']}/{t0['attempted']}건, 현재 {t1['complete']}/{t1['attempted']}건 완료**했다. 이 조건은 운영 권장값이 아니라 연결 고갈 시 동작을 비교하기 위한 경계 실험이다. 아래에 실패·미완료와 정상 조건을 분리했다.

최종 분석은 **{validation['trials']}회 실행, {validation['scenario_attempts']:,}개 시나리오, {validation['http_requests']:,}건 HTTP 요청**을 포함한다. 실제 Kubernetes/실제 LLM 용량 검증은 아니다. 과거 다른 조건에서 측정한 17초 등의 수치와 직접 비교하지 않는다.''')
md('scope','''## 무엇을 같은 조건으로 비교했나

비교 대상은 **64ad96f → 4bb5c2f**, 즉 전체 리팩터링 이전이 아니라 **DB 트랜잭션 수명 분리 한 항목의 전후**다. 두 버전 모두 이미 동시 실행·세션 소유권·종료 보호 기능을 가진 상태다.

- 각 사용자 수 1·10·30·50·100명에서 별도 초기화한 DB·신규 단일 서버 프로세스로 실행했다. 조건별 before→after / after→before 두 번씩 측정했다.
- 기본은 실행 자리 4개, 서비스 DB 연결 5개, overflow 0이다. 별도 PostgreSQL checkpoint 풀은 최대 4개다. 기존 배포 설정은 변경하지 않았다.
- 실제 HTTP, FastAPI, API 인증·CRUD, PostgreSQL Run queue, Worker, 분석 그래프, 체크포인트, HITL을 실행했다. 기존 LLM Mock만 사용했고 Executor는 호출하지 않았다. Redis 소비자·별도 Workflow 카탈로그 DB 저장·산출물 생성은 제외했다.
- 초기 혼합 부하는 LLM 1초/회, 전체 HITL은 0.1초/회다. 혼합 조건은 추가로 GET 프로젝트·POST 세션을 초당 20건(50:50) 제출했다.
- 상태조회는 0.5초 간격, HITL 응답 전에는 0.2초 대기했다. 사용자는 독립 계정·독립 HTTP 풀을 사용한다. 한 번의 동시 burst이며 장시간 반복 운영을 재현한 것은 아니다.

**p95는 성공 요청 100개 중 약 95개가 이 시간 이내에 끝났다는 뜻이다.** 실패 수는 따로 표시한다. **connection-seconds는 연결을 빌린 시간의 합**이다. 예를 들어 연결 4개를 각각 1초 빌리면 4 connection-seconds이며 DB CPU 4초를 뜻하지 않는다. 아래 지표는 서비스 DB 풀 기준이고 checkpoint 풀은 별도다.''')
mixed=[r for r in rows if r['name'].startswith('mixed-')]
datasets['mixed']=[{'worker_hold_seconds_per_run':r['worker_hold_seconds_per_run'],'background_crud_server_p95_ms':r['background_crud_server_p95_ms'],'users_label':str(r['users'])+'명','version':'직전' if r['label']=='before' else '현재'} for r in sorted(mixed,key=lambda x:(x['users'],0 if x['label']=='before' else 1))]
md('connection',f'''## 연결 점유 감소가 가장 명확한 개선이다

100명에서 Run당 Worker 연결 점유량은 **{f(m0['worker_hold_seconds_per_run'],3)} → {f(m1['worker_hold_seconds_per_run'],3)}초**다. 그래프 처리에 불필요했던 모델 대기 구간이 빠졌다. 전체 서비스 풀의 반복당 누적 점유량도 **{f(m0['db_hold_seconds_per_trial'])} → {f(m1['db_hold_seconds_per_trial'])} connection-seconds**로 바뀌었다.

아래는 사용자 수별 Run당 점유량이다. Worker 지표에는 큐 조회·취소 감시·heartbeat도 포함한다. 현재도 DB 기록과 감시는 계속 수행하므로 0이 되어야 하는 값은 아니다. 물리 연결을 매번 새로 만드는 것이 아니라 기존 공용 풀에 빨리 반환한다.''')
chart('connection-comparison','초기 혼합 부하의 Run당 서비스 DB 연결 점유','mixed','users_label','worker_hold_seconds_per_run','connection-seconds / Run')
md('api',f'''## 같은 Agent 대기 중에 CRUD가 쓸 수 있는 연결이 늘어난다

100명 초기 혼합 조건의 백그라운드 CRUD 서버 p95는 **{f(m0['background_crud_server_p95_ms'])} → {f(m1['background_crud_server_p95_ms'])}ms**이고, 클라이언트 p95는 **{f(m0['background_crud_p95_ms'])} → {f(m1['background_crud_p95_ms'])}ms**다. 이 결과는 모델 처리속도 자체의 변화가 아니라 공유 DB 연결 경쟁이 달라진 결과로 해석해야 한다.

연결이 충분하거나 사용자가 적으면 이미 대기가 짧아 차이가 작거나 반대로 흔들릴 수 있다. 두 번의 반복만으로 작은 차이의 통계적 유의성을 주장하지 않는다. 표의 정상 조건과 뒤의 고갈 조건을 함께 봐야 한다.''')
chart('crud-during-agent','Agent 실행 중 백그라운드 CRUD 서버 p95','mixed','users_label','background_crud_server_p95_ms','ms')
mi=[]
for n in (1,10,30,50,100):
 b,c=R(f'mixed-{n}','before'),R(f'mixed-{n}','after')
 mi.append({'users':n,'server_before':round(b['background_crud_server_p95_ms'],1),'server_after':round(c['background_crud_server_p95_ms'],1),
 'queue_before':round(b['queue_mean_ms']/1000,2),'queue_after':round(c['queue_mean_ms']/1000,2),
 'complete':f"{b['complete']}/{b['attempted']} → {c['complete']}/{c['attempted']}", 'errors':f"{b['http_errors']} → {c['http_errors']}"})
table('mixed-table','초기 혼합 부하: 서버 지연·큐 대기·완료율',mi,[('users','사용자','number'),('server_before','직전 CRUD p95 ms','number'),('server_after','현재 CRUD p95 ms','number'),('queue_before','직전 큐 평균 초','number'),('queue_after','현재 큐 평균 초','number'),('complete','완료/시도','text'),('errors','HTTP 오류','text')],'users')
md('flow',f'''## 실제 4단계 흐름에서는 큐와 저장 비용도 남는다

초기 요청 → 데이터 선택 → 분석 목적 입력 → 워크플로우 후보 선택 → **워크플로우 승인 대기**까지 실행했다. 사용자당 Run은 4개이며 실제 Mock LLM 호출도 총 4회다. 이 조건의 LLM 설정은 0.1초/회다. 승인 응답을 보내거나 Executor를 제출한 테스트가 아니다.

100명 평균 시나리오 완료시간은 **{sec(w0['scenario_mean_ms'])} → {sec(w1['scenario_mean_ms'])}초**, 성공 시나리오 p95는 **{sec(w0['scenario_p95_ms'])} → {sec(w1['scenario_p95_ms'])}초**다. 아래 표는 모든 사용자 수를 함께 보여준다. 빠른 Mock만 사용하면 남은 API·checkpoint·저장·큐 비용의 비중이 커진다.''')
flow=[]
for n in (1,10,30,50,100):
 b,c=R(f'flow-{n}','before'),R(f'flow-{n}','after')
 flow.append({'users':n,'mean_before':round(b['scenario_mean_ms']/1000,2),'mean_after':round(c['scenario_mean_ms']/1000,2),
 'p95_before':round(b['scenario_p95_ms']/1000,2),'p95_after':round(c['scenario_p95_ms']/1000,2),
 'rate_before':round(b['completed_scenarios_per_second'],2),'rate_after':round(c['completed_scenarios_per_second'],2),
 'complete':f"{b['complete']}/{b['attempted']} → {c['complete']}/{c['attempted']}"})
table('flow-table','워크플로우 승인 대기까지의 완료시간과 처리량',flow,[('users','사용자','number'),('mean_before','직전 평균 초','number'),('mean_after','현재 평균 초','number'),('p95_before','직전 p95 초','number'),('p95_after','현재 p95 초','number'),('rate_before','직전 완료/초','number'),('rate_after','현재 완료/초','number'),('complete','완료/시도','text')],'users')
stage_names={'data_selection':'1. 데이터 선택 대기','analysis_context':'2. 분석 목적 대기','workflow_candidate_selection':'3. 후보 선택 대기','workflow_approval':'4. 승인 대기'}
ss=[{**r,'stage_label':stage_names[r['stage']],'version':'직전' if r['label']=='before' else '현재','queue_seconds':r['queue_ms']/1000} for r in stages if r['name']=='flow-100']
datasets['stages100']=sorted(ss,key=lambda x:(x['stage_label'],0 if x['label']=='before' else 1))
flow_parts={label:{key:sum(r[key] for r in ss if r['label']==label)/1000 for key in ('queue_ms','model_ms','other_execution_ms','observation_and_http_ms','client_ms')} for label in ('before','after')}
q=flow_parts['after']
md('queue-headline',f'''현재 100명 HITL 시나리오의 평균 **{sec(w1['scenario_mean_ms'])}초**를 사용자 기준으로 더하면, **큐 대기 {f(q['queue_ms'],2)}초**, 실제 Mock LLM **{f(q['model_ms'],2)}초**, 기타 실행 **{f(q['other_execution_ms'],2)}초**, 단계별 관찰·HTTP **{f(q['observation_and_http_ms'],2)}초**다. 나머지 **{f(w1['scenario_mean_ms']/1000-q['client_ms'],2)}초**는 세션 생성과 단계 사이 사용자 대기 등의 단계 밖 시간이다.

즉 현재 이 조건에서는 **전체 평균의 약 {f(q['queue_ms']/(w1['scenario_mean_ms']/1000)*100)}%가 큐 대기**다. LLM 응답이 0.1초라고 해서 100명의 네 단계가 바로 끝나지는 않는다. 같은 네 실행 자리를 모든 세션이 나눠 쓴다. 이는 이번 조건에서 관측한 지연 구성이고 모든 사용자 수·실제 모델에서 같은 비율이라는 뜻은 아니다.

큐는 기다린 장소이고 원인을 전부 설명하는 말은 아니다. 한 Run이 DB/결과 저장/감시 정리 등에 오래 걸리면 다음 Run이 자리를 얻는 시간도 늦어진다. 실행 자리 제한과 자리당 처리 비용을 같이 봐야 하며, 지금 수치만 보고 무조건 자리를 늘리는 결론은 내리지 않는다.''')
md('queue','''## LLM이 빨라도 실행 자리를 기다리는 시간은 별개다

다음 그림은 100명 전체 HITL의 **각 Run이 시작되기 전 큐 대기**다. 한 세션 전체가 자리를 계속 점유하는 구조는 아니지만, 매 단계마다 최대 4자리 중 하나를 얻어야 한다. 사용자가 많으면 짧은 그래프라도 이 대기가 합쳐진다.

표의 ‘기타 실행’은 DB/체크포인트/그래프 관리/결과 저장/감시 정리 등을 모두 포함한다. SQL 실행시간만을 뜻하지 않는다. ‘관찰·HTTP’에는 0.5초 폴링으로 완료를 발견하기까지의 시간도 포함한다. 각각 동일 Run ID를 연결해 계산했으며 분위수끼리 빼지 않았다. **시간 분해는 근사치**다. DB의 transaction 시작 시각(now), 앱 wall clock, 모델 monotonic timer의 경계가 달라 전체에서 3건의 음수 잔여시간(최대 약 36ms)이 관측됐으며 값을 잘라내지 않고 원본에 보존했다. 초 단위 큐 대기와 달리 수십 ms 차이는 이 분해로 단정하지 않는다. ‘그래프 전체(참고)’는 그래프 ainvoke 자체의 시간으로 LLM을 이미 포함한다. 분해 항목에 다시 더하면 이중 계산이다.''')
chart('queue-by-stage','100명 HITL의 단계별 평균 큐 대기','stages100','stage_label','queue_seconds','초 / Run')
st=[]
for r in ss:
 st.append({'stage':r['stage_label'],'version':r['version'],'queue':round(r['queue_ms']/1000,3),'model':round(r['model_ms']/1000,3),
 'other':round(r['other_execution_ms']/1000,3),'graph':round(r['graph_ms']/1000,3),'observation':round(r['observation_and_http_ms']/1000,3),'client':round(r['client_ms']/1000,3)})
table('stage-table','100명 HITL의 단계 시간 분해(평균 초)',st,[('stage','단계','text'),('version','버전','text'),('queue','큐','number'),('model','LLM','number'),('other','기타 실행','number'),('graph','그래프 전체(참고)','number'),('observation','관찰·HTTP','number'),('client','단계 전체','number')],'stage')
md('resource-details','''## 남은 비용을 판단하기 위한 보조 계측

다음은 100명 초기 혼합과 전체 HITL의 서비스 DB·프로세스 지표다. 풀 획득 시간은 연결 생성이 발생하면 그 비용도 포함하고, SQL 횟수는 상태조회·취소 감시·소유권 관리까지 포함한다. Run당 쿼리가 모두 그래프가 직접 수행한 쿼리라는 뜻은 아니다.

CPU는 서버 단일 프로세스의 CPU seconds / 실험 seconds로 계산한 코어 사용량이다. DB 컨테이너 CPU·Pod CPU 제한·Kubernetes 자동 확장 지표와 같지 않다. 이벤트 루프 지연은 50ms sampler의 예정 시점 대비 초과 시간이며, 이 값 하나로 모든 동기 블로킹 부재를 입증하지 않는다.''')
resources=[]
for name in ('mixed-100','flow-100'):
 for label in ('before','after'):
  r=R(name,label)
  resources.append({'condition':name,'version':'직전' if label=='before' else '현재',
   'pool':round(r['mean_checked_out'],2),'acquire95':round(r['checkout_p95_ms'],2),
   'sql_per_run':round(r['sql_count_per_run'],1),'sql95':round(r['sql_p95_ms'],2),
   'cpu':round(r['cpu_core_fraction'],3),'loop95':round(r['loop_lag_p95_ms'],2),
   'peak':r['peak_graph']})
table('resource-table','100명 조건의 보조 계측',resources,[('condition','조건','text'),('version','버전','text'),('pool','평균 점유 연결','number'),('acquire95','연결 획득 p95 ms','number'),('sql_per_run','SQL/Run','number'),('sql95','SQL p95 ms','number'),('cpu','CPU 코어','number'),('loop95','루프 지연 p95 ms','number'),('peak','최대 동시 그래프','number')],'condition')
md('crud-alone',f'''## CRUD 자체를 최적화한 변경은 아니다

CRUD만 수행하는 대조 실험은 사용자당 18건의 생성·조회·수정·삭제 요청을 보냈다. 100명 서버 p95는 **{f(c0['crud_server_p95_ms'])} → {f(c1['crud_server_p95_ms'])}ms**다. 이 경로의 업무 SQL은 이번 변경으로 바뀌지 않았다. 작은 증감은 두 번의 반복만으로 개선·회귀라고 단정하기 어렵다.

이 대조군은 중요한 제한이다. 이번 변경이 없는 경로에서도 로컬 실행 편차가 존재하므로, Agent 조건의 작은 차이도 같은 기준으로 해석했다. 사용자가 늘면서 생기는 CRUD 자체의 연결 대기나 쿼리 비용까지 해결했다고 주장하지 않는다.''')
cr=[]
for n in (1,10,30,50,100):
 b,c=R(f'crud-{n}','before'),R(f'crud-{n}','after')
 cr.append({'users':n,'before':round(b['crud_server_p95_ms'],1),'after':round(c['crud_server_p95_ms'],1),'client_before':round(b['crud_p95_ms'],1),'client_after':round(c['crud_p95_ms'],1),
 'elapsed_before':round(b['mean_trial_seconds'],2),'elapsed_after':round(c['mean_trial_seconds'],2),'errors':f"{b['http_errors']} → {c['http_errors']}"})
table('crud-table','CRUD 단독 대조군',cr,[('users','사용자','number'),('before','직전 서버 p95 ms','number'),('after','현재 서버 p95 ms','number'),('client_before','직전 클라이언트 p95','number'),('client_after','현재 클라이언트 p95','number'),('elapsed_before','직전 전체 초','number'),('elapsed_after','현재 전체 초','number'),('errors','HTTP 오류','text')],'users')
md('code','''## 이전에 무엇이 문제였고 코드는 어떻게 바뀌었나

**문제 1 — commit을 했어도 바로 이어진 refresh가 새 트랜잭션을 열었다.** `TaskEventService.append()`는 commit 뒤 refresh를 수행했다. Run 실행 준비에서 이 함수를 호출한 다음 그래프로 넘어가므로, 시작 이벤트는 저장됐어도 새 읽기 트랜잭션의 연결을 가진 채 모델을 기다릴 수 있었다.

**문제 2 — 프로젝트 설정 조회와 그래프 실행이 같은 서비스 세션을 공유했다.** 초기 요청의 프로젝트 prompt 조회, 오래된 checkpoint의 prompt 보완 조회 뒤에 열린 세션을 `ainvoke`로 넘겼다. 스트리밍도 결과 저장에 사용한 세션을 다음 상태/소비자 대기까지 이어갔다.

현재는 다음처럼 경계를 나눈다.

1. 실행에 필요한 ID를 일반 값으로 보존하고, 시작 이벤트는 `commit=False`로 추가한 뒤 준비 트랜잭션을 명시적으로 commit한다. 그 뒤 refresh하지 않는다.
2. 프로젝트 설정은 짧은 전용 세션에서 읽고 닫는다. 오래된 checkpoint의 `aupdate_state`보다 먼저 반환한다.
3. 그래프 함수에는 열린 `db`를 전달하지 않는다. 기존 공용 풀을 사용하는 factory만 필요할 때 전달한다.
4. 결과가 나오면 별도 짧은 세션에서 Task 연결·메시지·로그를 기록한다. 스트리밍은 이 세션을 닫은 뒤 yield/다음 상태 대기를 한다.
5. 취소가 반복돼도 close/rollback 완료를 관찰한다. 최종 Run 상태 저장에서는 소유권과 취소 여부를 다시 확인한다.

**세션 객체·연결 풀·실제 빌린 연결은 서로 다르다.** Worker의 coordinator 세션 객체는 남아 있어도 모델 대기 중 트랜잭션이 없으면 연결을 점유하지 않는다. 풀을 요청마다 새로 만드는 변경도 아니며, 같은 세션의 사용자 입력 제한을 푸는 변경도 아니다.''','code')
md('sensitivity','''## 연결 수와 LLM 지연을 바꾸면 무엇이 달라지나

아래는 전체 조합을 전수 탐색한 결과가 아니라 원인을 확인하기 위한 선택된 민감도 조건이다. 모델이 느릴수록 예전 구조에서 빌린 연결이 오래 묶인다. 반대로 연결이 충분하면 응답시간 차이가 작아도 누적 점유량은 줄 수 있다. 실행 자리 1개 조건은 큐 처리 한도가 별도 문제임을 확인하기 위한 것이다.

완료하지 못한 실험의 짧은 성공 평균을 정상 실험과 비교하지 않는다. 연결 고갈로 감시 작업이 실패하면 기존 소유권 보호가 복구 필요 상태를 유지할 수 있으며, 이를 무리하게 자동 재실행하지 않는다.''')
sens=[]
for name in ('fast-mixed10','slow-mixed10','tight-slow10','roomy-mixed100','single-slot10'):
 for label in ('before','after'):
  r=R(name,label)
  sens.append({'condition':name,'version':'직전' if label=='before' else '현재','users':r['users'],'pool':r['pool'],'slots':r['slots'],'delay':r['delay_ms']/1000,
   'complete':f"{r['complete']}/{r['attempted']}",'errors':r['http_errors'],'recovery':r['recovery_tasks'],
   'hold':round(r['worker_hold_seconds_per_run'],3),'server95':round(r['background_crud_server_p95_ms'],1) if r['background_crud_server_p95_ms'] else None,
   'elapsed':round(r['mean_trial_seconds'],2)})
table('sensitivity-table','LLM 지연·풀·실행 자리 민감도',sens,[('condition','조건','text'),('version','버전','text'),('users','사용자','number'),('pool','풀','number'),('slots','자리','number'),('delay','LLM 초/회','number'),('complete','완료','text'),('errors','HTTP 오류','number'),('recovery','복구 필요 Task','number'),('hold','Worker 연결초/Run','number'),('server95','CRUD 서버 p95 ms','number'),('elapsed','전체 초','number')],'condition')
md('failure-mechanism',f'''### 작은 풀에서 관측한 중단의 순서

직전 버전의 연결 4개/실행 4자리 조건에서는 다음 순서가 실제 로그와 DB 상태에 남았다.

1. Run들이 모델을 기다리는 동안 서비스 연결을 점유한다.
2. 취소 감시가 조회용 연결을 요청하지만 풀에 여유가 없어 `QueuePool ... size 4 overflow 0 ... timeout 2.00`이 발생한다.
3. `cancel_watch_failed`가 실행 건전성을 실패로 표시한다. 다른 진행 중 실행도 `session_execution_interrupted_or_uncertain`으로 보호되고, Worker는 새 Run 점유를 중단한다.
4. 완료 여부가 불확실한 실행은 소유권을 유지한다. DB의 `running`은 그 시점에 LLM이 계속 실행 중이라는 증거가 아니다. 실제 그래프는 `CancelledError`로 반환했고, 상태는 복구가 필요한 채 남았다.
5. 후속 pending Run이 시작되지 않아 45초 시나리오 기한을 넘겼다. 두 번 합쳐 직전 **{t0['complete']}/{t0['attempted']} 완료**, 복구 필요 Task **{t0['recovery_tasks']}개**였다. 현재 동일 조건은 **{t1['complete']}/{t1['attempted']} 완료**, 복구 필요 Task **{t1['recovery_tasks']}개**였다.

**사용자가 cancel API를 호출한 실험이 아니다.** 일반 실행 중 취소 여부를 확인하는 감시가 DB 연결을 얻지 못한 것이다. 정상 HITL interrupt와도 다른 중단이다. 현재의 짧은 transaction은 이 실험에서 연결 고갈의 원인을 제거했고, 소유권 보호/자동 복구 정책 자체를 완화한 것은 아니다.

직전 실패 실험은 Worker가 멈춘 뒤에도 상태조회·백그라운드 CRUD를 계속한다. 그때의 성공 요청 p95나 Run당 점유량이 낮아 보여도 정상 완료한 현재 버전보다 성능이 좋다는 뜻은 아니다. 미완료율·복구 상태가 우선이다. 상세 로그 발췌와 Run 상태는 data/failure-evidence.json에 보존했다.''')
fast0,fast1=R('fast-mixed10','before'),R('fast-mixed10','after')
md('fast-tradeoff',f'''## 작은 빠른 요청에서는 오히려 비용이 늘어나는 구간도 있다

10명·LLM 0.1초/회 조건의 사용자 평균은 **{sec(fast0['scenario_mean_ms'])} → {sec(fast1['scenario_mean_ms'])}초**, 마지막 사용자까지 끝나는 실험 시간은 **{f(fast0['mean_trial_seconds'],2)} → {f(fast1['mean_trial_seconds'],2)}초**였다. 마지막 완료시간만 보면 악화가 더 크게 보이므로 평균과 함께 표시했다.

실제 Run 실행 평균은 **{f(fast0['execution_mean_ms'])} → {f(fast1['execution_mean_ms'])}ms**로 늘었다. 이 조건의 Worker SELECT 횟수는 반복당 **{f(fast0['worker_selects_per_trial'],0)} → {f(fast1['worker_selects_per_trial'],0)}회**였다. 새 저장 세션은 이전 ORM 세션에 있던 객체를 다시 조회하는 비용이 있으며, 소스 변경과 조회 증가가 같은 방향이다. 다만 쿼리별 attribution까지 계측한 것은 아니므로 증가한 모든 시간을 특정 SELECT 하나에 배분하지 않는다.

완료 시각이 조금 뒤로 밀리면 0.5초 주기의 다음 상태조회에서야 완료를 발견할 수 있다. 이 실험에서도 현재 버전은 상태조회 횟수가 늘었다. 따라서 DB 연결을 적게 점유하게 만든 변경이 **모든 조건에서 더 빠르다는 뜻은 아니다.** 짧은 transaction 경계는 유지하면서 불필요한 재조회·개별 commit 비용을 줄이는 것이 후속 검토 방향이다.''')
md('limits','''## 검증한 것과 아직 말할 수 없는 것

**강하게 확인한 부분:** 같은 commit 차이·같은 Mock·실제 API/DB/Worker에서 연결 점유 구간이 제거됐고, 실제 체크포인트를 사용한 초기 호출과 HITL 흐름을 통과했다. 원본 요청·checkout/checkin·SQL·Run 상태를 보존해 지표를 재계산할 수 있다.

**제한:** 조건별 반복은 2회다. 작은 시간 차이의 통계적 유의성, Kubernetes에서의 최대 수용 사용자, 실제 LLM 처리량, 일주일짜리 Executor 완료, 장시간 메모리 누수/soak는 검증하지 않았다. CPU·메모리가 전용 예약된 호스트도 아니다. 빠른 LLM 조건에서 DB/그래프 오버헤드가 더 두드러질 수 있다.

**측정 오염을 보정했다:** 예비 공유 HTTP 연결 풀에서 클라이언트 지연이 서버보다 크게 나타나 사용자별 풀로 바꿨다. 예비 수치는 최종 비교에서 제외했다. 현재 보고서의 서버 시간과 클라이언트 시간은 같은 요청에서 별도로 기록했다.

**원자성 문제와는 별개다:** 기존 CRUD handler의 개별 commit과 멱등 정책은 유지한다. checkpoint와 모든 CRUD 저장을 하나의 원자적 transaction으로 바꾼 것은 아니다. 기존 공개 API 계약·DB schema·운영 풀 크기를 바꾸지 않았다.''')
md('history','''## 이번 결과와 이전 리팩터링의 관계

다음은 이미 구현된 구조 변화다. 이번 부하 실험의 두 비교 버전 모두 001~015를 포함하므로, 아래 성과를 이번 A/B 개선율에 더하지 않는다. 이전 검증 기록과 이번 실측을 구분한다.

| 영역 | 이전 문제 | 현재 구조 | 이번 보고서에서의 판정 |
|---|---|---|---|
| 실행 정리·취소 (001/012) | 감시 정리 또는 동기 I/O의 종료가 불확실하면 Run 진행이 막힐 수 있음 | 자식 작업 종료를 관찰하고 불확실 상태는 복구 필요로 보호 | 정상 부하의 완료 여부 관찰. 과거 버그 제거율의 A/B는 아님 |
| 설정·기동 (002/007) | 여러 설정 진입점·불필요 Azure 설정 | 중앙 설정 snapshot, config > env > 기본값, Azure 제거 | 동일 설정으로 두 버전 주입. 실제 Gaia 원본 통합은 별도 |
| 자원 수명 (004) | 그래프/체크포인터 자원을 호출마다 재조립하는 비용 | 프로세스 런타임 단위 수명·재사용 | 두 버전 공통. checkpoint 풀 점유 수치는 별도 미계측 |
| Agent 구성·비동기 (005~012) | 역할·프롬프트·workflow 자산 분산, 동기 LLM 호출 | agent_service 업무 패키지, 역할별 create_agent·프롬프트, 공통 미들웨어·ainvoke 경로 | 실제 분석 그래프+Mock 경로 검증. 모든 파일 I/O의 native async 전환은 아님 |
| 실행 자리 (013) | 실행기 하나가 현재 Run 반환까지 다음 Run을 시작하지 않음 | 프로세스당 제한된 여러 Run 동시 실행, 세션 입력 제한 유지 | 두 버전 모두 4자리. 슬롯 증가 효과를 016 성과로 계산하지 않음 |
| 실행 소유권 (014) | API Run과 Executor 이벤트의 공통 실행 배제 경계 부족 | 공통 PostgreSQL 소유권, 불확실 종료 시 자동 탈취 금지 | 정상 실험의 소유권 반환 확인. 실제 Redis·Executor 동시 경합은 이번 범위 밖 |
| 종료 처리 (015) | 종료 중 새 점유·기존 실행 정리의 순서 문제 | 새 점유 중지, 진행 중 호출 drain, 불확실 종료 보호 | 매 trial 프로세스 종료. 이번 측정은 Kubernetes rolling 검증이 아님 |
| DB 수명 (016) | 열린 읽기 transaction이 모델/그래프 대기까지 연결 점유 | 조회·저장 각각 짧은 세션, 모델 대기 전에 풀 반환 | **이번에 같은 조건으로 직접 비교한 변경** |

이전 016 구현 시 전체 회귀는 355 passed, 2 subtests passed였으며 기존 durability 경고 43개가 있었다. 이것은 당시 검증 기록이고 이번 80회 부하 실험을 355개의 새 테스트로 계산하지 않는다. 상세 변경·검증 기록은 docs/improvements/001~016에 보존돼 있다.

현재도 다중 업무 Agent registry, project_memory의 실제 저장, 운영 복구 관리자 API, 실제 Gaia 템플릿·Kubernetes 검증 등은 별도 후속 범위다. 디렉터리 재배치나 경계 분리가 곧 이 기능들의 구현 완료를 뜻하지 않는다.''',None)
md('next','''## 다음 작업은 전체 재작성보다 남은 비용을 분리하는 것이다

1. **이번 연결 수명 개선은 유지한다.** 효과를 전체 응답시간만으로 평가하면 줄어든 DB 점유 여유를 놓친다. 운영에는 API p95/오류율과 함께 서비스 풀 점유·획득 시간·큐 대기를 관찰할 필요가 있다.
2. **큐 대기는 실행 자리 수와 요청 유입량 문제로 따로 다룬다.** DB 연결 수가 충분하다는 확인 없이 자리만 늘리지 않는다. CPU 기반 자동 확장만으로 LLM 대기/큐 길이에 맞는 확장이 일어난다고 보장할 수 없다.
3. **빠른 Mock에서도 남는 기타 실행 시간을 더 분해한다.** 결과 저장의 query/commit/refresh 횟수, 체크포인트 I/O, 상태 조회·취소 감시의 빈도를 우선 확인한다. 이번 결과만으로 특정 SQL 하나를 다음 병목이라고 확정하지 않는다.
4. **실제 프론트 폴링/사용자 대기를 넣은 지속 부하를 후속 검증한다.** 이번 독립 burst의 수치로 운영 SLO나 최대 수용 인원을 확정하지 않는다.
5. **운영 복구 관리자 API는 별도 후속 항목으로 유지한다.** 연결 점유 개선이 불확실 실행의 복구 절차를 대신하지 않는다.

남은 질문은 실제 Pod 자원 제한에서의 적정 실행 자리/풀 조합, 사용 중인 프론트 조회 빈도, 실제 모델 응답 분포, 장시간 운영 시 큐 안정성이다. 이 보고서는 그 판단을 위한 전후 근거이며 현재 서비스의 모든 병목이 해결됐다는 판정은 아니다.''')
artifact={'surface':'report','manifest':manifest,'snapshot':{'version':1,'status':'ready','generatedAt':now,'datasets':datasets},'sources':[source,code_source]}
(a.output/'artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2))
print(json.dumps({'blocks':len(manifest['blocks']),'charts':len(manifest['charts']),'tables':len(manifest['tables']),'hold_reduction_percent':hold_gain,'background_crud_server_p95_reduction_percent':crud_gain,'flow_mean_reduction_percent':flow_gain}))
