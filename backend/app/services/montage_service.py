"""MontagePlan: validated multi-part clip structure (hook -> meat -> punchline).

A plan references source time windows only; FFmpeg commands are built by
deterministic Python code. LLM output never drives rendering directly.
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from ..config import get_settings

PLAN_VERSION = 1
ALLOWED_ROLES = ("hook", "meat", "punchline")


class MontagePart(BaseModel):
    role: str = Field(..., pattern="^(hook|meat|punchline)$")
    source_start: float = Field(..., ge=0)
    source_end: float = Field(..., gt=0)

    @model_validator(mode="after")
    def check_order(self) -> "MontagePart":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class MontagePlan(BaseModel):
    version: int = Field(default=PLAN_VERSION, ge=1)
    video_id: str
    highlight_id: Optional[str] = None
    parts: list[MontagePart] = Field(..., min_length=1, max_length=8)

    @property
    def total_duration(self) -> float:
        return sum(part.source_end - part.source_start for part in self.parts)


def validate_plan(plan: MontagePlan, duration: float) -> MontagePlan:
    """Enforce ordering, bounds and total duration cap."""
    settings = get_settings()
    cursor = -1.0
    video_end = round(duration, 3)
    for part in plan.parts:
        if part.source_start < cursor:
            raise ValueError("Части монтажа должны идти по порядку и не пересекаться.")
        if round(part.source_end, 3) > video_end:
            raise ValueError("Часть монтажа выходит за длительность видео.")
        cursor = part.source_end
    if plan.total_duration > settings.max_clip_duration:
        raise ValueError(
            f"Монтаж длиннее лимита ({plan.total_duration:.1f} с > {settings.max_clip_duration:.0f} с)."
        )
    return plan


def build_plan(
    video: dict,
    highlights: list[dict],
    segments: Optional[list[dict]] = None,
    context_seconds: float = 4.0,
    max_parts: int = 5,
) -> MontagePlan:
    """Assemble the best moments across the whole video.

    Each highlight becomes one part (expanded by context), ordered by time.
    First part is the hook, last is the punchline. Lowest-score parts are
    dropped while the total exceeds the duration cap.
    """
    duration = float(video["duration"])
    settings = get_settings()
    ranked = sorted(highlights, key=lambda h: (-int(h["score"]), float(h["start_time"])))[:max_parts]
    if not ranked:
        raise ValueError("Нет моментов для монтажа.")

    windows: list[dict] = []
    for highlight in ranked:
        start = max(0.0, float(highlight["start_time"]) - context_seconds)
        end = min(duration, float(highlight["end_time"]) + context_seconds)
        if end - start < 1.0:
            continue
        windows.append({"highlight": highlight, "start": start, "end": end})

    cap = settings.max_clip_duration
    while sum(w["end"] - w["start"] for w in windows) > cap and len(windows) > 1:
        windows.pop()

    if not windows:
        raise ValueError("Пустой монтаж: моменты не влезли в лимит.")
    windows.sort(key=lambda w: w["start"])

    parts: list[dict] = []
    for index, window in enumerate(windows):
        if len(windows) == 1:
            role = "meat"
        elif index == 0:
            role = "hook"
        elif index == len(windows) - 1:
            role = "punchline"
        else:
            role = "meat"
        parts.append({
            "role": role,
            "source_start": round(window["start"], 3),
            "source_end": round(window["end"], 3),
        })
    plan = MontagePlan(
        video_id=video["id"],
        highlight_id=ranked[0]["id"],
        parts=parts,
    )
    return validate_plan(plan, duration)
