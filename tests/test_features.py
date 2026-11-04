from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from edge_tts_mcp.core import server, service
from edge_tts_mcp.core.server import create_server, settings_from_environment
from edge_tts_mcp.core.service import Settings, synthesize


def metadata_save(monkeypatch: pytest.MonkeyPatch, mp3_bytes: bytes, events: bool = True) -> None:
    async def save(self: object, filename: str, metadata_filename: str | None = None) -> None:
        Path(filename).write_bytes(mp3_bytes)
        if metadata_filename is not None:
            assert self.tts_config.boundary == "WordBoundary"
            records = (
                [
                    {"type": "WordBoundary", "text": "Привет", "offset": 0, "duration": 1_000_000},
                    {
                        "type": "WordBoundary",
                        "text": "мир!",
                        "offset": 1_100_000,
                        "duration": 1_000_000,
                    },
                ]
                if events
                else []
            )
            Path(metadata_filename).write_text(
                "\n".join(json.dumps(r) for r in records), encoding="utf-8"
            )

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)


@pytest.mark.parametrize(
    "subtitle_format,word_timings",
    [
        ("none", True),
        ("srt", False),
        ("srt", True),
        ("vtt", True),
    ],
)
async def test_audio_with_requested_sidecars(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    mp3_bytes: bytes,
    subtitle_format: str,
    word_timings: bool,
) -> None:
    metadata_save(monkeypatch, mp3_bytes)
    result = await synthesize(
        settings, "Привет мир!", subtitles=subtitle_format, word_timings=word_timings
    )
    assert result["success"], result
    assert result["path"] == result["audio_path"]
    assert result["format"] == "mp3" and result["mime_type"] == "audio/mpeg"
    audio = Path(result["audio_path"])
    assert audio.read_bytes() == mp3_bytes
    assert audio.stat().st_size == result["size"]
    assert result["duration_seconds"] > 0
    assert ("word_timings_path" in result) == word_timings
    assert ("subtitle_path" in result) == (subtitle_format != "none")
    if word_timings:
        words = json.loads(Path(result["word_timings_path"]).read_text(encoding="utf-8"))
        assert words[1] == {"word": "мир!", "start_ms": 110, "end_ms": 210}
        assert result["word_count"] == 2
    if subtitle_format != "none":
        content = Path(result["subtitle_path"]).read_text(encoding="utf-8")
        assert "Привет мир!" in content
    assert len(list(settings.output_dir.iterdir())) == 1 + int(word_timings) + int(
        subtitle_format != "none"
    )


