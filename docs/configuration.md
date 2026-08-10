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
| `TRANSPODCAST_OWNER_CONSOLE_DIST` | Compiled Owner Console directory |

Relative paths resolve from the repository root when the commands are started
there. Keep runtime state under `data/` so Git ignores it consistently.

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
default is `TRANSPODCAST_FEED_TOKEN_PATH`, which keeps the generated token in
an ignored runtime directory. Treat the complete private feed URL as a secret
because it contains that token.

## Codex

`TRANSPODCAST_CODEX_EXECUTABLE` selects the authenticated Codex CLI executable.
`TRANSPODCAST_CODEX_MODEL` is optional and allows an explicit model override.
Authentication remains in the CLI's own local store and must never be copied
into this repository or an artifact.

## Modal speech pipeline

`TRANSPODCAST_MODAL_COMMAND` must invoke either the speech pipeline directly or
the optional account router. `TRANSPODCAST_MODAL_ENVIRONMENT` isolates deployed
apps and spending from unrelated Modal projects.

The account-routing variables are optional. They identify local Modal profile
names, not credentials. Profiles still live in the local Modal configuration.
The cost variables are estimates and do not replace a provider-side budget.

## YouTube

`TRANSPODCAST_YOUTUBE_PROXY_URL` is optional. Do not put proxy credentials in a
committed example, issue, log, or screenshot. TransPodcast intentionally does
not support importing browser cookies or authenticated/private videos.

## Telegram

Both `TRANSPODCAST_TELEGRAM_BOT_TOKEN` and
`TRANSPODCAST_TELEGRAM_OWNER_CHAT_ID` are required to enable Telegram.
Telegram is optional: the Owner Console remains the complete decision surface.

## Precedence and verification

Environment variables override `.env` values. After changing configuration,
restart the API and worker, then inspect **Setup Readiness** in the Owner
Console. Do not paste the raw environment into support requests; report only
the readiness item names and sanitized errors.
