# TransPodcast

TransPodcast is a personal media translation service for privately consuming followed content in the owner's preferred language.

## Language

**Owner**:
The sole person who selects content sources and listens to the translated audio during the initial product phase.
_Avoid_: User, customer, audience

**TransPodcast Host**:
The always-on Mac that runs orchestration, durable workflow state, the local
Translation Agent, and the Owner Console.
_Avoid_: Cloud control plane

**Owner Console**:
The web interface through which the Owner configures sources and interests, reviews decisions, monitors Processing Runs, and manages publications.
_Avoid_: Dashboard, admin panel, public website

**Setup Readiness**:
The verified state in which Codex, the dedicated Modal Environment, Cloudflare publication resources, the Private Feed, and at least one Source Channel are configured. Telegram is reported separately as an optional notification channel and never blocks background processing or Web publication.
_Avoid_: Application health, onboarding progress, Processing Run status

**Owner Notification**:
An optional out-of-band Telegram Bot message for a high-confidence Candidate Recommendation, a failed Processing Run, processing paused by the Spend Guardrail, or a Translated Episode awaiting publication. Candidate Videos with missing evidence, ambiguous matches, unsupported languages, Duration Guardrail outcomes, or ignore decisions remain silent. Decision messages provide one-time actions appropriate to their state, and each event type is configurable. The Owner Console remains complete when Telegram is unavailable.
_Avoid_: Email, macOS notification, feed update

**Candidate Recommendation**:
A selective Mandarin Telegram message sent only when Agent triage returns `process` with at least 0.90 confidence, an explicit Interest Profile match, usable Triage Evidence, and valid Mandarin presentation fields. It includes the original title and canonical YouTube URL. In manual-processing mode it waits for the Owner to start complete processing or ignore the Candidate Video.
_Avoid_: Decision Queue notification, uncertain candidate, automatic processing

**Processing Service**:
The background service on the TransPodcast Host that continuously discovers content, advances Processing Runs, and recovers unfinished work after restarting.
_Avoid_: Script, cron job, Agent

**Processing Stage Attempt**:
One durable execution of a Processing Run stage, with its own start and finish
time, status, correlation identity, retry ancestry, transfer evidence, and
safe error or routing evidence. Successful stage checkpoints remain separate
and immutable. The existing database and API use the shorter compatibility
name `Processing Attempt` for this same concept.
_Avoid_: Processing Run, pipeline step, generic log line

**Modal Account**:
A named local Modal profile eligible to execute a routed GPU operation. The
Processing Run records the selected profile and safe failover path without
storing credentials, tokens, or raw provider output.
_Avoid_: Modal Environment, pipeline stage, billing credential

**Processing Target**:
The operational goal for an accepted Candidate Video to reach the Publication Queue within twenty-four hours under normal capacity and without an Owner or Spend Guardrail block.
_Avoid_: Guaranteed SLA, publication time, discovery interval

**Processing Priority**:
The queue order that favors Owner-requested revisions, then Owner-approved Candidate Videos, then automatically accepted videos ordered by source publication time. Priority does not interrupt an already running GPU stage.
_Avoid_: Channel Mode, processing status, preemption

**Retry Policy**:
The error-aware recovery rule that retries transient failures after approximately five minutes, thirty minutes, and two hours, while immediately surfacing budget, authentication, and deterministic validation failures. Retries resume from the latest valid stage checkpoint.
_Avoid_: Manual rerun, infinite retry, full restart

**Private Feed**:
A single access-controlled podcast feed that aggregates Owner-approved Translated Episodes from every Source Channel for personal listening.
_Avoid_: Private blog, public podcast

**Feed Token**:
The high-entropy credential embedded in the stable Private Feed URL. It has no scheduled rotation and changes only through an explicit Owner revocation, which invalidates the old URL.
_Avoid_: Login session, preview token, API key

**Publication Domain**:
The stable custom Cloudflare hostname used for the Private Feed, Private Previews, artwork, and Publication Audio regardless of changes to the underlying Worker or R2 layout.
_Avoid_: workers.dev URL, R2 endpoint, Owner Console address