async def test_default_is_audio_only(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    # This mock accepts only one file argument, proving metadata is not requested.
    async def save(self: object, filename: str) -> None:
        assert self.tts_config.boundary == "SentenceBoundary"
        Path(filename).write_bytes(mp3_bytes)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    result = await synthesize(settings, "hi")
    assert result["success"]
    assert not {"subtitle_path", "word_timings_path", "warnings"} & result.keys()
    assert len(list(settings.output_dir.iterdir())) == 1


async def test_missing_metadata_is_explicit_warning(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    metadata_save(monkeypatch, mp3_bytes, events=False)
    result = await synthesize(settings, "hi", subtitles="srt", word_timings=True)
    assert result["success"]
    assert result["warnings"][0]["code"] == "timing_metadata_unavailable"
    assert "subtitle_path" not in result and "word_timings_path" not in result
    assert len(list(settings.output_dir.iterdir())) == 1


async def test_disabled_sidecars_no_network(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    save = AsyncMock()
    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    result = await synthesize(replace(settings, allow_sidecars=False), "hi", subtitles="vtt")
    assert result["error_code"] == "feature_disabled"
    save.assert_not_called()


async def test_long_unicode_text_uses_library_chunking(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    text = "Русский текст 中文 😀. " * 400

    async def save(self: object, filename: str) -> None:
        chunks = list(self.texts)
        assert len(chunks) > 1
        assert all(len(chunk) <= 4096 for chunk in chunks)
        assert all(chunk.decode("utf-8") for chunk in chunks)
        Path(filename).write_bytes(mp3_bytes)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    result = await synthesize(settings, text)
    assert result["success"], result
    assert result["characters"] == len(text) > 5000


async def test_defaults_profile_and_explicit_overrides(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    metadata_save(monkeypatch, mp3_bytes)
    configured = replace(settings, voice="en-US-AriaNeural", rate="+5%")
    result = await synthesize(configured, "hello")
    assert result["voice"] == "en-US-AriaNeural" and result["rate"] == "+5%"
    result = await synthesize(configured, "hello", profile="slow", pitch="+2Hz")
    assert result["rate"] == "-25%" and result["pitch"] == "+2Hz"
    result = await synthesize(configured, "hi", profile="notification", rate="-5%")
    assert result["rate"] == "-5%"


async def test_rich_catalog_search_and_pagination(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    voice = {
        "ShortName": "en-US-AriaNeural",
        "Gender": "Female",
        "Locale": "en-US",
        "FriendlyName": "Aria",
        "VoiceTag": {"ContentCategories": ["News"]},
    }
    monkeypatch.setattr(
        server.edge_tts,
        "list_voices",
        AsyncMock(return_value=[voice, {**voice, "ShortName": "Other"}]),
    )
    _text, result = await create_server(settings).call_tool(
        "list_voice", {"query": "news", "offset": 1, "limit": 1}
    )
    assert result["result"] == [{**voice, "ShortName": "Other"}]


async def test_capabilities_no_network(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    fetch = AsyncMock()
    monkeypatch.setattr(server.edge_tts, "list_voices", fetch)
    _text, result = await create_server(replace(settings, allow_sidecars=False)).call_tool(
        "get_capabilities", {}
    )
    assert result["default_artifacts"] == ["audio"]
    assert result["max_text_characters"] == 100_000
    assert result["subtitles"] == [] and result["word_timings"] is False
    fetch.assert_not_called()


def test_environment_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
    monkeypatch.setenv("EDGE_TTS_ALLOW_SIDECARS", "false")
    assert settings_from_environment().voice == "en-US-AriaNeural"
    assert settings_from_environment().allow_sidecars is False


@pytest.mark.parametrize("failure_point", ["write", "publish"])
async def test_sidecar_failure_rolls_back_audio(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, failure_point: str
) -> None:
    metadata_save(monkeypatch, mp3_bytes)
    original_write, original_replace = Path.write_text, Path.replace

    def write(path: Path, *args: object, **kwargs: object) -> int:
        if path.name == "words.json" and failure_point == "write":
            raise PermissionError("private details")
        return original_write(path, *args, **kwargs)

    def publish(path: Path, target: Path) -> Path:
        if str(target).endswith(".words.json") and failure_point == "publish":
            raise PermissionError("private details")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(Path, "replace", publish)
    result = await synthesize(settings, "hello", subtitles="srt", word_timings=True)
    assert result["error_code"] == "io_error"
    assert "private" not in str(result)
    assert list(settings.output_dir.iterdir()) == []


async def test_cancelled_metadata_job_leaves_no_files(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    started = asyncio.Event()

    async def save(self: object, filename: str, metadata_filename: str) -> None:
        Path(filename).write_bytes(mp3_bytes)
        Path(metadata_filename).write_text("partial", encoding="utf-8")
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    task = asyncio.create_task(synthesize(settings, "hi", subtitles="vtt", word_timings=True))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(settings.output_dir.iterdir()) == []


async def test_malformed_metadata_does_not_fabricate_timings(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    async def save(self: object, filename: str, metadata_filename: str) -> None:
        Path(filename).write_bytes(mp3_bytes)
        Path(metadata_filename).write_text("invalid json", encoding="utf-8")

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    result = await synthesize(settings, "hi", subtitles="srt", word_timings=True)
    assert result["success"] and result["warnings"]
    assert "subtitle_path" not in result and "word_timings_path" not in result


async def test_timings_beyond_audio_are_not_published(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    async def save(self: object, filename: str, metadata_filename: str) -> None:
        Path(filename).write_bytes(mp3_bytes)
        event = {
            "type": "WordBoundary",
            "text": "hi",
            "offset": 60_000_000_000,
            "duration": 1_000_000,
        }
        Path(metadata_filename).write_text(json.dumps(event), encoding="utf-8")

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    result = await synthesize(settings, "hi", subtitles="srt", word_timings=True)
    assert result["success"] and result["warnings"][0]["code"] == "timing_metadata_unavailable"
    assert "subtitle_path" not in result and "word_timings_path" not in result


@pytest.mark.parametrize("options", [{"subtitles": "ass"}, {"profile": "unknown"}])
async def test_unknown_optional_arguments(settings: Settings, options: dict) -> None:
    result = await synthesize(settings, "hi", **options)
    assert result["error_code"] == "invalid_input"
    assert not settings.output_dir.exists()
