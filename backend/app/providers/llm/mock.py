from .base import LLMProvider


def _segment_interest(text: str) -> int:
    score = min(40, len(text))
    if "!" in text or "?" in text:
        score += 15
    if any(word in text.lower() for word in ("лох", "бля", "ха", "аха", "wtf", "omg", "fail", "нет", "да ладно")):
        score += 20
    if text.isupper() and len(text) > 3:
        score += 10
    return score


class MockLLMProvider(LLMProvider):
    def find_highlights(self, duration: float) -> list[dict]:
        if duration < 3:
            return [{"start_time": 0, "end_time": duration, "score": 75, "category": "OTHER", "reason": "Тестовый момент для короткого видео.", "transcript_excerpt": "Mock highlight"}]
        anchors = [duration * ratio for ratio in (0.2, 0.5, 0.8)]
        categories = ["REACTION", "FUNNY", "SURPRISE"]
        return [
            {
                "start_time": max(0, anchor - 5),
                "end_time": min(duration, anchor + 8),
                "score": 90 - index * 8,
                "category": categories[index],
                "reason": "Тестовый момент, созданный MockLLMProvider.",
                "transcript_excerpt": "Mock highlight — подключите Whisper и локальную LLM для реального анализа.",
            }
            for index, anchor in enumerate(anchors)
        ]

    def find_highlights_from_transcript(self, segments: list[dict], duration: float) -> list[dict]:
        if not segments:
            return self.find_highlights(duration)
        ranked = sorted(segments, key=lambda item: _segment_interest(item.get("text", "")), reverse=True)
        categories = ["FUNNY", "REACTION", "SURPRISE", "DIALOGUE", "CHAOS"]
        results = []
        for index, segment in enumerate(ranked[:8]):
            start = max(0.0, float(segment["start"]) - 2)
            end = min(duration, float(segment["end"]) + 6)
            text = str(segment.get("text", "")).strip()
            results.append({
                "start_time": start,
                "end_time": end,
                "score": max(55, min(95, 60 + _segment_interest(text) // 2)),
                "category": categories[index % len(categories)],
                "reason": "Момент выбран по транскрипту (mock-ранжирование без Ollama).",
                "transcript_excerpt": text[:1000] or "—",
            })
        return results or self.find_highlights(duration)
