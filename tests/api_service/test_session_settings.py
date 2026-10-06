"""Typed request/config boundaries: no infrastructure or model calls."""

import pytest
from pydantic import ValidationError

from api_service.schemas.session_schema import SessionCreate
from service_contracts.session_settings import resolve_session_settings
from service_settings import ConfigurationError, load_settings


@pytest.mark.parametrize('settings', [{}, {'kernel_profile': None}])
def test_default_is_resolved_and_not_left_implicit(settings):
    assert resolve_session_settings(settings, default_profile='default',
        allowed_profiles=('default', '3102311')) == {'kernel_profile': 'default'}


@pytest.mark.parametrize('settings', [
    {'kernel_profile': ''}, {'kernel_profile': ' '}, {'kernel_profile': ' default'},
    {'kernel_profile': 'a/b'}, {'kernel_profile': 'a' * 129}, {'kernel_profile': 3102311},
    {'main_model_name': 'other'}, {'repair_level': 4}, {'mode': 'MULTI'}, {'anything': True},
])
def test_unknown_session_options_and_invalid_kernel_syntax_are_rejected(settings):
    with pytest.raises(ValidationError):
        SessionCreate.model_validate({'settings': settings})


def test_supported_profile_can_be_selected_but_not_arbitrary_valid_name():
    assert resolve_session_settings({'kernel_profile': '3102311'},
        default_profile='default', allowed_profiles=('default', '3102311')) == {'kernel_profile': '3102311'}
    with pytest.raises(ValueError, match='not allowed'):
        resolve_session_settings({'kernel_profile': 'unknown'},
            default_profile='default', allowed_profiles=('default',))


@pytest.mark.parametrize('value', [[], {}, 'default', ['default', 'default'],
    ['other'], ['default', 3102311], ['default', 'invalid/path']])
def test_invalid_runtime_profile_configuration_fails_at_startup(value):
    with pytest.raises(ConfigurationError):
        load_settings(config={'EXECUTOR_RUNTIME_PROFILE': 'default',
            'EXECUTOR_RUNTIME_PROFILES': value}, environ={})


@pytest.mark.parametrize('value', ['', 'bad/path', ' default ', 'a'*129])
def test_invalid_default_profile_fails_at_startup(value):
    with pytest.raises(ConfigurationError):
        load_settings(config={'EXECUTOR_RUNTIME_PROFILE': value}, environ={})


def test_config_over_env_and_default_only_allowlist():
    current = load_settings(config={'EXECUTOR_RUNTIME_PROFILE': 'default', 'EXECUTOR_RUNTIME_PROFILES': ['default', '3102311']},
        environ={'EXECUTOR_RUNTIME_PROFILE': 'ignored', 'EXECUTOR_RUNTIME_PROFILES': '["ignored"]'})
    assert current.agent.executor_runtime_profiles == ('default', '3102311')
    assert current.sources['EXECUTOR_RUNTIME_PROFILES'] == 'config mapping'
    env = load_settings(config={}, environ={'EXECUTOR_RUNTIME_PROFILE': 'env-kernel',
        'EXECUTOR_RUNTIME_PROFILES': '["env-kernel","other"]'})
    assert env.agent.executor_runtime_profiles == ('env-kernel', 'other')
    fallback = load_settings(config={'EXECUTOR_RUNTIME_PROFILE': 'chosen'}, environ={})
    assert fallback.agent.executor_runtime_profiles == ('chosen',)
