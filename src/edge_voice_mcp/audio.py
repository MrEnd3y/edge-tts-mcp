"""Local audio validation and optional FFmpeg conversion."""

from __future__ import annotations

import asyncio
import math
import shutil
from pathlib import Path

import anyio
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus


class AudioError(Exception):
    """A safe-to-display audio processing failure."""


def validate_audio(path: Path, audio_format: str) -> tuple[int, float]:
    """Check container/codec, nonempty file, and positive audio duration."""
    try:
        size = path.stat().st_size
        if size == 0:
            raise ValueError("empty")
        audio = MP3(path) if audio_format == "mp3" else OggOpus(path)
        duration = float(audio.info.length)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid duration")
    except Exception as exc:
        raise AudioError("The generated audio is empty, invalid, or unreadable") from exc
    return size, duration


def find_ffmpeg(executable: str) -> str:
    """Resolve only the configured executable; never invoke a shell."""
    resolved = shutil.which(executable)
    if resolved is None:
        raise AudioError("OGG/Opus requires FFmpeg with libopus; set EDGE_TTS_FFMPEG")
    return resolved


async def convert_to_opus(ffmpeg: str, source: Path, destination: Path) -> None:
    """Convert local MP3 to mono OGG/Opus; kill/reap FFmpeg on cancellation."""
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "48k",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-application",
        "voip",
        "-f",
        "ogg",
        str(destination),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await process.wait()
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            # Shield reaping from MCP's cancellation scope, not just asyncio's
            # single task cancellation. Do not leave an encoder running.
            with anyio.CancelScope(shield=True):
                await process.wait()
    if process.returncode != 0:
        raise AudioError("FFmpeg conversion failed; check that FFmpeg supports libopus")
