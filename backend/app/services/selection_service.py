from functools import lru_cache
from pathlib import Path
import re
import subprocess

from ..config import get_settings
from ..utils.ffmpeg import bundled_exe, mean_volume
from .stats_service import category_affinity

_MOMENTARY_RE = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")


def _ffmpeg_binary() -> str:
    return bundled_exe() or "ffmpeg"


@lru_cache(maxsize=8)
def _loudness_profile_cached(path_str: str, signature: tuple) -> tuple:
    """Per-second peak momentary loudness (LUFS) for the whole file.

    signature = (size, mtime) so the cache invalidates on replacement.
    """
    command = [
        _ffmpeg_binary(), "-i", path_str,
        "-map", "0:a?", "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    momentary: list[float] = []
    current: list[float] = []
    for line in (result.stderr or "").splitlines():
        match = _MOMENTARY_RE.search(line)
        if match:
            try:
                current.append(float(match.group(1)))
            except ValueError:
                pass
            if len(current) >= 10:
                finite = [v for v in current if v > -70.0]
                momentary.append(max(finite) if finite else -70.0)
                current = []
    if current:
        finite = [v for v in current if v > -70.0]
        momentary.append(max(finite) if finite else -70.0)
    return tuple(momentary)


def loudness_profile(path: Path) -> tuple:
    try:
        stat = path.stat()
    except OSError:
        return ()
    return _loudness_profile_cached(str(path), (stat.st_size, stat.st_mtime_ns))


def audio_score(path: Path, start: float, end: float) -> int:
    """Map FFmpeg mean_volume (dB) into 0–100. Silence ≈ 0, loud peaks ≈ 100."""
    duration = max(0.2, end - start)
    volume = mean_volume(path, start, duration)
    if volume is None:
        return 50
    # Typical speech/stream levels sit roughly between -45 dB and -5 dB.
    normalized = (volume + 45.0) / 40.0
    return max(0, min(100, int(round(normalized * 100))))


def blend_scores(llm_score: int, audio: int) -> int:
    return max(0, min(100, int(round(0.7 * llm_score + 0.3 * audio))))


def peak_score(path: Path, start: float, end: float) -> int:
    """Score emotional peaks (shouts/laughter): share of loud seconds in range.

    A second is 'loud' when above the video's p90 momentary loudness.
    Returns 0–100.
    """
    profile = loudness_profile(path)
    if len(profile) < 5:
        return 50
    ordered = sorted(profile)
    threshold = ordered[max(0, int(len(ordered) * 0.9) - 1)]
    first = max(0, int(start))
    last = min(len(profile), int(end) + 1)
    window = profile[first:last]
    if not window:
        return 0
    loud = sum(1 for level in window if level >= threshold)
    return max(0, min(100, int(round(100 * loud / len(window)))))


def combine_scores(llm_score: int, audio: int, peaks: int) -> int:
    base = blend_scores(llm_score, audio)
    return max(0, min(100, int(round(0.8 * base + 0.2 * peaks))))


def clamp_window(start: float, end: float, duration: float, max_core: float, min_core: float = 8.0) -> tuple[float, float]:
    start = max(0.0, min(start, duration))
    end = max(0.0, min(end, duration))
    if end <= start:
        end = min(duration, start + min(5.0, max_core))
    # Expand short candidates around the center so clips are watchable.
    length = end - start
    if length < min_core:
        midpoint = (start + end) / 2
        half = min_core / 2
        start, end = midpoint - half, midpoint + half
        if start < 0:
            end -= start
            start = 0.0
        if end > duration:
            start -= end - duration
            end = duration
        start = max(0.0, start)
    if end - start > max_core:
        midpoint = (start + end) / 2
        start, end = midpoint - max_core / 2, midpoint + max_core / 2
    if start < 0:
        end -= start
        start = 0.0
    if end > duration:
        start = max(0.0, duration - (end - start))
        end = duration
    return round(start, 3), round(end, 3)


def select_highlights(candidates: list[dict], video: dict) -> list[dict]:
    """Rank by blended score, drop near-duplicates, keep top N."""
    settings = get_settings()
    path = Path(video["path"])
    duration = float(video["duration"])
    max_core = max(5.0, settings.max_clip_duration - settings.pre_roll_seconds - settings.post_roll_seconds)
    affinity = category_affinity()
    enriched: list[dict] = []
    for candidate in candidates:
        start, end = clamp_window(float(candidate["start_time"]), float(candidate["end_time"]), duration, max_core)
        audio = audio_score(path, start, end)
        peaks = peak_score(path, start, end)
        score = combine_scores(int(candidate["score"]), audio, peaks)
        score = max(0, min(100, score + affinity.get(str(candidate.get("category", "")).upper(), 0)))
        enriched.append({
            **candidate,
            "start_time": start,
            "end_time": end,
            "score": score,
        })
    enriched.sort(key=lambda item: (-item["score"], item["start_time"]))
    selected: list[dict] = []
    for candidate in enriched:
        midpoint = (candidate["start_time"] + candidate["end_time"]) / 2
        if any(abs(midpoint - (item["start_time"] + item["end_time"]) / 2) < settings.min_highlight_distance for item in selected):
            continue
        selected.append(candidate)
        if len(selected) >= settings.max_highlights:
            break
    return selected
