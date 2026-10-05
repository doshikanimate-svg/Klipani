"""KLIPANI subscription bot (aiogram 3, long polling).

Flow: /start -> plans -> stub checkout ("Я оплатил (тест)") -> license key.
Real acquiring plugs into `create_stub_invoice()` later: replace the fake
confirm step with a real payment provider callback, keep key issuance as is.
"""

import asyncio
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

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
    return connection


SUPPORT_URL = "https://t.me/LiveForWork1"
CHANNEL_URL = "https://t.me/Klipani_of"

ABOUT_TEXT = (
    "📱 <b>KLIPANI</b> — студия вертикальных клипов.\n\n"
    "Загружаете запись стрима — получаете готовые ролики для TikTok, Shorts и Reels: "
    "умный поиск моментов, субтитры-караоке, монтаж, водяной знак вашего твича/ютуба "
    "и публикация в один клик.\n\n"
    "Новости и обновления: @Klipani_of"
)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛟 Поддержка", url=SUPPORT_URL)],
        [InlineKeyboardButton(text="ℹ️ О проекте", callback_data="about")],
        [InlineKeyboardButton(text="💳 Тарифы", callback_data="plans")],
        [InlineKeyboardButton(text="🛒 Купить подписку", callback_data="buy")],
    ])


def back_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← В меню", callback_data="menu")],
    ])


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
        "для приложения. Если что-то непонятно — загляните в «О проекте» или напишите в поддержку.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def on_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Главное меню <b>KLIPANI</b> — чем помочь?",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


async def on_about(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        ABOUT_TEXT,
        parse_mode="HTML",
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


async def cmd_mykey(message: Message) -> None:
    with _db() as db:
        row = db.execute(
            "SELECT license_key, exp FROM subscribers WHERE tg_id=?", (message.from_user.id,)
        ).fetchone()
    if not row or row[1] <= time.time():
        await message.answer("Активного ключа нет. Выберите тариф: /start")
        return
    await message.answer(
        f"🔑 Ваш ключ:\n<code>{row[0]}</code>\n\nВставьте его в приложении: Подписка → Активировать.",
        parse_mode="HTML",
    )


async def on_plans(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "💳 <b>Тарифы KLIPANI:</b>\n\n" + "\n".join(
            f"• <b>{info['title']}</b> — {info['days']} дней"
            for info in PLANS.values()
        ) + "\n\nНажмите на тариф, чтобы оформить.",
        parse_mode="HTML",
        reply_markup=plans_keyboard(),
    )
    await callback.answer()


async def on_buy(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🛒 <b>Покупка подписки:</b> выберите тариф ниже — после тестовой оплаты "
        "бот сразу выдаст лицензионный ключ.",
        parse_mode="HTML",
        reply_markup=plans_keyboard(),
    )
    await callback.answer()


async def on_plan(callback: CallbackQuery) -> None:
    plan_id = callback.data.split(":", 1)[1]
    info = PLANS.get(plan_id)
    if not info:
        await callback.answer("Неизвестный тариф.", show_alert=True)
        return
    await callback.message.edit_text(
        f"📦 <b>{info['title']}</b>\nСрок: {info['days']} дней.\n\n"
        "Нажмите «Оплатить», затем подтвердите тестовую оплату.",
        parse_mode="HTML",
        reply_markup=pay_keyboard(plan_id),
    )
    await callback.answer()


async def on_pay(callback: CallbackQuery) -> None:
    from app.services import dapayments as da

    plan_id = callback.data.split(":", 1)[1]
    info = PLANS[plan_id]
    settings = get_settings()
    if da.is_configured() and settings.da_donate_url:
        code = da.create_payment_code(callback.from_user.id, plan_id)
        await callback.message.edit_text(
            f"💳 Оплата подписки <b>{info['title']}</b>: <b>{info['price_rub']} ₽</b>\n\n"
            f"1. Перейдите по ссылке: {settings.da_donate_url}\n"
            f"2. Задонатьте <b>{info['price_rub']} ₽</b> (или больше)\n"
            f"3. В сообщении к донату укажите код: <code>{code}</code>\n\n"
            "Ключ придёт сюда автоматически в течение пары минут после доната.",
            parse_mode="HTML",
            reply_markup=back_menu_keyboard(),
        )
        await callback.answer()
        return
    invoice_id = f"TEST-{callback.from_user.id}-{int(time.time())}"
    # STUB: real acquiring goes here (provider invoice + webhook callback).
    await callback.message.edit_text(
        f"🧾 Счёт <code>{invoice_id}</code>: <b>{info['price_rub']} ₽</b>\n"
        "⚠️ ТЕСТОВЫЙ РЕЖИМ: оплата — заглушка, деньги не списываются.\n"
        "Эквайринг будет подключён позже.",
        parse_mode="HTML",
        reply_markup=confirm_keyboard(plan_id),
    )
    await callback.answer()


async def on_confirm(callback: CallbackQuery) -> None:
    plan_id = callback.data.split(":", 1)[1]
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
    await callback.message.edit_text(
        "✅ Оплата (тестовая) принята!\n\n"
        f"🔑 Ваш ключ:\n<code>{issued['key']}</code>\n\n"
        f"Тариф {PLANS[plan_id]['title']} активен.\n"
        "Вставьте ключ в приложении: Подписка → Активировать.",
        parse_mode="HTML",
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
    dispatcher.callback_query.register(on_menu, F.data == "menu")
    dispatcher.callback_query.register(on_about, F.data == "about")
    dispatcher.callback_query.register(on_plans, F.data == "plans")
    dispatcher.callback_query.register(on_buy, F.data == "buy")
    dispatcher.callback_query.register(on_plan, F.data.startswith("plan:"))
    dispatcher.callback_query.register(on_pay, F.data.startswith("pay:"))
    dispatcher.callback_query.register(on_confirm, F.data.startswith("confirm:"))
    return bot, dispatcher


if __name__ == "__main__":
    asyncio.run(main())
