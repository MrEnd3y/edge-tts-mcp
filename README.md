# edge-voice-mcp

Independent, asynchronous Microsoft Edge TTS server built on the official
Python MCP SDK. Three stdio tools: **`list_voice`**, **`tts`**, and
**`get_capabilities`**. No
`stream_sync()`, nested event loops, `nest_asyncio`, or changes to Hermes' built-in TTS.

**Status:** development; not yet published to PyPI. `edge-voice-mcp` is a working
name, not a claim that the PyPI/GitHub name is available.

## Features

- All Microsoft Edge voices/languages; locale/gender filters, metadata search and pagination.
- Configurable voice defaults, speaking rate, pitch, volume, and optional parameter presets.
- MP3 without FFmpeg; OGG/Opus (mono, 48 kHz, 48 kbps) with FFmpeg/libopus.
- A single configurable deadline for synthesis + conversion (default 45 s).
- Container/codec and positive-duration validation, not just file existence.
- Unique names, private staging, atomic output publication and cancellation cleanup.
- Configurable local output directory; no credentials/API keys required by this server.
- Text up to 100,000 characters using edge-tts' internal chunking and continuous offsets.
- Optional SRT/WebVTT captions and JSON word timings from Microsoft WordBoundary events.
- **Audio only by default**: no caption/timing files unless explicitly requested.

Runtime pins: **edge-tts 7.3.1**, **MCP SDK 1.30.0**, **Mutagen 1.47.0**.
MCP SDK 2.x is intentionally excluded. Python >=3.12; CI targets 3.12/3.14
on Linux and Windows. See the test commands below for local verification.
See [validation results](docs/VALIDATION.md) for the exact tested version and cases.
GitHub CI has not yet run. A Russian-language overview is in [the roadmap](docs/ROADMAP.md).

## Run locally

### uv (optional convenience)

