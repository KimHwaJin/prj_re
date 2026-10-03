"""Validate a saved real capture and reject missing/failed/undrained evidence."""
import gzip,importlib.util,json,os
from copy import deepcopy
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('executor_analysis',Path(__file__).with_name('analyze.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

@pytest.fixture
def capture():
    path=os.getenv('DTEST_EXECUTOR_THROUGHPUT_CAPTURE')
    if not path:pytest.skip('Set a captured full HTTP/PG/Redis flow; no fabricated success fixture')
    raw=Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if path.endswith('.gz') else raw)


def test_saved_complete_capture(capture):
    result=module.analyze(capture)
    assert result['validated'] and result['successes']==capture['config']['users']


@pytest.mark.parametrize('fault',['missing_event','duplicate_event','owner','checkout','command_failure','http_error','missing_report','mock_error','duplicate_model'])
def test_reject_bad_evidence(capture,fault):
    raw=deepcopy(capture)
    if fault=='missing_event':raw['server']['event_handlers'].pop()
    elif fault=='duplicate_event':raw['server']['event_handlers'][0]=raw['server']['event_handlers'][1]
    elif fault=='owner':raw['database']['session_owners']=1
    elif fault=='checkout':raw['server']['crud_connections_checked_out']=1
    elif fault=='command_failure':raw['database']['commands'][0]['failure_attempts']=1
    elif fault=='http_error':raw['server']['http'][0]['status']=503
    elif fault=='missing_report':raw['results'][0]['report']['status']='evidence_only'
    elif fault=='mock_error':raw['mock']['tasks_failed']=['RuntimeError']
    elif fault=='duplicate_model':raw['server']['models'][1]=raw['server']['models'][0]
    with pytest.raises(AssertionError):module.analyze(raw)


def test_deferred_control_is_explicit_not_counted_as_normal(capture):
    raw=deepcopy(capture)
    extra=deepcopy(raw['server']['event_handlers'][0]);extra['error']='DeferEvent'
    raw['server']['event_handlers'].append(extra)
    with pytest.raises(AssertionError):module.analyze(raw)
    result=module.analyze(raw,allow_transient_deferrals=True)
    assert result['validated'] and not result['normal_without_deferrals'] and result['event_deferred_attempts']==1
