# Getting Started

This guide takes a new operator from a clean checkout to TransPodcast Host,
then explains what must be configured before processing a real episode.

There are two separate setup levels:

- **Local evaluation:** run the UI and deterministic tests without live provider
  calls.
- **Real operation:** process media for learning or research through Codex or
  Claude Code, Modal, publication storage, and optional Telegram notifications.

## 1. Install the local prerequisites

TransPodcast currently targets an always-on macOS host.

- Xcode Command Line Tools, which provide Git and `make`
- Python 3.12, 3.13, or 3.14
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 22 or newer and npm
- [FFmpeg](https://ffmpeg.org/download.html)
- Google Chrome or another Chromium-based browser, used locally to generate
  YouTube proof-of-origin tokens for source downloads

Install the Xcode Command Line Tools first if they are missing:

```shell
xcode-select --install
```

Confirm the tools are available:

```shell
xcode-select -p
git --version
make --version
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

## 3. Install and authenticate an Agent CLI

Codex CLI is the default processing Agent. Claude Code is an optional
alternative; install at least one and select it in **Settings**.

### Codex CLI

Install Codex CLI with the official macOS/Linux installer:

```shell
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex --version
```

From the repository directory, run `codex`. On first launch, choose **Sign in
with ChatGPT** or another available sign-in method, then verify the saved
session:

```shell
cd TransPodcast
codex
codex login status
```

Use the same macOS user account that will run `make dev` or the installed
TransPodcast services. Codex authentication is stored for that local user; do
not copy its authentication files into `.env`, this repository, or a deployment
artifact. See the official [Codex CLI guide](https://developers.openai.com/codex/cli)
for current installation and sign-in options.

### Claude Code (optional)

Install Claude Code with the official installer, sign in by running `claude`
once, then verify the saved session:

```shell
curl -fsSL https://claude.ai/install.sh | bash
claude --version
claude
claude auth status
```

TransPodcast runs Claude Code isolated from personal settings and does not pass
`ANTHROPIC_API_KEY`, so sign in with a Claude subscription account. After
TransPodcast Host starts, open **Settings** and select **Claude** as the
processing Agent. See the official
[Claude Code setup guide](https://code.claude.com/docs/en/setup) and
[Configuration](configuration.md#agent-providers).

## 4. Start TransPodcast Host

```shell
cp .env.example .env
make dev
```

Open <http://transpodcast.localhost:8000>. The development command starts the
FastAPI control plane and the background worker. The API serves the compiled
TransPodcast Host web interface from the same origin.

The background worker performs Setup Readiness checks after startup. These
checks may contact configured provider APIs, including Modal, but do not upload
episode media or execute a GPU speech stage. With the default manual-processing
settings, no episode is imported or processed until the operator starts it.

The default configuration stores SQLite state and generated local objects
under `data/`, which is ignored by Git. Stop both processes with `Ctrl-C`.
On first start, TransPodcast creates a private feed token at
`data/secrets/feed-token` with owner-only file permissions.

## 5. Understand Setup Readiness

TransPodcast Host reports each external capability separately. The web
interface can open before the real-processing requirements are ready.
Processing remains paused until the required providers are configured, which
prevents accidental partial or billable runs.

For a real episode, configure:

1. The selected Agent CLI (Codex or Claude Code) for triage, translation,
   review, Speaker ownership review, and metadata.
2. Modal and the pinned speech applications for diarization, ASR, alignment,
   speaker measurement, TTS, and the Listening Audit.
3. Publication storage: local mode for development, or private R2 for a remote
   feed.
4. At least one YouTube source channel or direct source URL that may be used
   for learning or research.
5. The optional Telegram bot only if out-of-band owner decisions are wanted.

See [Providers](providers.md), [Configuration](configuration.md), and
[Deployment](deployment/README.md) for the exact provider steps.

## 6. Configure show and publication settings

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

## 7. Process a source

TransPodcast is intended only for learning, research, and technical
experimentation. Do not import or process content you are not authorized to use.

In TransPodcast Host:

1. Open **Sources** and add a channel or direct video URL.
2. Review the discovered metadata and choose the episode.
3. Start processing and monitor the durable stage attempts.
4. Resolve any quality, attribution, or Listening Audit review items.
5. Listen to the private preview.
6. Publish only after the final owner decision is enabled.

The worker resumes from the latest valid checkpoint after a restart. It does
not intentionally rerun completed GPU stages.

## 8. Subscribe in Apple Podcasts

After at least one episode is published, open **Settings** in TransPodcast Host
and copy the private subscription URL. Follow it by URL in Apple Podcasts.

The private feed URL contains a token. Treat it as a secret and rotate it if it
is exposed.

See [Apple Podcasts and Private RSS](publishing/apple-podcasts.md) for the
private subscription path and the separate public Apple Podcasts Connect path.

## 9. Back up before relying on the service

The TransPodcast host is the system of record. Back up the configured SQLite database,
the local artifact root, and the feed token path. R2 objects are not a
substitute for the TransPodcast host database.

Never commit `data/`, `.env`, provider profiles, browser cookies, or generated
media.
