from __future__ import annotations

import re
import tomllib
from importlib.metadata import distribution
from pathlib import Path

from edge_voice_mcp import __version__
from edge_voice_mcp.server import main


def test_package_metadata_and_entrypoint_match_source() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    package = distribution("edge-voice-mcp")
    assert package.version == project["version"] == __version__
    assert package.metadata["License-Expression"] == "MIT"
    assert package.metadata["Requires-Python"] == ">=3.12"
    entry = next(entry for entry in package.entry_points if entry.name == "edge-voice-mcp")
    assert entry.load() is main


def test_docker_runtime_snapshot_is_hashed_and_runtime_only() -> None:
    snapshot = (Path(__file__).parents[1] / "requirements-runtime.txt").read_text(encoding="utf-8")
    lines = snapshot.replace("\\\n", "").splitlines()
    names = set()
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z0-9_.-]+)==[^*\s;]+", line)
        assert match and "--hash=sha256:" in line
        names.add(match.group(1).lower().replace("_", "-"))
    assert {"edge-tts", "mcp", "mutagen", "pydantic"} <= names
    assert not {"edge-voice-mcp", "pytest", "twine", "ruff", "imageio-ffmpeg"} & names
    assert "--index-url" not in snapshot
