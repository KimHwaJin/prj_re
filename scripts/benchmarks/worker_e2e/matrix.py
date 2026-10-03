"""Replay the 59-trial, equal-capacity study sequentially on disposable services."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from analyze import analyze


def study_cases():
    cases = []
    for scenario in ('approval', 'executor', 'result_burst', 'mixed'):
        for users in (1, 10, 30, 50):
            for repeat in range(1, 4 if users == 50 else 2):
                order = ('split', 'common') if repeat % 2 else ('common', 'split')
                cases.extend((scenario, users, repeat, architecture) for architecture in order)
    for mode in ('manual', 'auto_context'):
        for users in (1, 10):
            cases.extend(('followup-' + mode, users, 1, architecture)
                         for architecture in ('common', 'split'))
    cases.extend(('notify-off', 50, repeat, 'common') for repeat in (1, 2, 3))
    return cases


def main(args):
    runner = Path(__file__).with_name('run.py')
    args.output.mkdir(parents=True, exist_ok=True)
    cases = study_cases()
    for index, (scenario, users, repeat, architecture) in enumerate(cases, 1):
        folder = args.output / f'{scenario}-{users}-{repeat}-{architecture}'
        if folder.exists():
            captures = list(folder.rglob('raw.json'))
            receipt = folder / 'cleanup.json'
            if len(captures) != 1 or not receipt.exists():
                raise RuntimeError(f'Preserve and diagnose incomplete capture before replay: {folder}')
            cleanup = json.loads(receipt.read_text())
            if not cleanup['owned_scratch_databases_removed']:
                raise RuntimeError(f'Incomplete cleanup: {folder}')
            analyze(json.loads(captures[0].read_text()))
            continue
        actual = ('executor' if scenario.startswith('followup-')
                  else 'approval' if scenario == 'notify-off' else scenario)
        source_root = args.baseline_root if architecture == 'split' else args.current_root
        source_commit = args.baseline_commit if architecture == 'split' else args.current_commit
        command = [sys.executable, str(runner), '--database-url', args.database_url,
                   '--redis-url', args.redis_url, '--source-root', str(source_root),
                   '--source-commit', source_commit, '--output', str(folder),
                   '--users', str(users), '--concurrency', '20', '--delay-ms', '5000',
                   '--scenario', actual, '--trial-index', str(repeat)]
        if scenario.startswith('followup-'):
            command.extend(['--followup', '--memory-mode', scenario.removeprefix('followup-')])
        if scenario == 'notify-off':
            command.extend(['--notify', 'off'])
        print(json.dumps({'starting': index, 'total': len(cases), 'case': folder.name}), flush=True)
        with (args.output / 'controller.txt').open('a') as log:
            (args.output / 'controller.txt').chmod(0o600)
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                    cwd=runner.parents[3], timeout=700)
        if result.returncode:
            raise RuntimeError(f'Trial failed; preserve capture and inspect controller.txt: {folder}')
        summary = analyze(json.loads(next(folder.rglob('raw.json')).read_text()))
        (folder / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
        print(json.dumps({'completed': index, 'case': folder.name,
                          **{key: summary[key] for key in ('mean_seconds', 'p95_seconds',
                              'makespan_seconds', 'shared_peak', 'empty_claims', 'event_defer_attempts')}}), flush=True)
    print(json.dumps({'matrix_complete': len(cases)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--redis-url', required=True)
    parser.add_argument('--baseline-root', type=Path, required=True)
    parser.add_argument('--baseline-commit', required=True)
    parser.add_argument('--current-root', type=Path, required=True)
    parser.add_argument('--current-commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args())
