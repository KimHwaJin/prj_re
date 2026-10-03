"""Use actual 2622552 graph/nodes for the four previous-reader PG waits."""
from pathlib import Path
import importlib,json,sys,os
root=Path('/Users/a10054/.codex/worktrees/refactor-bootstrap/dtest-agent')
sys.path.insert(0,str(root/'src'));sys.path.insert(0,str(root/'scripts/diagnostics'))
import pytest
from api_service.test import test_analysis_state_postgres as tests
from profile_analysis_state import reference_sources
names=('agent_service.agents.analysis.planning.graph','agent_service.agents.analysis.execution.nodes')
current={name:importlib.import_module(name) for name in names}
commit,hashes=reference_sources('2622552')
reference={name:sys.modules[name] for name in names}
sys.modules.update(current)
def exact_reference(runtime,*,checkpointer):
 try:
  sys.modules.update(reference)
  return reference[names[0]].build_planning_graph(runtime,checkpointer=checkpointer)
 finally:sys.modules.update(current)
tests.full_read_graph=exact_reference
os.environ['DTEST_IDENTITY_TEST_DATABASE_URL']='postgresql+asyncpg://postgres:state-fixture@127.0.0.1:63374/identity_test'
report=root/'docs/reports/analysis-state-lifecycle-2026-10-04'
result=pytest.main(['-q','--tb=short',str(root/'src/api_service/test/test_analysis_state_postgres.py')])
if result:raise SystemExit(result)
(report/'exact-reference-compatibility.json').write_text(json.dumps({'reference_commit':commit,'reference_source_sha256':hashes,'previous_waits':['plan_review','executor','decision_review','repair_review'],'passed':4,'actual_reference_source':True,'pool_closed_and_reopened':True,'scope':'Real PG checkpoint resumption from actual graph/nodes sources. In-process mock model and Executor; no load measurement.'},indent=2)+'\n')