**Episode Metadata**:
The compact Agent-authored display information for a Translated Episode: a source-prefixed Mandarin title, a short Mandarin overview, and the original title, guests, publication date, and YouTube URL.
_Avoid_: Publication Description, Translation Artifact, Interest Match, full transcript

**Publication Description**:
The expanded Mandarin copy published on one episode page. It summarizes the retained editorial discussion in two or three paragraphs and remains separate from the compact Episode Metadata overview.
_Avoid_: Episode Metadata overview, transcript, Translation Artifact

**Publication Metadata Artifact**:
The versioned, independently validated Reviewer output containing a Publication Description, optional participant line, and semantic Episode Chapter proposals expressed as stable segment anchors without timestamps.
_Avoid_: Translation Artifact, Render Plan, final chapter sidecar

**Synchronized Transcript**:
The durable Mandarin text aligned to the assembled audio timeline and Speaker identities. It supports Owner Console search and editing but is not included in the initial Private Feed because Apple Podcasts does not display transcripts for private RSS feeds.
_Avoid_: Translation Artifact, Canonical Transcript, episode description

**Episode Chapter**:
An Agent-proposed Mandarin topic title resolved by the Processing Service to a validated final Mandarin audio time. Chapters are delivered through a Podcasting 2.0 JSON sidecar and matching description timestamps and remain Owner-editable before publication.
_Avoid_: Transcript segment, source chapter, content summary

**Show Artwork**:
The single 3000-by-3000 TransPodcast cover for the Private Feed channel and the fallback image for an episode whose Episode Artwork is unavailable.
_Avoid_: YouTube thumbnail, Episode Artwork, Source Channel avatar

**Episode Artwork**:
The deterministic 3000-by-3000 RGB image for one Translated Episode, derived from its complete source YouTube thumbnail and a small Show Artwork badge through a versioned local template.
_Avoid_: Show Artwork, raw YouTube thumbnail, generated image

**Private Publication**:
An Owner-approved Translated Episode, its metadata, and its immutable media files made available through the Private Feed.
_Avoid_: Upload, public release, deployment

**Publication Revision**:
A corrected immutable version of an existing Private Publication that keeps the current feed version active until the replacement passes applicable validation and receives a new Owner publication decision. An audio revision retains the episode GUID and changes the immutable enclosure URL; a metadata-only revision retains both the GUID and enclosure while changing only presentation metadata.
_Avoid_: In-place overwrite, new episode, automatic update

**Private Preview**:
A quality-approved immutable AAC file stored in private R2 and available to the Owner for seven days through a temporary authorized URL without appearing in the Private Feed. The Telegram message does not provide link renewal; the Owner Console remains available for later audition.
_Avoid_: Publication, local audio file, public link

**Publication Audio**:
The immutable 48 kHz stereo AAC-LC file in an M4A container encoded at 128 kbps. Mastering targets -16 LUFS and -1.5 dBTP before encoding; the final M4A must remain within -16 ±0.5 LUFS and at or below -1 dBTP for preview and Private Feed delivery.
_Avoid_: Lossless master, TTS render, source audio

**Quality Gate**:
A set of automated, deterministic checks that a Processing Run must pass before it becomes eligible for an Owner publication decision.
_Avoid_: Human review, quality score, approval

**Publication Queue**:
Translated Episodes that passed every Quality Gate and await an explicit Owner decision in the Owner Console or the optional Telegram channel to publish or not publish.
_Avoid_: Review Queue, automatic publication, RSS feed

**Publication Decision**:
The durable, channel-neutral Owner instruction to publish or not publish one eligible Translated Episode or Publication Revision. Owner Console and Telegram requests revalidate and apply through the same idempotent operation; transport tokens and notification delivery are not the system of record.
_Avoid_: Telegram action, Quality Gate result, automatic publication

**Unpublished Episode**:
A Translated Episode that the Owner chose not to add to the Private Feed while retaining its history and ability to be revised or resubmitted for publication.
_Avoid_: Deleted episode, failed Processing Run, ignored Candidate Video

**Review Queue**:
Processing Runs that failed a Quality Gate or contain unresolved uncertainty and therefore require an Owner decision before publication.
_Avoid_: Failed jobs, inbox, Triage Decision

