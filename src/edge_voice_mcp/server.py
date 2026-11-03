"""Official MCP Python SDK stdio entry point; stdout is protocol-only."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from pathlib import Path
from typing import Annotated, Any, Literal

import edge_tts
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import __version__
from .service import MAX_TEXT_LENGTH, PROFILES, AudioFormat, Settings, SpeechProfile, synthesize
from .timings import SubtitleFormat

DEFAULT_OUTPUT_DIR = Path.home() / ".edge-voice-mcp" / "output"


def settings_from_environment() -> Settings:
    """Read only documented, nonsecret settings. Never load a dotenv file."""
    return Settings(
        output_dir=Path(os.environ.get("EDGE_TTS_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
        ffmpeg=os.environ.get("EDGE_TTS_FFMPEG", "ffmpeg"),
        voice=os.environ.get("EDGE_TTS_VOICE", "ru-RU-SvetlanaNeural"),
        rate=os.environ.get("EDGE_TTS_RATE", "+0%"),
        volume=os.environ.get("EDGE_TTS_VOLUME", "+0%"),
        pitch=os.environ.get("EDGE_TTS_PITCH", "+0Hz"),
        allow_sidecars=os.environ.get("EDGE_TTS_ALLOW_SIDECARS", "1").strip().lower()
        not in ("0", "false", "no", "off"),
    )


def create_server(settings: Settings | None = None) -> FastMCP:
    """Create an isolated server; configuration never changes Hermes settings."""
    settings = settings or settings_from_environment()
    mcp = FastMCP(
        "edge-voice-mcp",
        instructions=(
            "Create local speech audio for the client to deliver. Use list_voice to choose a voice "
            "when the task specifies a language/gender. Use get_capabilities for local limits/defaults. "
            "For ordinary speech request only audio; use ogg for Telegram voice notes. "
            "Only request subtitles or word timings when needed for the user's task. "
            "This server only creates files; use the client's other tools for delivery. "
            "Check success and warnings before using any returned path."
        ),
    )

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def get_capabilities() -> dict[str, Any]:
        """Read local speech formats, optional artifacts, profiles and defaults; no network request.

        ffmpeg_available checks executable presence, not libopus support. Telegram
        delivery is the client's responsibility. Long text uses edge-tts internal
        chunking; increase the tool/client timeout for longer jobs.
        """
        return {
            "server_version": __version__,
            "audio_formats": ["mp3", "ogg"],
            "ffmpeg_available": shutil.which(settings.ffmpeg) is not None,
            "format_requirements": {"mp3": [], "ogg": ["ffmpeg", "libopus"]},
            "max_text_characters": MAX_TEXT_LENGTH,
            "timeout_seconds": {"default": 45, "maximum": 300},
            "long_text": "edge-tts internal UTF-8-safe chunking with continuous audio and offsets",
            "subtitles": ["srt", "vtt"] if settings.allow_sidecars else [],
            "word_timings": settings.allow_sidecars,
            "timing_source": "Microsoft WordBoundary events; not transcription or forced alignment",
            "default_artifacts": ["audio"],
            "defaults": {
                "voice": settings.voice,
                "rate": settings.rate,
                "volume": settings.volume,
                "pitch": settings.pitch,
            },
            "profiles": {"default": "Configured settings", **PROFILES},
            "delivery": "local_paths_only",
        }

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def list_voice(
        locale: str = "",
        gender: Literal["", "Female", "Male"] = "",
        query: str = "",
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int | None, Field(ge=1, le=1000)] = None,
    ) -> list[dict[str, Any]]:
        """List Microsoft voices; filter by locale prefix (ru or ru-RU) and gender.

        Pass a returned ShortName as the tts voice. No filter lists all voices.
        Query searches name/locale and service-supplied tags. Offset/limit can
        page results. Tags are descriptive metadata, not supported SSML styles.
        """
        try:
            async with asyncio.timeout(30):
                voices = await edge_tts.list_voices()
        except TimeoutError:
            raise ValueError("Voice listing timed out after 30 seconds") from None
        except Exception:
            raise ValueError("Cannot retrieve Microsoft voices; check network access") from None
        prefix = locale.strip().casefold()
        matches = []
        search = query.strip().casefold()
        for voice in voices:
            if not voice["Locale"].casefold().startswith(prefix) or (
                gender and voice["Gender"] != gender
            ):
                continue
            item = {
                key: voice[key]
                for key in ("ShortName", "Gender", "Locale", "FriendlyName", "Name", "VoiceTag")
                if key in voice
            }
            if search and search not in str(item).casefold():
                continue
            matches.append(item)
        return matches[offset : None if limit is None else offset + limit]

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def tts(
        text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)],
        voice: str | None = None,
        rate: str | None = None,
        volume: str | None = None,
        pitch: str | None = None,
        audio_format: AudioFormat = "mp3",
        timeout: Annotated[float, Field(gt=0, le=300)] = 45,
        subtitles: SubtitleFormat = "none",
        word_timings: bool = False,
        profile: SpeechProfile = "default",
    ) -> dict[str, Any]:
        """Generate MP3 or OGG/Opus (requires FFmpeg) and return a local file path.

        Rate/volume: signed percentage, e.g. +10%. Pitch: signed Hz, e.g. -5Hz.
        Ordinary speech/Telegram: leave subtitles=none and word_timings=false.
        Request srt/vtt only for captions; request word_timings only for per-word
        highlighting/editing. Missing boundary events return audio with warnings,
        never estimated timings. Profiles are rate presets, not emotional styles.
        Explicit voice/rate/volume/pitch override configured defaults/presets.
        Timeout covers synthesis, conversion and sidecars. Up to 100000 characters;
        for long jobs increase timeout and client's deadline or shorten text.
        Check success/warnings before using audio_path, subtitle_path or
        word_timings_path. Paths are local to the MCP host, not uploaded.
        """
        return await synthesize(
            settings,
            text,
            voice,
            rate,
            volume,
            pitch,
            audio_format,
            timeout,
            subtitles,
            word_timings,
            profile,
        )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Async Microsoft Edge TTS MCP server (stdio)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--output-dir", type=Path, help="Override EDGE_TTS_OUTPUT_DIR")
    parser.add_argument("--ffmpeg", help="Override EDGE_TTS_FFMPEG executable path")
    parser.add_argument("--voice", help="Override EDGE_TTS_VOICE default voice")
    parser.add_argument(
        "--disable-sidecars", action="store_true", help="Reject subtitle/timing requests"
    )
    args = parser.parse_args()
    defaults = settings_from_environment()
    settings = Settings(
        output_dir=args.output_dir or defaults.output_dir,
        ffmpeg=args.ffmpeg or defaults.ffmpeg,
        voice=args.voice or defaults.voice,
        rate=defaults.rate,
        volume=defaults.volume,
        pitch=defaults.pitch,
        allow_sidecars=defaults.allow_sidecars and not args.disable_sidecars,
    )
    create_server(settings).run(transport="stdio")


if __name__ == "__main__":
    main()
