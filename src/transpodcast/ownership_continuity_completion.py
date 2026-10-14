"""Checkpointed two-call evaluation of the candidate ownership policy.

This entry point returns candidate artifacts only. It never rewrites a historical
Source Analysis or selects a production Pipeline Version.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Protocol

from transpodcast.adapters.agent import AgentInvocationError
from transpodcast.domain import CanonicalSegment
from transpodcast.natural_segmentation import (
    NaturalSegmentationResult,
    segment_naturally,
)
from transpodcast.speaker_ownership_continuity import (
    CONTINUITY_POLICY_VERSION,
    ContinuityRegion,
    apply_reviewed_ownership,
    batch_continuity_regions,
    continuity_question,
    validate_ownership_result,
)
from transpodcast.speaker_ownership_v15 import (
    MAXIMUM_BATCH_CHARACTERS,
    MAXIMUM_BATCH_REGIONS,
    SpeakerOwnershipApplication,
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
    parse_ownership_plans,
)

_STAGE_STOPPING_AGENT_FAILURES = frozenset({"rate_limit", "auth"})


class ContinuityJudge(Protocol):
    def plan(
        self, regions: Sequence[SpeakerOwnershipRegion]
    ) -> SpeakerOwnershipPlanningResult: ...

    def review(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
        planning: SpeakerOwnershipPlanningResult,
    ) -> SpeakerOwnershipPlanningResult: ...


@dataclass(frozen=True)
class ContinuityCompletion:
    application: SpeakerOwnershipApplication
    natural: NaturalSegmentationResult
    checkpoint: dict[str, Any]
    batches: tuple[dict[str, Any], ...]
    executed_call_count: int
    resumed_call_count: int
    input_tokens: int
    output_tokens: int
    policy_version: str = CONTINUITY_POLICY_VERSION


def question_fingerprint(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def overlay_reviewed_ownership(
    previous_owners: Sequence[str], application: SpeakerOwnershipApplication
) -> tuple[str, ...]:
    """Compose a follow-up without resetting words lacking a new final assignment.

    Applications are validated by complete_continuity_ownership. A failed,
    missing, invalid, or preserving follow-up keeps the already accepted pass;
    the original timeline in the application is not a rollback instruction.
    """
    proposed = tuple(s.speaker_label for s in application.segments for _ in s.words)
    if len(previous_owners) != len(proposed):
        raise ValueError("Ownership overlay must preserve the immutable timeline")
    owners = list(previous_owners)
    for record in application.records:
        if (
            record.policy_version != CONTINUITY_POLICY_VERSION
            or record.evidence.get("applied_plan_source") != "reviewer"
            or record.evidence.get("applied_plan") is None
        ):
            continue
        start = record.subject["first_word_index"]
        end = record.subject["last_word_index"] + 1
        owners[start:end] = proposed[start:end]
    return tuple(owners)


def _encode(result: SpeakerOwnershipPlanningResult) -> dict[str, Any]:
    return {
        "plans": [
            {"region_id": key, **p.to_payload()}
            for key, p in result.plans.items()
            if p is not None
        ],
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "provider": result.provider,
        "model": result.model,
        "cli_version": result.cli_version,
        "error": result.error,
        "validation_errors": result.validation_errors or {},
        "raw_plans": {k: list(v) for k, v in (result.raw_plans or {}).items()},
    }


def _restore(
    entry: object,
    request_hash: str,
    regions: Sequence[SpeakerOwnershipRegion],
) -> SpeakerOwnershipPlanningResult | None:
    if not isinstance(entry, dict) or entry.get("request_sha256") != request_hash:
        return None
    value = entry.get("result")
    if not isinstance(value, dict) or entry.get(
        "response_sha256"
    ) != question_fingerprint(value):
        return None
    try:
        plans, errors, raw = parse_ownership_plans({"plans": value["plans"]}, regions)
        errors.update(value.get("validation_errors") or {})
        return SpeakerOwnershipPlanningResult(
            plans=plans,
            validation_errors=errors,
            raw_plans={k: tuple(v) for k, v in (value.get("raw_plans") or raw).items()},
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            provider=str(value["provider"]),
            model=str(value["model"]),
            cli_version=str(value["cli_version"]),
            error=value.get("error"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _combine(
    results: Sequence[
        tuple[Sequence[SpeakerOwnershipRegion], SpeakerOwnershipPlanningResult]
    ],
) -> SpeakerOwnershipPlanningResult:
    plans = {}
    errors: dict[str, str] = {}
    raw: dict[str, tuple[dict[str, Any], ...]] = {}
    for regions, result in results:
        plans.update(result.plans)
        errors.update(result.validation_errors or {})
        raw.update(result.raw_plans or {})
        if result.error:
            errors.update({r.region_id: result.error for r in regions})
    return SpeakerOwnershipPlanningResult(
        plans=plans,
        validation_errors=errors,
        raw_plans=raw,
        input_tokens=sum(r.input_tokens for _, r in results),
        output_tokens=sum(r.output_tokens for _, r in results),
    )


def complete_continuity_ownership(
    segments: Sequence[CanonicalSegment],
    regions: Sequence[ContinuityRegion],
    judge: ContinuityJudge,
    *,
    raw_turns: Sequence[tuple[str, float, float]],
    speaker_mapping: dict[str, str],
    full_asr_text: str = "",
    source_event_ranges: Sequence[tuple[float, float]] = (),
    checkpoint: dict[str, Any] | None = None,
    checkpoint_writer: Callable[[dict[str, Any]], None] | None = None,
    maximum_characters: int = MAXIMUM_BATCH_CHARACTERS,
    maximum_regions: int = MAXIMUM_BATCH_REGIONS,
) -> ContinuityCompletion:
    # Validate all editable ranges before any provider expense or mutation.
    empty = SpeakerOwnershipPlanningResult(plans={})
    apply_reviewed_ownership(
        segments,
        regions,
        empty,
        empty,
        raw_turns=raw_turns,
        speaker_mapping=speaker_mapping,
    )
    batches = batch_continuity_regions(
        regions, maximum_characters=maximum_characters, maximum_regions=maximum_regions
    )
    saved: dict[str, Any] = {"policy_version": CONTINUITY_POLICY_VERSION, "batches": {}}
    if (
        checkpoint
        and checkpoint.get("policy_version") == CONTINUITY_POLICY_VERSION
        and isinstance(checkpoint.get("batches"), dict)
    ):
        saved["batches"] = deepcopy(checkpoint["batches"])
    planning_results: list[
        tuple[Sequence[SpeakerOwnershipRegion], SpeakerOwnershipPlanningResult]
    ] = []
    review_results: list[
        tuple[Sequence[SpeakerOwnershipRegion], SpeakerOwnershipPlanningResult]
    ] = []
    records: list[dict[str, Any]] = []
    executed, resumed = 0, 0
    for batch in batches:
        evidence_hash = question_fingerprint(continuity_question(batch))
        existing = saved["batches"].get(evidence_hash)
        batch_checkpoint = deepcopy(existing) if isinstance(existing, dict) else {}
        role_results: dict[str, SpeakerOwnershipPlanningResult] = {}
        record: dict[str, Any] = {
            "evidence_sha256": evidence_hash,
            "region_ids": [r.region_id for r in batch],
        }
        for role in ("planner", "reviewer"):
            question = continuity_question(batch, role_results.get("planner"))
            request_hash = question_fingerprint(question)
            instructions, data, schema = question
            characters = len(
                json.dumps(
                    {
                        "instructions": instructions,
                        "source_data": data,
                        "schema": schema,
                    },
                    ensure_ascii=False,
                )
            )
            entry = batch_checkpoint.get(role)
            result = _restore(entry, request_hash, batch)
            from_cache = result is not None
            if result is None:
                began = time.monotonic()
                if characters > maximum_characters:
                    result = SpeakerOwnershipPlanningResult(
                        plans={},
                        error=(
                            "Final request exceeds the bounded envelope; "
                            "region preserved"
                        ),
                    )
                else:
                    executed += 1
                    try:
                        result = (
                            judge.plan(batch)
                            if role == "planner"
                            else judge.review(batch, role_results["planner"])
                        )
                        result = validate_ownership_result(result, batch)
                    except AgentInvocationError as error:
                        # A rate limit or a lost login is not a verdict on
                        # this batch: stop so the Retry Policy (or the Owner)
                        # acts; completed batches stay checkpointed.
                        if error.kind in _STAGE_STOPPING_AGENT_FAILURES:
                            raise
                        result = SpeakerOwnershipPlanningResult(
                            plans={}, error=f"{type(error).__name__}: {error}"[:500]
                        )
                    except Exception as error:
                        result = SpeakerOwnershipPlanningResult(
                            plans={}, error=f"{type(error).__name__}: {error}"[:500]
                        )
                encoded = _encode(result)
                entry = {
                    "request_sha256": request_hash,
                    "response_sha256": question_fingerprint(encoded),
                    "result": encoded,
                    "request_characters": characters,
                    "elapsed_seconds": time.monotonic() - began,
                }
                batch_checkpoint[role] = entry
                saved["batches"][evidence_hash] = batch_checkpoint
                if checkpoint_writer is not None:
                    checkpoint_writer(deepcopy(saved))
            else:
                resumed += 1
            role_results[role] = result
            assert isinstance(entry, dict)
            record[role] = {**entry, "resumed": from_cache}
        planning_results.append((batch, role_results["planner"]))
        review_results.append((batch, role_results["reviewer"]))
        records.append(record)
    preserved = tuple(r for r in regions if r.preservation_reason)
    if preserved:
        degraded = SpeakerOwnershipPlanningResult(
            plans={},
            validation_errors={
                r.region_id: r.preservation_reason or "Unbounded region"
                for r in preserved
            },
        )
        planning_results.append((preserved, degraded))
        review_results.append((preserved, degraded))
    planning, review = _combine(planning_results), _combine(review_results)
    application = apply_reviewed_ownership(
        segments,
        regions,
        planning,
        review,
        raw_turns=raw_turns,
        speaker_mapping=speaker_mapping,
    )
    executions: dict[str, dict[str, Any]] = {}
    for affected, result in planning_results:
        for r in affected:
            executions.setdefault(r.region_id, {})["planner_execution"] = _encode(
                result
            )
    for affected, result in review_results:
        for r in affected:
            executions.setdefault(r.region_id, {})["reviewer_execution"] = _encode(
                result
            )
    application = replace(
        application,
        records=tuple(
            replace(
                r,
                evidence={
                    **r.evidence,
                    **{
                        role: {
                            k: v
                            for k, v in details.items()
                            if k
                            in {
                                "input_tokens",
                                "output_tokens",
                                "provider",
                                "model",
                                "cli_version",
                                "error",
                            }
                        }
                        for role, details in executions[
                            str(r.subject["region_id"])
                        ].items()
                    },
                },
            )
            for r in application.records
        ),
    )
    natural = segment_naturally(
        application.segments,
        full_asr_text=full_asr_text,
        protected_word_ranges=tuple(
            (r.start_word_index, r.end_word_index)
            for r in regions
            if {"crosstalk", "multiple-handoffs", "three-speaker"}
            & set(r.trigger_reasons)
        ),
        source_event_ranges=source_event_ranges,
    )
    return ContinuityCompletion(
        application,
        natural,
        saved,
        tuple(records),
        executed,
        resumed,
        planning.input_tokens + review.input_tokens,
        planning.output_tokens + review.output_tokens,
    )
