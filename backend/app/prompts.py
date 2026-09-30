"""LLM prompt templates loaded from backend/prompts.json.

The JSON file is user-editable: prompt tuning no longer requires a code
change (or an app rebuild once packaged). If the file is missing or broken,
built-in defaults are used.
"""

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULTS = {
    "highlights_chunk": {
        "system": "Ты редактор коротких игровых видео.",
        "task": "Найди до {max_moments} интересных моментов в этом отрывке: смешные ситуации, фейлы, эмоциональные реакции, неожиданности, панчлайны. Оценивай контекст, не ищи только отдельные слова.",
        "rules": [
            "Категория — СТРОГО одна из: FUNNY, FAIL, REACTION, RAGE, SURPRISE, DIALOGUE, CLUTCH, CHAOS, OTHER.",
            "Оценка score — от 1 до 100.",
            "Причина reason — коротко, до 10 слов.",
            "Каждый момент должен длиться 8–25 секунд: короткие реплики расширяй контекстом вокруг.",
            "Верни только JSON по заданной schema. Таймкоды должны быть внутри отрывка.",
        ],
        "transcript_header": "ОТРЫВОК:",
    }
}


def _prompts_path() -> Path:
    override = os.environ.get("KLIPANI_PROMPTS", "")
    if override:
        return Path(override)
    candidates = [Path(__file__).resolve().parents[1] / "prompts.json"]
    try:
        import sys as _sys

        if getattr(_sys, "frozen", False):
            exe_dir = Path(_sys.executable).resolve().parent
            candidates.insert(0, exe_dir / "_internal" / "prompts.json")
            candidates.insert(0, exe_dir / "prompts.json")
    except Exception:  # noqa: BLE001
        pass
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


@lru_cache(maxsize=1)
def load_prompts() -> dict:
    merged = {key: dict(value) for key, value in DEFAULTS.items()}
    try:
        raw = json.loads(_prompts_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("prompts.json unreadable, using built-in prompts: %s", error)
        return merged
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in merged and isinstance(value, dict):
                merged[key].update(value)
            else:
                merged[key] = value
    return merged


def render_chunk_prompt(transcript: str, max_moments: int = 4) -> str:
    template = load_prompts()["highlights_chunk"]
    rules = "\n".join(template.get("rules", []))
    task = template.get("task", "").format(max_moments=max_moments)
    parts = [template.get("system", ""), task, rules]
    header = template.get("transcript_header", "")
    body = "\n".join(part for part in parts if part)
    return f"{body}\n\n{header}\n{transcript}" if header else body


def reload_prompts() -> dict:
    load_prompts.cache_clear()
    return load_prompts()
