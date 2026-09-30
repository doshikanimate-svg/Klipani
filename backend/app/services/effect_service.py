"""EffectEngine: safe, validated FFmpeg effects for vertical clips.

Only allowlisted filters with clamped numeric params are ever emitted.
LLM output never reaches FFmpeg; all values come from validated models.
"""

from typing import Optional

import logging

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

ALLOWED_ANCHORS = ("center", "top")


class Shake(BaseModel):
    at: float = Field(..., ge=0)
    duration: float = Field(default=0.5, ge=0.2, le=1.5)
    amplitude: float = Field(default=18.0, ge=0.0, le=60.0)

    @model_validator(mode="after")
    def check_all(self) -> "Shake":
        return self


class Fade(BaseModel):
    duration: float = Field(default=0.4, ge=0.0, le=2.0)


class SoundSpec(BaseModel):
    kind: str = Field(..., pattern="^(riser|impact)$")
    at: float = Field(..., ge=0)
    duration: float = Field(default=0.8, ge=0.2, le=2.0)
    volume: float = Field(default=0.25, ge=0.0, le=1.0)


class EffectPlan(BaseModel):
    anchor: str = Field(default="center", pattern="^(center|top)$")
    fade: Fade = Field(default_factory=Fade)
    shake: Optional[Shake] = None
    sounds: list[SoundSpec] = Field(default_factory=list)


def synth_source(sound: SoundSpec) -> str:
    """Deterministic lavfi source for a synthesized SFX (no user files needed)."""
    duration = round(sound.duration, 3)
    if sound.kind == "riser":
        # Linear sweep 300 -> 1500 Hz: phase = 2*pi*(300*t + 750*t^2).
        return (
            f"aevalsrc=sin(2*PI*(300*t+750*t*t)):s=44100:d={duration},"
            f"volume={round(sound.volume, 3)},"
            f"afade=t=out:st={round(duration * 0.6, 3)}:d={round(duration * 0.4, 3)}"
        )
    return (
        "sine=frequency=55:duration={0},"
        "volume='{1}*exp(-6*t)':eval=frame".format(duration, round(sound.volume * 2, 3))
    )


def vertical_video_filter(plan: EffectPlan, duration: float) -> str:
    """Full video chain: scale -> (animated) crop -> fade. Evaluated per frame."""
    if plan.anchor == "top":
        base_x, base_y = "(in_w-1080)/2", "0"
    else:
        base_x, base_y = "(in_w-1080)/2", "(in_h-1920)/2"
    if plan.shake is not None:
        start = round(plan.shake.at, 3)
        end = round(plan.shake.at + plan.shake.duration, 3)
        amp = round(plan.shake.amplitude, 1)
        period = round(plan.shake.duration, 3)
        x = f"if(between(t,{start},{end}),{base_x}+{amp}*sin(2*PI*(t-{start})/{period}),{base_x})"
        y = f"if(between(t,{start},{end}),{base_y}+{amp}*cos(2*PI*(t-{start})/{period}),{base_y})"
        crop = (
            "scale=1120:1990:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='{x}':y='{y}'"
        )
    else:
        crop = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:x='{base_x}':y='{base_y}'"
        )
    chain = f"{crop},setsar=1"
    fade_duration = min(plan.fade.duration, max(0.0, duration / 2))
    if fade_duration > 0:
        chain += f",fade=t=in:st=0:d={fade_duration:.2f}"
        chain += f",fade=t=out:st={max(0.0, duration - fade_duration):.2f}:d={fade_duration:.2f}"
    return chain


def fade_suffix(duration: float, fade_duration: float = 0.4) -> str:
    fade_duration = min(fade_duration, max(0.0, duration / 2))
    if fade_duration <= 0:
        return ""
    return (
        f",fade=t=in:st=0:d={fade_duration:.2f}"
        f",fade=t=out:st={max(0.0, duration - fade_duration):.2f}:d={fade_duration:.2f}"
    )


WATERMARK_FONT = "/Library/Fonts/Roboto-Bold.ttf"
WATERMARK_FONT_FALLBACK = "/System/Library/Fonts/Helvetica.ttc"
WATERMARK_LOGO_HEIGHT = 48
# TikTok/Shorts UI overlays: top search bar (~150px), right action rail (~160px),
# bottom captions + progress (~300px). Keep the watermark clear of all of them.
WATERMARK_TOP = 170
WATERMARK_RIGHT = 180
WATERMARK_BOTTOM = 330
WATERMARK_LEFT = 40

LOGO_API = "https://commons.wikimedia.org/w/api.php"
LOGO_FILES = {
    "twitch": "File:Twitch_logo.svg",
    "youtube": "File:YouTube_full-color_icon_(2017).svg",
}


