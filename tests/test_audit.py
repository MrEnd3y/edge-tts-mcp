"""Regression cases found during the pre-publication code review."""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from edge_tts_mcp import audio, cli, server, service
from edge_tts_mcp.service import Settings, synthesize


async def test_postprocessing_deadline_does_not_publish_success(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    async def save(self: object, filename: str) -> None:
        Path(filename).write_bytes(mp3_bytes)

    original = service.validate_audio

    def slow_validation(path: Path, audio_format: str) -> tuple[int, float]:
        time.sleep(0.04)
        return original(path, audio_format)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    monkeypatch.setattr(service, "validate_audio", slow_validation)
    result = await synthesize(settings, "hello", timeout=0.01)
    assert result["success"] is False and result["error_code"] == "timeout"
    assert list(settings.output_dir.iterdir()) == []


async def test_cancel_requested_at_end_of_save_does_not_publish(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    async def save(self: object, filename: str) -> None:
        Path(filename).write_bytes(mp3_bytes)
        asyncio.current_task().cancel()

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    task = asyncio.create_task(synthesize(settings, "hello"))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(settings.output_dir.iterdir()) == []


async def test_repeated_cancellation_still_reaps_encoder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    running, reaping, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Process:
        returncode = None
        killed = False
        reaped = False

        async def wait(self) -> int:
            if not self.killed:
                running.set()
                await asyncio.Event().wait()
            reaping.set()
            await finish.wait()
            self.reaped = True
            self.returncode = -9
            return -9

        def kill(self) -> None:
            self.killed = True

    process = Process()
    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(
        audio.convert_to_opus("ffmpeg", tmp_path / "a.mp3", tmp_path / "a.ogg")
    )
    await asyncio.wait_for(running.wait(), 2)
    task.cancel()
    await asyncio.wait_for(reaping.wait(), 2)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.reaped


def test_invalid_sidecar_environment_is_not_silently_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGE_TTS_ALLOW_SIDECARS", "flase")
    with pytest.raises(ValueError, match="EDGE_TTS_ALLOW_SIDECARS"):
        server.settings_from_environment()


async def test_catalog_search_matches_values_not_field_names(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(
        server.edge_tts,
        "list_voices",
        AsyncMock(
            return_value=[
                {"ShortName": "en-US-AriaNeural", "Gender": "Female", "Locale": "en-US"},
            ]
        ),
    )
    _text, result = await server.create_server(settings).call_tool(
        "list_voice", {"query": "ShortName"}
    )
    assert result["result"] == []


@pytest.mark.parametrize(
    "catalog", [None, [{}], ["invalid"], [{"ShortName": 42, "Locale": "en-US", "Gender": "Female"}]]
)
async def test_malformed_catalog_is_a_safe_tool_error(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, catalog: object
) -> None:
    monkeypatch.setattr(server.edge_tts, "list_voices", AsyncMock(return_value=catalog))
    with pytest.raises(Exception, match="Cannot retrieve Microsoft voices"):
        await server.create_server(settings).call_tool("list_voice", {})


async def test_invalid_optional_voice_tags_are_ignored(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    voice = {"ShortName": "en-US-AriaNeural", "Locale": "en-US", "Gender": "Female"}
    monkeypatch.setattr(
        server.edge_tts,
        "list_voices",
        AsyncMock(
            return_value=[
                {**voice, "VoiceTag": "invalid", "FriendlyName": None},
            ]
        ),
    )
    _text, result = await server.create_server(settings).call_tool("list_voice", {})
    assert result["result"] == [voice]


@pytest.mark.parametrize(
    "name,value",
    [
        ("EDGE_TTS_VOICE", ""),
        ("EDGE_TTS_RATE", "fast"),
        ("EDGE_TTS_FFMPEG", " "),
        ("EDGE_TTS_OUTPUT_DIR", ""),
    ],
)
def test_invalid_defaults_fail_before_server_starts(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        server.settings_from_environment()


def test_cli_overrides_apply_before_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EDGE_TTS_VOICE", "")
    monkeypatch.setenv("EDGE_TTS_ALLOW_SIDECARS", "misspelled")
    settings = server.settings_from_environment(
        voice="en-US-AriaNeural", output_dir=tmp_path, disable_sidecars=True
    )
    assert settings.voice == "en-US-AriaNeural" and settings.allow_sidecars is False


def test_cli_config_error_has_no_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("EDGE_TTS_ALLOW_SIDECARS", "invalid-private-details")
    monkeypatch.setattr(sys, "argv", ["edge-tts-mcp"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert not output.out and "EDGE_TTS_ALLOW_SIDECARS" in output.err
    assert "Traceback" not in output.err and "private-details" not in output.err


def test_cli_dispatches_stdio_with_explicit_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    instance = Mock()
    factory = Mock(return_value=instance)
    monkeypatch.setattr(cli, "create_server", factory)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "edge-tts-mcp",
            "--output-dir",
            str(tmp_path),
            "--voice",
            "en-US-AriaNeural",
            "--disable-sidecars",
        ],
    )
    cli.main()
    settings = factory.call_args.args[0]
    assert settings.output_dir == tmp_path and settings.voice == "en-US-AriaNeural"
    assert settings.allow_sidecars is False
    instance.run.assert_called_once_with(transport="stdio")


async def test_encoder_failure_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = Mock(returncode=1, wait=AsyncMock(return_value=1))
    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(audio.AudioError, match="conversion failed"):
        await audio.convert_to_opus("ffmpeg", tmp_path / "a.mp3", tmp_path / "a.ogg")


async def test_encoder_reaping_has_its_own_bounded_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    started = asyncio.Event()

    class Process:
        returncode = None

        async def wait(self) -> int:
            started.set()
            await asyncio.Event().wait()
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(audio, "PROCESS_CLEANUP_TIMEOUT", 0.01)
    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", AsyncMock(return_value=Process()))
    task = asyncio.create_task(
        audio.convert_to_opus("ffmpeg", tmp_path / "a.mp3", tmp_path / "a.ogg")
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert "bounded wait" in caplog.text


async def test_server_limits_parallel_synthesis(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    active = peak = 0

    async def save(self: object, filename: str) -> None:
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        try:
            await asyncio.sleep(0.02)
            Path(filename).write_bytes(mp3_bytes)
        finally:
            active -= 1

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    instance = server.create_server(replace(settings, max_concurrent=1))
    results = await asyncio.gather(
        *(instance.call_tool("tts", {"text": "hello"}) for _ in range(3))
    )
    assert all(result[1]["success"] for result in results)
    assert peak == 1


async def test_waiting_for_slot_uses_the_same_deadline(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def save(self: object, filename: str) -> None:
        started.set()
        await finish.wait()
        Path(filename).write_bytes(mp3_bytes)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    instance = server.create_server(replace(settings, max_concurrent=1))
    first = asyncio.create_task(instance.call_tool("tts", {"text": "hello"}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        _text, result = await instance.call_tool("tts", {"text": "queued", "timeout": 0.01})
        assert result["error_code"] == "timeout"
    finally:
        finish.set()
        await first
    assert len(list(settings.output_dir.iterdir())) == 1


@pytest.mark.parametrize("value", ["zero", "0", "17"])
def test_invalid_concurrency_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("EDGE_TTS_MAX_CONCURRENT", value)
    with pytest.raises(ValueError, match="integer between 1 and 16"):
        server.settings_from_environment()
