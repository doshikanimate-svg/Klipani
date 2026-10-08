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


def test_model_endpoints_are_public_first_run(client, monkeypatch) -> None:
    """Setup endpoints stay open without a key: the user downloads the model first."""
    from app.services import transcription_service as ts

    assert client.get("/api/model").status_code == 200
    monkeypatch.setattr(ts, "model_cached", lambda name=None: True)
    body = client.post("/api/model/download").json()
    assert body["ready"] is True
    state = client.get("/api/model").json()
    assert state["ready"] is True
    assert state["size_mb"] > 0


def test_llm_endpoints_are_public_and_need_daemon(client, monkeypatch) -> None:
    """Qwen setup is also key-free; pull refuses cleanly without the daemon."""
    from app.providers.llm import ollama as oll

    assert client.get("/api/llm").status_code == 200
    monkeypatch.setattr(oll, "daemon_running", lambda: False)
    refused = client.post("/api/llm/pull")
    assert refused.status_code == 409
    assert "ollama.com" in refused.json()["detail"]
    monkeypatch.setattr(oll, "daemon_running", lambda: True)
    monkeypatch.setattr(oll, "ollama_available", lambda: True)
    assert client.get("/api/llm").json()["ready"] is True


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


def test_service_spec_prefers_prebuilt_badge(tmp_path) -> None:
    """With Pillow the mark is one pre-rendered pill: logo and handle baked in."""
    pytest.importorskip("PIL")
    from PIL import Image

    from app.services.service_watermark import service_logo_png, service_spec

    spec = service_spec(tmp_path)
    assert spec is not None
    assert spec.get("prebuilt") is True
    assert spec["draw"] == ""  # no drawtext: the text is inside the PNG
    assert spec["overlay"] == "W-w-56:H-h-92"  # bottom-right corner
    with Image.open(spec["logo"]) as badge:
        assert badge.width > badge.height  # landscape pill
        assert badge.mode == "RGBA"
    assert service_logo_png() is not None


def test_badge_cache_is_reused(tmp_path) -> None:
    pytest.importorskip("PIL")
    from app.services.badge_service import badge_path

    logo = None
    first = badge_path(tmp_path, logo, "@Klipani_bot")
    assert first is not None
    stamp = first.stat().st_mtime_ns
    again = badge_path(tmp_path, logo, "@Klipani_bot")
    assert again == first and again.stat().st_mtime_ns == stamp
    other = badge_path(tmp_path, logo, "@Klipani_support")
    assert other != first  # different handle -> different badge