**Source Channel**:
A YouTube channel manually added by the Owner for monitoring during the initial product phase.
_Avoid_: Blog, feed, subscription

**Discovery Interval**:
The configurable cadence for checking every active Source Channel, initially set to two hours. Its next due time and single-flight lease are durable across Processing Service restarts. Manual discovery and overdue startup catch-up use the same idempotent discovery process.
_Avoid_: Processing Target, polling timeout, publication schedule

**Discovery Baseline**:
The Source Channel follow time used as the lower publication boundary for normal discovery. Unselected videos published at or before this point remain channel history; explicit Channel Backfill selections and direct YouTube URL imports bypass the baseline by Owner intent.
_Avoid_: Latest discovery time, Channel Backfill, source publication cursor

**Channel Mode**:
The monitoring behavior of a Source Channel: use Agent triage, process every newly discovered video, or pause discovery. Duration and Spend Guardrails apply in every processing mode.
_Avoid_: Interest Match, channel category, notification setting

**Channel Backfill**:
The ten most recent videos shown for optional manual selection when a Source Channel is added. Backfill videos never start automatically, and older videos may still be added by URL.
_Avoid_: Discovery, channel history import, automatic processing

**Interest Profile**:
The versioned combination of one Owner-edited Markdown document containing natural-language interests and explicit exclusions, plus accepted or rejected examples used to evaluate newly discovered videos. The Owner Console exposes the Markdown document as a simple editor rather than as structured topic or weighting controls. The Agent may propose a document revision, but only the Owner can activate it.
_Avoid_: Prompt, keyword list, hidden preference model

**Labeled Example**:
A Candidate Video marked positive when the Owner chooses to process it or negative when the Owner chooses to ignore it. The decision adds evidence to the Interest Profile without changing its explicit themes or exclusions.
_Avoid_: Rating, implicit behavior, Agent suggestion

**Candidate Video**:
A newly discovered video awaiting or undergoing relevance evaluation.
_Avoid_: Episode, recommendation

**Supported Source Video**:
A publicly accessible YouTube video whose metadata, captions, and audio can be retrieved without account credentials or browser cookies.
_Avoid_: Members-only video, private video, authenticated download

**Interest Match**:
The Agent's evidence-backed assessment of how a Candidate Video relates to the Interest Profile, including matched topics, confidence, and a short rationale.
_Avoid_: Category, recommendation, summary

**Triage Evidence**:
The title, description, chapters, and available source-language captions used to create an Interest Match. Missing or insufficient evidence sends an uncertain Candidate Video to the Decision Queue instead of triggering paid transcription.
_Avoid_: Canonical Transcript, downloaded audio, translated captions

**Triage Decision**:
The relevance outcome assigned to a Candidate Video: recommend processing, request an Owner decision, or ignore it. Execution policy independently determines whether a `process` result queues automatically or waits for explicit Owner approval.
_Avoid_: Recommendation, approval

**Duration Guardrail**:
A configurable source-duration limit on automatic processing, initially set to three hours. A longer Candidate Video always enters the Decision Queue even when its Interest Match is otherwise conclusive.
_Avoid_: Maximum supported duration, rejection, processing timeout

**Spend Guardrail**:
A Modal Environment budget dedicated to TransPodcast that caps its actual cloud-compute spend independently of other projects and TransPodcast's local estimates.
_Avoid_: Cost estimate, usage chart, processing quota

**Decision Queue**:
Candidate Videos that require an Owner decision. It contains uncertain Interest Matches and, while manual processing is enabled, clear Agent recommendations that have not yet been accepted or ignored. Only the latter may produce a selective Candidate Recommendation in Telegram.
_Avoid_: Review Queue, processing failure, inbox

**Ignored List**:
Candidate Videos classified as clearly outside the Interest Profile and retained as feedback evidence without starting a Processing Run.
_Avoid_: Deleted videos, blocked channels, Review Queue

**Target Language**:
Mandarin Chinese, the sole language of generated speech during the initial product phase.
_Avoid_: Preferred language, output locale

