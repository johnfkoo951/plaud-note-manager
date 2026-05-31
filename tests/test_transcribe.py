from core.transcribe import group_words_into_segments


def test_group_words_into_segments_splits_on_speaker_and_silence() -> None:
    words = [
        {"type": "word", "text": "안녕", "speaker_id": "speaker_0", "start": 0.0, "end": 0.3},
        {"type": "word", "text": "하세요", "speaker_id": "speaker_0", "start": 0.4, "end": 0.8},
        {"type": "word", "text": "네", "speaker_id": "speaker_1", "start": 0.9, "end": 1.1},
        {"type": "word", "text": "다시", "speaker_id": "speaker_1", "start": 3.0, "end": 3.2},
    ]

    segments = group_words_into_segments(words, max_silence_s=1.5)

    assert segments == [
        {
            "speaker": "speaker_0",
            "start_ms": 0,
            "end_ms": 800,
            "content": "안녕 하세요",
        },
        {
            "speaker": "speaker_1",
            "start_ms": 900,
            "end_ms": 1100,
            "content": "네",
        },
        {
            "speaker": "speaker_1",
            "start_ms": 3000,
            "end_ms": 3200,
            "content": "다시",
        },
    ]
