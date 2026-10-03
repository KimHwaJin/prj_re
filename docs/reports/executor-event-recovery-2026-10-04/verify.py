"""Verify archived diagnosis counts, identities, hashes and the stated limitations.

Run with the project's Python: python docs/reports/executor-event-recovery-2026-10-04/verify.py
No DB, Redis, Executor, LLM or network access is needed.
"""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def main():
    checks=0
    def require(condition,label):
        nonlocal checks
        if not condition:raise AssertionError(label)
        checks+=1
    summaries=json.loads((HERE/'summary.json').read_text())
    index=json.loads((HERE/'evidence-index.json').read_text())
    require(len(index)==12,'exact archived raw/log set')
    for receipt in index:
        data=(HERE/receipt['file']).read_bytes()
        require(hashlib.sha256(data).hexdigest()==receipt['sha256'],'compressed SHA')
        decoded=gzip.decompress(data)
        require(hashlib.sha256(decoded).hexdigest()==receipt['decoded_sha256'],'decoded SHA')
        require(len(decoded)==receipt['decoded_bytes'],'decoded bytes')
    expected={'root-reversed-u1':(1,4,3),'large50-r1':(50,20,1050),'large50-r2':(50,20,1050)}
    require({row['name'] for row in summaries}==set(expected),'exact diagnosis cases')
    for summary in summaries:
        name=summary['name'];users,facts,posts=expected[name]
        raw=json.loads(gzip.decompress((HERE/'raw'/f'{name}-raw.gz').read_bytes()))
        requests=raw['server']['executor_requests']
        require(raw['passed'] and not raw['errors'] and len(raw['results'])==users,'complete cohorts')
        require(raw['config']['executor_trace'] and raw['config']['delay_ms']==0 and raw['config']['concurrency']==20,'diagnostic controls')
        require(len(requests)==posts and summary['requests']==posts,'POST count')
        require(all(row['method']=='POST' and row['status_code']==202 and row['error'] is None for row in requests),'all POST202/no transport failure')
        require(len({row['idempotency_key_sha256'] for row in requests})==posts,'unique logical POST keys')
        require(not raw['server'].get('execution_errors') and not raw['mock']['tasks_failed'],'no graph or receiver error')
        require(all(len(row['observations'])==facts for row in raw['results']),'exact final observations')
        require(all(row['state']=='DONE' for row in raw['database']['common_commands']),'all commands done')
        require(all(raw['database'][key]==0 for key in ('session_owners','recovery_tasks','inbox_pending','outbox_pending')),'no pending owners/recovery/inbox')
        require(raw['server']['current_shared']==0 and raw['server']['peak_shared']<=20 and raw['server']['crud_connections_checked_out']==0,'slots and CRUD pool drained')
        require(abs(summary['diagnostic_elapsed_seconds']-raw['elapsed_seconds'])<1e-8,'elapsed audit')
        require(abs(summary['diagnostic_mean_user_seconds']-sum(row['seconds'] for row in raw['results'])/users)<1e-8,'mean audit')
        require(summary['runner_trial_index']==raw['config']['repeat']==1,'runner index explicit; external repetition names differ')
        require(summary['history_requests']==len(raw['mock']['history_requests'])==0,'this E2E does not claim history HTTP coverage')
        handlers=raw['server']['event_handlers']
        require(len({row['event_id'] for row in handlers})==posts and len(handlers)==posts and all(not row['error'] for row in handlers),'exact event handling once')
        if users==1:
            delivered=[row['sequence'] for row in raw['mock']['timeline'] if row['stage']=='event_published']
            require(delivered==[1,6,5,4,3,2,9,8,7,10],'reversed actual Redis delivery')
            require([row['sequence'] for row in raw['database']['commands']]==[6,9,10],'ordered filtered graph commands')
        else:
            boundary=[row for row in requests if row.get('event_sequence')==49]
            require(len(boundary)==50 and all(row['expected_version']==33 and row['status_code']==202 for row in boundary),'prior failing boundary covered')
    report=ET.parse(HERE/'regression.xml').getroot()
    cases=report.findall('.//testcase')
    require(len(cases)==105 and not report.findall('.//failure') and not report.findall('.//error') and not report.findall('.//skipped'),'105 unique regressions/no skip')
    require(any(case.attrib['name']=='test_default_root_history_route_with_real_http_socket_and_inbox' for case in cases),'actual HTTP history coverage distinct from E2E')
    require(any('legacy_reader_is_incompatible_with_new_tagged_pending_write' in case.attrib['name'] for case in cases),'reverse version boundary explicitly tested')
    historical=json.loads((HERE/'historical-first-error.json').read_text())
    require(not historical['original_exception_chain_recorded'] and historical['event_sequence']==49 and historical['operation_number']==16,'prior cause remains unresolved')
    source=HERE.parent/'observation-worker-2026-10-04/incidents/candidate-large20-u50-r1-failure-live.json.gz'
    require(hashlib.sha256(source.read_bytes()).hexdigest()==historical['source_sha256'],'historical evidence unchanged')
    cleanup=json.loads((HERE/'cleanup.json').read_text())
    require(cleanup['original_running_count']==18 and cleanup['original_running_ids_all_preserved'] and cleanup['removed_only_owned_resources'],'original containers preserved')
    audit=json.loads((HERE/'source-audit.json').read_text())
    require(audit['source_checked_against_git'] and not audit['performance_comparison'],'source fixed/diagnosis not a speed claim')
    for file,digest in audit['harness_sha256'].items():
        data=subprocess.check_output(['git','show',audit['measurement_source_commit']+':'+file],cwd=ROOT)
        require(hashlib.sha256(data).hexdigest()==digest,'pinned harness SHA')
    result={'passed':True,'checks':checks,'trials':3,'completed_flows':101,'executor_post_202':2103,
            'regressions':105,'original_cause_resolved':False,'new_to_legacy_pending_write_compatible':False,'performance_comparison':False}
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':main()
