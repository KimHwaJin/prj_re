"""Graph tests never depend on developer credentials or a local config.yml."""
import pytest
import service_settings


@pytest.fixture(autouse=True)
def isolated_service_snapshot(monkeypatch):
    monkeypatch.setattr(service_settings, "_snapshot", service_settings.load_settings(config={}, environ={}))
