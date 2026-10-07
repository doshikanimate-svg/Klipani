"""DonationAlerts payments for KLIPANI subscriptions.

Flow: bot shows the user a payment code (KLP-XXXXXX) -> user donates any
amount >= plan price with the code in the donation message -> background
poller matches the donation -> license is issued -> bot notifies the user.

No acquiring integration needed: DonationAlerts handles the money.
"""

import json
import logging
import random
import re
import string
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

from ..config import get_settings

logger = logging.getLogger("klipani-dapay")

OAUTH_AUTHORIZE = "https://www.donationalerts.com/oauth/authorize"
OAUTH_TOKEN = "https://www.donationalerts.com/oauth/token"
DONATIONS_URL = "https://www.donationalerts.com/api/v1/alerts/donations"
SCOPES = "oauth-user-show oauth-donation-index"

CODE_RE = re.compile(r"\bKLP-([A-Z0-9]{6})\b")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

POLL_INTERVAL = 60
_processed_ids: set = set()
_poller_started = False


def _resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else get_settings().storage.parent / candidate


def _token_path() -> Path:
    return _resolve(get_settings().da_token_path)


def _redirect_uri() -> str:
    settings = get_settings()
    if settings.public_url:
        return settings.public_url.rstrip("/") + "/api/payments/donationalerts/callback"
    return f"http://{settings.backend_host}:{settings.backend_port}/api/payments/donationalerts/callback"


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.da_client_id and settings.da_client_secret)


def is_connected() -> bool:
    return _access_token() is not None


def auth_url() -> str:
    settings = get_settings()
    if not is_configured():
        raise RuntimeError(
            "Нет ключей DonationAlerts. Создайте OAuth-приложение на donationalerts.com "
            "и задайте DA_CLIENT_ID/DA_CLIENT_SECRET в .env (см. README)."
        )
    query = urlencode({
        "client_id": settings.da_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
    })
    return f"{OAUTH_AUTHORIZE}?{query}"


def exchange_code(code: str) -> None:
    settings = get_settings()
    response = requests.post(OAUTH_TOKEN, data={
        "grant_type": "authorization_code",
        "client_id": settings.da_client_id,
        "client_secret": settings.da_client_secret,
        "redirect_uri": _redirect_uri(),
        "code": code,
    }, timeout=30)
    response.raise_for_status()
    _save_token(response.json())


