import json
import logging
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from ...config import get_settings
from .base import LLMProvider

logger = logging.getLogger(__name__)


HIGHLIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "number"}, "end_time": {"type": "number"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "category": {"type": "string"}, "reason": {"type": "string"},
                    "transcript_excerpt": {"type": "string"},
                },
                "required": ["start_time", "end_time", "score", "category", "reason", "transcript_excerpt"],
            },
        }
    },
    "required": ["highlights"],
}

ALLOWED_CATEGORIES = {"FUNNY", "FAIL", "REACTION", "RAGE", "SURPRISE", "DIALOGUE", "CLUTCH", "CHAOS", "OTHER"}


def _salvage_truncated(payload: str) -> Any:
    """Small models may hit the token limit mid-JSON. Cut the trailing fragment
    after the last complete item so the valid moments survive."""
    cut = payload.rfind("},")
    if cut == -1:
        logger.warning("Ollama response has no complete items, head=%.200r", payload[:200])
        raise json.JSONDecodeError("no complete items", payload, 0)
    return json.loads(payload[: cut + 1] + "]}")


def validate_highlights(payload: Any, duration: float) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("highlights"), list):
        raise ValueError("Local LLM вернула ответ не по JSON schema.")
    valid = []
    for item in payload["highlights"]:
        try:
            start, end, score = float(item["start_time"]), float(item["end_time"]), int(item["score"])
            category = str(item["category"]).upper()
            if category not in ALLOWED_CATEGORIES:
                # Small local models often invent categories; keep the moment under OTHER
                # instead of discarding a valid time window.
                category = "OTHER"
            if not (0 <= start < end <= duration and 0 <= score <= 100):
                continue
            valid.append({"start_time": start, "end_time": end, "score": score, "category": category,
                          "reason": str(item["reason"])[:500], "transcript_excerpt": str(item["transcript_excerpt"])[:1000]})
        except (KeyError, TypeError, ValueError):
            continue
    return valid


class OllamaLLMProvider(LLMProvider):
    """Local-only JSON client. It never returns executable instructions."""

    CHUNK_SEGMENTS = 40
    CHUNK_OVERLAP = 2
    CHUNK_MAX_MOMENTS = 4

    def find_highlights(self, duration: float) -> list[dict]:
        raise NotImplementedError("Для Ollama необходима транскрипция; подключите Whisper на следующем этапе.")

    def find_highlights_from_transcript(self, segments: list[dict], duration: float) -> list[dict]:
        if len(segments) <= self.CHUNK_SEGMENTS:
            return self._query_chunk(segments, duration)
        chunks: list[list[dict]] = []
        step = self.CHUNK_SEGMENTS - self.CHUNK_OVERLAP
        for start in range(0, len(segments), step):
            chunks.append(segments[start:start + self.CHUNK_SEGMENTS])
            if start + self.CHUNK_SEGMENTS >= len(segments):
                break
        highlights: list[dict] = []
        errors = 0
        for chunk in chunks:
            try:
                highlights.extend(self._query_chunk(chunk, duration))
            except RuntimeError as error:
                logger.warning("Ollama chunk failed, continuing with others: %s", error)
                errors += 1
        if not highlights:
            raise RuntimeError(
                f"Ollama не вернула ни одного момента ({errors}/{len(chunks)} чанков с ошибкой). "
                "Проверьте модель и сервис Ollama."
            )
        return highlights

    def _query_chunk(self, segments: list[dict], duration: float) -> list[dict]:
        # Compact timestamps: per-segment lines dominate the prompt on long transcripts.
        from ...prompts import render_chunk_prompt

        transcript = "\n".join(f"[{s['start']:.0f}-{s['end']:.0f}] {s['text']}" for s in segments)
        prompt = render_chunk_prompt(transcript, max_moments=self.CHUNK_MAX_MOMENTS)
        settings = get_settings()
        body = json.dumps({"model": settings.llm_model, "prompt": prompt, "stream": False, "think": False,
                           "options": {"num_ctx": 8192, "num_predict": 1200},
                           "format": HIGHLIGHT_SCHEMA}).encode()
        request = Request(f"{settings.ollama_url.rstrip('/')}/api/generate", data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=300) as response:
                result = json.loads(response.read().decode())
            try:
                return validate_highlights(json.loads(result["response"]), duration)
            except json.JSONDecodeError:
                # Truncated by the token limit: salvage complete items.
                return validate_highlights(_salvage_truncated(result["response"]), duration)
        except (URLError, TimeoutError, KeyError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Ollama недоступна или не вернула корректный JSON "
                f"({type(error).__name__}: {str(error)[:200]}). Проверьте модель и сервис Ollama."
            ) from error


def ollama_available() -> bool:
    settings = get_settings()
    try:
        with urlopen(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=2) as response:
            models = json.loads(response.read().decode()).get("models", [])
        return any(model.get("name") == settings.llm_model for model in models)
    except (URLError, TimeoutError, json.JSONDecodeError):
        return False


