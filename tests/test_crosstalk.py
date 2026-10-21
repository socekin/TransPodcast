from __future__ import annotations

from typing import Any

from transpodcast.crosstalk import crosstalk_candidates, crosstalk_candidates_payload


def _segments(spec: list[tuple[str, float, float]]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"seg-{index:04d}",
            "speaker": speaker,
            "source_start_seconds": start,
            "source_end_seconds": end,
            "source_text": f"line {index}",
        }
        for index, (speaker, start, end) in enumerate(spec, start=1)
    ]


def test_three_speaker_changes_within_six_seconds_form_a_candidate() -> None:
    segments = _segments(
        [
            ("a", 0.0, 22.0),
            ("b", 22.5, 23.0),
            ("c", 23.1, 23.6),
            ("a", 23.7, 24.2),
            ("b", 24.3, 25.0),
            ("a", 26.0, 40.0),
        ]
    )

    (candidate,) = crosstalk_candidates(segments)

    assert candidate["candidate_id"] == "xt-001"
    # The long opening turn has no other start within 6 s; the closing turn
    # starts inside the burst's window, so the Reviewer sees it too.
    assert candidate["start_segment_id"] == "seg-0002"
    assert candidate["end_segment_id"] == "seg-0006"
    assert candidate["speaker_changes"] == 4
    assert candidate["speakers"] == ["a", "b", "c"]
    assert [line["segment_id"] for line in candidate["lines"]] == [
        "seg-0002",
        "seg-0003",
        "seg-0004",
        "seg-0005",
        "seg-0006",
    ]


def test_ordinary_turn_taking_is_not_a_candidate() -> None:
    segments = _segments(
        [("a", 0.0, 8.0), ("b", 8.5, 16.0), ("a", 16.5, 24.0), ("b", 24.5, 30.0)]
    )

    assert crosstalk_candidates(segments) == []


def test_nearby_zones_merge_and_distant_zones_stay_apart() -> None:
    burst = [("a", 0.0, 0.4), ("b", 0.5, 0.9), ("a", 1.0, 1.4), ("b", 1.5, 1.9)]
    near = [("a", 2.5, 2.9), ("b", 3.0, 3.4), ("a", 3.5, 3.9), ("b", 4.0, 4.4)]
    far = [(s, t + 60.0, e + 60.0) for s, t, e in burst]
    candidates = crosstalk_candidates(_segments(burst + near + far))

    assert [(c["start_segment_id"], c["end_segment_id"]) for c in candidates] == [
        ("seg-0001", "seg-0008"),
        ("seg-0009", "seg-0012"),
    ]
    assert crosstalk_candidates_payload(_segments(burst))["policy_version"] == (
        "crosstalk-candidates-v1"
    )


def test_edge_candidates_reach_the_opening_and_closing() -> None:
    opening = [("a", 0.0, 4.0), ("b", 5.0, 5.4), ("a", 5.5, 5.9), ("b", 6.0, 6.4)]
    opening += [("a", 6.5, 6.9)]
    middle = [("a", 30.0, 60.0), ("b", 61.0, 90.0)]
    closing = [("a", 100.0, 100.4), ("b", 100.5, 100.9), ("a", 101.0, 101.4)]
    closing += [("b", 101.5, 101.9), ("a", 110.0, 130.0)]
    candidates = crosstalk_candidates(_segments(opening + middle + closing))

    first, last = candidates
    assert first["start_segment_id"] == "seg-0001" and first["opening"] is True
    assert last["end_segment_id"] == "seg-0012" and last["closing"] is True
    assert first["closing"] is False and last["opening"] is False
