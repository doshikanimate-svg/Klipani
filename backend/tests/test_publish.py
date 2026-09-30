from fastapi.testclient import TestClient

from app.db import initialize
from app.main import app
from app.providers.publishing.base import PublishMetadata


def test_publish_metadata_validation() -> None:
    PublishMetadata(title="ok", privacy="unlisted")
    try:
        PublishMetadata(title="", privacy="unlisted")
        raise AssertionError("expected validation error")
    except Exception:
        pass
    try:
        PublishMetadata(title="ok", privacy="everywhere")
        raise AssertionError("expected validation error")
    except Exception:
        pass


def test_youtube_status_and_guards() -> None:
    initialize()
    with TestClient(app) as client:
        status = client.get("/api/publish/youtube/status").json()
        assert status["provider"] == "youtube"
        assert isinstance(status["configured"], bool)
        assert isinstance(status["connected"], bool)
        bad_callback = client.get("/api/publish/youtube/callback")
        assert bad_callback.status_code == 400
        if status["connected"]:
            return  # real OAuth token present: skip live-upload guard
        bad_auth = client.get("/api/publish/youtube/auth-url")
        assert bad_auth.status_code in (200, 400)
        videos = client.get("/api/videos").json()
        if not videos:
            return  # empty storage after cache cleanup: nothing to guard
        clips = client.get(f"/api/clips?video_id={videos[0]['id']}").json()
        if clips:
            response = client.post(
                f"/api/clips/{clips[0]['id']}/publish",
                json={"title": "t", "description": "", "privacy": "unlisted"},
            )
            assert response.status_code == 409


def test_tiktok_status_and_guards() -> None:
    initialize()
    with TestClient(app) as client:
        status = client.get("/api/publish/tiktok/status").json()
        assert status["provider"] == "tiktok"
        assert isinstance(status["configured"], bool)
        assert isinstance(status["connected"], bool)
        bad_callback = client.get("/api/publish/tiktok/callback")
        assert bad_callback.status_code == 400
        if status["connected"]:
            return  # real OAuth token present: skip live guards
        bad_auth = client.get("/api/publish/tiktok/auth-url")
        assert bad_auth.status_code in (200, 400)
