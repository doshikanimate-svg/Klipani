import pytest
from pydantic import ValidationError

from app.services.montage_service import MontagePlan, build_plan, validate_plan


def _video(duration: float = 100.0) -> dict:
    return {"id": "vid", "duration": duration}


def _highlight(start: float = 20.0, end: float = 40.0) -> dict:
    return {"id": "hl", "start_time": start, "end_time": end, "score": 80}


def test_validate_plan_rejects_overlap_and_overflow() -> None:
    with pytest.raises(ValidationError):
        MontagePlan(video_id="v", parts=[{"role": "hook", "source_start": 10, "source_end": 5}])
    with pytest.raises(ValueError, match="порядку"):
        validate_plan(
            MontagePlan(video_id="v", parts=[
                {"role": "hook", "source_start": 0, "source_end": 10},
                {"role": "meat", "source_start": 5, "source_end": 15},
            ]),
            100.0,
        )
    with pytest.raises(ValueError, match="выходит"):
        validate_plan(
            MontagePlan(video_id="v", parts=[{"role": "meat", "source_start": 90, "source_end": 120}]),
            100.0,
        )


def _highlights() -> list[dict]:
    return [
        {"id": "h1", "start_time": 20.0, "end_time": 30.0, "score": 90},
        {"id": "h2", "start_time": 60.0, "end_time": 70.0, "score": 80},
        {"id": "h3", "start_time": 100.0, "end_time": 110.0, "score": 70},
    ]


def test_build_plan_assembles_moments_in_time_order() -> None:
    plan = build_plan(_video(200.0), _highlights(), context_seconds=1.0)
    assert [p.role for p in plan.parts] == ["hook", "meat", "punchline"]
    starts = [p.source_start for p in plan.parts]
    assert starts == sorted(starts)
    assert plan.parts[0].source_start == 19.0
    assert plan.parts[-1].source_end == 111.0
    assert plan.total_duration <= 45


def test_build_plan_drops_lowest_score_over_cap() -> None:
    highlights = [
        {"id": f"h{i}", "start_time": float(i * 40), "end_time": float(i * 40 + 20), "score": 90 - i}
        for i in range(5)
    ]
    plan = build_plan(_video(300.0), highlights)
    assert plan.total_duration <= 45
    assert len(plan.parts) < 5


def test_build_plan_single_highlight_is_meat() -> None:
    plan = build_plan(_video(), [_highlight()])
    assert [p.role for p in plan.parts] == ["meat"]


def test_build_plan_no_highlights_raises() -> None:
    import pytest as _pytest
    with _pytest.raises(ValueError, match="Нет моментов"):
        build_plan(_video(), [])
