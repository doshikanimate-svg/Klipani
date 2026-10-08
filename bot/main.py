"""KLIPANI subscription bot (aiogram 3, long polling).

Flow: /start -> plans -> stub checkout ("Я оплатил (тест)") -> license key.
Real acquiring plugs into `create_stub_invoice()` later: replace the fake
confirm step with a real payment provider callback, keep key issuance as is.
"""

import asyncio
import logging
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.config import get_settings
from app.services.license_service import PLANS, issue_license

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("klipani-bot")

DB_PATH = Path(__file__).resolve().parent / "bot.db"


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS subscribers (
             tg_id INTEGER PRIMARY KEY, plan TEXT NOT NULL,
             license_key TEXT NOT NULL, exp INTEGER NOT NULL
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS trial_claims (
             tg_id INTEGER PRIMARY KEY, claimed_at INTEGER NOT NULL
           )"""
    )
    # Backfill: trials issued before this table existed still count —
    # buying a paid plan later must not reopen the free trial.
    connection.execute(
        """INSERT OR IGNORE INTO trial_claims(tg_id, claimed_at)
           SELECT tg_id, exp FROM subscribers WHERE plan='trial'"""
    )
    connection.commit()
    return connection


SUPPORT_URL = "https://t.me/LiveForWork1"
CHANNEL_URL = "https://t.me/Klipani_of"
RELEASES_URL = "https://github.com/doshikanimate-svg/klipani/releases/latest"

ABOUT_TEXT = (
    "📱 <b>KLIPANI</b> — студия вертикальных клипов.\n\n"
    "Загружаете запись стрима — получаете готовые ролики для TikTok, Shorts и Reels: "
    "умный поиск моментов, субтитры-караоке, монтаж, водяной знак вашего твича/ютуба "
    "и публикация в один клик.\n\n"
    "Новости и обновления: @Klipani_of"
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Persistent bottom keyboard (stays with the user, not attached to a message)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛟 Поддержка"), KeyboardButton(text="ℹ️ О проекте")],
            [KeyboardButton(text="💳 Тарифы"), KeyboardButton(text="🛒 Купить подписку")],
            [KeyboardButton(text="📥 Скачать приложение")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def back_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← В меню", callback_data="menu")],
    ])


def _ensure_extra_columns(connection) -> None:
    for column in ("method TEXT NOT NULL DEFAULT ''", "status TEXT NOT NULL DEFAULT ''"):
        try:
            connection.execute(f"ALTER TABLE pending_payments ADD COLUMN {column}")
        except Exception:
            pass


def trial_used(tg_id: int) -> bool:
    """Trial is once per Telegram account, forever.

    The claim lives in its own table so buying a paid plan afterwards
    (which overwrites the subscribers row) can never reopen the free trial.
    """
    with _db() as db:
        row = db.execute(
            "SELECT 1 FROM trial_claims WHERE tg_id=?", (int(tg_id),)
        ).fetchone()
        return row is not None


def claim_trial(tg_id: int) -> bool:
    """Atomically record the free-trial claim. True = first time, False = already used."""
    import time as _time

    with _db() as db:
        cursor = db.execute(
            "INSERT OR IGNORE INTO trial_claims(tg_id, claimed_at) VALUES (?, ?)",
            (int(tg_id), int(_time.time())),
        )
        db.commit()
        return cursor.rowcount == 1


def save_subscriber(tg_id: int, plan: str, key: str, exp: int) -> None:
    with _db() as db:
        db.execute(
            "INSERT OR REPLACE INTO subscribers VALUES (?, ?, ?, ?)",
            (int(tg_id), plan, key, exp),
        )
        db.commit()


def is_admin(message_or_user) -> bool:
    username = (getattr(message_or_user, "username", "") or "").lower()
    expected = (get_settings().tg_admin_username or "LiveForWork1").lower()
    return bool(username) and username == expected


def plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=info["title"], callback_data=f"plan:{plan_id}")]
        for plan_id, info in PLANS.items()
    ] + [[InlineKeyboardButton(text="← В меню", callback_data="menu")]])


def pay_keyboard(plan_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить*", callback_data=f"pay:{plan_id}")],
        [InlineKeyboardButton(text="← В меню", callback_data="menu")],
    ])


def confirm_keyboard(plan_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Я оплатил (тест)", callback_data=f"confirm:{plan_id}")],
        [InlineKeyboardButton(text="← В меню", callback_data="menu")],
    ])


async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Я бот <b>KLIPANI</b> — студии вертикальных клипов.\n\n"
        "Здесь можно выбрать тариф, оформить подписку и получить лицензионный ключ "
        "для приложения. Меню всегда под рукой — кнопки внизу. "
        "Если что-то непонятно — загляните в «О проекте» или напишите в поддержку.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def on_menu_text(message: Message) -> None:
    """Reply-keyboard buttons route here."""
    text = (message.text or "").strip()
    if text.startswith("🛟"):
        await message.answer(
            "Возникли вопросы или что-то пошло не так? Напишите нам — разберёмся:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✉️ Написать в поддержку", url=SUPPORT_URL)],
            ]),
        )
    elif text.startswith("ℹ️"):
        await message.answer(
            ABOUT_TEXT,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📣 Telegram-канал", url=CHANNEL_URL)],
            ]),
        )
    elif text.startswith("💳"):
        await message.answer(
            "💳 <b>Тарифы KLIPANI:</b>\n\n" + "\n".join(
                f"• <b>{info['title']}</b> — {info['days']} дней"
                for info in PLANS.values()
            ) + "\n\nЧтобы оформить — нажмите «🛒 Купить подписку».",
            parse_mode="HTML",
        )
    elif text.startswith("🛒"):
        await message.answer(
            "🛒 Выберите тариф для покупки:",
            reply_markup=plans_keyboard(),
        )
    elif text.startswith("📥"):
        await message.answer(
            "📥 <b>Скачать KLIPANI:</b>\n\n"
            "Выбирайте файл под свою систему на странице релизов — "
            "там всегда последняя версия:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬇️ Страница загрузки", url=RELEASES_URL)],
            ]),
        )
        await message.answer(
            "⚠️ Приложение пока без платной подписи издателя:\n"
            "• <b>macOS</b>: после установки откройте через правый клик → «Открыть»;\n"
            "• <b>Windows</b>: при предупреждении SmartScreen нажмите «Подробнее» → «Выполнить в любом случае».\n\n"
            "При первом запуске скачайте модель Whisper кнопкой в приложении (~150 МБ, один раз).",
            parse_mode="HTML",
        )


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    """edit_text that ignores 'message is not modified' (double taps, retried updates)."""
    from aiogram.exceptions import TelegramBadRequest

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


async def _on_aiogram_error(event) -> bool:
    from aiogram.exceptions import TelegramBadRequest

    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc):
        return True
    logger.warning("unhandled bot update error: %r", exc)
    return True


async def on_menu(callback: CallbackQuery) -> None:
    await _safe_edit(
        callback,
        "Главное меню <b>KLIPANI</b> — чем помочь?",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


async def on_about(callback: CallbackQuery) -> None:
    await _safe_edit(
        callback,
        ABOUT_TEXT,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📣 Telegram-канал", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="← В меню", callback_data="menu")],
        ]),
    )
    await callback.answer()


async def cmd_status(message: Message) -> None:
    with _db() as db:
        row = db.execute(
            "SELECT plan, exp FROM subscribers WHERE tg_id=?", (message.from_user.id,)
        ).fetchone()
    if not row:
        await message.answer("Подписка не найдена. Выберите тариф: /start")
        return
    plan, exp = row
    left = max(0, (exp - int(time.time())) // 86400)
    await message.answer(
        f"📄 Тариф: <b>{PLANS[plan]['title']}</b>\nОсталось дней: <b>{left}</b>",
        parse_mode="HTML",
    )


async def cmd_myid(message: Message) -> None:
    await message.answer(
        f"🆔 Ваш Chat ID:\n<code>{message.from_user.id}</code>\n\n"
        "Вставьте его в приложении: Профиль → Telegram.",
        parse_mode="HTML",
    )


def convert_legacy_key(tg_id: int) -> Optional[str]:
    """Re-issue a stored HMAC key as Ed25519 with the same expiry.

    Old app versions are gone; the desktop verifies only KLIP2 now.
    Returns the new key, or None when there is nothing to convert.
    """
    from app.services.license_service import issue_license, key_format, verify_license

    with _db() as db:
        row = db.execute(
            "SELECT plan, license_key, exp FROM subscribers WHERE tg_id=?", (int(tg_id),)
        ).fetchone()
    if not row:
        return None
    plan, stored_key, _exp = row
    if key_format(stored_key) != "hmac":
        return None
    info = verify_license(stored_key)  # bot has LICENSE_SECRET: verifies HMAC fine
    if not info:
        return None
    converted = issue_license(info["telegram_id"], info["plan"], exp=info["exp"])
    save_subscriber(info["telegram_id"], info["plan"], converted["key"], info["exp"])
    logger.info("converted legacy key to ed25519 for tg_id=%s", tg_id)
    return converted["key"]


async def cmd_mykey(message: Message) -> None:
    with _db() as db:
        row = db.execute(
            "SELECT license_key, exp FROM subscribers WHERE tg_id=?", (message.from_user.id,)
        ).fetchone()
    if not row or row[1] <= time.time():
        await message.answer("Активного ключа нет. Выберите тариф: /start")
        return
    key = convert_legacy_key(message.from_user.id) or row[0]
    await message.answer(
        f"🔑 Ваш ключ:\n<code>{key}</code>\n\nВставьте его в приложении: Подписка → Активировать.",
        parse_mode="HTML",
    )


async def on_plans(callback: CallbackQuery) -> None:
    await _safe_edit(
        callback,
        "💳 <b>Тарифы KLIPANI:</b>\n\n" + "\n".join(
            f"• <b>{info['title']}</b> — {info['days']} дней"
            for info in PLANS.values()
        ) + "\n\nНажмите на тариф, чтобы оформить.",
        reply_markup=plans_keyboard(),
    )
    await callback.answer()


async def on_buy(callback: CallbackQuery) -> None:
    await _safe_edit(
        callback,
        "🛒 <b>Покупка подписки:</b> выберите тариф ниже — после тестовой оплаты "
        "бот сразу выдаст лицензионный ключ.",
        reply_markup=plans_keyboard(),
    )
    await callback.answer()


def methods_keyboard(plan_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ DonationAlerts (авто)", callback_data=f"method:da:{plan_id}")],
        [InlineKeyboardButton(text="🪙 Криптовалюта", callback_data=f"method:crypto:{plan_id}")],
        [InlineKeyboardButton(text="💬 Через личные сообщения", callback_data=f"method:dm:{plan_id}")],
        [InlineKeyboardButton(text="← В меню", callback_data="menu")],
    ])


async def _refuse_trial_payment(callback: CallbackQuery, plan_id: str) -> bool:
    """Trial is free and claimed with one button — never through a payment flow.

    Returns True when the plan is the trial (caller must stop right after).
    """
    if plan_id != "trial":
        return False
    await callback.answer(
        "Пробный тариф бесплатный: вернитесь в «Тарифы» и нажмите его кнопку — ключ выдастся сразу.",
        show_alert=True,
    )
    return True


def paid_keyboard(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{code}")],
        [InlineKeyboardButton(text="← В меню", callback_data="menu")],
    ])


async def on_plan(callback: CallbackQuery) -> None:
    plan_id = callback.data.split(":", 1)[1]
    info = PLANS.get(plan_id)
    if not info:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    if plan_id == "trial":
        if not claim_trial(callback.from_user.id):
            converted = convert_legacy_key(callback.from_user.id)
            if converted:
                # Claim stands, only the format was outdated: hand over the
                # new key instead of refusing.
                await _safe_edit(
                    callback,
                    "🔄 Ваш ключ обновлён под новую версию приложения:\n\n"
                    f"<code>{converted}</code>\n\n"
                    "Вставьте его в приложении: Подписка → Активировать.",
                )
                await callback.answer()
                return
            await _safe_edit(
                callback,
                "😕 Пробная подписка выдаётся <b>1 раз на аккаунт</b>, и вы её уже использовали.\n\n"
                "Выберите платный тариф «Месяц» — или напишите в поддержку, разберёмся: @LiveForWork1",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✉️ Написать в поддержку", url=SUPPORT_URL)],
                    [InlineKeyboardButton(text="← В меню", callback_data="menu")],
                ]),
            )
            await callback.answer()
            return
        try:
            issued = issue_license(callback.from_user.id, plan_id)
        except (ValueError, RuntimeError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        save_subscriber(callback.from_user.id, plan_id, issued["key"], issued["exp"])
        logger.info("issued trial license to tg_id=%s", callback.from_user.id)
        await _safe_edit(
            callback,
            "🎉 Пробная подписка на <b>3 дня</b> активирована!\n\n"
            f"🔑 Ваш ключ:\n<code>{issued['key']}</code>\n\n"
            "Вставьте ключ в приложении: Подписка → Активировать.",
        )
        await callback.answer()
        return
    await _safe_edit(
        callback,
        f"📦 <b>{info['title']}</b>\nСрок: {info['days']} дней.\n\nВыберите способ оплаты:",
        reply_markup=methods_keyboard(plan_id),
    )
    await callback.answer()


def _pending_insert(code: str, tg_id: int, plan: str, method: str, status: str = "new") -> None:
    import sqlite3

    connection = sqlite3.connect(DB_PATH)
    try:
        _ensure_extra_columns(connection)
        connection.execute(
            "INSERT OR REPLACE INTO pending_payments(code, tg_id, plan, created_at, method, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (code, int(tg_id), plan, int(time.time()), method, status),
        )
        connection.commit()
    finally:
        connection.close()


def _pending_mark(code: str, status: str) -> Optional[dict]:
    import sqlite3

    connection = sqlite3.connect(DB_PATH)
    try:
        _ensure_extra_columns(connection)
        row = connection.execute(
            "SELECT tg_id, plan, method, status FROM pending_payments WHERE code=?", (code,)
        ).fetchone()
        if not row:
            return None
        connection.execute("UPDATE pending_payments SET status=? WHERE code=?", (status, code))
        connection.commit()
        return {"tg_id": row[0], "plan": row[1], "method": row[2], "status": row[3]}
    finally:
        connection.close()


def _pending_delete(code: str) -> None:
    import sqlite3

    connection = sqlite3.connect(DB_PATH)
    try:
        _ensure_extra_columns(connection)
        connection.execute("DELETE FROM pending_payments WHERE code=?", (code,))
        connection.commit()
    finally:
        connection.close()


async def on_method(callback: CallbackQuery) -> None:
    from app.services import dapayments as da

    _, method, plan_id = callback.data.split(":", 2)
    info = PLANS.get(plan_id)
    if not info:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    if await _refuse_trial_payment(callback, plan_id):
        return
    settings = get_settings()
    if method == "da":
        if da.is_configured() and settings.da_donate_url:
            code = da.create_payment_code(callback.from_user.id, plan_id)
            _pending_insert(code, callback.from_user.id, plan_id, "da")
            await _safe_edit(
                callback,
                f"💳 Оплата подписки <b>{info['title']}</b>: <b>{info['price_rub']} ₽</b>\n\n"
                f"1. Перейдите по ссылке: {settings.da_donate_url}\n"
                f"2. Задонатьте <b>{info['price_rub']} ₽</b> (или больше)\n"
                f"3. В сообщении к донату укажите код: <code>{code}</code>\n\n"
                "Ключ придёт сюда автоматически в течение пары минут после доната.",
                reply_markup=back_menu_keyboard(),
            )
            await callback.answer()
            return
        await callback.answer("DonationAlerts пока не подключён, выберите другой способ.", show_alert=True)
        return
    if method == "crypto":
        wallet = (settings.crypto_wallet or "").strip()
        if not wallet:
            await callback.answer("Крипта временно недоступна, выберите другой способ.", show_alert=True)
            return
        code = "KLP-" + "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        _pending_insert(code, callback.from_user.id, plan_id, "crypto")
        await _safe_edit(
            callback,
            f"🪙 Оплата криптой: <b>{info['price_rub']} ₽</b> в эквиваленте.\n\n"
            f"Кошелёк:\n<code>{wallet}</code>\n\n"
            f"Ваш код платежа: <code>{code}</code>\n"
            "Укажите код в комментарии/ника при переводе, если есть такая возможность. "
            "Затем нажмите кнопку ниже — заявка уйдёт администратору, ключ придёт сюда после подтверждения.",
            reply_markup=paid_keyboard(code),
        )
        await callback.answer()
        return
    if method == "dm":
        code = "KLP-" + "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        _pending_insert(code, callback.from_user.id, plan_id, "dm")
        await _safe_edit(
            callback,
            f"💬 Оплата через личные сообщения: <b>{info['price_rub']} ₽</b>.\n\n"
            f"1. Напишите {SUPPORT_URL} с кодом: <code>{code}</code>\n"
            "2. Договоритесь об оплате.\n"
            "3. Нажмите кнопку ниже — администратор подтвердит и выдаст ключ.",
            reply_markup=paid_keyboard(code),
        )
        await callback.answer()
        return
    await callback.answer("Неизвестный способ.", show_alert=True)


async def on_paid(callback: CallbackQuery) -> None:
    code = callback.data.split(":", 1)[1]
    pending = _pending_mark(code, "awaiting")
    if not pending:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    await _safe_edit(
        callback,
        "📨 Заявка отправлена! Администратор проверит оплату и выдаст ключ сюда. "
        "Обычно это занимает до пары часов.",
    )
    await callback.answer()
    logger.info("manual payment awaiting: %s (%s)", code, pending.get("method"))


async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user):
        await message.answer("Нет доступа.")
        return
    import sqlite3

    connection = sqlite3.connect(DB_PATH)
    try:
        _ensure_extra_columns(connection)
        subs = connection.execute("SELECT COUNT(*), SUM(plan='month') FROM subscribers").fetchone()
        pend = connection.execute(
            "SELECT code, tg_id, plan, method, created_at FROM pending_payments WHERE status='awaiting' ORDER BY created_at"
        ).fetchall()
    finally:
        connection.close()
    text = (
        "🛠 <b>Админка</b>\n"
        f"Подписчиков всего: <b>{subs[0] or 0}</b>\n"
        f"Ожидают подтверждения: <b>{len(pend)}</b>\n"
    )
    buttons = []
    for code, tg_id, plan, method, _created in pend[:10]:
        text += f"\n<code>{code}</code> · {plan} · {method} · tg:{tg_id}"
        buttons.append([
            InlineKeyboardButton(text=f"✅ {code}", callback_data=f"adm:ok:{code}"),
            InlineKeyboardButton(text="❌", callback_data=f"adm:no:{code}"),
        ])
    if not pend:
        text += "\nОчередь пуста."
    await message.answer(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None,
    )


async def on_admin_decision(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    _, decision, code = callback.data.split(":", 2)
    pending = _pending_mark(code, "done" if decision == "ok" else "rejected")
    if not pending:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return
    if decision == "ok":
        if pending["plan"] == "trial" and not claim_trial(pending["tg_id"]):
            _pending_delete(code)
            await callback.answer("У этого аккаунта триал уже был — ключ не выдан.", show_alert=True)
            return
        try:
            issued = issue_license(pending["tg_id"], pending["plan"])
        except (ValueError, RuntimeError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        save_subscriber(pending["tg_id"], pending["plan"], issued["key"], issued["exp"])
        _pending_delete(code)
        try:
            await callback.bot.send_message(
                pending["tg_id"],
                "✅ Оплата подтверждена!\n\n"
                f"🔑 Ваш ключ:\n<code>{issued['key']}</code>\n\n"
                "Вставьте ключ в приложении: Подписка → Активировать.",
                parse_mode="HTML",
            )
        except Exception as error:
            logger.warning("notify failed: %s", error)
        await callback.answer("Ключ выдан.")
    else:
        _pending_delete(code)
        try:
            await callback.bot.send_message(
                pending["tg_id"],
                "😕 Оплата не подтверждена. Если это ошибка — напишите в поддержку: @LiveForWork1",
            )
        except Exception as error:
            logger.warning("notify failed: %s", error)
        await callback.answer("Заявка отклонена.")


async def on_pay(callback: CallbackQuery) -> None:
    from app.services import dapayments as da

    plan_id = callback.data.split(":", 1)[1]
    info = PLANS[plan_id]
    if await _refuse_trial_payment(callback, plan_id):
        return
    settings = get_settings()
    if da.is_configured() and settings.da_donate_url:
        code = da.create_payment_code(callback.from_user.id, plan_id)
        await _safe_edit(
            callback,
            f"💳 Оплата подписки <b>{info['title']}</b>: <b>{info['price_rub']} ₽</b>\n\n"
            f"1. Перейдите по ссылке: {settings.da_donate_url}\n"
            f"2. Задонатьте <b>{info['price_rub']} ₽</b> (или больше)\n"
            f"3. В сообщении к донату укажите код: <code>{code}</code>\n\n"
            "Ключ придёт сюда автоматически в течение пары минут после доната.",
            reply_markup=back_menu_keyboard(),
        )
        await callback.answer()
        return
    invoice_id = f"TEST-{callback.from_user.id}-{int(time.time())}"
    # STUB: real acquiring goes here (provider invoice + webhook callback).
    await _safe_edit(
        callback,
        f"🧾 Счёт <code>{invoice_id}</code>: <b>{info['price_rub']} ₽</b>\n"
        "⚠️ ТЕСТОВЫЙ РЕЖИМ: оплата — заглушка, деньги не списываются.\n"
        "Эквайринг будет подключён позже.",
        reply_markup=confirm_keyboard(plan_id),
    )
    await callback.answer()


async def on_confirm(callback: CallbackQuery) -> None:
    plan_id = callback.data.split(":", 1)[1]
    if await _refuse_trial_payment(callback, plan_id):
        return
    try:
        issued = issue_license(callback.from_user.id, plan_id)
    except (ValueError, RuntimeError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    with _db() as db:
        db.execute(
            "INSERT OR REPLACE INTO subscribers VALUES (?, ?, ?, ?)",
            (callback.from_user.id, plan_id, issued["key"], issued["exp"]),
        )
        db.commit()
    days = PLANS[plan_id]["days"]
    await _safe_edit(
        callback,
        "✅ Оплата (тестовая) принята!\n\n"
        f"🔑 Ваш ключ:\n<code>{issued['key']}</code>\n\n"
        f"Тариф {PLANS[plan_id]['title']} активен.\n"
        "Вставьте ключ в приложении: Подписка → Активировать.",
    )
    await callback.answer()
    logger.info("issued %s license to tg_id=%s", plan_id, callback.from_user.id)


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в .env (токен от @BotFather).")
    if not settings.license_secret:
        raise SystemExit("LICENSE_SECRET не задан в .env (openssl rand -hex 32).")
    bot, dispatcher = create_bot()
    logger.info("bot polling started")
    await dispatcher.start_polling(bot)


def create_bot():
    """Shared Bot + Dispatcher for polling (local) and webhooks (hosting)."""
    from aiogram import Bot, Dispatcher

    settings = get_settings()
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.message.register(cmd_start, CommandStart())
    dispatcher.message.register(cmd_status, Command("status"))
    dispatcher.message.register(cmd_mykey, Command("mykey"))
    dispatcher.message.register(cmd_myid, Command("myid"))
    dispatcher.message.register(cmd_admin, Command("admin"))
    dispatcher.message.register(
        on_menu_text,
        F.text.startswith("🛟") | F.text.startswith("ℹ️") | F.text.startswith("💳") | F.text.startswith("🛒") | F.text.startswith("📥"),
    )
    dispatcher.callback_query.register(on_menu, F.data == "menu")
    dispatcher.callback_query.register(on_about, F.data == "about")
    dispatcher.callback_query.register(on_plans, F.data == "plans")
    dispatcher.callback_query.register(on_buy, F.data == "buy")
    dispatcher.callback_query.register(on_plan, F.data.startswith("plan:"))
    dispatcher.callback_query.register(on_method, F.data.startswith("method:"))
    dispatcher.callback_query.register(on_paid, F.data.startswith("paid:"))
    dispatcher.callback_query.register(on_admin_decision, F.data.startswith("adm:"))
    dispatcher.callback_query.register(on_pay, F.data.startswith("pay:"))
    dispatcher.callback_query.register(on_confirm, F.data.startswith("confirm:"))
    dispatcher.errors.register(_on_aiogram_error)
    return bot, dispatcher


if __name__ == "__main__":
    asyncio.run(main())
