#!/usr/bin/env python3
"""Sequential CRUD soak and approval-wait ramp; no Executor submission."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
for scenario, output, users, warmup, seconds in [
    ('crud_mixed','var/loadtest/crud-mixed-soak-20260928','100','30','900'),
    ('approval','var/loadtest/approval-ramp-20260928','1,5,10,25,50,75,100','15','60'),
]:
    print('START',scenario,flush=True)
    subprocess.run([sys.executable,'scripts/loadtest/crud_ramp.py','--scenario',scenario,
                    '--output',output,'--users',users,'--warmup',warmup,'--seconds',seconds],cwd=ROOT,check=True)
    print('FINISHED',scenario,flush=True)
