"""Opt-in Microsoft synthesis + real local FFmpeg/FFprobe duration fitting."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from edge_tts_mcp.config import Settings
from edge_tts_mcp.core.audio import probe_duration
from edge_tts_mcp.core.service import synthesize

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("EDGE_TTS_LIVE_TESTS") != "1", reason="Network tests disabled"
    ),
]


@pytest.mark.parametrize("tempo", [0.9, 1.1])
@pytest.mark.parametrize("audio_format,subtitles", [("mp3", "srt"), ("ogg", "vtt")])
async def test_live_duration_and_rescaled_sidecars(
    settings: Settings, tempo: float, audio_format: str, subtitles: str
) -> None:
    ffprobe = shutil.which(os.environ.get("EDGE_TTS_FFPROBE", "ffprobe"))
    if ffprobe is None:
        pytest.skip("Real duration verification requires FFprobe (EDGE_TTS_FFPROBE or PATH)")
    settings = replace(settings, ffprobe=ffprobe)
    text = "Привет! Проверяем длительность речи, субтитры и метки слов."
    baseline = await synthesize(settings, text)
    assert baseline["success"], baseline
    target = await probe_duration(ffprobe, Path(baseline["path"])) / tempo
    result = await synthesize(
        settings,
        text,
        audio_format=audio_format,
        subtitles=subtitles,
        word_timings=True,
        target_duration_seconds=target,
        timeout=60,
    )
    assert result["success"] and not result.get("warnings"), result
    fitting = result["duration_fitting"]
    assert fitting["processed"] and fitting["target_met"], result
    actual = await probe_duration(ffprobe, Path(result["path"]))
    assert abs(actual - target) <= max(0.05, target * 0.01)
    assert result["duration_seconds"] == round(actual, 3)
    assert result["tempo"] == fitting["applied_tempo"]
    assert (result["tempo"] < 1) if tempo < 1 else (result["tempo"] > 1)
    words = json.loads(Path(result["word_timings_path"]).read_text(encoding="utf-8"))
    assert len(words) >= 5
    assert all(0 <= word["start_ms"] < word["end_ms"] <= round(actual * 1000) for word in words)
    assert all(left["end_ms"] <= right["start_ms"] for left, right in zip(words, words[1:]))
    subtitle = Path(result["subtitle_path"]).read_text(encoding="utf-8")
    assert "-->" in subtitle and "Привет" in subtitle
    assert subtitle.startswith("WEBVTT") == (subtitles == "vtt")
