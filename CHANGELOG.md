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
- Local FastAPI control plane, background processing service, React Owner
  Console, and durable SQLite workflow state.
- Modal speech applications for diarization, ASR, forced alignment, speaker
  embeddings, and Mandarin TTS.
- Private RSS and media publication through Cloudflare R2 and a token-protected
  Cloudflare Worker.
- Optional Telegram notifications for processing and publication decisions.
- Chinese and English documentation for installation, providers, deployment,
  Apple Podcasts, responsible use, troubleshooting, and agent-assisted setup.
- GitHub Pages publication workflow for repository-hosted audio comparisons.
- Two rights-cleared, loudness-normalized source-versus-rendered audio
  comparisons with timestamps, transcripts, translations, and AI disclosure.
- Contributor guidelines, security policy, governance, code of conduct,
  commercial licensing terms, and contributor license agreement.

### Security

- Public configuration uses placeholders only.
- Full source media, voice references, production state, and private
  infrastructure identifiers are excluded from the repository. The only real
  media consists of documented, rights-cleared demonstration excerpts.
- CI includes deterministic tests, documentation validation, secret scanning,
  dependency audits, and CodeQL analysis.

[Unreleased]: https://github.com/socekin/TransPodcast/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/socekin/TransPodcast/releases/tag/v0.1.0
