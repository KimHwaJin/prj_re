"""Canonical source-backed HTML report input, not a bespoke HTML renderer."""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics

LABELS={'approval':'승인 대기','executor':'리포트 완료','result_burst':'결과 집중','mixed':'새 요청+결과 혼합'}


def build(output):
    summary=json.loads((output/'summary.json').read_text());rows=json.loads((output/'results.json').read_text())
    verified=json.loads((output/'independent-verification.json').read_text())
    hold=json.loads((output/'hold-validation.json').read_text())
    pairs=defaultdict(dict)
    for row in summary:pairs[(row['scenario'],row['users'])][row['architecture']]=row
    def pair(s,n=50):return pairs[(s,n)]['split'],pairs[(s,n)]['common']
    def reduction(b,a):return (1-a/b)*100
    datasets={};blocks=[];charts=[];tables=[];source_id='measurement'
    generated=datetime.now(timezone.utc).isoformat()
    def md(key,title,body,source=True):
        block={'id':key,'type':'markdown','body':title+'\n\n'+body}
        if source:block['sourceId']=source_id
        blocks.append(block)
    def table(key,title,data,columns,sort='users'):
        datasets[key]=data
        tables.append({'id':key,'title':title,'dataset':key,'sourceId':source_id,
            'defaultSort':{'field':sort,'direction':'asc'},'columns':[{'field':f,'label':label,'type':typ} for f,label,typ in columns]})
        blocks.append({'id':key+'-block','type':'table','tableId':key})
    def comparative(s):
        items=[]
        for n in (1,10,30,50):
            b,a=pair(s,n)
            items.append({'users':n,'cohort':str(n)+'명','before':b['mean_seconds'],'after':a['mean_seconds'],
                'reduction_percent':reduction(b['mean_seconds'],a['mean_seconds']),'repeats':b['repeats'],
                'before_p95':f"{b['p95_min']:.2f}–{b['p95_max']:.2f}", 'after_p95':f"{a['p95_min']:.2f}–{a['p95_max']:.2f}",
                'before_batch':b['makespan_seconds'],'after_batch':a['makespan_seconds']})
        return items
    timing_columns=[('users','동시 사용자','number'),('before','기존 평균 초','number'),('after','현재 평균 초','number'),
        ('reduction_percent','평균 단축 %','number'),('repeats','양쪽 반복 수','number'),('before_batch','기존 전체 종료 초','number'),('after_batch','현재 전체 종료 초','number'),('before_p95','기존 trial p95 범위','text'),('after_p95','현재 trial p95 범위','text')]
    def chart(key,title,data,subtitle):
        datasets[key]=[{'cohort':v['cohort'],'seconds':v[field],'condition':label,'repeats':v['repeats']}
            for v in data for field,label in (('before','기존 분리 16+4'),('after','현재 공통 20'))]
        charts.append({'id':key,'title':title,'subtitle':subtitle,'showDescription':True,
            'type':'bar','intent':'comparison','dataset':key,'sourceId':source_id,
            'encodings':{'x':{'field':'cohort','type':'nominal','label':'동시 사용자'},
                'y':{'field':'seconds','type':'quantitative','label':'평균 초','format':'number','unit':'s'},
                'color':{'field':'condition','type':'nominal','label':'구조'},
                'tooltip':[{'field':'repeats','type':'quantitative','label':'각 조건 반복'}]},
            'palette':{'kind':'categorical'},'labels':{'values':'all'},'legend':{'position':'bottom','sort':'spec'},
            'settings':{'groupMode':'grouped','sort':'none'},'layout':'full'})
        blocks.append({'id':key+'-block','type':'chart','chartId':key})
    before,after=pair('executor');burst_before,burst_after=pair('result_burst');approve_before,approve_after=pair('approval')
    md('title','# 공통 Agent Worker 성능 비교','',False)
    md('summary','## 같은 총 실행 한도에서 결과 처리 대기를 줄였다',
        f"**50명 전체 분석의 사용자 평균은 {before['mean_seconds']:.2f}→{after['mean_seconds']:.2f}초, {reduction(before['mean_seconds'],after['mean_seconds']):.1f}% 단축**이었다. 각 3회 측정했으며, 기존 사용자 16자리+결과 4자리와 현재 공통 20자리의 총 한도를 맞췄다. 결과만 집중된 경우는 {burst_before['mean_seconds']:.2f}→{burst_after['mean_seconds']:.2f}초였다. 모델·Agent 로직을 바꾼 효과가 아니라, 한쪽에서 비는 실행 자리를 다른 요청이 사용하는 효과가 주된 차이다. 알림의 빈 조회 감소는 현재 코드 ON/OFF 대조로 별도 확인한다.\n\n"
        f"**검증한 {verified['trials']}개 trial·{verified['user_scenarios']:,}개 사용자 시나리오는 누락·중복 성공·한도 초과 없이 끝났다.** 임시 결과 유예는 별도 공개한다. SQL 원장 기록 비용은 늘 수 있으므로 시간 단축을 DB 총 비용 감소로 해석하지 않는다. 단일 로컬 프로세스·유한 일제 유입의 서비스 비교이며 Kubernetes 최대 처리량은 아니다.")
    md('definition','## 모델 대기는 모든 역할에서 같고, 측정 종료는 시나리오마다 다르다',
        '실제 create_agent와 Skill 조회·구조 검증·ProjectPrompt·SessionAnalysis·ProjectMemory middleware를 실행했다. 모델 전송만 고정 응답으로 대체하고 **측정 내 매 호출을 5초**로 설정했다. 정상 전체 분석은 계획 선택·계획 작성·review·report의 4콜로 모델 대기만 20초다. 승인 대기는 2콜/10초, 준비된 결과 집중은 review·report 2콜/10초다. 이론상 최소 모델 대기와 실제 요청 완료 시간을 구분한다.\n\n'
        '승인 대기는 프로젝트 생성→계획→편집→HITL, 전체 분석은 그 뒤 승인→Executor HTTP submit/continue/finalize→Redis 결과 재개→리포트 success SSE까지다. 리포트 완료는 Agent Markdown 응답과 ready 상태이며 별도 Executor Artifact POST 등록은 포함하지 않는다. 결과 집중은 모든 제출·owner 반환 뒤 결과 해제→리포트만, 혼합은 ceil(n/2) 준비 결과와 floor(n/2) 새 분석으로 **총 n명**이다. 1명 혼합은 결과 단독과 같다. 비교 표의 서로 다른 행은 이 종료 경계가 다르므로 절대 시간을 서로 성능 순위로 비교하지 않는다.\n\n'
        'API/SSO cookie·CSRF·권한·CRUD·checkpoint·공식 PostgreSQL Store·HTTP 계약·manifest/checksum·Redis Streams/Inbox·SSE는 실제다. 직원 검증 verdict·모델·Executor 분석 출력은 fixture이며 Python 코드를 실행하지 않는다. 로그인·등록·warm-up은 제외하고 사용자 생각 시간은 0이다. 등록 Skill·Tool의 metadata는 실제로 조회하며, Workflow 저장·추천 검색과 Task reconciler를 동일하게 비활성 설정으로 두어 pgvector 검색·정리 작업 비용은 포함하지 않는다.')
    md('flow','## 전체 완료 시간에서 결과 실행 자리 공유 효과가 나타났다',
        f"50명에서 전체 종료까지는 {before['makespan_seconds']:.2f}→{after['makespan_seconds']:.2f}초였다. 아래 막대는 사용자별 평균, 표의 p95는 각 trial의 범위다. 50명만 각 3회이며 다른 코호트는 각 1회다. **반복 p95의 평균을 합친 사용자 모집단 p95로 부르지 않는다.**\n\n"
        '기존 사용자 16자리가 비어도 결과는 4자리에서 기다렸다. 현재는 공통 20자리에서 처리 가능한 명령을 실행한다. Agent 소스는 해시로 동일함을 확인했고 역할별 호출 수도 같다. 이 비교는 16+4를 20으로 합친 것이며 총한도 자체를 늘린 것이 아니다.')
    chart('flow-chart','동시 사용자별 리포트 완료 시간',comparative('executor'),'모델 호출마다 5초·총 실행 한도 20·50명 각 3회, 나머지 각 1회')
    table('flow-table','리포트 완료 시간의 정확한 비교',comparative('executor'),timing_columns)
    md('approval','## 승인 대기는 사용자 실행 자리를 빌려 쓰는 효과를 보여준다',
        f"50명 승인 대기는 평균 {approve_before['mean_seconds']:.2f}→{approve_after['mean_seconds']:.2f}초였다. 이 구간은 Executor를 호출하지 않는다. 기존 결과 4자리의 빈자리를 사용자 요청이 함께 쓸 수 있다. 작은 코호트는 기존 16자리 안에 들어가므로 같은 규모의 개선을 기대하지 않는다. 모델의 연속 호출과 checkpoint·API 반영 시간은 남는다.")
    table('approval-table','승인 대기까지 비교',comparative('approval'),timing_columns)
    md('burst','## 결과가 한꺼번에 돌아와도 새 요청용 자리를 사용할 수 있다',
        f"50개 준비 결과의 평균 완료 시간은 {burst_before['mean_seconds']:.2f}→{burst_after['mean_seconds']:.2f}초, 전체 종료는 {burst_before['makespan_seconds']:.2f}→{burst_after['makespan_seconds']:.2f}초였다. 아래 막대는 **결과 해제 후의 시간**이다. 계획 준비·승인·제출 비용이 빠진 시나리오로, 전체 분석의 모델 대기 20초와 비교하지 않는다.\n\n"
        '준비 코호트는 모델 지연 0초로 만들고 owner 반환을 확인한 뒤 측정 reset·모델 지연 5초 복귀·결과 해제를 한다. 측정 구간에는 실제 결과 HTTP·Redis·manifest·graph·report 경로가 포함된다. 쌓인 작업의 실행 자리 공유 효과를 보는 burst이며 지속 유입의 최대 용량을 추정하는 시험은 아니다.')
    chart('burst-chart','동시 결과별 완료 시간',comparative('result_burst'),'결과 해제→리포트·review/report 각각 5초·계획 준비는 제외')
    table('burst-table','결과 집중 시간 비교',comparative('result_burst'),timing_columns)
    md('hold','## Executor 대기 표본의 순간 DB 사용과 실행 자리 점유를 구분했다',
        f"결과 해제 전 {hold['trials']}개 trial에서 250ms 간격으로 {hold['samples']}개 표본을 남겼고 Agent·결과 실행 자리는 모두 0이었다. 각 trial에는 CRUD 체크아웃 0인 표본이 있었다. 다만 {hold['crud_nonzero_samples']}개 표본은 순간적으로 최대 {hold['crud_peak']}개 연결이 사용 중이었다. 대기 중 모든 DB 연결이 항상 0이라고 주장하지 않는다.\n\n"
        '처음 집계 판정은 모든 표본의 CRUD 사용 0을 요구했지만 원문 확인 후 실행 자리 반환과 순간 DB 사용을 구분하도록 수정했다. 그 표본은 다음 표본에서 0으로 돌아왔다. 해당 순간의 쿼리 owner 진단은 켜지 않았으므로 어느 조회인지 확정하지 않는다. 표본은 짧은 대기 확인이며 일주일 장기 실행을 검증한 것이 아니다. 원문과 hold-validation.json에 보존했다.')
    md('mixed','## 새 요청과 결과가 섞이면 두 사용자 집단을 따로 읽는다',
        '혼합 시나리오는 시작점이 같아도 준비 결과와 새 분석의 작업량이 다르다. 아래 전체 표와 50명 집단별 표를 함께 본다. 기존 결과는 4자리로 제한되고 현재는 공통 자리를 쓰며, 새 요청은 자신의 계획부터 시작한다. 한 집단의 빠른 완료만으로 다른 집단의 대기를 숨기지 않는다.')
    table('mixed-table','혼합 총사용자 비교',comparative('mixed'),timing_columns)
    cohort_data=[]
    for arch in ('split','common'):
        for cohort in ('primary','incoming'):
            values=[r['cohorts'][cohort] for r in rows if r['scenario']=='mixed' and r['users']==50 and r['architecture']==arch]
            cohort_data.append({'condition':'기존16+4' if arch=='split' else '현재공통20','cohort':'준비결과25명' if cohort=='primary' else '새분석25명',
                'mean_seconds':statistics.mean(v['mean_seconds'] for v in values),'trial_p95_min':min(v['p95_seconds'] for v in values),'trial_p95_max':max(v['p95_seconds'] for v in values)})
    table('mixed-cohort','50명 혼합의 집단별 완료 시간',cohort_data,[('condition','구조','text'),('cohort','사용자 집단','text'),('mean_seconds','평균 초','number'),('trial_p95_min','trial p95 최소','number'),('trial_p95_max','trial p95 최대','number')],sort='cohort')
    md('cost','## 원장 기록 비용과 대기 감소를 서로 다른 지표로 평가한다',
        '새 원장은 추가 기록·claim·outcome SQL을 사용한다. 알림이 빈 조회와 대기를 줄여도 **전체 CRUD SQL이 반드시 줄어드는 구조는 아니다.** 아래는 50명 각 3회 평균이며 CPU는 API 프로세스만, SQL은 CRUD asyncpg 계측만이다. checkpoint·Store·Event·bridge의 psycopg SQL과 DB·Redis·mock·client CPU는 포함하지 않는다. API CPU에는 양쪽에 적용한 진단 hook과 표본 조회 비용도 포함되므로 무계측 운영 CPU 예산으로 쓰지 않는다. API CPU가 늘거나 DB 대기가 커지는 경우를 개선율 뒤에 숨기지 않는다.\n\n'
        f"50명 전체 분석의 결과 XADD→handler 시작 대기는 {before['event_wait_mean_ms']/1000:.2f}→{after['event_wait_mean_ms']/1000:.2f}초였다. 사용자당 API CPU는 {before['cpu_per_user_seconds']:.3f}→{after['cpu_per_user_seconds']:.3f}초, CRUD SQL은 {before['crud_sql_per_user']:.2f}→{after['crud_sql_per_user']:.2f}회였다. 반면 풀 획득 p95는 {before['pool_acquire_p95_ms']:.2f}→{after['pool_acquire_p95_ms']:.2f}ms였다. 처리 동시성이 높아져 순간 DB 경쟁이 커질 수 있으며, 총 CPU 감소를 순간 CPU 비율 감소나 HPA 필요량 감소로 해석하지 않는다.\n\n"
        'SQL 합계 시간·모델 시간·pool 대기·slot 시간은 다른 사용자와 중첩한다. 더해서 전체 wall time 비율로 만들지 않는다. DB connection peak는 열린 연결이고 checked-out은 빌려 사용 중인 연결이다. 풀 반환과 열린 연결 수를 구분한다.')
    cost=[]
    for s in LABELS:
        for arch in ('split','common'):
            v=pairs[(s,50)][arch]
            cost.append({'scenario':LABELS[s],'condition':'기존16+4' if arch=='split' else '현재공통20',
                **{k:v[k] for k in ('cpu_per_user_seconds','crud_sql_per_user','user_queue_mean_ms','event_wait_mean_ms','empty_claims','shared_peak','slot_utilization','pool_acquire_p95_ms','loop_lag_p95_ms','rss_peak_mib','db_connections_peak','event_defer_attempts')}})
    table('cost-table','50명 처리 비용과 대기',cost,[('scenario','시나리오','text'),('condition','구조','text'),('cpu_per_user_seconds','API CPU초/사용자','number'),('crud_sql_per_user','CRUD SQL/사용자','number'),('user_queue_mean_ms','User 큐평균ms','number'),('event_wait_mean_ms','Event 대기평균ms','number'),('pool_acquire_p95_ms','풀획득p95 ms','number'),('empty_claims','빈claim','number'),('event_defer_attempts','결과 유예 수','number')],sort='scenario')
    table('resource-table','50명 실행 자리와 자원 표본',cost,[('scenario','시나리오','text'),('condition','구조','text'),('shared_peak','동시 실행 peak','number'),('slot_utilization','총 20자리 사용률','number'),('loop_lag_p95_ms','loop지연p95 ms','number'),('rss_peak_mib','RSS peak MiB','number'),('db_connections_peak','DB연결peak','number')],sort='scenario')
    variation=[{'scenario':LABELS[v['scenario']],'condition':'기존 16+4' if v['architecture']=='split' else '현재 공통 20',
        'repeats':v['repeats'],'mean_min':v['mean_min'],'mean_max':v['mean_max'],'std':v['mean_std_seconds']} for v in summary if v['users']==50]
    table('repeat-table','50명 3회 반복의 사용자 평균 편차',variation,[('scenario','시나리오','text'),('condition','구조','text'),('repeats','반복','number'),('mean_min','trial 평균 최소 초','number'),('mean_max','trial 평균 최대 초','number'),('std','trial 평균 표본 표준편차 초','number')],sort='scenario')
    notify_off=[r for r in rows if r['architecture']=='common' and r['scenario']=='approval' and r['users']==50 and r['notify']=='off']
    md('notify','## 알림 효과는 공통20을 그대로 둔 polling 대조군으로 구분한다',
        f"ON/OFF의 사용자 평균은 {approve_after['mean_seconds']:.2f}/{statistics.mean(r['mean_seconds'] for r in notify_off):.2f}초, 빈 claim은 {approve_after['empty_claims']:.2f}/{statistics.mean(r['empty_claims'] for r in notify_off):.2f}회였다. 이 포화 부하 대조에서 알림이 완료 시간을 추가 단축했다고 주장하지 않는다.\n\n"
        '아래 비교는 현재 소스·공통 20자리·50명 승인 대기·각 3회이고 notify만 ON/OFF다. 따라서 앞의 분리 16+4→공통 20 효과와 섞지 않는다. 모델 실행 중 모든 자리가 찼다면 Worker는 완료를 기다리므로 알림 자체가 처리 용량을 늘리지는 않는다. 전체 시험의 빈 claim 감소율을 유휴 6초 시험의 감소율과 같다고 가정하지 않는다. SQL 전체와 빈 claim 수를 구분한다.')
    notify=[]
    for mode in ('on','off'):
        items=[r for r in rows if r['architecture']=='common' and r['scenario']=='approval' and r['users']==50 and r['notify']==mode]
        notify.append({'mode':mode,'repeats':len(items),**{k:statistics.mean(v[k] for v in items) for k in ('mean_seconds','makespan_seconds','empty_claims','api_cpu_per_user_seconds','crud_sql_per_user')}})
    table('notify-table','현재 코드의 알림 ON/OFF',notify,[('mode','알림','text'),('repeats','반복','number'),('mean_seconds','평균초','number'),('makespan_seconds','전체종료초','number'),('empty_claims','빈claim','number'),('api_cpu_per_user_seconds','CPU초/사용자','number'),('crud_sql_per_user','CRUD SQL/사용자','number')],sort='mode')
    md('memory','## 후속 설명과 프로젝트 메모리는 실제 Store·middleware를 거쳤다',
        '완료된 분석 뒤 같은 세션에서 근거 설명과 보고서 설명 요청 2개를 추가했다. manual은 관리 API로 audience 주제를 저장하고 auto_context는 첫 후속 요청의 현재 사용자 인용으로 저장한다. 두 모드는 다음 질문에서 memory·evidence 참조와 Store의 version 1·source를 확인한다. 원시 관찰을 프로젝트 메모리로 저장하지 않는다.\n\n'
        '모델 호출은 계획 2+review·report 2+후속 answer 2로 6콜/30초다. 아래 후속 시간에는 각 5초 대기와 실제 context·Store 읽기·구조 검증·메시지 반영이 포함된다. 별도 Artifact 파일 재작성·등록은 시험하지 않고 근거 기반 Markdown 응답을 검증한다. 1·10명 각 1회라 작은 차이를 자동 메모리의 확정적 비용으로 일반화하지 않는다.')
    mem=[{'users':r['users'],'mode':r['memory_mode'],'condition':'기존16+4' if r['architecture']=='split' else '현재공통20','mean_seconds':r['mean_seconds'],
        'first_followup':r['followup_mean_seconds'][0],'second_followup':r['followup_mean_seconds'][1],'cpu':r['api_cpu_per_user_seconds'],'sql':r['crud_sql_per_user']} for r in rows if r['followup']]
    table('memory-table','전체 분석과 후속 요청·메모리',mem,[('users','사용자','number'),('mode','메모리정책','text'),('condition','구조','text'),('mean_seconds','전체평균초','number'),('first_followup','첫후속평균초','number'),('second_followup','다음후속평균초','number'),('cpu','CPU초/사용자','number'),('sql','CRUD SQL/사용자','number')])
    md('validity','## 대조 조건과 완료 근거를 유지하고 실패·유예를 숨기지 않았다',
        f"59개 trial·1,722개 사용자 시나리오의 원문 gzip·hash와 별도 산술 검산을 남겼다. Agent Python 소스 {verified['agent_service_files_identical']}개 파일의 hash가 같아 Agent 알고리즘·prompt 변화를 처리량 효과로 섞지 않았다. 고유 Run·Command·Event, 역할별 콜 수, 5초 지연, 총 20자리 peak, 정상 continue·finalize, 관찰 4개, report ready, DB owner·recovery·Inbox/Outbox·CRUD checkout 반환을 검사했다.\n\n"
        '주 비교인 50명은 각 3회 측정했고 두 번째 반복에서 실행 순서를 뒤집었다. 같은 전용 PG·Redis, Python·라이브러리·풀 조건·단일 프로세스를 사용했다. DB는 trial마다 자기 scratch만 초기화해 이전 행 누적의 영향을 제거한다. RSS·DB는 0.5초 표본이라 순간 최대나 오래된 대규모 원장의 비용을 보장하지 않는다. repair는 이번 성공 시나리오에서 호출되지 않았다. 라이브러리 버전·실행기 source·검증 코드는 부속 근거에 보존한다.')
    md('limit','## 이 결과는 운영 HPA나 실제 분석 계산의 용량을 확정하지 않는다',
        '단일 로컬 API·호스트 CPU 제한 없음·finite burst다. 기존 호스트 서비스를 유지했고 백그라운드 부하는 별도로 통제하지 않았다. Kubernetes CPU quota, 멀티 Pod fan-out·인계, CPU 기반 HPA 응답, 배포 migration, 1주 장기 Executor, 실제 LLM provider·사내 SSO·대용량 PVC·MinIO·Jupyter 계산은 이번 측정 밖이다. 고정 지연 모델에는 별도 호출 동시성 제한이 없으며, 실제 모델 수용량이 낮으면 provider 대기가 추가되어 개선율이 달라진다. 과거 057은 계획만 5초이고 review·report가 0초였으므로 이 보고서의 절대 시간과 직접 비교하지 않는다.\n\n'
        '새 명령 원장의 조회량은 작은 정상 원장에 대한 값이다. 공통 20이 최적 concurrency라는 결론도 내리지 않는다. 기존 배포 기본값·컨테이너·DB·env를 바꾸지 않았고 이 브랜치에서 검증 자료만 추가했다. 모델 콜 수·prompt 최적화와 광범위 운영 기능의 보류를 유지한다.')
    md('next','## 다음은 측정된 저장·대기 비용부터 제한된 범위로 확인한다',
        '- 공통 슬롯 구조의 처리 시간·완료 검증을 배포 후보 근거로 사용한다. 실제 Pod CPU quota와 공유 DB 연결 예산에서 한도를 다시 검증한다.\n- 원장 SQL 기록 증가와 pool 대기를 구분하고, 다음 6단계에서 대표 흐름의 checkpoint 저장량·시간을 channel별로 측정한다. 저장 비용이 큰 항목만 최적화한다.\n- 20은 이번 동일 총한도 시험값이다. 측정 없이 DB 풀·concurrency·replica를 한꺼번에 늘리지 않는다.\n- 실제 후속 보고서 Artifact 등록·Dataset Registry·Workflow CRUD·오류 repair 부하는 기존 미확정·후순위 계획에서 따로 다룬다.')
    md('questions','## 운영 한도를 확정하려면 자원 제한과 유입 비율이 필요하다',
        '실제 Pod CPU·memory 한도, 초당 새 요청·결과·HITL·후속 질문 비율, DB 전체 connection budget과 HPA 최대 replica는 얼마인가? 장기 원장이 얼마나 누적되는가? 이 조건들이 확정되어야 이번 burst 결과를 운영 설정으로 옮길 수 있다.')
    source={'id':source_id,'label':'2026년 10월 3–4일 전용 localhost Worker 전체 경로 대조',
        'path':'docs/reports/agent-worker-e2e-2026-10-03/results.json','query':{'engine':'SQLite / PostgreSQL evidence','language':'sql',
        'sql':(output/'aggregation.sql').read_text(),'description':'Executed identical-capacity rollup of validated local HTTP capture trials',
        'tables_used':['trials','public.agent_runs','public.agent_commands','public.ew_commands','public.ew_inbox','public.session_executions'],
        'metric_definitions':['mean_seconds: User wall time with scenario-specific start/stop; registration and warmup excluded',
            'p95: Linear interpolation within each trial; repeat min/max kept separately','crud_sql: Measured asyncpg statements only, not all PostgreSQL SQL',
            'cpu: Measured API process only','event_wait: Original Redis XADD completion to successful handler start'],
        'filters':['split16+4 vs common20, one API process','all measured model calls5000ms','1/10/30/50 synchronized users;50 repeats3',
            'synthetic Executor output; code never executed','supplemental follow-up and notification controls separate']}}
    artifact={'surface':'report','manifest':{'version':1,'surface':'report','title':'공통 Agent Worker 성능 비교','generatedAt':generated,
        'blocks':blocks,'sources':[source],'charts':charts,'tables':tables},
        'snapshot':{'version':1,'status':'ready','generatedAt':generated,'datasets':datasets},'sources':[source]}
    (output/'artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
    markdown='\n\n'.join(b['body'] for b in blocks if b['type']=='markdown')
    (output/'narrative.md').write_text(markdown+'\n')
    notes={'audience':'technical','delivery':'portable canonical HTML, Codex runtime',
        'required_structure_map':{'title':'title','technical_summary':'summary','key_findings':'flow/approval/burst/mixed/cost/notify/memory',
            'definitions':'definition before evidence','methodology':'validity and definition','limitations':'limit and adjacent notes','next_steps':'next','further_questions':'questions'},
        'chart_contracts':[{'id':v['id'],'question':v['title'],'family':'comparison','variant':'grouped bar','row_count':8,'category_count':4,
            'series':'condition encoding; baseline then current; native categorical blue/orange roots','unit':'seconds','palette':'hard two-root: blue baseline, orange current; legend/order/value labels',
            'fallback':'exact table included','qa':'portable delivery receipt; desktop+narrow, light+dark'} for v in charts],
        'tables_without_charts':'Exact p95 ranges, costs and mixed cohort lookup; no fabricated continuous trend from four cohort levels',
        'smoke_excluded':True,'operating_deployment_changed':False}
    (output/'source-notes.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);build(p.parse_args().output)
