"""MCP stdio tools and server configuration; stdout is protocol-only."""

from __future__ import annotations

import asyncio
import shutil
from typing import Annotated, Any, Literal

import edge_tts
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import __version__
from ..config import MAX_TIMEOUT_SECONDS, TECHNICAL_TEMPO_MAX, TECHNICAL_TEMPO_MIN, Settings
from ..config import settings_from_environment as settings_from_environment
from .service import (
    DURATION_TOLERANCE_FRACTION,
    DURATION_TOLERANCE_SECONDS,
    MAX_TEXT_LENGTH,
    AudioFormat,
    synthesize,
)
from .timings import SubtitleFormat


def _filter_voices(
    voices: object, locale: str, gender: str, query: str, offset: int, limit: int | None
) -> list[dict[str, Any]]:
    if not isinstance(voices, list):
        raise ValueError("Invalid voice catalog")
    matches: list[dict[str, Any]] = []
    prefix, search = locale.strip().casefold(), query.strip().casefold()
    for raw in voices:
        if not isinstance(raw, dict) or any(
            not isinstance(raw.get(key), str) or not raw[key]
            for key in ("ShortName", "Gender", "Locale")
        ):
            raise ValueError("Invalid voice entry")
        if not raw["Locale"].casefold().startswith(prefix) or (gender and raw["Gender"] != gender):
            continue
        item: dict[str, Any] = {key: raw[key] for key in ("ShortName", "Gender", "Locale")}
        for key in ("FriendlyName", "Name"):
            if isinstance(raw.get(key), str):
                item[key] = raw[key]
        tags = raw.get("VoiceTag")
        if isinstance(tags, dict):
            item["VoiceTag"] = {
                key: values
                for key, values in tags.items()
                if isinstance(key, str)
                and isinstance(values, list)
                and all(isinstance(value, str) for value in values)
            }
        values = [value for value in item.values() if isinstance(value, str)]
        for tag_values in item.get("VoiceTag", {}).values():
            values.extend(tag_values)
        if not search or search in " ".join(values).casefold():
            matches.append(item)
    return matches[offset : None if limit is None else offset + limit]


