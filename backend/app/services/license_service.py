"""License keys for KLIPANI subscriptions.

Two formats (payload is always {"tg": <telegram_id>, "plan": <plan_id>, "exp": <unix_ts>}):

- KLIP2-<payload>.<ed25519_sig> — current. Signed by the bot's private key,
  verified by the embedded public key. Fully offline, nothing secret ships
  inside the desktop app.
- KLIP-<payload>.<hmac_sig> — legacy. Verified only where LICENSE_SECRET is
  configured (bot/server); the desktop app never sees that secret.

Offline-verifiable: the desktop app checks signature + expiry locally,
no central server needed. The Telegram bot issues keys after payment.
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

# Plans without payment: full features, but every render carries the service watermark.
FREE_PLANS = {"trial"}

PAID_PLANS = {plan for plan in PLANS if plan not in FREE_PLANS}

PREFIX = "KLIP-"
PREFIX2 = "KLIP2-"

# Ed25519 public key matching the bot's LICENSE_ED25519_PRIVATE.
# Public by design: it verifies signatures but cannot create them.
ED25519_PUBLIC_HEX = "0269f306e70d9ed641b2affa451efc6e85b954027df5a35552135908436f959d"


def _secret() -> bytes:
    secret = get_settings().license_secret
    if not secret:
        raise RuntimeError("LICENSE_SECRET не задан в .env (сгенерируйте: openssl rand -hex 32).")
    return secret.encode()


def _ed_private() -> Optional[bytes]:
    raw = (get_settings().license_ed25519_private or "").strip()
    if not raw:
        return None
    try:
        key = bytes.fromhex(raw)
    except ValueError as error:
        raise RuntimeError("LICENSE_ED25519_PRIVATE — не hex.") from error
    if len(key) != 32:
        raise RuntimeError("LICENSE_ED25519_PRIVATE — нужно 32 байта в hex.")
    return key


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _check_payload(payload: object) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    if payload.get("plan") not in PLANS:
        return None
    try:
        exp = int(payload.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if exp <= int(time.time()):
        return None
    try:
        telegram_id = int(payload.get("tg", 0))
    except (TypeError, ValueError):
        return None
    return {"telegram_id": telegram_id, "plan": payload["plan"], "exp": exp}


def _split(key: str, prefix: str) -> Optional[tuple]:
    try:
        if not key.startswith(prefix):
            return None
        raw_b64, sig_b64 = key[len(prefix):].split(".", 1)
        return _b64decode(raw_b64), _b64decode(sig_b64)
    except (ValueError, AttributeError):
        return None


def _verify_ed25519(key: str) -> Optional[dict]:
    parts = _split(key, PREFIX2)
    if not parts:
        return None
    raw, sig = parts
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(ED25519_PUBLIC_HEX))
        public.verify(sig, raw)
    except Exception:  # noqa: BLE001 — any failure means "not our signature"
        return None
    try:
        return _check_payload(json.loads(raw.decode()))
    except (ValueError, AttributeError):
        return None


def _verify_hmac(key: str) -> Optional[dict]:
    """Legacy keys. Needs LICENSE_SECRET, so it only works on bot/server —
    the desktop app raises RuntimeError here and treats the key as unlicensed
    unless it also verifies as KLIP2."""
    parts = _split(key, PREFIX)
    if not parts:
        return None
    raw, sig = parts
    if not hmac.compare_digest(sig, hmac.new(_secret(), raw, hashlib.sha256).digest()):
        return None
    try:
        return _check_payload(json.loads(raw.decode()))
    except (ValueError, AttributeError):
        return None


def issue_license(telegram_id: int, plan_id: str, exp: Optional[int] = None) -> dict:
    """Issue a key. Prefers Ed25519 (offline-verifiable in the app); falls back
    to HMAC when no Ed25519 private key is configured (local dev without it)."""
    if plan_id not in PLANS:
        raise ValueError("Неизвестный тариф.")
    private = _ed_private()
    payload = {
        "tg": int(telegram_id),
        "plan": plan_id,
        "exp": exp if exp is not None else int(time.time()) + PLANS[plan_id]["days"] * 86400,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    if private is not None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sig = Ed25519PrivateKey.from_private_bytes(private).sign(raw)
        return {"key": f"{PREFIX2}{_b64encode(raw)}.{_b64encode(sig)}", **payload}
    sig = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return {"key": f"{PREFIX}{_b64encode(raw)}.{_b64encode(sig)}", **payload}


def verify_license(key: str) -> Optional[dict]:
    """Return payload dict if valid and not expired, else None."""
    if not key:
        return None
    found = _verify_ed25519(key)
    if found:
        return found
    try:
        return _verify_hmac(key)
    except RuntimeError:
        return None  # no LICENSE_SECRET here (desktop app): HMAC keys don't verify


def key_format(key: str) -> Optional[str]:
    """'ed25519' / 'hmac' / None — used by the bot to convert legacy keys."""
    if key.startswith(PREFIX2):
        return "ed25519"
    if key.startswith(PREFIX):
        return "hmac"
    return None


def current_state() -> dict:
    """Subscription state of this machine: {active, plan, exp, free, days_left}.

    `free` means "no paid plan": trial, expired or no key at all. Paid plans get
    the app without the service watermark.
    """
    from .app_settings import get_private

    try:
        stored = get_private("license_key")
    except Exception:  # noqa: BLE001 — a broken settings DB must not kill the API
        stored = ""
    try:
        info = verify_license(stored) if stored else None
    except RuntimeError:
        info = None  # no LICENSE_SECRET configured: treat as unlicensed
    if not info:
        return {"active": False, "plan": None, "exp": None, "free": True, "days_left": 0}
    days_left = max(0, (info["exp"] - int(time.time())) // 86400)
    return {
        "active": True,
        "plan": info["plan"],
        "exp": info["exp"],
        "free": info["plan"] in FREE_PLANS,
        "days_left": days_left,
    }
