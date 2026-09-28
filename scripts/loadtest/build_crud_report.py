#!/usr/bin/env python3
"""Build a source-backed artifact.json and CSV from crud_ramp.py evidence."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import statistics
from crud_report_sql import build_sql_evidence


def memory_mib(text):
    value = text.split('/')[0].strip()
    for suffix, scale in [('GiB',1024),('MiB',1),('KiB',1/1024),('GB',1e9/1048576),('MB',1e6/1048576),('kB',1e3/1048576),('B',1/1048576)]:
        if value.endswith(suffix): return float(value[:-len(suffix)])*scale
    raise ValueError(value)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='var/loadtest/crud-ramp-20260928')
    p.add_argument('--output',default='docs/reports/crud-ramp-2026-09-28')
    args=p.parse_args(); root=Path(args.input); out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    stages=json.loads((root/'stages.json').read_text());meta=json.loads((root/'metadata.json').read_text())
    resources=[json.loads(line) for line in (root/'resources.jsonl').read_text().splitlines()]
    assert [s['users'] for s in stages]==meta['users'] and 'finished_at' in meta, 'Test is not complete'
    summary=[]; endpoint_rows=[]; cpu_rows=[]; resource_summary=[]
    for s in stages:
        by_name={(r['method'],r['name']):r for r in s['stats']['stats']}
        project=by_name['POST','/api/v1/projects'];session=by_name['POST','/api/v1/projects/:id/sessions']
        req=project['num_requests']+session['num_requests']; fail=project['num_failures']+session['num_failures']
        assert req>0 and s['stats']['user_count']==s['users']
        assert all(i['data']['user_count']==s['users'] for i in s['samples'])
        flow=sum(r['num_requests'] for r in s['stats']['stats'] if r['method']=='FLOW')
        assert abs(flow-req)<=2, 'Unexpected HTTP/FLOW discrepancy'
        assert all(r['num_requests']==0 for r in s['stats']['stats'] if r['method']=='GET' or r['name']=='/api/v1/users'), 'Setup leaked into measurement'
        measured=[r for r in resources if r['phase']=='measure' and r['users']==s['users'] and s['started_at']<=r['at']<=s['finished_at'] and 'error' not in r]
        assert len(measured)>=5, 'Insufficient resource samples'
        row={'users':s['users'],'user_label':str(s['users'])+'명','requests':req,'errors':fail,
            'seconds':round(s['elapsed_seconds'],3),'rps':round(req/s['elapsed_seconds'],3),'failure_rate':fail/req,
            'project_requests':project['num_requests'],'session_requests':session['num_requests'],
            'project_p50_ms':project['median_response_time'],'session_p50_ms':session['median_response_time'],
            'project_p95_ms':project['response_time_percentile_0.95'],'session_p95_ms':session['response_time_percentile_0.95'],
            'project_p99_ms':project['response_time_percentile_0.99'],'session_p99_ms':session['response_time_percentile_0.99'],
            'project_max_ms':project['max_response_time'],'session_max_ms':session['max_response_time'],
            'http_avg_ms':round(sum(r['avg_response_time']*r['num_requests'] for r in [project,session])/req,3),
            'flow_errors':sum(r['num_failures'] for r in s['stats']['stats'] if r['method']=='FLOW')}
        row['rps_per_user']=round(row['rps']/row['users'],3)
        summary.append(row)
        for label,r in [('프로젝트 생성',project),('세션 생성',session)]:
            endpoint_rows.append({'users':s['users'],'user_label':row['user_label'],'endpoint':label,'requests':r['num_requests'],
                'p50_ms':r['median_response_time'],'p95_ms':r['response_time_percentile_0.95'],'p99_ms':r['response_time_percentile_0.99'],
                'avg_ms':round(r['avg_response_time'],3),'max_ms':r['max_response_time'],'errors':r['num_failures']})
        resource={'users':s['users'],'samples':len(measured),'db_connections_max':max(r['database']['connections'] for r in measured),
            'db_active_max':max(r['database']['active'] for r in measured),'lock_wait_samples':sum(r['database']['lock_waits']>0 for r in measured),
            'idle_in_transaction_max':max(r['database']['idle_in_transaction'] for r in measured)}
        for service,label in [('api','API'),('postgres','PostgreSQL'),('locust','Locust')]:
            values=[next(c for c in r['containers'] if c['Name']==f'dtest-agent-loadtest-{service}-1') for r in measured]
            cpu=[float(c['CPUPerc'].rstrip('%')) for c in values]
            resource[service+'_cpu_avg']=round(statistics.mean(cpu),2)
            resource[service+'_cpu_max']=max(cpu)
            resource[service+'_memory_max_mib']=round(max(memory_mib(c['MemUsage']) for c in values),2)
            cpu_rows.append({'users':s['users'],'user_label':row['user_label'],'service':label,
                            'cpu_avg_pct':resource[service+'_cpu_avg'],'cpu_max_pct':max(cpu),'samples':len(cpu)})
        resource_summary.append(resource)
    total=sum(r['requests'] for r in summary);errors=sum(r['errors'] for r in summary)
    before=meta['before_database'];after=meta['after_database'];deltas={k:after[k]-before[k] for k in before}
    assert deltas['users']==100
    assert deltas['agent_runs']==0 and deltas['tasks']==0
    assert meta['before_mock_executor']['unique_submissions']==meta['after_mock_executor']['unique_submissions']
    peak=summary[-1];base=summary[0];rr=resource_summary[-1]
    fifty=next(r for r in summary if r['users']==50)
    peak_l=max(peak['project_p95_ms'],peak['session_p95_ms'])
    max_l=max(max(r['project_p95_ms'],r['session_p95_ms']) for r in summary)
    efficiency=peak['rps']/(base['rps']*100)
    result={'stages':summary,'endpoints':endpoint_rows,'resources':resource_summary,'database_delta':deltas,
            'total_measured_requests':total,'total_measured_errors':errors,'resource_sampling_errors':sum('error' in r for r in resources),
            'peak_scaling_vs_1_user':efficiency,'estimated_mean_inflight_at_100':peak['rps']*peak['http_avg_ms']/1000,
            'metadata':meta}
    (root/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    for name,rows in [('summary.csv',summary),('endpoints.csv',endpoint_rows),('resources-summary.csv',resource_summary)]:
        with (root/name).open('w') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    source={'id':'run_evidence','label':'로컬 CRUD 단계 부하테스트 실측', 'path':str(root/'summary.json'),
            'query':{'engine':'Locust 2.46.6 / Docker / PostgreSQL 17','language':'python',
                'description':'crud_ramp.py에서 저장한 단계별 Locust 통계와 Docker·PostgreSQL 표본을 build_crud_report.py로 집계.',
                'tables_used':[str(root/'stages.json'),str(root/'resources.jsonl'),str(root/'metadata.json'),str(root/'runtime-settings.json'),'src/app/core/auth.py','chat_app.public.users','chat_app.public.projects','chat_app.public.sessions','chat_app.public.agent_runs'],
                'executed_at':meta['finished_at'],
                'filters':['scenario=crud','프로젝트 및 세션 생성 POST만 HTTP 처리량에 포함','각 단계 15초 안정화 후 약 60초 측정','사용자 준비·안정화 요청은 측정 제외'],
                'metric_definitions':['RPS = 측정 생성 POST 요청 수 / 측정 경과 초','p95,p99 = 각 endpoint에 대한 Locust 누적 histogram 근사 백분위수; 서로 평균하지 않음','CPU 100% = 논리 코어 하나; 표본 평균·최댓값','오류율 = 실패 HTTP 요청 / 해당 HTTP 요청 수; FLOW 별도 검산']}}
    blocks=[];charts=[];tables=[]
    def md(id,body,evidence=False):
        b={'id':id,'type':'markdown','body':body}
        if evidence:b['sourceId']='run_evidence'
        blocks.append(b)
    def chart(id,title,dataset,x,y,y_label,color=None):
        enc={'x':{'field':x,'type':'nominal','label':'동시 사용자'},'y':{'field':y,'type':'quantitative','label':y_label}}
        if color:enc['color']={'field':color,'type':'nominal'}
        charts.append({'id':id,'title':title,'type':'bar','dataset':dataset,'sourceId':'run_evidence','layout':'full',
                       'valueFormat':'number','encodings':enc})
        blocks.append({'id':id+'_block','type':'chart','chartId':id})
    def table(id,title,dataset,columns):
        tables.append({'id':id,'title':title,'dataset':dataset,'sourceId':'run_evidence','density':'spacious',
            'defaultSort':{'field':'users','direction':'asc'},'columns':[{'field':f,'label':label,'format':'number'} if f != 'endpoint' else {'field':f,'label':label,'type':'text'} for f,label in columns]})
        blocks.append({'id':id+'_block','type':'table','tableId':id})
    title='CRUD Load Test: 1–100 Users'
    md('title','# '+title)
    md('summary',f'''## Executive Summary

- **100명 단계에서 초당 {peak['rps']:.1f}건을 처리했습니다.** 프로젝트 생성 p95 {peak['project_p95_ms']}ms, 세션 생성 p95 {peak['session_p95_ms']}ms, 해당 단계 오류 {peak['errors']}건입니다.
- **7개 단계 측정 구간의 총 {total:,}건 중 오류는 {errors:,}건입니다.** 프로젝트·세션 생성 기능의 이번 조건에 대한 관측이며, 전체 CRUD 기능이나 최대 수용량 인증은 아닙니다.
- **간헐적 지연과 시험 범위는 구분해야 합니다.** 50명 구간에서 최대 {max(fifty['project_max_ms'],fifty['session_max_ms']):.0f}ms의 지연이 관측됐습니다. 사용자당 약 1초의 대기가 있는 모델이며, 100명은 동시에 HTTP 100건을 처리한다는 뜻이 아닙니다. 현재 조건에서의 확장성을 확인하고, 다음 단계로 장시간 유지·조회/수정/삭제 혼합 시험을 권합니다.''',True)
    md('scope',f'''## 무엇을 측정했는가

2026년 9월 28일 로컬 Docker 환경에서 **1 → 5 → 10 → 25 → 50 → 75 → 100명**으로 같은 사용자 집단을 확장했습니다. 각 단계는 목표 인원 도달 후 15초 안정화, 약 60초 측정입니다. 프로젝트 또는 세션 생성을 50:50 확률로 선택하고, 요청 사이 0.5~1.5초를 무작위로 기다립니다.

응답 시간은 HTTP 클라이언트에서 관측한 값입니다. **p95는 요청의 약 95%가 그 시간 이내에 끝났다는 의미**이며 endpoint별로 계산합니다. 처리량은 두 생성 API의 요청 수를 경과 시간으로 나눴고, 같은 작업을 한 번 더 기록하는 FLOW 통계는 합산하지 않았습니다.

환경은 API 4개 프로세스와 별도 PostgreSQL·Redis, Docker VM 14 논리 CPU / 약 15.6GiB입니다. 사용자 등록·기본 프로젝트 준비는 안정화 이전에 수행하여 측정에서 제외했습니다. 기존 Run Worker와 Task reconciler의 백그라운드 동작은 유지했으며 컨테이너 자원 수치에 포함됩니다. LLM·LangGraph 실행·Executor 제출은 이번 범위에 없습니다.''',True)
    md('throughput',f'''## 사용자가 늘면서 생성 처리량이 증가했습니다

1명에서 {base['rps']:.2f}건/초, 100명에서 {peak['rps']:.2f}건/초였습니다. 1명 처리량을 100배 한 값 대비 비율은 {efficiency*100:.1f}%입니다. 아래 막대는 각 단계의 독립 측정 구간을 비교합니다. 1명 기준은 표본이 작고 대기 시간이 무작위이므로 이 비율을 정밀한 확장 효율로 보지는 않습니다.

대기 시간이 평균 약 1초이므로 100명 조건에서 요청률도 약 100건/초 부근으로 제한됩니다. 이 곡선은 실제 시험한 사용자 행동의 처리량이며, 서버가 더 이상 처리하지 못하는 포화점으로 해석하면 안 됩니다. 100명 구간의 처리량 × 평균 응답 시간으로 계산한 평균 진행 중 HTTP 요청 수는 약 {result['estimated_mean_inflight_at_100']:.1f}건입니다. 이는 직접 계측한 동시 처리 수가 아니라 정상 상태를 가정한 근삿값입니다.''',True)
    chart('throughput_chart','동시 사용자별 생성 처리량','stages','user_label','rps','요청/초')
    md('latency',f'''## 100명 단계의 생성 API p95는 최대 {peak_l}ms였습니다

100명에서 프로젝트 생성 p95는 {peak['project_p95_ms']}ms, 세션 생성 p95는 {peak['session_p95_ms']}ms였습니다. 전체 단계 중 가장 높은 endpoint p95는 {max_l}ms입니다. 아래 차트에서 두 생성 API를 같은 단위로 비교하고, 표에서 표본 수·중앙값·꼬리 지연을 확인할 수 있습니다.

1명 단계는 총 {base['requests']}건뿐이므로 p99는 극소수 요청의 영향을 크게 받습니다. 각 단계 1회 측정 결과로 인접 단계의 작은 차이를 구조적인 성능 저하로 단정하지 않았습니다. 사용자가 정한 SLO가 없어 임의의 합격선을 적용하지 않았습니다.''',True)
    chart('latency_chart','생성 API별 p95 응답 시간','endpoints','user_label','p95_ms','밀리초','endpoint')
    md('detail_reading','아래 표는 각 단계의 약 60초 측정값입니다. 요청 수·오류는 HTTP 기준이며, p95는 두 API를 각각 표시했습니다. FLOW 통계와 중복 합산하지 않았습니다.',True)
    table('detail','단계별 요청 수와 응답 시간','stages',[
        ('users','사용자'),('requests','요청 수'),('rps','요청/초'),('errors','오류'),
        ('project_p95_ms','프로젝트 p95 ms'),('session_p95_ms','세션 p95 ms')])
    worst_project=max(r['project_max_ms'] for r in summary)
    worst_session=max(r['session_max_ms'] for r in summary)
    md('tail',f'''## 오류가 없어도 짧은 지연 급증은 별도로 봐야 합니다

전체 단계에서 관측한 최대 응답 시간은 프로젝트 {worst_project:.0f}ms, 세션 {worst_session:.0f}ms였습니다. 50명 구간에서 두 API의 최대값이 모두 올라갔고, 세션 p99는 {fifty['session_p99_ms']}ms였습니다. 100명 구간의 p99는 프로젝트 {peak['project_p99_ms']}ms·세션 {peak['session_p99_ms']}ms로, p95보다 높았습니다.

아래 표는 평균·중앙값·꼬리 지연을 함께 보여줍니다. 전반적인 처리량은 유지됐지만 간헐적인 느린 요청은 있습니다. 서버·DB 세부 trace가 없어 원인은 확정하지 않았으며, 다음 장시간 시험에서는 느린 요청의 trace와 DB 대기·호스트 부하를 같은 시간축으로 수집하는 것이 좋습니다. 뒤 단계의 p95가 더 낮다는 사실도 워밍 효과와 순서 효과가 섞여 있으므로 사용자 증가가 성능을 개선했다고 해석하지 않습니다.''',True)
    table('endpoint_detail','API별 지연 분포 요약','endpoints',[
        ('users','사용자'),('endpoint','API'),('requests','요청 수'),('avg_ms','평균 ms'),
        ('p50_ms','p50 ms'),('p95_ms','p95 ms'),('p99_ms','p99 ms'),('max_ms','최대 ms')])
    md('resources',f'''## 자원 표본으로 병목 징후를 함께 확인했습니다

100명 단계의 API CPU 평균은 {rr['api_cpu_avg']:.1f}%, PostgreSQL은 {rr['postgres_cpu_avg']:.1f}%, 부하 발생기 Locust는 {rr['locust_cpu_avg']:.1f}%였습니다. **여기서 100%는 논리 코어 하나**이며, 14코어 VM 전체 사용률이 아닙니다. API 메모리 표본 최댓값은 {rr['api_memory_max_mib']:.1f}MiB였습니다.

해당 단계의 DB 연결 수 표본 최댓값은 {rr['db_connections_max']}개, 잠금 대기가 잡힌 표본은 {rr['lock_wait_samples']}개입니다. 전체 테스트 DB deadlock 증가량은 {deltas['deadlocks']}입니다. 표본 간 짧은 스파이크는 놓칠 수 있고, CPU만으로 병목 원인을 확정할 수는 없습니다. 다른 로컬 컨테이너가 같은 VM 자원을 공유하므로 운영 환경 수치로 직접 환산하지 않습니다.''',True)
    chart('cpu_chart','단계별 컨테이너 평균 CPU','cpu','user_label','cpu_avg_pct','CPU % · 100%=1코어','service')
    md('resource_reading',f"아래 표의 CPU 최댓값은 수초 간격 표본에서 관측한 최대이며 순간 최대를 보장하지 않습니다. DB 연결 수는 CRUD용 chat_app DB만 집계했습니다. PostgreSQL 메모리 표본 최댓값은 1명 구간 {resource_summary[0]['postgres_memory_max_mib']:.1f}MiB에서 100명 구간 {rr['postgres_memory_max_mib']:.1f}MiB로 증가했습니다. 연결·캐시 증가와 누수 여부를 구분하려면 장시간 유지 및 부하 종료 후 관측이 필요합니다.",True)
    table('resource_detail','단계별 자원 표본','resources',[
        ('users','사용자'),('samples','표본 수'),('api_cpu_max','API CPU 최대 %'),
        ('postgres_cpu_max','DB CPU 최대 %'),('api_memory_max_mib','API 메모리 MiB'),
        ('db_connections_max','DB 연결 최대'),('lock_wait_samples','잠금 대기 표본')])
    md('integrity',f'''## 생성 데이터와 호출 범위를 대조했습니다

테스트 전후 DB에서 사용자가 {deltas['users']:,}명, 프로젝트가 {deltas['projects']:,}개, 세션이 {deltas['sessions']:,}개 증가했습니다. 프로젝트 증분에는 신규 사용자별 기본 프로젝트 100개가 포함됩니다. 이 증분은 안정화 구간까지 포함하므로 위 측정 구간 요청 수와 직접 같지는 않습니다.

Agent Run·Task 증분은 각각 {deltas['agent_runs']}·{deltas['tasks']}이고, Mock Executor 고유 제출 수는 {meta['before_mock_executor']['unique_submissions']}건에서 변하지 않았습니다. 따라서 이번 결과에 에이전트 실행·Executor 제출 부하는 포함되지 않았습니다. 최종 Locust 상태는 `{meta['final_locust']}`이며 테스트 데이터는 이력 확인을 위해 유지했습니다.''',True)
    md('actions', '''## 다음 시험은 유지 시간과 실제 기능 조합을 넓히는 것이 좋습니다

1. **100명을 15~30분 유지**하여 메모리·DB 연결·오류율이 시간에 따라 변하는지 확인합니다. 이번 60초 구간으로 누수나 장시간 안정성을 판단할 수는 없습니다.
2. **목록 조회·상세 조회·수정·삭제를 혼합**하고 실제 사용자 행동 비율을 적용합니다. 프로젝트별 세션·메시지가 많은 조건도 별도로 추가합니다.
3. **생성 API SLO를 먼저 합의**하고 반복 시험합니다. 동일 조건 3회 이상 및 순서를 바꾼 시험으로 데이터 누적·워밍 효과를 분리합니다.
4. **다음은 세션부터 Executor 제출까지의 시나리오**를 같은 단계로 측정합니다. CRUD 결과만으로 Run Worker·checkpoint·HITL 처리 한계를 추정하지 않습니다.''')
    md('questions', '''## 추가로 확인할 질문

- 실제 사용자는 평균 몇 초마다 프로젝트나 세션을 만들며, 읽기·수정·삭제 비율은 얼마인가?
- 평균 응답 시간뿐 아니라 p95·p99 및 허용 오류율 목표는 무엇인가?
- 운영 환경의 CPU·메모리·DB 연결 제한과 데이터 규모는 이 로컬 환경과 얼마나 다른가?
- 한 프로젝트에 요청이 집중되는 경우와 사용자별로 분산되는 경우가 어떻게 다른가?''')
    md('caveats', '''## 해석 범위와 한계

- 여기서 CRUD는 기존 `crud` 시나리오 이름이며 **실제로 부하를 준 작업은 프로젝트·세션 생성**입니다. 사용자 등록·프로젝트 목록 조회는 준비 구간에서만 수행했습니다.
- 인증은 현재 코드의 사용자 UUID Bearer 확인 경로입니다. 별도 OIDC/JWT 공급자 호출 부하는 포함하지 않았습니다.
- 단일 로컬 머신, API 4개 프로세스, 7개 단계 각 1회입니다. 100명 이상의 한계나 운영 용량을 증명하지 않습니다.
- 사용자당 한 요청이 끝난 뒤 대기하는 closed-loop 모델입니다. 지연이 커지면 요청률이 자연히 줄어드는 특성이 있어 고정 도착률의 폭주를 재현하지 않습니다.
- DB는 단계 사이 초기화하지 않았습니다. 뒤 단계에는 더 큰 데이터와 더 오래 살아 있는 사용자·연결이 함께 포함됩니다.
- 백분위수는 Locust histogram의 근사값이며 서로 평균하지 않았습니다. 자원은 수초 간격의 표본이고, 순간 피크나 DB 내부 대기 시간을 전부 포착하지 않습니다.
- PostgreSQL rollback 누적값에는 세션 종료 시의 읽기 트랜잭션 정리도 들어갈 수 있으므로 HTTP 오류로 간주하지 않았습니다.
- 이전 `user_request is required` 오류가 발생하는 에이전트 호출·resume 경로는 실행하지 않았으므로, 이번 오류 0건으로 해당 문제 해결 여부를 판단할 수 없습니다.
- 전체 API 호출 성공만 확인한 부하 시험입니다. 모든 생성 행에 대한 별도 내용 검증이나 실패 주입·장애 복구 시험은 포함하지 않았습니다.''')
    artifact={'surface':'report','manifest':{'version':1,'surface':'report','title':title,
        'description':'로컬 프로젝트·세션 생성 API의 1~100명 단계 시험 · 2026년 9월 28일',
        'generatedAt':meta['finished_at'],'sources':[source],'blocks':blocks,'charts':charts,'tables':tables},
        'snapshot':{'version':1,'generatedAt':meta['finished_at'],'status':'ready',
            'datasets':{'stages':summary,'endpoints':endpoint_rows,'resources':resource_summary,'cpu':cpu_rows}},'sources':[source]}
    sql_datasets,sql_sources=build_sql_evidence(root,stages,resources,memory_mib)
    # Independently aggregated SQL must match every field displayed in the report.
    for dataset,rows in sql_datasets.items():
        original=artifact['snapshot']['datasets'][dataset]
        key=lambda r: (r['users'],r.get('endpoint',r.get('service','')))
        expected={key(r):r for r in original}
        for row in rows:
            prior=expected[key(row)]
            assert all(prior[k]==v for k,v in row.items()), (dataset,row,prior)
    artifact['snapshot']['datasets']=sql_datasets
    artifact['manifest']['sources']+=sql_sources
    artifact['sources']+=sql_sources
    for item in artifact['manifest']['charts']+artifact['manifest']['tables']:
        item['sourceId']=item['dataset']+'_sql'
    (out/'artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2))
    notes={'audience':'product stakeholders','delivery':'portable HTML in Codex desktop',
        'structure':'title, Executive Summary, scope definitions, throughput, latency, tail-latency, resources, integrity, recommendations, open questions, caveats',
        'chart_map':['throughput: discrete plateau comparison, single blue bar series','latency: grouped bars, endpoint comparison, 2 categories','CPU: grouped bars, three distinct services; 100%=one logical CPU'],
        'omitted_metrics':['No full CRUD read/update/delete load','No maximum throughput: think time limits arrivals','No raw latency samples/confidence intervals','No LLM/Executor processing benchmark'],
        'reproduction':f'.venv/bin/python scripts/loadtest/crud_ramp.py --output {args.input}',
        'analysis':f'.venv/bin/python scripts/loadtest/build_crud_report.py --input {args.input} --output {args.output}',
        'validation':'All seven user counts; stable observed users; HTTP/FLOW counts within 2; no setup requests in measured interval; >=5 resource samples per stage; DB users +100; no new Agent Runs/Tasks/Executor submissions.'}
    (out/'source-notes.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2))
    print(json.dumps({'total_requests':total,'errors':errors,'last_stage':peak,'last_resource':rr,'db_delta':deltas},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
