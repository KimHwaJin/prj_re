"""Independently validate the growth probe and summarize raw SQL/telemetry rows."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics


def analyze(raw):
    checks = 0
    def require(condition, label):
        nonlocal checks
        if not condition: raise ValueError(label)
        checks += 1
    trials = raw['trials']
    require(len({t['thread_id'] for t in trials}) == len(trials), 'fresh thread per trial')
    groups = defaultdict(list)
    for t in trials:
        rows = t['stored_rows']; cp, blobs, writes = (rows[k] for k in ('checkpoints', 'blobs', 'writes'))
        require(all(r['thread_id'] == t['thread_id'] and r['namespace'] == '' for table in (cp, blobs, writes) for r in table), 'root thread isolation')
        require(len({r['checkpoint_id'] for r in cp}) == len(cp), 'unique checkpoints')
        require(len({(r['channel'], r['version']) for r in blobs}) == len(blobs), 'unique blobs')
        require(len({(r['checkpoint_id'], r['task_id'], r['idx']) for r in writes}) == len(writes), 'unique writes')
        ids = {r['checkpoint_id'] for r in cp}
        require(all(not r['parent_checkpoint_id'] or r['parent_checkpoint_id'] in ids for r in cp), 'complete checkpoint ancestor chain')
        require(all(r['checkpoint_id'] in ids for r in writes), 'write checkpoint references')
        require(all(r['type'] == 'empty' or any(p['versions'].get(r['channel']) == r['version'] for p in cp) for r in blobs), 'referenced blobs')
        last = max(cp, key=lambda r: r['checkpoint_id'])
        all_bytes = sum(r['checkpoint_json_bytes'] + r['metadata_json_bytes'] for r in cp) + sum(r['bytes'] for r in blobs) + sum(r['bytes'] for r in writes)
        latest_bytes = last['checkpoint_json_bytes'] + last['metadata_json_bytes'] + sum(r['bytes'] for r in blobs if last['versions'].get(r['channel']) == r['version'])
        require(all_bytes == t['storage']['logical_payload_bytes'], 'independent logical total')
        require(latest_bytes == t['storage']['latest_json_and_referenced_blobs_bytes'], 'independent latest total')
        by_channel = Counter()
        for r in blobs + writes: by_channel[r['channel']] += r['bytes']
        require(dict(by_channel) == {k: sum(v.values()) for k, v in t['storage']['channel_payloads'].items()}, 'independent channel totals')
        calls = t['checkpoint_calls']
        require(not any(r['error'] or r['end'] < r['start'] for r in calls), 'successful nonnegative telemetry')
        require({r['checkpoint_id'] for r in calls if r['method'] == 'aput'} == ids, 'telemetry matches saved checkpoints')
        dump_bytes = Counter()
        for c in calls:
            for ser in c['serialization']:
                dump_bytes[ser['method']] += sum(p['bytes'] for p in ser['channels'])
        require(dump_bytes['_dump_blobs'] == sum(r['bytes'] for r in blobs), 'actual blob serialization matches SQL')
        require(dump_bytes['_dump_writes'] >= sum(r['bytes'] for r in writes), 'writes account for upsert replacement')
        require(all(t['state_outcome'][k] for k in ('approval_preserved', 'duplicate_event_no_extra_delivery', 'summary_preserved', 'pool_returned')), 'semantic invariants')
        require(t['state_outcome']['status'] == 'analysis_completed', 'completed outcome')
        case = t['case']
        expected_ops = case['repairs'] + 1 if case['name'].startswith('repair_') else (case['steps'] + case['batch'] - 1) // case['batch']
        require(t['operation_count'] == expected_ops and t['executor_calls'] == expected_ops + 1, 'operations and finalize count')
        require(t['state_outcome']['repair_attempts'] == case['repairs'], 'repair budget used exactly')
        expected_observations = 3 + 2 * case['repairs'] if case['name'].startswith('repair_') else case['steps']
        require(t['observation_count'] == expected_observations and t['observation_text_chars'] <= 16000 * expected_observations, 'bounded complete observations')
        require(len(t['wait_restore_ms']) == 5 and all(v >= 0 for v in t['wait_restore_ms']), 'five warm restore probes')
        groups[case['name']].append(t)
    expected = {f'output_{n}' for n in (0, 65536, 1048576, 16777216)} | {f'operations_{n}' for n in (1, 5, 20)} | {f'large_operations_{n}' for n in (1, 20)} | {f'repair_{n}' for n in (0, 1, 5, 10)}
    require(set(groups) == expected, 'all thirteen planned conditions covered')
    require(raw['all_completed_rows_read_after_pool_reopen'], 'durable completed decode after reopen')
    summary = []
    for name, rows in groups.items():
        require(sorted(t['trial'] for t in rows) == list(range(1, raw['repeats'] + 1)), 'repeat coverage: ' + name)
        def avg(fn): return statistics.mean(fn(t) for t in rows)
        item = {'case': rows[0]['case'], 'trial_count': len(rows), 'checkpoint_count': avg(lambda t: t['storage']['checkpoint_count']),
            'logical_payload_bytes': avg(lambda t: t['storage']['logical_payload_bytes']),
            'latest_bytes': avg(lambda t: t['storage']['latest_json_and_referenced_blobs_bytes']),
            'graph_ms': {'mean': avg(lambda t: t['graph_and_double_ms']), 'min': min(t['graph_and_double_ms'] for t in rows), 'max': max(t['graph_and_double_ms'] for t in rows)},
            'output_read_ms_mean': avg(lambda t: sum(r['ms'] for r in t['output_read_calls'])),
            'warm_restore_ms_mean': avg(lambda t: statistics.mean(t['wait_restore_ms'])),
            'warm_restore_ms_range': [min(v for t in rows for v in t['wait_restore_ms']), max(v for t in rows for v in t['wait_restore_ms'])],
            'checkpoint_json_share': avg(lambda t: t['storage']['checkpoint_json_and_metadata_bytes'] / t['storage']['logical_payload_bytes']),
            'serialization_ms_mean': avg(lambda t: sum(s['ms'] for c in t['checkpoint_calls'] for s in c['serialization'])),
            'channels': {k: avg(lambda t: sum(t['storage']['channel_payloads'].get(k, {}).values())) for k in rows[0]['storage']['channel_payloads']}}
        summary.append(item)
    return {'assessment': 'Share with caveats: local storage and bounded functional cost; no service capacity or deployment inference',
        'checks_passed': checks, 'trials': len(trials), 'conditions': len(groups), 'summary': sorted(summary, key=lambda r: r['case']['name']),
        'metric_notes': {'logical_payload': 'SQL JSON text bytes + unique blob bytes + retained write bytes; excludes row keys, indexes, WAL, TOAST pages and Python heap',
            'latest': 'latest JSON and metadata plus referenced version blobs; excludes pending writes',
            'graph_ms': 'sum of timed setup/approval/event/replay calls, includes double Python execution/file I/O; excludes SQL capture and five warm wait-restore probes',
            'checkpoint_telemetry': 'method spans overlap and include diagnostic reads outside graph_ms; must not sum and divide by graph_ms',
            'warm_restore': 'five same-process warm saver reads at finalize wait; not graph reconstruction, cold restart, killed Pod, or week-long wait'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = gzip.decompress(args.input.read_bytes()) if args.input.suffix == '.gz' else args.input.read_bytes()
    result = analyze(json.loads(data))
    result['raw_uncompressed_sha256'] = hashlib.sha256(data).hexdigest()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('assessment', 'checks_passed', 'trials', 'conditions')}))


if __name__ == '__main__': main()
