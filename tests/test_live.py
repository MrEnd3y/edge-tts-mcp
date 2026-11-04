"""Explicitly enabled network tests. Only fixed public sample text is transmitted."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from edge_tts_mcp.server import create_server
from edge_tts_mcp.service import Settings, synthesize

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("EDGE_TTS_LIVE_TESTS") != "1", reason="Network tests disabled"
    ),
]


@pytest.mark.parametrize(
    "voice,audio_format",
    [
        ("ru-RU-SvetlanaNeural", "mp3"),
        ("ru-RU-DmitryNeural", "ogg"),
        ("en-US-AriaNeural", "mp3"),
    ],
)
async def test_live_speech(settings: Settings, voice: str, audio_format: str) -> None:
    text = "Проверка синтеза речи." if voice.startswith("ru-") else "Speech synthesis test."
    result = await synthesize(settings, text, voice, audio_format=audio_format)
    assert result["success"], result
    assert result["duration_seconds"] > 0


async def test_live_voices(settings: Settings) -> None:
    _content, result = await create_server(settings).call_tool("list_voice", {"locale": "ru-RU"})
    names = {v["ShortName"] for v in result["result"]}
    assert "ru-RU-SvetlanaNeural" in names


@pytest.mark.parametrize("subtitle_format,audio_format", [("srt", "mp3"), ("vtt", "ogg")])
async def test_live_subtitles_and_word_timings(
    settings: Settings, subtitle_format: str, audio_format: str
) -> None:
    result = await synthesize(
        settings,
        "Привет! Сегодня мы проверяем субтитры и метки слов.",
        audio_format=audio_format,
        subtitles=subtitle_format,
        word_timings=True,
    )
    assert result["success"], result
    assert not result.get("warnings"), result
    words = json.loads(Path(result["word_timings_path"]).read_text(encoding="utf-8"))
    assert len(words) >= 5
    assert all(0 <= word["start_ms"] < word["end_ms"] for word in words)
    assert all(left["end_ms"] <= right["start_ms"] for left, right in zip(words, words[1:]))
    assert words[-1]["end_ms"] <= result["duration_seconds"] * 1000 + 500
    subtitle = Path(result["subtitle_path"]).read_text(encoding="utf-8")
    assert "-->" in subtitle and "Привет" in subtitle
    assert subtitle.startswith("WEBVTT") == (subtitle_format == "vtt")


async def test_live_long_text_offsets(settings: Settings) -> None:
    # Exceeds both the old 5000-character cap and edge-tts' 4096-byte chunks.
    text = "Проверяем длинный текст и сквозные метки слов. " * 120
    assert len(text) > 5000
    # A normal-rate version exceeded 180 seconds in local validation. Exercise
    # the same multi-chunk text with an explicit fast rate and larger deadline.
    result = await synthesize(
        settings, text, rate="+100%", subtitles="srt", word_timings=True, timeout=300
    )
    assert result["success"] and not result.get("warnings"), result
    words = json.loads(Path(result["word_timings_path"]).read_text(encoding="utf-8"))
    assert len(words) >= 700
    assert all(left["end_ms"] <= right["start_ms"] for left, right in zip(words, words[1:]))
    assert words[-1]["end_ms"] > 60_000
    assert words[-1]["end_ms"] <= result["duration_seconds"] * 1000 + 500
