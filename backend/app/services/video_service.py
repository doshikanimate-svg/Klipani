from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from ..db import database, many, one
from ..utils.ffmpeg import probe


def create_video(filename: str, path: Path, size: int) -> dict:
    metadata = probe(path)
    video = {"id": uuid4().hex, "filename": filename, "path": str(path), "size": size, "status": "READY", "created_at": datetime.now(timezone.utc).isoformat(), **metadata}
    with database() as db:
        db.execute("""INSERT INTO videos VALUES (:id,:filename,:path,:size,:duration,:width,:height,:fps,:codec,:audio_streams,:audio_codec,:status,:created_at)""", video)
    return video


def get_video(video_id: str) -> Optional[dict]:
    with database() as db:
        return one(db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())


def list_videos() -> list[dict]:
    with database() as db:
        return many(db.execute("SELECT * FROM videos ORDER BY created_at DESC").fetchall())


def delete_video(video_id: str) -> dict:
    """Remove a video and everything derived from it (clips, transcripts, plans)."""
    from .clip_service import list_clips

    video = get_video(video_id)
    if not video:
        return {"deleted": False, "removed_files": 0, "freed_bytes": 0}
    removed_files = 0
    freed_bytes = 0
    candidates = [Path(video["path"])]
    for clip in list_clips(video_id):
        candidates.append(Path(clip["output_path"]))
        candidates.append(Path(clip["thumbnail_path"]))
        stem = Path(clip["output_path"]).with_suffix("")
        candidates.append(Path(str(stem) + ".ass"))
        candidates.append(Path(str(stem) + ".montage.json"))
        candidates.append(Path(str(stem) + ".wm.txt"))
    with database() as db:
        transcript = db.execute("SELECT * FROM transcripts WHERE video_id=?", (video_id,)).fetchone()
        if transcript:
            candidates.append(Path(transcript["path"]))
        for candidate in candidates:
            try:
                if candidate.is_file():
                    freed_bytes += candidate.stat().st_size
                    candidate.unlink()
                    removed_files += 1
            except OSError:
                pass
        clip_ids = [row["id"] for row in db.execute("SELECT id FROM clips WHERE video_id=?", (video_id,)).fetchall()]
        for clip_id in clip_ids:
            db.execute("DELETE FROM publications WHERE clip_id=?", (clip_id,))
        db.execute("DELETE FROM clips WHERE video_id=?", (video_id,))
        db.execute("DELETE FROM highlights WHERE video_id=?", (video_id,))
        db.execute("DELETE FROM transcripts WHERE video_id=?", (video_id,))
        db.execute("DELETE FROM analyses WHERE video_id=?", (video_id,))
        db.execute("DELETE FROM jobs WHERE video_id=?", (video_id,))
        db.execute("DELETE FROM videos WHERE id=?", (video_id,))
    return {"deleted": True, "removed_files": removed_files, "freed_bytes": freed_bytes}


def clear_cache(keep_video_id: Optional[str] = None) -> dict:
    """Delete all videos except the one in progress (cache cleanup).

    The current video, its clips and transcripts are kept, so the
    clipping session is not reset.
    """
    deleted_videos = 0
    removed_files = 0
    freed_bytes = 0
    for video in list_videos():
        if keep_video_id and video["id"] == keep_video_id:
            continue
        result = delete_video(video["id"])
        if result.get("deleted"):
            deleted_videos += 1
            removed_files += result.get("removed_files", 0)
            freed_bytes += result.get("freed_bytes", 0)
    return {"deleted_videos": deleted_videos, "removed_files": removed_files, "freed_bytes": freed_bytes}
