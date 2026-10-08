"""The «клипани!» trigger word: deterministic signature moments."""

import pytest


@pytest.mark.parametrize("text", [
    "клипани!",
    "КЛИПАНИТЕ ЭТОТ МОМЕНТ",
    "ну клипанишь или нет",
    "он клипанул в конце",
    "клипуй быстрее",
    "клипуйте, пацаны",
    "сделай клип",
    "Сделайте клип, пожалуйста",
    "делай клип",
    "это клип!",
    "вот это клип",
    "заклипай это",
    "клипни меня",
    "сейчас клипану",
])
def test_trigger_forms_fire(text) -> None:
    from app.services.trigger_service import find_hit_word

    assert find_hit_word(text) is not None, text


@pytest.mark.parametrize("text", [
    "смотрим клип",
    "клип",
    "клипы",
    "новый клип вышел",
    "клипмейкер",
    "клиповый монтаж",
    "эклиптика",
    "",
])
def test_lookalikes_do_not_fire(text) -> None:
    from app.services.trigger_service import find_hit_word

    assert find_hit_word(text) is None, text


def _segments() -> list:
    return [
        {"start": 0.0, "end": 10.0, "text": "ну что, начинаем стрим"},
        {"start": 60.0, "end": 70.0, "text": "ребята клипаните этот момент",
         "words": [
             {"start": 60.0, "end": 60.4, "text": "ребята"},
             {"start": 65.5, "end": 66.1, "text": "клипаните"},
             {"start": 66.2, "end": 66.9, "text": "этот"},
         ]},
        {"start": 71.0, "end": 75.0, "text": "вот это было мощно"},
    ]


def test_word_level_timing_gives_30s_before() -> None:
    """Hit at 65.5 with word timestamps -> core [45.5, 70.5], renders as [35.5, 80.5]."""
    from app.services.trigger_service import find_trigger_moments

    moments = find_trigger_moments(_segments(), 120.0)
    assert len(moments) == 1
    moment = moments[0]
    assert moment["start_time"] == 45.5
    assert moment["end_time"] == 70.5
    assert moment["category"] == "KLIPANI"
    assert moment["score"] == 100
    assert moment["pinned"] is True
    assert "клипаните" in moment["reason"]


def test_segment_fallback_and_start_clamp() -> None:
    from app.services.trigger_service import find_trigger_moments

    segments = [{"start": 10.0, "end": 14.0, "text": "клипани"}]  # no words
    (moment,) = find_trigger_moments(segments, 120.0)
    assert moment["start_time"] == 0.0  # 10 - 20 clamped, not negative
    assert moment["end_time"] == 15.0


def test_chant_merges_into_one_window() -> None:
    from app.services.trigger_service import find_trigger_moments

    segments = [
        {"start": 60.0, "end": 62.0, "text": "клипани"},
        {"start": 64.0, "end": 66.0, "text": "клипани клипани"},
        {"start": 200.0, "end": 202.0, "text": "сделай клип"},
    ]
    moments = find_trigger_moments(segments, 300.0)
    assert len(moments) == 2  # chant merged, distant hit separate
    assert moments[0]["start_time"] == 40.0
    assert moments[0]["end_time"] == 69.0  # extended to second hit (64) + 5


def test_strip_overlapped_keeps_distant_picks() -> None:
    from app.services.trigger_service import find_trigger_moments, strip_overlapped

    triggers = find_trigger_moments(_segments(), 120.0)
    candidates = [
        {"start_time": 50.0, "end_time": 60.0, "score": 90},   # midpoint 55 inside -> dropped
        {"start_time": 0.0, "end_time": 8.0, "score": 90},     # far away -> kept
    ]
    kept = strip_overlapped(candidates, triggers)
    assert len(kept) == 1 and kept[0]["start_time"] == 0.0


def test_trigger_survives_full_pipeline() -> None:
    """End to end: mock provider + transcript -> KLIPANI moment pinned on top."""
    from app.db import initialize
    from app.services.highlight_service import create_highlights

    initialize()
    video = {"id": "trigger-vid", "path": "/nonexistent/stream.mp4", "duration": 120.0}
    highlights = create_highlights(video, _segments())
    by_category = [h["category"] for h in highlights]
    assert "KLIPANI" in by_category
    assert highlights[0]["category"] == "KLIPANI"  # pinned first, above the cap logic
    trigger = highlights[0]
    assert trigger["start_time"] == 45.5 and trigger["end_time"] == 70.5
    assert "клипаните" in trigger["reason"]