**Source Language**:
English, the sole source language covered by the initial product's automatic quality expectations. Other detected languages require an Owner decision and are treated as unsupported experiments.
_Avoid_: Target Language, caption language, interface locale

**Translated Episode**:
A Mandarin audio rendition of an accepted source video's complete editorial
conversation, delivered through the Private Feed. Interest matching selects
the whole source video and never limits translation to matched topics. A
validated Content Edit Artifact may remove only a separate third-party paid
advertisement or assembled opening highlight from Publication Audio while the
complete source and Mandarin artifacts remain available.
_Avoid_: Summary, adaptation, clip

**Canonical Transcript**:
The versioned, corrected source-language text that translation and speech generation treat as authoritative.
_Avoid_: Raw transcript, ASR output, captions

**Source Analysis Canonicalization**:
The retired v8 experiment that changed segmentation and listening behavior.
V9 and later replace it with Episode-local Speaker Consolidation and Boundary
Ownership Adjudication wrapped around the shared v6 word-grouping rules.
_Avoid_: Model inference, Speaker Registry matching, transcript editing

**Speech Pipeline V9**:
The historical Speech Pipeline identified as `transpodcast-modal-pipeline-v9`.
It established the production baseline that preserves v6 pacing,
segmentation, rendering, and mastering while applying Episode-local Speaker
Consolidation, Boundary Ownership Adjudication, and audited Voice Reference
selection. Its exact release remains reproducible under ADR-0021.
_Avoid_: v8 Source Analysis Canonicalization, calibration path, optional trust layer

**Speech Pipeline V10**:
The production Speech Pipeline identified as `transpodcast-modal-pipeline-v10`.
It preserves every V9/v6 segment, timing, rendering, and mastering behavior
while expanding only Boundary Ownership Adjudication candidate coverage and
adopting a clear Codex discourse decision when it conflicts with a fragment
voiceprint. It has no runtime feature gates; rollback is a code deployment
defined by ADR-0026.
_Avoid_: translation revision, resegmentation, all-segment adjudication

**Speech Pipeline V11**:
The historical Speech Pipeline identified as `transpodcast-modal-pipeline-v11`.
It preserves the V10/v6 listening baseline and adds bounded Tail-to-Next
Speaker Boundary Repair before translation. It retains an episode-level
ten-percent candidate guard and six-repair ceiling so unfinished V11 runs and
Decision Records remain reproducible under ADR-0028.
_Avoid_: active Pipeline Version, broad resegmentation, new diarization model

**Speech Pipeline V12**:
The historical Speech Pipeline identified as `transpodcast-modal-pipeline-v12`.
It upgrades the pinned diarizer to Streaming Sortformer v2.1 and preserves the
V11/V10/v6 listening baseline. Every Tail-to-Next Speaker Boundary Repair
candidate must still pass the complete individual acoustic, duration,
adjacency, ownership, and discourse contract, but no episode-level ratio or
accepted-repair count can discard independently qualified work.
_Avoid_: broad resegmentation, all-boundary adjudication, voiceprint-model upgrade

**Speech Pipeline V13**:
The historical Speech Pipeline identified as `transpodcast-modal-pipeline-v13`.
It preserves the complete V12 model and listening baseline while making
Boundary Ownership Adjudication continuity-first and applying compatible
Tail-to-Next Speaker Boundary Repair chains atomically per local connected
component. One local failure preserves only that component; unrelated valid
repairs still apply. V13 has no runtime feature flag.
_Avoid_: new segmentation model, broad resegmentation, translation revision

**Speech Pipeline V14**:
The production Speech Pipeline identified as `transpodcast-modal-pipeline-v14`.
It preserves the complete V13 model and listening baseline while replacing
sequential whole-segment ownership and tail-repair questions with Unified
Speaker Boundary Adjudication over one immutable Canonical Transcript
snapshot. V14 has no runtime feature flag; rollback is an explicit V13 code
deployment under ADR-0031.
_Avoid_: new acoustic model, all-boundary adjudication, new TTS unit

**Episode-local Speaker Consolidation**:
The evidence-gated mapping of multiple raw diarization labels to one Speaker
inside a single episode. It requires converging multi-window evidence and
never merges persistent Speaker Registry identities or replaces an approved
Canonical Voice Reference.
_Avoid_: Speaker Registry merge, Channel Speaker Profile match, expected Speaker count

