"""Negative capture checks: fail loudly on wrong grain or missing persistence."""
import copy
import json
import os
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_checkpoint import analyze_checkpoint


@pytest.fixture
def capture():
    path = os.environ.get('DTEST_CHECKPOINT_CAPTURE')
    if not path:
        pytest.skip('Set DTEST_CHECKPOINT_CAPTURE to a real measured raw.json')
    return json.loads(Path(path).read_text())


def test_valid_capture(capture):
    result = analyze_checkpoint(capture)
    assert result['latest_state_payload_bytes'] < result['logical_payload_bytes']
    assert sum(c['blob_bytes'] for c in result['channels']) == result['sizes']['blob_bytes']


@pytest.mark.parametrize('damage', ['missing_checkpoint', 'duplicate_blob', 'orphan_write',
                                  'changed_size', 'saver_failure', 'wrong_parent', 'missing_thread'])
def test_rejects_invalid(capture, damage):
    raw = copy.deepcopy(capture); p=raw['checkpoint_profile']
    if damage == 'missing_checkpoint': p['checkpoints'].pop()
    elif damage == 'duplicate_blob': p['blobs'].append(p['blobs'][0])
    elif damage == 'orphan_write': p['writes'][0]['checkpoint_id']='missing'
    elif damage == 'changed_size': p['blobs'][0]['bytes']+=1
    elif damage == 'saver_failure': raw['server']['checkpoint_calls'][0]['error']='InjectedFailure'
    elif damage == 'wrong_parent': p['checkpoints'][-1]['parent_checkpoint_id']='missing'
    elif damage == 'missing_thread': raw['config']['users']+=1
    with pytest.raises(AssertionError): analyze_checkpoint(raw)


@pytest.fixture
def lock_capture():
    path = os.environ.get('DTEST_CHECKPOINT_LOCK_CAPTURE')
    if not path:
        pytest.skip('Set DTEST_CHECKPOINT_LOCK_CAPTURE to the supplemental capture')
    return json.loads(Path(path).read_text())


def test_preserved_lock(lock_capture):
    result=analyze_checkpoint(lock_capture)
    assert result['lock_summary']['observed_peak_inside_lock']==1


@pytest.mark.parametrize('damage',['missing_lock','invalid_wait','invalid_hold'])
def test_rejects_bad_lock(lock_capture,damage):
    raw=copy.deepcopy(lock_capture)
    row=raw['server']['checkpoint_calls'][0]
    if damage=='missing_lock': row.pop('locks')
    elif damage=='invalid_wait': row['locks'][0]['wait_ms']=10000000
    else: row['locks'][0]['hold_ms']+=100
    with pytest.raises(AssertionError): analyze_checkpoint(raw)
