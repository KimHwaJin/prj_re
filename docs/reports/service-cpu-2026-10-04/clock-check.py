"""Check CPU-clock wait exclusion and offload capture, outside speed trials."""
import hashlib,json,pathlib,sys,time
from concurrent.futures import ThreadPoolExecutor
W=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(W/'scripts/benchmarks/worker_e2e'))
from cpu_profile import CPUProfile
original=ThreadPoolExecutor.submit
try:
 profile=CPUProfile(lambda:True);assert profile.finish() is None
 profile.start()
 def offload():
  value=b'fixed-clock-probe'*65536
  for _ in range(12):hashlib.sha256(value).digest()
 with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(offload).result()
 time.sleep(.08);result=profile.finish();assert profile.finish() is result
 sleeps=[r for r in result['main'] if r['function']=='<built-in method time.sleep>'];sleep_cpu=sum(r['self_cpu_seconds'] for r in sleeps)
 assert sleep_cpu<.04 and result['offload_thread_cpu_seconds']>0 and result['profile_wall_seconds']>=.08
 out={'passed':True,'sleep_wall_seconds':.08,'sleep_self_cpu_seconds':sleep_cpu,'offload_cpu_seconds':result['offload_thread_cpu_seconds'],'main_cpu_seconds':result['main_thread_cpu_seconds'],'wall_seconds':result['profile_wall_seconds'],'prestart_finish_safe':True,'finish_cached':True};print(json.dumps(out,indent=2));(W/'docs/reports/service-cpu-2026-10-04/clock-check.json').write_text(json.dumps(out,indent=2))
finally:ThreadPoolExecutor.submit=original
