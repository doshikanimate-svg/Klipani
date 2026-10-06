"""Shared test fixtures.

License enforcement is OFF for the whole suite: most tests exercise job/pipeline
behaviour and would otherwise all hit the 402 gate. Tests that check the gate
itself (backend/tests/test_license_gate.py) re-enable it in their own fixture.
"""

import pytest


@pytest.fixture(autouse=True)
def license_enforced_off(monkeypatch):
    """Disable the paid-endpoint gate for the suite.

    Both the env var and the live object are patched: several tests call
    ``get_settings.cache_clear()``, which builds a brand new Settings instance
    that must also come up with the gate off.
    """
    from app.config import get_settings

    monkeypatch.setenv("LICENSE_ENFORCED", "false")
    monkeypatch.setattr(get_settings(), "license_enforced", False, raising=False)