def _save_token(data: dict) -> None:
    token_path = _token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + int(data.get("expires_in", 3600)) - 300,
    }
    token_path.write_text(json.dumps(record), encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:
        pass


def _load_token() -> Optional[dict]:
    token_path = _token_path()
    if not token_path.exists():
        return None
    try:
        return json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _access_token() -> Optional[str]:
    record = _load_token()
    if not record or not record.get("access_token"):
        return None
    if time.time() < record.get("expires_at", 0):
        return record["access_token"]
    if not record.get("refresh_token"):
        return None
    try:
        settings = get_settings()
        response = requests.post(OAUTH_TOKEN, data={
            "grant_type": "refresh_token",
            "client_id": settings.da_client_id,
            "client_secret": settings.da_client_secret,
            "refresh_token": record["refresh_token"],
        }, timeout=30)
        response.raise_for_status()
        data = response.json()
        data.setdefault("refresh_token", record["refresh_token"])
        _save_token(data)
        return data["access_token"]
    except Exception as error:
        logger.warning("DA token refresh failed: %s", error)
        return None


def _bot_db() -> Path:
    return get_settings().storage.parent / "bot" / "bot.db"


def _ensure_pending_table(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS pending_payments (
             code TEXT PRIMARY KEY, tg_id INTEGER NOT NULL,
             plan TEXT NOT NULL, created_at INTEGER NOT NULL
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS subscribers (
             tg_id INTEGER PRIMARY KEY, plan TEXT NOT NULL,
             license_key TEXT NOT NULL, exp INTEGER NOT NULL
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS processed_donations (
             donation_id INTEGER PRIMARY KEY
           )"""
    )


def create_payment_code(tg_id: int, plan: str) -> str:
    """Create a pending payment, return the code the user must put in the donation message."""
    import sqlite3

    code = "KLP-" + "".join(random.choice(_CODE_ALPHABET) for _ in range(6))
    connection = sqlite3.connect(_bot_db())
    try:
        _ensure_pending_table(connection)
        try:
            connection.execute("ALTER TABLE pending_payments ADD COLUMN method TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        try:
            connection.execute("ALTER TABLE pending_payments ADD COLUMN status TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        connection.execute(
            "INSERT OR REPLACE INTO pending_payments(code, tg_id, plan, created_at, method, status)"
            " VALUES (?, ?, ?, ?, 'da', 'new')",
            (code, int(tg_id), plan, int(time.time())),
        )
        connection.commit()
    finally:
        connection.close()
    return code


def extract_code(message: Optional[str]) -> Optional[str]:
    if not message:
        return None
    match = CODE_RE.search(message.upper())
    return match.group(0) if match else None


def fetch_donations(page: int = 1) -> list:
    token = _access_token()
    if not token:
        raise RuntimeError("DonationAlerts не подключён.")
    response = requests.get(
        DONATIONS_URL, params={"page": page},
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def _notify_telegram(tg_id: int, text: str) -> None:
    token = get_settings().telegram_bot_token
    if not token:
        logger.warning("no bot token, cannot notify tg_id=%s", tg_id)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": tg_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as error:
        logger.warning("telegram notify failed: %s", error)


def process_donations(donations: list) -> int:
    """Match donations against pending payments. Returns number of licenses issued."""
    from .license_service import PLANS, issue_license

    import sqlite3

    issued = 0
    connection = sqlite3.connect(_bot_db())
    try:
        _ensure_pending_table(connection)
        for donation in donations:
            donation_id = donation.get("id")
            if donation_id is None:
                continue
            exists = connection.execute(
                "SELECT 1 FROM processed_donations WHERE donation_id=?", (donation_id,)
            ).fetchone()
            if exists:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO processed_donations VALUES (?)", (donation_id,)
            )
            code = extract_code(donation.get("message") or donation.get("message_type") or "")
            if not code:
                continue
            pending = connection.execute(
                "SELECT tg_id, plan FROM pending_payments WHERE code=?", (code,)
            ).fetchone()
            if not pending:
                continue
            tg_id, plan = pending
            if plan == "trial":
                # Trial is free and once-per-account: it is claimed with one button
                # in «Тарифы», never through a donation (its price is 0, so any
                # donation text with the code would otherwise auto-issue it).
                logger.info("donation %s ignored: trial plan is not payable", donation_id)
                connection.execute("DELETE FROM pending_payments WHERE code=?", (code,))
                connection.commit()
                _notify_telegram(
                    tg_id,
                    "😕 Пробный тариф бесплатный и выдаётся <b>1 раз на аккаунт</b> — "
                    "берите его кнопкой в «Тарифах», платить ничего не нужно.",
                )
                continue
            try:
                amount = float(donation.get("amount", 0) or 0)
            except (TypeError, ValueError):
                continue
            price = PLANS.get(plan, {}).get("price_rub", 0)
            currency = (donation.get("currency") or "").upper()
            if currency != "RUB" or amount < price:
                logger.info("donation %s ignored: %s %s < %s", donation_id, amount, currency, price)
                continue
            try:
                result = issue_license(tg_id, plan)
            except (ValueError, RuntimeError) as error:
                logger.warning("license issue failed: %s", error)
                continue
            connection.execute(
                "INSERT OR REPLACE INTO subscribers VALUES (?, ?, ?, ?)",
                (tg_id, plan, result["key"], result["exp"]),
            )
            connection.execute("DELETE FROM pending_payments WHERE code=?", (code,))
            connection.commit()
            issued += 1
            logger.info("issued %s license to tg_id=%s (donation %s)", plan, tg_id, donation_id)
            _notify_telegram(
                tg_id,
                "✅ Оплата получена, спасибо!\n\n"
                f"🔑 Ваш ключ:\n<code>{result['key']}</code>\n\n"
                "Вставьте его в приложении: Подписка → Активировать.",
            )
        connection.commit()
    finally:
        connection.close()
    return issued


def poll_once() -> int:
    try:
        donations = fetch_donations(page=1)
    except Exception as error:
        logger.warning("donation poll failed: %s", error)
        return 0
    try:
        return process_donations(donations)
    except Exception as error:
        logger.warning("donation processing failed: %s", error)
        return 0


def _poller_loop() -> None:
    while True:
        try:
            if is_configured() and _load_token():
                poll_once()
        except Exception as error:  # noqa: BLE001 — poller must never die
            logger.warning("poller error: %s", error)
        time.sleep(POLL_INTERVAL)


def start_poller() -> None:
    global _poller_started
    if _poller_started:
        return
    _poller_started = True
    thread = threading.Thread(target=_poller_loop, daemon=True, name="da-poller")
    thread.start()
    logger.info("donation poller started (every %ss)", POLL_INTERVAL)
