from app.utils.timecode import seconds_to_timecode


def test_timecode() -> None:
    assert seconds_to_timecode(3723.9) == "01:02:03"
