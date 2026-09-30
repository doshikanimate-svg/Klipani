from datetime import datetime, timezone
from typing import Optional

from ..db import database, one


def save_analysis(video_id: str, has_transcript: bool, segments: int, llm_provider: str) -> dict:
    """Persist how highlights were produced so the UI can show it honestly."""
    record = {
        "video_id": video_id,
        "has_transcript": 1 if has_transcript else 0,
        "segments": segments,
        "llm_provider": llm_provider,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with database() as db:
        db.execute("DELETE FROM analyses WHERE video_id=?", (video_id,))
        db.execute(
            "INSERT INTO analyses VALUES (:video_id,:has_transcript,:segments,:llm_provider,:created_at)",
            record,
        )
    return record


def get_analysis(video_id: str) -> Optional[dict]:
    with database() as db:
        return one(db.execute("SELECT * FROM analyses WHERE video_id=?", (video_id,)).fetchone())
