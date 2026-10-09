"""Persistent trial-claim storage.

The bot runs on Render's free tier, where the local disk (bot.db) is wiped on
every deploy and restart. A "once per Telegram account" rule kept in sqlite is
therefore really "once per deploy" — users re-claim free trials daily.

Fix: trial claims live in a secret GitHub Gist (free, no new accounts, API via
a classic personal access token). sqlite stays as the local-dev store and as a
fallback when the API is unreachable or unconfigured.

Env (Render + local .env):
    GITHUB_TOKEN   classic PAT with the `gist` scope only
    GIST_ID        id of the secret gist holding trial_claims.json
                   (auto-created on first use when missing — its id is logged
                   loudly, copy it into env afterwards)

File format: {"<tg_id>": <claimed_at unix>, ...}
Writes are read-modify-write with retries; simultaneous claims are rare and
sqlite still guards the same-process case.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

GIST_FILENAME = "trial_claims.json"
CACHE_TTL = 60.0

_cache = {"at": 0.0, "claims": None}


def gist_config():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    gist_id = os.environ.get("GIST_ID", "").strip()
    if not token:
        return None
    return token, gist_id or None


def _api(method: str, path: str, token: str, payload=None, timeout: float = 10.0):
    """One GitHub API call. Returns (ok, parsed json)."""
    try:
        import httpx

        response = httpx.request(
            method,
            f"https://api.github.com{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=payload,
            timeout=timeout,
        )
        if response.status_code in (200, 201):
            return True, response.json()
        logger.warning("gist api %s %s -> %s", method, path, response.status_code)
        return False, None
    except Exception as error:  # noqa: BLE001 — remote is best-effort, sqlite covers
        logger.warning("gist api unavailable: %s", error)
        return False, None


def _read_claims(token: str, gist_id: str, force: bool = False):
    """Full {tg_id: claimed_at} dict, or None on error. Memory-cached briefly."""
    now = time.time()
    if not force and _cache["claims"] is not None and now - _cache["at"] < CACHE_TTL:
        return dict(_cache["claims"])
    ok, gist = _api("GET", f"/gists/{gist_id}", token)
    if not ok or not gist:
        return None
    try:
        import json as _json

        raw = (gist.get("files", {}).get(GIST_FILENAME, {}) or {}).get("content", "{}")
        claims = _json.loads(raw or "{}")
        claims = {str(key): int(value) for key, value in claims.items()}
    except (ValueError, TypeError, AttributeError) as error:
        logger.warning("gist claims unparsable: %s", error)
        return None
    _cache["at"] = now
    _cache["claims"] = claims
    return dict(claims)


def _write_claims(token: str, gist_id: str, claims: dict) -> bool:
    import json as _json

    ok, _ = _api(
        "PATCH", f"/gists/{gist_id}", token,
        {"files": {GIST_FILENAME: {"content": _json.dumps(claims, separators=(",", ":"))}}},
    )
    if ok:
        _cache["at"] = time.time()
        _cache["claims"] = dict(claims)
    return ok


def _ensure_gist(token: str):
    """Create the secret gist on first use. Returns its id, or None."""
    import json as _json

    ok, gist = _api(
        "POST", "/gists", token,
        {"description": "KLIPANI trial claims (bot-managed, do not edit)",
         "public": False,
         "files": {GIST_FILENAME: {"content": _json.dumps({})}}},
    )
    if not ok or not gist:
        return None
    gist_id = gist.get("id")
    logger.warning("created trial-claims gist %s — copy it into GIST_ID env", gist_id)
    return gist_id


def remote_is_claimed(tg_id: int):
    """True/False from persistent store, None when off or errored."""
    config = gist_config()
    if config is None:
        return None
    _token, gist_id = config
    if not gist_id:
        return None
    claims = _read_claims(_token, gist_id)
    if claims is None:
        return None
    return str(int(tg_id)) in claims


def remote_claim(tg_id: int):
    """Record the claim persistently. True = first time, False = already used,
    None when off or errored (caller falls back to sqlite)."""
    config = gist_config()
    if config is None:
        return None
    token, gist_id = config
    if not gist_id:
        gist_id = _ensure_gist(token)
        if not gist_id:
            return None
        os.environ["GIST_ID"] = gist_id  # survives until next restart/redeploy
    key = str(int(tg_id))
    for _ in range(3):  # optimistic retries against concurrent writers
        claims = _read_claims(token, gist_id, force=True)
        if claims is None:
            return None
        if key in claims:
            return False
        claims[key] = int(time.time())
        if _write_claims(token, gist_id, claims):
            return True
    logger.warning("gist claim lost a write race for tg_id=%s", tg_id)
    return None
