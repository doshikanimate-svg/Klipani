import json
import logging
from typing import Any
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
        transcript = "\n".join(f"[{s['start']:.0f}-{s['end']:.0f}] {s['text']}" for s in segments)
        prompt = (
            "Ты редактор коротких игровых видео. Найди до 4 интересных моментов в этом отрывке: смешные ситуации, "
            "фейлы, эмоциональные реакции, неожиданности, панчлайны. Оценивай контекст, не ищи только отдельные слова. "
            "Категория — СТРОГО одна из: FUNNY, FAIL, REACTION, RAGE, SURPRISE, DIALOGUE, CLUTCH, CHAOS, OTHER. "
            "Оценка score — от 1 до 100. Причина reason — коротко, до 10 слов. "
            "Каждый момент должен длиться 8–25 секунд: короткие реплики расширяй контекстом вокруг. "
            "Верни только JSON по заданной schema. Таймкоды должны быть внутри отрывка.\n\nОТРЫВОК:\n" + transcript
        )
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
