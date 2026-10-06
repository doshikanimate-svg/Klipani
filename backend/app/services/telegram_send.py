"""Send rendered clips to the user via the subscription bot (Telegram Bot API).

Lets the user pull the MP4 on their phone and post it anywhere.
Recipients = subscribers known to bot/bot.db (single-user case: just you).
"""

import asyncio
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BYTES = 50 * 1024 * 1024


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def recipient_chat_ids() -> list:
    """Linked chat from app settings first, then bot subscribers."""
    from .app_settings import get_all

    try:
        linked = (get_all().get("tg_chat_id") or "").strip()
    except Exception:
        linked = ""
    ids = []
    if linked:
        try:
            ids.append(int(linked))
        except ValueError:
            pass
    ids.extend(known_chat_ids())
    seen, unique = set(), []
    for chat_id in ids:
        if chat_id not in seen:
            seen.add(chat_id)
            unique.append(chat_id)
    return unique


def known_chat_ids() -> list:
    db_path = _project_root() / "bot" / "bot.db"
    if not db_path.exists():
        return []
    try:
        connection = sqlite3.connect(db_path)
        rows = connection.execute("SELECT tg_id FROM subscribers").fetchall()
        connection.close()
    except sqlite3.Error as error:
        logger.warning("bot.db unreadable: %s", error)
        return []
    return [int(row[0]) for row in rows]


def send_clip(clip_path: str, caption: str = "") -> dict:
    from ..config import get_settings

    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env.")
    path = Path(clip_path)
    if not path.exists():
        raise RuntimeError("Файл клипа не найден.")
    if path.stat().st_size > MAX_BYTES:
        raise RuntimeError("Клип больше 50 МБ — лимит Telegram Bot API.")
    chat_ids = recipient_chat_ids()
    if not chat_ids:
        raise RuntimeError("Telegram не привязан: укажите Chat ID в профиле (команда /myid в боте).")

    from aiogram import Bot
    from aiogram.types import FSInputFile

    async def _send() -> list:
        bot = Bot(token=settings.telegram_bot_token)
        sent = []
        try:
            for chat_id in chat_ids:
                try:
                    await bot.send_video(chat_id, FSInputFile(path), caption=caption[:1024])
                    sent.append(chat_id)
                except Exception as error:
                    logger.warning("send to %s failed: %s", chat_id, error)
        finally:
            await bot.session.close()
        return sent

    sent = asyncio.run(_send())
    if not sent:
        raise RuntimeError("Не удалось доставить ни одному получателю.")
    return {"sent_to": sent}
