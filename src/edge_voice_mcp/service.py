"""Synthesis operations independent of the MCP transport."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import edge_tts

from .audio import AudioError, convert_to_opus, find_ffmpeg, validate_audio
from .timings import SubtitleFormat, TimingError, read_words, render_subtitles

logger = logging.getLogger(__name__)
AudioFormat = Literal["mp3", "ogg"]
SpeechProfile = Literal["default", "narration", "notification", "slow"]
MAX_TEXT_LENGTH = 100_000
PROFILES = {
    "narration": {"rate": "-10%", "volume": "+0%", "pitch": "+0Hz"},
    "notification": {"rate": "+15%", "volume": "+0%", "pitch": "+0Hz"},
    "slow": {"rate": "-25%", "volume": "+0%", "pitch": "+0Hz"},
}


@dataclass(frozen=True)
class Settings:
    output_dir: Path
    ffmpeg: str = "ffmpeg"
    voice: str = "ru-RU-SvetlanaNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    allow_sidecars: bool = True
    max_concurrent: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.output_dir, Path):
            raise ValueError("output_dir must be a pathlib.Path")
        if not isinstance(self.ffmpeg, str) or not self.ffmpeg.strip():
            raise ValueError("FFmpeg executable must not be empty")
        if not isinstance(self.voice, str) or not self.voice.strip():
            raise ValueError("Default voice must not be empty")
        if type(self.allow_sidecars) is not bool:
            raise ValueError("allow_sidecars must be boolean")
        if type(self.max_concurrent) is not int or not 1 <= self.max_concurrent <= 16:
            raise ValueError("max_concurrent must be an integer between 1 and 16")
        try:
            # Constructor validation only: no connection or synthesis is started.
            edge_tts.Communicate(
                "Configuration check",
                self.voice.strip(),
                rate=self.rate,
                volume=self.volume,
                pitch=self.pitch,
            )
        except (TypeError, ValueError):
            raise ValueError("Invalid default voice, rate, volume, or pitch") from None


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
    timeout: float = 45,
    subtitles: SubtitleFormat = "none",
    word_timings: bool = False,
    profile: SpeechProfile = "default",
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """Save, validate, optionally convert, and publish audio with requested sidecars."""
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
    if audio_format not in ("mp3", "ogg") or not 0 < timeout <= 300:
        return failure("invalid_input", "Use mp3/ogg and a timeout greater than 0 and at most 300")
    if subtitles not in ("none", "srt", "vtt"):
        return failure("invalid_input", "Subtitles must be none, srt, or vtt")
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
            ffmpeg = find_ffmpeg(settings.ffmpeg) if audio_format == "ogg" else None
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
            size, duration = validate_audio(mp3_path, "mp3")
            result_path = mp3_path
            if ffmpeg is not None:
                result_path = Path(work.name) / "speech.ogg"
                await convert_to_opus(ffmpeg, mp3_path, result_path)
                size, duration = validate_audio(result_path, "ogg")
            stem = f"edge-tts-{uuid.uuid4().hex}"
            final_path = directory / f"{stem}.{audio_format}"
            outputs = [(result_path, final_path)]
            result = {
                "success": True,
                "path": str(final_path),
                "audio_path": str(final_path),
                "size": size,
                "duration_seconds": round(duration, 3),
                "format": audio_format,
                "mime_type": "audio/mpeg" if audio_format == "mp3" else "audio/ogg",
                "voice": voice.strip(),
                "rate": rate,
                "volume": volume,
                "pitch": pitch,
                "profile": profile,
                "characters": len(text),
            }
            if needs_metadata:
                try:
                    words = read_words(metadata_path)
                    # Allow small codec/header rounding differences, not cues
                    # far beyond the generated recording.
                    if words[-1].end_ms > duration * 1000 + 500:
                        raise TimingError("Word events exceed audio duration")
                except TimingError:
                    result["warnings"] = [
                        {
                            "code": "timing_metadata_unavailable",
                            "message": "Audio was generated, but valid word events were not returned; no subtitle/timing files were created",
                        }
                    ]
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
