# Configuration

TransPodcast reads settings from environment variables and from a repository
root `.env` file. Every variable uses the `TRANSPODCAST_` prefix. Start by
copying `.env.example`; never commit the resulting `.env` file.

## Local paths and show identity

| Variable | Default purpose |
| --- | --- |
| `TRANSPODCAST_DATABASE_PATH` | SQLite workflow database |
| `TRANSPODCAST_ARTIFACT_ROOT` | Durable processing artifacts |
| `TRANSPODCAST_LOCAL_STORAGE_ROOT` | Local publication object store |
| `TRANSPODCAST_SHOW_ARTWORK_PATH` | 3000 x 3000 fallback show artwork |
| `TRANSPODCAST_SHOW_TITLE` | Private feed title |
| `TRANSPODCAST_SHOW_DESCRIPTION` | Private feed description |
| `TRANSPODCAST_SHOW_AUTHOR` | Private feed author |
| `TRANSPODCAST_OWNER_CONSOLE_DIST` | Compiled TransPodcast Host web directory |

Relative paths resolve from the repository root when the commands are started
there. Replace the example show title, description, and author with the private
podcast identity you want listeners to see. Keep runtime state under `data/` so
Git ignores it consistently.

## Publication

For local development:

```dotenv
TRANSPODCAST_STORAGE_MODE=local
TRANSPODCAST_PUBLICATION_DOMAIN=http://transpodcast.localhost:8000
```

For private R2 publication, set `TRANSPODCAST_STORAGE_MODE=r2` and provide the
R2 endpoint, bucket, access key ID, and secret access key. Use a token scoped
only to the project bucket. The public Cloudflare Worker has a separate R2
binding and must not receive the TransPodcast host's R2 API credentials.

`TRANSPODCAST_FEED_TOKEN` may inject a token directly. The safer persistent
default is `TRANSPODCAST_FEED_TOKEN_PATH`. When the configured file does not
exist, TransPodcast generates a cryptographically random token on first start
and writes it to the ignored runtime directory with owner-only permissions.
Treat the complete private feed URL as a secret because it contains that token.

## Agent providers

TransPodcast runs triage, translation, review, Speaker ownership review, and
publication metadata through a local Agent CLI. Codex CLI is the default
provider. Claude Code is optional: install it, then select **Claude** as the
processing Agent in **Settings**. The selection applies to Processing Runs
created or explicitly reprocessed afterwards; each run keeps its provider for
retries and revisions. Setup Readiness reports both CLIs, and only the selected
provider blocks processing.

| Variable | Purpose |
| --- | --- |
| `TRANSPODCAST_CODEX_EXECUTABLE` | Codex CLI executable (default `codex`) |
| `TRANSPODCAST_CODEX_MODEL` | Optional Codex model override |
| `TRANSPODCAST_CLAUDE_EXECUTABLE` | Claude Code CLI executable (default `claude`) |
| `TRANSPODCAST_CLAUDE_MODEL` | Optional Claude Code model or alias |
| `TRANSPODCAST_CLAUDE_EFFORT` | Optional effort: `low`, `medium`, `high`, `xhigh`, or `max` |
| `TRANSPODCAST_CLAUDE_OAUTH_TOKEN_PATH` | Claude Code token file for the services (default `data/secrets/claude-oauth-token`) |

Install and sign in to each CLI as the same macOS user that runs TransPodcast.
Verify Codex with `codex --version` and `codex login status`, and Claude Code
with `claude --version` and `claude auth status`. Authentication remains in
each CLI's own local store and must never be copied into this repository or an
artifact. See the official [Codex CLI guide](https://developers.openai.com/codex/cli)
and [Claude Code setup guide](https://code.claude.com/docs/en/setup).

TransPodcast starts Claude Code in an isolated mode that ignores user and
project settings, CLAUDE.md files, MCP servers, skills, hooks, and plugins.
It does not pass `ANTHROPIC_API_KEY` to the CLI, so sign in with a Claude
subscription account. Use a Claude Code version that supports the isolation
flags; readiness reports Claude Code as unavailable otherwise.

### Claude Code token file

Give the API and worker their own one-year token instead of relying on the
interactive `/login` credential. That credential lives in the macOS Keychain
and is shared and refreshed by every `claude` session of the same user; when
many interactive sessions refresh it at once it can stop working, Setup
Readiness then fails, and background processing pauses. A `setup-token` token
never refreshes, so nothing done in an interactive session can invalidate it.

```shell
claude setup-token
```

The command asks for browser approval and prints the token once. Copy it, then
write it to the configured path without echoing it:

```shell
(umask 077; pbpaste > data/secrets/claude-oauth-token)
(export CLAUDE_CODE_OAUTH_TOKEN="$(cat data/secrets/claude-oauth-token)"; claude auth status)
```

The status must report `"authMethod": "oauth_token"`. Every Claude call reads
the file again, so a new or renewed token takes effect within a minute without
a restart, and **Settings** shows Claude Code as signed in with the long-lived
token. The token expires one year after it is created: note the date and
renew it before then. Never export `CLAUDE_CODE_OAUTH_TOKEN` from a shell
profile, because interactive Claude Code sessions would switch to it. The file
is ignored by Git and excluded from State Backups.

## Modal speech pipeline

For one Modal account, use
`TRANSPODCAST_MODAL_COMMAND=uv run transpodcast-modal-pipeline`. For optional
multi-account credit routing, use
`TRANSPODCAST_MODAL_COMMAND=uv run transpodcast-modal-router` instead.
`TRANSPODCAST_MODAL_ENVIRONMENT` identifies the environment used by both the
deployment script and the runtime client.

The account-routing variables are optional. They identify local Modal profile
names, not credentials. Profiles still live in the local Modal configuration.
`TRANSPODCAST_MODAL_PROFILES` lists profiles in routing priority order.
`TRANSPODCAST_MODAL_CREDIT_THRESHOLD_USD` and
`TRANSPODCAST_MODAL_MONTHLY_ALLOWANCE_USD` control per-profile routing
guardrails; they do not replace provider-side budgets. Follow the complete
[single-account or multi-account deployment guide](deployment/modal.md) before
starting the worker.

## YouTube

`TRANSPODCAST_YOUTUBE_PROXY_URL` is optional. Do not put proxy credentials in a
committed example, issue, log, or screenshot. TransPodcast intentionally does
not support importing browser cookies or authenticated/private videos.

`TRANSPODCAST_YOUTUBE_BROWSER_EXECUTABLE` points to a local Chromium-based
browser executable. Source media downloads use it to generate YouTube
proof-of-origin tokens without cookies or a signed-in profile. The default is
the Google Chrome executable on macOS; quote the value in `.env` because the
path contains spaces. Setup Readiness reports the source adapter as unavailable
when the executable is missing.

## Telegram

Both `TRANSPODCAST_TELEGRAM_BOT_TOKEN` and
`TRANSPODCAST_TELEGRAM_OWNER_CHAT_ID` are required to enable Telegram.
Telegram is optional: TransPodcast Host remains the complete surface for
publication and Review Queue decisions.

## Precedence and verification

Environment variables override `.env` values. After changing configuration,
restart the API and worker, then inspect **Setup Readiness** in TransPodcast
Host. Do not paste the raw environment into support requests; report only
the readiness item names and sanitized errors.
