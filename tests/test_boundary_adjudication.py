from __future__ import annotations

from transpodcast.boundary_adjudication import (
    AdjudicationPolicy,
    find_ownership_disputes,
)

# --- dispute detection ---


def _candidate_segment(
    label: str,
    start: float,
    end: float,
    text: str,
) -> dict:
    return {
        "speaker_label": label,
        "start_seconds": start,
        "end_seconds": end,
        "text": text,
        "words": [
            {
                "text": text,
                "start_seconds": start,
                "end_seconds": end,
            }
        ],
    }


def test_short_boundary_candidates_do_not_require_an_aba_island() -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 3.0, "a1"),
        _candidate_segment("speaker-b", 3.1, 6.1, "b1"),
        _candidate_segment("speaker-c", 6.2, 9.2, "c1"),
        _candidate_segment("speaker-a", 9.3, 12.3, "a2"),
        _candidate_segment("speaker-b", 12.4, 13.2, "What do we"),
        _candidate_segment("speaker-c", 13.3, 16.3, "learn next"),
        _candidate_segment("speaker-a", 16.4, 19.4, "a3"),
        _candidate_segment("speaker-b", 19.5, 22.5, "b3"),
        _candidate_segment("speaker-c", 22.6, 25.6, "c3"),
    ]

    disputes = find_ownership_disputes(segments)

    candidate = next(
        item for item in disputes if item.start_seconds == 12.4
    )
    assert candidate.current_owner == "speaker-b"
    assert candidate.candidate_owners == (
        "speaker-a",
        "speaker-b",
        "speaker-c",
    )
    assert candidate.trigger_reasons == ("short-boundary",)


def test_a_sparse_episode_label_is_a_candidate_even_when_long() -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 4.0, "before"),
        _candidate_segment("speaker-c", 4.1, 7.1, "working there"),
        _candidate_segment("speaker-a", 7.2, 11.2, "after"),
        _candidate_segment("speaker-a", 11.3, 15.3, "more"),
    ]

    disputes = find_ownership_disputes(segments)

    candidate = next(
        item for item in disputes if item.current_owner == "speaker-c"
    )
    assert candidate.candidate_owners == ("speaker-a", "speaker-c")
    assert candidate.trigger_reasons == ("sparse-label",)


def test_every_segment_in_a_two_segment_sparse_run_is_adjudicated() -> None:
    segments = [
        _candidate_segment("speaker-c", 0.0, 4.0, "sparse one"),
        _candidate_segment("speaker-c", 4.1, 8.1, "sparse two"),
        _candidate_segment("speaker-a", 8.2, 12.2, "established one"),
        _candidate_segment("speaker-a", 12.3, 16.3, "established two"),
        _candidate_segment("speaker-a", 16.4, 20.4, "established three"),
    ]

    disputes = find_ownership_disputes(segments)
    sparse = [
        dispute
        for dispute in disputes
        if dispute.current_owner == "speaker-c"
    ]

    assert [dispute.start_seconds for dispute in sparse] == [0.0, 4.1]
    assert all(
        dispute.permissible_owners == ("speaker-a", "speaker-c")
        for dispute in sparse
    )
    assert sparse[0].group_id == sparse[1].group_id


def test_adjacent_candidates_share_one_local_decision_group() -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 4.0, "before"),
        _candidate_segment("speaker-b", 4.1, 4.8, "short one"),
        _candidate_segment("speaker-c", 4.9, 5.5, "short two"),
        _candidate_segment("speaker-a", 5.6, 9.6, "after"),
        _candidate_segment("speaker-a", 9.7, 13.7, "more"),
    ]

    disputes = find_ownership_disputes(segments)
    adjacent = [
        item
        for item in disputes
        if item.current_owner in {"speaker-b", "speaker-c"}
    ]

    assert len(adjacent) == 2
    assert adjacent[0].group_id == adjacent[1].group_id
    assert adjacent[0].group_id


def test_grouping_does_not_add_non_adjacent_speaker_choices() -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 4.0, "a1"),
        _candidate_segment("speaker-b", 4.1, 4.8, "b candidate"),
        _candidate_segment("speaker-c", 4.9, 5.5, "c candidate"),
        _candidate_segment("speaker-d", 5.6, 6.2, "d candidate"),
        _candidate_segment("speaker-a", 6.3, 10.3, "a2"),
        _candidate_segment("speaker-b", 10.4, 14.4, "b2"),
        _candidate_segment("speaker-c", 14.5, 18.5, "c2"),
        _candidate_segment("speaker-d", 18.6, 22.6, "d2"),
        _candidate_segment("speaker-a", 22.7, 26.7, "a3"),
        _candidate_segment("speaker-b", 26.8, 30.8, "b3"),
        _candidate_segment("speaker-c", 30.9, 34.9, "c3"),
        _candidate_segment("speaker-d", 35.0, 39.0, "d3"),
    ]

    disputes = find_ownership_disputes(segments)

    first = next(
        dispute for dispute in disputes if dispute.start_seconds == 4.1
    )
    last = next(
        dispute for dispute in disputes if dispute.start_seconds == 5.6
    )
    assert first.permissible_owners == (
        "speaker-a",
        "speaker-b",
        "speaker-c",
    )
    assert last.permissible_owners == (
        "speaker-a",
        "speaker-c",
        "speaker-d",
    )


def test_overlapping_candidates_share_a_group_across_a_stable_segment(
) -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 4.0, "a1"),
        _candidate_segment("speaker-b", 4.1, 8.1, "b1"),
        _candidate_segment("speaker-c", 8.2, 12.2, "c1"),
        _candidate_segment("speaker-b", 20.0, 22.0, "candidate one"),
        _candidate_segment("speaker-a", 20.5, 25.0, "stable overlap"),
        _candidate_segment("speaker-c", 21.0, 21.8, "candidate two"),
        _candidate_segment("speaker-a", 25.1, 29.1, "a2"),
        _candidate_segment("speaker-b", 29.2, 33.2, "b2"),
        _candidate_segment("speaker-c", 33.3, 37.3, "c2"),
    ]

    disputes = find_ownership_disputes(segments)

    first = next(
        dispute for dispute in disputes if dispute.start_seconds == 20.0
    )
    second = next(
        dispute for dispute in disputes if dispute.start_seconds == 21.0
    )
    assert first.group_id == second.group_id


def test_long_segments_are_never_disputes() -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 4.0, "opening"),
        _candidate_segment("speaker-b", 4.1, 4.6, "right and"),
        _candidate_segment("speaker-a", 4.7, 30.0, "a long answer"),
        _candidate_segment("speaker-b", 30.1, 34.1, "follow up"),
        _candidate_segment("speaker-a", 34.2, 38.2, "closing"),
        _candidate_segment("speaker-b", 38.3, 42.3, "thanks"),
    ]

    disputes = find_ownership_disputes(segments)

    assert disputes
    for dispute in disputes:
        span = float(dispute.words[-1]["end_seconds"]) - float(
            dispute.words[0]["start_seconds"]
        )
        assert span <= AdjudicationPolicy().maximum_dispute_seconds
