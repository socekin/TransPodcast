from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

from transpodcast.domain import (
    CanonicalSegment,
    SourceAnalysisResult,
    TranslationResult,
)

CONTENT_EDIT_POLICY_VERSION = "semantic-content-edit-v1"
_ALLOWED_CATEGORIES = {"opening_highlight", "paid_ad"}
_MINIMUM_CONFIDENCE = 0.90
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")


@dataclass(frozen=True)
class ResolvedContentExclusion:
    decision_id: str
    category: str
    confidence: float
    start_segment_id: str
    end_segment_id: str
    start_token_index: int
    end_token_index: int
    start_seconds: float
    end_seconds: float
    rationale: str
    evidence_segment_ids: tuple[str, ...]
    retained_start_target: str
    retained_end_target: str


@dataclass(frozen=True)
class RenderPlan:
    analysis: SourceAnalysisResult
    translation: TranslationResult
    exclusions: tuple[ResolvedContentExclusion, ...]
    status: str
    diagnostics: tuple[str, ...] = ()

    @property
    def excluded_source_seconds(self) -> float:
        return round(
            sum(
                item.end_seconds - item.start_seconds
                for item in self.exclusions
            ),
            3,
        )


@dataclass(frozen=True)
class _TimedToken:
    text: str
    start_seconds: float
    end_seconds: float
    segment_id: str
    token_index: int


def build_render_plan(
    analysis: SourceAnalysisResult,
    translation: TranslationResult,
) -> RenderPlan:
    if len(analysis.segments) != len(translation.segments):
        return _retained_plan(
            analysis,
            translation,
            "degraded",
            "content_edit_source_alignment_is_invalid",
        )
    try:
        payload = json.loads(translation.content_edit_json or "{}")
    except (TypeError, ValueError):
        return _retained_plan(
            analysis,
            translation,
            "degraded",
            "content_edit_artifact_is_malformed",
        )
    if not isinstance(payload, dict) or not payload:
        return _retained_plan(analysis, translation, "not_requested")
    if payload.get("policy_version") != CONTENT_EDIT_POLICY_VERSION:
        return _retained_plan(
            analysis,
            translation,
            "degraded",
            "content_edit_policy_is_unsupported",
        )
    if payload.get("status") != "accepted":
        return _retained_plan(
            analysis,
            translation,
            "degraded",
            "content_edit_artifact_is_not_accepted",
        )
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return _retained_plan(
            analysis,
            translation,
            "degraded",
            "content_edit_decisions_are_malformed",
        )

    decision_ids = [
        value.get("decision_id", "").strip()
        for value in decisions
        if isinstance(value, dict)
        and isinstance(value.get("decision_id"), str)
        and value.get("decision_id", "").strip()
    ]
    duplicate_ids = {
        decision_id
        for decision_id, count in Counter(decision_ids).items()
        if count > 1
    }
    candidates: list[tuple[int, ResolvedContentExclusion]] = []
    diagnostics: list[str] = []
    seen_ids: set[str] = set()
    for index, decision in enumerate(decisions, start=1):
        if (
            isinstance(decision, dict)
            and isinstance(decision.get("decision_id"), str)
            and decision.get("decision_id", "").strip() in duplicate_ids
        ):
            diagnostics.append(
                f"decision_{index}:content_edit_decision_id_is_duplicated"
            )
            continue
        try:
            exclusion = _resolve_decision(analysis, decision, seen_ids)
        except (IndexError, KeyError, TypeError, ValueError) as error:
            diagnostics.append(f"decision_{index}:{error}")
            continue
        candidates.append((index, exclusion))
        seen_ids.add(exclusion.decision_id)

    conflicting: dict[int, str] = {}
    by_id = {
        segment.segment_id: index
        for index, segment in enumerate(analysis.segments)
    }
    for left_offset, (left_index, left) in enumerate(candidates):
        for right_index, right in candidates[left_offset + 1 :]:
            overlaps = (
                left.start_seconds < right.end_seconds
                and left.end_seconds > right.start_seconds
            )
            shares_segment = not (
                by_id[left.end_segment_id] < by_id[right.start_segment_id]
                or by_id[right.end_segment_id]
                < by_id[left.start_segment_id]
            )
            if not overlaps and not shares_segment:
                continue
            reason = (
                "content_edit_overlap"
                if overlaps
                else "content_edit_multiple_intervals_per_segment"
            )
            conflicting[left_index] = reason
            conflicting[right_index] = reason
    resolved = [
        exclusion
        for index, exclusion in candidates
        if index not in conflicting
    ]
    diagnostics.extend(
        f"decision_{index}:{reason}"
        for index, reason in sorted(conflicting.items())
    )
    resolved.sort(key=lambda item: item.start_seconds)
    normalized = _merge_adjacent_exclusions(analysis, resolved)
    render_analysis, render_targets = _apply_exclusions(
        analysis,
        translation.segments,
        normalized,
    )
    if analysis.segments and not render_analysis.segments:
        return _retained_plan(
            analysis,
            translation,
            "degraded",
            "content_edit_would_remove_all_spoken_content",
        )
    if any(not target.strip() for target in render_targets):
        return _retained_plan(
            analysis,
            translation,
            "degraded",
            "content_edit_render_target_is_empty",
        )
    return RenderPlan(
        analysis=render_analysis,
        translation=replace(translation, segments=render_targets),
        exclusions=normalized,
        status="degraded" if diagnostics else "accepted",
        diagnostics=tuple(diagnostics),
    )


