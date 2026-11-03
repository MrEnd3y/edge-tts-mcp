from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from edge_voice_mcp.service import Settings


@pytest.fixture(scope="session")
def ffmpeg() -> str:
    # Bundled test-only executable. Production uses system FFmpeg or an explicit path.
    return imageio_ffmpeg.get_ffmpeg_exe()


@pytest.fixture(scope="session")
def mp3_bytes(tmp_path_factory: pytest.TempPathFactory, ffmpeg: str) -> bytes:
    path = tmp_path_factory.mktemp("fixture") / "tone.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.3",
            "-c:a",
            "libmp3lame",
            str(path),
        ],
        check=True,
        timeout=10,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path.read_bytes()


@pytest.fixture
def settings(tmp_path: Path, ffmpeg: str) -> Settings:
    return Settings(output_dir=tmp_path / "audio output", ffmpeg=ffmpeg)
