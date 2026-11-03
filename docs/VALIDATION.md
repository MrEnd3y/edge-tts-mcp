# Local validation

## Version 0.2.1 (pre-publication review)

On Windows, the reviewed code passes **85 offline tests** per interpreter,
with 7 live and 2 Docker tests skipped in the default run. Branch coverage is
about **95%** on Python 3.12.4 and 3.14.8; the CI minimum is 90%.

All **7 opt-in Microsoft tests were rerun and passed on both interpreters**,
including real SRT/VTT, OGG/Opus, word events and the >5000-character request.
These timings varied substantially from earlier runs; no throughput SLA follows.

Other checks:

- Ruff lint/format and mypy pass; Bandit reports no findings in owned source.
- `pip-audit` reports no known advisories in the pinned runtime export. OS,
  interpreter, FFmpeg and unknown vulnerabilities are outside that result.
- Wheel and sdist build; installation/CLI smoke checks succeed from the wheel
  on Python 3.12 and from sdist on 3.14 (`0.2.1`).
- Strict metadata validation initially failed with Twine 6.2 / metadata 2.5;
  after updating to Twine 7, both wheel and sdist pass `twine check --strict`.
- Docker now uses only pip/wheels. Its runtime-only dependency snapshot is
  pinned and hashed; a test checks its shape and CI checks export consistency.
- Docker Compose configuration validates without needing the engine. **The
  Docker image has not been built/run locally** because the Linux engine is
  not running. Two offline container tests and a CI image job are prepared.

The five reproduced pre-review defects, hardening changes and remaining release
decisions are detailed in [PUBLICATION_REVIEW.md](PUBLICATION_REVIEW.md).

## Version 0.2.0

On Windows, the offline suite passes on both Python 3.12.4 and 3.14.8:
**59 passed, 7 opt-in live tests skipped** per interpreter. This includes
requested-only sidecars, structured MCP content, profiles/defaults, Unicode
chunking, SRT/VTT serialization, invalid/missing timing metadata, disabled
features, cancellation and rollback after a sidecar write/publication failure.

Ruff lint/format checks pass. Wheel/sdist build successfully, do not include
`.comparison/`, and include the timing module / roadmap as appropriate.
`uvx --python 3.12 --from . edge-voice-mcp --version` returns `0.2.0`;
the 3.14 uvx CLI help lists the new voice and sidecar options.

Online checks: **7 passed per interpreter**, run as six short tests plus one
long-text test. These cover the baseline speech/voice checks, SRT/MP3 and VTT/OGG
with real WordBoundary events, and long-text timing offsets across library chunks.

The 5640-character long-text test at normal rate initially exceeded its
180-second deadline on both interpreters and returned a safe timeout failure,
not a partial successful result. A repeat with explicit rate `+100%` and timeout
300 seconds **passed on both** (about 188 s on 3.12 and 212 s on 3.14).
The 3.12 output contained one 226.752-second MP3 (1360512 bytes), SRT and 840
word events; the last word ended at 226321 ms. These are measurements from this
test, not a throughput guarantee or a recommendation to silently accelerate speech.

Maximum-length 100000-character requests were not tested. They may exceed the
maximum deadline; book-length workloads need a separate background job/resume
design rather than an indefinitely open MCP call. See the roadmap's future section.

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

## Integration limits (both versions)

Not verified here: Linux execution (CI workflow supplied but not run on GitHub),
real Hermes integration, Telegram delivery, GitHub installation, or PyPI name
availability/publication. No remote server, Hermes config or built-in TTS was
modified. The Microsoft endpoint may behave differently on another network or
at another time; rerun the opt-in live tests on the deployment host.
