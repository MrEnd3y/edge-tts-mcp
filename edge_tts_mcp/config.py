"""Validated server defaults: explicit configuration > ENV > built-in defaults.

Only documented, nonsecret environment variables are read. No dotenv file is
loaded; a launcher may explicitly provide one. MCP request overrides are resolved
by the synthesis service and cannot exceed the technical tempo/timeout bounds.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import edge_tts

TECHNICAL_TEMPO_MIN = 0.5
TECHNICAL_TEMPO_MAX = 2.0
DEFAULT_TEMPO_MIN = 0.5
DEFAULT_TEMPO_MAX = 2.0
MAX_TIMEOUT_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 45.0
# Clip length is independent of the wall-clock operation budget.
DEFAULT_MAX_AUDIO_DURATION_SECONDS = 300.0
DEFAULT_OUTPUT_DIR = Path.home() / ".edge-tts-mcp" / "output"


def positive_finite(value: float) -> bool:
    if type(value) not in (int, float) or value <= 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def valid_tempo_bounds(tempo_min: float, tempo_max: float) -> bool:
    return (
        type(tempo_min) in (int, float)
        and type(tempo_max) in (int, float)
        and TECHNICAL_TEMPO_MIN <= tempo_min <= tempo_max <= TECHNICAL_TEMPO_MAX
    )


@dataclass(frozen=True)
class Settings:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    ffmpeg: str = "ffmpeg"
    voice: str = "ru-RU-SvetlanaNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    allow_sidecars: bool = True
    max_concurrent: int = 2
    ffprobe: str = "ffprobe"
    tempo_min: float = DEFAULT_TEMPO_MIN
    tempo_max: float = DEFAULT_TEMPO_MAX
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_audio_duration_seconds: float = DEFAULT_MAX_AUDIO_DURATION_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.output_dir, Path):
            raise ValueError("output_dir must be a pathlib.Path")
        if not isinstance(self.ffmpeg, str) or not self.ffmpeg.strip():
            raise ValueError("FFmpeg executable (EDGE_TTS_FFMPEG) must not be empty")
        if not isinstance(self.ffprobe, str) or not self.ffprobe.strip():
            raise ValueError("FFprobe executable (EDGE_TTS_FFPROBE) must not be empty")
        if not isinstance(self.voice, str) or not self.voice.strip():
            raise ValueError("Default voice (EDGE_TTS_VOICE) must not be empty")
        if type(self.allow_sidecars) is not bool:
            raise ValueError("allow_sidecars must be boolean")
        if type(self.max_concurrent) is not int or not 1 <= self.max_concurrent <= 16:
            raise ValueError("EDGE_TTS_MAX_CONCURRENT must be an integer between 1 and 16")
        if not valid_tempo_bounds(self.tempo_min, self.tempo_max):
            raise ValueError("Tempo defaults must satisfy 0.5 <= tempo_min <= tempo_max <= 2.0")
        if type(self.timeout) not in (int, float) or not 0 < self.timeout <= MAX_TIMEOUT_SECONDS:
            raise ValueError(
                "Default timeout must be a finite number greater than 0 and at most 300"
            )
        if not positive_finite(self.max_audio_duration_seconds):
            raise ValueError("Maximum audio duration must be a finite number greater than 0")
        try:
            # Constructor validation only; no connection or synthesis is started.
            edge_tts.Communicate(
                "Configuration check",
                self.voice.strip(),
                rate=self.rate,
                volume=self.volume,
                pitch=self.pitch,
            )
        except (TypeError, ValueError):
            raise ValueError(
                "Invalid default voice, rate, volume, or pitch (EDGE_TTS_VOICE/RATE/VOLUME/PITCH)"
            ) from None


def _environment_number(
    name: str,
    default: float,
    *,
    minimum: float = 0,
    maximum: float | None = None,
    inclusive_minimum: bool = False,
) -> float:
    """Never echo supplied environment values in errors."""
    message = f"{name} must be a finite number "
    message += f"at least {minimum:g}" if inclusive_minimum else f"greater than {minimum:g}"
    if maximum is not None:
        message += f" and at most {maximum:g}"
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        raise ValueError(message) from None
    if (
        not math.isfinite(value)
        or (value < minimum if inclusive_minimum else value <= minimum)
        or (maximum is not None and value > maximum)
    ):
        raise ValueError(message)
    return value


def settings_from_environment(
    *,
    output_dir: Path | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    voice: str | None = None,
    disable_sidecars: bool = False,
    max_concurrent: int | None = None,
) -> Settings:
    """Read documented ENV, apply CLI overrides, and validate before startup."""
    flag = (
        "0" if disable_sidecars else os.environ.get("EDGE_TTS_ALLOW_SIDECARS", "1").strip().lower()
    )
    if flag not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
        raise ValueError("EDGE_TTS_ALLOW_SIDECARS must be 1/0, true/false, yes/no, or on/off")
    if output_dir is None:
        path = os.environ.get("EDGE_TTS_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))
        if not path.strip():
            raise ValueError("EDGE_TTS_OUTPUT_DIR must not be empty")
        output_dir = Path(path)
    if max_concurrent is None:
        try:
            max_concurrent = int(os.environ.get("EDGE_TTS_MAX_CONCURRENT", "2"))
        except ValueError:
            raise ValueError(
                "EDGE_TTS_MAX_CONCURRENT must be an integer between 1 and 16"
            ) from None
    tempo_min = _environment_number(
        "EDGE_TTS_TEMPO_MIN",
        DEFAULT_TEMPO_MIN,
        minimum=TECHNICAL_TEMPO_MIN,
        maximum=TECHNICAL_TEMPO_MAX,
        inclusive_minimum=True,
    )
    tempo_max = _environment_number(
        "EDGE_TTS_TEMPO_MAX",
        DEFAULT_TEMPO_MAX,
        minimum=TECHNICAL_TEMPO_MIN,
        maximum=TECHNICAL_TEMPO_MAX,
        inclusive_minimum=True,
    )
    if tempo_min > tempo_max:
        raise ValueError("EDGE_TTS_TEMPO_MIN must not exceed EDGE_TTS_TEMPO_MAX")
    return Settings(
        output_dir=output_dir,
        ffmpeg=os.environ.get("EDGE_TTS_FFMPEG", "ffmpeg") if ffmpeg is None else ffmpeg,
        ffprobe=os.environ.get("EDGE_TTS_FFPROBE", "ffprobe") if ffprobe is None else ffprobe,
        voice=os.environ.get("EDGE_TTS_VOICE", "ru-RU-SvetlanaNeural") if voice is None else voice,
        rate=os.environ.get("EDGE_TTS_RATE", "+0%"),
        volume=os.environ.get("EDGE_TTS_VOLUME", "+0%"),
        pitch=os.environ.get("EDGE_TTS_PITCH", "+0Hz"),
        allow_sidecars=flag in ("1", "true", "yes", "on"),
        max_concurrent=max_concurrent,
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        timeout=_environment_number(
            "EDGE_TTS_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, maximum=MAX_TIMEOUT_SECONDS
        ),
        max_audio_duration_seconds=_environment_number(
            "EDGE_TTS_MAX_AUDIO_DURATION_SECONDS", DEFAULT_MAX_AUDIO_DURATION_SECONDS
        ),
    )
