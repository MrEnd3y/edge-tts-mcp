"""Command-line configuration and stdio server startup."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import __version__
from ..config import settings_from_environment
from .server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Async Microsoft Edge TTS MCP server (stdio)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--output-dir", type=Path, help="Override EDGE_TTS_OUTPUT_DIR")
    parser.add_argument("--ffmpeg", help="Override EDGE_TTS_FFMPEG executable path")
    parser.add_argument("--ffprobe", help="Override EDGE_TTS_FFPROBE executable path")
    parser.add_argument("--voice", help="Override EDGE_TTS_VOICE default voice")
    parser.add_argument(
        "--max-concurrent", type=int, help="Concurrent synthesis limit (1-16); default 2"
    )
    parser.add_argument(
        "--disable-sidecars", action="store_true", help="Reject subtitle/timing requests"
    )
    args = parser.parse_args()
    try:
        settings = settings_from_environment(
            output_dir=args.output_dir,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            voice=args.voice,
            disable_sidecars=args.disable_sidecars,
            max_concurrent=args.max_concurrent,
        )
    except ValueError as exc:
        parser.error(str(exc))
    create_server(settings).run(transport="stdio")
