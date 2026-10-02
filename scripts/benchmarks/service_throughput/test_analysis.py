"""Validate measurement rejection using a real completed private capture."""
import copy,json,os
from pathlib import Path
import pytest
from analyze import analyze

@pytest.fixture
def capture():
    path=os.getenv('DTEST_SERVICE_THROUGHPUT_CAPTURE')
    if not path:pytest.skip('Select a completed actual service throughput capture')
    return json.loads(Path(path).read_text())

def test_completed_capture(capture):
    result=analyze(capture)
    assert result['passed'] and result['completed_users']==capture['config']['users']

@pytest.mark.parametrize('damage',['worker_missing','worker_duplicate','model_missing','model_duplicate','pool_owner','http_failure','attempt_retry'])
def test_incomplete_or_contaminated_capture_rejected(capture,damage):
    damaged=copy.deepcopy(capture)
    if damage=='worker_missing':damaged['server']['workers'].pop()
    elif damage=='worker_duplicate':damaged['server']['workers'][0]['run_id']=damaged['server']['workers'][1]['run_id']
    elif damage=='model_missing':damaged['server']['models'].pop()
    elif damage=='model_duplicate':damaged['server']['models'][0]['run_id']=damaged['server']['models'][1]['run_id']
    elif damage=='pool_owner':damaged['database']['session_owners']=1
    elif damage=='http_failure':damaged['results'][0]['requests'][0]['status']=503
    else:damaged['database']['runs'][0]['attempt_count']=2
    with pytest.raises(AssertionError):analyze(damaged)
