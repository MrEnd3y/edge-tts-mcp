from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock

import edge_tts
import pytest

from edge_voice_mcp import audio, service
from edge_voice_mcp.audio import validate_audio
from edge_voice_mcp.service import Settings, synthesize

VOICE = "ru-RU-SvetlanaNeural"


def mock_save(monkeypatch: pytest.MonkeyPatch, content: bytes) -> None:
    async def save(self: object, filename: str) -> None:
        Path(filename).write_bytes(content)

    # Retain the real Communicate constructor to test its parameter validation.
    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)


@pytest.mark.parametrize("audio_format", ["mp3", "ogg"])
async def test_valid_audio(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, audio_format: str
) -> None:
    mock_save(monkeypatch, mp3_bytes)
    result = await synthesize(settings, "Привет!", VOICE, audio_format=audio_format)
    assert result["success"] is True, result
    path = Path(result["path"])
    assert result["audio_path"] == str(path)
    assert path.suffix == f".{audio_format}"
    assert result["duration_seconds"] > 0
    assert result["size"] == path.stat().st_size
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    validate_audio(path, audio_format)
    assert list(settings.output_dir.iterdir()) == [path]


@pytest.mark.parametrize("content", [b"", b"not audio"])
async def test_invalid_audio_is_cleaned(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, content: bytes
) -> None:
    mock_save(monkeypatch, content)
    result = await synthesize(settings, "hi", VOICE)
    assert result["error_code"] == "audio_error"
    assert list(settings.output_dir.iterdir()) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": " "},
        {"text": "x" * (service.MAX_TEXT_LENGTH + 1)},
        {"voice": ""},
        {"rate": "fast"},
        {"pitch": "10%"},
        {"volume": "loud"},
        {"timeout": 0},
        {"timeout": 301},
        {"audio_format": "wav"},
    ],
)
async def test_invalid_input(settings: Settings, kwargs: dict) -> None:
    args = {"text": "hi", "voice": VOICE, **kwargs}
    result = await synthesize(settings, **args)
    assert result["error_code"] == "invalid_input"
    assert not settings.output_dir.exists()


async def test_timeout_cleans_partial_file(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    async def save(self: object, filename: str) -> None:
        Path(filename).write_bytes(b"partial")
        await asyncio.sleep(10)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    result = await synthesize(settings, "hi", VOICE, timeout=0.03)
    assert result["error_code"] == "timeout"
    assert list(settings.output_dir.iterdir()) == []


async def test_cancellation_propagates_and_cleans(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    started = asyncio.Event()

    async def save(self: object, filename: str) -> None:
        Path(filename).write_bytes(b"partial")
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    task = asyncio.create_task(synthesize(settings, "hi", VOICE))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(settings.output_dir.iterdir()) == []


@pytest.mark.parametrize(
    "error, code",
    [
        (edge_tts.exceptions.NoAudioReceived("private server details"), "no_audio"),
        (RuntimeError("private server details"), "service_error"),
        (PermissionError("private server details"), "io_error"),
    ],
)
async def test_safe_errors(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, error: Exception, code: str
) -> None:
    monkeypatch.setattr(service.edge_tts.Communicate, "save", AsyncMock(side_effect=error))
    result = await synthesize(settings, "hi", VOICE)
    assert result["error_code"] == code
    assert "private" not in str(result)
    assert list(settings.output_dir.iterdir()) == []


async def test_missing_ffmpeg_fails_before_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    save = AsyncMock()
    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    result = await synthesize(
        Settings(tmp_path, "missing-ffmpeg-command"), "hi", VOICE, audio_format="ogg"
    )
    assert result["error_code"] == "audio_error"
    save.assert_not_called()


async def test_concurrent_unique_files(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    mock_save(monkeypatch, mp3_bytes)
    results = await asyncio.gather(*(synthesize(settings, "hi", VOICE) for _ in range(5)))
    assert all(r["success"] for r in results)
    assert len({r["path"] for r in results}) == 5
    assert len(list(settings.output_dir.iterdir())) == 5


async def test_ffmpeg_killed_on_cancel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = False

        async def wait(self) -> int:
            if self.killed:
                self.returncode = -9
                return -9
            started.set()
            await asyncio.sleep(10)
            return 0

        def kill(self) -> None:
            self.killed = True

    process = Process()
    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(
        audio.convert_to_opus("ffmpeg", tmp_path / "a.mp3", tmp_path / "a.ogg")
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed
    assert process.returncode == -9


async def test_conversion_failure_cleans_everything(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    mock_save(monkeypatch, mp3_bytes)

    async def broken_conversion(ffmpeg: str, source: Path, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise audio.AudioError("Conversion failed")

    monkeypatch.setattr(service, "convert_to_opus", broken_conversion)
    result = await synthesize(settings, "hi", VOICE, audio_format="ogg")
    assert result["error_code"] == "audio_error"
    assert list(settings.output_dir.iterdir()) == []


async def test_timeout_includes_conversion(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    mock_save(monkeypatch, mp3_bytes)

    async def slow_conversion(ffmpeg: str, source: Path, destination: Path) -> None:
        destination.write_bytes(b"partial")
        await asyncio.sleep(10)

    monkeypatch.setattr(service, "convert_to_opus", slow_conversion)
    result = await synthesize(settings, "hi", VOICE, audio_format="ogg", timeout=0.05)
    assert result["error_code"] == "timeout"
    assert list(settings.output_dir.iterdir()) == []


async def test_output_dir_not_writable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("existing file", encoding="utf-8")
    result = await synthesize(Settings(blocker), "hi", VOICE)
    assert result["error_code"] == "io_error"
    assert blocker.read_text(encoding="utf-8") == "existing file"
