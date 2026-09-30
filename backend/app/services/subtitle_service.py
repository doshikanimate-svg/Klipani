"""ASS subtitle generation for vertical clips.

Builds a TikTok-style ASS file (bold white text, black outline, bottom center)
from transcript segments, shifted into clip-local timestamps.
"""

ESCAPES = {"\\": r"\\", "{": r"\{", "}": r"\}"}

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Clip,{font},64,&H00FFFFFF,&H000019FF,&H80000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,60,60,200,1
Style: Karaoke,{font},80,&H0000FFFF,&H00FFFFFF,&H90000000,&H90000000,-1,0,0,0,100,100,1,0,1,4,2,2,60,60,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def escape_ass(text: str) -> str:
    return "".join(ESCAPES.get(char, char) for char in text)


def ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = seconds % 60
    return f"{hours}:{minutes:02d}:{rest:05.2f}"


def build_ass(segments: list[dict], clip_start: float, clip_end: float, font: str = "Helvetica") -> str:
    """Plain segment-level subtitles, shifted to clip time."""
    lines = [HEADER.format(font=font)]
    for segment in segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
            text = str(segment.get("text", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not text or end <= clip_start or start >= clip_end:
            continue
        lines.append(
            "Dialogue: 0,{},{},Clip,,0,0,0,,{}\n".format(
                ass_timestamp(start - clip_start),
                ass_timestamp(end - clip_start),
                escape_ass(text),
            )
        )
    return "".join(lines)


def _words_in_range(segments: list[dict], clip_start: float, clip_end: float) -> list[dict]:
    """Flatten segments into word-level timings, all in clip-local time.

    Falls back to even splits when a segment has no word data (old transcripts).
    """
    words: list[dict] = []
    for segment in segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= clip_start or start >= clip_end or end <= start:
            continue
        stored = segment.get("words") or []
        parsed = []
        for word in stored:
            try:
                parsed.append({
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "text": str(word.get("text", "")).strip(),
                })
            except (KeyError, TypeError, ValueError):
                continue
        parsed = [w for w in parsed if w["text"] and w["end"] > w["start"]]
        if not parsed:
            tokens = str(segment.get("text", "")).split()
            if not tokens:
                continue
            step = (end - start) / len(tokens)
            parsed = [
                {"start": start + i * step, "end": start + (i + 1) * step, "text": token}
                for i, token in enumerate(tokens)
            ]
        for word in parsed:
            local_start = word["start"] - clip_start
            local_end = word["end"] - clip_start
            if local_end <= 0 or local_start >= clip_end - clip_start:
                continue
            words.append({
                "start": max(0.0, local_start),
                "end": max(0.0, local_end),
                "text": word["text"],
            })
    words.sort(key=lambda w: (w["start"], w["end"]))
    return words


def _render_karaoke(
    words: list[dict],
    font: str = "Helvetica",
    max_words: int = 4,
    max_chars: int = 24,
) -> str:
    phrases: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for word in words:
        text = word["text"].upper()
        if current and (len(current) >= max_words or current_chars + 1 + len(text) > max_chars):
            phrases.append(current)
            current = []
            current_chars = 0
        current.append({**word, "text": text})
        current_chars += (1 if current_chars else 0) + len(text)
    if current:
        phrases.append(current)
    lines = [HEADER.format(font=font)]
    for index, phrase in enumerate(phrases):
        start = phrase[0]["start"]
        end = phrase[-1]["end"]
        if index + 1 < len(phrases):
            end = min(end, phrases[index + 1][0]["start"])
        if end <= start:
            continue
        body = "".join(
            "{{\\kf{}}} {}".format(max(1, int(round((w['end'] - w['start']) * 100))), escape_ass(w["text"]))
            for w in phrase
        ).rstrip()
        lines.append("Dialogue: 0,{},Karaoke,,0,0,0,,{}\n".format(
            f"{ass_timestamp(start)},{ass_timestamp(end)}", body,
        ))
    return "".join(lines)


def build_karaoke_ass(
    segments: list[dict],
    clip_start: float,
    clip_end: float,
    font: str = "Helvetica",
    max_words: int = 4,
    max_chars: int = 24,
) -> str:
    """TikTok-style captions: 2-4 words per event, words light up as spoken."""
    return _render_karaoke(_words_in_range(segments, clip_start, clip_end), font, max_words, max_chars)


def build_karaoke_ass_montage(
    segments: list[dict],
    parts: list[tuple[float, float]],
    font: str = "Helvetica",
    max_words: int = 4,
    max_chars: int = 24,
) -> str:
    """Karaoke subtitles over concatenated parts; timestamps run continuously."""
    shifted: list[dict] = []
    offset = 0.0
    for part_start, part_end in parts:
        for word in _words_in_range(segments, part_start, part_end):
            shifted.append({
                "start": word["start"] + offset,
                "end": word["end"] + offset,
                "text": word["text"],
            })
        offset += max(0.2, part_end - part_start)
    total = offset
    if not shifted:
        return HEADER.format(font=font)
    return _render_karaoke(shifted, font, max_words, max_chars)
