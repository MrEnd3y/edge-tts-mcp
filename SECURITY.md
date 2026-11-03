# Security and deployment boundaries

This is a local **stdio** MCP server for trusted clients. It is not a public,
multi-user TTS API and does not implement authentication or per-user quotas.
Run it as an ordinary account with access only to its output directory.

## Data handling

- Speech text is transmitted to Microsoft's external service through `edge-tts`.
  Do not submit confidential or regulated data without permission to do so.
- Generated audio, captions and timing JSON contain user content. Keep output
  directories private. New POSIX outputs use `0600`; Windows access depends on
  inherited filesystem ACLs. Existing directory permissions are not changed.
- Successful files are retained. Operators must monitor disk use and choose a
  retention policy; no automatic deletion of successful files is implemented.
- Service error messages/URLs/headers and speech text are not echoed by this
  project's own failure handlers. Unexpected exception **class names** may be
  logged. Third-party tools and host/client logging have their own behaviour.
- This server needs no API key, Telegram token, credential file or dotenv file.
  Never commit credentials, logs with authorization data or generated private speech.

## Resource and cancellation limits

- Text is bounded to 100000 characters and the tool deadline to 300 seconds.
- MCP synthesis concurrency defaults to 2 per server instance, configurable
  from 1 to 16. Queue wait uses the same tool deadline. Multiple instances do
  not share a global quota. Direct use of internal Python helpers has no implicit
  server semaphore unless the caller supplies one.
- Cancellation is cooperative. Synchronous filesystem/codec inspection cannot
  interrupt a blocked OS call. The server checks the deadline before publication
  and rolls back ordinary partial publication failures; an OS crash is not a
  filesystem transaction and may leave artifacts.
- FFmpeg is killed on cancellation, then given a bounded reaping wait of up to
  5 additional seconds. If the OS cannot reap it, a warning is emitted rather
  than waiting forever. No hard guarantee is made against OS-level failures.
- Keep Python/FFmpeg/dependencies updated, including the container's OS packages.
  Use current supported Python security patches; an older interpreter listed
  in test history is not a production security recommendation.
  Dropping capabilities/read-only roots are defence in depth, not a sandbox proof.
- A Docker socket grants powerful host access. Do not expose it to untrusted
  clients or mount it inside this image.

## Reporting

Do not post exploit details with tokens, private input text, generated speech
or deployment configuration in a public issue. Before making the repository
public, the maintainer should enable GitHub private vulnerability reporting and
document a private contact channel. No private reporting endpoint is published
yet; do not assume a placeholder repository/account is an authorised contact.

Automated linting, tests, Bandit and a dependency advisory scan reduce risk;
they are not a complete security audit or a guarantee of vulnerability absence.
