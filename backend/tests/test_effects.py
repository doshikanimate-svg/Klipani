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


def test_watermark_positions_avoid_edges(tmp_path) -> None:
    from app.services.effect_service import watermark_spec

    (tmp_path / "clips").mkdir()
    (tmp_path / "watermarks").mkdir()
    spec = watermark_spec(True, "twitch", "nick", "top-left", tmp_path, "c1")
    assert spec is not None
    assert "y=170" in spec["draw"] or ":170" in spec["overlay"]
    assert "Roboto-Bold" in spec["draw"]
    spec_br = watermark_spec(True, "youtube", "nick", "bottom-right", tmp_path, "c2")
    assert "w-text_w-180" in spec_br["draw"] and "H-text_h-394" in spec_br["draw"]
    assert watermark_spec(False, "twitch", "nick", "top-left", tmp_path, "c3") is None
    assert watermark_spec(True, "twitch", "   ", "top-left", tmp_path, "c4") is None


def test_watermark_custom_logo_wins(tmp_path) -> None:
    from app.services.effect_service import resolve_logo, watermark_spec

    (tmp_path / "clips").mkdir()
    (tmp_path / "watermarks").mkdir()
    custom = tmp_path / "watermarks" / "twitch.png"
    custom.write_bytes(b"\x89PNG custom")
    assert resolve_logo("twitch", tmp_path) == str(custom)
    spec = watermark_spec(True, "twitch", "nick", "top-right", tmp_path, "c5")
    assert spec is not None and spec["overlay"] == "W-w-180:170"


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
