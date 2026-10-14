"""Natural Canonical Transcript segmentation after Speaker ownership."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from transpodcast.domain import CanonicalSegment
from transpodcast.source_segmentation import join_words
from transpodcast.speaker_boundaries import recover_sentence_punctuation

BoundaryKind = Literal[
    "speaker",
    "source_event",
    "sentence",
    "pause",
    "fallback",
]


@dataclass(frozen=True)
class NaturalSegmentationPolicy:
    version: str = "natural-segmentation-v1"
    soft_minimum_seconds: float = 5.0
    target_seconds: float = 12.0
    preferred_maximum_seconds: float = 18.0
    hard_maximum_seconds: float = 24.0
    pause_candidate_seconds: float = 0.30


DEFAULT_NATURAL_SEGMENTATION_POLICY = NaturalSegmentationPolicy()


@dataclass(frozen=True)
class NaturalBoundary:
    kind: BoundaryKind
    after_word_index: int
    before_word_index: int
    boundary_seconds: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "after_word_index": self.after_word_index,
            "before_word_index": self.before_word_index,
            "boundary_seconds": self.boundary_seconds,
        }


@dataclass(frozen=True)
class NaturalSegmentationResult:
    segments: tuple[CanonicalSegment, ...]
    policy_version: str
    punctuation_alignment: dict[str, Any]
    soft_minimum_seconds: float = 5.0
    preferred_maximum_seconds: float = 18.0
    speaker_boundary_count: int = 0
    source_event_boundary_count: int = 0
    sentence_boundary_count: int = 0
    pause_boundary_count: int = 0
    fallback_boundary_count: int = 0
    boundaries: tuple[NaturalBoundary, ...] = ()

    def to_summary(self) -> dict[str, Any]:
        durations = [
            segment.end_seconds - segment.start_seconds
            for segment in self.segments
        ]
        return {
            "policy_version": self.policy_version,
            "segment_count": len(self.segments),
            "speaker_boundary_count": self.speaker_boundary_count,
            "source_event_boundary_count": self.source_event_boundary_count,
            "sentence_boundary_count": self.sentence_boundary_count,
            "pause_boundary_count": self.pause_boundary_count,
            "fallback_boundary_count": self.fallback_boundary_count,
            "boundaries": [boundary.to_payload() for boundary in self.boundaries],
            "segments_below_soft_minimum": sum(
                duration < self.soft_minimum_seconds
                for duration in durations
            ),
            "segments_above_preferred_maximum": sum(
                duration
                > self.preferred_maximum_seconds
                for duration in durations
            ),
            "punctuation_alignment": self.punctuation_alignment,
        }


@dataclass(frozen=True)
class _OwnedWord:
    word_index: int
    source_segment_index: int
    text: str
    start_seconds: float
    end_seconds: float
    speaker: str
    overlap: bool
    events: tuple[str, ...]
    confidence: float
    review_state: str
    omitted_as_sponsor: bool
    sentence_end: bool = False
    sentence_suffix: str = ""


def segment_naturally(
    segments: Sequence[CanonicalSegment],
    *,
    full_asr_text: str,
    policy: NaturalSegmentationPolicy = DEFAULT_NATURAL_SEGMENTATION_POLICY,
    protected_word_ranges: Sequence[tuple[int, int]] = (),
    source_event_ranges: Sequence[tuple[float, float]] = (),
) -> NaturalSegmentationResult:
    """Regroup conserved words at Speaker, sentence, and pause boundaries."""
    original = tuple(segments)
    words = _flatten_segments(original)
    if not words:
        return NaturalSegmentationResult(
            segments=original,
            policy_version=policy.version,
            punctuation_alignment=_empty_punctuation_alignment(),
            soft_minimum_seconds=policy.soft_minimum_seconds,
            preferred_maximum_seconds=policy.preferred_maximum_seconds,
        )
    recovery_words = [
        {
            "text": word.text,
            "start_seconds": word.start_seconds,
            "end_seconds": word.end_seconds,
            "speaker": word.speaker,
            "overlap": word.overlap,
            "sentence_end": False,
            "sentence_suffix": "",
            "punctuation_confident": False,
        }
        for word in words
    ]
    punctuation_alignment = recover_sentence_punctuation(
        recovery_words,
        full_asr_text,
    )
    annotated = [
        replace(
            word,
            sentence_end=bool(recovered.get("sentence_end", False)),
            sentence_suffix=str(recovered.get("sentence_suffix", "")),
        )
        for word, recovered in zip(words, recovery_words, strict=True)
    ]
    source_event_boundary_indexes = _source_event_boundary_indexes(
        annotated,
        source_event_ranges,
    )
    groups = _partition_words(
        annotated,
        policy,
        protected_word_ranges,
        source_event_boundary_indexes,
    )
    rendered = tuple(
        _canonical_segment(index, group)
        for index, group in enumerate(groups, start=1)
    )
    if _word_sequence(rendered) != _word_sequence(original):
        raise ValueError("Natural segmentation changed source words")
    boundaries = _boundaries(
        groups,
        policy,
        protected_word_ranges,
        source_event_boundary_indexes,
    )
    return NaturalSegmentationResult(
        segments=rendered,
        policy_version=policy.version,
        punctuation_alignment=punctuation_alignment,
        soft_minimum_seconds=policy.soft_minimum_seconds,
        preferred_maximum_seconds=policy.preferred_maximum_seconds,
        speaker_boundary_count=sum(
            boundary.kind == "speaker" for boundary in boundaries
        ),
        source_event_boundary_count=sum(
            boundary.kind == "source_event" for boundary in boundaries
        ),
        sentence_boundary_count=sum(
            boundary.kind == "sentence" for boundary in boundaries
        ),
        pause_boundary_count=sum(
            boundary.kind == "pause" for boundary in boundaries
        ),
        fallback_boundary_count=sum(
            boundary.kind == "fallback" for boundary in boundaries
        ),
        boundaries=boundaries,
    )


def _flatten_segments(
    segments: tuple[CanonicalSegment, ...],
) -> list[_OwnedWord]:
    words: list[_OwnedWord] = []
    for source_segment_index, segment in enumerate(segments):
        source_words = segment.words or (
            (segment.text, segment.start_seconds, segment.end_seconds),
        )
        for text, start_seconds, end_seconds in source_words:
            words.append(
                _OwnedWord(
                    word_index=len(words),
                    source_segment_index=source_segment_index,
                    text=str(text),
                    start_seconds=float(start_seconds),
                    end_seconds=float(end_seconds),
                    speaker=segment.speaker_label,
                    overlap=segment.overlap,
                    events=segment.events,
                    confidence=segment.confidence,
                    review_state=segment.review_state,
                    omitted_as_sponsor=segment.omitted_as_sponsor,
                )
            )
    return words


def _partition_words(
    words: list[_OwnedWord],
    policy: NaturalSegmentationPolicy,
    protected_word_ranges: Sequence[tuple[int, int]],
    source_event_boundary_indexes: frozenset[int],
) -> list[list[_OwnedWord]]:
    groups: list[list[_OwnedWord]] = []
    start = 0
    while start < len(words):
        end = start + 1
        while (
            end < len(words)
            and words[end].speaker == words[start].speaker
            and not _is_source_event_boundary(
                words[end - 1],
                words[end],
                source_event_boundary_indexes,
            )
        ):
            end += 1
        run = words[start:end]
        groups.extend(
            run[left:right]
            for left, right in _partition_speaker_run(
                run,
                policy,
                protected_word_ranges,
            )
        )
        start = end
    return groups


def _partition_speaker_run(
    words: list[_OwnedWord],
    policy: NaturalSegmentationPolicy,
    protected_word_ranges: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    count = len(words)
    infinity = float("inf")
    costs = [infinity] * (count + 1)
    previous: list[int | None] = [None] * (count + 1)
    costs[0] = 0.0
    for end in range(1, count + 1):
        for start in range(end - 1, -1, -1):
            duration = words[end - 1].end_seconds - words[start].start_seconds
            if duration > policy.hard_maximum_seconds and end - start > 1:
                break
            if costs[start] == infinity:
                continue
            boundary_kind = _run_boundary_kind(
                words,
                end,
                policy,
                protected_word_ranges,
            )
            cost = costs[start] + _boundary_cost(boundary_kind)
            cost += abs(duration - policy.target_seconds) * 0.12
            whole_short_run = start == 0 and end == count
            if duration < policy.soft_minimum_seconds and not whole_short_run:
                cost += (policy.soft_minimum_seconds - duration) * 3.5
            if duration > policy.preferred_maximum_seconds:
                cost += (
                    duration - policy.preferred_maximum_seconds
                ) * 1.8
            if cost < costs[end]:
                costs[end] = cost
                previous[end] = start
    if previous[count] is None:
        raise ValueError("Natural segmentation could not partition a Speaker run")
    partitions: list[tuple[int, int]] = []
    end = count
    while end > 0:
        previous_start = previous[end]
        if previous_start is None:
            raise ValueError("Natural segmentation backtracking failed")
        partitions.append((previous_start, end))
        end = previous_start
    return list(reversed(partitions))


def _run_boundary_kind(
    words: list[_OwnedWord],
    end: int,
    policy: NaturalSegmentationPolicy,
    protected_word_ranges: Sequence[tuple[int, int]],
) -> BoundaryKind | Literal["final", "protected_fallback"]:
    if end == len(words):
        return "final"
    return _natural_boundary_kind(
        words[end - 1],
        words[end],
        policy,
        protected_word_ranges,
    )


def _natural_boundary_kind(
    previous: _OwnedWord,
    following: _OwnedWord,
    policy: NaturalSegmentationPolicy,
    protected_word_ranges: Sequence[tuple[int, int]],
) -> Literal["sentence", "pause", "fallback", "protected_fallback"]:
    if _is_protected_boundary(following.word_index, protected_word_ranges):
        return "protected_fallback"
    if previous.sentence_end:
        return "sentence"
    if (
        following.start_seconds - previous.end_seconds
        >= policy.pause_candidate_seconds
    ):
        return "pause"
    return "fallback"


def _boundary_cost(
    kind: BoundaryKind | Literal["final", "protected_fallback"],
) -> float:
    return {
        "sentence": 0.0,
        "pause": 1.0,
        "final": 0.0,
        "speaker": 0.0,
        "source_event": 0.0,
        "fallback": 9.0,
        "protected_fallback": 1_000.0,
    }[kind]


def _canonical_segment(
    index: int,
    words: list[_OwnedWord],
) -> CanonicalSegment:
    source_words = tuple(
        (word.text, word.start_seconds, word.end_seconds) for word in words
    )
    text = join_words(
        [
            {
                "text": _punctuated_text(word),
                "start_seconds": word.start_seconds,
                "end_seconds": word.end_seconds,
            }
            for word in words
        ]
    )
    review_states = [
        word.review_state
        for word in words
        if word.review_state != "accepted"
    ]
    return CanonicalSegment(
        segment_id=f"seg-{index:04d}",
        speaker_label=words[0].speaker,
        start_seconds=words[0].start_seconds,
        end_seconds=words[-1].end_seconds,
        text=text,
        words=source_words,
        overlap=any(word.overlap for word in words),
        events=tuple(
            dict.fromkeys(event for word in words for event in word.events)
        ),
        confidence=min(word.confidence for word in words),
        review_state=review_states[0] if review_states else "accepted",
        omitted_as_sponsor=any(word.omitted_as_sponsor for word in words),
    )


def _punctuated_text(word: _OwnedWord) -> str:
    if not word.sentence_suffix or word.text.endswith(word.sentence_suffix):
        return word.text
    return f"{word.text}{word.sentence_suffix}"


def _boundaries(
    groups: list[list[_OwnedWord]],
    policy: NaturalSegmentationPolicy,
    protected_word_ranges: Sequence[tuple[int, int]],
    source_event_boundary_indexes: frozenset[int],
) -> tuple[NaturalBoundary, ...]:
    boundaries: list[NaturalBoundary] = []
    for current, following in zip(groups, groups[1:], strict=False):
        left = current[-1]
        right = following[0]
        if left.speaker != right.speaker:
            kind: BoundaryKind = "speaker"
        elif _is_source_event_boundary(
            left,
            right,
            source_event_boundary_indexes,
        ):
            kind = "source_event"
        else:
            natural_kind = _natural_boundary_kind(
                left,
                right,
                policy,
                protected_word_ranges,
            )
            kind = (
                "fallback"
                if natural_kind == "protected_fallback"
                else natural_kind
            )
        boundaries.append(
            NaturalBoundary(
                kind=kind,
                after_word_index=left.word_index,
                before_word_index=right.word_index,
                boundary_seconds=round(
                    (left.end_seconds + right.start_seconds) / 2,
                    6,
                ),
            )
        )
    return tuple(boundaries)


def _is_source_event_boundary(
    left: _OwnedWord,
    right: _OwnedWord,
    source_event_boundary_indexes: frozenset[int] = frozenset(),
) -> bool:
    return (
        right.word_index in source_event_boundary_indexes
        or (
            left.source_segment_index != right.source_segment_index
            and bool(left.events or right.events)
        )
    )


def _source_event_boundary_indexes(
    words: Sequence[_OwnedWord],
    source_event_ranges: Sequence[tuple[float, float]],
) -> frozenset[int]:
    return frozenset(
        right.word_index
        for left, right in zip(words, words[1:], strict=False)
        if any(
            event_start <= right.start_seconds + 1e-6
            and event_end >= left.end_seconds - 1e-6
            for event_start, event_end in source_event_ranges
        )
    )


def _is_protected_boundary(
    word_index: int,
    protected_word_ranges: Sequence[tuple[int, int]],
) -> bool:
    return any(
        start_word_index < word_index < end_word_index
        for start_word_index, end_word_index in protected_word_ranges
    )


def _word_sequence(
    segments: Sequence[CanonicalSegment],
) -> tuple[tuple[str, float, float], ...]:
    return tuple(word for segment in segments for word in segment.words)


def _empty_punctuation_alignment() -> dict[str, Any]:
    return {
        "aligned_word_count": 0,
        "transcript_token_count": 0,
        "matched_word_count": 0,
        "confident_matched_word_count": 0,
        "coverage": 0.0,
        "confident_coverage": 0.0,
        "sentence_end_count": 0,
        "mismatch_ranges": [],
        "low_confidence_ranges": [],
    }
