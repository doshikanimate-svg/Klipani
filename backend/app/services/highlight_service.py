from typing import Optional
from uuid import uuid4

from ..db import database, many
from ..providers.llm.factory import get_llm_provider
from ..providers.llm.mock import MockLLMProvider
from ..providers.llm.ollama import OllamaLLMProvider
from .analysis_service import save_analysis
from .selection_service import select_highlights

import logging

logger = logging.getLogger(__name__)


def _persist(video_id: str, candidates: list[dict]) -> list[dict]:
    highlights = [{"id": uuid4().hex, "video_id": video_id, **candidate} for candidate in candidates]
    with database() as db:
        db.execute("DELETE FROM highlights WHERE video_id=?", (video_id,))
        db.executemany(
            """INSERT INTO highlights VALUES (:id,:video_id,:start_time,:end_time,:score,:category,:reason,:transcript_excerpt)""",
            highlights,
        )
    return highlights


def create_highlights(video: dict, segments: Optional[list[dict]] = None) -> list[dict]:
    provider = get_llm_provider()
    provider_name = "ollama" if isinstance(provider, OllamaLLMProvider) else "mock"
    duration = float(video["duration"])
    try:
        if segments:
            candidates = provider.find_highlights_from_transcript(segments, duration)
        else:
            candidates = provider.find_highlights(duration)
    except Exception as error:
        logger.warning("LLM provider '%s' failed, falling back to mock: %s", provider_name, error)
        candidates = []
    if not candidates:
        fallback = MockLLMProvider()
        provider_name = "mock"
        candidates = (
            fallback.find_highlights_from_transcript(segments, duration)
            if segments
            else fallback.find_highlights(duration)
        )
    selected = select_highlights(candidates, video)
    save_analysis(video["id"], has_transcript=bool(segments), segments=len(segments or []), llm_provider=provider_name)
    return _persist(video["id"], selected)


def create_mock_highlights(video: dict) -> list[dict]:
    """Backward-compatible alias used by tests and older call sites."""
    return create_highlights(video, segments=None)


def create_ai_highlights(video: dict, segments: list[dict]) -> list[dict]:
    return create_highlights(video, segments=segments)


def update_highlight(highlight_id: str, start_time: float, end_time: float, duration: float) -> Optional[dict]:
    if not (0 <= start_time < end_time <= duration):
        raise ValueError("Границы момента должны быть внутри длительности видео.")
    with database() as db:
        db.execute(
            "UPDATE highlights SET start_time=?, end_time=? WHERE id=?",
            (round(start_time, 3), round(end_time, 3), highlight_id),
        )
    return get_highlight(highlight_id)


def list_highlights(video_id: str) -> list[dict]:
    with database() as db:
        return many(db.execute("SELECT * FROM highlights WHERE video_id=? ORDER BY score DESC", (video_id,)).fetchall())


def get_highlight(highlight_id: str) -> Optional[dict]:
    with database() as db:
        row = db.execute("SELECT * FROM highlights WHERE id=?", (highlight_id,)).fetchone()
        return dict(row) if row else None
