# Troubleshooting

## The Owner Console does not open

Run `make build`, start `uv run transpodcast-api`, and open
<http://transpodcast.localhost:8000>. If port 8000 is already used, stop the
other process; the packaged server currently binds to that local port.

## The console opens but processing is paused

This is expected until Setup Readiness passes. Open the readiness view and fix
only the failed capability. A missing Telegram configuration is optional and
does not block processing.

## Codex is reported unavailable

Run `codex --version` in the same shell and verify the CLI is authenticated.
If a custom executable is configured, confirm it is an executable path. Do not
put authentication tokens in `.env` unless the provider explicitly requires
that mechanism.

## Modal commands fail

Verify the selected Modal profile and environment, deploy the pinned apps, and
try the provider's own read-only status command. Authentication, budget, and
capacity failures are intentionally not retried as generic transient work.

## YouTube discovery or archive fails

Confirm the source is public, accessible without cookies, and authorized for
your use. Update the locked `yt-dlp` dependency if the source platform changed,
then rerun the focused adapter tests. Do not work around access controls by
committing cookies or account exports.

## The private feed returns 401 or 404

A 401 normally means the feed token is missing, incorrect, or revoked. A 404
normally means the requested immutable object is not present. Check the domain,
Worker route, R2 binding, and token without printing the token or full feed URL
to shared logs.

## A run failed after a provider call

Inspect the sanitized Processing Stage Attempt in the Owner Console. Fix the
specific provider or validation error and retry from the latest checkpoint.
Do not delete the SQLite database or artifact directory; doing so discards the
recovery evidence.

## A dependency or test command fails

Use the locked dependency versions (`uv sync --all-groups` and `npm ci`). Run
the smallest failing command directly for readable output. If reporting an
issue, include OS and tool versions plus a minimal sanitized log, never `.env`,
provider payloads, local database contents, or private media.
