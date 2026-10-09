"""Trial claims survive Render's ephemeral disk via a secret gist (sqlite fallback)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bot"))

import trial_store


class FakeGist:
    """Dict-backed GitHub Gists double: GET/PATCH/POST over one JSON file."""

    def __init__(self):
        self.files: dict = {}
        self.fail = False
        self.id = "fake-gist-id"

    def __call__(self, method, path, token, payload=None, timeout=10.0):
        import json as _json

        if self.fail:
            return False, None
        if method == "GET":
            content = _json.dumps(self.files)
            return True, {"files": {trial_store.GIST_FILENAME: {"content": content}}}
        if method == "PATCH":
            self.files = _json.loads(payload["files"][trial_store.GIST_FILENAME]["content"])
            return True, {"id": self.id}
        if method == "POST":
            return True, {"id": self.id}
        raise AssertionError(f"unexpected call {method} {path}")


def _setup(monkeypatch, tmp_path, with_token=True):
    import main as bot_main

    if with_token:
        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    else:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GIST_ID", raising=False)
    trial_store._cache.update({"at": 0.0, "claims": None})
    monkeypatch.setattr(bot_main, "DB_PATH", tmp_path / "bot.db")
    return bot_main


def test_gist_config_needs_token(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert trial_store.gist_config() is None


def test_claim_once_then_refused(monkeypatch, tmp_path) -> None:
    bot_db = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(trial_store, "_api", FakeGist())
    monkeypatch.setenv("GIST_ID", "fake-gist-id")
    assert bot_db.claim_trial(101) is True
    assert bot_db.claim_trial(101) is False
    assert bot_db.trial_used(101) is True
    assert bot_db.trial_used(102) is False


def test_claim_survives_sqlite_wipe(monkeypatch, tmp_path) -> None:
    """THE BUG: Render wipes bot.db on every deploy. The claim must persist."""
    bot_db = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(trial_store, "_api", FakeGist())
    monkeypatch.setenv("GIST_ID", "fake-gist-id")
    assert bot_db.claim_trial(202) is True

    # Simulate a redeploy: brand-new empty database file.
    monkeypatch.setattr(bot_db, "DB_PATH", tmp_path / "fresh-bot.db")
    assert bot_db.trial_used(202) is True
    assert bot_db.claim_trial(202) is False


def test_sqlite_claim_self_heals_into_gist(monkeypatch, tmp_path) -> None:
    """A claim recorded before the gist existed migrates on first sight."""
    bot_db = _setup(monkeypatch, tmp_path, with_token=False)
    assert bot_db.claim_trial(303) is True  # sqlite only, no token configured

    fake = FakeGist()
    monkeypatch.setattr(trial_store, "_api", fake)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GIST_ID", "fake-gist-id")
    assert bot_db.trial_used(303) is True
    assert fake.files.get("303") is not None
    monkeypatch.setattr(bot_db, "DB_PATH", tmp_path / "wiped.db")
    assert bot_db.claim_trial(303) is False


def test_api_outage_falls_back_to_sqlite(monkeypatch, tmp_path) -> None:
    bot_db = _setup(monkeypatch, tmp_path)
    fake = FakeGist()
    fake.fail = True
    monkeypatch.setattr(trial_store, "_api", fake)
    monkeypatch.setenv("GIST_ID", "fake-gist-id")
    assert bot_db.claim_trial(404) is True
    assert bot_db.trial_used(404) is True
    assert bot_db.claim_trial(404) is False
