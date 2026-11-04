"""Keep the documented configuration surface aligned with code and MCP schema."""

from __future__ import annotations

import ast
from pathlib import Path

from edge_tts_mcp.config import Settings
from edge_tts_mcp.core.server import create_server

ROOT = Path(__file__).parents[1]


async def test_readme_documents_every_tts_argument(settings: Settings) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tool = next(tool for tool in await create_server(settings).list_tools() if tool.name == "tts")
    for name in tool.inputSchema["properties"]:
        assert f"| `{name}` |" in readme, f"Missing tts argument documentation: {name}"


def test_readme_documents_every_server_environment_variable() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    module = ast.parse((ROOT / "edge_tts_mcp" / "config.py").read_text(encoding="utf-8"))
    names = {
        node.value
        for node in ast.walk(module)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("EDGE_TTS_")
        and node.value.replace("_", "").isalnum()
    }
    assert names
    for name in names:
        assert f"| `{name}` |" in readme, f"Missing ENV documentation: {name}"
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    for name in names:
        assert f"      {name}:" in compose, f"Missing Compose environment mapping: {name}"


def test_readme_structure_and_entrypoint() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "edge_tts_mcp.core.cli:main" in readme
    assert "edge_tts_mcp.cli:main" not in readme
    assert "config.py" in readme and "core/" in readme