def _merge_adjacent_exclusions(
    analysis: SourceAnalysisResult,
    exclusions: list[ResolvedContentExclusion],
) -> tuple[ResolvedContentExclusion, ...]:
    normalized: list[ResolvedContentExclusion] = []
    all_tokens = tuple(
        token
        for segment in analysis.segments
        for token in _timed_tokens(segment)
    )
    for exclusion in exclusions:
        if not normalized:
            normalized.append(exclusion)
            continue
        previous = normalized[-1]
        retained_between = any(
            token.start_seconds >= previous.end_seconds
            and token.end_seconds <= exclusion.start_seconds
            for token in all_tokens
        )
        mergeable = (
            previous.category == exclusion.category
            and previous.end_seconds <= exclusion.start_seconds
            and not retained_between
            and not previous.retained_end_target
            and not exclusion.retained_start_target
        )
        if not mergeable:
            normalized.append(exclusion)
            continue
        normalized[-1] = replace(
            previous,
            decision_id=f"{previous.decision_id}+{exclusion.decision_id}",
            end_segment_id=exclusion.end_segment_id,
            end_token_index=exclusion.end_token_index,
            end_seconds=exclusion.end_seconds,
            confidence=min(previous.confidence, exclusion.confidence),
            rationale=f"{previous.rationale} {exclusion.rationale}".strip(),
            evidence_segment_ids=tuple(
                dict.fromkeys(
                    (
                        *previous.evidence_segment_ids,
                        *exclusion.evidence_segment_ids,
                    )
                )
            ),
            retained_end_target=exclusion.retained_end_target,
        )
    return tuple(normalized)


def content_edit_for_segment(
    plan: RenderPlan,
    segment: CanonicalSegment,
) -> dict[str, object] | None:
    matches = [
        item
        for item in plan.exclusions
        if item.start_seconds < segment.end_seconds
        and item.end_seconds > segment.start_seconds
    ]
    if not matches:
        return None
    exclusion = matches[0]
    tokens = _timed_tokens(segment)
    fully_excluded = bool(tokens) and (
        exclusion.start_seconds <= tokens[0].start_seconds
        and exclusion.end_seconds >= tokens[-1].end_seconds
    )
    return {
        "state": "excluded" if fully_excluded else "partially_excluded",
        "category": exclusion.category,
        "confidence": exclusion.confidence,
        "decision_id": exclusion.decision_id,
        "source_start_seconds": exclusion.start_seconds,
        "source_end_seconds": exclusion.end_seconds,
        "rationale": exclusion.rationale,
        "evidence_segment_ids": list(exclusion.evidence_segment_ids),
    }


def _retained_plan(
    analysis: SourceAnalysisResult,
    translation: TranslationResult,
    status: str,
    diagnostic: str | None = None,
) -> RenderPlan:
    return RenderPlan(
        analysis=analysis,
        translation=translation,
        exclusions=(),
        status=status,
        diagnostics=(diagnostic,) if diagnostic else (),
    )


