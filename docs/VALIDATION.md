# Local validation

## Version 0.2.0

On Windows, the offline suite passes on both Python 3.12.4 and 3.14.8:
**57 passed, 7 opt-in live tests skipped** per interpreter. This includes
requested-only sidecars, structured MCP content, profiles/defaults, Unicode
chunking, SRT/VTT serialization, invalid/missing timing metadata, disabled
features, cancellation and rollback after a sidecar write/publication failure.

Ruff lint/format checks pass. Wheel/sdist build successfully, do not include
`.comparison/`, and include the timing module / roadmap as appropriate.
`uvx --python 3.12 --from . edge-voice-mcp --version` returns `0.2.0`;
the 3.14 uvx CLI help lists the new voice and sidecar options.

Online checks: 6 passed per interpreter (the baseline speech/voice checks plus
SRT/MP3 and VTT/OGG with real WordBoundary events). The >5000-character long-text
test at normal rate exceeded its 180-second deadline on both interpreters and
returned a safe timeout failure, not a partial successful result. A repeat with
explicit rate `+100%` and timeout 300 seconds is pending. This is not a claim
that all 100000-character jobs can finish within the maximum deadline.

## Version 0.1.0 (earlier baseline)

Executed on Windows on 2026-11-04, independently for both interpreters:

| Check | Python 3.12.4 | Python 3.14.8 |
| --- | --- | --- |
| Offline suite | 28 passed, 4 live tests skipped | 28 passed, 4 live tests skipped |
| Live Microsoft tests | 4 passed | 4 passed |

Live tests requested real speech, saved audio with `await Communicate.save()`,
and checked positive duration and codec/container using Mutagen:

- `ru-RU-SvetlanaNeural`: Russian MP3.
- `ru-RU-DmitryNeural`: Russian speech converted to OGG/Opus with FFmpeg.
- `en-US-AriaNeural`: English MP3.
- Online voice listing contains Svetlana.

The offline suite includes an actual subprocess MCP stdio handshake,
tool discovery and tool calls, cancellation cleanup, deadline handling,
sanitization of errors, unique concurrent output names and codec conversion.
Test codecs are exercised with the test-only imageio-ffmpeg executable;
production OGG output requires a separate configured FFmpeg installation.

Other completed checks:

- `ruff check .` and `ruff format --check .`.
- `uv build`: wheel and source distribution built successfully.
- `uvx --python 3.12 --from . edge-voice-mcp --version`: `0.1.0`.

Not verified here: Linux execution (CI workflow supplied but not run on GitHub),
real Hermes integration, Telegram delivery, GitHub installation, or PyPI name
availability/publication. No remote server, Hermes config or built-in TTS was
modified. The Microsoft endpoint may behave differently on another network or
at another time; rerun the opt-in live tests on the deployment host.
