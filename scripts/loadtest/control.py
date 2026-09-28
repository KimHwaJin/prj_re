#!/usr/bin/env python3
"""Switch the isolated load-test API's Executor target without starting load."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'var/loadtest/executor-mode'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executor', choices=['mock', 'real'], help='mock=real Executor OFF; real=ON')
    parser.add_argument('--build', action='store_true', help='Rebuild API image after source changes')
    args = parser.parse_args()
    mode = args.executor or (STATE.read_text().strip() if STATE.exists() else 'mock')
    if mode not in ('mock', 'real'):
        parser.error('Invalid saved mode; pass --executor mock or real')
    command = ['docker', 'compose', '-f', 'compose.loadtest.yaml']
    if mode == 'real':
        command += ['-f', 'compose.loadtest.real.yaml']
    command += ['--profile', 'load-generator']

    def run(*parts, **kwargs):
        return subprocess.run(command + list(parts), cwd=ROOT, check=True, **kwargs)

    # Refuse changes in the middle of a run; do not silently mix targets in metrics.
    port = os.getenv('LOADTEST_LOCUST_PORT', '18089')
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/stats/requests', timeout=3) as response:
            stats = json.load(response)
        if stats.get('state') not in ('ready', 'stopped') or stats.get('user_count', 0):
            parser.error('Stop the running Locust test before changing Executor mode.')
    except urllib.error.URLError:
        # No UI yet on first boot is normal; a running container must be reachable.
        existing = run('ps', '--status', 'running', '-q', 'locust', capture_output=True, text=True)
        if existing.stdout.strip():
            parser.error('Locust is running but its status is unavailable; stop it before switching.')
    run('config', '--quiet')
    if args.build:
        run('build', 'api')
    run('stop', 'locust')
    run('up', '-d', '--wait', 'postgres', 'redis', 'mock-executor', 'api')
    run('up', '-d', '--no-deps', '--force-recreate', 'locust')
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(mode + '\n')
    print(f'Executor: {mode.upper()} (real API {"ON" if mode == "real" else "OFF"})')
    print(f'Locust: http://localhost:{port} — select crud / submit / approval, users=100, spawn rate=10')
    print('No load started. Change scenarios only after stopping the previous run.')


if __name__ == '__main__':
    main()