**Speaker Boundary Stabilization**:
The retired v7 Source Analysis step that corrected weak, short-lived
episode-local word-to-Speaker changes with deterministic lexical rules. It
is no longer part of the active pipeline; Boundary Ownership Adjudication
supersedes it (ADR-0019).
_Avoid_: Transcript editing, Speaker Registry merge, new attribution model

**Boundary Ownership Adjudication**:
The evidence-gated decision that assigns a high-risk short-boundary or
sparse-label source segment to one permissible episode-local Speaker using
two independent judges: a fragment voiceprint measurement and a text-only
discourse-continuity check through the Translation Adapter. Judge agreement
applies; a clear Codex discourse verdict wins a disagreement and marks the
segment disputed for asynchronous Owner review. When the voiceprint cannot
measure the fragment, a clear discourse verdict may apply alone and is always
marked; an explicit discourse `unsure` or missing answer keeps the original
assignment and is marked. Stable segments are not judged. Outcome application
changes only Speaker ownership and never blocks synthesis or publication.
The V13 discourse question treats the initial owner as untrusted and compares
deterministic adjacent-owner reconstructions before deciding.
V14 retains its candidate evidence inside Unified Speaker Boundary
Adjudication but does not apply this whole-segment policy as a separate step.
_Avoid_: V14 active application, Speaker Boundary Stabilization, Review Queue entry

**Mixed-Speaker Segment Split Adjudication**:
The retired, unactivated V11 experiment that could create a new child segment
for one internal mixed-Speaker handoff. Source review found that simultaneous
speech, ASR corruption, and multiple boundaries made this too broad for the
current listening requirement.
_Avoid_: production behavior, Tail-to-Next Speaker Boundary Repair

**Tail-to-Next Speaker Boundary Repair**:
The V11-and-later decision that moves one short, clearly misowned suffix from
the current Canonical Transcript segment to the beginning of its immediately
following differently owned segment. Persisted acoustic evidence selects a
bounded pair for review; Codex decides `keep`, `shift_tail`, or `unsure`. A
valid repair retains both segment IDs and Speakers, conserves every word and
timestamp, and creates no new TTS unit. Ambiguity preserves the V10 pair.
V13 groups adjacent candidates into local linear components. A middle segment
may receive one conserved tail and send a different original suffix in one
atomic plan. Invalid components roll back locally while independent valid
components remain applied.
V14 retains its acoustic candidate evidence inside Unified Speaker Boundary
Adjudication but does not apply this bounded policy as a separate step.
_Avoid_: V14 active application, general resegmentation, overlap reconstruction

**Unified Speaker Boundary Adjudication**:
The V14 decision over one suspicious adjacent-segment boundary from an
immutable Canonical Transcript snapshot. One Codex Boundary Planner proposes
`keep`, `reassign_whole`, `split_to_next`, or `unsure`; a separate Codex
Boundary Reviewer must approve every mutation. An approved suffix move uses
one exact existing word boundary, moves only into the immediate next segment,
keeps segment IDs and count stable, and conserves every word, timestamp, and
order. Acoustic and voiceprint results are evidence rather than hard vetoes.
Third-Speaker evidence, multiple internal handoffs, incompatible connected
actions, malformed output, provider degradation, or uncertainty preserves the
affected component.
_Avoid_: all-segment review, new diarization pass, transcript rewriting

**Decision Record**:
The durable, content-addressed record of one automatic speaker decision —
a consolidation verdict, an ownership adjudication, a tail boundary repair,
a unified boundary decision, a Voice Reference selection, or a fallback —
including its scores, policy version, and
plain-language rationale. Records cover kept decisions as well as changes
so misses can be diagnosed and thresholds re-derived from full
populations.
_Avoid_: Operational Log, Processing Run Activity, review note

**Processing Detail**:
The interactive Owner Console surface beside the transcript that mirrors
its segmentation and shows, per segment, the translation, source text,
Speaker attribution, listen actions, and Decision Record status, with
disputed segments marked for one-click rulings. The Evaluation Report
remains the read-only post-hoc surface.
_Avoid_: Evaluation Report, Review Queue, admin log view

