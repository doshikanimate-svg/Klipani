"""License keys for KLIPANI subscriptions.

Key format: KLIP-<base64url(payload)>.<base64url(hmac_sha256(payload, secret))>
payload = {"tg": <telegram_id>, "plan": <plan_id>, "exp": <unix_ts>}.

Offline-verifiable: the desktop app checks signature + expiry locally,
no central server needed. The Telegram bot issues keys after (stub) payment.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from ..config import get_settings

PLANS = {
    "trial": {"days": 3, "price_rub": 0, "title": "Пробная · 3 дня"},
    "month": {"days": 30, "price_rub": 2490, "title": "Месяц · 2490 ₽"},
}

PREFIX = "KLIP-"


def _secret() -> bytes:
    secret = get_settings().license_secret
    if not secret:
        raise RuntimeError("LICENSE_SECRET не задан в .env (сгенерируйте: openssl rand -hex 32).")
    return secret.encode()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_license(telegram_id: int, plan_id: str) -> dict:
    if plan_id not in PLANS:
        raise ValueError("Неизвестный тариф.")
    payload = {
        "tg": int(telegram_id),
        "plan": plan_id,
        "exp": int(time.time()) + PLANS[plan_id]["days"] * 86400,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return {"key": f"{PREFIX}{_b64encode(raw)}.{_b64encode(sig)}", **payload}


def verify_license(key: str) -> Optional[dict]:
    """Return payload dict if valid and not expired, else None."""
    try:
        if not key.startswith(PREFIX):
            return None
        raw_b64, sig_b64 = key[len(PREFIX):].split(".", 1)
        raw = _b64decode(raw_b64)
        sig = _b64decode(sig_b64)
        if not hmac.compare_digest(sig, hmac.new(_secret(), raw, hashlib.sha256).digest()):
            return None
        payload = json.loads(raw.decode())
        if not isinstance(payload, dict):
            return None
        if payload.get("plan") not in PLANS:
            return None
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None
        return {"telegram_id": int(payload.get("tg", 0)), "plan": payload["plan"], "exp": int(payload["exp"])}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