Install [uv](https://docs.astral.sh/uv/), then from this checkout:

```sh
uv sync --locked --no-dev
uv run --locked edge-voice-mcp --help
uv run --locked edge-voice-mcp
```

The last command waits for MCP stdio messages; this is not a hang. Use an MCP
client to call tools. It is not an interactive speech CLI.

For OGG, install FFmpeg separately with `libopus` support. MP3 needs no FFmpeg.
Set `EDGE_TTS_FFMPEG` or `--ffmpeg` to an executable path if not on PATH. Test
dependencies include their own FFmpeg only for automated tests; it is not
included in a normal `uvx` installation.

### Python + pip + venv (no uv required)

Use Python 3.12 or newer. On Windows, from this checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m edge_voice_mcp --help
```

On Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m edge_voice_mcp --help
```

Point the MCP client directly at that environment's Python with
`args: ["-m", "edge_voice_mcp"]`, or at its installed `edge-voice-mcp` command.
No environment activation is required. Install dependencies ahead of time for
deployment; do not run pip on each MCP invocation.

### pipx

With pipx installed and a suitable Python available:

```sh
pipx install .
edge-voice-mcp --version
```

The package is not published to PyPI yet: install from the checkout or your
eventual Git URL, not an unrelated package with a similar name. The checked-in
`uv.lock` is used by uv project commands; pip/pipx/uvx do not automatically
apply that lockfile. Pin package versions/commits for repeatable deployment.

## MCP configuration

Local checkout (replace `/absolute/path/to/project` with this directory):

```json
{
  "mcpServers": {
    "edge-voice-mcp": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/project", "--locked", "--no-dev", "edge-voice-mcp"],
      "env": {"EDGE_TTS_OUTPUT_DIR": "/srv/drive/files/admin"}
    }
  }
}
```

After publishing **your** repository, `uvx` can install it directly. Replace
`OWNER` and `COMMIT` (prefer a full commit hash for reproducible deployment):

```json
{
  "mcpServers": {
    "edge-voice-mcp": {
      "command": "uvx",
      "args": ["--python", "3.12", "--from", "git+https://github.com/OWNER/edge-voice-mcp@COMMIT", "edge-voice-mcp"],
      "env": {"EDGE_TTS_OUTPUT_DIR": "/srv/drive/files/admin"}
    }
  }
}
```

For a local `uvx` smoke check:

```sh
uvx --from . edge-voice-mcp --version
```

These are generic MCP examples, **not** Hermes config commands. Test under a
separate server name in your client's supported configuration before replacing
an existing MCP. Do not change Hermes' built-in TTS or other profiles. The old
client-side server name `edge-tts-mcp` may be kept when switching implementations.
Allow a client timeout longer than the tool deadline (e.g. 60 s for the default
45 s), with enough additional time for startup/dependency installation.

## Tools

### `get_capabilities()`

Read local formats, FFmpeg executable availability, limits, configured defaults,
profiles and whether subtitle/timing files are allowed. Makes no network request.
FFmpeg executable presence does not guarantee libopus support. It also does not
prove the Microsoft service is currently reachable.

### `list_voice(locale="", gender="", query="", offset=0, limit=null)`

No arguments returns voices for **all languages**. `locale` is a case-insensitive
prefix, e.g. `ru`, `ru-RU`, `en`; `gender` is `Female`, `Male`, or empty.
Returns a list like:

```json
[{"ShortName": "ru-RU-SvetlanaNeural", "Gender": "Female", "Locale": "ru-RU"}]
```

Use `ShortName` as `tts.voice`. Network retrieval is limited to 30 seconds;
failure is reported as an MCP tool error, not an empty voice list.
Service-supplied `FriendlyName`, `Name`, and `VoiceTag` fields are retained when
present. `query` searches the returned metadata case-insensitively; `offset`
and `limit` page the filtered list. No limit means all matches. Voice tags are
descriptive metadata, not a promise of emotional styles or arbitrary SSML support.

### `tts`

| Argument | Default | Meaning |
| --- | --- | --- |
| `text` | required | 1–100000 characters, not blank |
| `voice` | configured default | Any available voice's `ShortName`; initially `ru-RU-SvetlanaNeural` |
| `rate` | configured default or profile | Signed percentage, e.g. `+15%`; initially `+0%` |
| `volume` | configured default or profile | Signed percentage, e.g. `-10%`; initially `+0%` |
| `pitch` | configured default or profile | Signed Hz, e.g. `+5Hz`; initially `+0Hz` |
| `audio_format` | `mp3` | `mp3` or `ogg` (OGG/Opus, not Vorbis) |
| `timeout` | `45` | Total seconds, greater than 0 and at most 300 |
| `subtitles` | `none` | `none`, `srt`, or `vtt`; only request for caption tasks |
| `word_timings` | `false` | Create per-word JSON only for highlighting/editing tasks |
| `profile` | `default` | `default`, `narration`, `notification`, or `slow` |

Profiles select rate/volume/pitch presets, not emotions. Configured settings are
used for `default`; `narration` uses rate `-10%`, `notification` `+15%`, and `slow`
`-25%`, with neutral volume/pitch. Explicit call parameters override any profile.
Voice is never silently changed by a profile.

Russian MP3:

```json
{"text": "Привет! Проверка синтеза речи.", "voice": "ru-RU-SvetlanaNeural"}
```

English OGG/Opus:

```json
{"text": "Hello!", "voice": "en-US-AriaNeural", "audio_format": "ogg", "rate": "+10%"}
```

Speech plus subtitles and word timing JSON:

```json
{"text": "Привет! Сегодня хорошая погода.", "subtitles": "srt", "word_timings": true}
```

This produces audio and optional `subtitle_path` / `word_timings_path`. JSON is
an array of `{"word": "Привет", "start_ms": 100, "end_ms": 500}` objects with
times relative to the beginning of the audio; `word_count` reports its length.
SRT/WebVTT cues are heuristically grouped from word events (short phrases,
pauses, available punctuation), not one caption per word. The files are separate:
the MCP does not burn subtitles into video. Events may normalize spelling or
omit punctuation; they are not a verbatim transcript or frame-accurate alignment.

If Microsoft returns no usable word events, audio still succeeds and `warnings`
contains `timing_metadata_unavailable`; no fake timings or empty sidecars are
published. If writing/publishing a requested sidecar fails, the job fails and
rolls back any files it published. Individual file publication is atomic; the
multi-file set is not a filesystem transaction. An OS crash can leave a partial set.

For long text, edge-tts handles UTF-8-safe chunking and adjusts event offsets
across chunks. One save call produces one MP3; no manual FFmpeg concatenation
is needed. Longer text may exceed 45 seconds: raise both tool and MCP-client
deadlines or submit smaller jobs. The 100,000-character limit is a bound, not
a guarantee that a request will finish within the maximum 300 seconds.

Success includes `success: true`, `audio_path` (and the equivalent `path`),
`size` in bytes, `duration_seconds`, `format`, `mime_type`, and applied voice/
rate/volume/pitch/profile settings. MCP structured content contains the result.
Operational failures return `success: false`, `error_code`, and a safe message.
Codes: `invalid_input`, `timeout`, `no_audio`, `audio_error`, `io_error`,
`service_error`, `feature_disabled`. Invalid MCP argument types/schema constraints are MCP tool errors.
Cancellation propagates to the client and cleans partial files.

`audio_path` is on the **MCP host**. The server does not upload files or call
Telegram. An OGG/Opus file can be passed to Telegram `sendVoice` by the client
or integration; actual Hermes/Telegram delivery must be tested separately.

## Configuration and safety

| Setting | Default |
| --- | --- |
| `EDGE_TTS_OUTPUT_DIR` / `--output-dir` | `~/.edge-voice-mcp/output` |
| `EDGE_TTS_FFMPEG` / `--ffmpeg` | `ffmpeg` on PATH |
| `EDGE_TTS_VOICE` / `--voice` | `ru-RU-SvetlanaNeural` |
| `EDGE_TTS_RATE` | `+0%` |
| `EDGE_TTS_VOLUME` | `+0%` |
| `EDGE_TTS_PITCH` | `+0Hz` |
| `EDGE_TTS_ALLOW_SIDECARS` | `1`; `0`, `false`, `no`, `off` reject subtitle/timing requests |

`--disable-sidecars` also rejects subtitle/timing requests regardless of model
choices. Requests without those options still work. No dotenv file is loaded.

CLI options override environment settings. The output directory must be
writable by the MCP account. Successful files remain until **you** delete them;
monitor storage and set your own retention policy. Partial files are removed on
ordinary errors/cancellation; an OS crash or forced process kill can leave staging
directories. Cleanup failures are logged without speech text or service details.

Speech text is sent to Microsoft's external online service through `edge-tts`.
Do not submit confidential text without appropriate permission. This is not an
official Microsoft API or an offline engine; availability and behaviour can
change. No SLA is provided. Local validation checks headers/codec/duration, not
full playback or subjective speech quality. Deadlines rely on cooperative async
cancellation; OS-level filesystem stalls cannot be bounded by asyncio.

## Development and tests

```sh
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
```

Run the complete offline suite independently on each interpreter:

```sh
uv run --isolated --python 3.12 --locked pytest -q
uv run --isolated --python 3.14 --locked pytest -q
```

Offline tests cover real MP3 and OGG/Opus parsing/encoding using a generated
tone, async save mocks, invalid inputs, timeout/cancellation cleanup, unique
parallel outputs, sidecar rollback, disabled/missing metadata, safe errors,
subtitle serialization, and a real MCP stdio handshake/tool call. This
does not alone prove online synthesis works.

Live tests are opt-in and send fixed public sample phrases to Microsoft:

```sh
# POSIX
EDGE_TTS_LIVE_TESTS=1 uv run --locked pytest -q -m live
```

```powershell
# PowerShell
$env:EDGE_TTS_LIVE_TESTS = "1"
uv run --locked pytest -q -m live
Remove-Item Env:EDGE_TTS_LIVE_TESTS
```

## Provenance and license

This is a standalone implementation, not a repackaged fork. The old
[eraincc/edge-tts-mcp](https://github.com/eraincc/edge-tts-mcp) was reviewed for
its interface and failure modes; its source is not included. The old
`srt_enable` parameter is not supported: use `subtitles="srt"` instead.
See [the review](docs/UPSTREAM_REVIEW.md).
Other existing implementations are compared in
[the alternatives review](docs/ALTERNATIVES_REVIEW.md).

Project code: [MIT](LICENSE). Dependencies retain their own licenses:
[`edge-tts`](https://github.com/rany2/edge-tts),
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[Mutagen](https://mutagen.readthedocs.io/). Mutagen is GPL-licensed; MIT applies
to this project's code, not a relicensing of dependencies. Before distributing
a bundled application, review the applicable combined-distribution obligations,
including the selected FFmpeg build's license. FFmpeg is an external executable
and is not bundled in this runtime package. This project is not affiliated with Microsoft.