**Transcript Revision**:
A versioned Owner correction to a Canonical Transcript segment that invalidates translation and speech artifacts only for the affected segment and its required downstream assembly.
_Avoid_: Translation edit, raw ASR replacement, full retranscription

**Translation Artifact**:
A versioned, segment-aligned Mandarin rendition of a Canonical Transcript produced by a translation Agent.
_Avoid_: Machine translation, prompt response, translated audio

**Content Edit Artifact**:
A versioned semantic record produced by the independent Translation Reviewer
that proposes only high-confidence `paid_ad` and `opening_highlight` source
intervals for render-only removal. It carries literal source anchors,
confidence, rationale, evidence, and any Mandarin boundary-fragment text. An
invalid or uncertain decision retains content.
_Avoid_: Sponsor detector, keyword filter, edited transcript

**Render Plan**:
The deterministic, source-aligned set of retained Mandarin units submitted to
speech rendering after Content Edit anchors are validated against Canonical
Transcript word timestamps. It never mutates the Canonical Transcript or
Translation Artifact.
_Avoid_: Translation Artifact, playlist, source edit

**Translation Project**:
The durable, validator-owned workspace that lets one Author Session complete a full-episode Translation Artifact over multiple resumable turns before a separate Reviewer Session checks an immutable author-complete snapshot.
_Avoid_: Prompt, temporary worksheet, Processing Run, Codex chat

**Translation Review**:
An independent Reviewer Session that checks a complete authored Translation
Artifact for omissions, names, numbers, terminology, and speaker alignment,
applies targeted corrections in its own snapshot, produces the final verdict,
and separately writes the Content Edit Artifact. Translation approval and
Content Edit validity remain independent.
_Avoid_: Owner approval, full retranslation, audio quality check

**Segment Revision**:
A versioned Owner correction to one segment's translation, Speaker assignment, or emotion reference that reruns only the affected rendering and required downstream assembly.
_Avoid_: In-place edit, full Processing Run, source transcript correction

**Translation Agent**:
The context-aware Agent responsible for producing and checking a Translation Artifact; its execution provider is not part of the term.
_Avoid_: Translation model, Codex CLI, LLM

**Translation Adapter**:
The replaceable boundary that invokes a Translation Agent provider and converts its structured result and usage data into a Translation Artifact.
_Avoid_: Translation Agent, prompt, subprocess

**Speaker**:
A distinct person in a source video whose translated speech must remain distinguishable from every other person.
_Avoid_: Host, narrator, voice

**Speaker Registry**:
The persistent collection of verified Speaker identities and canonical voice references reused across source videos. Automatic identity reuse requires a high-confidence match.
_Avoid_: Diarization labels, voice files

**Channel Speaker Profile**:
The Owner-maintained relationship that makes one verified Speaker and its Canonical Voice Reference eligible for high-confidence reuse within one Source Channel. An inactive profile remains in audit history but is excluded from future automatic matching.
_Avoid_: Global Speaker, channel role, voice preset

**Channel Voice Candidate**:
A Provisional Speaker and representative voice reference from one completed episode awaiting an Owner decision to save, ignore, or merge within its Source Channel. Ignoring it changes only the maintenance queue and never changes the completed episode.
_Avoid_: Episode-local diarization label, rejected Speaker, Processing Run decision

**Provisional Speaker**:
A newly detected or ambiguously matched Speaker kept separate until the Owner confirms, merges, or splits the identity.
_Avoid_: Unknown error, diarization label, guest

**Voice Fidelity**:
The recognizable continuity of a Speaker's timbre and speaking character in a Translated Episode; it does not require matching the source timing or lip movements.
_Avoid_: Lip sync, exact voice copy

**Canonical Voice Reference**:
The Owner-approved clean speech sample that anchors a verified Speaker's identity across episodes. A newly discovered candidate cannot replace it without Owner approval.
_Avoid_: Emotion reference, latest sample, diarization embedding

