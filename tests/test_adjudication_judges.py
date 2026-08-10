from __future__ import annotations

import pytest

from transpodcast.adjudication_judges import (
    discourse_question,
    measure_fragment_voiceprints,
    parse_discourse_verdicts,
)
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


def test_discourse_question_carries_the_context_and_schema() -> None:
    dispute = _dispute()

    instructions, source_data, schema = discourse_question([dispute])

    assert "permissible owner" in instructions
    group = source_data["groups"][0]
    entry = group["candidates"][0]
    assert entry["disputed_words"] == "and"
    assert group["context_before"] == "compute bound"
    assert group["context_after"] == "the entire industry"
    assert entry["current_owner"] == "speaker-2"
    assert entry["candidate_owners"] == ["speaker-1", "speaker-2"]
    assert entry["context_before_speaker"] == "speaker-1"
    assert entry["context_before"] == "compute bound"
    assert entry["context_after_speaker"] == "speaker-1"
    assert entry["context_after"] == "the entire industry"
    assert group["context_before_speaker"] == "speaker-1"
    assert group["context_after_speaker"] == "speaker-1"
    group_schema = schema["properties"]["groups"]["items"]
    verdict_schema = group_schema["properties"]["verdicts"]["items"]
    assert "group_id" in group_schema["properties"]
    assert "owner" in verdict_schema["properties"]
    assert "verdict" not in verdict_schema["properties"]


def test_adjacent_disputes_are_presented_as_one_local_group() -> None:
    first = _dispute(start=100.0, group_id="group-100")
    second = _dispute(start=100.6, group_id="group-100")

    _, source_data, _ = discourse_question([first, second])

    assert len(source_data["groups"]) == 1
    group = source_data["groups"][0]
    assert group["group_id"] == "group-100"
    assert [
        item["dispute_id"] for item in group["candidates"]
    ] == [first.dispute_id, second.dispute_id]


def test_discourse_verdicts_map_to_speakers() -> None:
    continuation = _dispute(start=100.0)
    interjection = _dispute(start=200.0)

    verdicts = parse_discourse_verdicts(
        {
            "verdicts": [
                {
                    "dispute_id": continuation.dispute_id,
                    "verdict": "continuation",
                    "rationale": "Continues the sentence.",
                },
                {
                    "dispute_id": interjection.dispute_id,
                    "verdict": "interjection",
                    "rationale": "A separate utterance.",
                }
            ],
        },
        [continuation, interjection],
    )

    assert verdicts[continuation.dispute_id].owner == "speaker-1"
    assert verdicts[interjection.dispute_id].owner == "speaker-2"


def test_discourse_can_choose_any_permissible_owner() -> None:
    dispute = _dispute(
        candidate_owners=("speaker-1", "speaker-2", "speaker-3")
    )

    verdicts = parse_discourse_verdicts(
        {
            "verdicts": [
                {
                    "dispute_id": dispute.dispute_id,
                    "owner": "speaker-3",
                    "rationale": "The words continue speaker-3's turn.",
                }
            ]
        },
        [dispute],
    )

    assert verdicts[dispute.dispute_id].owner == "speaker-3"


def test_an_incomplete_group_abstains_without_discarding_other_groups(
) -> None:
    first = _dispute(start=100.0, group_id="group-one")
    second = _dispute(start=100.6, group_id="group-one")
    independent = _dispute(start=200.0, group_id="group-two")

    verdicts = parse_discourse_verdicts(
        {
            "groups": [
                {
                    "group_id": "group-one",
                    "verdicts": [
                        {
                            "dispute_id": first.dispute_id,
                            "owner": first.current_owner,
                            "rationale": "Only half of the group.",
                        }
                    ],
                },
                {
                    "group_id": "group-two",
                    "verdicts": [
                        {
                            "dispute_id": independent.dispute_id,
                            "owner": independent.counterpart,
                            "rationale": "Clear continuation.",
                        }
                    ],
                },
            ]
        },
        [first, second, independent],
    )

    assert verdicts[first.dispute_id] is not None
    assert verdicts[first.dispute_id].owner is None
    assert verdicts[second.dispute_id] is not None
    assert verdicts[second.dispute_id].owner is None
    assert verdicts[independent.dispute_id] is not None
    assert verdicts[independent.dispute_id].owner == independent.counterpart


def test_an_impermissible_owner_abstains_the_affected_group_only() -> None:
    rejected = _dispute(start=100.0, group_id="group-one")
    accepted = _dispute(start=200.0, group_id="group-two")

    verdicts = parse_discourse_verdicts(
        {
            "groups": [
                {
                    "group_id": "group-one",
                    "verdicts": [
                        {
                            "dispute_id": rejected.dispute_id,
                            "owner": "speaker-unknown",
                            "rationale": "Invalid owner.",
                        }
                    ],
                },
                {
                    "group_id": "group-two",
                    "verdicts": [
                        {
                            "dispute_id": accepted.dispute_id,
                            "owner": accepted.counterpart,
                            "rationale": "Clear continuation.",
                        }
                    ],
                },
            ]
        },
        [rejected, accepted],
    )

    assert verdicts[rejected.dispute_id] is not None
    assert verdicts[rejected.dispute_id].owner is None
    assert verdicts[accepted.dispute_id] is not None
    assert verdicts[accepted.dispute_id].owner == accepted.counterpart


def test_provider_failure_abstains_for_every_dispute() -> None:
    dispute = _dispute()

    verdicts = parse_discourse_verdicts(None, [dispute])

    assert verdicts == {dispute.dispute_id: None}


def test_an_incomplete_answer_is_rejected_instead_of_looking_healthy() -> None:
    answered = _dispute(start=100.0)
    missing = _dispute(start=200.0)

    with pytest.raises(ValueError, match="missing verdicts"):
        parse_discourse_verdicts(
            {
                "verdicts": [
                    {
                        "dispute_id": answered.dispute_id,
                        "verdict": "continuation",
                        "rationale": "Continues the sentence.",
                    }
                ]
            },
            [answered, missing],
        )


@pytest.mark.parametrize("invalid_id", ["unknown", "duplicate"])
def test_an_ambiguous_answer_is_rejected(invalid_id: str) -> None:
    dispute = _dispute()
    dispute_id = (
        "unknown" if invalid_id == "unknown" else dispute.dispute_id
    )
    items = [
        {
            "dispute_id": dispute_id,
            "verdict": "continuation",
            "rationale": "First answer.",
        }
    ]
    if invalid_id == "duplicate":
        items.append(
            {
                "dispute_id": dispute.dispute_id,
                "verdict": "interjection",
                "rationale": "Conflicting second answer.",
            }
        )

    with pytest.raises(ValueError, match="unknown|duplicate"):
        parse_discourse_verdicts(
            {"verdicts": items},
            [dispute],
        )


def test_an_unsure_verdict_is_a_real_abstention() -> None:
    from transpodcast.adjudication_judges import DISCOURSE_SCHEMA

    owner_schema = DISCOURSE_SCHEMA["properties"]["groups"]["items"][
        "properties"
    ]["verdicts"]["items"]["properties"]["owner"]
    assert owner_schema["type"] == "string"

    dispute = _dispute()
    verdicts = parse_discourse_verdicts(
        {
            "verdicts": [
                {
                    "dispute_id": dispute.dispute_id,
                    "owner": "unsure",
                    "rationale": "Could be either Speaker.",
                }
            ]
        },
        [dispute],
    )
    verdict = verdicts[dispute.dispute_id]
    assert verdict is not None
    assert verdict.owner is None
    assert verdict.rationale == "Could be either Speaker."
