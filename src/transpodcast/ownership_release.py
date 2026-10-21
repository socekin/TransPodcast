"""Versioned worker integration of the evaluated continuity ownership policy."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, Protocol, cast

from transpodcast.adjudication_completion import (
    AdjudicationCompletion,
    DiscourseAdjudicator,
    _natural_boundary_records,
    _with_attribution_records,
)
from transpodcast.continuity_preparation import (
    decode_continuity_evidence,
    prepare_continuity_regions,
)
from transpodcast.decision_records import DecisionRecord
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.natural_segmentation import (
    DEFAULT_NATURAL_SEGMENTATION_POLICY,
    NaturalBoundary,
    NaturalSegmentationResult,
    segment_naturally,
)
from transpodcast.ownership_acoustic_coverage import find_ownership_edge_gaps
from transpodcast.ownership_continuity_completion import (
    ContinuityCompletion,
    complete_continuity_ownership,
    overlay_reviewed_ownership,
    question_fingerprint,
)
from transpodcast.pipeline_versions import DETERMINISTIC_OWNERSHIP_PIPELINES
from transpodcast.sentence_integrity import (
    DEFAULT_SENTENCE_INTEGRITY_POLICY,
    SENTENCE_INTEGRITY_SUMMARY_KEY,
    SentenceIntegrityPolicy,
    SentenceIntegrityResult,
    integrate_sentences,
)
from transpodcast.speaker_ownership_continuity import ContinuityRegion
from transpodcast.speaker_ownership_v15 import (
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
)

RELEASE_POLICY_VERSION = "speaker-ownership-v17"


class _Adjudicator(Protocol):
    def plan_ownership_continuity(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult: ...

    def review_ownership_continuity(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
        planning: SpeakerOwnershipPlanningResult,
    ) -> SpeakerOwnershipPlanningResult: ...


class _Judge:
    def __init__(self, adjudicator: _Adjudicator) -> None:
        self.adjudicator = adjudicator

    def plan(
        self, regions: Sequence[SpeakerOwnershipRegion]
    ) -> SpeakerOwnershipPlanningResult:
        return self.adjudicator.plan_ownership_continuity(regions)

    def review(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
        planning: SpeakerOwnershipPlanningResult,
    ) -> SpeakerOwnershipPlanningResult:
        return self.adjudicator.review_ownership_continuity(regions, planning)


def complete_released_ownership(
    analysis: SourceAnalysisResult,
    adjudicator: DiscourseAdjudicator,
    *,
    checkpoint_writer: Callable[[SourceAnalysisResult], None] | None = None,
    measure_edges: Callable[[list[tuple[int, int]]], dict[str, Any]] | None = None,
) -> AdjudicationCompletion:
    payload = json.loads(analysis.boundary_adjudication_json or "{}")
    if "speaker_ownership_continuity" in payload:
        return AdjudicationCompletion(analysis=analysis)
    if not any(s.words for s in analysis.segments):
        raise ValueError("Continuity ownership requires the aligned word timeline")
    consolidation = json.loads(analysis.speaker_consolidation_json or "{}")
    mapping = dict(consolidation.get("speaker_mapping", {}))
    acoustic = payload.get("continuity_evidence", {})
    evidence = decode_continuity_evidence(acoustic)
    pending = payload.get("pending", {})
    regions = prepare_continuity_regions(
        analysis.segments,
        analysis.raw_diarization_turns,
        mapping,
        analysis.full_asr_text,
        pending,
        evidence,
    )
    phases = dict(payload.get("speaker_ownership_continuity_checkpoint", {}))
    judge = _Judge(cast(_Adjudicator, adjudicator))
    results: list[ContinuityCompletion] = []

    def persist_phases() -> None:
        if checkpoint_writer:
            checkpoint_writer(
                replace(
                    analysis,
                    boundary_adjudication_json=json.dumps(
                        {
                            **payload,
                            "speaker_ownership_continuity_checkpoint": phases,
                        }
                    ),
                )
            )

    def complete(
        name: str, selected: Sequence[ContinuityRegion]
    ) -> ContinuityCompletion:
        def save(checkpoint: dict[str, Any]) -> None:
            phases[name] = checkpoint
            persist_phases()

        result = complete_continuity_ownership(
            analysis.segments,
            selected,
            judge,
            raw_turns=analysis.raw_diarization_turns,
            speaker_mapping=mapping,
            full_asr_text=analysis.full_asr_text,
            source_event_ranges=tuple((s, e) for _, s, e in analysis.source_events),
            checkpoint=phases.get(name),
            checkpoint_writer=save,
        )
        results.append(result)
        return result

    normal = complete("normal", regions)
    original = tuple(s.speaker_label for s in analysis.segments for _ in s.words)
    owners = overlay_reviewed_ownership(original, normal.application)
    gaps = find_ownership_edge_gaps(regions, original, owners)
    edge_acoustic: dict[str, Any] = {}
    if gaps:
        key = question_fingerprint({"ranges": gaps, "acoustic": acoustic})
        cached = phases.get("edge_acoustic", {})
        if cached.get("request_sha256") == key and cached.get(
            "response_sha256"
        ) == question_fingerprint(cached.get("result")):
            edge_acoustic = cached["result"]
        else:
            try:
                if measure_edges is None:
                    raise ValueError("Conditional ownership measurement is unavailable")
                edge_acoustic = measure_edges(list(gaps))
            except Exception as error:
                edge_acoustic = {"errors": [f"{type(error).__name__}: {error}"[:500]]}
            phases["edge_acoustic"] = {
                "request_sha256": key,
                "result": edge_acoustic,
                "response_sha256": question_fingerprint(edge_acoustic),
            }
            persist_phases()
        evidence = (*evidence, *decode_continuity_evidence(edge_acoustic))
    edge_regions = (
        prepare_continuity_regions(
            analysis.segments,
            analysis.raw_diarization_turns,
            mapping,
            analysis.full_asr_text,
            pending,
            evidence,
            gaps,
        )
        if gaps
        else ()
    )
    if edge_regions:
        edge = complete("edges", edge_regions)
        owners = overlay_reviewed_ownership(owners, edge.application)
    remaining = find_ownership_edge_gaps(regions, original, owners)
    records = [record for result in results for record in result.application.records]
    errors = [*acoustic.get("errors", []), *edge_acoustic.get("errors", [])]
    if "continuity_evidence" not in payload:
        errors.append("Continuity acoustic coverage is unavailable")
    errors.extend(
        str(
            r.evidence["reviewer_execution"].get("error")
            or r.evidence.get("reviewer_validation_error")
            or "Final Reviewer plan is unavailable"
        )
        for r in records
        if r.evidence["reviewer_execution"].get("error")
        or r.evidence.get("reviewer_validation_error")
        or r.evidence.get("reviewer_plan") is None
    )
    if remaining:
        errors.append("Ownership coverage edges remain unresolved")
    disputed_records = [
        r
        for r in records
        if r.verdict
        in {
            "applied-disputed",
            "possible-missing-speech",
            "provider-degraded",
            "reviewer-degraded",
        }
        or r.evidence.get("reviewer_plan") is None
        or r.evidence["reviewer_execution"].get("error")
        or r.evidence.get("reviewer_validation_error")
        or (r.evidence.get("reviewer_plan") or {}).get("action")
        in {"unsure", "possible_missing_speech"}
        or any(
            start <= r.subject["last_word_index"]
            and r.subject["first_word_index"] < end
            for start, end in remaining
        )
    ]
    disputed = disputed_word_indexes(
        disputed_records,
        original,
        owners,
        remaining,
        narrow=analysis.pipeline_version in DETERMINISTIC_OWNERSHIP_PIPELINES,
    )
    words: list[CanonicalSegment] = []
    for segment in analysis.segments:
        for word in segment.words:
            i = len(words)
            words.append(
                replace(
                    segment,
                    segment_id=f"word-{i}",
                    speaker_label=owners[i],
                    text=word[0],
                    start_seconds=word[1],
                    end_seconds=word[2],
                    words=(word,),
                    review_state="disputed" if i in disputed else segment.review_state,
                )
            )
    natural = segment_naturally(
        words,
        full_asr_text=analysis.full_asr_text,
        protected_word_ranges=tuple(
            (r.start_word_index, r.end_word_index)
            for r in (*regions, *edge_regions)
            if {"crosstalk", "multiple-handoffs", "three-speaker"}
            & set(r.trigger_reasons)
        ),
        source_event_ranges=tuple((s, e) for _, s, e in analysis.source_events),
    )
    changed = sum(a != b for a, b in zip(original, owners, strict=True))
    batches = [b for result in results for b in result.batches]
    usage = {
        role: {
            key: sum(b[role]["result"][key] for b in batches)
            for key in ("input_tokens", "output_tokens")
        }
        for role in ("planner", "reviewer")
    }
    execution: dict[str, Any] = next((b["reviewer"]["result"] for b in batches), {})
    # Processing Detail prioritizes applied records, then preserves their order.
    # Newest first selects the final edge assignment while a preserving edge
    # cannot hide the earlier assignment that still determines the word owners.
    decisions = [
        r.to_payload()
        for r in (*reversed(records), *_natural_boundary_records(natural))
    ]
    summary: dict[str, Any] = {
        "policy_version": RELEASE_POLICY_VERSION,
        "region_count": len(regions) + len(edge_regions),
        "batch_count": len(batches),
        "changed_word_count": changed,
        "applied_region_count": sum(
            r.application.applied_region_count for r in results
        ),
        "disputed_region_count": len(disputed_records),
        "preserved_region_count": sum(
            r.application.preserved_region_count for r in results
        ),
        "possible_missing_speech_count": sum(
            r.application.possible_missing_speech_count for r in results
        ),
        "planner_usage": usage["planner"],
        "reviewer_usage": usage["reviewer"],
        "batches": batches,
        "natural_segmentation": natural.to_summary(),
        "decision_records": decisions,
        "degraded_errors": list(dict.fromkeys(errors)),
        "conditional_edge_regions": len(edge_regions),
        "remaining_edge_ranges": remaining,
        "acoustic_window_count": acoustic.get("measured_window_count", 0),
        "edge_acoustic": edge_acoustic,
    }
    payload.pop("pending", None)
    payload.pop("speaker_ownership_continuity_checkpoint", None)
    payload.update(
        {
            "policy_version": RELEASE_POLICY_VERSION,
            "changed_count": changed,
            "marked_count": summary["disputed_region_count"],
            "speaker_ownership_continuity": summary,
            "decision_records": [*payload.get("decision_records", []), *decisions],
        }
    )
    updated = replace(
        analysis,
        segments=natural.segments,
        estimated_cost_usd=analysis.estimated_cost_usd
        + float(edge_acoustic.get("estimated_cost_usd", 0.0)),
        call_seconds=(
            *analysis.call_seconds,
            (
                "ownership_edge_embeddings",
                float(edge_acoustic.get("inference_seconds", 0.0)),
            ),
        )
        if gaps
        else analysis.call_seconds,
        boundary_adjudication_json=json.dumps(payload, ensure_ascii=False),
        speaker_consolidation_json=_with_attribution_records(
            analysis.speaker_consolidation_json,
            natural.segments,
            analysis.pipeline_version,
        ),
    )
    return AdjudicationCompletion(
        analysis=updated,
        completed=True,
        dispute_count=summary["region_count"],
        changed_count=changed,
        marked_count=summary["disputed_region_count"],
        input_tokens=sum(v["input_tokens"] for v in usage.values()),
        output_tokens=sum(v["output_tokens"] for v in usage.values()),
        provider=str(execution.get("provider", "")),
        model=str(execution.get("model", "")),
        cli_version=str(execution.get("cli_version", "")),
        discourse_error=errors[0] if errors else None,
        ownership_region_count=summary["region_count"],
        ownership_changed_word_count=changed,
        ownership_disputed_region_count=summary["disputed_region_count"],
        natural_segment_count=len(natural.segments),
        natural_fallback_boundary_count=natural.fallback_boundary_count,
    )


def disputed_word_indexes(
    records: Sequence[DecisionRecord],
    original: Sequence[str],
    owners: Sequence[str],
    remaining: Sequence[tuple[int, int]],
    *,
    narrow: bool,
) -> set[int]:
    """Return the word indexes whose final ownership stays uncertain.

    V17 marks every word of an uncertain region. V21 (``narrow``) marks an
    applied region whose change contradicts acoustics only on the words the
    final Reviewer actually changed; degraded, unsure, possible-missing-speech,
    and unresolved-edge regions remain fully marked.
    """
    disputed: set[int] = set()
    for record in records:
        span = range(
            record.subject["first_word_index"], record.subject["last_word_index"] + 1
        )
        reviewed = (
            record.evidence.get("reviewer_plan") is not None
            and not record.evidence["reviewer_execution"].get("error")
            and not record.evidence.get("reviewer_validation_error")
        )
        unresolved = any(
            start <= span.stop - 1 and span.start < end for start, end in remaining
        )
        if (
            narrow
            and record.verdict == "applied-disputed"
            and reviewed
            and not unresolved
        ):
            disputed.update(i for i in span if owners[i] != original[i])
        else:
            disputed.update(span)
    disputed.update(i for start, end in remaining for i in range(start, end))
    return disputed


def complete_sentence_integrity(
    analysis: SourceAnalysisResult,
    policy: SentenceIntegrityPolicy = DEFAULT_SENTENCE_INTEGRITY_POLICY,
) -> SourceAnalysisResult:
    """Apply Sentence Integrity to a completed analysis (V23/V24), once.

    Runs after the Agent ownership layer and before Voice References, for
    new and reused analyses alike. Only the segments around moved words are
    re-segmented (Natural Segmentation over that block, punctuated by the
    segments' own text); every other segment, and its natural-boundary
    record, stays exactly as completed. Segment ids are renumbered in order.
    """
    payload = json.loads(analysis.boundary_adjudication_json or "{}")
    if SENTENCE_INTEGRITY_SUMMARY_KEY in payload:
        return analysis
    per_word: list[CanonicalSegment] = []
    segment_of_word: list[int] = []
    for index, segment in enumerate(analysis.segments):
        for word in segment.words:
            segment_of_word.append(index)
            per_word.append(
                replace(
                    segment,
                    segment_id=f"word-{len(per_word)}",
                    text=word[0],
                    start_seconds=word[1],
                    end_seconds=word[2],
                    words=(word,),
                )
            )
    result = integrate_sentences(
        [word.words[0] for word in per_word],
        tuple(word.speaker_label for word in per_word),
        analysis.full_asr_text,
        policy,
    )
    segments = analysis.segments
    consolidation_json = analysis.speaker_consolidation_json
    if result.moves:
        segments, boundaries = _resegment_moved_blocks(
            analysis,
            per_word,
            segment_of_word,
            result,
        )
        records = [record.to_payload() for record in result.records(policy)]
        natural_records = [
            record.to_payload() for record in _natural_boundary_records(boundaries)
        ]

        def rewritten(existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
            kept = [
                record
                for record in existing
                if record.get("kind") != "natural-segmentation-boundary"
            ]
            return [*kept, *records, *natural_records]

        payload["decision_records"] = rewritten(payload.get("decision_records", []))
        continuity = dict(payload.get("speaker_ownership_continuity") or {})
        if continuity:
            continuity["decision_records"] = rewritten(
                continuity.get("decision_records", [])
            )
            continuity["natural_segmentation"] = {
                **dict(continuity.get("natural_segmentation") or {}),
                **{
                    key: value
                    for key, value in boundaries.to_summary().items()
                    if key != "punctuation_alignment"
                },
            }
            payload["speaker_ownership_continuity"] = continuity
        consolidation_json = _with_attribution_records(
            analysis.speaker_consolidation_json,
            segments,
            analysis.pipeline_version,
        )
    payload[SENTENCE_INTEGRITY_SUMMARY_KEY] = result.summary(policy)
    return replace(
        analysis,
        segments=segments,
        boundary_adjudication_json=json.dumps(payload, ensure_ascii=False),
        speaker_consolidation_json=consolidation_json,
    )


def _resegment_moved_blocks(
    analysis: SourceAnalysisResult,
    per_word: list[CanonicalSegment],
    segment_of_word: list[int],
    result: SentenceIntegrityResult,
) -> tuple[tuple[CanonicalSegment, ...], NaturalSegmentationResult]:
    """Re-segment the blocks of segments around moved words.

    A block is every segment holding a moved word or its sentence, plus one
    neighbour on each side so a moved word can join an adjacent segment of
    its new owner. Returns the renumbered segments and the episode's
    natural boundaries (kept outside the blocks, recomputed inside).
    """
    affected: set[int] = set()
    for move in result.moves:
        first = segment_of_word[
            min(move.first_word_index, move.sentence_first_word_index)
        ]
        last = segment_of_word[
            max(move.last_word_index, move.sentence_last_word_index)
        ]
        affected.update(
            range(max(0, first - 1), min(len(analysis.segments), last + 2))
        )
    blocks: list[list[int]] = []
    for index in sorted(affected):
        if blocks and index == blocks[-1][-1] + 1:
            blocks[-1].append(index)
        else:
            blocks.append([index])
    first_word = [0]
    for segment in analysis.segments:
        first_word.append(first_word[-1] + len(segment.words))
    old = _recorded_natural_boundaries(analysis)
    rebuilt: dict[int, tuple[CanonicalSegment, ...]] = {}
    boundaries: list[NaturalBoundary] = []
    inside: list[tuple[int, int]] = []
    for block in blocks:
        start, end = first_word[block[0]], first_word[block[-1] + 1]
        inside.append((start, end))
        local = segment_naturally(
            [
                replace(word, speaker_label=result.owners[start + offset])
                for offset, word in enumerate(per_word[start:end])
            ],
            full_asr_text=" ".join(analysis.segments[i].text for i in block),
            source_event_ranges=tuple((s, e) for _, s, e in analysis.source_events),
        )
        rebuilt[block[0]] = local.segments
        boundaries.extend(
            replace(
                boundary,
                after_word_index=boundary.after_word_index + start,
                before_word_index=boundary.before_word_index + start,
            )
            for boundary in local.boundaries
        )
    boundaries.extend(
        boundary
        for boundary in old
        if not any(
            start <= boundary.after_word_index < end - 1 for start, end in inside
        )
    )
    boundaries.sort(key=lambda boundary: boundary.after_word_index)
    skipped = {index for block in blocks for index in block[1:]}
    ordered: list[CanonicalSegment] = []
    for index, segment in enumerate(analysis.segments):
        if index in rebuilt:
            ordered.extend(rebuilt[index])
        elif index not in skipped:
            ordered.append(segment)
    # Natural Segmentation numbers segments from one.
    renumbered = tuple(
        replace(segment, segment_id=f"seg-{index:04d}")
        for index, segment in enumerate(ordered, start=1)
    )
    natural = NaturalSegmentationResult(
        segments=renumbered,
        policy_version=DEFAULT_NATURAL_SEGMENTATION_POLICY.version,
        punctuation_alignment={},
        boundaries=tuple(boundaries),
        speaker_boundary_count=sum(b.kind == "speaker" for b in boundaries),
        source_event_boundary_count=sum(b.kind == "source_event" for b in boundaries),
        sentence_boundary_count=sum(b.kind == "sentence" for b in boundaries),
        pause_boundary_count=sum(b.kind == "pause" for b in boundaries),
        fallback_boundary_count=sum(b.kind == "fallback" for b in boundaries),
    )
    return renumbered, natural


def _recorded_natural_boundaries(
    analysis: SourceAnalysisResult,
) -> list[NaturalBoundary]:
    payload = json.loads(analysis.boundary_adjudication_json or "{}")
    continuity = payload.get("speaker_ownership_continuity") or {}
    summary = continuity.get("natural_segmentation") or {}
    return [
        NaturalBoundary(
            kind=item["kind"],
            after_word_index=int(item["after_word_index"]),
            before_word_index=int(item["before_word_index"]),
            boundary_seconds=float(item["boundary_seconds"]),
        )
        for item in summary.get("boundaries", [])
    ]


def has_sentence_integrity(analysis: SourceAnalysisResult) -> bool:
    payload = json.loads(analysis.boundary_adjudication_json or "{}")
    return SENTENCE_INTEGRITY_SUMMARY_KEY in payload
