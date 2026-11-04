"""Local audio validation, FFmpeg encoding, and FFprobe duration checks."""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
from pathlib import Path

import anyio
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus

from ..config import TECHNICAL_TEMPO_MAX, TECHNICAL_TEMPO_MIN

logger = logging.getLogger(__name__)
PROCESS_CLEANUP_TIMEOUT = 5


class AudioError(Exception):
    """A safe-to-display audio processing failure."""


def validate_audio(path: Path, audio_format: str) -> tuple[int, float]:
    """Check container/codec, nonempty file, and positive audio duration."""
    try:
        size = path.stat().st_size
        if size == 0:
            raise ValueError("empty")
        audio = MP3(path) if audio_format == "mp3" else OggOpus(path)
        info = audio.info
        if info is None:
            raise ValueError("missing audio info")
        duration = float(info.length)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid duration")
    except Exception as exc:
        raise AudioError("The generated audio is empty, invalid, or unreadable") from exc
    return size, duration


def find_ffmpeg(executable: str, *, duration_fitting: bool = False) -> str:
    """Resolve only the configured executable; never invoke a shell."""
    resolved = shutil.which(executable)
    if resolved is None:
        if duration_fitting:
            raise AudioError("Audio duration fitting requires FFmpeg; set EDGE_TTS_FFMPEG")
        raise AudioError("OGG/Opus requires FFmpeg with libopus; set EDGE_TTS_FFMPEG")
    return resolved


def find_ffprobe(executable: str) -> str:
    """Resolve only the configured probe when duration fitting needs it."""
    resolved = shutil.which(executable)
    if resolved is None:
        raise AudioError("Audio duration fitting requires FFprobe; set EDGE_TTS_FFPROBE")
    return resolved


async def probe_duration(ffprobe: str, path: Path) -> float:
    """Read local container duration without exposing probe output or errors."""
    try:
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await process.communicate()
        finally:
            if process.returncode is None:
                await _stop_encoder(process)
        if process.returncode != 0:
            raise ValueError("probe failed")
        duration = float(stdout)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid duration")
    except (OSError, ValueError):
        raise AudioError("FFprobe could not verify audio duration") from None
    return duration


async def _stop_encoder(process: asyncio.subprocess.Process) -> None:
    try:
        process.kill()
    except ProcessLookupError:
        pass
    interrupted = False
    # AnyIO protects against level-triggered cancel scopes; asyncio.shield
    # protects the reaper task against repeated direct Task.cancel() calls.
    with anyio.CancelScope(shield=True):
        waiter = asyncio.create_task(asyncio.wait_for(process.wait(), PROCESS_CLEANUP_TIMEOUT))
        while not waiter.done():
            try:
                await asyncio.shield(waiter)
            except asyncio.CancelledError:
                interrupted = True
            except (TimeoutError, OSError):
                break
        try:
            waiter.result()
        except (TimeoutError, OSError):
            logger.warning("Media process cleanup did not complete within its bounded wait")
    if interrupted:
        raise asyncio.CancelledError


async def convert_to_opus(ffmpeg: str, source: Path, destination: Path) -> None:
    """Convert local MP3 to mono OGG/Opus; kill/reap FFmpeg on cancellation."""
    await _encode(ffmpeg, source, destination, "ogg")


async def adjust_tempo(
    ffmpeg: str, source: Path, destination: Path, audio_format: str, tempo: float
) -> None:
    """Encode to a separate file with atempo; never overwrite the original."""
    if (
        source == destination
        or not math.isfinite(tempo)
        or not TECHNICAL_TEMPO_MIN <= tempo <= TECHNICAL_TEMPO_MAX
    ):
        raise AudioError("Invalid audio duration fitting parameters")
    await _encode(ffmpeg, source, destination, audio_format, tempo)


async def _encode(
    ffmpeg: str,
    source: Path,
    destination: Path,
    audio_format: str,
    tempo: float | None = None,
) -> None:
    codec = (
        ["-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-ac", "1", "-application", "voip"]
        if audio_format == "ogg"
        else ["-c:a", "libmp3lame", "-b:a", "48k"]
    )
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
        *(["-af", f"atempo={tempo!r}"] if tempo is not None else []),
        *codec,
        "-f",
        audio_format,
        str(destination),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await process.wait()
    finally:
        if process.returncode is None:
            await _stop_encoder(process)
    if process.returncode != 0:
        if tempo is not None:
            raise AudioError("FFmpeg duration fitting failed; check the required audio encoder")
        raise AudioError("FFmpeg conversion failed; check that FFmpeg supports libopus")
