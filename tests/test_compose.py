"""Opt-in tests of the actual Compose launcher, not equivalent docker-run flags.

Only generated nonsecret settings files are loaded, never a user's .env.
Each test owns a uniquely named project and removes only its own volume.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from edge_tts_mcp import __version__

ROOT = Path(__file__).parents[1]
DEFAULT_ENV = {
    "EDGE_TTS_OUTPUT_DIR": "/data/output",
    "EDGE_TTS_VOICE": "ru-RU-SvetlanaNeural",
    "EDGE_TTS_RATE": "+0%",
    "EDGE_TTS_VOLUME": "+0%",
    "EDGE_TTS_PITCH": "+0Hz",
    "EDGE_TTS_TEMPO_MIN": "0.5",
    "EDGE_TTS_TEMPO_MAX": "2.0",
    "EDGE_TTS_TIMEOUT_SECONDS": "45",
    "EDGE_TTS_RETRY_ATTEMPTS": "5",
    "EDGE_TTS_RETRY_INITIAL_DELAY_SECONDS": "2",
    "EDGE_TTS_RETRY_MAX_DELAY_SECONDS": "10",
    "EDGE_TTS_MAX_AUDIO_DURATION_SECONDS": "300",
    "EDGE_TTS_ALLOW_SIDECARS": "1",
    "EDGE_TTS_MAX_CONCURRENT": "2",
    "EDGE_TTS_FFMPEG": "/usr/bin/ffmpeg",
    "EDGE_TTS_FFPROBE": "/usr/bin/ffprobe",
}
pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("EDGE_TTS_DOCKER_TESTS") != "1", reason="Docker tests disabled"
    ),
]


@pytest.fixture
def compose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[list[str]]:
    # Do not inherit host executable paths or unrelated defaults into this test.
    for name in DEFAULT_ENV:
        monkeypatch.delenv(name, raising=False)
    settings_file = tmp_path / "compose-settings.txt"
    settings_file.write_text("# Generated nonsecret test settings\n", encoding="utf-8")
    command = [
        "docker",
        "compose",
        "--env-file",
        str(settings_file),
        "--file",
        str(ROOT / "compose.yaml"),
        "--project-name",
        f"edge-tts-compose-test-{uuid.uuid4().hex}",
    ]
    try:
        yield command
    finally:
        subprocess.run([*command, "down", "--volumes"], capture_output=True, timeout=45, check=True)


def set_file(command: list[str], values: dict[str, str]) -> None:
    Path(command[3]).write_text(
        "\n".join(f"{name}={value}" for name, value in values.items()) + "\n", encoding="utf-8"
    )


def service_config(command: list[str]) -> dict:
    result = subprocess.run(
        [*command, "config", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)["services"]["edge-tts-mcp"]


def stdio_parameters(command: list[str]) -> StdioServerParameters:
    # The SDK filters inherited ENV. Docker Desktop needs this nonsecret Windows
    # directory to discover its Compose plugin; never pass the full host ENV.
    environment = {}
    if os.name == "nt" and "ProgramFiles" in os.environ:
        environment["ProgramFiles"] = os.environ["ProgramFiles"]
    return StdioServerParameters(
        command=command[0],
        args=[*command[1:], "run", "--rm", "--no-deps", "-T", "edge-tts-mcp"],
        env=environment,
    )


def test_compose_defaults(compose: list[str]) -> None:
    service = service_config(compose)
    assert service["environment"] == DEFAULT_ENV
    assert service["stdin_open"] and not service.get("tty", False)
    assert service["read_only"] and service["init"]
    assert service["volumes"][0]["target"] == "/data/output"


def test_compose_explicit_settings_and_shell_precedence(
    compose: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {
        "EDGE_TTS_OUTPUT_DIR": "C:/host-only-path",
        "EDGE_TTS_VOICE": "en-US-AriaNeural",
        "EDGE_TTS_RATE": "+5%",
        "EDGE_TTS_VOLUME": "-5%",
        "EDGE_TTS_PITCH": "+2Hz",
        "EDGE_TTS_TEMPO_MIN": "0.85",
        "EDGE_TTS_TEMPO_MAX": "1.15",
        "EDGE_TTS_TIMEOUT_SECONDS": "60",
        "EDGE_TTS_MAX_AUDIO_DURATION_SECONDS": "600",
        "EDGE_TTS_ALLOW_SIDECARS": "false",
        "EDGE_TTS_MAX_CONCURRENT": "3",
        "EDGE_TTS_FFMPEG": "/usr/bin/ffmpeg",
        "EDGE_TTS_FFPROBE": "/usr/bin/ffprobe",
        "EDGE_TTS_UNRELATED_PUBLIC_TEST": "not-forwarded",
    }
    set_file(compose, values)
    monkeypatch.setenv("EDGE_TTS_TIMEOUT_SECONDS", "75")
    environment = service_config(compose)["environment"]
    expected = {
        **DEFAULT_ENV,
        **values,
        "EDGE_TTS_OUTPUT_DIR": "/data/output",
        "EDGE_TTS_TIMEOUT_SECONDS": "75",
    }
    expected.pop("EDGE_TTS_UNRELATED_PUBLIC_TEST")
    assert environment == expected


@pytest.mark.parametrize("overrides", [False, True])
async def test_compose_mcp_runtime_defaults_and_overrides(
    compose: list[str], overrides: bool
) -> None:
    if overrides:
        set_file(
            compose,
            {
                "EDGE_TTS_VOICE": "en-US-AriaNeural",
                "EDGE_TTS_RATE": "+5%",
                "EDGE_TTS_VOLUME": "-5%",
                "EDGE_TTS_PITCH": "+2Hz",
                "EDGE_TTS_TEMPO_MIN": "0.85",
                "EDGE_TTS_TEMPO_MAX": "1.15",
                "EDGE_TTS_TIMEOUT_SECONDS": "60",
                "EDGE_TTS_MAX_AUDIO_DURATION_SECONDS": "600",
                "EDGE_TTS_ALLOW_SIDECARS": "false",
                "EDGE_TTS_MAX_CONCURRENT": "3",
            },
        )
    parameters = stdio_parameters(compose)
    async with asyncio.timeout(60):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool("get_capabilities", {})
                caps = response.structuredContent
                assert caps["server_version"] == __version__
                assert caps["ffmpeg_available"] and caps["ffprobe_available"]
                assert caps["defaults"]["voice"] == (
                    "en-US-AriaNeural" if overrides else "ru-RU-SvetlanaNeural"
                )
                assert caps["defaults"]["rate"] == ("+5%" if overrides else "+0%")
                assert caps["defaults"]["volume"] == ("-5%" if overrides else "+0%")
                assert caps["defaults"]["pitch"] == ("+2Hz" if overrides else "+0Hz")
                assert caps["duration_fitting"]["default_tempo_range"] == (
                    [0.85, 1.15] if overrides else [0.5, 2.0]
                )
                assert caps["timeout_seconds"]["default"] == (60 if overrides else 45)
                assert caps["max_audio_duration_seconds"] == (600 if overrides else 300)
                assert caps["max_concurrent_synthesis"] == (3 if overrides else 2)
                assert caps["retry_policy"]["max_attempts"] == 5
                schema = next(
                    tool for tool in (await session.list_tools()).tools if tool.name == "tts"
                ).inputSchema
                assert schema["properties"]["timeout"]["default"] == (60 if overrides else 45)
                if overrides:
                    assert caps["subtitles"] == [] and caps["word_timings"] is False
                    rejected = await session.call_tool("tts", {"text": "hi", "word_timings": True})
                    assert rejected.structuredContent["error_code"] == "feature_disabled"


@pytest.mark.parametrize(
    "values,name",
    [
        ({"EDGE_TTS_TEMPO_MAX": "2.01"}, "EDGE_TTS_TEMPO_MAX"),
        ({"EDGE_TTS_TEMPO_MIN": "1.2", "EDGE_TTS_TEMPO_MAX": "1.1"}, "EDGE_TTS_TEMPO_MIN"),
        ({"EDGE_TTS_TIMEOUT_SECONDS": ""}, "EDGE_TTS_TIMEOUT_SECONDS"),
        ({"EDGE_TTS_MAX_AUDIO_DURATION_SECONDS": "nan"}, "EDGE_TTS_MAX_AUDIO_DURATION_SECONDS"),
    ],
)
def test_compose_rejects_bad_settings_at_startup(
    compose: list[str], values: dict, name: str
) -> None:
    set_file(compose, values)
    result = subprocess.run(
        [*compose, "run", "--rm", "--no-deps", "-T", "edge-tts-mcp"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 2
    assert result.stdout == "" and name in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("EDGE_TTS_LIVE_TESTS") != "1", reason="Network tests disabled")
@pytest.mark.parametrize("audio_format,tempo", [("mp3", 1.1), ("ogg", 0.9)])
async def test_compose_live_synthesis_and_duration(
    compose: list[str], audio_format: str, tempo: float
) -> None:
    set_file(
        compose,
        {
            "EDGE_TTS_TEMPO_MIN": "0.85",
            "EDGE_TTS_TEMPO_MAX": "1.15",
            "EDGE_TTS_TIMEOUT_SECONDS": "60",
            "EDGE_TTS_RETRY_ATTEMPTS": "3",
            "EDGE_TTS_RETRY_INITIAL_DELAY_SECONDS": "1",
            "EDGE_TTS_RETRY_MAX_DELAY_SECONDS": "4",
            "EDGE_TTS_MAX_AUDIO_DURATION_SECONDS": "600",
        },
    )
    parameters = stdio_parameters(compose)
    async with asyncio.timeout(120):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                text = "Привет! Проверяем настройки и длительность речи через Docker Compose."
                response = await session.call_tool("tts", {"text": text})
                baseline = response.structuredContent
                assert baseline["success"], baseline
                target = baseline["duration_seconds"] / tempo
                response = await session.call_tool(
                    "tts",
                    {
                        "text": text,
                        "audio_format": audio_format,
                        "target_duration_seconds": target,
                        "subtitles": "srt" if audio_format == "mp3" else "vtt",
                        "word_timings": True,
                    },
                )
                result = response.structuredContent
                assert result["success"] and not result.get("warnings"), result
                assert result["duration_fitting"]["target_met"], result
                assert result["duration_fitting"]["tempo_min"] == 0.85
                assert result["duration_fitting"]["tempo_max"] == 1.15
                assert abs(result["duration_seconds"] - target) <= max(0.05, target * 0.01)
                assert result["tempo"] < 1 if tempo < 1 else result["tempo"] > 1
                assert result["audio_path"].startswith("/data/output/")
                assert result["word_count"] >= 5 and result["subtitle_path"]
    # A second Compose container must be able to read and validate the same volume.
    script = """
import json, sys
from pathlib import Path
from edge_tts_mcp.core.audio import validate_audio
from edge_tts_mcp.core.audio import probe_duration
import asyncio
result = json.loads(sys.argv[1])
path = Path(result['audio_path'])
size, _ = validate_audio(path, result['format'])
actual = asyncio.run(probe_duration('/usr/bin/ffprobe', path))
words = json.loads(Path(result['word_timings_path']).read_text())
subtitle = Path(result['subtitle_path']).read_text()
assert size == result['size'] and actual > 0
assert abs(actual - result['duration_fitting']['target_duration_seconds']) <= result['duration_fitting']['tolerance_seconds']
assert all(0 <= word['start_ms'] < word['end_ms'] <= round(actual * 1000) for word in words)
assert '-->' in subtitle
assert not list(path.parent.glob('.edge-tts-*'))
print('audio, sidecars, duration and temporary cleanup verified in shared Compose volume')
"""
    verified = subprocess.run(
        [
            *compose,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            "python",
            "edge-tts-mcp",
            "-c",
            script,
            json.dumps(result),
        ],
        capture_output=True,
        text=True,
        timeout=45,
        check=True,
    )
    assert "verified in shared Compose volume" in verified.stdout
