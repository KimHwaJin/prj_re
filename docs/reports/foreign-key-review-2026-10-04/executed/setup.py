import json,os,subprocess,sys,hashlib
from pathlib import Path
W=Path('/Users/a10054/.codex/worktrees/refactor-bootstrap/dtest-agent');T=Path('/private/tmp/dtest-fk-review')
url='postgresql+asyncpg://postgres:fk-review-test-only@127.0.0.1:63374/postgres'
cfg=T/'private-config.yml';cfg.write_text('service:\n  DATABASE_URL: '+url+'\n  EW_DATABASE_URL: postgresql://postgres:fk-review-test-only@127.0.0.1:63374/postgres\n  WORKFLOW_DATABASE_URL: postgresql://postgres:fk-review-test-only@127.0.0.1:63374/postgres\n');cfg.chmod(0o600)
env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG','LC_ALL')};env.update(PYTHONPATH=str(W/'src'),PYTHONDONTWRITEBYTECODE='1',SERVICE_CONFIG_FILE=str(cfg))
with (T/'migration.log').open('w') as log:
 for ini in ('alembic.crud.ini','alembic.ini'):subprocess.run([sys.executable,'-m','alembic','-c',ini,'upgrade','head'],cwd=W,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
subprocess.run([sys.executable,str(T/'inventory.py')],cwd=W,env=env,check=True)
