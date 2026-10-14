"""The voiceprint judge for Boundary Ownership Adjudication candidates.

It scores a disputed fragment's TitaNet embedding against the permissible
Speakers' consolidated centroids and abstains when the fragment is too short
to measure reliably. The retired V9-V14 discourse judge no longer runs for
any Processing Run; its stored Decision Records remain data.
"""

from __future__ import annotations

from transpodcast.boundary_adjudication import (
    OwnershipDispute,
    VoiceprintVerdict,
)
from transpodcast.similarity import cosine_similarity

MINIMUM_FRAGMENT_SECONDS = 0.3
# Sub-second fragments can embed as noise; a measurement whose best
# score is below this floor is no evidence at all, so the judge
# abstains instead of casting a noise vote (historical calibration: the
# 0.32s "right And" fragment scored 0.04 vs 0.13).
MINIMUM_CONFIDENCE_SCORE = 0.35


def measure_fragment_voiceprints(
    disputes: list[OwnershipDispute],
    fragment_embeddings: dict[str, tuple[float, ...]],
    centroids: dict[str, tuple[float, ...]],
    *,
    minimum_fragment_seconds: float = MINIMUM_FRAGMENT_SECONDS,
    minimum_confidence_score: float = MINIMUM_CONFIDENCE_SCORE,
) -> dict[str, VoiceprintVerdict | None]:
    verdicts: dict[str, VoiceprintVerdict | None] = {}
    for dispute in disputes:
        verdicts[dispute.dispute_id] = _measure_one(
            dispute,
            fragment_embeddings.get(dispute.dispute_id, ()),
            centroids,
            minimum_fragment_seconds=minimum_fragment_seconds,
            minimum_confidence_score=minimum_confidence_score,
        )
    return verdicts


def _measure_one(
    dispute: OwnershipDispute,
    embedding: tuple[float, ...],
    centroids: dict[str, tuple[float, ...]],
    *,
    minimum_fragment_seconds: float,
    minimum_confidence_score: float,
) -> VoiceprintVerdict | None:
    span = dispute.end_seconds - dispute.start_seconds
    if span < minimum_fragment_seconds - 1e-9:
        return None
    if not embedding:
        return None
    scores: dict[str, float] = {}
    for label in dispute.permissible_owners:
        centroid = centroids.get(label)
        if not centroid:
            continue
        scores[label] = round(
            cosine_similarity(list(embedding), list(centroid)),
            6,
        )
    if len(scores) < 2:
        return None
    if max(scores.values()) < minimum_confidence_score:
        return None
    owner = max(scores, key=lambda label: scores[label])
    return VoiceprintVerdict(owner=owner, scores=scores)
