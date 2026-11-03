# Pre-publication review

Reviewed on 2026-11-04, starting at commit `79adfd6`. This is an engineering
review of this project's owned source, tests, configuration, CI and packaging;
it is not an independent penetration test, legal opinion or audit of Microsoft's
service. Third-party comparison clones were not included in this revision or
shipped package.

## Confirmed defects and fixes

Five regression cases were written **before** modifying the implementation;
all five failed against the starting code and passed after the fixes:

| Finding | Fix |
| --- | --- |
| A synchronous validation/sidecar stage could run past the asyncio deadline and still publish success. | Cancellation checkpoints and explicit monotonic deadline checks before/through publication; rollback on failure. Blocked OS calls still cannot be interrupted. |
| Cancellation requested at the end of save could leave a published file. | Yield to pending cancellation before postprocessing/publication; preserve `CancelledError` and clean staging. |
| Repeated `Task.cancel()` could interrupt FFmpeg reaping. | Separate shielded reaper handles AnyIO cancel scopes and repeated asyncio cancellation; bounded additional wait. |
| Misspelled `EDGE_TTS_ALLOW_SIDECARS=flase` silently enabled the feature. | Explicit boolean parsing; bad configuration fails instead of enabling output. |
| Voice queries matched field names such as `ShortName`. | Search only metadata values; validate catalog structure and optional metadata types. |

Additional hardening:

- Validate default voice/rate/pitch/volume at startup without network calls.
- Apply CLI overrides before validating environment defaults; return a clean
  argparse configuration error without echoing invalid values or a traceback.
- Use the SDK's `ToolAnnotations` model; fix all mypy findings in owned source.
- Bound MCP synthesis concurrency to 2 by default (configurable 1–16), with queue
  wait included in the same deadline and no output directory created for a queued failure.
- Use `0700` for new POSIX output directories and `0600` for final files. Windows
  permission enforcement depends on inherited ACLs, not POSIX mode bits.
- Limit the broad operational error handler; constructor input errors are
  distinguished from unexpected failures. Log exception class names, not raw
  network errors/headers/text. Socket timeouts no longer claim the whole tool
  budget was necessarily consumed.
- Add regression cases for output rollback, malformed metadata/catalogs, CLI
  validation, bounded cleanup, queue timeout and real MCP structured responses.

## Tooling and distribution checks

- Baseline: 59 offline tests passed; coverage was about 92% before the review.
- Updated checks include branch coverage, a 90% minimum, mypy, Ruff and Bandit.
  Current measured counts/results are recorded in [VALIDATION.md](VALIDATION.md).
- `pip-audit` found **no known advisories** in the pinned runtime requirements
  exported from `uv.lock` during this review. This does not cover future releases,
  the external FFmpeg binary, container OS packages, or undisclosed vulnerabilities.
- GitHub Actions references are pinned to verified commit IDs, with read-only
  repository permissions and no persisted checkout credentials. uv is version-pinned.
- CI checks wheel/sdist metadata with strict Twine validation; package version,
  Python requirement, license expression and console entry point have a regression test.
  The first strict check exposed Twine 6.2's rejection of metadata version 2.5
  emitted by the build backend. Developer tooling was upgraded to Twine 7 rather
  than skipping metadata validation or weakening the license metadata.
- No publishing credentials, upload command or automatic release workflow were added.

## Docker review and limitations

Added multi-stage Docker/Compose examples with a non-root runtime, FFmpeg,
allowlisted build context and optional hardened stdio invocation. No database,
HTTP server or exposed port is introduced. The Dockerfile uses pip/wheels only,
with a hash-verified runtime requirements snapshot whose consistency with
`uv.lock` is checked in CI. uv is a development exporter, not a Docker dependency;
test dependencies are not installed in the image.

`docker compose config --quiet` passed locally. The installed Docker CLI could
not connect to its Linux engine; **image build and container execution remain
unverified locally**. Two opt-in offline smoke tests and a Linux CI image job
are prepared. They must pass before advertising a tested image. See
[DOCKER.md](DOCKER.md), including host/container path and UID/GID alignment.

## Release checklist: decisions/checks still required

- [ ] Choose the final package/repository name and verify availability. No claim
  is made that `edge-voice-mcp` is available on PyPI or your GitHub account.
- [ ] Confirm the maintainer identity, attribution and project URLs; replace
  `OWNER`/`COMMIT` installation examples once an actual repository exists.
  Check that documentation links also resolve in the rendered PyPI README.
- [ ] Review dependency licenses and the intended distribution model. This
  project's own code is MIT; **Mutagen is GPL-licensed**. Its obligations are
  not removed by our MIT metadata, particularly for bundled applications/images.
  FFmpeg's obligations also depend on the packaged build. Decide the compatible
  licensing/distribution approach before publishing a bundled image; no licenses
  have been silently replaced or relicensed.
- [ ] Start Docker's Linux engine, build the image and run the container tests.
  Reassess OS/FFmpeg advisories and record/pin release image digests.
- [ ] Run the supplied Linux/Windows GitHub CI. A workflow file is not a green run.
- [ ] Test the actual Hermes → generated OGG → Telegram delivery chain on the
  deployment host; neither Hermes configuration nor Telegram was modified here.
- [ ] Enable a private vulnerability reporting channel and review
  [SECURITY.md](../SECURITY.md).
- [ ] Choose output retention/disk monitoring and deployment limits. No automatic
  deletion policy, cross-instance quota, resumable book jobs or public multi-user
  authentication is implemented.
- [ ] Validate final wheel/sdist from a clean checkout, rerun appropriate online
  smoke tests, then create a release/tag and publish only after explicit approval.
  Publish explicitly selected versioned artifacts, not every old file in `dist/`.

The code is substantially better tested after this review, but those release
decisions and unexecuted integration checks should not be represented as done.
