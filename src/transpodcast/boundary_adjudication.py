"""Boundary Ownership Adjudication candidates and their acoustic evidence.

Source Analysis selects bounded short-boundary and sparse-label ownership
candidates and measures each fragment's voiceprint. Since V15 those disputes
are evidence for Speaker Ownership Risk Regions; they no longer go to the
retired V9-V14 two-judge verdict engine. Historical Decision Records written
by that engine remain stored data only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

ADJUDICATION_POLICY_VERSION = "boundary-ownership-adjudication-v3"
CONTINUITY_ADJUDICATION_POLICY_VERSION = "boundary-ownership-adjudication-v4"
SHORT_BOUNDARY_TRIGGER = "short-boundary"
SPARSE_LABEL_TRIGGER = "sparse-label"


@dataclass(frozen=True)
class AdjudicationPolicy:
    maximum_dispute_seconds: float = 2.0
    maximum_sparse_segment_count: int = 2


@dataclass(frozen=True)
class OwnershipDispute:
    dispute_id: str
    words: tuple[dict[str, Any], ...]
    current_owner: str
    counterpart: str
    candidate_owners: tuple[str, ...] = ()
    trigger_reasons: tuple[str, ...] = ()
    group_id: str = ""
    context_before_owner: str = ""
    context_after_owner: str = ""
    context_before: tuple[str, ...] = ()
    context_after: tuple[str, ...] = ()

    @property
    def start_seconds(self) -> float:
        return float(self.words[0]["start_seconds"])

    @property
    def end_seconds(self) -> float:
        return float(self.words[-1]["end_seconds"])

    @property
    def permissible_owners(self) -> tuple[str, ...]:
        if self.candidate_owners:
            return self.candidate_owners
        return tuple(sorted({self.current_owner, self.counterpart}))

    def to_payload(self) -> dict[str, Any]:
        return {
            "dispute_id": self.dispute_id,
            "words": [dict(word) for word in self.words],
            "current_owner": self.current_owner,
            "counterpart": self.counterpart,
            "candidate_owners": list(self.permissible_owners),
            "trigger_reasons": list(self.trigger_reasons),
            "group_id": self.group_id or self.dispute_id,
            "context_before_owner": self.context_before_owner,
            "context_after_owner": self.context_after_owner,
            "context_before": list(self.context_before),
            "context_after": list(self.context_after),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OwnershipDispute:
        counterpart = str(payload["counterpart"])
        return cls(
            dispute_id=str(payload["dispute_id"]),
            words=tuple(dict(word) for word in payload["words"]),
            current_owner=str(payload["current_owner"]),
            counterpart=counterpart,
            candidate_owners=tuple(
                str(item)
                for item in payload.get("candidate_owners", [])
            ),
            trigger_reasons=tuple(
                str(item)
                for item in payload.get("trigger_reasons", [])
            ),
            group_id=str(
                payload.get("group_id") or payload["dispute_id"]
            ),
            context_before_owner=str(
                payload.get("context_before_owner") or counterpart
            ),
            context_after_owner=str(
                payload.get("context_after_owner") or counterpart
            ),
            context_before=tuple(
                str(item) for item in payload.get("context_before", [])
            ),
            context_after=tuple(
                str(item) for item in payload.get("context_after", [])
            ),
        )


@dataclass(frozen=True)
class VoiceprintVerdict:
    owner: str
    scores: dict[str, float]

    def to_payload(self) -> dict[str, Any]:
        return {"owner": self.owner, "scores": dict(self.scores)}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> VoiceprintVerdict:
        return cls(
            owner=str(payload["owner"]),
            scores={
                str(label): float(score)
                for label, score in payload["scores"].items()
            },
        )


def find_ownership_disputes(
    segments: list[dict[str, Any]],
    *,
    policy: AdjudicationPolicy | None = None,
) -> list[OwnershipDispute]:
    """Select bounded high-risk ownership candidates."""
    policy = policy or AdjudicationPolicy()
    label_counts = Counter(
        str(segment["speaker_label"]) for segment in segments
    )
    disputes: list[OwnershipDispute] = []
    previous_candidate_index: int | None = None
    previous_candidate_end: float | None = None
    group_id = ""
    for index, segment in enumerate(segments):
        before = segments[index - 1] if index > 0 else None
        after = segments[index + 1] if index + 1 < len(segments) else None
        owner = str(segment["speaker_label"])
        neighbors = {
            str(item["speaker_label"])
            for item in (before, after)
            if item is not None
        }
        start_seconds = float(segment["start_seconds"])
        end_seconds = float(segment["end_seconds"])
        span = end_seconds - start_seconds
        reasons: list[str] = []
        if (
            span <= policy.maximum_dispute_seconds
            and any(label != owner for label in neighbors)
        ):
            reasons.append(SHORT_BOUNDARY_TRIGGER)
        if label_counts[owner] <= policy.maximum_sparse_segment_count:
            reasons.append(SPARSE_LABEL_TRIGGER)
        if not reasons:
            continue
        candidate_owners = tuple(sorted({owner, *neighbors}))
        follows_candidate = (
            previous_candidate_index is not None
            and index == previous_candidate_index + 1
        )
        overlaps_candidate = (
            previous_candidate_end is not None
            and start_seconds < previous_candidate_end
        )
        if not follows_candidate and not overlaps_candidate:
            group_id = f"group-{segment['start_seconds']}"
        previous_candidate_index = index
        previous_candidate_end = max(
            previous_candidate_end or end_seconds,
            end_seconds,
        )
        legacy_counterpart = next(
            (
                label
                for label in candidate_owners
                if label != owner
            ),
            owner,
        )
        disputes.append(
            OwnershipDispute(
                dispute_id=(
                    f"dispute-{segment['start_seconds']}-{owner}"
                ),
                words=tuple(dict(word) for word in segment["words"]),
                current_owner=owner,
                counterpart=legacy_counterpart,
                candidate_owners=candidate_owners,
                trigger_reasons=tuple(reasons),
                group_id=group_id,
                context_before_owner=(
                    str(before["speaker_label"]) if before else ""
                ),
                context_after_owner=(
                    str(after["speaker_label"]) if after else ""
                ),
                context_before=tuple(
                    str(word["text"])
                    for word in list(
                        before.get("words", []) if before else []
                    )[-12:]
                ),
                context_after=tuple(
                    str(word["text"])
                    for word in list(
                        after.get("words", []) if after else []
                    )[:12]
                ),
            )
        )
    sparse_alternates: dict[tuple[str, str], set[str]] = {}
    for dispute in disputes:
        if SPARSE_LABEL_TRIGGER not in dispute.trigger_reasons:
            continue
        sparse_alternates.setdefault(
            (dispute.group_id, dispute.current_owner), set()
        ).update(
            owner
            for owner in dispute.permissible_owners
            if owner != dispute.current_owner
        )
    normalized: list[OwnershipDispute] = []
    for dispute in disputes:
        permissible_owners = set(dispute.permissible_owners)
        # Include every segment in a same-label sparse run without
        # leaking unrelated owners across the wider decision group.
        if (
            len(permissible_owners) == 1
            and SPARSE_LABEL_TRIGGER in dispute.trigger_reasons
        ):
            alternatives = sparse_alternates.get(
                (dispute.group_id, dispute.current_owner), set()
            )
            if len(alternatives) == 1:
                permissible_owners.update(alternatives)
        ordered_owners = tuple(sorted(permissible_owners))
        normalized.append(
            replace(
                dispute,
                candidate_owners=ordered_owners,
                counterpart=next(
                    (
                        owner
                        for owner in ordered_owners
                        if owner != dispute.current_owner
                    ),
                    dispute.current_owner,
                ),
            )
        )
    return normalized
