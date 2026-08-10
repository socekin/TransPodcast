# Agent Deployment Brief

Give this file to a coding agent when you want help deploying or operating
TransPodcast. The agent should also read `AGENTS.md`, `README.md` or
`README.en.md`, `docs/getting-started.md`, `docs/configuration.md`, and
`docs/deployment/README.md` before making changes.

## Agent operating rules

- Do not call live providers, deploy infrastructure, publish media, rotate
  tokens, or change secrets without explicit owner approval.
- Do not copy `.env`, provider profiles, browser cookies, account IDs, private
  feed URLs, transcripts, generated audio, source audio, voice references,
  production databases, or logs into the repository.
- Keep runtime state under ignored directories such as `data/` or `var/`.
- Use synthetic fixtures for tests and screenshots.
- Run focused checks after edits and `make check` before handoff.

## Deployment map

| Goal | Human-facing guide | Agent-facing files |
| --- | --- | --- |
| Run local UI | `README.md`, `docs/getting-started.md` | `Makefile`, `scripts/dev.sh`, `src/transpodcast/config.py` |
| Configure providers | `docs/providers.md`, `docs/configuration.md` | `src/transpodcast/production.py`, `.env.example` |
| Install macOS host | `docs/deployment/macos.md` | `deploy/install-launchd.sh`, `deploy/*.plist` |
| Deploy Modal speech apps | `docs/deployment/modal.md` | `src/transpodcast/modal_pipeline.py`, `src/transpodcast/modal_router.py` |
| Deploy private publication | `docs/deployment/cloudflare.md` | `workers/publication/`, `workers/publication/wrangler.jsonc` |
| Subscribe in Apple Podcasts | `docs/publishing/apple-podcasts.md` | `src/transpodcast/feed.py`, Owner Console settings screen |

## Recommended prompt

```text
You are helping deploy TransPodcast. Read AGENTS.md, README.md or README.en.md,
docs/getting-started.md, docs/configuration.md, docs/deployment/README.md, and
docs/agent-deployment.md first.

Do not call live providers, deploy, publish, rotate secrets, or add real media
without explicit approval. First report the current readiness state, missing
prerequisites, exact files you intend to edit, and the verification commands you
will run.
```

## Readiness checklist

- Python, uv, Node.js, npm, FFmpeg, and Git are installed.
- `make setup` and `make check` pass without live providers.
- `.env` exists locally and is not tracked by Git.
- Codex CLI is authenticated for translation and review.
- Modal apps are deployed under the intended environment.
- Publication storage is either local for development or private R2 for remote
  listening.
- The Cloudflare Worker rejects unauthenticated feed and media requests.
- The private feed URL is treated as a secret.
- Source media and voice-use rights are documented before processing.
