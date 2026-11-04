"""Offline duration fitting, including real encoders and cancellation cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from edge_tts_mcp.core import audio, service
from edge_tts_mcp.core.server import create_server
from edge_tts_mcp.core.service import Settings, synthesize
from edge_tts_mcp.core.timings import TimingError, Word, rescale_words


@pytest.fixture(autouse=True)
def fake_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most cases isolate probing; real encoder cases opt back into real FFprobe."""
    monkeypatch.setattr(service, "find_ffprobe", lambda executable: "ffprobe")

    async def probe(executable: str, path: Path) -> float:
        return service.validate_audio(path, path.suffix[1:])[1]

    monkeypatch.setattr(service, "probe_duration", probe)


@pytest.fixture
def ffprobe() -> str:
    executable = shutil.which(os.environ.get("EDGE_TTS_FFPROBE", "ffprobe"))
    if executable is None:
        pytest.skip("Real encoder integration requires FFprobe (EDGE_TTS_FFPROBE or PATH)")
    return executable


def save_mock(monkeypatch: pytest.MonkeyPatch, content: bytes, metadata: str = "") -> None:
    async def save(self: object, filename: str, metadata_filename: str | None = None) -> None:
        Path(filename).write_bytes(content)
        if metadata_filename:
            Path(metadata_filename).write_text(metadata, encoding="utf-8")

    monkeypatch.setattr(service.edge_tts.Communicate, "save", save)