**Emotion Reference**:
A source-episode speech sample used to guide the delivery of one translated segment without changing the Speaker's Canonical Voice Reference. An unusable sample falls back first to nearby clean speech from the same Speaker and then to the Speaker's neutral Canonical Voice Reference without requiring Owner review.
_Avoid_: Voice identity, emotion parameter, generated prompt

**Source Event**:
A non-verbal moment such as laughter that is retained from the source audio instead of translated or regenerated.
_Avoid_: Speech segment, background audio

**Crosstalk**:
Source speech from multiple Speakers that overlaps in time. Meaningful translated turns are serialized in source order with short pauses for clarity, while non-verbal reactions remain Source Events when practical.
_Avoid_: Speaker error, combined voice, omitted interruption

**Background Audio**:
Music, ambience, and sound effects from a source video that may be retained only when they do not reduce speech clarity or Voice Fidelity.
_Avoid_: Required audio, soundtrack

**Spoken Content Retention**:
The historical ADR-0020 policy that retired phrase-based Sponsor omission and
kept all speech in Publication Audio. ADR-0023 supersedes only its retain-all
consequence while preserving its ban on phrase matching and its
retain-on-uncertainty rule.
_Avoid_: Active Sponsor detector, phrase filter, implicit omission

**Processing Run**:
An auditable execution that transforms one accepted source video into one Translated Episode while retaining its history and artifacts according to the retention policy.
_Avoid_: Job, task, generation

**Processing Run Activity**:
An append-only owner-facing lifecycle record for meaningful Processing Run transitions such as queue entry, stage completion, failure, retry request, and completion. It is durable audit evidence, not a stream of provider logs.
_Avoid_: Operational Log, Processing Attempt, console refresh event

**Pipeline Version**:
The immutable set of speech-model revisions, runtime images, policies, and assembly settings used by a Processing Run. The locally installed Codex CLI is not pinned, but its observed CLI and model identities are recorded separately.
_Avoid_: Application version, latest models, Processing Run

**Translation Baseline**:
An accepted Processing Run whose source, transcript, segmentation, voices, rendering settings, and assembly are fixed for controlled comparisons.
_Avoid_: Sample, demo, latest output

**Pinned Artifact**:
An artifact explicitly exempted from automatic local cleanup because it is part of a Translation Baseline or is needed for investigation.
_Avoid_: Permanent file, backup, favorite

**Recovery Window**:
The thirty-day period after an Owner deletes an episode during which it is removed from the Private Feed but its R2 source and output media remain recoverable.
_Avoid_: Local retention, unpublished episode, backup

**State Backup**:
A daily snapshot of SQLite, structured artifacts, recoverable Translation Project state, configuration, and the Speaker Registry stored in private R2 using Cloudflare-managed storage encryption. Daily snapshots are retained for thirty days and monthly snapshots for twelve months.
_Avoid_: Source archive, local intermediate, customer-managed encryption

**Operational Log**:
A secret-redacted local diagnostic stream retained for at most seven days and capped at 500 MB. Durable Processing Run state, version, usage, and final error summaries are not Operational Logs.
_Avoid_: Audit record, Translation Artifact, cloud telemetry

**Evaluation Report**:
A post-hoc, read-only review surface for one completed Translated Episode that joins its Canonical Transcript, Translation Artifact, and publication segment timings so the Owner can attribute a heard defect to the stage that caused it. Generating one never modifies the system of record and never changes a Processing Run.
_Avoid_: Quality Gate, Review Queue, listening scorecard

**Review Finding**:
A deterministic review prompt on an Evaluation Report naming the segments and the stage involved. A Finding asserts a pattern worth listening to, never a defect.
_Avoid_: Error, Quality Gate failure, Interest Match

**Review Verdict**:
The Owner's classification of a Review Finding as ok, segmentation error, serialization artifact, or translation issue. Verdicts live in exported files scoped to their episode and are never written back to the system of record.
_Avoid_: Owner publication decision, Triage Decision, Labeled Example

**Partial Evaluation Report**:
An Evaluation Report whose Processing Run never recorded some evidence, such as publication segment timings, per-word timestamps, or Speaker Registry links. It is labelled partial so thin evidence is never read as complete.
_Avoid_: Failed report, missing episode, Quality Gate failure
