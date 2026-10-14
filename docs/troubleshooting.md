# Troubleshooting

## TransPodcast Host does not open

Run `make build`, start `uv run transpodcast-api`, and open
<http://transpodcast.localhost:8000>. If port 8000 is already used, stop the
other process; the packaged server currently binds to that local port.

## The console opens but processing is paused

This is expected until Setup Readiness passes. Open the readiness view and fix
only the failed capability. A missing Telegram configuration is optional and
does not block processing.

## Codex is reported unavailable

Run `codex --version` and `codex login status` as the same macOS user that runs
TransPodcast. If Codex is missing or signed out, follow the official
[Codex CLI installation and sign-in guide](https://developers.openai.com/codex/cli).
If a custom executable is configured, confirm it is an executable path. Do not
put authentication tokens in `.env` unless the provider explicitly requires
that mechanism.

## Claude Code is reported unavailable

Claude Code is required only when it is the selected processing Agent. Run
`claude --version` and `claude auth status` as the same macOS user that runs
TransPodcast. TransPodcast ignores `ANTHROPIC_API_KEY`, so sign in with a
Claude subscription account. If the CLI is signed in but still unavailable,
update it: TransPodcast requires the CLI flags that isolate it from personal
settings, MCP servers, skills, and hooks.

## A run reports a different Agent provider

Each Processing Run keeps the Agent provider it started with. A Translation
Project created by one provider cannot resume under another. Changing the
setting affects new runs; reprocess the complete episode to move an existing
run to the newly selected provider.

## A run waits after the Listening Audit

Severe Listening Audit findings are re-rendered once automatically. If a severe
finding remains, the run enters the Review Queue. Listen to the listed units in
Processing Detail, then confirm to continue or revise the affected segments. An
unavailable audit is shown but never blocks publication.

## Modal commands fail

Verify the selected Modal profile and environment, deploy the pinned apps, and
confirm that a single-account setup uses
`TRANSPODCAST_MODAL_COMMAND=uv run transpodcast-modal-pipeline`. Multi-account
setups use `uv run transpodcast-modal-router`. Try the provider's own read-only
status command. Authentication, budget, and capacity failures are intentionally
not retried as generic transient work.

## YouTube discovery or archive fails

Confirm the source is public, accessible without cookies, and authorized for
your use. Source downloads need a local Chromium-based browser: install Google
Chrome or set `TRANSPODCAST_YOUTUBE_BROWSER_EXECUTABLE` to another browser
executable. Update the locked `yt-dlp` dependency if the source platform changed,
then rerun the focused adapter tests. Do not work around access controls by
committing cookies or account exports.

## The private feed returns 404

TransPodcast intentionally returns 404 when the feed token is missing,
incorrect, or revoked, and when a requested immutable object is absent. Check
the domain, Worker route, R2 binding, and token without printing the token or
full feed URL to shared logs.

## A run failed after a provider call

Inspect the sanitized Processing Stage Attempt in TransPodcast Host. Fix the
specific provider or validation error and retry from the latest checkpoint.
Do not delete the SQLite database or artifact directory; doing so discards the
recovery evidence.

## A dependency or test command fails

Use the locked dependency versions (`uv sync --all-groups` and `npm ci`). Run
the smallest failing command directly for readable output. When requesting
help, include OS and tool versions plus a minimal sanitized log, never `.env`,
provider payloads, local database contents, or private media.
