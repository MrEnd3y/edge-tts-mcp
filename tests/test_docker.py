"""Opt-in container smoke tests; marked live cases also contact Microsoft."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from edge_tts_mcp.core.audio import validate_audio

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("EDGE_TTS_DOCKER_TESTS") != "1", reason="Docker tests disabled"
    ),
]
IMAGE = os.environ.get("EDGE_TTS_DOCKER_IMAGE", "edge-tts-mcp:local")
HARDENING = [
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--tmpfs",
    "/tmp:rw,noexec,nosuid,size=64m",
]


def test_nonroot_container_encodes_real_mp3_and_opus() -> None:
    script = """
import asyncio, json, os, subprocess, tempfile
from pathlib import Path
from edge_tts_mcp.core.audio import validate_audio, convert_to_opus, probe_duration
from edge_tts_mcp.core import service
from edge_tts_mcp.config import Settings
with tempfile.TemporaryDirectory() as root:
    mp3, ogg = Path(root) / 'tone.mp3', Path(root) / 'tone.ogg'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=3', '-c:a', 'libmp3lame', str(mp3)],
                   check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    asyncio.run(asyncio.wait_for(convert_to_opus('/usr/bin/ffmpeg', mp3, ogg), 10))
    mp3_size, _ = validate_audio(mp3, 'mp3')
    ogg_size, _ = validate_audio(ogg, 'ogg')
    original = validate_audio(mp3, 'mp3')[1]
    content = mp3.read_bytes()
    async def save(self, filename):
        Path(filename).write_bytes(content)
    service.edge_tts.Communicate.save = save
    settings = Settings(Path(root) / 'published')
    fitted = 0
    for fmt in ('mp3', 'ogg'):
        for tempo in (0.5, 0.9, 1.1, 2.0):
            target = original / tempo
            result = asyncio.run(service.synthesize(settings, 'Offline tone test',
                                 audio_format=fmt, target_duration_seconds=target, timeout=10))
            assert result['success'], result
            output = Path(result['audio_path'])
            validate_audio(output, fmt)
            actual = asyncio.run(probe_duration('/usr/bin/ffprobe', output))
            within_tolerance = abs(actual - target) <= max(0.05, target * 0.01)
            assert result['duration_fitting']['target_met'] == within_tolerance, result
            assert abs(result['tempo'] - tempo) < 1e-12
            if not within_tolerance:
                assert any(w['code'] == 'duration_mismatch' for w in result['warnings']), result
            assert mp3.read_bytes() == content
            assert not list(settings.output_dir.glob('.edge-tts-*'))
            fitted += 1
    print(json.dumps({'uid': os.getuid(), 'mp3_bytes': mp3_size, 'ogg_bytes': ogg_size, 'fitted': fitted}))
