import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import initialize
from app.main import app
from app.services.job_service import JobConflictError, active_job, create_job
from app.services.video_service import create_video


@pytest.fixture()
def sample_video_id():
    initialize()
    from app.services.video_service import list_videos

    existing = list_videos()
    if existing:
        yield existing[0]["id"]
        return
    storage = get_settings().storage
    path = storage / "uploads" / "test-sample.mp4"
    if not path.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=15:duration=5",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
            check=True,
        )
    created_id = create_video("test-sample.mp4", path, path.stat().st_size)["id"]
    yield created_id
    from app.services.video_service import delete_video

    delete_video(created_id)


def _sample_highlight(client: TestClient, video_id: str) -> str:
    from app.services.highlight_service import create_mock_highlights
    from app.services.video_service import get_video

    highlights = client.get(f"/api/videos/{video_id}/highlights").json()
    if highlights:
        return highlights[0]["id"]
    created = create_mock_highlights(get_video(video_id))
    assert created
    return created[0]["id"]


def test_analyze_conflict_when_job_active(sample_video_id: str) -> None:
    video_id = sample_video_id
    with TestClient(app) as client:
        first = create_job(video_id, "ANALYZE")
        try:
            assert active_job(video_id, "ANALYZE") is not None
            try:
                create_job(video_id, "ANALYZE")
                raise AssertionError("expected JobConflictError")
            except JobConflictError:
                pass
            response = client.post(f"/api/videos/{video_id}/analyze")
            assert response.status_code == 409
        finally:
            from app.services.job_service import cancel_job

            cancel_job(first["id"])


def test_render_style_validation(sample_video_id: str) -> None:
    video_id = sample_video_id
    with TestClient(app) as client:
        highlight_id = _sample_highlight(client, video_id)
        bad = client.post(f"/api/highlights/{highlight_id}/generate?style=nope")
        assert bad.status_code == 422
        ok = client.post(f"/api/highlights/{highlight_id}/generate?style=blur&subtitles=false")
        assert ok.status_code == 200
        assert ok.json()["style"] == "blur"
        from app.services.job_service import cancel_job

        cancel_job(ok.json()["id"])


def test_app_settings_roundtrip() -> None:
    initialize()
    with TestClient(app) as client:
        client.put("/api/settings", json={
            "watermark_enabled": "0",
            "watermark_platform": "twitch",
            "watermark_text": "",
            "watermark_position": "top-right",
        })
        current = client.get("/api/settings").json()
        assert current["watermark_enabled"] == "0"
        assert current["watermark_platform"] == "twitch"
        updated = client.put("/api/settings", json={
            "watermark_enabled": "1",
            "watermark_platform": "youtube",
            "watermark_text": "tester",
            "watermark_position": "bottom-left",
            "nope": "ignored",
        }).json()
        assert updated["watermark_platform"] == "youtube"
        assert updated["watermark_text"] == "tester"
        bad = client.put("/api/settings", json={"watermark_platform": "kick"})
        assert bad.status_code == 400
        client.put("/api/settings", json={"watermark_enabled": "0", "watermark_text": ""})


def test_delete_clip_removes_files_and_rows(tmp_path) -> None:
    initialize()
    from app.db import database
    from app.services.clip_service import delete_clip

    fake_mp4 = tmp_path / "x.mp4"
    fake_mp4.write_bytes(b"0")
    with database() as db:
        db.execute(
            "INSERT INTO clips VALUES ('clipx','vid1','hl1',1,2,?,?,?,?,?)",
            (str(fake_mp4), str(tmp_path / "x.jpg"), "DONE", "2026-01-01", "CUT"),
        )
        db.execute("INSERT INTO clip_stats VALUES ('clipx',10,2,'2026-01-01')")
    result = delete_clip("clipx")
    assert result == {"deleted": True, "removed_files": 1}
    assert delete_clip("clipx")["deleted"] is False
    with database() as db:
        assert db.execute("SELECT * FROM clips WHERE id='clipx'").fetchone() is None
        assert db.execute("SELECT * FROM clip_stats WHERE clip_id='clipx'").fetchone() is None