def _watermark_font() -> str:
    from pathlib import Path as _Path

    return WATERMARK_FONT if _Path(WATERMARK_FONT).exists() else WATERMARK_FONT_FALLBACK


def resolve_logo(platform: str, storage_dir) -> Optional[str]:
    """Custom PNG wins (storage/watermarks/{platform}.png), else auto-download
    the official logo via Wikimedia thumbnail API into {platform}.auto.png."""
    from pathlib import Path as _Path

    storage_dir = _Path(storage_dir)
    custom = storage_dir / "watermarks" / f"{platform}.png"
    if custom.is_file():
        return str(custom)
    cached = storage_dir / "watermarks" / f"{platform}.auto.png"
    if cached.is_file() and cached.stat().st_size > 0:
        return str(cached)
    filename = LOGO_FILES.get(platform)
    if not filename:
        return None
    try:
        import requests

        info = requests.get(
            LOGO_API,
            params={"action": "query", "titles": filename, "prop": "imageinfo",
                    "iiprop": "url", "iiurlwidth": 256, "format": "json"},
            headers={"User-Agent": "Klipani/1.0 (local video app)"},
            timeout=20,
        ).json()
        pages = info.get("query", {}).get("pages", {}).values()
        thumb = next((p.get("imageinfo", [{}])[0].get("thumburl", "") for p in pages), "")
        if not thumb:
            return None
        data = requests.get(
            thumb, headers={"User-Agent": "Klipani/1.0 (local video app)"}, timeout=30,
        )
        data.raise_for_status()
        if not data.content.startswith(b"\x89PNG"):
            return None
        cached.write_bytes(data.content)
        return str(cached)
    except Exception as error:  # noqa: BLE001 — logo is optional decoration
        logger.warning("Auto logo download failed for %s: %s", platform, error)
        return None


def watermark_spec(
    enabled: bool,
    platform: str,
    text: str,
    position: str,
    storage_dir,
    clip_id: str,
) -> Optional[dict]:
    """Build a watermark overlay spec, or None when disabled/empty.

    Returns {"logo": Path|None, "draw": <drawtext fragment>, "overlay": "X:Y"}.
    Logo: custom storage/watermarks/{twitch,youtube}.png wins, else the
    official logo is auto-downloaded and cached. Text-only when offline.
    Positions respect TikTok/Shorts UI safe zones (not at the edges).
    """
    nickname = (text or "").strip()
    if not enabled or not nickname:
        return None
    if position not in ("top-right", "top-left", "bottom-right", "bottom-left"):
        position = "top-left"
    from pathlib import Path as _Path

    storage_dir = _Path(storage_dir)
    logo_file = resolve_logo(platform, storage_dir)
    logo_path = _Path(logo_file) if logo_file else None
    textfile = storage_dir / "clips" / f"{clip_id}.wm.txt"
    textfile.write_text(f"@{nickname}", encoding="utf-8")
    right = position.endswith("right")
    bottom = position.startswith("bottom")
    text_h = "text_h"
    margin_x = WATERMARK_RIGHT if right else WATERMARK_LEFT
    if logo_path is not None:
        if position == "top-right":
            overlay = f"W-w-{margin_x}:{WATERMARK_TOP}"
        elif position == "top-left":
            overlay = f"{margin_x}:{WATERMARK_TOP}"
        elif position == "bottom-right":
            overlay = f"W-w-{margin_x}:H-h-{WATERMARK_BOTTOM}"
        else:
            overlay = f"{margin_x}:H-h-{WATERMARK_BOTTOM}"
        gap = WATERMARK_LOGO_HEIGHT + 16
        if bottom:
            y = f"H-{text_h}-{WATERMARK_BOTTOM + gap}"
        else:
            y = str(WATERMARK_TOP + gap)
    else:
        overlay = ""
        y = f"H-{text_h}-{WATERMARK_BOTTOM}" if bottom else str(WATERMARK_TOP)
    x = f"w-text_w-{margin_x}" if right else str(margin_x)
    draw = (
        f"drawtext=fontfile='{_watermark_font()}':textfile='{textfile}'"
        f":fontsize=44:fontcolor=white:borderw=2:bordercolor=black@0.8:x={x}:y={y}"
    )
    return {"logo": logo_path, "draw": draw, "overlay": overlay}


def audio_filter(duration: float, fade_duration: float = 0.4) -> str:
    fade_duration = min(fade_duration, max(0.0, duration / 2))
    if fade_duration <= 0:
        return "anull"
    return (
        f"afade=t=in:st=0:d={fade_duration:.2f},"
        f"afade=t=out:st={max(0.0, duration - fade_duration):.2f}:d={fade_duration:.2f}"
    )
