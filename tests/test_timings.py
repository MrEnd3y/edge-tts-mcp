from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_tts_mcp.timings import TimingError, Word, read_words, render_subtitles


def test_word_events_to_milliseconds(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    events = [
        {"type": "SentenceBoundary", "text": "ignored"},
        {"type": "WordBoundary", "text": "Привет", "offset": 1_000_000, "duration": 2_000_000},
        {"type": "WordBoundary", "text": "&amp; мир", "offset": 3_000_000, "duration": 2_000_000},
    ]
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    assert [word.as_dict() for word in read_words(path)] == [
        {"word": "Привет", "start_ms": 100, "end_ms": 300},
        {"word": "& мир", "start_ms": 300, "end_ms": 500},
    ]


@pytest.mark.parametrize(
    "events",
    [
        [],
        [{"type": "SentenceBoundary"}],
        [{"type": "WordBoundary"}],
        [{"type": "WordBoundary", "text": "a", "offset": -1, "duration": 10000}],
        [{"type": "WordBoundary", "text": "a", "offset": 0, "duration": 0}],
        [{"type": "WordBoundary", "text": "a", "offset": 0.1, "duration": 10000}],
        [
            {"type": "WordBoundary", "text": "a", "offset": 0, "duration": 20000},
            {"type": "WordBoundary", "text": "b", "offset": 10000, "duration": 20000},
        ],
    ],
)
def test_invalid_events_refused(tmp_path: Path, events: list) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    with pytest.raises(TimingError):
        read_words(path)


def test_subtitle_formats_and_safe_markup() -> None:
    words = [Word("Привет", 100, 300), Word("мир!", 300, 500), Word("<b>&\n", 800, 1000)]
    assert render_subtitles(words, "srt") == (
        "1\n00:00:00,100 --> 00:00:00,500\nПривет мир!\n\n"
        "2\n00:00:00,800 --> 00:00:01,000\n&lt;b&gt;&amp;\n"
    )


def test_vtt_and_grouping_limits() -> None:
    words = [Word("你好", 0, 200), Word("世界。", 200, 500), Word("Hello", 2000, 2500)]
    vtt = render_subtitles(words, "vtt")
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:00.500\n你好世界。")
    assert "2\n00:00:02.000 --> 00:00:02.500" in vtt


def test_long_pause_starts_new_cue() -> None:
    srt = render_subtitles([Word("one", 0, 100), Word("two", 1000, 1200)], "srt")
    assert "\n2\n" in srt
