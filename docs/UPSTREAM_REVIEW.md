# Review of eraincc/edge-tts-mcp

Source reviewed: <https://github.com/eraincc/edge-tts-mcp>, `main` as fetched
on 2026-11-04. This is a source review, not a reproduction of every reported
runtime problem. The repository has eight tracked root files and no test suite
or CI in the reviewed tree.

## Files

| File | Findings |
| --- | --- |
| `main.py` | Two sync MCP tools; `nest_asyncio.apply()` at import time; voice manager obtained via `asyncio.run()` and cached globally; speech consumes `stream_sync()`; timestamps have only second resolution; output is inside the installed package; optional SRT; broad exception wrapping; no application deadline or audio validation. |
| `pyproject.toml` | `edge-tts==7.0.2`, `nest-asyncio==1.6.0`, unbounded `mcp[cli]>=1.9.3`; Python >=3.11; script points to the root `main` module. No explicit build backend. |
| `requirements.txt` | Fully pinned older dependency snapshot, including MCP 1.9.3; not consistent with the open-ended SDK constraint in project metadata. |
| `uv.lock` | Resolution of the older dependency set; Edge TTS 7.0.2 and MCP 1.9.3. Not reused by this project. |
| `.python-version` | 3.11. |
| `.gitignore` | Python/build/cache and output exclusions. |
| `README.md` | Basic stdio/uvx setup and description of two tools. |
| `LICENSE` | LGPL v3 text. This is distinct from the licenses of `edge-tts` and MCP SDK. |

## Reported runtime evidence

The user observed a hang on Edge TTS 7.0.2 `stream_sync()`, SDK 2.x import
failure, and a Python 3.14 failure involving `nest_asyncio`. Their separate
Python 3.12 test of Edge TTS 7.3.1 `await Communicate.save()` produced a valid
Russian MP3. Source inspection supports removing nested/synchronous event
loops and unbounded SDK upgrades; it does not prove that all failures have
one cause or that Microsoft will always be available.

## Independent implementation decisions

- Keep tool names `tts` and `list_voice`, voice identifiers, and `audio_path`.
- Use async tools, one event loop, and `await Communicate.save()`.
- Pin Edge TTS 7.3.1 and official MCP Python SDK 1.30.0; do not claim SDK 2 support.
- Unique private staging directories, validation, atomic publication, cleanup on
  errors/cancellation; no writes to installed package directories.
- One bounded deadline for synthesis and optional FFmpeg OGG/Opus encoding.
- Parse MP3/OGG Opus locally with Mutagen; do not treat file existence as success.
- Safe errors: no raw network exception messages, speech text, or headers in responses.
- Package/CLI, mock and real-codec tests, stdio client test, opt-in Microsoft tests,
  and CI for Python 3.12/3.14 on Linux and Windows.

Not a drop-in clone: the original `srt_enable` argument is not supported. Version
0.2.0 uses `subtitles="srt"` / `"vtt"`, optional word timing JSON, rich voice
metadata and a third tool `get_capabilities`. `tts` returns explicit success/error
metadata and uses a configurable default voice (initially Russian). No original
implementation, license text, lockfile, or documentation is copied into the package.