"""
    result = subprocess.run(
        ["docker", "run", "--rm", *HARDENING, "--entrypoint", "python", IMAGE, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=40,
    )
    data = json.loads(result.stdout)
    assert data["uid"] != 0
    assert data["mp3_bytes"] > 0 and data["ogg_bytes"] > 0
    assert data["fitted"] == 8


async def test_container_stdio_protocol() -> None:
    parameters = StdioServerParameters(
        command="docker",
        args=["run", "--rm", "--init", "-i", *HARDENING, IMAGE],
    )
    async with asyncio.timeout(45):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert {tool.name for tool in (await session.list_tools()).tools} == {
                    "tts",
                    "list_voice",
                    "get_capabilities",
                }
                capabilities = await session.call_tool("get_capabilities", {})
                assert capabilities.structuredContent["ffmpeg_available"] is True
                assert capabilities.structuredContent["ffprobe_available"] is True
                rejected = await session.call_tool("tts", {"text": " "})
                assert rejected.structuredContent["error_code"] == "invalid_input"


def test_named_volume_persists_private_output() -> None:
    volume = f"edge-tts-test-{uuid.uuid4().hex}"
    args = [
        "docker",
        "run",
        "--rm",
        *HARDENING,
        "--mount",
        f"type=volume,source={volume},target=/data/output",
        "--entrypoint",
        "python",
        IMAGE,
    ]
    try:
        script = "from pathlib import Path; p=Path('/data/output/check.txt'); p.write_text('test'); p.chmod(0o600)"
        subprocess.run(
            [*args, "-c", script], check=True, capture_output=True, text=True, timeout=30
        )
        script = "import json,os,stat; from pathlib import Path; p=Path('/data/output/check.txt'); print(json.dumps({'content':p.read_text(),'mode':stat.S_IMODE(p.stat().st_mode),'uid':os.getuid()}))"
        result = subprocess.run(
            [*args, "-c", script], check=True, capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        assert data["content"] == "test" and data["mode"] == 0o600 and data["uid"] != 0
    finally:
        # Only the uniquely named volume created by this test is removed.
        subprocess.run(
            ["docker", "volume", "rm", volume], capture_output=True, timeout=30, check=False
        )


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("EDGE_TTS_LIVE_TESTS") != "1", reason="Network tests disabled")
@pytest.mark.parametrize("audio_format,subtitles", [("mp3", "srt"), ("ogg", "vtt")])
@pytest.mark.parametrize("tempo", [None, 0.9, 1.1])
async def test_container_live_speech_and_sidecars(
    tmp_path: Path, audio_format: str, subtitles: str, tempo: float | None
) -> None:
    parameters = StdioServerParameters(
        command="docker",
        args=[
            "run",
            "--rm",
            "--init",
            "-i",
            *HARDENING,
            "--mount",
            f"type=bind,source={tmp_path},target=/data/output",
            IMAGE,
        ],
    )
    async with asyncio.timeout(90):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(
                    "tts",
                    {
                        "text": "Привет! Проверяем синтез речи внутри контейнера.",
                        "voice": "ru-RU-SvetlanaNeural",
                        "audio_format": audio_format,
                        "subtitles": subtitles,
                        "word_timings": True,
                        "timeout": 60,
                    },
                )
                result = response.structuredContent
                assert not response.isError and result["success"], result
                assert not result.get("warnings"), result
                if tempo is not None:
                    target = result["duration_seconds"] / tempo
                    response = await session.call_tool(
                        "tts",
                        {
                            "text": "Привет! Проверяем синтез речи внутри контейнера.",
                            "voice": "ru-RU-SvetlanaNeural",
                            "audio_format": audio_format,
                            "subtitles": subtitles,
                            "word_timings": True,
                            "target_duration_seconds": target,
                            "timeout": 60,
                        },
                    )
                    result = response.structuredContent
                    assert not response.isError and result["success"], result
                    assert not result.get("warnings"), result
                    fitting = result["duration_fitting"]
                    assert fitting["processed"] and fitting["target_met"], result
                    assert abs(fitting["actual_duration_seconds"] - target) <= max(
                        0.05, target * 0.01
                    )
                    assert result["tempo"] == fitting["applied_tempo"]
                    assert (result["tempo"] < 1) if tempo < 1 else (result["tempo"] > 1)
    # Explicit host/container mapping is part of this test, not an automatic
    # translation promised by the MCP server to arbitrary clients.
    audio = tmp_path / Path(result["audio_path"]).name
    subtitle = tmp_path / Path(result["subtitle_path"]).name
    timing = tmp_path / Path(result["word_timings_path"]).name
    size, duration = validate_audio(audio, audio_format)
    assert size == result["size"] and duration > 0
    assert "-->" in subtitle.read_text(encoding="utf-8")
    assert subtitle.read_text(encoding="utf-8").startswith("WEBVTT") == (subtitles == "vtt")
    words = json.loads(timing.read_text(encoding="utf-8"))
    assert len(words) >= 5 and words[-1]["end_ms"] <= duration * 1000 + 500
    assert len(list(tmp_path.iterdir())) == (3 if tempo is None else 6)
