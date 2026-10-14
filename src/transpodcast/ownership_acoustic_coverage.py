"""Candidate acoustic coverage before ownership judgment; no identity mutation."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from transpodcast.speaker_ownership_continuity import _overlap_seconds
from transpodcast.speaker_ownership_v15 import (
    OwnershipVoiceprintEvidence,
    OwnershipWord,
    SpeakerOwnershipRegion,
)


@dataclass(frozen=True)
class OwnershipAcousticWindow:
    name: str
    start_seconds: float
    end_seconds: float
    first_word_index: int
    last_word_index: int
    overlap_fraction: float
    current_owner: str


def plan_ownership_acoustic_windows(
    regions: Sequence[SpeakerOwnershipRegion],
    raw_turns: Sequence[tuple[str, float, float]],
    speaker_mapping: dict[str, str],
    source_identity: str,
    *,
    window_seconds: float = 4.0,
    minimum_seconds: float = 1.5,
) -> tuple[OwnershipAcousticWindow, ...]:
    """Tile existing owner runs without borrowing audio for brief interjections.

    Overlap is retained as a quality signal. A mixed window may help diagnose a
    source problem, but its dominant embedding cannot label every word within it.
    The caller must bind cached measurements to source, model, and anchor identity.
    """
    if not 0 < minimum_seconds <= window_seconds or not math.isfinite(window_seconds):
        raise ValueError("Acoustic window limits must be finite and positive")
    windows: dict[str, OwnershipAcousticWindow] = {}
    for region in regions:
        groups = itertools.groupby(region.words, key=lambda w: w.current_owner)
        for _, grouped in groups:
            run = list(grouped)
            left, right = run[0].start_seconds, max(w.end_seconds for w in run)
            if right - left < minimum_seconds:
                continue
            starts = [
                left + window_seconds * n
                for n in range(int((right - left) // window_seconds) + 1)
                if right - (left + window_seconds * n) >= minimum_seconds
            ]
            if right - min(right, starts[-1] + window_seconds) > 0:
                starts.append(max(left, right - window_seconds))
            for start in sorted(set(starts)):
                end = min(start + window_seconds, right)
                indexes = [
                    w.word_index
                    for w in run
                    if w.start_seconds >= start and w.end_seconds <= end
                ]
                if not indexes or indexes != list(range(indexes[0], indexes[-1] + 1)):
                    continue
                payload = json.dumps(
                    [source_identity, start, end],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                name = "window-" + hashlib.sha256(payload.encode()).hexdigest()[:18]
                windows[name] = OwnershipAcousticWindow(
                    name,
                    start,
                    end,
                    indexes[0],
                    indexes[-1],
                    _overlap_seconds(start, end, raw_turns, speaker_mapping)
                    / (end - start),
                    run[0].current_owner,
                )
    return tuple(windows.values())


def acoustic_window_evidence(
    window: OwnershipAcousticWindow,
    scores: Mapping[str, float],
    *,
    minimum_score: float = 0.45,
    minimum_margin: float = 0.15,
    maximum_overlap_fraction: float = 0.25,
) -> OwnershipVoiceprintEvidence | None:
    """Accept separated identity evidence, never treating similarity as probability.

    Scores must cover the established episode anchors. Thresholds are conservative
    trial parameters, not a calibrated accuracy guarantee. Short unmeasured turns
    keep their original evidence and remain the final Reviewer's responsibility.
    """
    if (
        len(scores) < 2
        or not all(math.isfinite(s) for s in scores.values())
        or not math.isfinite(window.overlap_fraction)
        or window.overlap_fraction > maximum_overlap_fraction
    ):
        return None
    ranked = sorted(scores, key=lambda s: scores[s], reverse=True)
    if (
        scores[ranked[0]] < minimum_score
        or scores[ranked[0]] - scores[ranked[1]] < minimum_margin
    ):
        return None
    return OwnershipVoiceprintEvidence(
        window.name,
        window.first_word_index,
        window.last_word_index + 1,
        ranked[0],
        tuple(sorted(scores.items())),
    )


def project_acoustic_word_evidence(
    window: OwnershipAcousticWindow,
    scores: Mapping[str, float],
    words: Sequence[OwnershipWord],
) -> tuple[OwnershipVoiceprintEvidence, ...]:
    """Do not turn a dominant window embedding into votes on mixed speech words.

    Single-identity raw support is a coverage condition, not an identity label:
    a clean raw-a word may still have a measured identity of b. Missing support,
    simultaneous/adjacent multi-identity support, and zero-duration words receive
    no new word-level vote and remain available for contextual final review.
    """
    evidence = acoustic_window_evidence(window, scores)
    if evidence is None:
        return ()
    indexes = [
        w.word_index
        for w in words
        if window.first_word_index <= w.word_index <= window.last_word_index
        and window.start_seconds
        <= w.start_seconds
        < w.end_seconds
        <= window.end_seconds
        and len({s for s, duration in w.speaker_seconds if duration > 0}) == 1
    ]
    spans = []
    for _, grouped in itertools.groupby(enumerate(indexes), key=lambda v: v[1] - v[0]):
        span = [index for _, index in grouped]
        spans.append(
            replace(
                evidence,
                dispute_id=f"{window.name}::words-{span[0]}-{span[-1] + 1}",
                start_word_index=span[0],
                end_word_index=span[-1] + 1,
            )
        )
    return tuple(spans)


def find_ownership_edge_gaps(
    regions: Sequence[SpeakerOwnershipRegion],
    original_owners: Sequence[str],
    proposed_owners: Sequence[str],
) -> tuple[tuple[int, int], ...]:
    """Request evidence when a repair creates a turn at an arbitrary scope edge.

    Returned half-open ranges cover the entire original owner run. They are
    requests for additional measurement/review, never automatic owner changes.
    Adjacent request partitions with agreeing final owners do not create a gap.
    """
    if len(original_owners) != len(proposed_owners):
        raise ValueError("Ownership timelines must have equal lengths")
    gaps: set[tuple[int, int]] = set()
    for region in regions:
        if (
            not 0
            <= region.start_word_index
            < region.end_word_index
            <= len(original_owners)
        ):
            raise ValueError("Ownership region exceeds the immutable timeline")
        for boundary in (region.start_word_index, region.end_word_index):
            if not 0 < boundary < len(original_owners):
                continue
            if (
                original_owners[boundary - 1] != original_owners[boundary]
                or proposed_owners[boundary - 1] == proposed_owners[boundary]
            ):
                continue
            start, end = boundary - 1, boundary + 1
            while start > 0 and original_owners[start - 1] == original_owners[boundary]:
                start -= 1
            while (
                end < len(original_owners)
                and original_owners[end] == original_owners[boundary]
            ):
                end += 1
            gaps.add((start, end))
    return tuple(sorted(gaps))
