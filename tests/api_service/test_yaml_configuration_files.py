"""Real files/source precedence/export/schema launcher; never contact external services."""
import json
from pathlib import Path
import runpy
import shutil
import subprocess
from contextlib import asynccontextmanager
import sys

import pytest
import yaml

import dtest.settings.loader as service_settings
from dtest.settings.files import initialize_profile, yaml_document, write_private
from dtest.settings.loader import ConfigurationError, load_settings

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_snapshot(monkeypatch):
    monkeypatch.setattr(service_settings, "_snapshot", None)


@pytest.fixture
def profile_root(tmp_path):
    shutil.copy(ROOT/'config.example.yml',tmp_path/'config.yml')
    for env in ('dev','stg','prd'):
        shutil.copy(ROOT/f'config.{env}.example.yml',tmp_path/f'config.{env}.example.yml')
    return tmp_path


@pytest.mark.parametrize('profile',['dev','stg','prd'])
def test_profile_copy_is_private_and_same_db_targets_derive(profile_root,profile):
    target=initialize_profile(profile,root=profile_root)
    assert target.stat().st_mode & 0o777 == 0o600
    assert not (profile_root/'.env').exists()
    snapshot=load_settings(root=profile_root,profile=profile,environ={})
    assert snapshot.worker.database_url==snapshot.database.database_url.replace('+asyncpg','')
    assert snapshot.agent.checkpoint_db_uri==snapshot.agent.checkpoint_db_uri
    assert snapshot.redis.redis_url==snapshot.worker.redis_url
    assert snapshot.agent.executor_base_url==snapshot.worker.executor_base_url
    assert snapshot.agent.executor_source_type=='INLINE'
    assert snapshot.agent.max_plan_candidates==5
    assert snapshot.agent.model_structured_output_mode=='prompt_json'
    assert snapshot.sources['DATABASE_URL']==f'config.{profile}.yml'
    assert snapshot.sources['AGENT_WORKER_CONCURRENCY']==f'config.{profile}.yml'


def test_missing_selected_profile_does_not_silently_use_legacy_defaults(profile_root):
    with pytest.raises(ConfigurationError,match='initialize it from config.dev.example.yml'):
        load_settings(root=profile_root,profile="dev",environ={})


def test_copy_never_overwrites_private_file_implicitly(profile_root):
    target=initialize_profile('dev',root=profile_root)
    original=target.read_bytes()
    with pytest.raises(FileExistsError): initialize_profile('dev',root=profile_root)
    assert target.read_bytes()==original


def test_legacy_dotenv_import_canonicalizes_and_preserves_false_zero_sources(profile_root):
    source=profile_root/'legacy.env'
    source.write_text('DATABASE_URL=postgresql+asyncpg://u:import-secret@local:15432/chat_app\n'
        'CHECKPOINT_DB_URI=postgresql://u:import-secret@local:15432/agent\n'
        'EW_REDIS_URL=redis://:import-secret@local:6379/2\n'
        'LLM_MODEL_NAME=legacy-model\nMODEL_API_KEY=import-secret\n'
        'EXECUTOR_SUBMIT_ENABLED=false\nMODEL_MAX_RETRIES=0\nSERVER_PORT=18110\n'
        'PYTHONUTF8=1\n')
    before=source.read_bytes()
    target=initialize_profile('dev',root=profile_root,source_env=source)
    snapshot=load_settings(root=profile_root,profile='dev',environ={'SERVER_PORT':'9999'})
    assert source.read_bytes()==before
    assert snapshot.api.server_port==18110
    assert snapshot.agent.model_name=='legacy-model'
    assert snapshot.redis.redis_url=='redis://:import-secret@local:6379/2'
    assert not snapshot.agent.executor_submit_enabled and snapshot.agent.model_max_retries==0
    text=target.read_text()
    assert 'ew_redis_url' not in text and 'llm_model_name' not in text and 'pythonutf8' not in text
    assert 'import-secret' not in json.dumps(snapshot.summary())
    assert 'import-secret' not in repr(snapshot)


@pytest.mark.parametrize('line',[
    'EW_DISPATCH_CONCURRENCY=4',
    'MODEL_NAME=a\nLLM_MODEL_NAME=b',
    'DATABASE_URL=invalid-private-value',
])
def test_invalid_import_does_not_create_or_replace_target(profile_root,line):
    target=profile_root/'config.dev.yml'
    target.write_text('unchanged')
    source=profile_root/'legacy.env';source.write_text(line+'\n')
    with pytest.raises(ConfigurationError):
        initialize_profile('dev',root=profile_root,source_env=source,overwrite=True)
    assert target.read_text()=='unchanged'


def test_exported_resolved_yaml_reloads_exact_runtime_without_env(profile_root):
    initialize_profile('dev',root=profile_root)
    snapshot=load_settings(root=profile_root,environ={})
    target=profile_root/'resolved.yml'
    write_private(target,yaml.safe_dump(yaml_document(snapshot.inputs)))
    restored=load_settings(config_path=target,environ={})
    assert restored.api==snapshot.api and restored.agent==snapshot.agent
    assert restored.sso==snapshot.sso and restored.workflow_search==snapshot.workflow_search
    assert restored.worker.event_group==snapshot.worker.event_group
    assert restored.worker.database_url==snapshot.worker.database_url


