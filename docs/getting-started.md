# Getting Started

This guide takes a new operator from a clean checkout to a local Owner Console,
then explains what must be configured before processing a real episode.

There are two separate setup levels:

- **Local evaluation:** run the UI and deterministic tests without live provider
  calls.
- **Real operation:** process authorized media through Codex, Modal, publication
  storage, and optional Telegram notifications.

## 1. Install the local prerequisites

TransPodcast currently targets an always-on macOS host.

- Python 3.12, 3.13, or 3.14
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 22 or newer and npm
- [FFmpeg](https://ffmpeg.org/download.html)
- Git

Confirm the tools are available:

```shell
python3 --version
uv --version
node --version
npm --version
ffmpeg -version
```

Linux is supported in CI for deterministic tests, but the first supported
operator deployment target is macOS.

## 2. Clone and verify the project

```shell
git clone https://github.com/socekin/TransPodcast.git
cd TransPodcast
make setup
make check
```

`make check` performs linting, strict type checks, deterministic backend and
frontend tests, a production frontend build, and a local secret scan. It does
not contact external providers.

## 3. Start in local mode

```shell
cp .env.example .env
make dev
```

Open <http://transpodcast.localhost:8000>. The development command starts the
FastAPI control plane and the background worker. The API serves the compiled
Owner Console from the same origin.

The default configuration stores SQLite state and generated local objects
under `data/`, which is ignored by Git. Stop both processes with `Ctrl-C`.

## 4. Understand Setup Readiness

The Owner Console reports each external capability separately. A local UI can
open before the real-processing requirements are ready. Processing remains
paused until the required providers are configured, which prevents accidental
partial or billable runs.

For a real episode, configure:

1. Codex CLI authentication for triage, translation, review, and metadata.
2. Modal and the pinned speech applications for diarization, ASR, alignment,
   speaker measurement, and TTS.
3. Publication storage: local mode for development, or private R2 for a remote
   feed.
4. At least one authorized YouTube source channel or a direct source URL.
5. The optional Telegram bot only if out-of-band owner decisions are wanted.

See [Providers](providers.md), [Configuration](configuration.md), and
[Deployment](deployment/README.md) for the exact provider steps.

## 5. Configure show and publication settings

Edit `.env` locally. Do not commit it.

At minimum, review:

- `TRANSPODCAST_SHOW_TITLE`
- `TRANSPODCAST_SHOW_DESCRIPTION`
- `TRANSPODCAST_SHOW_AUTHOR`
- `TRANSPODCAST_SHOW_ARTWORK_PATH`
- `TRANSPODCAST_STORAGE_MODE`
- `TRANSPODCAST_PUBLICATION_DOMAIN`
- `TRANSPODCAST_FEED_TOKEN_PATH`

For local development, `TRANSPODCAST_STORAGE_MODE=local` is enough. For remote
listening in Apple Podcasts or another podcast client, configure Cloudflare R2
and the publication Worker so media URLs remain available over HTTPS.

## 6. Process an authorized source

Before importing a source, confirm that you are allowed to download, process,
translate, synthesize, and publish it. See [Responsible use](responsible-use.md).

In the Owner Console:

1. Open **Sources** and add a channel or direct video URL.
2. Review the discovered metadata and choose the episode.
3. Start processing and monitor the durable stage attempts.
4. Resolve any quality or attribution review items.
5. Listen to the private preview.
6. Publish only after the final owner decision is enabled.

The worker resumes from the latest valid checkpoint after a restart. It does
not intentionally rerun completed GPU stages.

## 7. Subscribe in Apple Podcasts

After at least one episode is published, open **Settings** in the Owner Console
and copy the private subscription URL. Follow it by URL in Apple Podcasts.

The private feed URL contains a token. Treat it as a secret and rotate it if it
is exposed.

See [Apple Podcasts and Private RSS](publishing/apple-podcasts.md) for the
private subscription path and the separate public Apple Podcasts Connect path.

## 8. Back up before relying on the service

The TransPodcast host is the system of record. Back up the configured SQLite database,
the local artifact root, and the feed token path. R2 objects are not a
substitute for the TransPodcast host database.

Never commit `data/`, `.env`, provider profiles, browser cookies, or generated
media. See [Security](../SECURITY.md) for incident guidance.
