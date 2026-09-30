from app.providers.llm.ollama import _salvage_truncated, validate_highlights
from app.services.selection_service import blend_scores, clamp_window, select_highlights


def test_llm_highlight_validation_discards_invalid_items() -> None:
    payload = {"highlights": [
        {"start_time": 2, "end_time": 8, "score": 90, "category": "FUNNY", "reason": "ok", "transcript_excerpt": "text"},
        {"start_time": 8, "end_time": 2, "score": 120, "category": "INVALID", "reason": "no", "transcript_excerpt": "text"},
    ]}
    assert validate_highlights(payload, 10) == [payload["highlights"][0]]


def test_llm_highlight_validation_maps_unknown_category_to_other() -> None:
    payload = {"highlights": [
        {"start_time": 1, "end_time": 5, "score": 42, "category": "smash", "reason": "invented by small LLM", "transcript_excerpt": "text"},
    ]}
    result = validate_highlights(payload, 10)
    assert len(result) == 1
    assert result[0]["category"] == "OTHER"
    assert result[0]["score"] == 42


def test_salvage_truncated_keeps_complete_items() -> None:
    truncated = (
        '{"highlights": ['
        '{"start_time": 1, "end_time": 5, "score": 80, "category": "FUNNY", "reason": "a", "transcript_excerpt": "a"},'
        '{"start_time": 6, "end_time": 9, "score": 70, "category": "REACTION", "reason": "b", "transcript_excerpt": "b"},'
        '{"start_time": 10, "end_time": 12, "score": 60, "category": "SMASH", "reason": "cut'
    )
    result = validate_highlights(_salvage_truncated(truncated), 60)
    assert [item["category"] for item in result] == ["FUNNY", "REACTION"]


def test_blend_and_clamp() -> None:
    assert blend_scores(100, 0) == 70
    start, end = clamp_window(-5, 100, 50, 20)
    assert (start, end) == (15.0, 35.0)
    short_start, short_end = clamp_window(10, 12, 100, 25)
    assert short_end - short_start == 8.0


def test_select_highlights_respects_max_and_distance(monkeypatch) -> None:
    monkeypatch.setattr("app.services.selection_service.audio_score", lambda *args, **kwargs: 50)
    video = {"path": "/tmp/missing.mp4", "duration": 300}
    candidates = [
        {"start_time": 10, "end_time": 20, "score": 90, "category": "FUNNY", "reason": "a", "transcript_excerpt": "a"},
        {"start_time": 15, "end_time": 25, "score": 80, "category": "FUNNY", "reason": "b", "transcript_excerpt": "b"},
        {"start_time": 120, "end_time": 135, "score": 70, "category": "REACTION", "reason": "c", "transcript_excerpt": "c"},
        {"start_time": 200, "end_time": 220, "score": 60, "category": "SURPRISE", "reason": "d", "transcript_excerpt": "d"},
    ]
    selected = select_highlights(candidates, video)
    assert len(selected) <= 3
    assert selected[0]["start_time"] == 10
    mids = [(item["start_time"] + item["end_time"]) / 2 for item in selected]
    assert all(abs(mids[i] - mids[j]) >= 30 for i in range(len(mids)) for j in range(i + 1, len(mids)))


def test_combine_scores() -> None:
    from app.services.selection_service import combine_scores, peak_score
    from pathlib import Path

    assert combine_scores(100, 100, 100) == 100
    assert combine_scores(0, 0, 0) == 0
    assert combine_scores(80, 70, 60) == 74
    assert peak_score(Path("/nonexistent.mp4"), 0, 10) == 50
