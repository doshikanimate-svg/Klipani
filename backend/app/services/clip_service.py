from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from ..config import get_settings
from ..db import database, many, one
from ..utils.ffmpeg import create_thumbnail, render_montage, render_vertical
from .highlight_service import list_highlights
from .montage_service import build_plan
from .effect_service import EffectPlan, audio_filter, vertical_video_filter, watermark_spec
from .subtitle_service import build_ass, build_karaoke_ass, build_karaoke_ass_montage
from .transcription_service import get_transcript
from . import app_settings

import json
import logging

logger = logging.getLogger(__name__)


def _subtitles_for_clip(video_id: str, start: float, end: float, clip_id: str) -> Optional[Path]:
    """Build an ASS file from the video transcript, or None when unavailable."""
    try:
        transcript = get_transcript(video_id)
    except Exception as error:  # noqa: BLE001 — subtitles are best-effort
        logger.warning("Subtitles skipped, transcript unreadable: %s", error)
        return None
    if not transcript or not transcript.get("segments"):
        return None
    content = build_karaoke_ass(transcript["segments"], start, end)
    if "Dialogue" not in content.split("[Events]", 1)[-1]:
        content = build_ass(transcript["segments"], start, end)
        if "Dialogue" not in content.split("[Events]", 1)[-1]:
            return None
    path = get_settings().storage / "clips" / f"{clip_id}.ass"
    path.write_text(content, encoding="utf-8")
    return path


def _watermark_for_clip(clip_id: str) -> Optional[dict]:
    """Read watermark settings; returns render spec or None. Best-effort."""
    try:
        values = app_settings.get_all()
    except Exception as error:  # noqa: BLE001
        logger.warning("Watermark skipped, settings unreadable: %s", error)
        return None
    try:
        return watermark_spec(
            enabled=values.get("watermark_enabled") in ("1", "true"),
            platform=values.get("watermark_platform", "twitch"),
            text=values.get("watermark_text", ""),
            position=values.get("watermark_position", "top-right"),
            storage_dir=get_settings().storage,
            clip_id=clip_id,
        )
    except Exception as error:  # noqa: BLE001
        logger.warning("Watermark skipped: %s", error)
        return None


def _effect_plan(anchor: str) -> EffectPlan:
    return EffectPlan(anchor=anchor if anchor in ("center", "top") else "center")


def generate_clip(video: dict, highlight: dict, with_subtitles: bool = True, style: str = "crop") -> dict:
    settings = get_settings()
    start = max(0, float(highlight["start_time"]) - settings.pre_roll_seconds)
    end = min(float(video["duration"]), float(highlight["end_time"]) + settings.post_roll_seconds)
    end = min(end, start + settings.max_clip_duration)
    duration = max(0.2, end - start)
    clip_id = uuid4().hex
    output = settings.storage / "clips" / f"{clip_id}.mp4"
    thumbnail = settings.storage / "thumbnails" / f"{clip_id}.jpg"
    subtitles = _subtitles_for_clip(video["id"], start, end, clip_id) if with_subtitles else None
    effects = _effect_plan(settings.crop_anchor)
    vf = vertical_video_filter(effects, duration)
    af = audio_filter(duration)
    watermark = _watermark_for_clip(clip_id)
    try:
        render_vertical(Path(video["path"]), output, start, duration, subtitles_path=subtitles, vf_video=vf, af_chain=af, sounds=effects.sounds or None, style=style, watermark=watermark)
    except Exception:
        if subtitles is not None:
            # Subtitles are enhancement, not requirement: retry clean render.
            logger.warning("Render with subtitles failed, retrying without them.")
            subtitles.unlink(missing_ok=True)
            render_vertical(Path(video["path"]), output, start, duration, vf_video=vf, af_chain=af, sounds=effects.sounds or None, style=style, watermark=watermark)
        else:
            raise
    create_thumbnail(output, thumbnail)
    clip = {"id": clip_id, "video_id": video["id"], "highlight_id": highlight["id"], "start_time": start, "end_time": end, "output_path": str(output), "thumbnail_path": str(thumbnail), "status": "DONE", "created_at": datetime.now(timezone.utc).isoformat(), "kind": "CUT"}
    with database() as db:
        db.execute("""INSERT INTO clips VALUES (:id,:video_id,:highlight_id,:start_time,:end_time,:output_path,:thumbnail_path,:status,:created_at,:kind)""", clip)
    return clip


