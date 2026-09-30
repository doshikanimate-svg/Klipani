from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def find_highlights(self, duration: float) -> list[dict]:
        """Return validated highlight candidates; providers never execute video commands."""

    def find_highlights_from_transcript(self, segments: list[dict], duration: float) -> list[dict]:
        """Default: ignore transcript and use duration-based heuristics."""
        return self.find_highlights(duration)