def _resolve_decision(
    analysis: SourceAnalysisResult,
    value: object,
    seen_ids: set[str],
) -> ResolvedContentExclusion:
    if not isinstance(value, dict):
        raise ValueError("content_edit_decision_is_malformed")
    decision_id = _required_string(value, "decision_id")
    if decision_id in seen_ids:
        raise ValueError("content_edit_decision_id_is_duplicated")
    category = _required_string(value, "category")
    if category not in _ALLOWED_CATEGORIES:
        raise ValueError("content_edit_category_is_unsupported")
    confidence = float(value.get("confidence", 0.0))
    if (
        not math.isfinite(confidence)
        or confidence < _MINIMUM_CONFIDENCE
        or confidence > 1.0
    ):
        raise ValueError("content_edit_confidence_is_insufficient")
    rationale = _required_string(value, "rationale")
    start_segment_id = _required_string(value, "start_segment_id")
    end_segment_id = _required_string(value, "end_segment_id")
    start_anchor = _required_string(value, "start_anchor")
    end_anchor = _required_string(value, "end_anchor")
    by_id = {
        segment.segment_id: (index, segment)
        for index, segment in enumerate(analysis.segments)
    }
    if start_segment_id not in by_id or end_segment_id not in by_id:
        raise ValueError("content_edit_segment_is_unknown")
    start_index, start_segment = by_id[start_segment_id]
    end_index, end_segment = by_id[end_segment_id]
    if start_index > end_index:
        raise ValueError("content_edit_segment_range_is_reversed")
    tokens = tuple(
        token
        for segment in analysis.segments[start_index : end_index + 1]
        for token in _timed_tokens(segment)
    )
    if not tokens:
        raise ValueError("content_edit_word_alignment_is_missing")
    start_match = _unique_anchor_match(tokens, start_anchor)
    end_match = _unique_anchor_match(tokens, end_anchor)
    start_seconds = start_match[0].start_seconds
    end_seconds = end_match[-1].end_seconds
    if start_seconds >= end_seconds:
        raise ValueError("content_edit_anchor_range_is_reversed")
    if start_match[0].segment_id != start_segment_id:
        raise ValueError("content_edit_start_anchor_is_outside_segment")
    if end_match[-1].segment_id != end_segment_id:
        raise ValueError("content_edit_end_anchor_is_outside_segment")
    retained_start_target = _optional_string(
        value,
        "retained_start_target",
    )
    retained_end_target = _optional_string(
        value,
        "retained_end_target",
    )
    first_word_start = _timed_tokens(start_segment)[0].start_seconds
    last_word_end = _timed_tokens(end_segment)[-1].end_seconds
    if start_seconds > first_word_start and not retained_start_target:
        raise ValueError("content_edit_retained_start_target_is_missing")
    if end_seconds < last_word_end and not retained_end_target:
        raise ValueError("content_edit_retained_end_target_is_missing")
    evidence = value.get("evidence_segment_ids", [])
    if not isinstance(evidence, list):
        raise ValueError("content_edit_evidence_is_malformed")
    evidence_segment_ids = tuple(str(item).strip() for item in evidence)
    if not any(evidence_segment_ids):
        raise ValueError("content_edit_evidence_is_missing")
    known_ids = set(by_id)
    if any(item not in known_ids for item in evidence_segment_ids):
        raise ValueError("content_edit_evidence_segment_is_unknown")
    return ResolvedContentExclusion(
        decision_id=decision_id,
        category=category,
        confidence=confidence,
        start_segment_id=start_segment_id,
        end_segment_id=end_segment_id,
        start_token_index=start_match[0].token_index,
        end_token_index=end_match[-1].token_index,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        rationale=rationale,
        evidence_segment_ids=evidence_segment_ids,
        retained_start_target=retained_start_target,
        retained_end_target=retained_end_target,
    )


