# Changelog

All notable public changes to TransPodcast are documented in this file. The
project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
uses semantic versioning for public releases.

## [Unreleased]

## [0.2.0] - 2026-10-14

### Added

- Selectable Agent provider: Codex CLI remains the default, and Claude Code
  CLI can be selected in Settings. Each Processing Run freezes its provider
  when it is queued or explicitly reprocessed, so retries never switch
  providers, and a Translation Project never resumes another provider's
  session. Claude Code runs isolated from personal CLI configuration.
- Optional IndexTTS 2.5 renderer, selectable in Settings next to IndexTTS 2.0,
  with a versioned speech-only pronunciation policy for English names and
  terms. The renderer choice is also frozen per Processing Run.
- Rhythm restoration: pauses follow the source gaps between speech units, with
  separate limits for same-speaker pauses and speaker changes. Edge silence is
  trimmed, removed content becomes a short fixed transition, stretched short
  replies are sped up within a bound, and long pauses inside a rendered
  sentence are capped.
- Unit Clip Store: raw rendered speech units are kept as lossless local clips,
  so assembly and segment revisions master the episode once.
- Full-band voice references selected after speaker ownership is final, away
  from removed content, plus emotion-reference hygiene that never borrows a
  prompt containing another speaker, crosstalk, or a sound event.
- Listening Audit after rendering: Mandarin back-transcription, truncation,
  insertion, English leakage, speech rate, internal silence, duration, and
  voice-consistency checks. Units with severe findings are re-rendered once
  automatically; remaining severe findings require an explicit confirmation
  before the episode enters the publication queue. The audit is shown in
  Processing Detail with listen actions, and an unavailable audit never blocks
  publication.
- Deterministic speaker ownership before the agent ownership review:
  zero-duration word retiming, earlier speaker-change onsets with a short-turn
  guard, dense voiceprint windows, a sparse-label merge, and a Span Identity
  Check that relabels a whole misattributed span when the voiceprints agree.
- Agent-reviewed speaker ownership regions and natural segmentation at speaker
  changes, sentence endings, and audible pauses, with the subflow and its
  evidence shown in Processing Detail.
- Listening-oriented translation policy for new Translation Projects: spoken
  Mandarin numerals, no parenthetical glosses, established Chinese names, one
  written form per name through a project glossary seeded from episode
  metadata, a soft spoken-length budget, cut bridging, in-session content-edit
  validation, sponsor accounting, and structured source warnings.
- Measured Quality Gate fields: codec, sample rate, channels, and average
  bitrate are probed from the encoded publication audio.

### Changed

- New Processing Runs use Speech Pipeline V21 with IndexTTS 2.0 or V22 with
  IndexTTS 2.5. Existing Processing Runs keep their recorded Pipeline Version
  on retry, resume, and segment revision.
- YouTube source downloads use a local Chromium-based browser to generate
  proof-of-origin tokens. Setup Readiness requires the configured browser
  executable (`TRANSPODCAST_YOUTUBE_BROWSER_EXECUTABLE`).
- Setup Readiness reports both Agent CLIs; only the selected provider blocks
  processing. The Modal check also requires the IndexTTS 2.5 renderer class
  and reports Listening Audit availability.
- Translation Review separates source and factual caveats from unresolved
  translation defects, and conflicting approval fields receive one bounded
  repair attempt.
- Provider-neutral wording for agent usage, errors, and cost prompts in
  TransPodcast Host.

### Removed

- Legacy speaker-attribution code paths that new Processing Runs can no longer
  reach: the v7 boundary stabilizer, v8 source canonicalization, and the
  V9-V14 adjudication and boundary-repair worker paths. Stored Decision
  Records of historical runs remain readable.
- The V7 speaker-boundary comparison script.

### Upgrade notes

- Wait until processing is idle, then run `make setup` to install the new
  Python dependency and rebuild TransPodcast Host.
- Redeploy the Modal applications with `./deploy/modal-deploy.sh`; the speech
  application now includes the IndexTTS 2.5 renderer and the Listening Audit
  methods.
- Install Google Chrome, or set `TRANSPODCAST_YOUTUBE_BROWSER_EXECUTABLE` to
  another Chromium-based browser executable.
- Optionally install and sign in to Claude Code, then select it in Settings.
- The database schema upgrades automatically on startup. Unfinished V14
  Processing Runs cannot resume Source Analysis; reprocess them as complete
  episodes. Published episodes and their audio are unchanged.

### Security

- Claude Code invocations ignore user and project settings, instruction
  files, MCP servers, skills, hooks, and plugins, and the child process does
  not receive `ANTHROPIC_API_KEY`.

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

[Unreleased]: https://github.com/socekin/TransPodcast/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/socekin/TransPodcast/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/socekin/TransPodcast/releases/tag/v0.1.0
