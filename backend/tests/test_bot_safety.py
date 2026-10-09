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
    import main as bot_main

    monkeypatch.setattr(bot_main, "DB_PATH", tmp_path / "bot.db")
    assert bot_main.trial_used(111) is False
    assert bot_main.claim_trial(111) is True
    assert bot_main.trial_used(111) is True
    assert bot_main.claim_trial(111) is False
    bot_main.save_subscriber(111, "trial", "KLIP-x", 9999999999)
    assert bot_main.trial_used(111) is True
    assert bot_main.trial_used(222) is False


def test_reply_menu_buttons() -> None:
    import main as bot_main

    keyboard = bot_main.main_menu_keyboard()
    texts = [button.text for row in keyboard.keyboard for button in row]
    assert texts == ["🛟 Поддержка", "ℹ️ О проекте", "💳 Тарифы", "🛒 Купить подписку", "📥 Скачать приложение", "🛠 Админка"]


def test_every_menu_button_gets_an_answer() -> None:
    """Regression: a keyboard button the dispatcher filter drops stays silent.

    Feeds every button text through on_menu_text with a fake message and
    requires an answer — exactly the «кнопка есть, тишина» failure.
    """
    import asyncio

    import main as bot_main

    class _FakeMessage:
        def __init__(self, text):
            self.text = text
            self.answers = []
            self.from_user = SimpleNamespace(id=999, username="randomuser")

        async def answer(self, text, **kwargs):
            self.answers.append(text)

    keyboard = bot_main.main_menu_keyboard()
    texts = [button.text for row in keyboard.keyboard for button in row]
    for text in texts:
        message = _FakeMessage(text)
        asyncio.run(bot_main.on_menu_text(message))
        assert message.answers, f"no answer for button {text!r}"
    download = _FakeMessage("📥 Скачать приложение")
    asyncio.run(bot_main.on_menu_text(download))
    assert any("Скачать KLIPANI" in answer for answer in download.answers)


def test_admin_button_shared_with_command(monkeypatch, tmp_path) -> None:
    """🛠 button shows the same panel as /admin; strangers get no access."""
    import asyncio
    import sqlite3

    import main as bot_main

    monkeypatch.setattr(bot_main, "DB_PATH", tmp_path / "bot.db")

    class _Msg:
        def __init__(self, username):
            self.from_user = SimpleNamespace(id=1, username=username)
            self.answers = []

        async def answer(self, text, **kwargs):
            self.answers.append(text)

    stranger = _Msg("randomuser")
    asyncio.run(bot_main.on_menu_text(_WrapText("🛠 Админка", stranger)))
    assert stranger.answers == ["Нет доступа."]

    admin = _Msg("LiveForWork1")
    asyncio.run(bot_main.on_menu_text(_WrapText("🛠 Админка", admin)))
    assert any("Админка" in a for a in admin.answers)

    # Same content as /admin command.
    direct = _Msg("LiveForWork1")
    asyncio.run(bot_main.cmd_admin(direct))
    assert direct.answers == admin.answers


class _WrapText:
    """Message facade: fixed button text, delegated answers + identity."""

    def __init__(self, text, origin):
        self.text = text
        self.from_user = origin.from_user
        self._origin = origin

    async def answer(self, *args, **kwargs):
        return await self._origin.answer(*args, **kwargs)


def test_admin_lookup_and_notify(monkeypatch, tmp_path) -> None:
    import asyncio

    import main as bot_main

    monkeypatch.setattr(bot_main, "DB_PATH", tmp_path / "bot.db")
    assert bot_main._admin_id() is None  # nobody said /start yet

    bot_main._remember_user(777, "LiveForWork1")
    assert bot_main._admin_id() == 777
    bot_main._remember_user(777, None)  # empty username must not orphan the admin
    assert bot_main._admin_id() == 777

    sent = []

    class _Bot:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((chat_id, text))

    bot_main._remember_user(777, "liveforwork1")  # case-insensitive
    asyncio.run(bot_main._notify_admin(_Bot(), "💰 test"))
    assert sent == [(777, "💰 test")]

    monkeypatch.setattr(bot_main, "DB_PATH", tmp_path / "empty.db")
    asyncio.run(bot_main._notify_admin(_Bot(), "💰 lost"))  # silent, no crash
    assert len(sent) == 1


def test_da_admin_notify_on_issue(monkeypatch, tmp_path) -> None:
    """DonationAlerts auto-issue pings the admin, not just the buyer."""
    import sqlite3

    import app.services.dapayments as da

    db_path = tmp_path / "da.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE users (tg_id INTEGER PRIMARY KEY, username TEXT, updated_at INTEGER)"
    )
    connection.execute("INSERT INTO users VALUES (555, 'liveforwork1', 1)")
    connection.commit()

    assert da._admin_id(connection) == 555
    connection.close()

    calls = []
    monkeypatch.setattr(da, "_notify_telegram", lambda tg, text: calls.append((tg, text)))
    connection = sqlite3.connect(db_path)
    da._notify_admin(connection, "💰 ping")
    connection.close()
    assert calls == [(555, "💰 ping")]
