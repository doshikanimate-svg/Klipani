from ..db import database, one

DEFAULTS = {
    "watermark_enabled": "0",
    "watermark_platform": "twitch",
    "watermark_text": "",
    "watermark_position": "top-left",
}


def get_all() -> dict:
    with database() as db:
        rows = db.execute("SELECT key, value FROM app_settings").fetchall()
    result = dict(DEFAULTS)
    for row in rows:
        if row["key"] in result:
            result[row["key"]] = row["value"]
    return result


def update(values: dict) -> dict:
    allowed = set(DEFAULTS)
    clean = {k: str(v) for k, v in values.items() if k in allowed}
    if "watermark_platform" in clean and clean["watermark_platform"] not in ("twitch", "youtube"):
        raise ValueError("Платформа: twitch или youtube.")
    if "watermark_position" in clean and clean["watermark_position"] not in (
        "top-right", "top-left", "bottom-right", "bottom-left",
    ):
        raise ValueError("Некорректная позиция.")
    if "watermark_enabled" in clean and clean["watermark_enabled"] not in ("0", "1", "true", "false"):
        raise ValueError("Некорректный флаг.")
    if "watermark_text" in clean:
        clean["watermark_text"] = clean["watermark_text"].strip()[:60]
    with database() as db:
        for key, value in clean.items():
            db.execute("INSERT OR REPLACE INTO app_settings VALUES (?, ?)", (key, value))
    return get_all()
