# Agent Guide

This file contains repository-wide instructions for coding agents. It is
tool-agnostic and applies to agents from any vendor.

## Mission

Maintain TransPodcast as a safe, reproducible, local-first podcast translation
system. Prefer explicit state, deterministic validation, and recoverable
workflows over implicit behavior.

## Public repository boundary

- Never add credentials, tokens, private URLs, account IDs, chat IDs, database
  files, logs, local paths, or provider authentication state.
- Never add source audio, transcripts, voice references, embeddings, generated
  speech, thumbnails, or metadata from real people unless the maintainer has
  documented redistribution and voice-use rights in the repository.
- Use synthetic fixtures for tests and examples. Synthetic people, shows, IDs,
  domains, and media must be obviously fictional.
- Do not copy deployment or data directories into this repository. An approved
  audio comparison must be prepared in a temporary workspace, reduced to the
  short final excerpt, and added only under
  `docs/assets/audio-comparisons/<example-id>/` after its permission basis is
  recorded in `docs/audio-comparisons.md`.
- Treat `.env` as secret. Only `.env.example` with empty or clearly fake values
  may be committed.

## Language

- Source code, comments, engineering documentation, commits, issues, and pull
  requests are written in English.
- `README.md` is the default Simplified Chinese product README.
- `README.en.md` is the matching English product README. Keep its structure and
  technical claims synchronized with `README.md`.
- Chinese is otherwise limited to localization resources and localized
  fixtures.

## Architecture boundaries

- `src/transpodcast/` owns domain behavior, orchestration, persistence, and
  provider interfaces.
- `src/transpodcast/adapters/` owns external-provider implementations.
- `apps/owner-console/` owns the local React user interface.
- `workers/publication/` owns the token-protected Cloudflare publication edge.
- `experiments/pipeline/` owns pinned Modal model applications.
- `tests/` must run without live provider calls or billable work.

Do not bypass domain services with direct database writes. Keep provider output
at adapter boundaries and validate it before changing workflow state.

## Required workflow

1. Read the relevant code, tests, and documentation before editing.
2. Add or update a focused test for behavioral changes.
3. Run the smallest relevant test during development.
4. Run `make check` before finishing.
5. Run `make security-check` when dependencies, configuration, publication, or
   credential handling changes.
6. Update documentation and `CHANGELOG.md` for user-visible changes.

Do not run live YouTube, Codex, Modal, Cloudflare, R2, Telegram, or TTS actions
unless the maintainer explicitly requests them and confirms expected cost and
data scope.

## Generated code and migrations

- Regenerate the Owner Console API client with `make generate-api` after API
  contract changes.
- Add a new migration for schema changes. Never rewrite a released migration.
- Keep model IDs, revisions, image dependencies, and pipeline policy versions
  explicit and reviewable.

## Git safety

- Preserve unrelated changes.
- Do not rewrite history, force-push, deploy, or publish a release unless the
  maintainer explicitly asks.
- Use Conventional Commits for repository commits.
