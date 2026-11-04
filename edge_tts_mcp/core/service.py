"""Synthesis operations independent of the MCP transport."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import tempfile
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Literal

import edge_tts

from ..config import MAX_TIMEOUT_SECONDS, positive_finite, valid_tempo_bounds
from ..config import Settings as Settings
from .audio import (
    AudioError,
    adjust_tempo,
    convert_to_opus,
    find_ffmpeg,
    find_ffprobe,
    probe_duration,
    validate_audio,
)
from .timings import SubtitleFormat, TimingError, read_words, render_subtitles, rescale_words

logger = logging.getLogger(__name__)
AudioFormat = Literal["mp3", "ogg"]
SpeechProfile = Literal["default", "narration", "notification", "slow"]
MAX_TEXT_LENGTH = 100_000
DURATION_TOLERANCE_SECONDS = 0.05
DURATION_TOLERANCE_FRACTION = 0.01
PROFILES = {
    "narration": {"rate": "-10%", "volume": "+0%", "pitch": "+0Hz"},
    "notification": {"rate": "+15%", "volume": "+0%", "pitch": "+0Hz"},
    "slow": {"rate": "-25%", "volume": "+0%", "pitch": "+0Hz"},
}


def failure(code: str, message: str) -> dict:
    return {"success": False, "error_code": code, "error": message}


def _check_deadline(deadline: float) -> None:
    if asyncio.get_running_loop().time() >= deadline:
        raise TimeoutError


async def _checkpoint(deadline: float) -> None:
    # asyncio timeouts/cancellation are delivered at suspension points. Save
    # mocks (and the final synchronous write in the SDK) need not suspend.
    await asyncio.sleep(0)
    _check_deadline(deadline)


async def synthesize(
    settings: Settings,
    text: str,
    voice: str | None = None,
    rate: str | None = None,
    volume: str | None = None,
    pitch: str | None = None,
    audio_format: AudioFormat = "mp3",
    timeout: float | None = None,
    subtitles: SubtitleFormat = "none",
    word_timings: bool = False,
    profile: SpeechProfile = "default",
    target_duration_seconds: float | None = None,
    tempo_min: float | None = None,
    tempo_max: float | None = None,
    max_audio_duration_seconds: float | None = None,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """Publish audio with optional subtitle and word timing files."""
    timeout = settings.timeout if timeout is None else timeout
    tempo_min = settings.tempo_min if tempo_min is None else tempo_min
    tempo_max = settings.tempo_max if tempo_max is None else tempo_max
    max_audio_duration_seconds = (
        settings.max_audio_duration_seconds
        if max_audio_duration_seconds is None
        else max_audio_duration_seconds
    )
    if not text.strip() or len(text) > MAX_TEXT_LENGTH:
        return failure(
            "invalid_input", f"Text must contain 1–{MAX_TEXT_LENGTH} characters and not be blank"
        )
    if profile != "default" and profile not in PROFILES:
        return failure("invalid_input", "Unknown speech profile")
    defaults = PROFILES.get(
        profile, {"rate": settings.rate, "volume": settings.volume, "pitch": settings.pitch}
    )
    voice = settings.voice if voice is None else voice
    rate = defaults["rate"] if rate is None else rate
    volume = defaults["volume"] if volume is None else volume
    pitch = defaults["pitch"] if pitch is None else pitch
    if not voice.strip():
        return failure("invalid_input", "Voice cannot be empty")
    if (
        audio_format not in ("mp3", "ogg")
        or type(timeout) not in (int, float)
        or not 0 < timeout <= MAX_TIMEOUT_SECONDS
    ):
        return failure("invalid_input", "Use mp3/ogg and a timeout greater than 0 and at most 300")
    if not positive_finite(max_audio_duration_seconds):
        return failure(
            "invalid_input", "Maximum audio duration must be a finite number greater than 0"
        )
    if target_duration_seconds is not None and (
        type(target_duration_seconds) not in (int, float)
        or not positive_finite(target_duration_seconds)
        or target_duration_seconds > max_audio_duration_seconds
    ):
        return failure(
            "invalid_input",
            "Target duration must be finite, greater than 0, and not exceed max_audio_duration_seconds",
        )
    if subtitles not in ("none", "srt", "vtt"):
        return failure("invalid_input", "Subtitles must be none, srt, or vtt")
    if not valid_tempo_bounds(tempo_min, tempo_max):
        return failure(
            "invalid_input", "Tempo bounds must be finite numbers: 0.5 <= min <= max <= 2.0"
        )
    needs_metadata = subtitles != "none" or word_timings
    if needs_metadata and not settings.allow_sidecars:
        return failure("feature_disabled", "Subtitle and timing files are disabled by the server")

    work: tempfile.TemporaryDirectory | None = None
    published: list[Path] = []
    completed = False
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        async with asyncio.timeout_at(deadline), AsyncExitStack() as stack:
            # Validate options before creating files or contacting the service.
            try:
                communicate = edge_tts.Communicate(
                    text,
                    voice.strip(),
                    rate=rate,
                    volume=volume,
                    pitch=pitch,
                    boundary="WordBoundary" if needs_metadata else "SentenceBoundary",
                    connect_timeout=10,
                    receive_timeout=30,
                )
            except (ValueError, TypeError):
                return failure("invalid_input", "Invalid voice, rate, volume, or pitch parameter")
            if semaphore is not None:
                await stack.enter_async_context(semaphore)
            ffmpeg = (
                find_ffmpeg(settings.ffmpeg)
                if audio_format == "ogg" and target_duration_seconds is None
                else None
            )
            directory = settings.output_dir.expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            work = tempfile.TemporaryDirectory(prefix=".edge-tts-", dir=directory)
            mp3_path = Path(work.name) / "speech.mp3"
            metadata_path = Path(work.name) / "boundaries.jsonl"
            # edge-tts handles UTF-8-safe 4096-byte chunks and global event offsets.
            if needs_metadata:
                await communicate.save(str(mp3_path), str(metadata_path))
            else:
                await communicate.save(str(mp3_path))
            await _checkpoint(deadline)
            stem = f"edge-tts-{uuid.uuid4().hex}"
            outputs: list[tuple[Path, Path]] = []
            result: dict[str, Any] = {
                "success": True,
                "voice": voice.strip(),
                "rate": rate,
                "volume": volume,
                "pitch": pitch,
                "profile": profile,
                "characters": len(text),
            }
            size, duration = validate_audio(mp3_path, "mp3")
            original_duration = duration
            applied_tempo = 1.0
            result_path = mp3_path
            if target_duration_seconds is not None:
                await _checkpoint(deadline)
                tempo = original_duration / target_duration_seconds
                tolerance = max(
                    DURATION_TOLERANCE_SECONDS,
                    target_duration_seconds * DURATION_TOLERANCE_FRACTION,
                )
                if not tempo_min <= tempo <= tempo_max:
                    return {
                        **failure(
                            "target_duration_unreachable",
                            "Required tempo is outside the allowed range; no fitted audio was published",
                        ),
                        "target_duration": target_duration_seconds,
                        "source_duration": original_duration,
                        "required_tempo": tempo if math.isfinite(tempo) else None,
                        "tempo_min": tempo_min,
                        "tempo_max": tempo_max,
                    }
                # A target must be verified even when no speed change is needed.
                if audio_format == "ogg":
                    ffmpeg = find_ffmpeg(settings.ffmpeg, duration_fitting=True)
                ffprobe = find_ffprobe(settings.ffprobe)
                warning = None
                if tempo != 1.0:
                    ffmpeg = ffmpeg or find_ffmpeg(settings.ffmpeg, duration_fitting=True)
                    candidate = Path(work.name) / f"fitted.{audio_format}"
                    try:
                        await adjust_tempo(ffmpeg, mp3_path, candidate, audio_format, tempo)
                        candidate_size, _ = validate_audio(candidate, audio_format)
                        candidate_duration = await probe_duration(ffprobe, candidate)
                    except (AudioError, OSError):
                        warning = "Audio duration fitting failed; returning unmodified speech"
                    else:
                        result_path = candidate
                        size, duration = candidate_size, candidate_duration
                        applied_tempo = tempo
                result["duration_fitting"] = {
                    "target_duration_seconds": target_duration_seconds,
                    "original_duration_seconds": original_duration,
                    "requested_tempo": tempo if math.isfinite(tempo) else None,
                    "tempo_min": tempo_min,
                    "tempo_max": tempo_max,
                    "applied_tempo": applied_tempo,
                    "processed": result_path != mp3_path,
                    "tolerance_seconds": tolerance,
                    "target_met": False,
                }
                if warning:
                    result.setdefault("warnings", []).append(
                        {"code": "duration_target_not_met", "message": warning}
                    )
            if ffmpeg is not None and audio_format == "ogg" and result_path == mp3_path:
                result_path = Path(work.name) / "speech.ogg"
                await convert_to_opus(ffmpeg, mp3_path, result_path)
                size, duration = validate_audio(result_path, "ogg")
            if target_duration_seconds is not None:
                if result_path == mp3_path or applied_tempo == 1.0:
                    duration = await probe_duration(ffprobe, result_path)
                fitting = result["duration_fitting"]
                result["tempo"] = applied_tempo
                fitting["actual_duration_seconds"] = duration
                fitting["target_met"] = (
                    "warnings" not in result
                    and abs(duration - target_duration_seconds) <= tolerance
                )
                if not fitting["target_met"]:
                    result.setdefault("warnings", []).append(
                        {
                            "code": "duration_mismatch",
                            "message": (
                                f"Target {target_duration_seconds:.3f}s; actual {duration:.3f}s "
                                f"(tolerance {tolerance:.3f}s); target duration was not achieved"
                            ),
                        }
                    )
            if duration > max_audio_duration_seconds:
                await _checkpoint(deadline)
                return {
                    **failure(
                        "audio_duration_exceeded",
                        "Generated audio exceeds max_audio_duration_seconds; no audio was published",
                    ),
                    "actual_duration_seconds": duration,
                    "max_audio_duration_seconds": max_audio_duration_seconds,
                }
            final_path = directory / f"{stem}.{audio_format}"
            outputs.append((result_path, final_path))
            result.update(
                path=str(final_path),
                audio_path=str(final_path),
                size=size,
                duration_seconds=round(duration, 3),
                format=audio_format,
                mime_type="audio/mpeg" if audio_format == "mp3" else "audio/ogg",
            )
            if needs_metadata:
                try:
                    words = read_words(metadata_path)
                    # Allow small codec/header rounding differences, not cues
                    # far beyond the generated recording.
                    timing_duration = (
                        original_duration if target_duration_seconds is not None else duration
                    )
                    if words[-1].end_ms > timing_duration * 1000 + 500:
                        raise TimingError("Word events exceed audio duration")
                    if target_duration_seconds is not None:
                        words = rescale_words(words, applied_tempo, duration)
                except TimingError:
                    result.setdefault("warnings", []).append(
                        {
                            "code": "timing_metadata_unavailable",
                            "message": "Audio was generated, but valid word events were not returned; no subtitle/timing files were created",
                        }
                    )
                else:
                    if word_timings:
                        source = Path(work.name) / "words.json"
                        target = directory / f"{stem}.words.json"
                        source.write_text(
                            json.dumps(
                                [word.as_dict() for word in words], ensure_ascii=False, indent=2
                            ),
                            encoding="utf-8",
                        )
                        outputs.append((source, target))
                        result["word_timings_path"] = str(target)
                        result["word_count"] = len(words)
                    if subtitles != "none":
                        source = Path(work.name) / f"subtitles.{subtitles}"
                        target = directory / f"{stem}.{subtitles}"
                        source.write_text(render_subtitles(words, subtitles), encoding="utf-8")
                        outputs.append((source, target))
                        result["subtitle_path"] = str(target)
                        result["subtitle_format"] = subtitles
            await _checkpoint(deadline)
            # No awaits during publication. Roll back published files on failure.
            for source, target in outputs:
                _check_deadline(deadline)
                source.chmod(0o600)
                source.replace(target)
                published.append(target)
            _check_deadline(deadline)
            completed = True
            return result
    except TimeoutError:
        return failure(
            "timeout", f"Synthesis/conversion timed out (overall budget: {timeout:g} seconds)"
        )
    except edge_tts.exceptions.NoAudioReceived:
        return failure("no_audio", "Microsoft returned no audio; check the voice or retry later")
    except AudioError as exc:
        return failure("audio_error", str(exc))
    except OSError:
        return failure(
            "io_error", "Cannot read/write audio or launch FFmpeg; check paths and permissions"
        )
    except Exception as exc:
        # Service exceptions may include URLs/headers. Never echo their values.
        logger.warning("Unexpected speech operation failure (%s)", type(exc).__name__)
        return failure(
            "service_error", "Microsoft speech service request failed; check network access"
        )
    finally:
        if not completed:
            for path in published:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove an incomplete output file")
        # CancelledError deliberately propagates, but still removes partial files.
        if work is not None:
            try:
                work.cleanup()
            except OSError:
                logger.warning(
                    "Could not remove temporary audio files; manual cleanup may be needed"
                )
