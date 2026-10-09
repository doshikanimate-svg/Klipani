"""Persistent trial-claim storage.

The bot runs on Render's free tier, where the local disk (bot.db) is wiped on
every deploy and restart. A "once per Telegram account" rule kept in sqlite is
therefore really "once per deploy" — users re-claim free trials daily.

Fix: trial claims live in Upstash Redis (free tier, plain HTTPS, no new
dependencies — httpx is already installed). sqlite stays as the local-dev
store and as a fallback when Redis is unreachable or unconfigured.

Env (Render + local .env):
    UPSTASH_REDIS_REST_URL   https://...upstash.io
    UPSTASH_REDIS_REST_TOKEN <read-write token>

Key:    klipani:trial:{tg_id}  -> "1"
TTL:    10 years (effectively forever; keeps the keyspace honest).
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

KEY_PREFIX = "klipani:trial:"
TTL_SECONDS = 10 * 365 * 24 * 3600


def redis_config() -> Optional[tuple]:
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not url or not token:
        return None
    return url, token


def _redis(*parts: str, timeout: float = 5.0) -> tuple:
    """One Upstash REST call. Returns (ok, result): ok=False on any error."""
    config = redis_config()
    if config is None:
        return False, None
    url, token = config
    try:
        import httpx

        response = httpx.post(
            f"{url}/{'/'.join(parts)}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        response.raise_for_status()
        return True, response.json().get("result")
    except Exception as error:  # noqa: BLE001 — Redis is best-effort, sqlite covers
        logger.warning("trial redis unavailable: %s", error)
        return False, None


def redis_is_claimed(tg_id: int) -> Optional[bool]:
    """True/False from persistent store, None when Redis is off or errored."""
    ok, result = _redis("GET", f"{KEY_PREFIX}{int(tg_id)}")
    if not ok:
        return None
    return result is not None


def redis_claim(tg_id: int) -> Optional[bool]:
    """Atomic first-claim via SET NX. True = first time, False = already used,
    None when Redis is off or errored (caller falls back to sqlite)."""
    ok, result = _redis("SET", f"{KEY_PREFIX}{int(tg_id)}", "1", "NX", "EX", str(TTL_SECONDS))
    if not ok:
        return None
    return result == "OK"
