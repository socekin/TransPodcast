"""Episode-local Speaker Consolidation and Voice Reference selection.

This is the speaker-pipeline-v9 replacement for the retired v8 policy.
Two decisions from the v6-v8 retrospective shape it:

- Merge evidence is judged by the median similarity of screened windows
  with a unanimous vote, so a single contaminated window can no longer
  veto a correct merge (the v8 production failure).
- Voice References avoid the intro and outro zones and turns that overlap
  another Speaker, then fall back to the plain v6 longest-turn rule, so a
  reference can never silently land beside the outro music again.

Consolidation windows are deliberately NOT position-screened: the phantom
labels this stage exists to merge live inside the cold open, so their
windows must keep their vote. Position screening applies only to Voice
Reference selection.

Every verdict, including "kept", is returned as a Decision Record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any

from transpodcast.decision_records import DecisionRecord
from transpodcast.similarity import cosine_similarity

CONSOLIDATION_POLICY_VERSION = "speaker-consolidation-v2"
REFERENCE_SELECTION_POLICY_VERSION = "voice-reference-selection-v1"


@dataclass(frozen=True)
class ConsolidationWindow:
    window_id: str
    speaker_label: str
    start_seconds: float
    end_seconds: float
    clean: bool
    embedding: tuple[float, ...]

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass(frozen=True)
class ConsolidationPolicy:
    minimum_window_seconds: float = 2.0
    minimum_window_count: int = 3
    merge_median_similarity: float = 0.65
    minimum_margin: float = 0.2


@dataclass(frozen=True)
class ConsolidationResult:
    mapping: dict[str, str]
    records: tuple[DecisionRecord, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReferenceSelectionPolicy:
    head_trim_seconds: float = 90.0
    tail_trim_seconds: float = 90.0
    maximum_reference_seconds: float = 12.0
    minimum_reference_seconds: float = 1.0


@dataclass(frozen=True)
class ReferenceSelectionResult:
    selections: dict[str, dict[str, float]]
    records: tuple[DecisionRecord, ...] = field(default_factory=tuple)


def consolidate_episode_speakers(
    windows: list[ConsolidationWindow],
    *,
    policy: ConsolidationPolicy | None = None,
) -> ConsolidationResult:
    policy = policy or ConsolidationPolicy()
    grouped: dict[str, list[ConsolidationWindow]] = {}
    for window in windows:
        grouped.setdefault(window.speaker_label, []).append(window)

    screened: dict[str, list[ConsolidationWindow]] = {}
    screened_out: dict[str, list[dict[str, Any]]] = {}
    for label, values in grouped.items():
        kept: list[ConsolidationWindow] = []
        rejected: list[dict[str, Any]] = []
        for window in values:
            reason = _screen_reason(window, policy)
            if reason is None:
                kept.append(window)
            else:
                rejected.append(
                    {"window_id": window.window_id, "reason": reason}
                )
        screened[label] = kept
        screened_out[label] = rejected

    def total_duration(label: str) -> float:
        return sum(
            window.duration_seconds for window in screened[label]
        )

    ordered = sorted(
        grouped,
        key=lambda label: (total_duration(label), label),
    )
    mapping = {label: label for label in grouped}
    records: list[DecisionRecord] = []
    # A label that has absorbed another is frozen: allowing it to merge
    # onward would chain A->B->C without ever comparing A against C,
    # and similarity is not transitive ("never false-merge").
    absorbers: set[str] = set()

    for label in ordered:
        candidate_windows = screened[label]
        if label in absorbers:
            records.append(
                DecisionRecord(
                    kind="consolidation",
                    policy_version=CONSOLIDATION_POLICY_VERSION,
                    subject={"source_label": label},
                    evidence={
                        "window_count": len(candidate_windows),
                        "screened_out_windows": screened_out[label],
                    },
                    verdict="kept",
                    rationale=(
                        "Absorbed another label and is frozen as a "
                        "canonical Speaker; onward merges would chain "
                        "unverified identities."
                    ),
                )
            )
            continue
        targets = [
            other
            for other in grouped
            if other != label
            and mapping[other] == other
            and screened[other]
            and total_duration(other) > total_duration(label)
        ]
        verdict, merge_target, evidence, rationale = (
            _evaluate_candidate(
                candidate_windows,
                targets={
                    other: _centroid(screened[other])
                    for other in targets
                },
                policy=policy,
            )
        )
        evidence["screened_out_windows"] = screened_out[label]
        if merge_target is not None:
            mapping[label] = merge_target
            evidence["merged_into"] = merge_target
            absorbers.add(merge_target)
        records.append(
            DecisionRecord(
                kind="consolidation",
                policy_version=CONSOLIDATION_POLICY_VERSION,
                subject={"source_label": label},
                evidence=evidence,
                verdict=verdict,
                rationale=rationale,
            )
        )

    resolved = {
        label: _resolve(mapping, label) for label in mapping
    }
    return ConsolidationResult(
        mapping=resolved,
        records=tuple(records),
    )


def select_voice_references(
    turns: list[dict[str, Any]],
    *,
    source_duration_seconds: float,
    policy: ReferenceSelectionPolicy | None = None,
) -> ReferenceSelectionResult:
    policy = policy or ReferenceSelectionPolicy()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for turn in turns:
        grouped.setdefault(str(turn["speaker"]), []).append(turn)

    records: list[DecisionRecord] = []
    selections: dict[str, dict[str, float]] = {}
    for label, values in sorted(grouped.items()):
        eligible: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for turn in values:
            reason = _turn_rejection_reason(
                turn,
                label=label,
                turns=turns,
                source_duration_seconds=source_duration_seconds,
                policy=policy,
            )
            if reason is None:
                eligible.append(turn)
            else:
                rejected.append(
                    {
                        "start_seconds": float(turn["start_seconds"]),
                        "end_seconds": float(turn["end_seconds"]),
                        "reason": reason,
                    }
                )
        pool = eligible
        fallback = not pool
        if fallback:
            # The plain v6 rule: the longest raw turn, no filters. An
            # episode must always yield a reference.
            pool = values
        selected = max(
            pool,
            key=lambda turn: float(turn["end_seconds"])
            - float(turn["start_seconds"]),
        )
        start = float(selected["start_seconds"])
        duration = min(
            policy.maximum_reference_seconds,
            float(selected["end_seconds"]) - start,
        )
        if duration < policy.minimum_reference_seconds and not fallback:
            fallback = True
            selected = max(
                values,
                key=lambda turn: float(turn["end_seconds"])
                - float(turn["start_seconds"]),
            )
            start = float(selected["start_seconds"])
            duration = min(
                policy.maximum_reference_seconds,
                float(selected["end_seconds"]) - start,
            )
        selections[label] = {
            "start_seconds": start,
            "duration_seconds": duration,
        }
        records.append(
            DecisionRecord(
                kind="reference-selection",
                policy_version=REFERENCE_SELECTION_POLICY_VERSION,
                subject={"speaker": label},
                evidence={
                    "start_seconds": start,
                    "duration_seconds": duration,
                    "eligible_turns": len(eligible),
                    "rejected_turns": rejected,
                    "fallback": fallback,
                },
                verdict="selected",
                rationale=(
                    "Longest turn surviving the position and overlap "
                    "filters."
                    if not fallback
                    else "No turn survived the filters; the plain v6 "
                    "longest-turn rule was applied."
                ),
            )
        )
        if fallback:
            records.append(
                DecisionRecord(
                    kind="fallback",
                    policy_version=REFERENCE_SELECTION_POLICY_VERSION,
                    subject={"speaker": label},
                    evidence={"rejected_turns": rejected},
                    verdict="plain-v6-selection",
                    rationale=(
                        "Every turn was inside a trimmed zone, "
                        "overlapped another Speaker, or was too short."
                    ),
                )
            )
    return ReferenceSelectionResult(
        selections=selections,
        records=tuple(records),
    )


def _screen_reason(
    window: ConsolidationWindow,
    policy: ConsolidationPolicy,
) -> str | None:
    if not window.clean:
        return "overlaps-another-speaker"
    if window.duration_seconds < policy.minimum_window_seconds:
        return "shorter-than-minimum"
    if not window.embedding:
        return "missing-embedding"
    return None


def _evaluate_candidate(
    candidate_windows: list[ConsolidationWindow],
    *,
    targets: dict[str, tuple[float, ...]],
    policy: ConsolidationPolicy,
) -> tuple[str, str | None, dict[str, Any], str]:
    evidence: dict[str, Any] = {
        "window_count": len(candidate_windows),
    }
    if not targets:
        return (
            "kept",
            None,
            evidence,
            "Dominant label retained as the canonical Speaker.",
        )
    if len(candidate_windows) < policy.minimum_window_count:
        return (
            "kept",
            None,
            evidence,
            "Too few screened windows to justify a merge.",
        )
    similarities: dict[str, list[float]] = {
        label: [] for label in targets
    }
    votes: dict[str, str] = {}
    for window in candidate_windows:
        scored = {
            label: cosine_similarity(
                list(window.embedding), list(centroid)
            )
            for label, centroid in targets.items()
        }
        winner = max(scored, key=lambda label: scored[label])
        votes[window.window_id] = winner
        for label, value in scored.items():
            similarities[label].append(value)
    evidence["window_votes"] = votes
    medians = {
        label: round(median(values), 6)
        for label, values in similarities.items()
    }
    evidence["median_similarities"] = medians
    winners = set(votes.values())
    if len(winners) != 1:
        return (
            "kept",
            None,
            evidence,
            "Window votes were not unanimous.",
        )
    winner = winners.pop()
    best = medians[winner]
    runners_up = [
        value for label, value in medians.items() if label != winner
    ]
    margin = round(
        best - max(runners_up) if runners_up else best,
        6,
    )
    evidence["median_similarity"] = best
    evidence["margin"] = margin
    if best < policy.merge_median_similarity:
        return (
            "kept",
            None,
            evidence,
            "Median similarity is below the merge threshold.",
        )
    if margin < policy.minimum_margin:
        return (
            "kept",
            None,
            evidence,
            "The margin over the runner-up Speaker is too small.",
        )
    return (
        "merged",
        winner,
        evidence,
        "Every screened window voted for the same Speaker and the "
        "median similarity cleared the merge threshold.",
    )


def _centroid(
    windows: list[ConsolidationWindow],
) -> tuple[float, ...]:
    dimension = len(windows[0].embedding)
    return tuple(
        sum(window.embedding[index] for window in windows)
        / len(windows)
        for index in range(dimension)
    )


def _resolve(mapping: dict[str, str], label: str) -> str:
    seen = set()
    while mapping[label] != label and label not in seen:
        seen.add(label)
        label = mapping[label]
    return label


def _turn_rejection_reason(
    turn: dict[str, Any],
    *,
    label: str,
    turns: list[dict[str, Any]],
    source_duration_seconds: float,
    policy: ReferenceSelectionPolicy,
) -> str | None:
    start = float(turn["start_seconds"])
    end = float(turn["end_seconds"])
    if start < policy.head_trim_seconds:
        return "inside-intro-zone"
    if end > source_duration_seconds - policy.tail_trim_seconds:
        return "inside-outro-zone"
    if end - start < policy.minimum_reference_seconds:
        return "shorter-than-minimum"
    for other in turns:
        if str(other["speaker"]) == label:
            continue
        if min(end, float(other["end_seconds"])) > max(
            start, float(other["start_seconds"])
        ):
            return "overlaps-another-speaker"
    return None