def test_schema_check_and_app_check_resolve_the_same_yaml(profile_root):
    initialize_profile('dev',root=profile_root)
    source=load_settings(root=profile_root,environ={})
    target=profile_root/'resolved.yml'
    write_private(target,yaml.safe_dump(yaml_document(source.inputs)))
    results=[]
    for script in ('app.py','scripts/migrate.py'):
        result=subprocess.run([sys.executable,str(ROOT/script),'--config',str(target),'--check-config'],
            cwd=ROOT,env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(ROOT/'src')},capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stderr
        results.append(json.loads(result.stdout))
    assert results[0]==results[1]


def test_cicd_yaml_moves_mutable_values_and_preserves_platform_contract():
    documents=list(yaml.safe_load_all((ROOT/'cicd/basic/dev/deployment.yml').read_text()))
    pod=next(d for d in documents if d['kind']=='Deployment')['spec']['template']['spec']
    container=pod['containers'][0]
    values={item['name']:item['value'] for item in container['env']}
    for key in ('DATABASE_URL','REDIS_URL','MODEL_API_KEY','CHECKPOINT_DB_URI','EW_DATABASE_URL'):
        assert values[key]=='[설정 값 변경 불가]'
    assert 'EW_DISPATCH_CONCURRENCY' not in values
    assert 'SERVER_PORT' not in values and 'EXECUTOR_BASE_URL' not in values
    assert container['image']=='[설정 값 변경 불가]'
    assert container['resources']['limits']['cpu']=='[설정 값 변경 불가]'
    assert len(pod['containers'])==1 and not pod.get('initContainers')
    config=yaml.safe_load((ROOT/'cicd/basic/dev/config.dev.example.yml').read_text())
    settings=load_settings(config=config,environ={
        'DATABASE_URL':'postgresql+asyncpg://host/chat_app','CHECKPOINT_DB_URI':'postgresql://host/agent',
        'REDIS_URL':'redis://host:6379/0','MODEL_API_KEY':'secret','EW_DATABASE_URL':'postgresql://host/chat_app'})
    assert settings.api.server_port==5000 and settings.agent.model_name=='gpt-oss-120b'
    assert settings.agent.executor_source_type=='PATH'


def test_schema_launcher_uses_selected_targets_and_prepares_in_order(profile_root, monkeypatch):
    from alembic import command
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    initialize_profile('dev', root=profile_root)
    snapshot = load_settings(root=profile_root, environ={})
    target = profile_root / 'resolved.yml'
    write_private(target, yaml.safe_dump(yaml_document(snapshot.inputs)))
    steps = []
    def upgrade(config, revision):
        assert service_settings.get_settings().database.database_url == snapshot.database.database_url
        assert service_settings.get_settings().worker.database_url == snapshot.worker.database_url
        assert revision == 'head'
        steps.append(Path(config.config_file_name).name)
    @asynccontextmanager
    async def saver(uri):
        assert uri == snapshot.agent.checkpoint_db_uri
        async def setup(): steps.append('checkpoint')
        yield type('Saver', (), {'setup': staticmethod(setup)})()
    monkeypatch.setattr(command, 'upgrade', upgrade)
    monkeypatch.setattr(AsyncPostgresSaver, 'from_conn_string', saver)
    module = runpy.run_path(str(ROOT / 'scripts/migrate.py'))
    module['main'](['--env', 'dev', '--config', str(target)])
    assert steps == ['alembic.crud.ini', 'alembic.ini', 'checkpoint']


def test_secret_mount_and_container_tools_have_the_same_profile_contract():
    deployment = list(yaml.safe_load_all((ROOT / 'deploy/dtest-agent.yaml').read_text()))
    pod = next(d for d in deployment if d['kind'] == 'Deployment')['spec']['template']['spec']
    container = pod['containers'][0]
    config_map = next(d for d in deployment if d['kind'] == 'ConfigMap')
    assert config_map['metadata']['name'] == container['envFrom'][0]['configMapRef']['name']
    assert config_map['data'] == {'APP_ENV': 'prd'}
    mount = next(m for m in container['volumeMounts'] if m['name'] == 'application-config')
    assert mount['mountPath'] == '/app/config.prd.yml' and mount['subPath'] == 'config.prd.yml'
    assert mount['readOnly']
    volume = next(v for v in pod['volumes'] if v['name'] == 'application-config')
    secret = yaml.safe_load((ROOT / 'deploy/secret.example.yaml').read_text())
    assert volume['secret']['secretName'] == secret['metadata']['name']
    profile = yaml.safe_load(secret['stringData']['config.prd.yml'])
    config = profile
    resolved = load_settings(config=config, environ={}, profile='prd')
    assert resolved.api.server_host == '0.0.0.0' and resolved.api.server_port == 8000
    assert resolved.worker.database_url == resolved.database.database_url.replace('+asyncpg', '')
    assert 'scripts/migrate.py scripts/configure.py' in (ROOT / 'Dockerfile').read_text()