def _apply_exclusions(
    analysis: SourceAnalysisResult,
    targets: tuple[str, ...],
    exclusions: tuple[ResolvedContentExclusion, ...],
) -> tuple[SourceAnalysisResult, tuple[str, ...]]:
    rendered_segments: list[CanonicalSegment] = []
    rendered_targets: list[str] = []
    for segment, target in zip(analysis.segments, targets, strict=True):
        matches = [
            item
            for item in exclusions
            if item.start_seconds < segment.end_seconds
            and item.end_seconds > segment.start_seconds
        ]
        if not matches:
            rendered_segments.append(segment)
            rendered_targets.append(target)
            continue
        exclusion = matches[0]
        tokens = _timed_tokens(segment)
        before = (
            tokens[: exclusion.start_token_index]
            if segment.segment_id == exclusion.start_segment_id
            else ()
        )
        after = (
            tokens[exclusion.end_token_index + 1 :]
            if segment.segment_id == exclusion.end_segment_id
            else ()
        )
        if before:
            rendered_segments.append(
                _retained_segment(
                    segment,
                    before,
                    f"{segment.segment_id}-retained-before",
                )
            )
            rendered_targets.append(exclusion.retained_start_target)
        if after:
            rendered_segments.append(
                _retained_segment(
                    segment,
                    after,
                    f"{segment.segment_id}-retained-after",
                )
            )
            rendered_targets.append(exclusion.retained_end_target)
    intervals = tuple(
        (item.start_seconds, item.end_seconds) for item in exclusions
    )
    return (
        replace(
            analysis,
            segments=tuple(rendered_segments),
            source_events=tuple(
                event
                for event in analysis.source_events
                if not _inside_any_interval(event[1], event[2], intervals)
            ),
            background_regions=tuple(
                region
                for region in analysis.background_regions
                if not _inside_any_interval(region[0], region[1], intervals)
            ),
        ),
        tuple(rendered_targets),
    )


def _retained_segment(
    original: CanonicalSegment,
    tokens: tuple[_TimedToken, ...],
    segment_id: str,
) -> CanonicalSegment:
    return replace(
        original,
        segment_id=segment_id,
        start_seconds=tokens[0].start_seconds,
        end_seconds=tokens[-1].end_seconds,
        text=" ".join(token.text for token in tokens),
        words=tuple(
            (token.text, token.start_seconds, token.end_seconds)
            for token in tokens
        ),
    )


def _timed_tokens(segment: CanonicalSegment) -> tuple[_TimedToken, ...]:
    tokens: list[_TimedToken] = []
    for word, start_seconds, end_seconds in segment.words:
        for part in _TOKEN_PATTERN.findall(str(word)):
            tokens.append(
                _TimedToken(
                    text=part,
                    start_seconds=float(start_seconds),
                    end_seconds=float(end_seconds),
                    segment_id=segment.segment_id,
                    token_index=len(tokens),
                )
            )
    return tuple(tokens)


def _unique_anchor_match(
    tokens: tuple[_TimedToken, ...],
    anchor: str,
) -> tuple[_TimedToken, ...]:
    expected = [item.lower() for item in _TOKEN_PATTERN.findall(anchor)]
    if not expected:
        raise ValueError("content_edit_anchor_is_empty")
    actual = [token.text.lower() for token in tokens]
    starts = [
        index
        for index in range(0, len(actual) - len(expected) + 1)
        if actual[index : index + len(expected)] == expected
    ]
    if not starts:
        raise ValueError("content_edit_anchor_is_missing")
    if len(starts) != 1:
        raise ValueError("content_edit_anchor_is_ambiguous")
    start = starts[0]
    return tokens[start : start + len(expected)]


def _inside_any_interval(
    start_seconds: float,
    end_seconds: float,
    intervals: tuple[tuple[float, float], ...],
) -> bool:
    return any(
        start <= start_seconds and end >= end_seconds
        for start, end in intervals
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    raw = payload.get(key)
    if not isinstance(raw, str):
        raise ValueError(f"content_edit_{key}_is_malformed")
    value = raw.strip()
    if not value:
        raise ValueError(f"content_edit_{key}_is_missing")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str:
    raw = payload.get(key, "")
    if not isinstance(raw, str):
        raise ValueError(f"content_edit_{key}_is_malformed")
    return raw.strip()
