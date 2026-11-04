from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from edge_tts_mcp.core import server
from edge_tts_mcp.core.server import create_server
from edge_tts_mcp.core.service import Settings


async def test_voice_filter(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    voices = [
        {"ShortName": "ru-RU-SvetlanaNeural", "Gender": "Female", "Locale": "ru-RU"},
        {"ShortName": "ru-RU-DmitryNeural", "Gender": "Male", "Locale": "ru-RU"},
        {"ShortName": "en-US-AriaNeural", "Gender": "Female", "Locale": "en-US"},
    ]
    monkeypatch.setattr(server.edge_tts, "list_voices", AsyncMock(return_value=voices))
    _content, structured = await create_server(settings).call_tool(
        "list_voice", {"locale": "RU", "gender": "Female"}
    )
    assert structured["result"] == voices[:1]
    _content, all_voices = await create_server(settings).call_tool("list_voice", {})
    assert all_voices["result"] == voices


async def test_voice_timeout(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(server.edge_tts, "list_voices", AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(Exception, match="Voice listing timed out"):
        await create_server(settings).call_tool("list_voice", {})


async def test_voice_listing_error_sanitized(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(
        server.edge_tts, "list_voices", AsyncMock(side_effect=RuntimeError("private details"))
    )
    with pytest.raises(Exception, match="Cannot retrieve Microsoft voices") as exc:
        await create_server(settings).call_tool("list_voice", {})
    assert "private details" not in str(exc.value)


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("entrypoint", [["-m", "edge_tts_mcp"], ["main.py"]])
async def test_stdio_protocol(tmp_path: Path, disabled: bool, entrypoint: list[str]) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[*entrypoint, "--output-dir", str(tmp_path)]
        + (["--disable-sidecars", "--voice", "en-US-AriaNeural"] if disabled else []),
    )
    async with asyncio.timeout(20):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert {tool.name for tool in tools} == {"tts", "list_voice", "get_capabilities"}
                schema = next(tool for tool in tools if tool.name == "tts").inputSchema
                assert schema["properties"]["audio_format"]["enum"] == ["mp3", "ogg"]
                result = await session.call_tool("tts", {"text": " "})
                assert json.loads(result.content[0].text)["error_code"] == "invalid_input"
                assert result.structuredContent["error_code"] == "invalid_input"
                capabilities = await session.call_tool("get_capabilities", {})
                assert capabilities.structuredContent["default_artifacts"] == ["audio"]
                if disabled:
                    assert capabilities.structuredContent["subtitles"] == []
                    assert capabilities.structuredContent["defaults"]["voice"] == "en-US-AriaNeural"
                    rejected = await session.call_tool("tts", {"text": "hi", "subtitles": "srt"})
                    assert rejected.structuredContent["error_code"] == "feature_disabled"
                assert schema["properties"]["subtitles"]["default"] == "none"
                assert schema["properties"]["word_timings"]["default"] is False
                assert "save_audio" not in schema["properties"]
                assert "artifacts_without_audio" not in capabilities.structuredContent
                result = await session.call_tool("tts", {"text": "hi", "timeout": 0})
                assert result.isError
    assert list(tmp_path.iterdir()) == []
