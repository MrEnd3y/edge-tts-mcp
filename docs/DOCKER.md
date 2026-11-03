# Docker: optional stdio deployment

Docker is an alternative to Python/venv, pipx and uvx, not a requirement.
The image contains Python, the installed package and FFmpeg/libopus. It does
**not** start HTTP/Uvicorn, expose a port, contain a database client, or include
uv/test tools in the runtime stage. **uv is not used in the Dockerfile at all.**
The builder uses `pip wheel` with the pinned, hashed `requirements-runtime.txt`,
then installs the project and dependencies offline into a venv from those wheels.
Only the installed venv is copied to runtime; the wheel cache is not copied.

## Build and smoke-check

Requires a running **Linux** Docker engine (Docker Desktop on Windows/macOS is
fine when its Linux engine is running):

```sh
docker build --tag edge-voice-mcp:local .
docker run --rm edge-voice-mcp:local --version
```

The two-stage build uses the same Python base for builder/runtime, installs
the project non-editably in `/opt/venv`, and runs as `app` (UID/GID 10001 by
default). Only FFmpeg and certificates are added through apt; no compiler,
PostgreSQL library or web server is required.
Runtime dependencies must have binary wheels for the selected Linux/Python
architecture. The builder deliberately fails instead of downloading arbitrary
source builds and adding a compiler implicitly.

`requirements-runtime.txt` is a committed runtime-only export of `uv.lock`;
CI regenerates it and fails if it has drifted. Developers updating dependencies
can regenerate it with:

```sh
uv export --locked --no-dev --no-emit-project --no-header --format requirements-txt --output-file requirements-runtime.txt --quiet
```

uv is used here only as a development exporter; Docker users do not need it.

The source build context is **allowlisted** in `.dockerignore`: Git data,
local environments, output files, `.env` files and downloaded comparisons are
not copied. Do not add credentials through build arguments or Docker layers.

Base tags receive upstream patch updates and are not immutable. For a release,
record/pin verified image digests through your release process and rebuild
regularly for OS/FFmpeg security updates. No image is automatically published.
Before redistributing an image, review Mutagen/FFmpeg license obligations;
the MIT license for our own source is not a relicensing of included dependencies.
See the [publication checklist](PUBLICATION_REVIEW.md).

## Basic stdio run

```sh
docker run --rm --init -i --mount type=volume,source=edge-voice-output,target=/data/output edge-voice-mcp:local
```

Use **`-i` without `-t`**. A TTY may corrupt the MCP protocol. The process waits
for MCP messages, not text typed interactively. The client starts the container
and communicates through stdin/stdout; diagnostics go to stderr.
The image uses `SIGINT` as its stop signal so asyncio/AnyIO can request task
cancellation. Docker can still force-kill a process after its stop deadline;
that case may leave partial files in a persistent volume.

The named volume retains successful files after the container exits. Returned
paths such as `/data/output/edge-tts-….mp3` are **inside the container**. A named
volume alone does not make those paths readable by a host-based Hermes process.

## Hermes on a Linux host: share the same absolute path

For Hermes to send a generated file, mount a directory with **the same absolute
path** inside and outside the container. Example: `/srv/drive/files/admin`.
Set `EDGE_TTS_OUTPUT_DIR` to that same path.

The service account running Hermes must be able to read the output. New files
use POSIX mode `0600`, so match the container UID/GID to that account (do not
make files world-readable). For example, if the account uses UID/GID 1000:

```sh
docker build --build-arg APP_UID=1000 --build-arg APP_GID=1000 --tag edge-voice-mcp:local .
```

Use your actual non-root service account IDs, not necessarily 1000. The bind
directory must already exist and be writable by that account; existing host
permissions are not changed by the MCP. Do not switch to root to bypass a
permission error.

Generic MCP client configuration (adapt the outer format to Hermes):

```json
{
  "mcpServers": {
    "edge-voice-mcp": {
      "command": "docker",
      "args": [
        "run", "--rm", "--init", "-i",
        "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--mount", "type=bind,source=/srv/drive/files/admin,target=/srv/drive/files/admin",
        "--env", "EDGE_TTS_OUTPUT_DIR=/srv/drive/files/admin",
        "edge-voice-mcp:local"
      ]
    }
  }
}
```

This enables a read-only root filesystem, drops Linux capabilities, prevents
privilege escalation and provides writable temporary storage. Outbound network
access to Microsoft is still required. No incoming port needs publishing.
The Docker daemon is a privileged host service: only trusted clients should
have access to its socket. Do **not** mount that socket into the container.

On Windows/macOS Docker Desktop, the returned Linux path is not automatically
a host filesystem path. Client-side mapping is required. Until that is set up,
use a host-native venv/pipx/uvx installation when Hermes needs direct file access.

## Compose

An example `compose.yaml` includes stdio, a non-TTY session, an init process,
read-only root, dropped capabilities, temporary storage and a persistent output
volume. Build it ahead of time:

```sh
docker compose -f compose.yaml build
docker compose -f compose.yaml run --rm --no-deps -T edge-voice-mcp --version
docker compose -f compose.yaml run --rm --no-deps -T edge-voice-mcp
```

For a client, the command is `docker` and args start with
`compose -f /absolute/path/to/compose.yaml run --rm --no-deps -T edge-voice-mcp`.
Do not use `compose up -d` as though this were an HTTP service. The default
Compose volume has the same host-path limitation described above; adapt mounts
and `EDGE_TTS_OUTPUT_DIR` before using it for host-based delivery.

Keep container reuse and state separate from the future background-job design:
the current server has no persistent/resumable jobs. `--rm` removes the container,
not the named volume or bind-mounted successful files.

## Container tests

After building the default image, run the opt-in tests. They check non-root
execution, actual MP3→Opus conversion using the image's FFmpeg, and an MCP
stdio handshake. They do not contact Microsoft or Telegram.

```sh
EDGE_TTS_DOCKER_TESTS=1 uv run --locked pytest -q -m docker
```

```powershell
$env:EDGE_TTS_DOCKER_TESTS = "1"
try { uv run --locked pytest -q -m docker } finally { Remove-Item Env:EDGE_TTS_DOCKER_TESTS }
```

`EDGE_TTS_DOCKER_IMAGE` can select a prebuilt image instead of
`edge-voice-mcp:local`. CI includes a Linux image build and these offline smoke
tests, but the workflow must actually run before claiming a validated image.

During local preparation the Docker CLI was available but its Linux engine
was not running. `docker compose config --quiet` passed; the image build and
container tests have **not** been executed locally.