def create_server(settings: Settings | None = None) -> FastMCP:
    """Create an isolated stdio MCP server."""
    settings = settings or settings_from_environment()
    # Capture non-optional, validated defaults for the generated tool schema.
    request_defaults: Settings = settings
    semaphore = asyncio.Semaphore(settings.max_concurrent)
    mcp = FastMCP(
        "edge-tts-mcp",
        instructions=(
            "Create local speech audio for the client to deliver. Use list_voice to choose a voice "
            "when the task specifies a language/gender. Use get_capabilities for local limits/defaults. "
            "For ordinary speech request only audio; use ogg for Telegram voice notes. "
            "Only request subtitles or word timings when needed for the user's task. "
            "This server only creates files; use the client's other tools for delivery. "
            "Check success and warnings before using any returned path."
        ),
    )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def get_capabilities() -> dict[str, Any]:
        """Read local speech formats, optional artifacts and defaults; no network request.

        ffmpeg_available checks executable presence, not libopus support. Telegram
        delivery is the client's responsibility. Long text uses edge-tts internal
        chunking; increase the tool/client timeout for longer jobs.
        """
        return {
            "server_version": __version__,
            "audio_formats": ["mp3", "ogg"],
            "ffmpeg_available": shutil.which(settings.ffmpeg) is not None,
            "ffprobe_available": shutil.which(settings.ffprobe) is not None,
            "format_requirements": {"mp3": [], "ogg": ["ffmpeg", "libopus"]},
            "max_text_characters": MAX_TEXT_LENGTH,
            "timeout_seconds": {"default": settings.timeout, "maximum": MAX_TIMEOUT_SECONDS},
            "max_audio_duration_seconds": settings.max_audio_duration_seconds,
            "duration_fitting": {
                "maximum_target_seconds": settings.max_audio_duration_seconds,
                "tempo_range": [TECHNICAL_TEMPO_MIN, TECHNICAL_TEMPO_MAX],
                "default_tempo_range": [settings.tempo_min, settings.tempo_max],
                "tolerance_seconds": DURATION_TOLERANCE_SECONDS,
                "tolerance_fraction": DURATION_TOLERANCE_FRACTION,
                "requires": ["ffmpeg", "ffprobe"],
            },
            "max_concurrent_synthesis": settings.max_concurrent,
            "queue_wait_counts_toward_timeout": True,
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
                "tempo_min": settings.tempo_min,
                "tempo_max": settings.tempo_max,
                "timeout": settings.timeout,
                "max_audio_duration_seconds": settings.max_audio_duration_seconds,
            },
            "delivery": "local_paths_only",
        }

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
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
            return _filter_voices(voices, locale, gender, query, offset, limit)
        except TimeoutError:
            raise ValueError("Voice listing timed out after 30 seconds") from None
        except Exception:
            raise ValueError("Cannot retrieve Microsoft voices; check network access") from None

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
        )
    )
    async def tts(
        text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)],
        voice: str | None = None,
        rate: str | None = None,
        volume: str | None = None,
        pitch: str | None = None,
        audio_format: AudioFormat = "mp3",
        timeout: Annotated[
            float, Field(gt=0, le=MAX_TIMEOUT_SECONDS, allow_inf_nan=False, strict=True)
        ] = request_defaults.timeout,
        subtitles: SubtitleFormat = "none",
        word_timings: bool = False,
        target_duration_seconds: Annotated[
            float | None,
            Field(gt=0, allow_inf_nan=False, strict=True),
        ] = None,
        tempo_min: Annotated[
            float,
            Field(ge=TECHNICAL_TEMPO_MIN, le=TECHNICAL_TEMPO_MAX, allow_inf_nan=False, strict=True),
        ] = request_defaults.tempo_min,
        tempo_max: Annotated[
            float,
            Field(ge=TECHNICAL_TEMPO_MIN, le=TECHNICAL_TEMPO_MAX, allow_inf_nan=False, strict=True),
        ] = request_defaults.tempo_max,
        max_audio_duration_seconds: Annotated[
            float, Field(gt=0, allow_inf_nan=False, strict=True)
        ] = request_defaults.max_audio_duration_seconds,
    ) -> dict[str, Any]:
        """Generate MP3 or OGG/Opus (requires FFmpeg) and return a local file path.

        Rate/volume: signed percentage, e.g. +10%. Pitch: signed Hz, e.g. -5Hz.
        Ordinary speech/Telegram: leave subtitles=none and word_timings=false.
        Request srt/vtt only for captions; request word_timings only for per-word
        highlighting/editing. Missing boundary events return audio with warnings,
        never estimated timings. Explicit voice/rate/volume/pitch override server defaults.
        Choose rate directly for the task, e.g. +15% or -10%.
        Optional target_duration_seconds fits audio duration with FFmpeg, verified by FFprobe.
        Omitted parameters inherit config.py ENV defaults (built-in tempo 0.5–2.0).
        Set tempo_min/tempo_max for the task (immutable technical limits 0.5–2.0).
        Bounds are never widened automatically; retry explicitly to change them.
        Unreachable targets return target_duration_unreachable with required tempo and bounds,
        not original audio. Without a target, tempo bounds do not change audio.
        Check duration_fitting.target_met and warnings; precise sync/natural speech
        are not guaranteed. Timings are rescaled using the applied tempo.
        Successful requests create audio; subtitles and word timings are optional sidecar files.
        Timeout includes queue wait, synthesis, conversion and sidecars. Up to 100000 characters;
        max_audio_duration_seconds limits audio duration independently of the operation timeout.
        Targets must not exceed that duration limit; oversized output is not trimmed or published.
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
            target_duration_seconds,
            tempo_min,
            tempo_max,
            max_audio_duration_seconds,
            semaphore=semaphore,
        )

    return mcp
