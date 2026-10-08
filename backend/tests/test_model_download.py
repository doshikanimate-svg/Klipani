"""Whisper download retries: stalled HuggingFace connections resume, not restart."""

import pytest


def test_download_retries_then_succeeds(monkeypatch) -> None:
    from app.services import transcription_service as ts

    calls = {"n": 0}

    def flaky(repo_id):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("stalled")
        return "/tmp/fake"

    monkeypatch.setattr("huggingface_hub.snapshot_download", flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(ts, "model_cached", lambda name=None: calls["n"] >= 3)
    result = ts.download_model(max_attempts=5)
    assert result["ready"] is True
    assert calls["n"] == 3


def test_download_stores_error_after_last_attempt(monkeypatch) -> None:
    from app.db import initialize
    from app.services import transcription_service as ts

    initialize()
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda **k: (_ for _ in ()).throw(ConnectionError("down")),
    )
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(ts, "model_cached", lambda name=None: False)
    with pytest.raises(RuntimeError):
        ts.download_model(max_attempts=2)
    state = ts.model_download_state()
    assert state["ready"] is False
    assert "error" in state
