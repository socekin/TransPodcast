# Changelog

All notable public changes to TransPodcast are documented in this file. The
project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
uses semantic versioning for public releases.

## [Unreleased]

### Changed

- Add an episode from any link yt-dlp can extract one episode from:
  YouTube, Apple Podcasts and other podcast pages, direct audio files, and
  the other sites yt-dlp supports. The web interface and `transpodcast add`
  accept such links; another platform's show is kept paused, and following
  a channel stays YouTube-only.
- YouTube downloads no longer open a browser: current yt-dlp uses YouTube
  clients that need no proof-of-origin token. The `yt-dlp-getpot-wpc`
  plugin and `TRANSPODCAST_YOUTUBE_BROWSER_EXECUTABLE` are removed.
- yt-dlp runs from its own install under `TRANSPODCAST_YTDLP_ROOT` and moves
  to each new release: the worker checks once a day, and a failed download is
  retried once after an upgrade when a newer release is out.

- TransPodcast Host's web interface is rebuilt around adding, listening, and
  publishing: a home screen with the add field, episodes in progress, a
  resume-listening mini player, and the library; an episode screen with a
  ring player, a transcript synced to playback, summary, quality findings,
  and the processing record; a Run screen with progress and, when a Run
  needs you, the decision inside the failing step; and Settings.
- Adding an episode in the web interface starts its Run right away.
- Playback speed (0.75× to 2×) is one remembered setting for every episode;
  auditioning a Listening Audit line has its own speed starting at 1×.

### Added

- `GET /api/v1/processing-runs/{id}/transcript` returns each line with its
  place in the rendered audio, and episode detail names its
  `processing_run_id`.
- `GET /api/v1/processing-runs/{id}/rendered-audio` serves a Run's rendered
  audio so a Listening Audit line can be auditioned before an episode exists.

### Removed

- The Decision Queue, Interest Profile, Source Channel, Channel Voice Library,
  Run list, segment correction, and publication metadata screens. Their APIs
  remain for Telegram and the `transpodcast` command.

### Security

- Refresh dependencies with newly published advisories: brotli 1.2.1 (via
  yt-dlp), and for development only filelock 4.2.5 and msgpack 1.2.4 (via
  pip-audit), Vitest 4.1.12, and undici 8.12.3 in TransPodcast Host's test
  environment and 7.30.1 in the publication Worker's, with Wrangler 4.156.0
  and Cloudflare Workers types 5.20261031.1.

## [0.3.0] - 2026-10-21

### Added

- Crosstalk handling in Speech Pipeline V23 (IndexTTS 2.0) and V24
  (IndexTTS 2.5), selected for new Processing Runs. Sentence Integrity
  returns a short foreign owner run to the Speaker who owns the sentence, so
  one sentence is not voiced by two Speakers; backchannels and answered runs
  keep their Speaker. Crosstalk Candidates (three or more Speaker changes
  within 6 s) each need a Reviewer decision under
  `translation-policy-v5-crosstalk`: reassemble the segments into complete
  per-Speaker sentences in source order, or, only where attribution cannot be
  recovered, trim fragments without information. No original English audio
  is kept.
- `transpodcast` command for local agents (Codex, Claude Code, and others):
  `add`, `start`, `channel add`, `status`, and `list` call the local Control
  API. An added episode shows which agent added it and a Modal cost
  estimate; an agent starts a waiting Run only when asked. Deleting,
  reprocessing, and publishing stay in TransPodcast Host.
- Close a Review Queue Run from its Run page. A Closed Processing Run is
  never retried, leaves the Episode Library, and keeps its stages, attempts,
  costs, and error in Run history.
- Claude Code token file (`TRANSPODCAST_CLAUDE_OAUTH_TOKEN_PATH`, default
  `data/secrets/claude-oauth-token`): the API and worker pass a one-year
  `claude setup-token` token to every Claude Code call instead of the shared
  interactive Keychain login, which interactive sessions can invalidate. The
  file is read on every call, so renewing it needs no restart.
- Settings shows how each Agent CLI is signed in.

### Changed

- Listening Audit policy `listening-audit-v2`: on a line of fewer than six
  spoken syllables, a back-transcription mismatch (high CER, truncation, or
  insertion) is listed as a soft "short line" finding unless the line's
  speech is shorter than 0.15 s, longer than 2.5 s, or broken by a silence
  over 1.2 s. One or two misheard syllables no longer send an episode to the
  Review Queue or trigger a re-render. Longer lines keep the v1 rules.

### Fixed

- Qwen3-ASR runaway repetition: the Modal transcriber applies the audio
  encoder's attention-window mask, transcribes its own fixed-length chunks,
  re-transcribes a runaway chunk in short pieces, and drops what still runs
  away. Reprocessing an episode whose stored transcript holds such a loop
  transcribes it again instead of reusing it.
- A Claude Code call that fails with an expired or unrefreshable sign-in is
  recorded as an Agent sign-in failure instead of a retried provider error.
- A translation worksheet that ends with extra blank lines no longer fails
  validation.

### Upgrade notes

- Wait until processing is idle, then run `make setup` to install the new
  command and rebuild TransPodcast Host.
- Redeploy the Modal applications with `./deploy/modal-deploy.sh`; the
  Qwen3-ASR application changed.
- If Claude Code is the processing Agent, create the Claude Code token file
  (see `docs/configuration.md`).
- Existing Processing Runs keep their recorded Pipeline Version and Listening
  Audit results; only new Runs use V23/V24.

### Security

- Refresh dependencies with published advisories: Pillow 13.0.0 (the
  supported range is now `>=13,<14`), aiohttp 3.14.5, certifi 2026.10.19,
  and Vite 6.4.5 for the development server.

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

[Unreleased]: https://github.com/socekin/TransPodcast/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/socekin/TransPodcast/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/socekin/TransPodcast/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/socekin/TransPodcast/releases/tag/v0.1.0
