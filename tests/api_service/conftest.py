"""API tests start from an isolated snapshot, never from local credentials.

Tests that need a specific environment replace this snapshot in their own fixtures.
"""
import pytest
import dtest.settings.loader as service_settings
@pytest.fixture(autouse=True)
def isolated_service_snapshot(monkeypatch):
    from dtest.container import install_container
    install_container()
    monkeypatch.setattr(service_settings, "_snapshot", service_settings.load_settings(config={}, environ={}))
