"""Offline verification of saved review evidence; no DB/Redis/service access."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[2]
checks = []


def check(name, condition):
    checks.append({'name': name, 'passed': bool(condition)})
    assert condition, name


def read(name):
    return json.loads((ROOT / name).read_text())


inventory = read('inventory.json')
fks = inventory['foreign_keys']
check('36 API model FK', inventory['model_fk_count'] == len(inventory['model_foreign_keys']) == 36)
check('40 migrated FK', inventory['database_fk_count'] == len(fks) == 40)
check('No FK definition differences', not inventory['model_database_fk_differences'])
check('All FK validated', all(fk['convalidated'] for fk in fks))
check('CRUD migration head', inventory['versions']['crud'] == [{'version_num': '20261003_0027'}])
check('Worker migration head', inventory['versions']['worker'] == [{'version_num': 'ew_0002'}])
missing = set()
for fk in fks:
    candidates = [ix['index_name'] for ix in inventory['indexes']
                  if ix['table_name'] == fk['child'] and ix['indisvalid']
                  and ix['amname'] == 'btree' and ix['predicate'] is None
                  and ix['columns'] and ix['columns'][0] in fk['columns']]
    check(f"Index coverage recomputed: {fk['conname']}", candidates == fk['usable_unfiltered_leading_index'])
    if not candidates:
        missing.add((fk['child'], tuple(fk['columns'])))
check('Exactly seven specified index coverage gaps', missing == {
    ('agent_commands', ('invocation_id',)), ('agent_commands', ('session_id',)),
    ('agent_runs', ('agent_message_id',)), ('agent_runs', ('interpreted_message_id',)),
    ('project_members', ('user_id',)), ('tasks', ('trigger_message_id',)),
    ('workflow_executions', ('catalog_id',)),
})
actions = {'a': 'NO ACTION', 'r': 'RESTRICT', 'c': 'CASCADE', 'n': 'SET NULL', 'd': 'SET DEFAULT'}
for model in inventory['model_foreign_keys']:
    matches = [fk for fk in fks if all(fk[key] == model[key]
               for key in ('child', 'columns', 'parent', 'parent_columns'))]
    check(f"Model FK alignment: {model['child']}.{','.join(model['columns'])}", len(matches) == 1
          and actions[matches[0]['confdeltype']] == model['ondelete']
          and matches[0]['condeferrable'] == model['deferrable']
          and matches[0]['condeferred'] == model['deferred'])
probes = read('probes.json')
check('16 distinct final probes', len(probes) == len({p['name'] for p in probes}) == 16)
check('Final outcomes match expectations', all(p['passed'] and p['actual_sqlstate'] == p['expected_sqlstate'] for p in probes))
check('Six directly accepted cross-owner/run/session fixtures', sum(bool((p.get('observations') or {}).get('database_accepts')) for p in probes) == 6)
check('Observed FK rejects', sum(p['actual_sqlstate'] == '23503' for p in probes) == 5)
check('Observed lock timeout', sum(p['actual_sqlstate'] == '55P03' for p in probes) == 1)
supplement = read('supplement.json')
check('Nullable Workflow source Run', supplement['source_run_nullable']['is_nullable'] == 'YES')
indexes = [i for i in inventory['indexes'] if i['index_name'] in ('ix_task_events_task_sequence', 'uq_task_events_task_sequence')]
check('Exact duplicate sequence index keys', len(indexes) == 2 and all(
    i['columns'] == ['task_id', 'sequence'] and i['amname'] == 'btree' and i['predicate'] is None
    and i['indisvalid'] for i in indexes) and sum(i['indisunique'] for i in indexes) == 1)
check('No INCLUDE columns on duplicate indexes', all(i['indnkeyatts'] == i['indnatts'] == 2 for i in supplement['task_event_sequence_indexes']))
cleanup = read('cleanup.json')
check('Owned temporary resource removed', cleanup['owned_removed'])
check('All 18 original services still running', cleanup['original_running_count'] == cleanup['original_still_running_count'] == 18 and not cleanup['original_missing'])
for filename, expected in read('source-audit.json')['files'].items():
    check(f'Source unchanged: {filename}', hashlib.sha256((WORKSPACE / filename).read_bytes()).hexdigest() == expected)
report = (ROOT / 'README.md').read_text()
check('All 40 FK decisions in report', sum(line.startswith('| ') and line.split('|')[1].strip().isdigit() for line in report.split('## 전체 40개 관계 판정', 1)[1].splitlines()) == 40)
(ROOT / 'verification.json').write_text(json.dumps({'all_passed': True, 'checks': checks}, indent=2) + '\n')
print(f'{len(checks)} evidence checks passed; no runtime/performance change claimed')
