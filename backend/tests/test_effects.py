import pytest
from pydantic import ValidationError

from app.services.effect_service import EffectPlan, Shake, SoundSpec, audio_filter, synth_source, vertical_video_filter
from app.utils.ffmpeg import sfx_mix_chain


def test_shake_validation_clamps() -> None:
    with pytest.raises(ValidationError):
        Shake(at=-1)
    with pytest.raises(ValidationError):
        Shake(at=1, amplitude=500)
    assert Shake(at=2).duration == 0.5


def test_anchor_validation() -> None:
    with pytest.raises(ValidationError):
        EffectPlan(anchor="left")
    assert "y='0'" in vertical_video_filter(EffectPlan(anchor="top"), 10.0)


def test_shake_filter_contains_window() -> None:
    chain = vertical_video_filter(EffectPlan(shake=Shake(at=5.0, duration=0.5)), 20.0)
    assert "between(t,5.0,5.5)" in chain
    assert "fade=t=in" in chain and "fade=t=out" in chain


def test_no_shake_plain_crop() -> None:
    chain = vertical_video_filter(EffectPlan(), 10.0)
    assert "between" not in chain
    assert "crop=1080:1920" in chain


def test_audio_fade_short_clip() -> None:
    assert audio_filter(0.0) == "anull"
    assert "afade=t=out:st=19.60" in audio_filter(20.0)


def test_sound_validation() -> None:
    with pytest.raises(ValidationError):
        SoundSpec(kind="explosion", at=1.0)
    with pytest.raises(ValidationError):
        SoundSpec(kind="riser", at=-1.0)
    assert "aevalsrc" in synth_source(SoundSpec(kind="riser", at=2.0))
    assert "exp(-6*t)" in synth_source(SoundSpec(kind="impact", at=2.0))


def test_sfx_mix_chain_delays_and_fades() -> None:
    sounds = [SoundSpec(kind="riser", at=4.2), SoundSpec(kind="impact", at=5.0, duration=0.4)]
    chain = sfx_mix_chain("[0:a]", sounds, 1, 20.0)
    assert "[1:a]adelay=4200|4200[sfx0]" in chain
    assert "[2:a]adelay=5000|5000[sfx1]" in chain
    assert "[0:a][sfx0][sfx1]amix=inputs=3:normalize=0[mix]" in chain
    assert chain.endswith("[aout]")


def _fake_logo(tmp_path, platform: str = "twitch", width: int = 64, height: int = 32) -> None:
    """Local PNG so watermark layout tests never depend on the network."""
    import struct
    import zlib

    (tmp_path / "watermarks").mkdir(exist_ok=True)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    (tmp_path / "watermarks" / f"{platform}.png").write_bytes(png)


def test_watermark_positions_avoid_edges(tmp_path) -> None:
    from app.services.effect_service import WATERMARK_BOTTOM, WATERMARK_LEFT, watermark_spec

    (tmp_path / "clips").mkdir()
    _fake_logo(tmp_path, "twitch")
    _fake_logo(tmp_path, "youtube")
    spec = watermark_spec(True, "twitch", "nick", "top-left", tmp_path, "c1")
    assert spec is not None
    assert "y=170" in spec["draw"] or ":170" in spec["overlay"]
    assert "Roboto-Bold" in spec["draw"]
    # logo 64x32 at height 48 -> width 96; text starts right of it
    assert spec["overlay"] == f"{WATERMARK_LEFT}:170+2"
    assert spec["draw"].endswith(f"x={WATERMARK_LEFT + 96 + 14}:y=170+0")
    spec_br = watermark_spec(True, "youtube", "nick", "bottom-right", tmp_path, "c2")
    assert "W-180" in spec_br["draw"] and f"H-{WATERMARK_BOTTOM}" in spec_br["draw"]
    assert watermark_spec(False, "twitch", "nick", "top-left", tmp_path, "c3") is None
    assert watermark_spec(True, "twitch", "   ", "top-left", tmp_path, "c4") is None


def test_resolve_logo_prefers_bundled_over_download(tmp_path) -> None:
    """No storage file -> the logo shipped with the app wins, no network."""
    from app.services.effect_service import resolve_logo

    (tmp_path / "watermarks").mkdir(exist_ok=True)
    found = resolve_logo("twitch", tmp_path)
    assert found is not None and found.endswith("assets/twitch.png")


