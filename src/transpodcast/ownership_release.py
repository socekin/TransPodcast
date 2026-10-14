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
from transpodcast.natural_segmentation import segment_naturally
from transpodcast.ownership_acoustic_coverage import find_ownership_edge_gaps
from transpodcast.ownership_continuity_completion import (
    ContinuityCompletion,
    complete_continuity_ownership,
    overlay_reviewed_ownership,
    question_fingerprint,
)
from transpodcast.pipeline_versions import DETERMINISTIC_OWNERSHIP_PIPELINES
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
