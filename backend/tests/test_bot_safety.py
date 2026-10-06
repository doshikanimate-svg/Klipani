import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bot"))

from aiogram.exceptions import TelegramBadRequest

import main as bot_main


class _FakeMessage:
    def __init__(self, fail=False):
        self.fail = fail
        self.texts = []

    async def edit_text(self, text, parse_mode=None, reply_markup=None):
        if self.fail:
            raise TelegramBadRequest(
                method="editMessageText",
                message="Bad Request: message is not modified: specified new message content "
                        "and reply markup are exactly the same as a current content and reply markup of the message",
            )
        self.texts.append(text)


def _callback(fail=False):
    answered = []

    async def answer(*args, **kwargs):
        answered.append(True)

    return SimpleNamespace(message=_FakeMessage(fail), answer=answer)


def test_safe_edit_swallows_not_modified():
    asyncio.run(bot_main._safe_edit(_callback(fail=True), "same"))
    callback = _callback(fail=False)
    asyncio.run(bot_main._safe_edit(callback, "new"))
    assert callback.message.texts == ["new"]


def test_error_handler_swallows_not_modified():
    event = SimpleNamespace(
        exception=TelegramBadRequest(method="x", message="Bad Request: message is not modified")
    )
    assert asyncio.run(bot_main._on_aiogram_error(event)) is True
    other = SimpleNamespace(exception=ValueError("boom"))
    assert asyncio.run(bot_main._on_aiogram_error(other)) is True


def test_trial_once_per_account(monkeypatch, tmp_path) -> None:
    import sqlite3
    import main as bot_main

    db_path = tmp_path / "bot.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE subscribers (tg_id INTEGER PRIMARY KEY, plan TEXT, license_key TEXT, exp INTEGER)"
    )
    connection.commit()

    class _Ctx:
        def __enter__(self):
            return sqlite3.connect(db_path)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(bot_main, "_db", lambda: _Ctx())
    assert bot_main.trial_used(111) is False
    bot_main.save_subscriber(111, "trial", "KLIP-x", 9999999999)
    assert bot_main.trial_used(111) is True
    assert bot_main.trial_used(222) is False


def test_reply_menu_buttons() -> None:
    import main as bot_main

    keyboard = bot_main.main_menu_keyboard()
    texts = [button.text for row in keyboard.keyboard for button in row]
    assert texts == ["🛟 Поддержка", "ℹ️ О проекте", "💳 Тарифы", "🛒 Купить подписку"]
