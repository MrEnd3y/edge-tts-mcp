# Changelog

## 0.2.1 — pre-publication hardening

- Fix missed timeout/cancellation checkpoints before publishing output files.
- Reap FFmpeg despite repeated cancellation, with a separate bounded cleanup wait.
- Reject malformed configuration early; apply CLI overrides before validation.
- Search voice metadata values rather than field names; validate catalog shapes.
- Bound MCP synthesis concurrency (default 2); include queue wait in the deadline.
- Restrict new POSIX output files to `0600` and new output directories to `0700`.
- Add static typing/security checks, branch coverage, strict distribution checks,
  pinned CI actions and packaging metadata validation.
- Add optional multi-stage, non-root Docker/Compose deployment and offline
  container smoke tests. The Dockerfile uses pip/wheels, not uv. Local image
  execution is pending a running Docker engine.

## 0.2.0 — independent implementation

- Async Microsoft Edge TTS MCP with `tts`, `list_voice`, `get_capabilities`.
- MP3/OGG Opus, configurable defaults and optional speech parameter presets.
- Optional SRT/VTT captions and JSON word boundaries; audio-only defaults.
- Library-driven long-text chunking, input/deadline limits, audio validation,
  staged publication and cleanup on errors/cancellation.
- Windows validation on Python 3.12/3.14, including online speech and long text.

No release has been published to PyPI or an image registry yet.
