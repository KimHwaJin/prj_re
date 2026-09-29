"""Build the canonical portable report input from reviewed measurement summaries."""
import argparse,json,sqlite3
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('summary',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
data=json.loads(a.summary.read_text());assert [r['users'] for r in data]==[1,10,30,50]
rows=[];stages=[];cost=[]
for r in data:
 m=lambda k:r['per_user'][k]['mean']
 rows.append({'users':r['users'],'cohort':f"{r['users']}명",'slots':4,'calls_per_user':4,
  **{k:round(m(k),4) for k in ['total_s','queue_s','llm_s','internal_s','outside_worker_queue_s','projection_s','init_s','checkpoint_exclusive_s','other_internal_s','delivery_s']},
  'p95_s':round(r['per_user']['total_s']['p95'],4),'batch_s':round(r['elapsed_s'],4),'occupancy':r['slot_occupancy']})
 cost.append({'users':r['users'],'service_sql':r['sql_total'],'worker_sql_per_user':r['sql_by_actor']['worker']['count']/r['users'],
 'commit_calls_per_user':r['commit_calls_by_actor']['worker']/r['users'],'pool_p95_ms':round(r['pool_acquire_ms']['p95'],3),
 'pool_max_ms':round(r['pool_acquire_ms']['max'],3),'lag_p95_ms':round(r['event_loop_lag_ms']['p95'],3)})
for index,(name,metrics) in enumerate(data[-1]['stages'].items(),1):
 labels={'data_selection':'데이터 선택 대기까지','analysis_context':'분석 목적 입력 대기까지','workflow_candidate_selection':'Workflow 후보 선택 대기까지','workflow_approval':'Workflow 승인 대기까지'}
 stages.append({'order':index,'stage':labels[name],**{k:round(metrics[k]['mean'],4) for k in ['stage_s','queue_s','worker_s','llm_s','model_calls','delivery_s']}})
source={'id':'profile','label':'Current runtime ea871a1 · local 5-second LLM · measured September 29, 2026',
 'path':'summary.json'}
blocks=[]
def md(id,body,sourced=True):
 blocks.append({'id':id,'type':'markdown','body':body,**({'sourceId':'profile'} if sourced else {})})
md('title','# Runtime Latency Profile',False)
md('summary','''## 결론: 내부 최적화는 남았지만, 긴 대기의 주원인은 실행 슬롯 경쟁입니다

**현재 코드에서 내부 처리 비용을 모두 없앴다고 볼 수는 없습니다.** 다만 이번 경로의 실제 Worker 처리 시간은 사용자당 약 21초이고, 그중 LLM 호출 4회가 약 20초입니다. 나머지 내부 처리는 평균 0.92~1.52초였습니다.

50명이 동시에 시작하면 평균 완료 시간은 **257.65초**입니다. 그중 **234.20초는 네 번의 실행에 걸쳐 누적된 큐 대기**, LLM은 20.03초, 내부 처리는 1.05초입니다. 한 작업의 코드가 257초 동안 실행된 결과가 아닙니다.

다음 내부 개선 후보는 메시지·로그·이벤트의 DB 저장 경로입니다. 다만 이 경로의 측정 비용은 약 0.52~0.70초/사용자입니다. 이를 줄이는 것이 수분짜리 대기를 없앨 것이라고 설명하면 안 됩니다. 이번 작업에서는 서비스 동작 코드를 변경하지 않았습니다.''')
md('scope','''## 같은 조건에서 사용자 수만 바꿨습니다

현재 코드 한 버전, API 프로세스 1개, 실행 슬롯 4개, 로컬 PostgreSQL, LLM HTTP mock 호출당 5초로 측정했습니다. 각 사용자는 새 세션을 만들고 네 번의 Run 실행을 거쳐 **Workflow 승인 대기**에서 종료합니다. 실제 Executor·Redis는 사용하지 않았습니다.

모든 표의 시간은 초이며, 별도 표기가 없으면 **한 사용자 전체 흐름의 평균**입니다. 1·10·30·50명이 각 한 번씩 동시에 시작한 배치입니다. 단계 사이 사용자 생각 시간은 0.2초씩 총 0.6초입니다. 상태 확인은 SSE를 사용했고 GET 반복 폴링은 없습니다.

전체 시간 = DB에 기록된 큐 대기 + Worker 구간 안의 LLM + Worker 구간 안의 나머지 처리 + 구간 밖 잔여 시간입니다. 잔여 시간에는 세션 생성·접수 일부·사용자 생각 시간·SSE 수신 대기 등이 포함됩니다. 순수 CPU 시간으로 해석하면 안 됩니다.''')
md('waiting','''## 사용자 수가 늘 때 증가한 것은 대부분 큐 대기입니다

막대의 합은 전체 흐름의 평균 시간입니다. LLM과 내부 처리 시간은 거의 유지됐고, 50명 조건에서 큐 대기가 전체의 **90.90%**를 차지했습니다. 50명 배치의 슬롯 점유율은 **97.17%**였습니다. 이는 CPU 사용률이 아니라, 준비된 4개 자리가 Worker 실행으로 채워져 있던 시간의 비율입니다.

따라서 이번 측정은 사용 가능한 슬롯이 오래 비어 있어 지연된 상황과는 맞지 않습니다. 네 슬롯이 LLM 응답을 기다리는 동안 다음 실행이 대기열에 쌓입니다. 표의 P95는 단일 배치의 관측값이며 운영 SLA 추정치가 아닙니다.''')
blocks += [{'id':'total-chart','type':'chart','chartId':'total'},{'id':'total-table','type':'table','tableId':'total-values'}]
md('internal','''## 내부 처리 중 가장 큰 분리 항목은 DB 저장입니다

다음 막대는 **LLM·큐 대기를 제외한 Worker 내부 시간만** 확대해서 보여줍니다. 메시지·로그·이벤트 등을 서비스 DB로 반영하는 구간이 평균 0.52~0.70초였습니다. checkpoint 항목은 다른 구간과 겹치지 않는 관측 시간을 뜻하며, 백그라운드 저장 시간까지 전부 더한 값이 아닙니다.

1명 조건의 초기화 비용 0.43초는 최초 그래프·공유 자원을 준비하는 비용입니다. 나머지 조건도 배치당 그래프는 한 번만 생성됐습니다. 여러 사용자 평균에서 이 값이 작아지는 것은 초기화 비용이 분산되기 때문이며, 코드가 달라진 성능 향상이 아닙니다.

50명 조건의 내부 시간 1.05초는 Worker 시간의 약 4.98%입니다. 이를 전부 0으로 만드는 비현실적인 가정에서도 슬롯당 처리율 개선 여지는 약 5.2%입니다. 개별 DB 최적화의 실제 효과는 그보다 작고, 아직 A/B로 측정하지 않았습니다.''')
blocks.append({'id':'internal-chart','type':'chart','chartId':'internal'})
md('short-stage','''## 0.15초짜리 재개도 60초를 기다렸습니다

아래는 **50명 조건의 단계별 평균**입니다. LLM 호출은 단계별로 **2회 → 0회 → 2회 → 0회**입니다. 두 번째 단계는 실제 Worker 처리에 0.15초, 실행 시작을 기다리는 데 60.03초가 들었습니다. 마지막 단계도 처리 0.25초에 큐 대기 54.47초였습니다.

짧은 재개 작업도 긴 LLM 작업과 같은 실행 슬롯을 기다립니다. 이는 대기 정책과 실행 동시성을 별도로 검토할 근거입니다. 이 측정만으로 resume 우선 처리나 동시성 증가가 최선이라고 확정할 수는 없습니다. 사용자별 공정성·LLM 한도·DB 사용량을 함께 검증해야 합니다.''')
blocks.append({'id':'stage-table','type':'table','tableId':'stages'})
md('db','''## 반복 저장 비용과 순간적인 DB 풀 대기는 남아 있습니다

서비스 DB 계측에서 Worker 경로는 사용자당 **SQL 322회, AsyncSession.commit 호출 61회**였습니다. commit 호출 수에는 실제 변경이 없는 호출도 포함될 수 있습니다. SQL 수치는 SQLAlchemy로 실행한 서비스 쿼리만 집계하며, psycopg 기반 checkpoint·bridge 쿼리는 제외합니다.

소스의 로그 생성 경로는 로그 커밋·refresh 이후 이벤트 저장을 별도로 호출하고, 이벤트 저장은 다시 커밋·refresh합니다. 따라서 중복 조회와 트랜잭션 왕복을 줄일 후보가 있습니다. 단, 조회마다 권한·멱등성·이벤트 순서를 보장하는 책임이 있으므로 일괄 삭제해서는 안 됩니다.

50명에서 DB 연결 획득 지연은 P95 약 1.70ms였지만 최대 772.92ms였습니다. 100ms 초과 201건 중 200건이 시작 후 2초 안에 발생했습니다. 초반 동시 접수의 순간 병목은 존재하며, 수분 단위로 지속되는 대기와는 구분해야 합니다.''')
blocks.append({'id':'db-table','type':'table','tableId':'cost'})
md('visibility','''## 결과 수신 지연은 처리 시간과 별개입니다

Run 최종 커밋 이후 클라이언트가 새 입력 대기 상태를 받기까지의 시간은 네 단계를 합해 평균 **1.14~1.42초**였습니다. 현재 SSE 변경 통지에는 0.5초 간격으로 변경을 모아 읽는 정책이 있습니다. 짧은 단계에서는 이 수신 지연의 비중이 큽니다.

관측 지연에는 SSE 스케줄링·조회·전송·클라이언트 처리도 포함됩니다. 전부 0.5초 설정 탓이라고 단정하지 않았고, 간격 변경 A/B도 수행하지 않았습니다. 이 값을 줄이면 체감 반응은 좋아질 수 있지만 LLM 처리 용량이 늘어나는 것은 아닙니다. 위 전체 시간에 별도로 더해서는 안 됩니다.''')
md('method','''## 검증은 원본 타임라인을 기준으로 했습니다

총 **91명, Run 실행 구간 364개, 모델 호출 364회**가 정상 완료됐습니다. 이때 전체 흐름은 승인 대기에서 멈추므로 DB의 실행 구간은 정상적인 interrupted 상태입니다. 재시도·오류·복구 필요 상태·남은 세션 실행 소유권은 없었습니다. 각 배치의 그래프 생성은 1회였고, 모든 모델 호출은 async였습니다.

Worker 내부의 모델·DB 반영·초기화·checkpoint 구간은 겹치는 시간을 합집합으로 처리했습니다. 우선순위는 모델 → DB 반영 → 초기화 → checkpoint이며, 나머지는 기타 내부 처리로 남겼습니다. 병렬 SQL 시간의 합을 사용자 지연으로 더하지 않았습니다. 별도의 검산 스크립트로 원본 시간·호출 수·공개 Run과 실행 구간의 대응을 다시 확인했습니다.

API 연결 풀은 준비 후 측정했고 그래프 최초 초기화는 포함했습니다. 진단 span과 SQL·이벤트 루프 샘플은 메모리에 수집했습니다. 이 계측 자체의 비용도 측정 결과에 포함됩니다.''')
md('limits','''## 이 결과로 운영 전체의 성능을 확정할 수는 없습니다

각 조건은 한 번의 동시 시작 배치입니다. 지속 유입·장시간 soak·오류/취소·여러 Pod·실제 LLM rate limit·공유 PV·Executor 제출/완료 이벤트는 측정하지 않았습니다. 로컬 PostgreSQL의 짧은 네트워크 왕복이 실제 배포 DB 비용을 낮게 보이게 할 수 있습니다. 초기 코드와의 A/B 비교도 이번 범위가 아닙니다.

현재 일반 Run 경로의 모델 HTTP 호출은 **비스트리밍**이었습니다. 따라서 직전 토큰 버퍼 개선의 성능 효과를 이 수치로 주장하지 않습니다. 이벤트 루프 지연의 P95는 약 2.4~2.8ms였지만 최대 0.26~0.41초의 순간 지연은 관측됐고, 최대 지연의 샘플 구간은 최초 의존성 로딩과 겹쳤습니다. 모든 동기 작업이 제거됐다는 뜻은 아닙니다.''')
md('next','''## 다음은 저장 경로를 좁혀 개선하고 같은 조건으로 확인하는 것이 적절합니다

1. **먼저 내부 저장 경로를 검토합니다.** 로그와 이벤트의 개별 커밋·중복 refresh·반복 식별 조회를 중심으로 불필요한 왕복을 줄일 수 있는지 확인합니다. 멱등성·이벤트 순서·오류 시 원자성을 유지해야 합니다.
2. **변경 후 이 시나리오를 같은 조건으로 다시 측정합니다.** 전체 시간뿐 아니라 DB 반영 시간·SQL 수·커밋 호출 수가 줄었는지 확인합니다. 지금부터 수십 초 개선을 약속할 근거는 없습니다.
3. **별도 항목으로 수신 지연과 짧은 재개 대기를 검토합니다.** 상태 변경의 즉시 통지, coalescing 간격, 스케줄링 공정성은 서로 다른 문제입니다. 접수 제한은 처리 시간을 줄이는 기능이 아니므로 이번 내부 개선의 대체 수단으로 삼지 않습니다.

아직 필요한 확인은 실제 배포 DB의 왕복 시간, 현실적인 지속 유입량, 목표 응답시간입니다. 이 정보가 있어야 동시성 4가 적절한지와 후속 용량 정책을 결정할 수 있습니다.''',False)
chart_datasets={}
def chart(id,title,fields,labels,colors,question):
 chart_datasets[id+'-components']=[{'cohort':row['cohort'],'users':row['users'],'component':label,'seconds':row[field],'total_s':row['total_s'],'slots':4,'calls_per_user':4} for row in rows for field,label in zip(fields,labels)]
 return {'id':id,'title':title,'type':'bar','dataset':id+'-components','sourceId':'profile','valueFormat':'number',
 'intent':'composition','question':question,'rationale':'Four discrete user cohorts; mutually exclusive duration components reconcile to the reported total. Full-width stacked bars show scale and composition.',
 'encodings':{'x':{'field':'cohort','type':'ordinal','label':'동시 시작 사용자'},'y':{'field':'seconds','type':'quantitative','label':'사용자당 평균 시간 (초)'},'color':{'field':'component','type':'nominal','label':'시간 구간'}},
 'palette':{'kind':'categorical'},'legend':{'position':'bottom'},'options':{'grouping':'stacked'}}

charts=[chart('total','사용자 수별 전체 시간 구성',['queue_s','llm_s','internal_s','outside_worker_queue_s'],['큐 대기','LLM','내부 처리','구간 밖 잔여'],['blue','orange','yellow','neutral'],'사용자 수가 늘 때 어느 시간이 증가하는가?'),chart('internal','LLM을 제외한 Worker 내부 시간',['projection_s','init_s','checkpoint_exclusive_s','other_internal_s'],['서비스 DB 반영','최초 자원 준비','checkpoint 비중첩','기타 내부'],['blue','orange','yellow','neutral'],'내부 처리 중 어느 경로를 우선 검토할 것인가?')]
def table(id,title,dataset,fields,labels,sort):
 return {'id':id,'title':title,'dataset':dataset,'sourceId':'profile','defaultSort':{'field':sort,'direction':'asc'},'columns':[{'field':f,'label':l,**({'type':'text'} if f=='stage' else {'format':'number'})} for f,l in zip(fields,labels)]}
tables=[table('total-values','전체 흐름 시간 (초)','users',['users','total_s','queue_s','llm_s','internal_s','outside_worker_queue_s','p95_s'],['사용자','평균 전체','큐 대기','LLM','내부 처리','구간 밖','전체 P95'],'users'),table('stages','50명 조건 단계별 시간 (초)','stages',['order','stage','model_calls','stage_s','queue_s','worker_s'],['순서','도달 지점','LLM 호출','체감 시간','큐 대기','실제 Worker'],'order'),table('cost','서비스 DB 비용과 순간 대기','cost',['users','service_sql','worker_sql_per_user','commit_calls_per_user','pool_p95_ms','pool_max_ms'],['사용자','서비스 SQL 합계','Worker SQL/명','commit 호출/명','풀 획득 P95 ms','풀 획득 최대 ms'],'users')]
artifact={'surface':'report','manifest':{'version':1,'surface':'report','title':'Runtime Latency Profile','blocks':blocks,'charts':charts,'tables':tables,'sources':[source]},'snapshot':{'version':1,'status':'ready','datasets':{'users':rows,'stages':stages,'cost':cost,**chart_datasets}}}
# Rendered datasets are extracted through actual SQLite queries, preserving exact
# SQL provenance required by the portable chart/table renderer. This is a local
# report staging database, never the service or production database.
with sqlite3.connect(a.output.parent/'report-datasets.sqlite') as db:
 db.execute('DROP TABLE IF EXISTS report_rows')
 db.execute('CREATE TABLE report_rows(dataset TEXT, position INTEGER, row_json TEXT)')
 for dataset,values in artifact['snapshot']['datasets'].items():
  db.executemany('INSERT INTO report_rows VALUES (?,?,?)',[(dataset,i,json.dumps(row,ensure_ascii=False)) for i,row in enumerate(values)])
  fields=list(values[0])
  expressions=[f"json_extract(row_json, '$.{field}') AS \"{field}\"" for field in fields]
  sql='SELECT '+', '.join(expressions)+f" FROM main.report_rows WHERE dataset = '{dataset}' ORDER BY position"
  selected=[dict(zip(fields,row)) for row in db.execute(sql)]
  assert selected==values
  artifact['snapshot']['datasets'][dataset]=selected
  sid='extract-'+dataset
  artifact['manifest']['sources'].append({'id':sid,'label':f'Local report dataset: {dataset} (from summary.json)', 'path':'report-datasets.sqlite', 'query':{'engine':'sqlite','sql':sql,'description':'Exact report extraction from main.report_rows; staged from the verified Python summaries in summary.json. This is not a production database.'}})
  for item in charts+tables:
   if item['dataset']==dataset:item['sourceId']=sid
 db.commit()
a.output.write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
