from app.db import initialize
from app.services.analysis_service import get_analysis, save_analysis


def test_analysis_roundtrip() -> None:
    initialize()
    record = save_analysis("test-analysis-video", has_transcript=True, segments=11, llm_provider="ollama")
    assert record["has_transcript"] == 1
    assert record["segments"] == 11
    fetched = get_analysis("test-analysis-video")
    assert fetched is not None
    assert fetched["llm_provider"] == "ollama"
    assert get_analysis("test-analysis-missing") is None
