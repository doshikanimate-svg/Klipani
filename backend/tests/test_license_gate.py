"""License gate, free-tier service watermark and job ETA."""

import pytest
from fastapi.testclient import TestClient

from app.db import initialize
from app.services.app_settings import PRIVATE_KEYS, get_all, get_private, set_private
from app.services.estimate_service import estimate_remaining, format_duration
from app.services.license_service import FREE_PLANS, current_state, issue_license, verify_license


@pytest.fixture
def client(monkeypatch):
    """API with the license gate ON and an isolated settings store."""
    from app.config import get_settings

    # Env vars + cache_clear so a rebuilt Settings also has the gate ON;
    # the suite-wide fixture in conftest.py turns it off for every other test.
    monkeypatch.setenv("LICENSE_ENFORCED", "true")
    monkeypatch.setenv("LICENSE_SECRET", "test-secret")
    get_settings.cache_clear()
    assert get_settings().license_enforced is True
    set_private("license_key", "")
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    set_private("license_key", "")
    get_settings.cache_clear()


def test_write_endpoints_require_license(client) -> None:
    assert client.get("/api/health").status_code == 200  # read-only stays open
    assert client.get("/api/videos").status_code == 200
    blocked = client.post("/api/videos/upload")
    assert blocked.status_code == 402
    assert blocked.json()["code"] == "license_required"
    assert "Klipani_bot" in blocked.json()["detail"]


def test_trial_and_paid_plans_unlock_and_report_free_flag(client) -> None:
    trial = issue_license(42, "trial")["key"]
    assert client.post("/api/license", json={"key": trial}).status_code == 200
    state = client.get("/api/license").json()
    assert state["active"] is True
    assert state["free"] is True  # trial keeps the service watermark
    assert state["days_left"] == 3

    paid = issue_license(42, "month")["key"]
    client.post("/api/license", json={"key": paid})
    assert client.get("/api/license").json()["free"] is False


def test_free_flag_definition() -> None:
    assert "trial" in FREE_PLANS
    assert "month" not in FREE_PLANS


def test_license_key_is_not_exposed_or_overwritable_through_settings(client) -> None:
    assert "license_key" in PRIVATE_KEYS
    assert "license_key" not in get_all()
    assert "license_key" not in client.get("/api/settings").json()
    key = issue_license(7, "month")["key"]
    client.post("/api/license", json={"key": key})
    # A stray settings write must not be able to drop the key.
    client.put("/api/settings", json={"watermark_text": "x", "license_key": ""})
    assert verify_license(get_private("license_key"))


def test_unknown_key_rejected() -> None:
    assert verify_license("KLIP-bm90LWEtdXNpZ25hdHVyZQ.bm90cmVhbA") is None
    assert verify_license("garbage") is None
    assert current_state()["active"] is False


def test_service_spec_has_logo_and_handle(tmp_path) -> None:
    from app.services.service_watermark import service_spec

    (tmp_path / "clips").mkdir()
    spec = service_spec(tmp_path)
    assert spec is not None and spec["logo"] is not None
    assert "@Klipani_bot" in (tmp_path / "clips" / "service.svc.txt").read_text(encoding="utf-8")
    assert spec["draw"].startswith("drawtext=")
    assert "40" not in spec["overlay"].split(":")[0].split("-")[0]  # right margin, not left


def test_service_spec_can_be_disabled(tmp_path, monkeypatch) -> None:
    from app.config import get_settings
    from app.services.service_watermark import service_spec

    (tmp_path / "clips").mkdir()
    monkeypatch.setattr(get_settings(), "service_watermark", False, raising=False)
    assert service_spec(tmp_path) is None


def test_free_tier_gets_service_mark_paid_does_not(monkeypatch, tmp_path) -> None:
    from app.services import clip_service
    from app.services import service_watermark

    (tmp_path / "clips").mkdir()
    monkeypatch.setattr(service_watermark, "needs_service_watermark", lambda: True)
    assert clip_service._service_mark() is not None
    monkeypatch.setattr(service_watermark, "needs_service_watermark", lambda: False)
    assert clip_service._service_mark() is None


def test_needs_service_watermark_follows_plan(monkeypatch) -> None:
    from app.services import license_service, service_watermark

    monkeypatch.setattr(license_service, "current_state", lambda: {"active": True, "free": True})
    assert service_watermark.needs_service_watermark() is True
    monkeypatch.setattr(license_service, "current_state", lambda: {"active": True, "free": False})
    assert service_watermark.needs_service_watermark() is False
    monkeypatch.setattr(license_service, "current_state", lambda: {"active": False, "free": True})
    assert service_watermark.needs_service_watermark() is True


def test_ffmpeg_graph_carries_both_marks() -> None:
    """Free tier with a user watermark must chain two marks, not one."""
    from app.utils.ffmpeg import _apply_watermark

    service = {"logo": "/tmp/svc.png", "overlay": "W-w-48:H-h-104", "draw": "drawtext=svc"}
    user = {"logo": "/tmp/tw.png", "overlay": "40:170+2", "draw": "drawtext=user"}
    graph = _apply_watermark("[0:v]scale=1080:1920", user, service)
    assert "[base][svcg]overlay" in graph
    assert "[svc][wmg]overlay" in graph
    assert graph.endswith("[wm]null[vout]")
    # Each stage reads the previous label exactly once and never re-consumes it.
    assert "[base][base]" not in graph and "[svc][svc]" not in graph
    assert _apply_watermark("[0:v]scale=1080:1920", None, None).endswith("[base]null[vout]")


def test_job_eta_shrinks_with_progress() -> None:
    video = {"duration": 2700.0}
    early = {"type": "ANALYZE", "status": "RUNNING", "progress": 10}
    late = {"type": "ANALYZE", "status": "RUNNING", "progress": 70}
    eta_early = estimate_remaining(early, video)
    eta_late = estimate_remaining(late, video)
    assert eta_early and eta_late and eta_late < eta_early
    assert estimate_remaining({"type": "RENDER", "status": "DONE", "progress": 100}, video) is None
    assert estimate_remaining({"type": "RENDER", "status": "QUEUED", "progress": 0}, video) >= 3


def test_job_payload_includes_eta() -> None:
    """The /api/jobs response carries eta_seconds, and None once finished."""
    from app.db import database
    from app.main import _job_payload
    from app.services.job_service import get_job

    initialize()
    with database() as db:
        db.execute(
            """INSERT OR REPLACE INTO jobs VALUES (:id,:video_id,:type,:status,:progress,:current_step,
               :error,:cancelled,:created_at,:updated_at,:subtitles,:style)""",
            {
                "id": "eta-job", "video_id": "no-such-video", "type": "RENDER", "status": "RUNNING",
                "progress": 35, "current_step": "RENDERING", "error": None, "cancelled": 0,
                "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
                "subtitles": 1, "style": "crop",
            },
        )
    running = get_job("eta-job")
    assert running is not None
    assert isinstance(_job_payload(running)["eta_seconds"], int)

    with database() as db:
        db.execute("UPDATE jobs SET status='DONE', progress=100 WHERE id='eta-job'")
    done = get_job("eta-job")
    assert done is not None
    assert _job_payload(done)["eta_seconds"] is None


def test_format_duration_is_human() -> None:
    assert "сек" in format_duration(42)
    assert "мин" in format_duration(240)
    assert "ч" in format_duration(7200)
    assert format_duration(None) == ""