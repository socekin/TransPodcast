"""Post-hoc evaluation report model for a Translated Episode.

The report builder is a pure function over durable Processing Run
checkpoints. It never reads the database, the filesystem, or object
storage, so the review surface stays observable in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from transpodcast.content_editing import build_render_plan, content_edit_for_segment
from transpodcast.domain import SourceAnalysisResult, TranslationResult

# Mirrors the pipeline's own sentence-boundary rule so the report judges
# segmentation by the same standard that produced it.
_SENTENCE_END = re.compile(r"[.!?。！？][\"')\]]?$")


class FindingKind(StrEnum):
    CROSSTALK = "crosstalk"
    INTERLEAVE_SUSPECT = "interleave_suspect"
    MIDSPLIT_SUSPECT = "midsplit_suspect"


class FindingStage(StrEnum):
    CANONICAL_SEGMENTATION = "canonical_segmentation"
    CROSSTALK_SERIALIZATION = "crosstalk_serialization"


class Verdict(StrEnum):
    OK = "ok"
    SEGMENTATION_ERROR = "segmentation_error"
    SERIALIZATION_ARTIFACT = "serialization_artifact"
    TRANSLATION_ISSUE = "translation_issue"


class PartialReason(StrEnum):
    """Evidence a Processing Run never recorded, named so a thin report
    is marked as thin instead of read as complete."""

    PUBLICATION_TIMINGS = "publication_timings"
    WORD_TIMESTAMPS = "word_timestamps"
    SPEAKER_IDENTITIES = "speaker_identities"
    SENTENCE_PUNCTUATION = "sentence_punctuation"


CROSSTALK = FindingKind.CROSSTALK
INTERLEAVE_SUSPECT = FindingKind.INTERLEAVE_SUSPECT
MIDSPLIT_SUSPECT = FindingKind.MIDSPLIT_SUSPECT
CANONICAL_SEGMENTATION = FindingStage.CANONICAL_SEGMENTATION
CROSSTALK_SERIALIZATION = FindingStage.CROSSTALK_SERIALIZATION

DEFAULT_SHORT_SEGMENT_WORDS = 3
DEFAULT_SHORT_SEGMENT_SECONDS = 2.0

# Production ASR word tokens carry no punctuation, so a transcript built
# from them has no sentence boundaries for the pipeline — or this report —
# to reason about. Below this share of punctuated segments the
# sentence-boundary signal is treated as absent rather than as evidence.
_PUNCTUATION_PRESENCE_FLOOR = 0.05

# Diarization turn edges routinely miss each other by tens of
# milliseconds. Only an overlap longer than this reads as one Speaker
# talking over another rather than as boundary noise.
_GENUINE_OVERLAP_SECONDS = 0.25

# Suspect kinds point at segmentation; Crosstalk describes the source
# and is expected behaviour, so it never makes a segment a suspect.
SUSPECT_KINDS = (INTERLEAVE_SUSPECT, MIDSPLIT_SUSPECT)


@dataclass(frozen=True)
class EpisodeInfo:
    """Identifying context for the reviewed Translated Episode."""

    guid: str
    title: str
    source_show: str
    source_title: str
    run_id: int
    generated_at: str
    publication_duration_seconds: int


@dataclass(frozen=True)
class Provenance:
    """The Pipeline Version and models that produced what is reviewed."""

    pipeline_version: str
    diarization_model: str
    transcription_model: str
    alignment_model: str
    translation_model: str
    translation_provider: str
    diarization_model_revision: str = ""


@dataclass(frozen=True)
class ReportSegment:
    segment_id: str
    speaker_label: str
    speaker_name: str
    speaker_provisional: bool
    source_start: float
    source_end: float
    source_text: str
    target_text: str | None
    publication_start: float | None
    publication_end: float | None
    crosstalk: bool
    omitted_as_sponsor: bool
    words: tuple[tuple[str, float, float], ...] = ()
    content_edit_state: str | None = None
    content_edit_category: str | None = None
    content_edit_confidence: float | None = None
    content_edit_rationale: str | None = None
    content_edit_evidence_segment_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceEvent:
    """A non-verbal source moment retained in the publication."""

    kind: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class Finding:
    """A review prompt, never an asserted defect.

    The Owner classifies it; the tool only points at evidence.
    """

    kind: FindingKind
    stage: FindingStage
    segment_ids: tuple[str, ...]
    neighbour_speaker: str = ""
    subject_speaker: str = ""
    word_count: int = 0
    source_seconds: float = 0.0
    overlapping: bool = False
    severity: int = 0
    verdict: Verdict | None = None

    @property
    def key(self) -> str:
        """Stable identity so recorded verdicts survive regeneration."""
        return f"{self.kind}:{'+'.join(self.segment_ids)}"

    @property
    def suspect(self) -> bool:
        return self.kind in SUSPECT_KINDS


@dataclass(frozen=True)
class ReportSummary:
    segment_count: int
    translated_segment_count: int
    omitted_sponsor_count: int
    speaker_count: int
    crosstalk_count: int
    suspect_count: int
    high_severity_count: int
    finding_counts: dict[str, int]
    source_duration_seconds: float
    reviewed_count: int = 0
    semantic_exclusion_count: int = 0
    semantic_excluded_source_seconds: float = 0.0


@dataclass(frozen=True)
class EvaluationReport:
    episode: EpisodeInfo
    provenance: Provenance
    segments: tuple[ReportSegment, ...]
    seek_available: bool
    summary: ReportSummary
    findings: tuple[Finding, ...] = ()
    speakers: tuple[tuple[str, str], ...] = ()
    source_events: tuple[SourceEvent, ...] = ()
    partial_reasons: tuple[PartialReason, ...] = ()

    @property
    def partial(self) -> bool:
        return bool(self.partial_reasons)

    def suspect_segment_ids(self) -> frozenset[str]:
        return frozenset(
            segment_id
            for finding in self.findings
            if finding.suspect
            for segment_id in finding.segment_ids[1:2] or finding.segment_ids
        )


def _display_name(label: str, registry_name: str | None) -> str | None:
    """Drop the auto-generated Provisional Speaker suffix.

    The Speaker Registry names a Provisional Speaker "<label> · <episode
    title>" so candidates stay distinguishable in the Channel Voice
    Library. On a per-segment chip that is unreadable, and the report's
    provisional marker already carries the same meaning.
    """
    if registry_name is None:
        return None
    if registry_name.startswith(f"{label} · "):
        return label
    return registry_name


def _word_count(segment: ReportSegment) -> int:
    if segment.words:
        return len(segment.words)
    return len(segment.source_text.split())


def _interleave_findings(
    segments: tuple[ReportSegment, ...],
    *,
    short_words: int,
    short_seconds: float,
) -> list[Finding]:
    findings: list[Finding] = []
    for index in range(1, len(segments) - 1):
        before, middle, after = segments[index - 1 : index + 2]
        if before.speaker_label != after.speaker_label:
            continue
        if middle.speaker_label == before.speaker_label:
            continue
        words = _word_count(middle)
        source_seconds = middle.source_end - middle.source_start
        if words > short_words and source_seconds > short_seconds:
            continue
        # A short turn separated from its neighbours by clean source gaps
        # is most likely a real interjection. One that genuinely overlaps
        # them is where word-to-Speaker attribution is uncertain. The
        # pipeline's own overlap flag counts on its own, because it means
        # words fell inside more than one Speaker's turn.
        overlapping = middle.crosstalk or (
            max(
                before.source_end - middle.source_start,
                middle.source_end - after.source_start,
            )
            > _GENUINE_OVERLAP_SECONDS
        )
        findings.append(
            Finding(
                kind=INTERLEAVE_SUSPECT,
                stage=CANONICAL_SEGMENTATION,
                segment_ids=(
                    before.segment_id,
                    middle.segment_id,
                    after.segment_id,
                ),
                neighbour_speaker=before.speaker_name,
                subject_speaker=middle.speaker_name,
                word_count=words,
                source_seconds=source_seconds,
                overlapping=overlapping,
                severity=2 if overlapping else 1,
            )
        )
    return findings


def _punctuation_available(segments: tuple[ReportSegment, ...]) -> bool:
    """Whether this transcript carries sentence boundaries at all."""
    if not segments:
        return False
    punctuated = sum(
        1 for segment in segments if _SENTENCE_END.search(segment.source_text.strip())
    )
    return punctuated / len(segments) > _PUNCTUATION_PRESENCE_FLOOR


def _midsplit_findings(segments: tuple[ReportSegment, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for before, after in zip(segments, segments[1:], strict=False):
        if before.speaker_label == after.speaker_label:
            continue
        if _SENTENCE_END.search(before.source_text.strip()):
            continue
        findings.append(
            Finding(
                kind=MIDSPLIT_SUSPECT,
                stage=CANONICAL_SEGMENTATION,
                segment_ids=(before.segment_id, after.segment_id),
                neighbour_speaker=after.speaker_name,
                subject_speaker=before.speaker_name,
                severity=1,
            )
        )
    return findings


def _crosstalk_findings(segments: tuple[ReportSegment, ...]) -> list[Finding]:
    return [
        Finding(
            kind=CROSSTALK,
            stage=CROSSTALK_SERIALIZATION,
            segment_ids=(segment.segment_id,),
            subject_speaker=segment.speaker_name,
            source_seconds=segment.source_end - segment.source_start,
        )
        for segment in segments
        if segment.crosstalk
    ]


def _scoped_verdicts(
    verdicts: dict[str, object] | None,
    episode_guid: str,
) -> dict[str, Verdict]:
    """Accept a verdict export only for the episode that produced it.

    Segment ids restart per episode, so an unscoped replay would apply
    one episode's review to every other episode.
    """
    if not verdicts:
        return {}
    recorded_guid = verdicts.get("episode_guid")
    if recorded_guid is not None and str(recorded_guid) != episode_guid:
        return {}
    raw = verdicts.get("verdicts", verdicts)
    if not isinstance(raw, dict):
        return {}
    resolved: dict[str, Verdict] = {}
    for key, value in raw.items():
        try:
            resolved[str(key)] = Verdict(str(value))
        except ValueError:
            continue
    return resolved


def build_report(
    *,
    analysis: SourceAnalysisResult,
    translation: TranslationResult,
    segment_timings: tuple[tuple[float, float], ...],
    episode: EpisodeInfo,
    speaker_names: dict[str, str],
    verdicts: dict[str, object] | None = None,
    short_segment_words: int = DEFAULT_SHORT_SEGMENT_WORDS,
    short_segment_seconds: float = DEFAULT_SHORT_SEGMENT_SECONDS,
) -> EvaluationReport:
    """Join analysis, translation, and publication timings into a report."""
    segments: list[ReportSegment] = []
    render_plan = build_render_plan(analysis, translation)
    render_timings_by_source: dict[
        str, list[tuple[float, float]]
    ] = {}
    if render_plan.exclusions:
        for render_segment, timing in zip(
            render_plan.analysis.segments,
            segment_timings,
            strict=False,
        ):
            source_segment_id = render_segment.segment_id.split(
                "-retained-",
                1,
            )[0]
            render_timings_by_source.setdefault(
                source_segment_id,
                [],
            ).append(timing)
    legacy_sponsor_omission = (
        len(translation.segments) < len(analysis.segments)
        and len(translation.segments)
        == sum(
            1
            for segment in analysis.segments
            if not segment.omitted_as_sponsor
        )
    )
    translated_ordinal = 0
    for source_ordinal, segment in enumerate(analysis.segments):
        target_text: str | None = None
        publication_start: float | None = None
        publication_end: float | None = None
        legacy_omitted = (
            legacy_sponsor_omission and segment.omitted_as_sponsor
        )
        semantic_edit = content_edit_for_segment(render_plan, segment)
        semantic_evidence = (
            semantic_edit.get("evidence_segment_ids", [])
            if semantic_edit is not None
            else []
        )
        if not isinstance(semantic_evidence, list):
            semantic_evidence = []
        if render_plan.exclusions:
            if source_ordinal < len(translation.segments):
                target_text = translation.segments[source_ordinal]
            mapped_timings = render_timings_by_source.get(
                segment.segment_id,
                [],
            )
            if mapped_timings:
                publication_start = mapped_timings[0][0]
                publication_end = mapped_timings[-1][1]
        elif not legacy_omitted:
            if translated_ordinal < len(translation.segments):
                target_text = translation.segments[translated_ordinal]
            if translated_ordinal < len(segment_timings):
                publication_start, publication_end = segment_timings[
                    translated_ordinal
                ]
            translated_ordinal += 1
        resolved_name = _display_name(
            segment.speaker_label,
            speaker_names.get(segment.speaker_label),
        )
        segments.append(
            ReportSegment(
                segment_id=segment.segment_id,
                speaker_label=segment.speaker_label,
                speaker_name=resolved_name or segment.speaker_label,
                speaker_provisional=(
                    resolved_name is None or resolved_name == segment.speaker_label
                ),
                source_start=segment.start_seconds,
                source_end=segment.end_seconds,
                source_text=segment.text,
                target_text=target_text,
                publication_start=publication_start,
                publication_end=publication_end,
                crosstalk=segment.overlap,
                omitted_as_sponsor=legacy_omitted,
                words=segment.words,
                content_edit_state=(
                    str(semantic_edit["state"])
                    if semantic_edit is not None
                    else None
                ),
                content_edit_category=(
                    str(semantic_edit["category"])
                    if semantic_edit is not None
                    else None
                ),
                content_edit_confidence=(
                    float(str(semantic_edit["confidence"]))
                    if semantic_edit is not None
                    else None
                ),
                content_edit_rationale=(
                    str(semantic_edit["rationale"])
                    if semantic_edit is not None
                    else None
                ),
                content_edit_evidence_segment_ids=(
                    tuple(str(item) for item in semantic_evidence)
                    if semantic_edit is not None
                    else ()
                ),
            )
        )
    ordered = tuple(segments)
    recorded = _scoped_verdicts(verdicts, episode.guid)
    punctuated = _punctuation_available(ordered)
    detected = [
        *_interleave_findings(
            ordered,
            short_words=short_segment_words,
            short_seconds=short_segment_seconds,
        ),
        *(_midsplit_findings(ordered) if punctuated else []),
        *_crosstalk_findings(ordered),
    ]
    ordinal = {segment.segment_id: index for index, segment in enumerate(ordered)}
    findings = tuple(
        replace(finding, verdict=recorded.get(finding.key))
        # Worst first, then in publication order, so the Owner reviews
        # the most uncertain moments before the routine ones.
        for finding in sorted(
            detected,
            key=lambda item: (
                -item.severity,
                ordinal.get(item.segment_ids[0], 0),
            ),
        )
    )
    speakers: dict[str, str] = {}
    for reported in ordered:
        speakers.setdefault(reported.speaker_label, reported.speaker_name)
    finding_counts: dict[str, int] = {}
    for finding in findings:
        finding_counts[str(finding.kind)] = (
            finding_counts.get(str(finding.kind), 0) + 1
        )
    reasons: list[PartialReason] = []
    if not segment_timings:
        reasons.append(PartialReason.PUBLICATION_TIMINGS)
    if not any(segment.words for segment in ordered):
        reasons.append(PartialReason.WORD_TIMESTAMPS)
    if not speaker_names:
        reasons.append(PartialReason.SPEAKER_IDENTITIES)
    if not punctuated:
        reasons.append(PartialReason.SENTENCE_PUNCTUATION)
    return EvaluationReport(
        episode=episode,
        provenance=Provenance(
            pipeline_version=analysis.pipeline_version,
            diarization_model=analysis.diarization_model,
            diarization_model_revision=analysis.diarization_model_revision,
            transcription_model=analysis.transcription_model,
            alignment_model=analysis.alignment_model,
            translation_model=translation.model,
            translation_provider=translation.provider,
        ),
        segments=ordered,
        seek_available=bool(segment_timings),
        summary=ReportSummary(
            segment_count=len(ordered),
            translated_segment_count=len(translation.segments),
            omitted_sponsor_count=sum(
                1 for segment in ordered if segment.omitted_as_sponsor
            ),
            speaker_count=len(speakers),
            crosstalk_count=sum(1 for segment in ordered if segment.crosstalk),
            suspect_count=sum(1 for finding in findings if finding.suspect),
            high_severity_count=sum(
                1 for finding in findings if finding.suspect and finding.severity > 1
            ),
            finding_counts=finding_counts,
            source_duration_seconds=(
                max(segment.source_end for segment in ordered) if ordered else 0.0
            ),
            semantic_exclusion_count=len(render_plan.exclusions),
            semantic_excluded_source_seconds=(
                render_plan.excluded_source_seconds
            ),
            reviewed_count=sum(
                1 for finding in findings if finding.verdict is not None
            ),
        ),
        findings=findings,
        speakers=tuple(speakers.items()),
        source_events=tuple(
            SourceEvent(
                kind=str(kind),
                start_seconds=float(start),
                end_seconds=float(end),
            )
            for kind, start, end in analysis.source_events
        ),
        partial_reasons=tuple(reasons),
    )
