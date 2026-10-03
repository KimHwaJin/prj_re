import json,os,subprocess,sys,time
from pathlib import Path
root=Path('/Users/a10054/.codex/worktrees/refactor-bootstrap/dtest-agent')
base=Path('/private/tmp/dtest-worker-e2e-20261003');captures=base/'matrix';captures.mkdir(exist_ok=True)
runner=root/'scripts/benchmarks/worker_e2e/run.py'
sys.path.insert(0,str(runner.parent));from analyze import analyze
cases=[]
for scenario in ('approval','executor','result_burst','mixed'):
 for users in (1,10,30,50):
  for repeat in range(1,4 if users==50 else 2):
   for architecture in (('split','common') if repeat%2 else ('common','split')):
    cases.append((scenario,users,repeat,architecture))
# Supplemental full analysis + two follow-up requests, true Store and middleware.
for mode in ('manual','auto_context'):
 for users in (1,10):
  for architecture in ('common','split'):
   cases.append(('followup-'+mode,users,1,architecture))
# Matched current-source control isolates NOTIFY from command scheduler sharing.
for repeat in (1,2,3):cases.append(('notify-off',50,repeat,'common'))
for index,(scenario,n,repeat,arch) in enumerate(cases,1):
 folder=captures/f'{scenario}-{n}-{repeat}-{arch}'
 if folder.exists():
  paths=list(folder.rglob('raw.json'))
  if paths and (folder/'cleanup.json').exists():
   analyze(json.loads(paths[0].read_text()));continue
  raise RuntimeError('Incomplete capture exists; preserve and diagnose before replay')
 actual='executor' if scenario.startswith('followup-') else 'approval' if scenario=='notify-off' else scenario
 cmd=[sys.executable,str(runner),'--database-url','postgresql+asyncpg://postgres:benchmark-local@127.0.0.1:63372/postgres',
  '--redis-url','redis://127.0.0.1:63373/0','--source-commit','faca5b1' if arch=='split' else '32f443e',
  '--output',str(folder),'--users',str(n),'--concurrency','20','--delay-ms','5000','--scenario',actual,'--trial-index',str(repeat)]
 if arch=='split':cmd.extend(['--source-root',str(base/'baseline')])
 if scenario.startswith('followup-'):cmd.extend(['--followup','--memory-mode',scenario.removeprefix('followup-')])
 if scenario=='notify-off':cmd.extend(['--notify','off'])
 print(json.dumps({'starting':index,'total':len(cases),'case':folder.name}),flush=True)
 with (base/'controller.txt').open('a') as output:
  result=subprocess.run(cmd,cwd=root,stdout=output,stderr=subprocess.STDOUT,timeout=700)
 if result.returncode:raise RuntimeError('Trial failed; inspect controller and local capture: '+folder.name)
 raw=next(folder.rglob('raw.json'));summary=analyze(json.loads(raw.read_text()))
 print(json.dumps({'completed':index,'case':folder.name,**{k:summary[k] for k in ('mean_seconds','p95_seconds','makespan_seconds','shared_peak','empty_claims','event_defer_attempts')}}),flush=True)
 (folder/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'matrix_complete':len(cases)}),flush=True)
