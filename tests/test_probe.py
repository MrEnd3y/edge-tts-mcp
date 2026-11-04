"""FFprobe is local, shell-free, deadline-bound, and never leaks output."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from edge_tts_mcp.core import audio, service
from edge_tts_mcp.core.server import create_server, settings_from_environment
from edge_tts_mcp.core.service import Settings, synthesize


@pytest.mark.parametrize(
    "output,code",
    [
        (b"12.345\n", 0),
        (b"nan", 0),
        (b"inf", 0),
        (b"0", 0),
        (b"-1", 0),
        (b"N/A", 0),
        (b"private details", 1),
    ],
)
async def test_probe_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output: bytes, code: int
) -> None:
    process = Mock(returncode=code, communicate=AsyncMock(return_value=(output, None)))
    execute = AsyncMock(return_value=process)
    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", execute)
    monkeypatch.setattr(
        audio.asyncio, "create_subprocess_shell", AsyncMock(side_effect=AssertionError("shell"))
    )
    path = tmp_path / "audio.mp3"
    if output == b"12.345\n":
        assert await audio.probe_duration("ffprobe", path) == 12.345
    else:
        with pytest.raises(audio.AudioError, match="FFprobe could not verify") as exc:
            await audio.probe_duration("ffprobe", path)
        assert "private" not in str(exc.value)
    args, kwargs = execute.call_args
    assert args[0] == "ffprobe" and args[-1] == str(path)
    assert "format=duration" in args and "shell" not in kwargs
    assert kwargs["stderr"] == asyncio.subprocess.DEVNULL


async def test_probe_launch_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        audio.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("private"))
    )
    with pytest.raises(audio.AudioError, match="FFprobe could not verify") as exc:
        await audio.probe_duration("ffprobe", tmp_path / "audio.mp3")
    assert "private" not in str(exc.value)


async def test_probe_failure_never_reports_success(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    async def save(self: object, filename: str) -> None:
        Path(filename).write_bytes(mp3_bytes)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    monkeypatch.setattr(service, "find_ffprobe", lambda executable: "ffprobe")
    monkeypatch.setattr(
        service,
        "probe_duration",
        AsyncMock(side_effect=audio.AudioError("FFprobe could not verify audio duration")),
    )
    result = await synthesize(settings, "hi", target_duration_seconds=10)
    assert result["error_code"] == "audio_error" and not result["success"]
    assert "path" not in result and list(settings.output_dir.iterdir()) == []


def test_probe_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EDGE_TTS_FFPROBE", "custom-ffprobe")
    assert settings_from_environment(output_dir=tmp_path).ffprobe == "custom-ffprobe"
    assert settings_from_environment(output_dir=tmp_path, ffprobe="override").ffprobe == "override"
    with pytest.raises(ValueError, match="FFprobe executable"):
        Settings(output_dir=tmp_path, ffprobe=" ")


async def test_range_capabilities(settings: Settings) -> None:
    _, result = await create_server(settings).call_tool("get_capabilities", {})
    assert result["duration_fitting"]["tempo_range"] == [0.5, 2.0]
    assert result["duration_fitting"]["default_tempo_range"] == [0.5, 2.0]
    assert result["duration_fitting"]["requires"] == ["ffmpeg", "ffprobe"]


@pytest.mark.parametrize("cancel", [False, True])
async def test_probe_timeout_and_cancel_in_service(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, cancel: bool
) -> None:
    async def save(self: object, filename: str) -> None:
        Path(filename).write_bytes(mp3_bytes)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    monkeypatch.setattr(service, "find_ffprobe", lambda executable: "ffprobe")
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = False
        reaped = False

        async def communicate(self) -> tuple[bytes, None]:
            started.set()
            await asyncio.Event().wait()
            return b"10", None

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.reaped = True
            self.returncode = -9
            return -9

    process = Process()
    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(
        synthesize(settings, "hi", target_duration_seconds=10, timeout=2 if cancel else 0.05)
    )
    await asyncio.wait_for(started.wait(), 2)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task)["error_code"] == "timeout"
    assert process.killed and process.reaped
    assert list(settings.output_dir.iterdir()) == []