def daemon_running() -> bool:
    """Is the Ollama app/service up (regardless of which models it has)?"""
    settings = get_settings()
    try:
        with urlopen(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=2) as response:
            json.loads(response.read().decode())
        return True
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return False


_spawned: Optional[Any] = None


def ensure_daemon(wait_seconds: float = 20.0) -> bool:
    """Start `ollama serve` in the background when the binary exists but the
    daemon is down. Returns True when the API answers. Never raises."""
    global _spawned
    if daemon_running():
        return True
    import shutil
    import subprocess
    import time as _time

    binary = shutil.which("ollama")
    if binary is None:
        # GUI-launched apps (and the packaged build) have a minimal PATH that
        # misses /usr/local/bin and Homebrew prefixes — check them directly.
        from pathlib import Path as _Path

        for candidate in (
            "/usr/local/bin/ollama",
            "/opt/homebrew/bin/ollama",
            str(_Path.home() / ".ollama" / "bin" / "ollama"),
            "C:\\Program Files\\Ollama\\ollama.exe",
            str(_Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"),
        ):
            if _Path(candidate).is_file():
                binary = candidate
                break
    if binary is None:
        return False
    try:
        if _spawned is not None and _spawned.poll() is None:
            pass  # already starting, just wait below
        else:
            _spawned = subprocess.Popen(
                [binary, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info("started ollama serve in background (pid=%s)", _spawned.pid)
    except Exception as error:  # noqa: BLE001
        logger.warning("could not start ollama serve: %s", error)
        return False
    deadline = _time.time() + wait_seconds
    while _time.time() < deadline:
        if daemon_running():
            return True
        _time.sleep(0.5)
    return daemon_running()


def stop_spawned_daemon() -> None:
    """On backend shutdown, stop the daemon only if we started it ourselves."""
    global _spawned
    process, _spawned = _spawned, None
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
    except Exception:  # noqa: BLE001
        pass


def llm_state() -> dict:
    """First-run screen data for the Qwen model: daemon? model? pulling?"""
    import sys as _sys

    from ...db import database

    settings = get_settings()
    daemon = daemon_running()
    ready = ollama_available() if daemon else False
    pulling: dict = {"active": False}
    try:
        with database() as db:
            row = db.execute("SELECT value FROM app_settings WHERE key='llm_pull'").fetchone()
        if row:
            pulling = json.loads(row["value"])
    except (ValueError, TypeError):
        pulling = {"active": False}
    platform = _sys.platform
    if platform == "darwin":
        download_url = "https://ollama.com/download/Ollama-darwin.zip"
        os_label = "macOS"
    elif platform == "win32":
        download_url = "https://ollama.com/download/OllamaSetup.exe"
        os_label = "Windows"
    else:
        download_url = "https://ollama.com/download/ollama-linux-amd64.tgz"
        os_label = "Linux"
    return {
        "provider": "ollama",
        "model": settings.llm_model,
        "daemon": daemon,
        "ready": ready,
        "pulling": bool(pulling.get("active")),
        "percent": pulling.get("percent"),
        "install_url": "https://ollama.com/download",
        "download_url": download_url,
        "os_label": os_label,
    }


def _set_pull_state(state: dict) -> None:
    from ...db import database

    with database() as db:
        db.execute(
            "INSERT OR REPLACE INTO app_settings VALUES (?, ?)",
            ("llm_pull", json.dumps(state)),
        )


def pull_model() -> dict:
    """`ollama pull <model>` in the caller's thread; progress via llm_state().

    Raises RuntimeError with a human message when the daemon is down or
    the pull fails — the endpoint runs this in a background thread.
    """
    settings = get_settings()
    if not ensure_daemon():
        raise RuntimeError(
            "Ollama не найдена. Установите её с https://ollama.com/download — "
            "дальше приложение запустит её само."
        )
    _set_pull_state({"active": True, "model": settings.llm_model, "percent": 0})
    try:
        body = json.dumps({"model": settings.llm_model, "stream": True}).encode()
        request = Request(
            f"{settings.ollama_url.rstrip('/')}/api/pull",
            data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=3600) as response:
            for line in response:
                try:
                    event = json.loads(line.decode())
                except (ValueError, UnicodeDecodeError):
                    continue
                total = event.get("total") or 0
                done = event.get("completed") or 0
                if total > 0:
                    _set_pull_state({
                        "active": True, "model": settings.llm_model,
                        "percent": max(1, min(99, round(done / total * 100))),
                    })
                if event.get("status") == "success":
                    break
    except Exception as error:  # noqa: BLE001 — clean message for the UI
        _set_pull_state({"active": False, "error": str(error)[:200], "model": settings.llm_model})
        raise RuntimeError(f"Не удалось скачать модель ({error}). Проверьте интернет и перезапустите Ollama.") from error
    _set_pull_state({"active": False, "done": True, "model": settings.llm_model})
    return llm_state()
