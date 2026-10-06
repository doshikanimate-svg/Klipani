from ..db import database, one

DEFAULTS = {
    "watermark_enabled": "0",
    "watermark_platform": "twitch",
    "watermark_text": "",
    "watermark_position": "top-left",
    "tg_chat_id": "",
}

# Stored separately from DEFAULTS: never exposed via /api/settings, only via
# the license endpoints (so a stray settings write cannot wipe the key).
PRIVATE_KEYS = {"license_key"}


def get_all() -> dict:
    with database() as db:
        rows = db.execute("SELECT key, value FROM app_settings").fetchall()
    result = dict(DEFAULTS)
    for row in rows:
        if row["key"] in result:
            result[row["key"]] = row["value"]
    return result


def get_private(key: str) -> str:
    """Read one private setting (license key) — never part of the public dict."""
    with database() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""


def set_private(key: str, value: str) -> str:
    """Write one private setting (license key) after the caller validated it."""
    if key not in PRIVATE_KEYS:
        raise ValueError("Неизвестная настройка.")
    with database() as db:
        db.execute("INSERT OR REPLACE INTO app_settings VALUES (?, ?)", (key, str(value)))
    return get_private(key)


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
    if "tg_chat_id" in clean:
        clean["tg_chat_id"] = clean["tg_chat_id"].strip()
        if clean["tg_chat_id"] and not clean["tg_chat_id"].lstrip("-").isdigit():
            raise ValueError("Chat ID — только цифры.")
    with database() as db:
        for key, value in clean.items():
            db.execute("INSERT OR REPLACE INTO app_settings VALUES (?, ?)", (key, value))
    return get_all()
