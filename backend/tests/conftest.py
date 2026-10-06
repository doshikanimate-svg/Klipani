"""Shared test fixtures.

License enforcement is OFF for the whole suite: most tests exercise job/pipeline
behaviour and would otherwise all hit the 402 gate. Tests that check the gate
itself (backend/tests/test_license_gate.py) re-enable it in their own fixture.
"""

import pytest


@pytest.fixture(autouse=True)
def license_enforced_off(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "license_enforced", False, raising=False)