@pytest.mark.parametrize("target", [None, "omitted", "custom_bounds"])
async def test_no_target_keeps_original_path(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, target: object
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    encoder = AsyncMock(side_effect=AssertionError("must not process"))
    monkeypatch.setattr(service, "adjust_tempo", encoder)
    monkeypatch.setattr(
        service, "find_ffmpeg", Mock(side_effect=AssertionError("must not resolve"))
    )
    monkeypatch.setattr(service, "find_ffprobe", Mock(side_effect=AssertionError("must not probe")))
    result = await synthesize(
        settings,
        "hi",
        **(
            {"tempo_min": 0.5, "tempo_max": 1.5}
            if target == "custom_bounds"
            else {}
            if target == "omitted"
            else {"target_duration_seconds": None}
        ),
    )
    assert result["success"] and "duration_fitting" not in result and "warnings" not in result
    assert Path(result["path"]).read_bytes() == mp3_bytes
    encoder.assert_not_called()


@pytest.mark.parametrize("tempo", [0.5, 0.85, 0.9, 1.1, 1.15, 1.5, 2.0])
@pytest.mark.parametrize("audio_format", ["mp3", "ogg"])
async def test_real_duration_fitting(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    tmp_path: Path,
    tempo: float,
    audio_format: str,
    ffprobe: str,
) -> None:
    settings = replace(settings, ffprobe=ffprobe)
    monkeypatch.setattr(service, "find_ffprobe", audio.find_ffprobe)
    monkeypatch.setattr(service, "probe_duration", audio.probe_duration)
    source = tmp_path / "tone.mp3"
    subprocess.run(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "libmp3lame",
            str(source),
        ],
        check=True,
        timeout=10,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    original = audio.validate_audio(source, "mp3")[1]
    save_mock(monkeypatch, source.read_bytes())
    result = await synthesize(
        settings,
        "hi",
        audio_format=audio_format,
        target_duration_seconds=original / tempo,
        tempo_min=0.5,
        tempo_max=2.0,
    )
    assert result["success"], result
    fitting = result["duration_fitting"]
    assert fitting["processed"], result
    assert fitting["applied_tempo"] == pytest.approx(tempo)
    assert result["tempo"] == pytest.approx(tempo)
    actual = await audio.probe_duration(ffprobe, Path(result["path"]))
    assert result["duration_seconds"] == round(actual, 3)
    within_tolerance = abs(actual - original / tempo) <= fitting["tolerance_seconds"]
    assert fitting["target_met"] == within_tolerance
    if not within_tolerance:
        assert any(warning["code"] == "duration_mismatch" for warning in result["warnings"])
    else:
        assert not result.get("warnings")


@pytest.mark.parametrize("tempo", [0.49, 2.01, float("inf")])
async def test_outside_limit_returns_unreachable_without_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, tempo: float
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    result = await synthesize(
        replace(settings, ffmpeg="missing-ffmpeg"),
        "hi",
        target_duration_seconds=10 / tempo if tempo != float("inf") else 5e-324,
    )
    assert not result["success"] and result["error_code"] == "target_duration_unreachable"
    assert result["required_tempo"] == (tempo if tempo != float("inf") else None)
    assert result["tempo_min"] == 0.5 and result["tempo_max"] == 2.0
    assert result["source_duration"] == 10.0
    assert result["target_duration"] == (10 / tempo if tempo != float("inf") else 5e-324)
    assert "path" not in result and list(settings.output_dir.iterdir()) == []
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("tempo", [0.5, 2.0])
@pytest.mark.parametrize("audio_format", ["mp3", "ogg"])
async def test_real_encoder_technical_bounds_preserve_source(
    settings: Settings, tmp_path: Path, mp3_bytes: bytes, tempo: float, audio_format: str
) -> None:
    source = tmp_path / "source.mp3"
    destination = tmp_path / f"processed.{audio_format}"
    source.write_bytes(mp3_bytes)
    await audio.adjust_tempo(settings.ffmpeg, source, destination, audio_format, tempo)
    assert source.read_bytes() == mp3_bytes
    _, original = audio.validate_audio(source, "mp3")
    _, actual = audio.validate_audio(destination, audio_format)
    assert (actual > original) if tempo < 1 else (actual < original)


@pytest.mark.parametrize(
    "target", [float("nan"), float("inf"), -float("inf"), 0, -1, 301, True, "2"]
)
async def test_invalid_target_before_network(settings: Settings, target: object) -> None:
    result = await synthesize(settings, "hi", target_duration_seconds=target)
    assert result["error_code"] == "invalid_input"
    assert not settings.output_dir.exists()


@pytest.mark.parametrize(
    "bounds",
    [
        {"tempo_min": 0.49},
        {"tempo_max": 2.01},
        {"tempo_min": 1.2, "tempo_max": 1.1},
        {"tempo_min": float("nan")},
        {"tempo_max": float("inf")},
        {"tempo_min": -float("inf")},
        {"tempo_min": True},
        {"tempo_max": "1.2"},
    ],
)
async def test_invalid_bounds_before_network(settings: Settings, bounds: dict) -> None:
    result = await synthesize(settings, "hi", target_duration_seconds=10, **bounds)
    assert result["error_code"] == "invalid_input"
    assert not settings.output_dir.exists()


async def test_custom_narrow_range(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    result = await synthesize(
        settings, "hi", target_duration_seconds=9, tempo_min=0.99, tempo_max=1.01
    )
    assert result["error_code"] == "target_duration_unreachable"
    assert result["required_tempo"] == pytest.approx(10 / 9)
    assert (result["tempo_min"], result["tempo_max"]) == (0.99, 1.01)
    assert list(settings.output_dir.iterdir()) == []


@pytest.mark.parametrize("tempo", [0.5, 0.85, 1.15, 1.5, 2.0])
async def test_custom_inclusive_bounds(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, tempo: float
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    monkeypatch.setattr(
        service,
        "validate_audio",
        lambda path, fmt: (path.stat().st_size, 10.0 if path.name == "speech.mp3" else 10 / tempo),
    )

    async def encode(
        executable: str, source: Path, destination: Path, fmt: str, factor: float
    ) -> None:
        assert factor == pytest.approx(tempo)
        destination.write_bytes(mp3_bytes)

    monkeypatch.setattr(service, "adjust_tempo", encode)
    result = await synthesize(
        settings, "hi", target_duration_seconds=10 / tempo, tempo_min=tempo, tempo_max=tempo
    )
    assert result["success"] and result["duration_fitting"]["target_met"], result
    assert result["duration_fitting"]["applied_tempo"] == pytest.approx(tempo)


async def test_missing_probe_is_safe_error(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    monkeypatch.setattr(service, "find_ffprobe", audio.find_ffprobe)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    result = await synthesize(
        replace(settings, ffprobe="missing-ffprobe"), "hi", target_duration_seconds=9
    )
    assert result["error_code"] == "audio_error" and "requires FFprobe" in result["error"]
    assert list(settings.output_dir.iterdir()) == []


async def test_missing_ffmpeg_when_fitting_required(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    result = await synthesize(
        replace(settings, ffmpeg="missing-ffmpeg"), "hi", target_duration_seconds=9.5
    )
    assert result["error_code"] == "audio_error" and "fitting requires FFmpeg" in result["error"]
    assert list(settings.output_dir.iterdir()) == []


async def test_equal_duration_needs_no_encoder(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    result = await synthesize(
        replace(settings, ffmpeg="missing-ffmpeg"), "hi", target_duration_seconds=10
    )
    assert result["success"] and result["duration_fitting"]["target_met"]
    assert not result["duration_fitting"]["processed"] and "warnings" not in result
    assert Path(result["path"]).read_bytes() == mp3_bytes


async def test_ogg_outside_limit_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    result = await synthesize(settings, "hi", audio_format="ogg", target_duration_seconds=100)
    assert result["error_code"] == "target_duration_unreachable"
    assert "path" not in result and list(settings.output_dir.iterdir()) == []


@pytest.mark.parametrize("failure", ["encoder", "invalid_output", "io", "mismatch", "probe"])
async def test_fitting_failure_preserves_original(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, failure: str
) -> None:
    save_mock(monkeypatch, mp3_bytes)

    def validate(path: Path, audio_format: str) -> tuple[int, float]:
        if path.name.startswith("fitted") and failure == "invalid_output":
            raise audio.AudioError("invalid audio")
        return path.stat().st_size, 10.0

    async def encode(ffmpeg: str, source: Path, destination: Path, fmt: str, tempo: float) -> None:
        assert source != destination and source.read_bytes() == mp3_bytes
        destination.write_bytes(b"processed")
        if failure == "encoder":
            raise audio.AudioError("private details")
        if failure == "io":
            raise OSError("private details")
        assert source.read_bytes() == mp3_bytes

    monkeypatch.setattr(service, "validate_audio", validate)
    monkeypatch.setattr(service, "adjust_tempo", encode)
    if failure == "probe":
        monkeypatch.setattr(
            service,
            "probe_duration",
            AsyncMock(
                side_effect=[
                    audio.AudioError("FFprobe could not verify audio duration"),
                    10.0,
                ]
            ),
        )
    result = await synthesize(settings, "hi", target_duration_seconds=9.0)
    assert result["success"] and not result["duration_fitting"]["target_met"]
    assert "private" not in str(result)
    assert result["duration_fitting"]["processed"] == (failure == "mismatch")
    assert Path(result["path"]).read_bytes() == (
        b"processed" if failure == "mismatch" else mp3_bytes
    )
    assert len(list(settings.output_dir.iterdir())) == 1


@pytest.mark.parametrize("subtitle_format", ["srt", "vtt"])
@pytest.mark.parametrize("tempo", [0.8, 1.2])
async def test_rescaled_sidecars(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    mp3_bytes: bytes,
    subtitle_format: str,
    tempo: float,
) -> None:
    event = {"type": "WordBoundary", "text": "hello!", "offset": 1_200_000, "duration": 2_400_000}
    save_mock(monkeypatch, mp3_bytes, json.dumps(event))
    monkeypatch.setattr(
        service,
        "validate_audio",
        lambda path, fmt: (len(mp3_bytes), 10.0 if path.name == "speech.mp3" else 10 / tempo),
    )

    async def encode(ffmpeg: str, source: Path, destination: Path, fmt: str, factor: float) -> None:
        destination.write_bytes(mp3_bytes)

    monkeypatch.setattr(service, "adjust_tempo", encode)
    result = await synthesize(
        settings,
        "hello!",
        target_duration_seconds=10 / tempo,
        tempo_min=0.8,
        tempo_max=1.2,
        subtitles=subtitle_format,
        word_timings=True,
    )
    assert result["success"] and "warnings" not in result
    words = json.loads(Path(result["word_timings_path"]).read_text(encoding="utf-8"))
    assert words == [
        {"word": "hello!", "start_ms": round(120 / tempo), "end_ms": round(360 / tempo)}
    ]
    content = Path(result["subtitle_path"]).read_text(encoding="utf-8")
    sep = "," if subtitle_format == "srt" else "."
    assert (
        f"00:00:00{sep}{round(120 / tempo):03} --> 00:00:00{sep}{round(360 / tempo):03}" in content
    )


@pytest.mark.parametrize(
    "metadata",
    [
        "invalid json",
        "",
        json.dumps({"type": "WordBoundary", "text": "hi", "offset": 0, "duration": 200_000_000}),
    ],
)
async def test_bad_metadata_does_not_replace_duration_warning(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, metadata: str
) -> None:
    save_mock(monkeypatch, mp3_bytes, metadata)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    result = await synthesize(
        settings, "hi", target_duration_seconds=9, subtitles="srt", word_timings=True
    )
    assert result["success"]
    assert {warning["code"] for warning in result["warnings"]} >= {
        "duration_mismatch",
        "timing_metadata_unavailable",
    }
    assert "subtitle_path" not in result and "word_timings_path" not in result


@pytest.mark.parametrize(
    "words,tempo,duration",
    [
        ([Word("a", 2, 3)], 1.2, 1),  # rounding collapses the interval
        ([Word("a", 0, 200)], 0.8, 0.1),
        ([Word("a", -1, 200)], 1, 1),
        ([Word("a", 0, 200), Word("b", 100, 300)], 1, 1),
        ([], 1, 1),
        ([Word("a", 0, 200)], float("nan"), 1),
    ],
)
def test_invalid_scaled_boundaries(words: list[Word], tempo: float, duration: float) -> None:
    with pytest.raises(TimingError):
        rescale_words(words, tempo, duration)


@pytest.mark.parametrize("audio_format", ["mp3", "ogg"])
async def test_encoder_uses_exec_not_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio_format: str
) -> None:
    source, destination = tmp_path / "source.mp3", tmp_path / f"output.{audio_format}"
    source.write_bytes(b"original")
    process = Mock(returncode=1, wait=AsyncMock(return_value=1))
    execute = AsyncMock(return_value=process)
    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", execute)
    monkeypatch.setattr(
        audio.asyncio, "create_subprocess_shell", AsyncMock(side_effect=AssertionError("shell"))
    )
    with pytest.raises(audio.AudioError, match="duration fitting failed"):
        await audio.adjust_tempo("ffmpeg", source, destination, audio_format, 1.1)
    args, kwargs = execute.call_args
    assert "atempo=1.1" in args and "shell" not in kwargs
    assert str(source) in args and args[-1] == str(destination)
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("cancel", [False, True])
async def test_fitting_timeout_and_cancellation_reap_process(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mp3_bytes: bytes, cancel: bool
) -> None:
    save_mock(monkeypatch, mp3_bytes)
    monkeypatch.setattr(service, "validate_audio", lambda *args: (len(mp3_bytes), 10.0))
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = False
        reaped = False

        async def wait(self) -> int:
            if not self.killed:
                started.set()
                await asyncio.Event().wait()
            self.reaped = True
            self.returncode = -9
            return -9

        def kill(self) -> None:
            self.killed = True

    process = Process()
    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(
        synthesize(settings, "hi", target_duration_seconds=9, timeout=2 if cancel else 0.05)
    )
    await asyncio.wait_for(started.wait(), 2)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task)["error_code"] == "timeout"
    assert process.killed and process.reaped
    assert list(settings.output_dir.iterdir()) == []


async def test_mcp_target_schema_and_forwarding(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    synth = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("edge_tts_mcp.core.server.synthesize", synth)
    mcp = create_server(settings)
    schema = next(tool for tool in await mcp.list_tools() if tool.name == "tts").inputSchema
    target = schema["properties"]["target_duration_seconds"]
    assert target["default"] is None
    assert target["anyOf"][0]["exclusiveMinimum"] == 0
    await mcp.call_tool("tts", {"text": "hi", "target_duration_seconds": 2})
    assert synth.call_args.args[-4:] == (2, 0.5, 2.0, 300)
    assert schema["properties"]["tempo_min"]["minimum"] == 0.5
    assert schema["properties"]["tempo_max"]["maximum"] == 2.0
    await mcp.call_tool("tts", {"text": "hi", "tempo_min": 0.5, "tempo_max": 1.5})
    assert synth.call_args.args[-3:] == (0.5, 1.5, 300)
    for value in [0, -1, float("nan"), float("inf"), True, "2"]:
        with pytest.raises(Exception):
            await mcp.call_tool("tts", {"text": "hi", "target_duration_seconds": value})
    for name in ["tempo_min", "tempo_max"]:
        for value in [0.49, 2.01, float("nan"), float("inf"), True, "1.0", None]:
            with pytest.raises(Exception):
                await mcp.call_tool("tts", {"text": "hi", name: value})


@pytest.mark.parametrize(
    "tempo,same_path", [(0.49, False), (2.01, False), (float("nan"), False), (1.1, True)]
)
async def test_encoder_rejects_unsafe_parameters(
    tmp_path: Path, tempo: float, same_path: bool
) -> None:
    source = tmp_path / "original.mp3"
    with pytest.raises(audio.AudioError, match="Invalid"):
        await audio.adjust_tempo(
            "ffmpeg", source, source if same_path else tmp_path / "out.mp3", "mp3", tempo
        )
