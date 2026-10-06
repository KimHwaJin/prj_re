"""Graph tests never depend on developer credentials or a local config.yml."""
import pytest
import dtest.settings.loader as service_settings
@pytest.fixture(autouse=True)
def isolated_service_snapshot(monkeypatch):
    from dtest.container import install_container
    install_container()
    monkeypatch.setattr(service_settings, "_snapshot", service_settings.load_settings(config={}, environ={}))
