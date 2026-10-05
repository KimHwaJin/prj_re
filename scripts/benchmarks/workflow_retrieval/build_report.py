"""Build canonical portable-report input from independently verified results."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

LABELS = {'exact':'정확한 전량 집계','overfetch_50':'후보 50개','overfetch_200':'후보 200개',
          'expand':'후보 확대(최대 1천개)','exclude_default':'제외·기본 설정',
          'exclude_iterative':'제외·반복 탐색','exclude_tuned':'제외·탐색 폭 500'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--results',type=Path,required=True)
    args=parser.parse_args()
    folder=args.results
    summary=json.loads((folder/'summary.json').read_text())
    receipt=json.loads((folder/'verification.json').read_text())
    assert receipt['status']=='passed'
    data=[]
    for row in summary:
        data.append({**row,'method_label':LABELS[row['method']],
            'query_count_label':str(row['queries_per_workflow'])+'개',
            'index_label':'활성 항목 인덱스' if row['label']=='partial' else '전체 항목 인덱스'})
    primary=[row for row in data if row['label']=='main' and row['query_kind']=='concentrated']
    boundary=[row for row in data if row['label'] in ('main','partial') and row['queries_per_workflow']==500 and row['query_kind']=='boundary']
    concentrated_chart=[row for row in primary if row['method'] in ('exact','overfetch_50','expand','exclude_iterative')]
    boundary_chart=[row for row in boundary if row['method'] in ('exact','overfetch_50','exclude_iterative','exclude_tuned')]
    scale=[row for row in data if row['label']=='scale' and row['query_kind']=='concentrated' and row['method'] in ('exact','overfetch_50','expand','exclude_iterative','exclude_tuned')]
    lookup={(r['label'],r['queries_per_workflow'],r['query_kind'],r['method']):r for r in data}
    exact=lookup[('main',500,'concentrated','exact')]
    iterative=lookup[('main',500,'concentrated','exclude_iterative')]
    filtered=lookup[('main',500,'filtered','exclude_iterative')]
    comparison=lookup[('main',500,'boundary','exclude_iterative')]
    partial=lookup[('partial',500,'boundary','exclude_iterative')]
    improvement=(1-iterative['mean_ms']/exact['mean_ms'])*100
    generated=datetime.now(timezone.utc).isoformat()
    report_title='다중 쿼리 Workflow 검색 — pgvector 검증'
    source={'id':'benchmark','label':'격리 PostgreSQL 합성 검색 측정·독립 검산',
        'path':'docs/reports/workflow-retrieval-2026-10-05/summary.json',
        'query':{'engine':'PostgreSQL 17.11 / pgvector 0.8.6',
            'id':'workflow-retrieval-093','executed_at':json.loads((folder/'main-environment.json').read_text())['recorded_at'],'language':'sql',
            'description':'768차원 합성 벡터. Workflow별 최소 코사인 거리와 근사 검색 결과를 비교한다.',
            'tables_used':['workflow_search.public.retrieval_queries'],
            'sql':(folder/'queries.sql').read_text(),
            'filters':['is_active=true','workflow_id>=minimum_workflow',
                '선택된 Workflow는 후속 제외 검색에서 제외','상위 서로 다른 Workflow 최대 5개'],
            'metric_definitions':{'mean_ms':'SET LOCAL·트랜잭션·검색·클라이언트 집계를 포함한 호스트 왕복 시간의 산술평균. EXPLAIN은 제외.',
                'p95_ms':'각 조건의 측정 지연 95분위. 집중 요청 30개, 경계/필터 요청 각각 15개 관측.',
                'mean_workflows':'중복을 제거한 반환 Workflow 수의 평균.',
                'mean_recall_at_5':'정확한 기준 TOP5와 검색 결과의 교집합 크기 / 5의 평균.',
                'exact_order_rate':'Workflow ID 전체 순서가 정확한 기준과 일치한 측정의 비율.',
                'mean_search_sql_calls':'검색 SELECT 횟수. 세션 설정·BEGIN/COMMIT·진단 EXPLAIN은 제외.'}}}
    def markdown(identity,body,sourced=False):
        return {'id':identity,'type':'markdown','body':body,**({'sourceId':'benchmark'} if sourced else {})}
    blocks=[markdown('title','# '+report_title),
        markdown('summary',f'''## 고정 후보 수는 해결책이 아니며, 반복 제외 검색도 정확한 TOP 5를 보장하지 않는다

Workflow당 쿼리 50개에서 후보 50개 검색은 Workflow 하나만 반환했고 후보 200개도 네 개에 그쳤다. 500개씩 등록한 집중 요청에서는 후보를 최대 1천개로 확대해도 두 Workflow만 반환했다.

반복 인덱스 탐색을 켠 제외 검색은 집중 요청에서 다섯 개를 찾았고, 5만 벡터 평균 시간은 전량 집계 {exact['mean_ms']:.2f}ms → {iterative['mean_ms']:.2f}ms였다({improvement:.1f}% 감소). 그러나 경계 요청의 Recall@5는 {comparison['mean_recall_at_5']:.0%}였다. 개수 확보와 정확한 후보·순위 확보는 별개다.

근사 검색의 설계 후보로 유지하되, 전역 TOP 5 보장이나 운영 성능 검증 완료로 해석하지 않는다.''',True),
        markdown('scope','''## 무엇을 비교했는가

한 번의 요청에서 서로 다른 Workflow 최대 다섯 개를 반환하는 검색을 측정했다. 100개 Workflow × 쿼리 5·50·500개, 1,000개 Workflow × 50개를 구성했다. PostgreSQL 17.11·pgvector 0.8.6, 컨테이너 CPU 두 개·메모리 2GiB, 768차원 합성 벡터를 사용했다.

집중 요청은 가까운 한 Workflow의 쿼리가 상위권을 차지하는 경우, 경계 요청은 두 Workflow 중심 사이의 경우, 필터 요청은 가까운 다섯 Workflow를 검색 대상에서 제외한 경우다. 일부 비활성 Workflow도 포함했다. 실제 자연어·임베딩 API·LLM·Executor·서비스 인증은 포함하지 않았다.

각 셀에서 집중 요청 열 개·경계 다섯 개·필터 다섯 개를 세 번 반복했다. 방법 순서는 섞었고 준비 실행 뒤 측정했다. Recall@5는 정확한 TOP 5 중 검색이 찾아낸 비율이며, 결과 개수나 실행 성공률이 아니다. p95는 이 작은 순차 표본의 기술 통계다.''',True),
        markdown('concentration','''## 동일 Workflow의 쿼리는 검색 후보 자리를 차지한다

아래는 집중 요청의 평균 반환 개수다. 목표는 다섯 개다. 고정 후보 검색은 등록 쿼리 수가 증가하면 감소하고, 후보 확대도 정한 상한에 도달하면 부족해진다. 기본 제외 검색 역시 ef_search=40·반복 탐색 off에서는 50개·500개 조건에서 하나만 반환했다.''',True),
        {'id':'count_chart','type':'chart','chartId':'count','layout':'full'},
        markdown('concentration_detail','''반복 제외 검색은 이 집중 표본에서 모두 다섯 개를 반환했다. 다만 500개 쿼리만 있는 작은 테이블에서는 PostgreSQL이 순차 검색을 선택했고, 제외 검색은 여러 왕복 때문에 전량 집계보다 느렸다. “인덱스가 있으니 항상 빠르다”는 결론도 아니다.''',True),
        {'id':'timing_table','type':'table','tableId':'timing','layout':'full'},
        markdown('quality','''## 다섯 개를 반환해도 후보와 순위가 달라질 수 있다

5만 벡터의 경계 요청에서는 전체 항목 인덱스의 반복 제외 검색이 평균 네 개, Recall@5 68%를 반환했다. 활성 항목만 넣은 인덱스에서는 다섯 개를 반환했지만 Recall@5는 80%였다. 탐색 폭을 500으로 늘린 활성 인덱스도 84%였다.

각 인덱스는 한 번씩 구축했으므로 이 차이를 부분 인덱스만의 인과적 개선으로 일반화하지 않는다. 핵심은 활성 필터를 고려해도 근사 검색의 누락·순위 차이가 남는다는 점이다.''',True),
        {'id':'recall_chart','type':'chart','chartId':'recall','layout':'full'},
        markdown('quality_detail','''실제 반례에는 다섯 개를 반환하면서 정확한 기준의 Workflow 하나를 놓친 요청과, 전체 항목 인덱스에서 비활성 근접 쿼리 때문에 빈 결과가 나온 요청이 있다. 결과가 비었다고 “적합한 Workflow가 없다”고 단정하면 안 된다. 근사 검색에는 확인되지 않은 후보가 있을 수 있다.''',True),
        {'id':'quality_table','type':'table','tableId':'quality','layout':'full'},
        markdown('cost',f'''## 전체 행 계산을 피했지만, 제외한 쿼리의 탐색 비용은 남았다

5만 벡터 집중 요청의 EXPLAIN 표본에서 전량 집계는 5만 행을 순차 검색했다. 반복 제외 검색은 HNSW를 사용했고, 다섯 번째 조회에서 이미 선택한 Workflow에 속한 2천 행을 필터로 제거했다. 제외 조건이 내부 탐색을 무료로 만드는 것은 아니다.

비활성·범위 필터가 더 강한 요청은 반복 제외 검색 평균 {filtered['mean_ms']:.2f}ms, p95 {filtered['p95_ms']:.2f}ms였다. 같은 총 5만 벡터를 1,000개 Workflow × 50개로 분산한 대조군도 아래에 따로 표시했다. 이 결과는 동시 부하·원격 DB·운영 Pod의 처리량을 측정한 것이 아니다.''',True),
        {'id':'scale_table','type':'table','tableId':'scale','layout':'full'},
        markdown('method','''## 실행·검산 방법

정확한 비교 기준은 모든 검색 대상 쿼리의 코사인 거리를 계산하고 Workflow별 최소 거리를 집계한 TOP 5다. 후보 방법은 HNSW 검색 후 제한된 후보를 집계했다. 제외 방법은 매 조회에서 이미 찾은 Workflow ID를 제외하며 최대 다섯 번 검색했다.

일반 후보/반복 제외는 ef_search=100, 반복 탐색 strict_order, max_scan_tuples=20,000, scan_mem_multiplier=2를 사용했다. 튜닝 대조군은 ef_search=500이다. jit는 off, HNSW m=16·ef_construction=128이었다.

2,100개 측정 기록을 보존했다. 별도 검산기는 NumPy의 코사인 계산으로 정확한 기준 순위를 재계산하고, 중복·필터·거리·평균·p95·Recall과 순서 일치를 확인했다. 2,200개 검산 항목을 통과했다. 진단 EXPLAIN은 측정 지연에 넣지 않았으며, 버퍼 수는 실행 계획의 부모·자식을 중복 합산하지 않았다.''',True),
        markdown('limitations','''## 해석의 한계

합성 벡터는 쿼리 집중·필터·그룹 검색의 기계적 반례를 확인한다. 한국어 표현·업무 의도·등록 Workflow의 의미 적합성을 평가하지 않는다. 운영 임베딩 모델의 차원도 아직 확인되지 않았고, 768차원은 이번 검증의 명시적 가정이다.

조회 시간에는 로컬 왕복과 설정이 포함되지만 임베딩 생성·API·SSO·파일 읽기·Agent 판단은 빠져 있다. 기존 로컬 컨테이너가 실행 중인 환경이며 콜드 캐시·동시 부하·장기 데이터 변화·여러 인덱스 재구축의 변동은 측정하지 않았다. 값 하나를 운영 기본값이나 SLA로 채택하지 않는다.

근사 검색에서 strict_order는 발견한 후보의 거리 순서다. 발견하지 못한 전역 후보까지 완전하다는 뜻은 아니다. 정확한 전역 TOP 5가 필수라면 이 근사 검색만으로 계약을 확정할 수 없다.'''),
        markdown('next','''## 등록·추천 계약에 반영할 내용

다중 user_queries와 Workflow JSON은 분리하고 각 쿼리를 같은 Workflow ID에 연결한다. 검색 결과의 단위는 Workflow이며 중복을 반환하지 않는다. 쿼리 개수를 고정 후보 배수로 환산해 완전성을 보장한다고 표현하지 않는다.

pgvector 유지 시 활성 검색용 인덱스와 반복 제외 검색을 근사 후보안으로 검토한다. 탐색 상한·시간 상한·검색 방식은 응답/진단에서 확인할 수 있게 하고, 빈 결과와 탐색 제한을 구분한다. 부족할 때 정확한 검색으로 전환하는 정책은 전체 행 계산 비용을 명시하고 별도 결정한다. 다섯 개가 채워졌다는 이유로 누락을 감지했다고 가정하지 않는다.

이번 브랜치는 진단과 설계 문서만 추가했다. 현행 POST/PATCH와 추천 런타임·DB DDL·설정은 바꾸지 않았다. 실제 임베딩 모델과 현업 쿼리 사례로 품질을 평가한 뒤 최종 검색 정책과 등록 API를 구현한다.'''),
        markdown('questions','''## 다음에 결정할 사항

추천을 위한 근사 후보 검색을 허용할지, 정확한 전역 TOP 5가 필수인지 결정한다. 이후 실제 임베딩 모델·차원, 예상 Workflow 수·쿼리 수·동시 요청량, 검색 지연 목표를 확인한다. 이 정보 없이 후보 개수나 탐색 설정을 운영값으로 확정하지 않는다.''')]
    def table(identity,title,dataset,columns,sort):
        return {'id':identity,'title':title,'dataset':dataset,'sourceId':'benchmark',
            'defaultSort':{'field':sort,'direction':'asc'},'density':'dense','layout':'full','columns':columns}
    common=[{'field':'method_label','label':'검색 방식','type':'text'},
        {'field':'mean_ms','label':'평균 ms','format':'number'},
        {'field':'p95_ms','label':'p95 ms','format':'number'},
        {'field':'mean_workflows','label':'평균 Workflow 수','format':'number'},
        {'field':'mean_recall_at_5','label':'Recall@5','format':'percent'},
        {'field':'mean_search_sql_calls','label':'검색 SELECT 수','format':'number'}]
    charts=[{'id':'count','title':'쿼리 수별 반환 Workflow 개수',
        'subtitle':'100개 Workflow·집중 요청 조건별 30회 측정. 목표는 서로 다른 다섯 개.',
        'type':'bar','dataset':'concentration_chart','sourceId':'benchmark',
        'encodings':{'x':{'field':'query_count_label','type':'nominal','label':'Workflow당 등록 쿼리'},
            'y':{'field':'mean_workflows','type':'quantitative','label':'평균 반환 Workflow 수','format':'number'},
            'color':{'field':'method_label','type':'nominal','label':'검색 방식'}},
        'palette':{'kind':'categorical'},'valueFormat':'number'},
        {'id':'recall','title':'경계 요청의 Workflow Recall@5',
        'subtitle':'100개 Workflow·5만 벡터·조건별 15회. 인덱스 구축별 변동은 분리하지 못했다.',
        'type':'bar','dataset':'boundary_chart','sourceId':'benchmark',
        'encodings':{'x':{'field':'method_label','type':'nominal','label':'검색 방식'},
            'y':{'field':'mean_recall_at_5','type':'quantitative','label':'정확한 TOP5 중 검색된 비율','format':'percent'},
            'color':{'field':'index_label','type':'nominal','label':'인덱스 대상'}},
        'palette':{'kind':'categorical'},'valueFormat':'percent'}]
    tables=[table('timing','집중 요청의 시간·반환 개수·정확도','primary',
        [{'field':'queries_per_workflow','label':'쿼리/Workflow','format':'number'}]+common,'queries_per_workflow'),
        table('quality','경계 요청의 인덱스별 결과','boundary',
            [{'field':'index_label','label':'인덱스','type':'text'}]+common+
            [{'field':'full_count_rate','label':'5개 확보 비율','format':'percent'},
             {'field':'exact_order_rate','label':'정확한 순서 일치','format':'percent'}],'index_label'),
        table('scale','1,000개 Workflow × 50개 쿼리 집중 대조군','scale',common,'mean_ms')]
    artifact={'surface':'report','manifest':{'version':1,'surface':'report','title':report_title,
        'generatedAt':generated,'sources':[source],'blocks':blocks,'charts':charts,'tables':tables},
        'snapshot':{'version':1,'generatedAt':generated,'status':'ready','datasets':{
            'concentration_chart':concentrated_chart,'boundary_chart':boundary_chart,
            'primary':primary,'boundary':boundary,'scale':scale}},'sources':[source]}
    (folder/'artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
    notes={'audience':'technical','delivery':'portable HTML in Codex; repository documents are supporting audit/design files',
        'structure_map':{'Title':'title','Technical summary':'summary','Key findings':'concentration,quality,cost',
            'Scope and metrics':'scope','Methodology':'method','Limitations':'limitations',
            'Next steps':'next','Further questions':'questions'},
        'chart_contracts':[{'chart':'count','question':'Does fixed row retrieval retain five distinct Workflows as aliases grow?',
            'family':'grouped bar','rows':len(concentrated_chart),'grain':'query-count × method; main concentrated only',
            'palette':'categorical, up to four approved roots','zero_baseline':True},
            {'chart':'recall','question':'Does filling five results imply exact group TOP5?',
            'family':'grouped bar','rows':len(boundary_chart),'grain':'index × method; 500/query boundary only',
            'palette':'two categorical roots','zero_baseline':True}],
        'source_sha256':{'summary.json':hashlib.sha256((folder/'summary.json').read_bytes()).hexdigest()},
        'not_claimed':['production SLA','semantic model quality','global ANN top5 guarantee','partial-index causal improvement'],
        'query_plans':sorted(path.name for path in folder.glob('*-plans-*.json'))}
    (folder/'report-source-notes.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2)+'\n')
    print('Canonical report artifact: PASS')


if __name__=='__main__':
    main()
