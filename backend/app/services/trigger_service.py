"""The signature KLIPANI feature: the «клипани!» trigger word.

When the streamer (or chat, caught on mic) says «клипани», «клипаните»,
«сделайте клип» and friends, the app cuts the 30 seconds before the word
plus the moment itself — no LLM needed, deterministic, works in mock mode too.

Pipeline math: generate_clip adds pre_roll (10s) before and post_roll (10s)
after the highlight core, capped at max_clip_duration (45s). So a core of
[T-20, T+5] renders as [T-30, T+15]: exactly 30 seconds before the word,
the word itself, and a breath of reaction after it. The core also fits
max_core (25s), so the selection clamp never eats into it.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

CATEGORY = "KLIPANI"
SCORE = 100

CORE_BEFORE = 20.0
CORE_AFTER = 5.0
MERGE_GAP = 10.0

# Normalized (lowercased, ё→е) transcript text is matched against these.
# Deliberately NOT bare «клип/клипы»: «смотрим клип» (music video) must not fire.
PATTERNS = (
    re.compile(r"\bклипани\w*"),          # клипани, клипаните, клипанишь
    re.compile(r"\bклипан\w*"),           # клипанул, клипанешь, клипанем
    re.compile(r"\bклипн\w*"),            # клипни, клипните
    re.compile(r"\bклипу\w*"),            # клипуй, клипуйте
    re.compile(r"\bзаклипа\w*"),          # заклипай, заклипайте
    re.compile(r"\bсделай(?:те)?\s+клип\b"),   # сделай(те) клип
    re.compile(r"\bделай(?:те)?\s+клип\b"),    # делай(те) клип
    re.compile(r"\bэто\s+клип\b"),            # это клип! (= clip it)
    re.compile(r"\bвот\s+это\s+клип\b"),      # вот это клип!
)


def normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def find_hit_word(text: str) -> Optional[str]:
    """The exact trigger word/phrase in the text, or None."""
    clean = normalize(text)
    for pattern in PATTERNS:
        match = pattern.search(clean)
        if match:
            return match.group(0)
    return None


def _hit_time(segment: dict) -> tuple:
    """(timestamp, word) of the first trigger hit in a segment.

    Prefers word-level timestamps (Whisper provides them); falls back to the
    segment start. Returns (None, None) when nothing hits.
    """
    words = segment.get("words") or []
    for word in words:
        text = str(word.get("text", ""))
        if find_hit_word(text):
            try:
                return float(word.get("start", segment.get("start", 0.0))), text.strip()
            except (TypeError, ValueError):
                pass
    text = str(segment.get("text", ""))
    hit = find_hit_word(text)
    if not hit:
        return None, None
    try:
        return float(segment.get("start", 0.0)), hit
    except (TypeError, ValueError):
        return None, None


def find_trigger_moments(segments: Optional[list], duration: float) -> list[dict]:
    """Build pinned highlight candidates from trigger words.

    Each hit at time T becomes core [T-20, T+5]; close hits merge into one
    window so a «клипани-клипани-клипани!» chant is a single clip.
    """
    if not segments or duration <= 0:
        return []
    hits: list[tuple] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        moment, word = _hit_time(segment)
        if moment is None:
            continue
        hits.append((max(0.0, min(moment, duration)), word, index))
    hits.sort()
    windows: list[dict] = []
    for moment, word, index in hits:
        start = max(0.0, moment - CORE_BEFORE)
        end = min(duration, moment + CORE_AFTER)
        if windows and start - windows[-1]["end"] <= MERGE_GAP:
            previous = windows[-1]
            previous["end"] = end
            previous["words"].append(word)
            previous["segment_indexes"].append(index)
            continue
        windows.append({
            "start": start, "end": end, "moment": moment,
            "words": [word], "segment_indexes": [index],
        })
    results = []
    for window in windows:
        excerpt = " … ".join(
            str(segments[i].get("text", "")).strip()
            for i in window["segment_indexes"]
            if str(segments[i].get("text", "")).strip()
        )[:1000] or "—"
        showcase = window["words"][0]
        results.append({
            "start_time": round(window["start"], 3),
            "end_time": round(window["end"], 3),
            "score": SCORE,
            "category": CATEGORY,
            "reason": f"⚡ Триггер «{showcase}»: 30 секунд до слова + сам момент.",
            "transcript_excerpt": excerpt,
            "pinned": True,
        })
    if results:
        logger.info("trigger words fired %s time(s)", len(results))
    return results


def strip_overlapped(candidates: list[dict], triggers: list[dict]) -> list[dict]:
    """Drop LLM/mock candidates covered by a trigger window (trigger wins)."""
    if not triggers:
        return candidates
    kept = []
    for candidate in candidates:
        start = float(candidate.get("start_time", 0.0))
        end = float(candidate.get("end_time", 0.0))
        midpoint = (start + end) / 2
        if any(t["start_time"] - 1.0 <= midpoint <= t["end_time"] + 1.0 for t in triggers):
            continue
        kept.append(candidate)
    return kept
