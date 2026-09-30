from datetime import datetime, timezone
from typing import Optional

from ..db import database, many, one


def save_publication(clip_id: str, provider: str, external_id: str, url: str) -> dict:
    record = {
        "id": external_id,
        "clip_id": clip_id,
        "provider": provider,
        "external_id": external_id,
        "url": url,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with database() as db:
        db.execute(
            """INSERT OR REPLACE INTO publications VALUES
               (:id,:clip_id,:provider,:external_id,:url,:created_at)""",
            record,
        )
    return record


def list_publications(clip_id: Optional[str] = None) -> list[dict]:
    with database() as db:
        if clip_id:
            rows = db.execute(
                "SELECT * FROM publications WHERE clip_id=? ORDER BY created_at DESC", (clip_id,)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM publications ORDER BY created_at DESC").fetchall()
        return many(rows)
