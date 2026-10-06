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
