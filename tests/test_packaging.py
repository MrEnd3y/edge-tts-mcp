from __future__ import annotations

import re
import tomllib
from importlib.metadata import distribution
from pathlib import Path

from edge_tts_mcp import __version__
from edge_tts_mcp.core.cli import main


def test_package_metadata_and_entrypoint_match_source() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    package = distribution("edge-tts-mcp")
    assert package.version == project["version"] == __version__
    assert package.metadata["License-Expression"] == "MIT"
    assert package.metadata["Requires-Python"] == ">=3.12"
    entry = next(entry for entry in package.entry_points if entry.name == "edge-tts-mcp")
    assert entry.load() is main


def test_runtime_requirements_match_package_dependencies() -> None:
    root = Path(__file__).parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert all(re.fullmatch(r"[a-zA-Z0-9_.-]+==[0-9][^\s]*", line) for line in requirements)
    assert set(requirements) == set(project["dependencies"])