def test_badge_layout_has_logo_beside_text(tmp_path) -> None:
    """Logo on the left, handle on the right, on the same line."""
    pytest.importorskip("PIL")
    from PIL import Image

    from app.services.badge_service import render_badge
    from app.services.service_watermark import service_logo_png

    out = render_badge(service_logo_png(), "@Klipani_bot", tmp_path / "svc.png")
    assert out is not None
    with Image.open(out) as badge:
        width, height = badge.size
        alpha = badge.getchannel("A")
        box = alpha.getbbox()
        assert box is not None
        left = badge.crop((0, 0, width // 2, height)).convert("RGB")
        right = badge.crop((width // 2, 0, width, height)).convert("RGB")
        # Both halves carry ink: icon left, handle right.
        assert left.getbbox() is not None and right.getbbox() is not None


def test_service_spec_falls_back_without_pillow(tmp_path, monkeypatch) -> None:
    """No Pillow -> logo + drawtext chain, still a watermark."""
    import builtins

    from app.services import badge_service
    from app.services.service_watermark import service_spec

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("PIL"):
            raise ImportError("no Pillow")
        return real_import(name, *args, **kwargs)

    (tmp_path / "clips").mkdir()
    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.setattr(badge_service, "render_badge", lambda *a, **k: None)
    spec = service_spec(tmp_path)
    assert spec is not None and spec["draw"].startswith("drawtext=")
    assert spec.get("prebuilt") is not True


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


def test_trial_key_expires_three_days_after_issue(monkeypatch) -> None:
    """Free code stops working 3 days after receipt: verify() rejects it."""
    import base64
    import hashlib
    import hmac
    import json

    import app.services.license_service as lic

    monkeypatch.setenv("LICENSE_SECRET", "test-secret-exp")
    lic.get_settings.cache_clear()
    try:
        expired_payload = json.dumps(
            {"tg": 99, "plan": "trial", "exp": 1_000_000}, separators=(",", ":")
        ).encode()
        sig = hmac.new(b"test-secret-exp", expired_payload, hashlib.sha256).digest()
        blob = (
            base64.urlsafe_b64encode(expired_payload).rstrip(b"=").decode()
            + "."
            + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        )
        assert lic.verify_license(f"KLIP-{blob}") is None

        fresh = lic.issue_license(99, "trial")
        assert lic.verify_license(fresh["key"]) is not None
        assert fresh["exp"] - __import__("time").time() <= 3 * 86400 + 5
    finally:
        lic.get_settings.cache_clear()


def test_trial_claim_survives_paid_plan_override(monkeypatch, tmp_path) -> None:
    """trial -> month -> trial again must stay refused (the reported hole)."""
    import sqlite3
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bot"))
    import main as bot

    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "trial-test.db")
    assert bot.claim_trial(777) is True  # first claim
    assert bot.trial_used(777) is True
    assert bot.claim_trial(777) is False  # second claim refused

    # User buys the paid plan: subscribers row is overwritten...
    bot.save_subscriber(777, "month", "KLIP-x", 9_999_999_999)
    # ...but the trial claim must persist.
    assert bot.trial_used(777) is True
    assert bot.claim_trial(777) is False

    # Legacy DBs (trial row, no claims table yet) are backfilled on open.
    legacy = tmp_path / "legacy.db"
    connection = sqlite3.connect(legacy)
    connection.execute(
        "CREATE TABLE subscribers (tg_id INTEGER PRIMARY KEY, plan TEXT NOT NULL,"
        " license_key TEXT NOT NULL, exp INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO subscribers VALUES (555, 'trial', 'KLIP-old', 1_000_000)"
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(bot, "DB_PATH", legacy)
    assert bot.trial_used(555) is True
    bot.save_subscriber(555, "month", "KLIP-y", 9_999_999_999)
    assert bot.trial_used(555) is True


def test_format_duration_is_human() -> None:
    assert "сек" in format_duration(42)
    assert "мин" in format_duration(240)
    assert "ч" in format_duration(7200)
    assert format_duration(None) == ""



class _NoSecrets:
    """Simulates the desktop app: no LICENSE_SECRET, no private key on disk."""
    license_secret = ""
    license_ed25519_private = ""


def test_ed25519_roundtrip_and_tamper() -> None:
    """Current format: signed by bot, verified anywhere (real .env has both keys)."""
    import app.services.license_service as lic

    issued = lic.issue_license(4242, "month")
    assert issued["key"].startswith("KLIP2-")
    assert lic.key_format(issued["key"]) == "ed25519"
    info = lic.verify_license(issued["key"])
    assert info is not None and info["plan"] == "month" and info["telegram_id"] == 4242
    assert lic.verify_license(issued["key"][:-2] + "xx") is None
    assert lic.verify_license("garbage") is None


def test_ed25519_verifies_without_any_secret(monkeypatch) -> None:
    """Desktop has zero secrets — KLIP2 must verify, legacy KLIP- must not."""
    import app.services.license_service as lic

    modern = lic.issue_license(11, "trial")["key"]
    monkeypatch.setattr(lic, "_ed_private", lambda: None)
    legacy = lic.issue_license(11, "trial")["key"]
    assert legacy.startswith("KLIP-") and not legacy.startswith("KLIP2-")

    monkeypatch.setattr(lic, "get_settings", lambda: _NoSecrets())
    assert lic.verify_license(modern) is not None
    assert lic.verify_license(modern)["plan"] == "trial"
    assert lic.verify_license(legacy) is None


def test_convert_legacy_key_keeps_expiry(monkeypatch, tmp_path) -> None:
    import sys
    from pathlib import Path

    import app.services.license_service as lic

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bot"))
    import main as bot

    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "conv.db")
    real_signer = lic._ed_private
    monkeypatch.setattr(lic, "_ed_private", lambda: None)
    legacy = lic.issue_license(31337, "month")
    assert legacy["key"].startswith("KLIP-")
    bot.save_subscriber(31337, "month", legacy["key"], legacy["exp"])

    monkeypatch.setattr(lic, "_ed_private", real_signer)  # restore real signer
    converted = bot.convert_legacy_key(31337)
    assert converted is not None and converted.startswith("KLIP2-")
    info = lic.verify_license(converted)
    assert info is not None and info["exp"] == legacy["exp"]
    assert bot.convert_legacy_key(31337) is None  # already converted
