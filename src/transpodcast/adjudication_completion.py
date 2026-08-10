"""Completes Boundary Ownership Adjudication at the worker layer.

Source Analysis is purely acoustic: it emits the detected ownership
disputes and their fragment-voiceprint measurements as a pending
payload. This module asks the discourse judge one batched question
through the Translation Adapter, runs the fixed verdict engine, applies
reassignments to the final Canonical Transcript segments, and rewrites
the attribution ledger so it always describes what the Owner will hear.

A discourse failure can only produce abstentions: ownership is kept and
the segment is marked disputed. It never fails the analysis stage — the
GPU work must not be re-billed because a text question failed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from transpodcast.adjudication_judges import (
    MappedDiscourseJudge,
    MappedVoiceprintJudge,
)
from transpodcast.boundary_adjudication import (
    ADJUDICATION_POLICY_VERSION,
    LEGACY_ADJUDICATION_POLICY_VERSION,
    AdjudicationOutcome,
    DiscourseAdjudicationResult,
    OwnershipDispute,
    VoiceprintVerdict,
    adjudicate_ownership,
)
from transpodcast.decision_records import DecisionRecord
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.pipeline_versions import (
    SPEECH_PIPELINE_V11,
    SPEECH_PIPELINE_V12,
    SPEECH_PIPELINE_V13,
    SPEECH_PIPELINE_V14,
)
from transpodcast.source_segmentation import (
    group_assigned_words,
    join_words,
)
from transpodcast.speaker_boundary_adjudication import (
    UNIFIED_BOUNDARY_POLICY_VERSION,
    BoundaryPlanningResult,
    BoundaryProposal,
    BoundaryReviewResult,
    UnifiedBoundaryApplication,
    UnifiedBoundaryCandidate,
    apply_unified_boundary_decisions,
    build_unified_boundary_candidates,
)
from transpodcast.speaker_tail_boundary_repair import (
    ATOMIC_SPEAKER_TAIL_REPAIR_POLICY_VERSION,
    LEGACY_MAXIMUM_CANDIDATE_RATIO,
    LEGACY_MAXIMUM_REPAIR_COUNT,
    LEGACY_SPEAKER_TAIL_REPAIR_POLICY_VERSION,
    SPEAKER_TAIL_REPAIR_POLICY_VERSION,
    SpeakerTailCandidateSelection,
    SpeakerTailRepairApplication,
    SpeakerTailRepairCandidate,
    SpeakerTailRepairResult,
    apply_speaker_tail_repair_verdicts,
    find_speaker_tail_repair_candidates,
    preserve_speaker_tail_repair_candidates,
)


class DiscourseAdjudicator(Protocol):
    def adjudicate_discourse(
        self,
        disputes: Sequence[OwnershipDispute],
    ) -> DiscourseAdjudicationResult: ...

    def adjudicate_speaker_tail_repairs(
        self,
        candidates: Sequence[SpeakerTailRepairCandidate],
    ) -> SpeakerTailRepairResult: ...

    def plan_speaker_boundaries(
        self,
        candidates: Sequence[UnifiedBoundaryCandidate],
    ) -> BoundaryPlanningResult: ...

    def review_speaker_boundaries(
        self,
        candidates: Sequence[UnifiedBoundaryCandidate],
        proposals: dict[str, BoundaryProposal | None],
    ) -> BoundaryReviewResult: ...


@dataclass(frozen=True)
class _TailRepairPipelinePolicy:
    policy_version: str
    maximum_candidate_ratio: float | None
    maximum_repair_count: int | None


_TAIL_REPAIR_PIPELINE_POLICIES = {
    SPEECH_PIPELINE_V11: _TailRepairPipelinePolicy(
        policy_version=LEGACY_SPEAKER_TAIL_REPAIR_POLICY_VERSION,
        maximum_candidate_ratio=LEGACY_MAXIMUM_CANDIDATE_RATIO,
        maximum_repair_count=LEGACY_MAXIMUM_REPAIR_COUNT,
    ),
    SPEECH_PIPELINE_V12: _TailRepairPipelinePolicy(
        policy_version=SPEAKER_TAIL_REPAIR_POLICY_VERSION,
        maximum_candidate_ratio=None,
        maximum_repair_count=None,
    ),
    SPEECH_PIPELINE_V13: _TailRepairPipelinePolicy(
        policy_version=ATOMIC_SPEAKER_TAIL_REPAIR_POLICY_VERSION,
        maximum_candidate_ratio=None,
        maximum_repair_count=None,
    ),
}


@dataclass(frozen=True)
class AdjudicationCompletion:
    analysis: SourceAnalysisResult
    completed: bool = False
    dispute_count: int = 0
    changed_count: int = 0
    marked_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    provider: str = ""
    model: str = ""
    cli_version: str = ""
    discourse_error: str | None = None
    tail_repair_candidate_count: int = 0
    tail_repair_proposed_count: int = 0
    tail_repair_count: int = 0
    tail_repair_locally_preserved_component_count: int = 0
    tail_repair_locally_degraded_component_count: int = 0
    tail_repair_conflict_component_count: int = 0
    tail_repair_provider_degraded_component_count: int = 0
    tail_repair_error: str | None = None
    boundary_candidate_count: int = 0
    boundary_planner_proposed_count: int = 0
    boundary_reviewer_approved_count: int = 0
    boundary_split_count: int = 0
    boundary_reassign_count: int = 0
    boundary_locally_preserved_component_count: int = 0
    boundary_conflict_component_count: int = 0
    boundary_provider_degraded_component_count: int = 0


def complete_boundary_adjudication(
    analysis: SourceAnalysisResult,
    adjudicator: DiscourseAdjudicator,
) -> AdjudicationCompletion:
    if analysis.pipeline_version == SPEECH_PIPELINE_V14:
        return _complete_unified_boundary_adjudication(analysis, adjudicator)
    payload = json.loads(analysis.boundary_adjudication_json or "{}")
    prior_decision_records = list(payload.get("decision_records") or [])
    policy_version = str(payload.get("policy_version") or ADJUDICATION_POLICY_VERSION)
    pending = payload.pop("pending", None)
    tail_repair_policy = _tail_repair_pipeline_policy(analysis.pipeline_version)
    tail_repair_enabled = tail_repair_policy is not None
    if pending is None and (
        not tail_repair_enabled or "speaker_tail_boundary_repair" in payload
    ):
        return AdjudicationCompletion(analysis=analysis)
    if not tail_repair_enabled and not (pending or {}).get("disputes"):
        return AdjudicationCompletion(analysis=analysis)
    payload.pop("discourse_error", None)
    disputes = [
        replace(
            OwnershipDispute.from_payload(item),
            question_policy_version=policy_version,
        )
        for item in ((pending or {}).get("disputes") or [])
    ]
    voiceprints = {
        dispute_id: (
            VoiceprintVerdict.from_payload(verdict) if verdict is not None else None
        )
        for dispute_id, verdict in (pending or {}).get("voiceprints", {}).items()
    }
    answer = (
        adjudicator.adjudicate_discourse(disputes)
        if disputes
        else DiscourseAdjudicationResult(verdicts={})
    )
    if (
        disputes
        and answer.error is None
        and any(answer.verdicts.get(dispute.dispute_id) is None for dispute in disputes)
    ):
        # Explicit "unsure" answers are represented by a verdict whose
        # owner is None. A missing mapping is different: the provider did
        # not fulfill the batched contract and the run must show that
        # degradation instead of pretending the response was complete.
        answer = replace(
            answer,
            error="Provider answered but returned no verdicts.",
        )
    outcomes = (
        adjudicate_ownership(
            disputes,
            voiceprint_judge=MappedVoiceprintJudge(voiceprints),
            discourse_judge=MappedDiscourseJudge(answer.verdicts),
            policy_version=policy_version,
        )
        if disputes
        else []
    )
    disagreement_count = sum(
        outcome.record.verdict in {"discourse-preferred", "voiceprint-preferred"}
        for outcome in outcomes
    )
    abstention_count = sum(
        outcome.record.evidence.get("voiceprint_scores") is None
        or outcome.record.evidence.get("discourse_owner") is None
        for outcome in outcomes
    )
    segments, marked = _apply_outcomes(
        analysis.segments,
        disputes,
        outcomes,
        policy_version=policy_version,
    )
    changed = sum(1 for outcome in outcomes if outcome.changed)
    selection, repair_answer, repair_application = _prepare_speaker_tail_repairs(
        replace(analysis, segments=segments),
        adjudicator,
    )
    ownership_records = [outcome.record.to_payload() for outcome in outcomes]
    repair_error = selection.guard_reason or repair_application.error
    tail_repair_policy_version = (
        tail_repair_policy.policy_version
        if tail_repair_policy is not None
        else LEGACY_SPEAKER_TAIL_REPAIR_POLICY_VERSION
    )
    combined_records = [
        *(ownership_records if disputes else prior_decision_records),
        *(record.to_payload() for record in repair_application.records),
    ]
    total_input_tokens = answer.input_tokens + repair_answer.input_tokens
    total_output_tokens = answer.output_tokens + repair_answer.output_tokens
    discourse_error = answer.error
    tail_repair_summary: dict[str, Any] = {
        "policy_version": tail_repair_policy_version,
        "detected_count": selection.detected_count,
        "candidate_count": len(selection.candidates),
        "repair_count": repair_application.repair_count,
        "marked_count": repair_application.marked_count,
        "guard_rejected": selection.guard_rejected,
        "decision_records": [
            record.to_payload() for record in repair_application.records
        ],
        "discourse_usage": {
            "input_tokens": repair_answer.input_tokens,
            "output_tokens": repair_answer.output_tokens,
        },
        "discourse_execution": {
            "provider": repair_answer.provider,
            "model": repair_answer.model,
            "cli_version": repair_answer.cli_version,
        },
    }
    if tail_repair_policy_version == ATOMIC_SPEAKER_TAIL_REPAIR_POLICY_VERSION:
        tail_repair_summary.update(
            {
                "proposed_repair_count": (repair_application.proposed_repair_count),
                "locally_preserved_component_count": (
                    repair_application.locally_preserved_component_count
                ),
                "locally_degraded_component_count": (
                    repair_application.locally_degraded_component_count
                ),
                "conflict_component_count": (
                    repair_application.conflict_component_count
                ),
                "provider_degraded_component_count": (
                    repair_application.provider_degraded_component_count
                ),
            }
        )
    payload.update(
        {
            "policy_version": policy_version,
            "changed_count": (
                changed if disputes else int(payload.get("changed_count", 0))
            ),
            "marked_count": (
                (marked if disputes else int(payload.get("marked_count", 0)))
                + repair_application.marked_count
            ),
            "disagreement_count": (
                disagreement_count
                if disputes
                else int(payload.get("disagreement_count", 0))
            ),
            "abstention_count": (
                abstention_count
                if disputes
                else int(payload.get("abstention_count", 0))
            ),
            "decision_records": combined_records,
            "discourse_usage": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
            "discourse_execution": {
                "provider": answer.provider,
                "model": answer.model,
                "cli_version": answer.cli_version,
            },
            "speaker_tail_boundary_repair": tail_repair_summary,
        }
    )
    if discourse_error is not None:
        payload["discourse_error"] = discourse_error
    if repair_error is not None:
        payload["speaker_tail_boundary_repair"]["error"] = repair_error
    updated = replace(
        analysis,
        segments=repair_application.segments,
        boundary_adjudication_json=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
        speaker_consolidation_json=_with_attribution_records(
            analysis.speaker_consolidation_json,
            repair_application.segments,
            analysis.pipeline_version,
        ),
    )
    return AdjudicationCompletion(
        analysis=updated,
        completed=bool(disputes or selection.detected_count),
        dispute_count=len(disputes),
        changed_count=changed,
        marked_count=marked + repair_application.marked_count,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        provider=answer.provider or repair_answer.provider,
        model=answer.model or repair_answer.model,
        cli_version=answer.cli_version or repair_answer.cli_version,
        discourse_error=discourse_error,
        tail_repair_candidate_count=len(selection.candidates),
        tail_repair_proposed_count=(repair_application.proposed_repair_count),
        tail_repair_count=repair_application.repair_count,
        tail_repair_locally_preserved_component_count=(
            repair_application.locally_preserved_component_count
        ),
        tail_repair_locally_degraded_component_count=(
            repair_application.locally_degraded_component_count
        ),
        tail_repair_conflict_component_count=(
            repair_application.conflict_component_count
        ),
        tail_repair_provider_degraded_component_count=(
            repair_application.provider_degraded_component_count
        ),
        tail_repair_error=repair_error,
    )


def _complete_unified_boundary_adjudication(
    analysis: SourceAnalysisResult,
    adjudicator: DiscourseAdjudicator,
) -> AdjudicationCompletion:
    payload = json.loads(analysis.boundary_adjudication_json or "{}")
    if "unified_boundary_adjudication" in payload:
        return AdjudicationCompletion(analysis=analysis)
    pending = payload.pop("pending", None) or {}
    raw_disputes = pending.get("disputes") or []
    disputes: list[OwnershipDispute] = []
    candidates: tuple[UnifiedBoundaryCandidate, ...] = ()
    planning = BoundaryPlanningResult(proposals={})
    review = BoundaryReviewResult(verdicts={})
    try:
        disputes = [OwnershipDispute.from_payload(item) for item in raw_disputes]
        voiceprints = {
            dispute_id: (
                VoiceprintVerdict.from_payload(verdict) if verdict is not None else None
            )
            for dispute_id, verdict in (pending.get("voiceprints") or {}).items()
        }
        consolidation = json.loads(analysis.speaker_consolidation_json or "{}")
        speaker_mapping = {
            str(source): str(target)
            for source, target in (consolidation.get("speaker_mapping") or {}).items()
        }
        tail_selection = find_speaker_tail_repair_candidates(
            analysis.segments,
            analysis.raw_diarization_turns,
            speaker_mapping,
            maximum_candidate_ratio=None,
        )
        candidates = build_unified_boundary_candidates(
            analysis.segments,
            disputes,
            voiceprints,
            tail_selection.candidates,
            analysis.raw_diarization_turns,
            speaker_mapping,
        )
    except Exception as error:
        planning = BoundaryPlanningResult(
            proposals={},
            error=_boundary_internal_error(error),
        )
    if candidates and planning.error is None:
        try:
            planning = adjudicator.plan_speaker_boundaries(candidates)
        except Exception as error:
            planning = BoundaryPlanningResult(
                proposals={},
                error=_boundary_internal_error(error),
            )
        if planning.error is None:
            try:
                review = adjudicator.review_speaker_boundaries(
                    candidates,
                    planning.proposals,
                )
            except Exception as error:
                review = BoundaryReviewResult(
                    verdicts={},
                    error=_boundary_internal_error(error),
                )
    try:
        application = apply_unified_boundary_decisions(
            analysis.segments,
            candidates,
            planning,
            review,
        )
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"[:500]
        planning = replace(
            planning,
            proposals={candidate.candidate_id: None for candidate in candidates},
            error=reason,
        )
        application = apply_unified_boundary_decisions(
            analysis.segments,
            candidates,
            planning,
            review,
        )

    prior_records = list(payload.get("decision_records") or [])
    total_input_tokens = planning.input_tokens + review.input_tokens
    total_output_tokens = planning.output_tokens + review.output_tokens
    summary = _unified_boundary_summary(
        candidates,
        planning,
        review,
        application,
    )
    payload.update(
        {
            "policy_version": UNIFIED_BOUNDARY_POLICY_VERSION,
            "changed_count": (
                application.split_to_next_count + application.reassign_whole_count
            ),
            "marked_count": (
                application.split_to_next_count + application.reassign_whole_count
            ),
            "decision_records": [
                *prior_records,
                *(record.to_payload() for record in application.records),
            ],
            "discourse_usage": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
            "discourse_execution": {
                "provider": planning.provider or review.provider,
                "model": planning.model or review.model,
                "cli_version": planning.cli_version or review.cli_version,
            },
            "unified_boundary_adjudication": summary,
        }
    )
    if application.error is not None:
        payload["unified_boundary_adjudication"]["error"] = application.error
    updated = replace(
        analysis,
        segments=application.segments,
        boundary_adjudication_json=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
        speaker_consolidation_json=_with_attribution_records(
            analysis.speaker_consolidation_json,
            application.segments,
            analysis.pipeline_version,
        ),
    )
    changed_count = application.split_to_next_count + application.reassign_whole_count
    return AdjudicationCompletion(
        analysis=updated,
        completed=bool(candidates),
        dispute_count=len(disputes) if disputes else len(raw_disputes),
        changed_count=changed_count,
        marked_count=changed_count,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        provider=planning.provider or review.provider,
        model=planning.model or review.model,
        cli_version=planning.cli_version or review.cli_version,
        discourse_error=application.error,
        boundary_candidate_count=len(candidates),
        boundary_planner_proposed_count=application.planner_proposed_count,
        boundary_reviewer_approved_count=application.reviewer_approved_count,
        boundary_split_count=application.split_to_next_count,
        boundary_reassign_count=application.reassign_whole_count,
        boundary_locally_preserved_component_count=(
            application.locally_preserved_component_count
        ),
        boundary_conflict_component_count=(application.conflict_component_count),
        boundary_provider_degraded_component_count=(
            application.provider_degraded_component_count
        ),
    )


def _boundary_internal_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]


def _unified_boundary_summary(
    candidates: Sequence[UnifiedBoundaryCandidate],
    planning: BoundaryPlanningResult,
    review: BoundaryReviewResult,
    application: UnifiedBoundaryApplication,
) -> dict[str, Any]:
    return {
        "policy_version": UNIFIED_BOUNDARY_POLICY_VERSION,
        "candidate_count": len(candidates),
        "planner_proposed_count": application.planner_proposed_count,
        "reviewer_approved_count": application.reviewer_approved_count,
        "split_to_next_count": application.split_to_next_count,
        "reassign_whole_count": application.reassign_whole_count,
        "locally_preserved_component_count": (
            application.locally_preserved_component_count
        ),
        "conflict_component_count": application.conflict_component_count,
        "provider_degraded_component_count": (
            application.provider_degraded_component_count
        ),
        "decision_records": [record.to_payload() for record in application.records],
        "planner_usage": {
            "input_tokens": planning.input_tokens,
            "output_tokens": planning.output_tokens,
        },
        "reviewer_usage": {
            "input_tokens": review.input_tokens,
            "output_tokens": review.output_tokens,
        },
        "planner_execution": {
            "provider": planning.provider,
            "model": planning.model,
            "cli_version": planning.cli_version,
        },
        "reviewer_execution": {
            "provider": review.provider,
            "model": review.model,
            "cli_version": review.cli_version,
        },
    }


def _speaker_tail_repair_candidates(
    analysis: SourceAnalysisResult,
) -> SpeakerTailCandidateSelection:
    policy = _tail_repair_pipeline_policy(analysis.pipeline_version)
    if policy is None:
        return find_speaker_tail_repair_candidates((), (), {})
    consolidation = json.loads(analysis.speaker_consolidation_json or "{}")
    mapping = {
        str(source): str(target)
        for source, target in (consolidation.get("speaker_mapping") or {}).items()
    }
    return find_speaker_tail_repair_candidates(
        analysis.segments,
        analysis.raw_diarization_turns,
        mapping,
        maximum_candidate_ratio=policy.maximum_candidate_ratio,
    )


def _prepare_speaker_tail_repairs(
    analysis: SourceAnalysisResult,
    adjudicator: DiscourseAdjudicator,
) -> tuple[
    SpeakerTailCandidateSelection,
    SpeakerTailRepairResult,
    SpeakerTailRepairApplication,
]:
    selection = SpeakerTailCandidateSelection(candidates=(), detected_count=0)
    answer = SpeakerTailRepairResult(verdicts={})
    policy = _tail_repair_pipeline_policy(analysis.pipeline_version)
    policy_version = (
        policy.policy_version
        if policy is not None
        else LEGACY_SPEAKER_TAIL_REPAIR_POLICY_VERSION
    )
    try:
        selection = _speaker_tail_repair_candidates(analysis)
        selection = replace(
            selection,
            candidates=tuple(
                replace(
                    candidate,
                    question_policy_version=policy_version,
                )
                for candidate in selection.candidates
            ),
            rejected_candidates=tuple(
                replace(
                    candidate,
                    question_policy_version=policy_version,
                )
                for candidate in selection.rejected_candidates
            ),
        )
        if selection.guard_rejected:
            reason = selection.guard_reason or (
                "Speaker tail repair candidate population was rejected."
            )
            application = preserve_speaker_tail_repair_candidates(
                analysis.segments,
                selection.rejected_candidates,
                reason,
                result=answer,
                policy_version=policy_version,
            )
            return selection, answer, application
        if selection.candidates:
            answer = adjudicator.adjudicate_speaker_tail_repairs(selection.candidates)
        application = apply_speaker_tail_repair_verdicts(
            analysis.segments,
            selection.candidates,
            answer,
            maximum_repair_count=(
                policy.maximum_repair_count if policy is not None else None
            ),
            policy_version=policy_version,
        )
    except Exception as error:
        reason = _tail_repair_internal_error(error)
        answer = replace(answer, verdicts={}, error=reason)
        application = preserve_speaker_tail_repair_candidates(
            analysis.segments,
            (*selection.candidates, *selection.rejected_candidates),
            reason,
            result=answer,
            policy_version=policy_version,
        )
    return selection, answer, application


def _tail_repair_internal_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]


def _tail_repair_pipeline_policy(
    pipeline_version: str,
) -> _TailRepairPipelinePolicy | None:
    return _TAIL_REPAIR_PIPELINE_POLICIES.get(pipeline_version)


def supports_speaker_tail_repair(pipeline_version: str) -> bool:
    return _tail_repair_pipeline_policy(pipeline_version) is not None


def _apply_outcomes(
    segments: tuple[CanonicalSegment, ...],
    disputes: list[OwnershipDispute],
    outcomes: list[AdjudicationOutcome],
    *,
    policy_version: str = ADJUDICATION_POLICY_VERSION,
) -> tuple[tuple[CanonicalSegment, ...], int]:
    items = list(segments)
    by_id = {dispute.dispute_id: dispute for dispute in disputes}
    segment_indexes = {
        dispute.dispute_id: _dispute_segment_index(items, dispute)
        for dispute in disputes
    }
    for outcome in outcomes:
        if not outcome.changed:
            continue
        dispute = by_id[outcome.dispute_id]
        index = segment_indexes[outcome.dispute_id]
        items[index] = replace(
            items[index],
            speaker_label=outcome.final_owner,
        )
    if policy_version == LEGACY_ADJUDICATION_POLICY_VERSION and any(
        outcome.changed for outcome in outcomes
    ):
        items = list(_resegment_with_v6_rules(items))
    marked = 0
    for outcome in outcomes:
        if not outcome.disputed_marker:
            continue
        dispute = by_id[outcome.dispute_id]
        expected_words = {_word_signature(word) for word in dispute.words}
        matches = [
            position
            for position, segment in enumerate(items)
            if segment.speaker_label == outcome.final_owner
            and _segment_word_signatures(segment) & expected_words
        ]
        covered_words = set().union(
            *(
                _segment_word_signatures(items[position]) & expected_words
                for position in matches
            )
        )
        if covered_words != expected_words:
            raise ValueError(
                f"Ownership dispute {dispute.dispute_id} cannot be marked "
                "in the Canonical Transcript"
            )
        for position in matches:
            items[position] = replace(
                items[position],
                review_state="disputed",
            )
        marked += 1
    return tuple(items), marked


def _dispute_segment_index(
    segments: list[CanonicalSegment],
    dispute: OwnershipDispute,
) -> int:
    matches = [
        index
        for index, segment in enumerate(segments)
        if segment.speaker_label == dispute.current_owner
        and abs(segment.start_seconds - dispute.start_seconds) < 1e-6
        and abs(segment.end_seconds - dispute.end_seconds) < 1e-6
        and _segment_contains_dispute(segment, dispute)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Ownership dispute {dispute.dispute_id} does not match "
            "a Canonical Transcript segment"
        )
    return matches[0]


def _segment_contains_dispute(
    segment: CanonicalSegment,
    dispute: OwnershipDispute,
) -> bool:
    segment_words = tuple(
        (str(text), float(start), float(end)) for text, start, end in segment.words
    )
    disputed_words = tuple(
        (
            str(word["text"]),
            float(word["start_seconds"]),
            float(word["end_seconds"]),
        )
        for word in dispute.words
    )
    width = len(disputed_words)
    return any(
        all(
            actual[0] == expected[0]
            and abs(actual[1] - expected[1]) < 1e-6
            and abs(actual[2] - expected[2]) < 1e-6
            for actual, expected in zip(
                segment_words[start : start + width],
                disputed_words,
                strict=True,
            )
        )
        for start in range(len(segment_words) - width + 1)
    )


def _segment_word_signatures(
    segment: CanonicalSegment,
) -> set[tuple[str, float, float]]:
    return {(str(text), float(start), float(end)) for text, start, end in segment.words}


def _word_signature(word: dict[str, Any]) -> tuple[str, float, float]:
    return (
        str(word["text"]),
        float(word["start_seconds"]),
        float(word["end_seconds"]),
    )


def _resegment_with_v6_rules(
    segments: list[CanonicalSegment],
) -> tuple[CanonicalSegment, ...]:
    """Preserve exact V9 checkpoint semantics for unfinished V2 runs."""
    assigned: list[dict[str, Any]] = []
    for segment in segments:
        words = segment.words or (
            (segment.text, segment.start_seconds, segment.end_seconds),
        )
        for text, start_seconds, end_seconds in words:
            assigned.append(
                {
                    "text": text,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "speaker": segment.speaker_label,
                    "overlap": segment.overlap,
                    "events": segment.events,
                    "confidence": segment.confidence,
                    "review_state": segment.review_state,
                }
            )
    groups = group_assigned_words(assigned)
    rebuilt = []
    for index, group in enumerate(groups, start=1):
        events = tuple(
            dict.fromkeys(event for word in group for event in tuple(word["events"]))
        )
        review_states = [
            str(word["review_state"])
            for word in group
            if str(word["review_state"]) != "accepted"
        ]
        rebuilt.append(
            CanonicalSegment(
                segment_id=f"seg-{index:04d}",
                speaker_label=str(group[0]["speaker"]),
                start_seconds=float(group[0]["start_seconds"]),
                end_seconds=float(group[-1]["end_seconds"]),
                text=join_words(group),
                words=tuple(
                    (
                        str(word["text"]),
                        float(word["start_seconds"]),
                        float(word["end_seconds"]),
                    )
                    for word in group
                ),
                overlap=any(bool(word["overlap"]) for word in group),
                events=events,
                confidence=min(float(word["confidence"]) for word in group),
                review_state=(review_states[0] if review_states else "accepted"),
                omitted_as_sponsor=False,
            )
        )
    return tuple(rebuilt)


def _with_attribution_records(
    consolidation_json: str,
    segments: tuple[CanonicalSegment, ...],
    pipeline_version: str,
) -> str:
    consolidation = json.loads(consolidation_json or "{}")
    if not consolidation:
        return consolidation_json
    kept = [
        record
        for record in consolidation.get("decision_records") or []
        if record.get("kind") != "segment-attribution"
    ]
    consolidation["decision_records"] = [
        *kept,
        *(
            DecisionRecord(
                kind="segment-attribution",
                policy_version=pipeline_version,
                subject={
                    "segment_index": index,
                    "speaker": segment.speaker_label,
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds,
                },
                evidence={
                    "word_count": len(segment.words),
                    "overlap": segment.overlap,
                    "review_state": segment.review_state,
                },
                verdict="attributed",
                rationale=(
                    "Final word ownership after consolidation and adjudication."
                ),
            ).to_payload()
            for index, segment in enumerate(segments)
        ),
    ]
    return json.dumps(
        consolidation,
        ensure_ascii=False,
        sort_keys=True,
    )
