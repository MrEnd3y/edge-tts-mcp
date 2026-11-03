"""Opt-in Linux container smoke tests, with no calls to Microsoft or Telegram."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("EDGE_TTS_DOCKER_TESTS") != "1", reason="Docker tests disabled"
    ),
]
IMAGE = os.environ.get("EDGE_TTS_DOCKER_IMAGE", "edge-voice-mcp:local")
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
from edge_voice_mcp.audio import validate_audio, convert_to_opus
with tempfile.TemporaryDirectory() as root:
    mp3, ogg = Path(root) / 'tone.mp3', Path(root) / 'tone.ogg'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.3', '-c:a', 'libmp3lame', str(mp3)],
                   check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    asyncio.run(asyncio.wait_for(convert_to_opus('/usr/bin/ffmpeg', mp3, ogg), 10))
    mp3_size, _ = validate_audio(mp3, 'mp3')
    ogg_size, _ = validate_audio(ogg, 'ogg')
    print(json.dumps({'uid': os.getuid(), 'mp3_bytes': mp3_size, 'ogg_bytes': ogg_size}))
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
                rejected = await session.call_tool("tts", {"text": " "})
                assert rejected.structuredContent["error_code"] == "invalid_input"