def test_watermark_without_logo_is_text_only(tmp_path, monkeypatch) -> None:
    """No logo available: the nickname still renders, edge-anchored."""
    from app.services import effect_service
    from app.services.effect_service import WATERMARK_BOTTOM, WATERMARK_LEFT, watermark_spec

    (tmp_path / "clips").mkdir()
    (tmp_path / "watermarks").mkdir()
    monkeypatch.setattr(effect_service, "resolve_logo", lambda platform, storage: None)

    spec = watermark_spec(True, "twitch", "nick", "top-left", tmp_path, "nl1")
    assert spec is not None
    assert spec["logo"] is None
    assert spec["overlay"] == ""
    assert spec["draw"].endswith(f"x={WATERMARK_LEFT}:y=170")

    bottom = watermark_spec(True, "twitch", "nick", "bottom-right", tmp_path, "nl2")
    assert bottom is not None and bottom["logo"] is None
    assert f"H-text_h-{WATERMARK_BOTTOM}" in bottom["draw"]


def test_watermark_logo_sits_beside_nickname(tmp_path) -> None:
    """Icon and nickname share one line: logo right of the text on left anchors,
    text left of the logo on right anchors."""
    import struct
    import zlib

    from app.services.effect_service import (
        WATERMARK_LOGO_GAP,
        watermark_spec,
    )

    def png(width: int, height: int) -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(
            b"IDAT", zlib.compress(raw)
        ) + chunk(b"IEND", b"")

    (tmp_path / "clips").mkdir()
    (tmp_path / "watermarks").mkdir()
    # 16:9 logo at height 48 -> width 84
    (tmp_path / "watermarks" / "twitch.png").write_bytes(png(320, 180))

    left = watermark_spec(True, "twitch", "nick", "top-left", tmp_path, "s1")
    assert left["overlay"] == "40:170+2"          # logo anchored, text after it
    # text starts after logo width + gap: 40 + 84 + 14 = 138
    assert left["draw"].endswith(f"x=138:y=170+0")
    assert WATERMARK_LOGO_GAP == 14

    right = watermark_spec(True, "twitch", "nick", "top-right", tmp_path, "s2")
    assert right["overlay"] == "W-180-84:170+2"    # logo pinned to the right margin
    assert right["draw"].endswith("x=W-180-84-14-text_w:y=170+0")

    bottom = watermark_spec(True, "twitch", "nick", "bottom-left", tmp_path, "s3")
    assert "H-330-52" in bottom["overlay"]          # whole group above the safe zone
    assert "y=H-330-52+0" in bottom["draw"]


def test_logo_display_width_from_png_header(tmp_path) -> None:
    from app.services.effect_service import WATERMARK_LOGO_HEIGHT, logo_display_width

    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG custom")  # not a real PNG -> square fallback
    assert logo_display_width(broken) == WATERMARK_LOGO_HEIGHT


def test_watermark_custom_logo_wins(tmp_path) -> None:
    from app.services.effect_service import resolve_logo, watermark_spec

    (tmp_path / "clips").mkdir()
    (tmp_path / "watermarks").mkdir()
    custom = tmp_path / "watermarks" / "twitch.png"
    custom.write_bytes(b"\x89PNG custom")
    assert resolve_logo("twitch", tmp_path) == str(custom)
    spec = watermark_spec(True, "twitch", "nick", "top-right", tmp_path, "c5")
    # unreadable PNG -> square fallback width 48
    assert spec is not None and spec["overlay"] == "W-180-48:170+2"


def test_category_affinity() -> None:
    from app.db import database, initialize
    from app.services.stats_service import category_affinity

    initialize()
    assert category_affinity() == {}
    with database() as db:
        db.execute("INSERT OR IGNORE INTO highlights VALUES ('hha','vvv',1,5,80,'FUNNY','r','e')")
        db.execute("INSERT OR IGNORE INTO highlights VALUES ('hhb','vvv',10,15,70,'REACTION','r','e')")
        db.execute("INSERT OR IGNORE INTO clips VALUES ('cca','vvv','hha',1,5,'/tmp/a.mp4','/tmp/a.jpg','DONE','2026-01-01','CUT')")
        db.execute("INSERT OR IGNORE INTO clips VALUES ('ccb','vvv','hhb',10,15,'/tmp/b.mp4','/tmp/b.jpg','DONE','2026-01-01','CUT')")
        db.execute("INSERT OR REPLACE INTO clip_stats VALUES ('cca',1000,50,'2026-01-02')")
        db.execute("INSERT OR REPLACE INTO clip_stats VALUES ('ccb',100,5,'2026-01-02')")
    affinity = category_affinity()
    assert affinity.get("FUNNY") == 10
    assert affinity.get("REACTION") == 1
    with database() as db:
        db.execute("DELETE FROM clip_stats WHERE clip_id IN ('cca','ccb')")
        db.execute("DELETE FROM clips WHERE id IN ('cca','ccb')")
        db.execute("DELETE FROM highlights WHERE id IN ('hha','hhb')")
