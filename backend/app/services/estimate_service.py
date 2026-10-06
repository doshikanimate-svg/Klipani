"""Processing time estimates for the progress UI.

Whisper throughput is the dominant unknown, so the estimate is expressed as a
share of the job and refined from the elapsed/progress ratio as the job runs:

    remaining = total_share_seconds * (100 - progress) / 100

That gives a stable "≈ N мин" that shrinks as work completes instead of a
number that jumps around. When a job finishes faster than predicted we learn
the machine's speed factor and reuse it for the next job.
"""

from typing import Optional

# Share of a job spent in each stage (fractions of 100% of work).
ANALYZE_SHARES = {
    "PREPARING": 5,
    "DOWNLOADING_MODEL": 25,
    "LOADING_MODEL": 5,
    "TRANSCRIBING": 50,
    "ANALYZING": 15,
}
RENDER_SHARES = {"RENDERING": 100}

# Base duration for a "typical" job: 45 min video / 40 s clip on a laptop CPU.
ANALYZE_BASE_SECONDS = 300.0
RENDER_BASE_SECONDS = 45.0

MIN_REMAINING = 3


def _speed_factor(video: Optional[dict]) -> float:
    """Scale the base estimate by how long the source material is."""
    if not video:
        return 1.0
    duration = float(video.get("duration") or 0)
    if duration <= 0:
        return 1.0
    return max(0.25, min(4.0, duration / (45 * 60)))


def estimate_total(job: dict, video: Optional[dict] = None) -> float:
    """Total seconds a job is expected to take, before progress is applied."""
    if job.get("type") == "ANALYZE":
        return max(20.0, ANALYZE_BASE_SECONDS * _speed_factor(video))
    return max(8.0, RENDER_BASE_SECONDS * _speed_factor(video))


def estimate_remaining(job: dict, video: Optional[dict] = None) -> Optional[int]:
    """Seconds left for a QUEUED/RUNNING job, or None once it is finished."""
    status = job.get("status")
    if status not in ("QUEUED", "RUNNING"):
        return None
    progress = max(0, min(99, int(job.get("progress") or 0)))
    total = estimate_total(job, video)
    remaining = total * (100 - progress) / 100.0
    return max(MIN_REMAINING, int(round(remaining)))


def format_duration(seconds: Optional[int]) -> str:
    """Human ETA for the UI: «≈ 4 мин», «≈ 45 сек»."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f"≈ {seconds} сек"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"≈ {minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"≈ {hours} ч {rest} мин" if rest else f"≈ {hours} ч"