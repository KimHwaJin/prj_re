"""Capture validation rejects misleading completeness/capacity/model comparisons."""
import copy
import gzip
import json
import os
from pathlib import Path

import pytest
from analyze import analyze


@pytest.fixture
def capture():
    path=os.environ.get('DTEST_WORKER_E2E_CAPTURE')
    if not path:pytest.skip('Set an actual disposable HTTP capture')
    capture_path=Path(path);data=capture_path.read_bytes()
    return json.loads(gzip.decompress(data) if capture_path.suffix=='.gz' else data)


def test_complete_actual_http_capture(capture):
    assert analyze(capture)['successes']==capture['config']['users']


@pytest.mark.parametrize('defect',['missing_user','duplicate_run','over_capacity','missing_role','short_model_delay','leftover_owner','failed_command','duplicate_event','held_slot','held_crud_connection'])
def test_reject_misleading_capture(capture,defect):
    value=copy.deepcopy(capture)
    if defect=='missing_user':value['results'].pop()
    elif defect=='duplicate_run':value['database']['runs'][-1]['run_id']=value['database']['runs'][0]['run_id']
    elif defect=='over_capacity':value['server']['peak_shared']=value['config']['total_capacity']+1
    elif defect=='missing_role':value['server']['models'].pop()
    elif defect=='short_model_delay':
        value['config']['delay_ms']=5000
        value['server']['models'][0]['end']=value['server']['models'][0]['start']+.1
    elif defect=='leftover_owner':value['database']['session_owners']=1
    elif defect=='failed_command':value['database']['commands'][0]['state']='FAILED'
    elif defect=='duplicate_event':value['server']['event_handlers'].append(value['server']['event_handlers'][0])
    elif defect in ('held_slot','held_crud_connection'):
        if not value['hold_proof']:pytest.skip('Use a burst capture to validate idle hold evidence')
        if defect=='held_slot':value['hold_proof'][0]['agent_active']=1
        else:
            for sample in value['hold_proof']:sample['crud_checkedout']=1
    with pytest.raises(AssertionError):analyze(value)
