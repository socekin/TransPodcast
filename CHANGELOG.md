# Changelog

All notable public changes to TransPodcast are documented in this file. The
project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
uses semantic versioning for public releases.

## [Unreleased]

## [0.1.0] - 2026-08-10

### Added

- End-to-end workflow for source discovery, speaker-aware transcription,
  translation, review, Mandarin speech rendering, quality validation, and
  owner-approved publication.
- Conservative removal of clearly separable paid advertisements and assembled
  opening highlights while retaining uncertain editorial content.
- Local FastAPI control plane, background processing service, TransPodcast Host
  web interface, and durable SQLite workflow state.
- Modal speech applications for diarization, ASR, forced alignment, speaker
  embeddings, and Mandarin TTS.
- Private RSS and media publication through Cloudflare R2 and a token-protected
  Cloudflare Worker.
- Optional Telegram notifications for processing and publication decisions.
- Chinese and English documentation for installation, providers, deployment,
  Apple Podcasts, troubleshooting, and agent-assisted setup.
- GitHub Pages publication workflow for repository-hosted audio comparisons.
- Two loudness-normalized source-versus-rendered audio comparisons with
  timestamps, transcripts, translations, and AI disclosure.
- Repository social preview artwork for GitHub and link-sharing platforms.
- PolyForm noncommercial terms and a separate commercial licensing contact.
- Automatic first-run generation and owner-only persistence of private feed
  tokens.
- Modal deployment state tracking for both single-account and multi-profile
  configurations.

### Changed

- Replaced maintainer-specific show and console defaults with neutral values
  for new deployments.
- Expanded the Modal guide with complete single-account and multi-account
  profile authentication, environment creation, deployment, billing, and
  readiness verification steps.
- Added official Codex CLI installation, sign-in, and same-user verification
  instructions.
- Clarified that TransPodcast startup performs provider readiness checks but
  does not automatically start an episode or GPU speech stage in the default
  manual-processing mode.

### Security

- Public configuration uses placeholders only.
- The local management API rejects untrusted Host headers.
- Cloudflare invocation logs and automatic traces are disabled so tokenized
  private feed and media URLs are not persisted by Worker observability.
- Full source media, voice references, production state, and private
  infrastructure identifiers are excluded from the repository. The only real
  media consists of short, documented demonstration excerpts.
- CI includes deterministic tests, documentation validation, secret scanning,
  dependency audits, and CodeQL analysis.

[Unreleased]: https://github.com/socekin/TransPodcast/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/socekin/TransPodcast/releases/tag/v0.1.0
