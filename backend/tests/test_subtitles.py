from app.services.subtitle_service import ass_timestamp, build_ass, build_karaoke_ass, escape_ass


def test_ass_timestamp() -> None:
    assert ass_timestamp(0) == "0:00:00.00"
    assert ass_timestamp(3723.9) == "1:02:03.90"
    assert ass_timestamp(-5) == "0:00:00.00"


def test_escape_ass() -> None:
    assert escape_ass(r"a{b}\c") == r"a\{b\}\\c"


def test_build_ass_shifts_and_clamps() -> None:
    segments = [
        {"start": 0.0, "end": 5.0, "text": "before"},
        {"start": 8.0, "end": 12.0, "text": "inside {x}"},
        {"start": 20.0, "end": 25.0, "text": "after"},
    ]
    content = build_ass(segments, clip_start=6.0, clip_end=15.0)
    assert "Dialogue: 0,0:00:02.00,0:00:06.00,Clip,,0,0,0,,inside \\{x\\}\n" in content
    assert "before" not in content.split("[Events]", 1)[-1]
    assert "after" not in content.split("[Events]", 1)[-1]


def test_build_ass_skips_bad_segments() -> None:
    segments = [
        {"start": 1.0, "end": 2.0, "text": "  "},
        {"start": "bad", "end": 2.0, "text": "broken"},
    ]
    content = build_ass(segments, clip_start=0.0, clip_end=10.0)
    assert "Dialogue" not in content.split("[Events]", 1)[-1]


def test_karaoke_groups_words_and_highlights() -> None:
    segments = [{"start": 10.0, "end": 14.0, "text": "Привет как дела друг",
                 "words": [{"start": 10.0, "end": 10.4, "text": "Привет"},
                           {"start": 10.5, "end": 10.8, "text": "как"},
                           {"start": 10.9, "end": 11.3, "text": "дела"},
                           {"start": 11.4, "end": 11.8, "text": "друг"}]}]
    content = build_karaoke_ass(segments, clip_start=9.0, clip_end=20.0)
    body = content.split("[Events]", 1)[-1]
    assert "Dialogue: 0,0:00:01.00,0:00:02.80,Karaoke" in body
    assert r"{\kf40} ПРИВЕТ" in body
    assert "Dialogue" in body and body.count("Dialogue") == 1


def test_karaoke_falls_back_to_even_split_without_words() -> None:
    segments = [{"start": 5.0, "end": 9.0, "text": "раз два три четыре пять"}]
    content = build_karaoke_ass(segments, clip_start=0.0, clip_end=20.0, max_words=2)
    body = content.split("[Events]", 1)[-1]
    assert body.count("Dialogue") == 3
    assert "РАЗ" in body and r"\kf" in body


def test_karaoke_splits_long_phrases() -> None:
    segments = [{"start": 0.0, "end": 10.0, "text": "а б в г д е ж",
                 "words": [{"start": float(i), "end": float(i) + 0.5, "text": t}
                           for i, t in enumerate("а б в г д е ж".split())]}]
    content = build_karaoke_ass(segments, clip_start=0.0, clip_end=20.0, max_words=3)
    assert content.split("[Events]", 1)[-1].count("Dialogue") == 3
