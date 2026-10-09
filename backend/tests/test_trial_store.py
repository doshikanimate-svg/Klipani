"""Trial claims survive Render's ephemeral disk via Redis (with sqlite fallback)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bot"))

import trial_store


class FakeRedis:
    """Dict-backed Upstash REST double: implements GET and SET .. NX EX."""

    def __init__(self):
        self.data: dict = {}

    def __call__(self, *parts: str, timeout: float = 5.0):
        command = parts[0]
        if command == "GET":
            return True, self.data.get(parts[1])
        if command == "SET":
            key = parts[1]
            if "NX" in parts and key in self.data:
                return True, None
            self.data[key] = parts[2]
            return True, "OK"
        raise AssertionError(f"unexpected command {parts}")


@pytest.fixture
def redis_env(monkeypatch):
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://fake.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "fake-token")


@pytest.fixture
def bot_db(monkeypatch, tmp_path):
    import main as bot_main

    monkeypatch.setattr(bot_main, "DB_PATH", tmp_path / "bot.db")
    return bot_main


def test_redis_config_needs_both_vars(monkeypatch) -> None:
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    assert trial_store.redis_config() is None


def test_claim_once_then_refused(redis_env, bot_db, monkeypatch) -> None:
    monkeypatch.setattr(trial_store, "_redis", FakeRedis())
    assert bot_db.claim_trial(101) is True
    assert bot_db.claim_trial(101) is False
    assert bot_db.trial_used(101) is True
    assert bot_db.trial_used(102) is False


def test_claim_survives_sqlite_wipe(redis_env, bot_db, monkeypatch, tmp_path) -> None:
    """THE BUG: Render wipes bot.db on every deploy. The claim must persist."""
    monkeypatch.setattr(trial_store, "_redis", FakeRedis())
    assert bot_db.claim_trial(202) is True

    # Simulate a redeploy: brand-new empty database file.
    monkeypatch.setattr(bot_db, "DB_PATH", tmp_path / "fresh-bot.db")
    assert bot_db.trial_used(202) is True
    assert bot_db.claim_trial(202) is False


def test_sqlite_claim_self_heals_into_redis(redis_env, bot_db, monkeypatch) -> None:
    """A claim recorded before Redis existed migrates on first sight."""
    fake = FakeRedis()
    # Redis "doesn't exist yet": claim goes to sqlite only.
    monkeypatch.setattr(trial_store, "_redis", lambda *a, **k: (False, None))
    assert bot_db.claim_trial(303) is True

    monkeypatch.setattr(trial_store, "_redis", fake)
    assert bot_db.trial_used(303) is True
    assert fake.data.get("klipani:trial:303") == "1"
    # ...and stays refused even after another wipe.
    monkeypatch.setattr(bot_db, "DB_PATH", bot_db.DB_PATH.parent / "wiped.db")
    assert bot_db.claim_trial(303) is False


def test_redis_outage_falls_back_to_sqlite(redis_env, bot_db, monkeypatch) -> None:
    monkeypatch.setattr(trial_store, "_redis", lambda *a, **k: (False, None))
    assert bot_db.claim_trial(404) is True
    assert bot_db.trial_used(404) is True
    assert bot_db.claim_trial(404) is False
