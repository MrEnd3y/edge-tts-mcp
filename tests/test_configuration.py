"""Environment defaults, request precedence, and independent time/clip limits."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from edge_tts_mcp.config import Settings, settings_from_environment
from edge_tts_mcp.core import cli, server, service
from edge_tts_mcp.core.server import create_server
from edge_tts_mcp.core.service import synthesize

NUMERIC_ENV = [
    "EDGE_TTS_TEMPO_MIN",
    "EDGE_TTS_TEMPO_MAX",
    "EDGE_TTS_TIMEOUT_SECONDS",
    "EDGE_TTS_MAX_AUDIO_DURATION_SECONDS",
]
RETRY_ENV = [
    "EDGE_TTS_RETRY_ATTEMPTS",
    "EDGE_TTS_RETRY_INITIAL_DELAY_SECONDS",
    "EDGE_TTS_RETRY_MAX_DELAY_SECONDS",
]


@pytest.fixture(autouse=True)
def clear_numeric_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NUMERIC_ENV + RETRY_ENV:
        monkeypatch.delenv(name, raising=False)


def mock_save(monkeypatch: pytest.MonkeyPatch, mp3_bytes: bytes) -> None:
    async def save(self: object, filename: str) -> None:
        Path(filename).write_bytes(mp3_bytes)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)


def test_builtin_defaults_without_dotenv(tmp_path: Path) -> None:
    settings = settings_from_environment(output_dir=tmp_path)
    assert (settings.tempo_min, settings.tempo_max) == (0.5, 2.0)
    assert settings.timeout == 45 and settings.max_audio_duration_seconds == 300


def test_env_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values = dict(zip(NUMERIC_ENV, ["0.7", "1.4", "12", "600"]))
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = settings_from_environment(output_dir=tmp_path)
    assert (settings.tempo_min, settings.tempo_max) == (0.7, 1.4)
    assert settings.timeout == 12 and settings.max_audio_duration_seconds == 600
    assert settings.retry_attempts == 5


def test_retry_env_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EDGE_TTS_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("EDGE_TTS_RETRY_INITIAL_DELAY_SECONDS", "1.5")
    monkeypatch.setenv("EDGE_TTS_RETRY_MAX_DELAY_SECONDS", "6")
    settings = settings_from_environment(output_dir=tmp_path)
    assert settings.retry_attempts == 3
    assert settings.retry_initial_delay_seconds == 1.5
    assert settings.retry_max_delay_seconds == 6


@pytest.mark.parametrize("name", NUMERIC_ENV)
@pytest.mark.parametrize("value", ["", "private-invalid-value", "NaN", "inf", "-inf", "0", "-1"])
def test_invalid_numeric_env_is_named_and_not_echoed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name) as exc:
        settings_from_environment(output_dir=tmp_path)
    assert "private-invalid-value" not in str(exc.value)


@pytest.mark.parametrize(
    "name,value",
    [
        ("EDGE_TTS_RETRY_ATTEMPTS", "0"),
        ("EDGE_TTS_RETRY_ATTEMPTS", "6"),
        ("EDGE_TTS_RETRY_ATTEMPTS", "2.5"),
        ("EDGE_TTS_RETRY_INITIAL_DELAY_SECONDS", "-1"),
        ("EDGE_TTS_RETRY_INITIAL_DELAY_SECONDS", "NaN"),
        ("EDGE_TTS_RETRY_MAX_DELAY_SECONDS", "61"),
        ("EDGE_TTS_RETRY_MAX_DELAY_SECONDS", "1"),
    ],
)
def test_invalid_retry_env_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        settings_from_environment(output_dir=tmp_path)


@pytest.mark.parametrize(
    "name,value",
    [
        ("EDGE_TTS_TEMPO_MIN", "0.49"),
        ("EDGE_TTS_TEMPO_MAX", "2.01"),
        ("EDGE_TTS_TIMEOUT_SECONDS", "301"),
    ],
)
def test_env_cannot_override_technical_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        settings_from_environment(output_dir=tmp_path)


def test_reversed_env_bounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EDGE_TTS_TEMPO_MIN", "1.4")
    monkeypatch.setenv("EDGE_TTS_TEMPO_MAX", "1.3")
    with pytest.raises(ValueError, match="EDGE_TTS_TEMPO_MIN must not exceed EDGE_TTS_TEMPO_MAX"):
        settings_from_environment(output_dir=tmp_path)


def test_bad_config_cli_is_safe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("EDGE_TTS_TIMEOUT_SECONDS", "private-invalid-value")
    monkeypatch.setattr(sys, "argv", ["edge-tts-mcp"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert not output.out and "EDGE_TTS_TIMEOUT_SECONDS" in output.err
    assert "Traceback" not in output.err and "private-invalid-value" not in output.err


async def test_mcp_schema_and_calls_use_env_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in zip(NUMERIC_ENV, ["0.7", "1.3", "60", "600"]):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
    settings = settings_from_environment(output_dir=tmp_path)
    synth = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(server, "synthesize", synth)
    mcp = create_server(settings)
    schema = next(tool for tool in await mcp.list_tools() if tool.name == "tts").inputSchema
    properties = schema["properties"]
    for key, value in {
        "tempo_min": 0.7,
        "tempo_max": 1.3,
        "timeout": 60,
        "max_audio_duration_seconds": 600,
    }.items():
        assert properties[key]["default"] == value
    await mcp.call_tool("tts", {"text": "hi"})
    args = synth.call_args.args
    assert args[7] == 60 and args[-3:] == (0.7, 1.3, 600)
    await mcp.call_tool(
        "tts",
        {
            "text": "hi",
            "voice": "ru-RU-SvetlanaNeural",
            "timeout": 10,
            "tempo_min": 0.9,
            "tempo_max": 1.1,
            "max_audio_duration_seconds": 30,
        },
    )
    args = synth.call_args.args
    assert args[2] == "ru-RU-SvetlanaNeural" and args[7] == 10
    assert args[-3:] == (0.9, 1.1, 30)
    _, caps = await mcp.call_tool("get_capabilities", {})
    assert caps["duration_fitting"]["tempo_range"] == [0.5, 2.0]
    assert caps["duration_fitting"]["default_tempo_range"] == [0.7, 1.3]
    assert caps["timeout_seconds"] == {"default": 60, "maximum": 300}
    assert caps["max_audio_duration_seconds"] == 600
    assert caps["retry_policy"]["max_attempts"] == 5
    assert caps["retry_policy"]["initial_delay_seconds"] == 2
    assert caps["retry_policy"]["max_delay_seconds"] == 10
    assert caps["retry_policy"]["retryable_failures"] == [
        "no_audio",
        "network_error",
        "timeout",
        "http_429",
        "http_5xx",
    ]
    assert caps["retry_policy"] == {
        "max_attempts": 5,
        "initial_delay_seconds": 2,
        "max_delay_seconds": 10,
        "retryable_failures": ["no_audio", "network_error", "timeout", "http_429", "http_5xx"],
        "uses_request_timeout": True,
        "unreachable_duration_is_retried": False,
    }


async def test_request_bounds_override_env_without_automatic_widening(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    mock_save(monkeypatch, mp3_bytes)
    monkeypatch.setenv("EDGE_TTS_TEMPO_MIN", "0.99")
    monkeypatch.setenv("EDGE_TTS_TEMPO_MAX", "1.01")
    configured = settings_from_environment(output_dir=settings.output_dir, ffmpeg=settings.ffmpeg)
    monkeypatch.setattr(
        service,
        "validate_audio",
        lambda path, fmt: (len(mp3_bytes), 10 if path.name == "speech.mp3" else 9),
    )
    encode = AsyncMock()
    monkeypatch.setattr(service, "adjust_tempo", encode)
    result = await synthesize(configured, "hi", target_duration_seconds=9)
    assert result["error_code"] == "target_duration_unreachable"
    assert result["tempo_min"] == 0.99 and result["tempo_max"] == 1.01
    assert result["source_duration"] == 10 and result["target_duration"] == 9
    assert result["required_tempo"] == pytest.approx(10 / 9)
    encode.assert_not_called()

    async def process(
        executable: str, source: Path, destination: Path, fmt: str, tempo: float
    ) -> None:
        destination.write_bytes(mp3_bytes)

    monkeypatch.setattr(service, "adjust_tempo", process)
    monkeypatch.setattr(service, "find_ffprobe", lambda executable: "ffprobe")
    monkeypatch.setattr(service, "probe_duration", AsyncMock(return_value=9))
    result = await synthesize(configured, "hi", target_duration_seconds=9, tempo_max=1.2)
    assert result["duration_fitting"]["target_met"]
    assert result["duration_seconds"] == 9 and result["tempo"] == pytest.approx(10 / 9)
    assert configured.tempo_max == 1.01  # no persistent widening


@pytest.mark.parametrize(
    "limit,explicit,success", [(0.1, None, False), (0.1, 10, True), (10, 0.1, False)]
)
async def test_final_audio_limit_and_override(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    mp3_bytes: bytes,
    limit: float,
    explicit: float | None,
    success: bool,
) -> None:
    mock_save(monkeypatch, mp3_bytes)
    result = await synthesize(
        replace(settings, max_audio_duration_seconds=limit),
        "hi",
        max_audio_duration_seconds=explicit,
    )
    assert result["success"] == success
    if not success:
        assert result["error_code"] == "audio_duration_exceeded"
        assert result["actual_duration_seconds"] > result["max_audio_duration_seconds"]
        assert list(settings.output_dir.iterdir()) == []


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "10"])
async def test_invalid_audio_limit(settings: Settings, value: object) -> None:
    result = await synthesize(settings, "hi", max_audio_duration_seconds=value)
    assert result["error_code"] == "invalid_input" and not settings.output_dir.exists()


async def test_target_limit_not_timeout_limit(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    mock_save(monkeypatch, mp3_bytes)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 600.0))
    monkeypatch.setattr(service, "find_ffprobe", lambda executable: "ffprobe")
    monkeypatch.setattr(service, "probe_duration", AsyncMock(return_value=600.0))
    result = await synthesize(
        replace(settings, max_audio_duration_seconds=900, timeout=1),
        "hi",
        target_duration_seconds=600,
    )
    assert result["success"] and result["duration_seconds"] == 600
    result = await synthesize(settings, "hi", timeout=300, target_duration_seconds=600)
    assert result["error_code"] == "invalid_input"  # timeout does not increase clip limit


@pytest.mark.parametrize("explicit", [None, 0.03])
async def test_env_timeout_and_request_override(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, explicit: float | None
) -> None:
    async def save(self: object, filename: str) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)
    configured = replace(settings, timeout=0.03 if explicit is None else 20)
    result = await synthesize(configured, "hi", timeout=explicit)
    assert result["error_code"] == "timeout"
    assert list(settings.output_dir.iterdir()) == []
