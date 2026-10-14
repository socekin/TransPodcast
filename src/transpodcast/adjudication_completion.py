"""Completes Speaker ownership adjudication at the worker layer.

Source Analysis is purely acoustic: it emits the detected ownership
disputes and their fragment-voiceprint measurements as a pending
payload. For word-ownership analyses this module asks the Planner and
Reviewer ownership questions through the Translation Adapter, applies the
validated plans before natural segmentation, and rewrites the attribution
ledger so it always describes what the Owner will hear. V17 continuity
analyses are completed by the released ownership policy.

A provider failure only degrades the affected batch: ownership is kept and
the failure is recorded. It never fails the analysis stage, so the GPU work
is not re-billed because a text question failed. The exceptions are a V17
Agent rate limit or lost login, which say nothing about the batch: they stop
the stage for the Retry Policy or the Owner, and the acoustic checkpoint and
completed batches are reused on resume.

Historical V9-V14 analyses are immutable stored data. No new Processing Run
can reach their worker paths, so completion leaves every other analysis
unchanged; Processing Detail and the Evaluation Report still render their
stored Decision Records.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from transpodcast.boundary_adjudication import (
    OwnershipDispute,
    VoiceprintVerdict,
)
from transpodcast.decision_records import DecisionRecord
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.natural_segmentation import (
    NaturalSegmentationResult,
    segment_naturally,
)
from transpodcast.pipeline_versions import (
    CONTINUITY_PIPELINES,
    SPEECH_PIPELINE_V15,
)
from transpodcast.speaker_ownership_v15 import (
    MAXIMUM_BATCH_CHARACTERS,
    SPEAKER_OWNERSHIP_POLICY_VERSION,
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
    apply_speaker_ownership_plans,
    batch_speaker_ownership_regions,
    build_speaker_ownership_regions,
    ownership_request_character_count,
    parse_ownership_plans,
)


class DiscourseAdjudicator(Protocol):
    def plan_speaker_ownership(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult: ...

    def review_speaker_ownership(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult: ...


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
    ownership_region_count: int = 0
    ownership_changed_word_count: int = 0
    ownership_disputed_region_count: int = 0
    natural_segment_count: int = 0
    natural_fallback_boundary_count: int = 0


def complete_boundary_adjudication(
    analysis: SourceAnalysisResult,
    adjudicator: DiscourseAdjudicator,
    checkpoint_writer: Callable[[SourceAnalysisResult], None] | None = None,
    *,
    measure_edges: Callable[[list[tuple[int, int]]], dict[str, Any]] | None = None,
) -> AdjudicationCompletion:
    if analysis.pipeline_version in CONTINUITY_PIPELINES:
        from transpodcast.ownership_release import complete_released_ownership

        return complete_released_ownership(
            analysis, adjudicator, checkpoint_writer=checkpoint_writer,
            measure_edges=measure_edges,
        )
    if analysis.pipeline_version == SPEECH_PIPELINE_V15:
        return _complete_speaker_ownership_v15(
            analysis,
            adjudicator,
            checkpoint_writer=checkpoint_writer,
        )
    return AdjudicationCompletion(analysis=analysis)


def _complete_speaker_ownership_v15(
    analysis: SourceAnalysisResult,
    adjudicator: DiscourseAdjudicator,
    *,
    checkpoint_writer: Callable[[SourceAnalysisResult], None] | None = None,
) -> AdjudicationCompletion:
    payload = json.loads(analysis.boundary_adjudication_json or "{}")
    if "speaker_ownership_v15" in payload:
        return AdjudicationCompletion(analysis=analysis)
    pending = payload.get("pending") or {}
    boundary_disputes: list[OwnershipDispute] = []
    voiceprints: dict[str, VoiceprintVerdict | None] = {}
    evidence_errors: list[str] = []
    for raw_dispute in pending.get("disputes") or []:
        try:
            boundary_disputes.append(
                OwnershipDispute.from_payload(dict(raw_dispute))
            )
        except Exception as error:
            evidence_errors.append(_boundary_internal_error(error))
    for dispute_id, raw_voiceprint in (
        pending.get("voiceprints") or {}
    ).items():
        try:
            voiceprints[str(dispute_id)] = (
                VoiceprintVerdict.from_payload(dict(raw_voiceprint))
                if raw_voiceprint is not None
                else None
            )
        except Exception as error:
            evidence_errors.append(_boundary_internal_error(error))
    consolidation = json.loads(analysis.speaker_consolidation_json or "{}")
    speaker_mapping = {
        str(source): str(target)
        for source, target in (consolidation.get("speaker_mapping") or {}).items()
    }
    regions = build_speaker_ownership_regions(
        analysis.segments,
        analysis.raw_diarization_turns,
        speaker_mapping,
        analysis.full_asr_text,
        boundary_disputes,
        voiceprints,
    )
    oversized_regions = tuple(
        region
        for region in regions
        if ownership_request_character_count((region,))
        > MAXIMUM_BATCH_CHARACTERS
    )
    oversized_region_ids = {region.region_id for region in oversized_regions}
    batches = batch_speaker_ownership_regions(
        tuple(
            region
            for region in regions
            if region.region_id not in oversized_region_ids
        )
    )
    planning_plans: dict[str, Any] = {}
    review_plans: dict[str, Any] = {}
    oversized_error = (
        "Speaker Ownership Risk Region exceeds the request envelope and was "
        "locally preserved"
    )
    planning_errors: dict[str, str] = {
        region.region_id: oversized_error for region in oversized_regions
    }
    review_errors: dict[str, str] = {
        region.region_id: oversized_error for region in oversized_regions
    }
    raw_plans: dict[str, tuple[dict[str, Any], ...]] = {}
    raw_reviews: dict[str, tuple[dict[str, Any], ...]] = {}
    planning_input_tokens = 0
    planning_output_tokens = 0
    review_input_tokens = 0
    review_output_tokens = 0
    planning_provider = ""
    planning_model = ""
    planning_cli_version = ""
    review_provider = ""
    review_model = ""
    review_cli_version = ""
    batch_records: list[dict[str, Any]] = []
    degraded_errors: list[str] = [
        *evidence_errors,
        *([oversized_error] if oversized_regions else []),
    ]
    checkpoint_payload = payload.get("speaker_ownership_v15_checkpoint")
    completed_checkpoints = {
        str(item.get("fingerprint")): item
        for item in (
            checkpoint_payload.get("completed_batches", [])
            if isinstance(checkpoint_payload, dict)
            else []
        )
        if isinstance(item, dict) and item.get("fingerprint")
    }
    for batch_index, batch in enumerate(batches, start=1):
        fingerprint = _ownership_batch_fingerprint(batch)
        checkpoint = completed_checkpoints.get(fingerprint)
        if checkpoint is not None:
            planned = _planning_result_from_checkpoint(
                checkpoint.get("planner"),
                batch,
            )
            reviewed = _planning_result_from_checkpoint(
                checkpoint.get("reviewer"),
                batch,
            )
        else:
            try:
                planned = adjudicator.plan_speaker_ownership(batch)
            except Exception as error:
                planned = SpeakerOwnershipPlanningResult(
                    plans={},
                    error=_boundary_internal_error(error),
                )
            try:
                reviewed = adjudicator.review_speaker_ownership(batch)
            except Exception as error:
                reviewed = SpeakerOwnershipPlanningResult(
                    plans={},
                    error=_boundary_internal_error(error),
                )
        planning_input_tokens += planned.input_tokens
        planning_output_tokens += planned.output_tokens
        planning_provider = planned.provider or planning_provider
        planning_model = planned.model or planning_model
        planning_cli_version = planned.cli_version or planning_cli_version
        planning_plans.update(planned.plans)
        planning_errors.update(planned.validation_errors or {})
        raw_plans.update(planned.raw_plans or {})
        if planned.error is not None:
            degraded_errors.append(planned.error)
            for region in batch:
                planning_errors.setdefault(region.region_id, planned.error)
        review_input_tokens += reviewed.input_tokens
        review_output_tokens += reviewed.output_tokens
        review_provider = reviewed.provider or review_provider
        review_model = reviewed.model or review_model
        review_cli_version = reviewed.cli_version or review_cli_version
        review_plans.update(reviewed.plans)
        review_errors.update(reviewed.validation_errors or {})
        raw_reviews.update(reviewed.raw_plans or {})
        if reviewed.error is not None:
            degraded_errors.append(reviewed.error)
            for region in batch:
                review_errors.setdefault(region.region_id, reviewed.error)
        batch_record = {
            "batch_index": batch_index,
            "region_ids": [region.region_id for region in batch],
            "fingerprint": fingerprint,
            "resumed_from_checkpoint": checkpoint is not None,
            "request_characters": ownership_request_character_count(batch),
            "planner_error": planned.error,
            "reviewer_error": reviewed.error,
            "planner_provider": planned.provider,
            "planner_model": planned.model,
            "planner_cli_version": planned.cli_version,
            "reviewer_provider": reviewed.provider,
            "reviewer_model": reviewed.model,
            "reviewer_cli_version": reviewed.cli_version,
            "planner_input_tokens": planned.input_tokens,
            "planner_output_tokens": planned.output_tokens,
            "reviewer_input_tokens": reviewed.input_tokens,
            "reviewer_output_tokens": reviewed.output_tokens,
        }
        batch_records.append(batch_record)
        if checkpoint is None:
            completed_checkpoints[fingerprint] = {
                **batch_record,
                "planner": _planning_result_to_checkpoint(planned),
                "reviewer": _planning_result_to_checkpoint(reviewed),
            }
            if checkpoint_writer is not None:
                partial_payload = dict(payload)
                partial_payload["speaker_ownership_v15_checkpoint"] = {
                    "policy_version": SPEAKER_OWNERSHIP_POLICY_VERSION,
                    "completed_batches": list(completed_checkpoints.values()),
                }
                checkpoint_writer(
                    replace(
                        analysis,
                        boundary_adjudication_json=json.dumps(
                            partial_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                )
    planning = SpeakerOwnershipPlanningResult(
        plans=planning_plans,
        input_tokens=planning_input_tokens,
        output_tokens=planning_output_tokens,
        provider=planning_provider,
        model=planning_model,
        cli_version=planning_cli_version,
        validation_errors=planning_errors,
        raw_plans=raw_plans,
    )
    review = SpeakerOwnershipPlanningResult(
        plans=review_plans,
        input_tokens=review_input_tokens,
        output_tokens=review_output_tokens,
        provider=review_provider,
        model=review_model,
        cli_version=review_cli_version,
        validation_errors=review_errors,
        raw_plans=raw_reviews,
    )
    application = apply_speaker_ownership_plans(
        analysis.segments,
        regions,
        planning,
        review,
        raw_turns=analysis.raw_diarization_turns,
        speaker_mapping=speaker_mapping,
    )
    natural = segment_naturally(
        application.segments,
        full_asr_text=analysis.full_asr_text,
        protected_word_ranges=tuple(
            (region.start_word_index, region.end_word_index)
            for region in regions
            if {
                "crosstalk",
                "multiple-handoffs",
                "three-speaker",
            }
            & set(region.trigger_reasons)
        ),
        source_event_ranges=tuple(
            (start_seconds, end_seconds)
            for _kind, start_seconds, end_seconds in analysis.source_events
        ),
    )
    natural_records = _natural_boundary_records(natural)
    summary = {
        "policy_version": SPEAKER_OWNERSHIP_POLICY_VERSION,
        "region_count": len(regions),
        "batch_count": len(batches),
        "oversized_region_count": len(oversized_regions),
        "changed_word_count": application.changed_word_count,
        "applied_region_count": application.applied_region_count,
        "disputed_region_count": application.disputed_region_count,
        "possible_missing_speech_count": (
            application.possible_missing_speech_count
        ),
        "preserved_region_count": application.preserved_region_count,
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
        "batches": batch_records,
        "natural_segmentation": natural.to_summary(),
        "decision_records": [
            record.to_payload()
            for record in (*application.records, *natural_records)
        ],
    }
    prior_records = list(payload.get("decision_records") or [])
    payload.pop("pending", None)
    payload.pop("speaker_ownership_v15_checkpoint", None)
    payload.update(
        {
            "policy_version": SPEAKER_OWNERSHIP_POLICY_VERSION,
            "changed_count": application.applied_region_count,
            "marked_count": application.disputed_region_count,
            "decision_records": [
                *prior_records,
                *(record.to_payload() for record in application.records),
                *(record.to_payload() for record in natural_records),
            ],
            "speaker_ownership_v15": summary,
        }
    )
    if degraded_errors:
        payload["speaker_ownership_v15"]["degraded_errors"] = list(
            dict.fromkeys(degraded_errors)
        )
    updated = replace(
        analysis,
        segments=natural.segments,
        boundary_adjudication_json=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
        speaker_consolidation_json=_with_attribution_records(
            analysis.speaker_consolidation_json,
            natural.segments,
            analysis.pipeline_version,
        ),
    )
    return AdjudicationCompletion(
        analysis=updated,
        completed=True,
        dispute_count=len(regions),
        changed_count=application.changed_word_count,
        marked_count=application.disputed_region_count,
        input_tokens=planning.input_tokens + review.input_tokens,
        output_tokens=planning.output_tokens + review.output_tokens,
        provider=planning.provider or review.provider,
        model=planning.model or review.model,
        cli_version=planning.cli_version or review.cli_version,
        discourse_error=(degraded_errors[0] if degraded_errors else None),
        ownership_region_count=len(regions),
        ownership_changed_word_count=application.changed_word_count,
        ownership_disputed_region_count=application.disputed_region_count,
        natural_segment_count=len(natural.segments),
        natural_fallback_boundary_count=natural.fallback_boundary_count,
    )


def _natural_boundary_records(
    natural: NaturalSegmentationResult,
) -> tuple[DecisionRecord, ...]:
    return tuple(
        DecisionRecord(
            kind="natural-segmentation-boundary",
            policy_version=natural.policy_version,
            subject={
                "after_word_index": boundary.after_word_index,
                "before_word_index": boundary.before_word_index,
                "boundary_seconds": boundary.boundary_seconds,
            },
            evidence={"boundary_kind": boundary.kind},
            verdict=(
                "fallback-cut"
                if boundary.kind == "fallback"
                else "natural-boundary"
            ),
            rationale=(
                "The hard duration envelope required a non-natural cut."
                if boundary.kind == "fallback"
                else f"The {boundary.kind} boundary was selected."
            ),
        )
        for boundary in natural.boundaries
    )


def _ownership_batch_fingerprint(
    batch: Sequence[SpeakerOwnershipRegion],
) -> str:
    canonical = json.dumps(
        [region.to_payload() for region in batch],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _planning_result_to_checkpoint(
    result: SpeakerOwnershipPlanningResult,
) -> dict[str, Any]:
    return {
        "plans": [
            {"region_id": region_id, **plan.to_payload()}
            for region_id, plan in result.plans.items()
            if plan is not None
        ],
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "provider": result.provider,
        "model": result.model,
        "cli_version": result.cli_version,
        "error": result.error,
        "validation_errors": result.validation_errors or {},
        "raw_plans": {
            region_id: list(values)
            for region_id, values in (result.raw_plans or {}).items()
        },
    }


def _planning_result_from_checkpoint(
    value: object,
    batch: Sequence[SpeakerOwnershipRegion],
) -> SpeakerOwnershipPlanningResult:
    if not isinstance(value, dict):
        return SpeakerOwnershipPlanningResult(
            plans={},
            error="Speaker ownership batch checkpoint is malformed",
        )
    raw_plan_items = value.get("plans")
    try:
        plans, parse_errors, parsed_raw = parse_ownership_plans(
            {"plans": raw_plan_items},
            batch,
        )
    except (TypeError, ValueError) as error:
        return SpeakerOwnershipPlanningResult(
            plans={},
            error=f"{type(error).__name__}: {error}",
        )
    stored_errors = value.get("validation_errors")
    validation_errors = {
        **(
            {str(key): str(error) for key, error in stored_errors.items()}
            if isinstance(stored_errors, dict)
            else {}
        ),
        **parse_errors,
    }
    stored_raw = value.get("raw_plans")
    raw_plans = dict(parsed_raw)
    if isinstance(stored_raw, dict):
        raw_plans.update(
            {
                str(region_id): tuple(
                    item for item in items if isinstance(item, dict)
                )
                for region_id, items in stored_raw.items()
                if isinstance(items, list)
            }
        )
    return SpeakerOwnershipPlanningResult(
        plans=plans,
        input_tokens=int(value.get("input_tokens", 0)),
        output_tokens=int(value.get("output_tokens", 0)),
        provider=str(value.get("provider", "")),
        model=str(value.get("model", "")),
        cli_version=str(value.get("cli_version", "")),
        error=(str(value["error"]) if value.get("error") is not None else None),
        validation_errors=validation_errors,
        raw_plans=raw_plans,
    )


def _boundary_internal_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:500]


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