def generate_montage(video: dict, highlight: dict, with_subtitles: bool = True, style: str = "crop") -> dict:
    settings = get_settings()
    try:
        transcript = get_transcript(video["id"])
        segments = transcript["segments"] if transcript else None
    except Exception:
        segments = None
    highlights = list_highlights(video["id"]) or [highlight]
    plan = build_plan(video, highlights, segments)
    clip_id = uuid4().hex
    output = settings.storage / "clips" / f"{clip_id}.mp4"
    thumbnail = settings.storage / "thumbnails" / f"{clip_id}.jpg"
    plan_path = settings.storage / "clips" / f"{clip_id}.montage.json"
    plan_path.write_text(plan.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")
    parts = [(part.source_start, part.source_end) for part in plan.parts]
    total = plan.total_duration
    subtitles: Optional[Path] = None
    if segments and with_subtitles:
        content = build_karaoke_ass_montage(segments, parts)
        if "Dialogue" in content.split("[Events]", 1)[-1]:
            subtitles = settings.storage / "clips" / f"{clip_id}.ass"
            subtitles.write_text(content, encoding="utf-8")
    effects = _effect_plan(settings.crop_anchor)
    vf = vertical_video_filter(effects, total)
    af = audio_filter(total)
    watermark = _watermark_for_clip(clip_id)
    try:
        render_montage(Path(video["path"]), output, parts, subtitles_path=subtitles, vf_video=vf, af_chain=af, sounds=effects.sounds or None, style=style, watermark=watermark)
    except Exception:
        if subtitles is not None:
            logger.warning("Montage render with subtitles failed, retrying without them.")
            subtitles.unlink(missing_ok=True)
            render_montage(Path(video["path"]), output, parts, vf_video=vf, af_chain=af, sounds=effects.sounds or None, style=style, watermark=watermark)
        else:
            raise
    create_thumbnail(output, thumbnail)
    clip = {"id": clip_id, "video_id": video["id"], "highlight_id": highlight["id"], "start_time": parts[0][0], "end_time": parts[0][0] + total, "output_path": str(output), "thumbnail_path": str(thumbnail), "status": "DONE", "created_at": datetime.now(timezone.utc).isoformat(), "kind": "MONTAGE"}
    with database() as db:
        db.execute("""INSERT INTO clips VALUES (:id,:video_id,:highlight_id,:start_time,:end_time,:output_path,:thumbnail_path,:status,:created_at,:kind)""", clip)
    return clip


def list_clips(video_id: Optional[str] = None) -> list[dict]:
    with database() as db:
        query, params = ("SELECT * FROM clips WHERE video_id=? ORDER BY created_at DESC", (video_id,)) if video_id else ("SELECT * FROM clips ORDER BY created_at DESC", ())
        return many(db.execute(query, params).fetchall())


def get_clip(clip_id: str) -> Optional[dict]:
    with database() as db:
        return one(db.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone())


def delete_clip(clip_id: str) -> dict:
    """Remove one clip with all its files (video, subtitles, plan, thumbnail)."""
    clip = get_clip(clip_id)
    if not clip:
        return {"deleted": False, "removed_files": 0}
    removed_files = 0
    candidates = [Path(clip["output_path"]), Path(clip["thumbnail_path"])]
    stem = Path(clip["output_path"]).with_suffix("")
    candidates.append(Path(str(stem) + ".ass"))
    candidates.append(Path(str(stem) + ".montage.json"))
    candidates.append(Path(str(stem) + ".wm.txt"))
    for candidate in candidates:
        try:
            if candidate.is_file():
                candidate.unlink()
                removed_files += 1
        except OSError:
            pass
    with database() as db:
        db.execute("DELETE FROM publications WHERE clip_id=?", (clip_id,))
        db.execute("DELETE FROM clip_stats WHERE clip_id=?", (clip_id,))
        db.execute("DELETE FROM clips WHERE id=?", (clip_id,))
    return {"deleted": True, "removed_files": removed_files}
