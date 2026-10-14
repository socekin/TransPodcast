from __future__ import annotations

from transpodcast.adjudication_judges import measure_fragment_voiceprints
from transpodcast.boundary_adjudication import OwnershipDispute


def _dispute(
    *,
    start: float = 100.0,
    end: float = 100.5,
    current: str = "speaker-2",
    counterpart: str = "speaker-1",
    candidate_owners: tuple[str, ...] = (),
    group_id: str = "",
) -> OwnershipDispute:
    return OwnershipDispute(
        dispute_id=f"dispute-{start}-{current}",
        words=(
            {"text": "and", "start_seconds": start, "end_seconds": end},
        ),
        current_owner=current,
        counterpart=counterpart,
        candidate_owners=candidate_owners,
        group_id=group_id,
        context_before_owner=counterpart,
        context_after_owner=counterpart,
        context_before=("compute", "bound"),
        context_after=("the", "entire", "industry"),
    )


CENTROIDS = {
    "speaker-1": (1.0, 0.0),
    "speaker-2": (0.0, 1.0),
    "speaker-3": (-1.0, 0.0),
}


def test_voiceprint_picks_the_closest_candidate() -> None:
    dispute = _dispute()

    verdicts = measure_fragment_voiceprints(
        [dispute],
        {dispute.dispute_id: (0.99, 0.1)},
        CENTROIDS,
    )

    verdict = verdicts[dispute.dispute_id]
    assert verdict is not None
    assert verdict.owner == "speaker-1"
    assert verdict.scores["speaker-1"] > verdict.scores["speaker-2"]


def test_voiceprint_scores_every_permissible_owner() -> None:
    dispute = _dispute(
        candidate_owners=("speaker-1", "speaker-2", "speaker-3")
    )

    verdicts = measure_fragment_voiceprints(
        [dispute],
        {dispute.dispute_id: (-0.99, 0.1)},
        CENTROIDS,
    )

    verdict = verdicts[dispute.dispute_id]
    assert verdict is not None
    assert verdict.owner == "speaker-3"
    assert set(verdict.scores) == {
        "speaker-1",
        "speaker-2",
        "speaker-3",
    }


def test_voiceprint_compares_reliable_owners_when_one_centroid_is_missing(
) -> None:
    dispute = _dispute(
        candidate_owners=("speaker-1", "speaker-2", "speaker-3")
    )

    verdicts = measure_fragment_voiceprints(
        [dispute],
        {dispute.dispute_id: (0.99, 0.1)},
        {
            "speaker-1": CENTROIDS["speaker-1"],
            "speaker-2": CENTROIDS["speaker-2"],
        },
    )

    verdict = verdicts[dispute.dispute_id]
    assert verdict is not None
    assert verdict.owner == "speaker-1"
    assert set(verdict.scores) == {"speaker-1", "speaker-2"}


def test_voiceprint_abstains_below_the_duration_floor() -> None:
    dispute = _dispute(start=100.0, end=100.2)

    verdicts = measure_fragment_voiceprints(
        [dispute],
        {dispute.dispute_id: (0.99, 0.1)},
        CENTROIDS,
    )

    assert verdicts[dispute.dispute_id] is None


def test_voiceprint_abstains_on_noise_level_scores() -> None:
    # A calibrated short fragment scored 0.04 vs 0.13 — noise,
    # not evidence. A measurement that close to zero must not vote.
    dispute = _dispute()

    verdicts = measure_fragment_voiceprints(
        [dispute],
        {dispute.dispute_id: (-1.0, 0.15)},
        CENTROIDS,
    )

    assert verdicts[dispute.dispute_id] is None


def test_voiceprint_abstains_without_an_embedding_or_centroid() -> None:
    dispute = _dispute()

    missing_embedding = measure_fragment_voiceprints(
        [dispute],
        {},
        CENTROIDS,
    )
    missing_centroid = measure_fragment_voiceprints(
        [dispute],
        {dispute.dispute_id: (0.99, 0.1)},
        {"speaker-1": (1.0, 0.0)},
    )

    assert missing_embedding[dispute.dispute_id] is None
    assert missing_centroid[dispute.dispute_id] is None
