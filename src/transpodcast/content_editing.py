from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from transpodcast.domain import (
    CanonicalSegment,
    SourceAnalysisResult,
    TranslationResult,
)
from transpodcast.pipeline_versions import WORD_OWNERSHIP_PIPELINES
from transpodcast.speaker_boundaries import recover_sentence_punctuation

CONTENT_EDIT_POLICY_VERSION = "semantic-content-edit-v1"
_ALLOWED_CATEGORIES = {"opening_highlight", "paid_ad"}
_MINIMUM_CONFIDENCE = 0.90
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")
MAXIMUM_RENDER_UNIT_CHARACTERS = 240
RENDER_PLAN_SCHEMA_VERSION = "render-plan-v1"
_TARGET_SENTENCE_BOUNDARY = re.compile(
    r"[\u3002\uFF01\uFF1F!?\uFF1B;]+[\u201D\u2019\"']*\s*"
)


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
class RenderPlanUnit:
    unit_id: str
    parent_segment_id: str
    source_segment_id: str
    source_start_seconds: float
    source_end_seconds: float
    pause_before_seconds: float
    pause_after_seconds: float
    target_text: str
    internal_split: bool = False
    approved_boundary_kind: str | None = None

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> RenderPlanUnit:
        try:
            unit_id = value["unit_id"]
            parent_segment_id = value["parent_segment_id"]
            source_segment_id = value["source_segment_id"]
            source_start_seconds = float(str(value["source_start_seconds"]))
            source_end_seconds = float(str(value["source_end_seconds"]))
            pause_before_seconds = float(str(value["pause_before_seconds"]))
            pause_after_seconds = float(str(value["pause_after_seconds"]))
            target_text = value["target_text"]
            internal_split = value["internal_split"]
            approved_boundary_kind = value["approved_boundary_kind"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Render Plan unit is malformed") from error
        if (
            not isinstance(unit_id, str)
            or not unit_id.strip()
            or not isinstance(parent_segment_id, str)
            or not parent_segment_id.strip()
            or not isinstance(source_segment_id, str)
            or not source_segment_id.strip()
        ):
            raise ValueError("Render Plan unit identity is invalid")
        numeric_values = (
            source_start_seconds,
            source_end_seconds,
            pause_before_seconds,
            pause_after_seconds,
        )
        if not all(math.isfinite(number) for number in numeric_values):
            raise ValueError("Render Plan unit timing must be finite")
        if source_start_seconds >= source_end_seconds:
            raise ValueError("Render Plan unit source span is invalid")
        if pause_before_seconds < 0 or pause_after_seconds < 0:
            raise ValueError("Render Plan pause metadata is invalid")
        if not isinstance(target_text, str) or not target_text.strip():
            raise ValueError("Render Plan unit target is invalid")
        if not isinstance(internal_split, bool):
            raise ValueError("Render Plan internal split flag is invalid")
        if approved_boundary_kind not in {None, "sentence", "pause"}:
            raise ValueError("Render Plan boundary kind is invalid")
        return cls(
            unit_id=unit_id,
            parent_segment_id=parent_segment_id,
            source_segment_id=source_segment_id,
            source_start_seconds=source_start_seconds,
            source_end_seconds=source_end_seconds,
            pause_before_seconds=pause_before_seconds,
            pause_after_seconds=pause_after_seconds,
            target_text=target_text,
            internal_split=internal_split,
            approved_boundary_kind=approved_boundary_kind,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "parent_segment_id": self.parent_segment_id,
            "source_segment_id": self.source_segment_id,
            "source_start_seconds": self.source_start_seconds,
            "source_end_seconds": self.source_end_seconds,
            "pause_before_seconds": self.pause_before_seconds,
            "pause_after_seconds": self.pause_after_seconds,
            "target_text": self.target_text,
            "internal_split": self.internal_split,
            "approved_boundary_kind": self.approved_boundary_kind,
        }


@dataclass(frozen=True)
class _InternalRenderBoundary:
    word_offset: int
    kind: str
    boundary_seconds: float
    pause_seconds: float


@dataclass(frozen=True)
class RenderPlan:
    analysis: SourceAnalysisResult
    translation: TranslationResult
    exclusions: tuple[ResolvedContentExclusion, ...]
    status: str
    diagnostics: tuple[str, ...] = ()
    units: tuple[RenderPlanUnit, ...] = ()

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
    units = _render_plan_units(
        render_analysis.segments,
        tuple(render_targets),
        allow_internal_splits=(
            analysis.pipeline_version in WORD_OWNERSHIP_PIPELINES
        ),
    )
    return RenderPlan(
        analysis=_analysis_with_render_units(render_analysis, units, normalized),
        translation=replace(translation, segments=render_targets),
        exclusions=normalized,
        status="degraded" if diagnostics else "accepted",
        diagnostics=tuple(diagnostics),
        units=units,
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
    units = _render_plan_units(
        analysis.segments,
        translation.segments,
        allow_internal_splits=(
            analysis.pipeline_version in WORD_OWNERSHIP_PIPELINES
        ),
    )
    return RenderPlan(
        analysis=_analysis_with_render_units(analysis, units),
        translation=translation,
        exclusions=(),
        status=status,
        diagnostics=(diagnostic,) if diagnostic else (),
        units=units,
    )


def _analysis_with_render_units(
    analysis: SourceAnalysisResult,
    units: tuple[RenderPlanUnit, ...],
    exclusions: tuple[ResolvedContentExclusion, ...] = (),
) -> SourceAnalysisResult:
    # The applied removal intervals let assembly place Content Edit cut
    # transitions; renderers ignore them.
    return replace(
        analysis,
        render_plan_json=json.dumps(
            {
                "schema_version": RENDER_PLAN_SCHEMA_VERSION,
                "units": [unit.to_payload() for unit in units],
                "exclusions": [
                    {
                        "start_seconds": item.start_seconds,
                        "end_seconds": item.end_seconds,
                    }
                    for item in exclusions
                ],
            },
            sort_keys=True,
        ),
    )


def _render_plan_units(
    segments: tuple[CanonicalSegment, ...],
    targets: tuple[str, ...],
    *,
    allow_internal_splits: bool,
) -> tuple[RenderPlanUnit, ...]:
    if len(segments) != len(targets):
        return ()
    units: list[RenderPlanUnit] = []
    for index, (segment, target) in enumerate(
        zip(segments, targets, strict=True)
    ):
        pause_before = round(
            max(
                0.0,
                segment.start_seconds
                - (
                    segments[index - 1].end_seconds
                    if index > 0
                    else segment.start_seconds
                ),
            ),
            6,
        )
        pause_after = round(
            max(
                0.0,
                (
                    segments[index + 1].start_seconds
                    if index + 1 < len(segments)
                    else segment.end_seconds
                )
                - segment.end_seconds,
            ),
            6,
        )
        split_units = (
            _internal_render_units(
                segment,
                target,
                pause_before=pause_before,
                pause_after=pause_after,
            )
            if allow_internal_splits
            else ()
        )
        if split_units:
            units.extend(split_units)
            continue
        units.append(
            RenderPlanUnit(
                unit_id=segment.segment_id,
                parent_segment_id=segment.segment_id.split(
                    "-retained-",
                    maxsplit=1,
                )[0],
                source_segment_id=segment.segment_id,
                source_start_seconds=segment.start_seconds,
                source_end_seconds=segment.end_seconds,
                pause_before_seconds=pause_before,
                pause_after_seconds=pause_after,
                target_text=target,
            )
        )
    return tuple(units)


def _internal_render_units(
    segment: CanonicalSegment,
    target: str,
    *,
    pause_before: float,
    pause_after: float,
) -> tuple[RenderPlanUnit, ...]:
    target_ranges = _target_render_ranges(target)
    if len(target_ranges) < 2:
        return ()
    source_boundaries = _internal_source_boundaries(segment)
    required = len(target_ranges) - 1
    if len(source_boundaries) < required:
        return ()
    selected = _select_source_boundaries(
        source_boundaries,
        target_ranges,
        len(target),
        len(segment.words),
    )
    words = segment.words
    word_offsets = (0, *(item.word_offset for item in selected), len(words))
    lineage = segment.segment_id.split("-retained-", maxsplit=1)[0]
    units: list[RenderPlanUnit] = []
    for index, ((target_start, target_end), word_start, word_end) in enumerate(
        zip(
            target_ranges,
            word_offsets[:-1],
            word_offsets[1:],
            strict=True,
        ),
        start=1,
    ):
        boundary_before = selected[index - 2] if index > 1 else None
        boundary_after = selected[index - 1] if index <= len(selected) else None
        units.append(
            RenderPlanUnit(
                unit_id=f"{segment.segment_id}--render-{index:02d}",
                parent_segment_id=lineage,
                source_segment_id=segment.segment_id,
                source_start_seconds=words[word_start][1],
                source_end_seconds=words[word_end - 1][2],
                pause_before_seconds=(
                    boundary_before.pause_seconds
                    if boundary_before is not None
                    else pause_before
                ),
                pause_after_seconds=(
                    boundary_after.pause_seconds
                    if boundary_after is not None
                    else pause_after
                ),
                target_text=target[target_start:target_end],
                internal_split=True,
                approved_boundary_kind=(
                    boundary_after.kind if boundary_after is not None else None
                ),
            )
        )
    return tuple(units)


def _target_render_ranges(target: str) -> tuple[tuple[int, int], ...]:
    if len(target) <= MAXIMUM_RENDER_UNIT_CHARACTERS:
        return ((0, len(target)),)
    boundaries = [
        match.end()
        for match in _TARGET_SENTENCE_BOUNDARY.finditer(target)
        if 0 < match.end() < len(target)
    ]
    ranges: list[tuple[int, int]] = []
    start = 0
    while len(target) - start > MAXIMUM_RENDER_UNIT_CHARACTERS:
        choices = [
            boundary
            for boundary in boundaries
            if start < boundary <= start + MAXIMUM_RENDER_UNIT_CHARACTERS
        ]
        if not choices:
            return ()
        end = choices[-1]
        ranges.append((start, end))
        start = end
    ranges.append((start, len(target)))
    return tuple(ranges)


def _internal_source_boundaries(
    segment: CanonicalSegment,
) -> tuple[_InternalRenderBoundary, ...]:
    if len(segment.words) < 2:
        return ()
    recovered: list[dict[str, Any]] = [
        {
            "text": text,
            "start_seconds": start,
            "end_seconds": end,
            "sentence_end": False,
            "sentence_suffix": "",
            "punctuation_confident": False,
        }
        for text, start, end in segment.words
    ]
    recover_sentence_punctuation(recovered, segment.text)
    boundaries: list[_InternalRenderBoundary] = []
    for offset in range(1, len(recovered)):
        previous = recovered[offset - 1]
        following = recovered[offset]
        gap = max(
            0.0,
            float(following["start_seconds"])
            - float(previous["end_seconds"]),
        )
        kind = (
            "sentence"
            if bool(previous.get("sentence_end"))
            else "pause" if gap >= 0.30 else ""
        )
        if not kind:
            continue
        boundaries.append(
            _InternalRenderBoundary(
                word_offset=offset,
                kind=kind,
                boundary_seconds=round(
                    (
                        float(previous["end_seconds"])
                        + float(following["start_seconds"])
                    )
                    / 2,
                    6,
                ),
                pause_seconds=round(gap, 6),
            )
        )
    return tuple(boundaries)


def _select_source_boundaries(
    candidates: tuple[_InternalRenderBoundary, ...],
    target_ranges: tuple[tuple[int, int], ...],
    target_length: int,
    source_word_count: int,
) -> tuple[_InternalRenderBoundary, ...]:
    selected: list[_InternalRenderBoundary] = []
    start_index = 0
    required = len(target_ranges) - 1
    for target_range in target_ranges[:-1]:
        remaining = required - len(selected) - 1
        available = candidates[start_index : len(candidates) - remaining]
        ratio = target_range[1] / target_length
        chosen = min(
            available,
            key=lambda item: abs(item.word_offset / source_word_count - ratio),
        )
        selected.append(chosen)
        start_index = candidates.index(chosen, start_index) + 1
    return tuple(selected)


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
