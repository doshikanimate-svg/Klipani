from datetime import datetime, timezone

from ..db import database, one


def get_clip_stats(clip_id: str) -> dict:
    with database() as db:
        row = one(db.execute("SELECT * FROM clip_stats WHERE clip_id=?", (clip_id,)).fetchone())
    if row:
        return row
    return {"clip_id": clip_id, "views": 0, "likes": 0, "updated_at": None}


def put_clip_stats(clip_id: str, views: int, likes: int) -> dict:
    record = {
        "clip_id": clip_id,
        "views": max(0, int(views)),
        "likes": max(0, int(likes)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with database() as db:
        db.execute(
            "INSERT OR REPLACE INTO clip_stats VALUES (:clip_id,:views,:likes,:updated_at)",
            record,
        )
    return record


def category_affinity(min_views: int = 100, max_bonus: int = 10) -> dict:
    """Bonus per highlight category from historical views.

    Categories whose clips averaged more views earn up to max_bonus points
    in future selections. Needs at least min_views total to count (noise guard).
    """
    with database() as db:
        rows = db.execute(
            """SELECT h.category AS category, SUM(s.views) AS views, COUNT(*) AS n
               FROM clip_stats s JOIN clips c ON c.id = s.clip_id
               JOIN highlights h ON h.id = c.highlight_id
               GROUP BY h.category"""
        ).fetchall()
    totals = [(row["category"], row["views"] or 0) for row in rows if (row["views"] or 0) >= min_views]
    if not totals:
        return {}
    peak = max(views for _, views in totals)
    if peak <= 0:
        return {}
    return {category: int(round(max_bonus * views / peak)) for category, views in totals}
