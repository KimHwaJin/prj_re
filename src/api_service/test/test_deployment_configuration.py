"""Effective configuration and deployment contracts, without external IO."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import yaml

import service_settings
from service_settings import ConfigurationError, load_settings

ROOT = Path(__file__).resolve().parents[3]

@pytest.fixture(autouse=True)
def snapshot(monkeypatch):
    monkeypatch.setattr(service_settings, '_snapshot', None)

@pytest.mark.parametrize('canonical,legacy,value', [
    ('REDIS_URL', 'EW_REDIS_URL', 'redis://localhost:6379/2'),
    ('EXECUTOR_BASE_URL', 'EW_EXECUTOR_BASE_URL', 'http://executor:8000'),
])
def test_one_endpoint_accepts_legacy_spelling_and_config_overrides_env(canonical, legacy, value):
    settings = load_settings(config={legacy: value}, environ={canonical: 'ignored'})
    if canonical == 'REDIS_URL':
        assert settings.api.redis_url == settings.worker.redis_url == value
    else:
        assert settings.agent.executor_base_url == settings.worker.executor_base_url == value
    with pytest.raises(ConfigurationError, match=canonical):
        load_settings(config={}, environ={canonical: value, legacy: 'different'})
    assert load_settings(config={}, environ={canonical: value, legacy: value})

@pytest.mark.parametrize('profile,port', [('dev', 8000), ('dev', 5000), ('stg', 5000), ('prd', 5000)])
def test_shipped_profiles_do_not_mask_deployment_ports_or_executor_switch(profile, port):
    env = {'APP_ENV': profile, 'SERVER_PORT': str(port),
           'DATABASE_URL': 'postgresql+asyncpg://localhost/chat_app',
           'CHECKPOINT_DB_URI': 'postgresql://localhost/agent',
           'EXECUTOR_SUBMIT_ENABLED': 'true', 'EVENT_WORKER_ENABLED': 'true'}
    settings = load_settings(environ=env, root=ROOT)
    assert settings.api.server_port == port
    assert settings.api.agent_worker_enabled and settings.api.task_reconciler_enabled
    assert settings.event_worker_enabled and settings.agent.executor_submit_enabled
    assert settings.worker.health_port == 0
    # An explicit YAML decision still wins over deployment env.
    explicit = load_settings(config={'SERVER_PORT': 8123, 'EVENT_WORKER_ENABLED': False}, environ=env)
    assert explicit.api.server_port == 8123 and not explicit.event_worker_enabled

def test_event_worker_default_tracks_agent_but_accepts_explicit_override():
    assert load_settings(config={}, environ={}).event_worker_enabled
    assert not load_settings(config={'AGENT_WORKER_ENABLED': False}, environ={}).event_worker_enabled
    assert load_settings(config={'AGENT_WORKER_ENABLED': False, 'EVENT_WORKER_ENABLED': True}, environ={}).event_worker_enabled

def test_config_summary_never_exposes_shared_credentials():
    settings = load_settings(config={}, environ={'REDIS_URL': 'redis://:do-not-print@host:6379',
        'DATABASE_URL': 'postgresql+asyncpg://u:do-not-print@host/chat_app'})
    assert 'do-not-print' not in str(settings.summary())
    assert settings.summary()['server_processes'] == 1
    assert settings.summary()['connection_pool_limits']['crud'] == 20

@pytest.mark.parametrize('path', ['deploy/dtest-agent.yaml', 'cicd/basic/dev/deployment.yml'])
def test_deployment_starts_one_container_with_canonical_lifecycle(path):
    docs = list(yaml.safe_load_all((ROOT/path).read_text()))
    deployments = [d for d in docs if d['kind'] == 'Deployment']
    assert len(deployments) == 1
    pod = deployments[0]['spec']['template']['spec']
    assert not pod.get('initContainers')
    assert len(pod['containers']) == 1
    container = pod['containers'][0]
    assert container['command'] == ['python', 'app.py']
    assert container['readinessProbe']['httpGet']['path'] == '/service/ready'
    assert container['livenessProbe']['httpGet']['path'] == '/service/live'
    assert pod['terminationGracePeriodSeconds'] >= 20 + 25 + 10
    env = {v['name']: v.get('value') for v in container.get('env', [])}
    assert 'EW_INSTANCE_ID' not in env
    for d in docs:
        if d['kind'] == 'Ingress':
            assert d['apiVersion'] == 'networking.k8s.io/v1'

def test_integrated_readiness_waits_for_consumer_then_tracks_its_health(monkeypatch):
    import service_bootstrap
    from api_service.worker.telemetry import Telemetry
    worker = type('Worker', (), {'ready': AsyncMock(return_value=True), 'telemetry': Telemetry()})()
    holder = {}
    async def loop():
        await asyncio.Event().wait()
    def factories(settings, stop, *, on_event_worker):
        holder['publish'] = on_event_worker
        return {'executor-event-worker': loop}
    monkeypatch.setattr(service_bootstrap, '_background_factories', factories)
    database = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(return_value=True))
    @asynccontextmanager
    async def short_session():
        yield database
    monkeypatch.setattr('api_service.core.database.short_session', short_session)
    settings = load_settings(config={'AGENT_WORKER_ENABLED': False, 'TASK_RECONCILER_ENABLED': False,
        'EVENT_WORKER_ENABLED': True, 'SHUTDOWN_DRAIN_SECONDS': 0}, environ={})
    app = service_bootstrap.create_app(settings)
    with TestClient(app) as client:
        assert client.get('/service/ready').status_code == 503
        holder['publish'](worker)
        assert client.get('/service/ready').status_code == 200
        metrics = client.get('/service/metrics')
        assert metrics.status_code == 200
        assert 'ew_operations' in metrics.text and 'python_info' in metrics.text
        database.scalar.return_value = False
        assert client.get('/service/ready').status_code == 503
        database.scalar.return_value = True
        worker.ready.return_value = False
        assert client.get('/service/ready').status_code == 503
        assert client.get('/service/live').status_code == 200
        holder['publish'](None)
        assert client.get('/service/ready').status_code == 503

def test_local_generation_has_one_canonical_env_and_keeps_source_unchanged(tmp_path, monkeypatch):
    import json
    import runpy
    import subprocess
    module = runpy.run_path(str(ROOT / 'scripts/local.py'))
    initialize = module['initialize']
    monkeypatch.setitem(initialize.__globals__, 'ROOT', tmp_path)
    monkeypatch.setitem(initialize.__globals__, 'ENV_FILE', tmp_path / '.env.local')
    source_file = tmp_path / '.env'
    source_file.write_text('source must stay unchanged\n')
    (tmp_path / '.env.local.example').write_text('LOCAL_API_PORT=18000\n')
    source = {'DATABASE_URL':'postgresql+asyncpg://u:pass@external:5432/chat_app',
        'CHECKPOINT_DB_URI':'postgresql://u:pass@external:5432/agent',
        'AGENT_CHECKPOINT_DATABASE_URL':'postgresql://u:pass@external:5432/agent',
        'EW_DATABASE_URL':'postgresql://u:pass@external:5432/agent',
        'REDIS_URL':'redis://external:6379/0','EW_REDIS_URL':'redis://external:6379/0',
        'EXECUTOR_BASE_URL':'http://executor','EW_EXECUTOR_BASE_URL':'http://executor'}
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: subprocess.CompletedProcess(a,0,
        json.dumps({'services':{'inspect':{'environment':source}}}),''))
    initialize()
    env = service_settings.read_local_env(tmp_path / '.env.local')
    assert source_file.read_text() == 'source must stay unchanged\n'
    assert env['LOCAL_DATABASE_URL'] == 'postgresql+asyncpg://u:pass@postgres:5432/chat_app'
    assert env['LOCAL_EW_DATABASE_URL'] == 'postgresql://u:pass@postgres:5432/agent'
    assert env['LOCAL_CHECKPOINT_DB_URI'] == 'postgresql://u:pass@postgres:5432/agent'
    assert env['REDIS_URL'] == source['REDIS_URL']
    assert not {'AGENT_CHECKPOINT_DATABASE_URL','EW_REDIS_URL','EW_EXECUTOR_BASE_URL'} & env.keys()
    assert (tmp_path / '.env.local').stat().st_mode & 0o777 == 0o600
    assert (ROOT/'scripts/local/init-databases.sql').is_file()
    assert 'CREATE DATABASE agent' in (ROOT/'scripts/local/init-databases.sql').read_text()

def test_local_upgrade_drains_only_legacy_worker_in_same_project(monkeypatch):
    import runpy
    import subprocess
    module = runpy.run_path(str(ROOT / 'scripts/local.py'))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, 'legacy-id\n' if args[1] == 'ps' else '', '')
    monkeypatch.delenv('COMPOSE_PROJECT_NAME', raising=False)
    monkeypatch.setattr(subprocess, 'run', run)
    module['retire_legacy_event_worker']()
    assert 'label=com.docker.compose.project=dtest-agent-local' in calls[0]
    assert 'label=com.docker.compose.service=event-worker' in calls[0]
    assert calls[1] == ['docker','stop','--time','70','legacy-id']
    assert calls[2] == ['docker','rm','legacy-id']


def test_explicit_event_database_normalizes_driver_from_common_api_url():
    url = 'postgresql+asyncpg://user:pass@localhost/chat_app'
    settings = load_settings(config={'DATABASE_URL': url, 'EW_DATABASE_URL': url}, environ={})
    assert settings.api.database_url == url
    assert settings.worker.database_url == 'postgresql://user:pass@localhost/chat_app'
    assert settings.workflow_database_url == settings.worker.database_url


def test_configured_worker_identity_is_prefix_not_shared_consumer_identity():
    values = {'EW_INSTANCE_ID':'legacy-fixed-name'}
    first = load_settings(config={}, environ=values)
    second = load_settings(config={}, environ=values)
    assert first.worker.instance_id.startswith('legacy-fixed-name:')
    assert second.worker.instance_id.startswith('legacy-fixed-name:')
    assert first.worker.instance_id != second.worker.instance_id


@pytest.mark.parametrize('url,local', [('redis://redis:6379/0', True), ('redis://external:6379/0', False)])
def test_local_start_prepares_selected_infrastructure(tmp_path, monkeypatch, url, local):
    import runpy
    import sys
    module = runpy.run_path(str(ROOT / 'scripts/local.py'))
    main = module['main']
    env = tmp_path / '.env.local'
    env.write_text('REDIS_URL=' + url + '\n')
    calls = []
    for name, value in {'ENV_FILE':env, 'initialize':lambda: None,
                        'compose':lambda *args: calls.append(args),
                        'retire_legacy_event_worker':lambda: None, 'smoke':lambda: None}.items():
        monkeypatch.setitem(main.__globals__, name, value)
    monkeypatch.setattr(sys, 'argv', ['local.py','up'])
    main()
    if local:
        assert ('--profile','local-redis','up','-d','--wait','postgres','redis') in calls
    else:
        assert ('up','-d','--wait','postgres') in calls
        assert not any('redis' in call for call in calls)


def test_active_command_worker_requires_same_database_without_exposing_credentials():
    with pytest.raises(ConfigurationError, match='same database') as error:
        load_settings(config={'DATABASE_URL':'postgresql+asyncpg://u:secret@host/chat_app',
            'EW_DATABASE_URL':'postgresql://u:secret@host/agent'}, environ={})
    assert 'secret' not in str(error.value)
    assert load_settings(config={'DATABASE_URL':'postgresql+asyncpg://u:secret@host/chat_app',
        'EW_DATABASE_URL':'postgresql://u:secret@host:5432/chat_app'}, environ={})
    # Inspection/migration can still resolve the old separate targets with execution disabled.
    assert load_settings(config={'DATABASE_URL':'postgresql+asyncpg://u:secret@host/chat_app',
        'EW_DATABASE_URL':'postgresql://u:secret@host/agent',
        'AGENT_WORKER_ENABLED':False,'EVENT_WORKER_ENABLED':False}, environ={})


@pytest.mark.parametrize('name', sorted(service_settings.REMOVED_SETTINGS))
@pytest.mark.parametrize('source', ['config','env'])
def test_removed_dispatch_settings_fail_with_migration_instructions(name,source):
    with pytest.raises(ConfigurationError,match='Removed service setting: '+name):
        load_settings(config={name:'obsolete'} if source=='config' else {},
            environ={name:'obsolete'} if source=='env' else {})


@pytest.mark.parametrize('name', sorted(service_settings.REMOVED_INFRASTRUCTURE_SETTINGS))
@pytest.mark.parametrize('source', ['config', 'env'])
def test_removed_infrastructure_settings_fail_without_exposing_values(name, source):
    with pytest.raises(ConfigurationError, match='Removed infrastructure API setting: '+name) as error:
        load_settings(config={name: 'private-value'} if source == 'config' else {},
            environ={name: 'private-value'} if source == 'env' else {})
    assert 'private-value' not in str(error.value)


def test_ingress_has_one_limit_and_old_common_spelling_is_only_an_alias():
    settings=load_settings(config={'EW_CONCURRENCY':3},environ={})
    assert settings.worker.ingress_concurrency==3
    assert 'concurrency' not in type(settings.worker).model_fields
    assert 'dispatch_concurrency' not in type(settings.worker).model_fields
    with pytest.raises(ConfigurationError,match='Conflicting aliases'):
        load_settings(config={'EW_CONCURRENCY':3,'EW_INGRESS_CONCURRENCY':4},environ={})


@pytest.mark.parametrize('config,expected', [
    ({'EXECUTOR_BASE_URL':'http://executor:8080'}, '/api/v1/executions/test/events'),
    ({'EXECUTOR_BASE_URL':'http://executor:8080/api/v1', 'EXECUTOR_EXECUTION_PATH':'/executions/{execution_id}'}, '/api/v1/executions/test/events'),
    ({'EXECUTOR_BASE_URL':'http://executor:8080/gateway', 'EXECUTOR_EXECUTION_PATH':'/v2/jobs/{execution_id}'}, '/gateway/v2/jobs/test/events'),
    ({'EXECUTOR_BASE_URL':'http://executor:8080/gateway', 'EXECUTOR_EVENTS_PATH':'/history/{execution_id}'}, '/gateway/history/test'),
    ({'EXECUTOR_BASE_URL':'http://executor:8080', 'EW_EXECUTOR_EVENTS_PATH':'/custom/{execution_id}/events'}, '/custom/test/events'),
])
@pytest.mark.asyncio
async def test_event_history_uses_same_base_and_execution_resource(config, expected):
    from api_service.worker.runtime import ExecutorWorker
    settings = load_settings(config=config, environ={})
    worker = ExecutorWorker(settings.worker, {'execution.completed'})
    try:
        request = worker.http.build_request('GET', worker.router.events_path.format(execution_id='test'))
        assert request.url.path == expected
        if 'EXECUTOR_EVENTS_PATH' not in config and 'EW_EXECUTOR_EVENTS_PATH' not in config:
            assert str(request.url) == settings.agent.executor_execution_url.format(execution_id='test') + '/events'
    finally:
        await worker.http.aclose()
        await worker.redis.aclose()
        await worker.pool.close()


@pytest.mark.parametrize('path', ['/events', 'https://other/{execution_id}', '//other/{execution_id}',
                                  '/{wrong}/events', '/{execution_id}/events?x=1', '/{execution_id!r}/events'])
def test_invalid_event_history_template_fails_at_configuration(path):
    with pytest.raises(ConfigurationError):
        load_settings(config={'EXECUTOR_EVENTS_PATH':path}, environ={})
