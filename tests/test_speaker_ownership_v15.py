from __future__ import annotations

import pytest

from transpodcast.domain import CanonicalSegment
from transpodcast.speaker_ownership_v15 import (
    OwnershipAssignment,
    SpeakerOwnershipApplication,
    SpeakerOwnershipPlan,
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
    apply_speaker_ownership_plans,
    batch_speaker_ownership_regions,
    build_speaker_ownership_regions,
    ownership_request_character_count,
    parse_ownership_plans,
)


def _segment(
    segment_id: str,
    owner: str,
    words: tuple[tuple[str, float, float], ...],
    *,
    overlap: bool = False,
) -> CanonicalSegment:
    return CanonicalSegment(
        segment_id=segment_id,
        speaker_label=owner,
        start_seconds=words[0][1],
        end_seconds=words[-1][2],
        text=" ".join(word[0] for word in words),
        words=words,
        overlap=overlap,
    )


def _regions() -> tuple[
    tuple[CanonicalSegment, ...],
    tuple[tuple[str, float, float], ...],
    tuple[SpeakerOwnershipRegion, ...],
]:
    segments = (
        _segment(
            "seg-1",
            "speaker-a",
            (("dimension", 0.0, 0.4), ("We're", 0.4, 0.7)),
        ),
        _segment(
            "seg-2",
            "speaker-b",
            (("unclear", 0.7, 1.1), ("now", 1.1, 1.4)),
        ),
    )
    turns = (
        ("speaker-a", 0.0, 0.4),
        ("speaker-b", 0.4, 1.4),
    )
    regions = build_speaker_ownership_regions(segments, turns, {})
    return segments, turns, regions


def _complete_assignments(
    region: SpeakerOwnershipRegion,
    changes: dict[int, str],
) -> tuple[OwnershipAssignment, ...]:
    owners = [changes.get(word.word_index, word.current_owner) for word in region.words]
    assignments: list[OwnershipAssignment] = []
    start = region.start_word_index
    for offset in range(1, len(owners) + 1):
        if offset < len(owners) and owners[offset] == owners[offset - 1]:
            continue
        assignments.append(
            OwnershipAssignment(
                start_word_index=start,
                end_word_index=region.start_word_index + offset,
                speaker_label=owners[offset - 1],
            )
        )
        start = region.start_word_index + offset
    return tuple(assignments)


def _apply_with_invariants(
    segments: tuple[CanonicalSegment, ...],
    regions: tuple[SpeakerOwnershipRegion, ...],
    planning: SpeakerOwnershipPlanningResult,
    review: SpeakerOwnershipPlanningResult,
    *,
    raw_turns: tuple[tuple[str, float, float], ...],
    speaker_mapping: dict[str, str],
) -> SpeakerOwnershipApplication:
    result = apply_speaker_ownership_plans(
        segments,
        regions,
        planning,
        review,
        raw_turns=raw_turns,
        speaker_mapping=speaker_mapping,
    )
    original_words = tuple(
        word for segment in segments for word in segment.words
    )
    assert tuple(
        word for segment in result.segments for word in segment.words
    ) == original_words
    assert all(segment.speaker_label for segment in result.segments)
    assert all(
        left.end_seconds <= right.start_seconds + 1e-6
        for left, right in zip(
            result.segments,
            result.segments[1:],
            strict=False,
        )
    )
    assert [segment.segment_id for segment in result.segments] == [
        f"ownership-{index:04d}"
        for index in range(1, len(result.segments) + 1)
    ]
    replay = apply_speaker_ownership_plans(
        segments,
        regions,
        planning,
        review,
        raw_turns=raw_turns,
        speaker_mapping=speaker_mapping,
    )
    assert replay == result
    return result


def test_builds_one_region_with_global_word_indexes() -> None:
    _segments, _turns, regions = _regions()

    assert len(regions) == 1
    assert [word.word_index for word in regions[0].words] == [0, 1, 2, 3]
    assert regions[0].permissible_speakers == ("speaker-a", "speaker-b")


def test_builds_overlap_only_region_with_punctuation_evidence() -> None:
    segment = _segment(
        "seg-1",
        "speaker-a",
        (("Hello", 0.0, 0.5), ("there", 0.5, 1.0)),
        overlap=True,
    )

    regions = build_speaker_ownership_regions(
        (segment,),
        (
            ("speaker-a", 0.0, 1.0),
            ("unresolved-raw-speaker", 0.4, 0.7),
        ),
        {},
        "Hello there.",
    )

    assert len(regions) == 1
    assert regions[0].trigger_reasons == ("crosstalk",)
    assert regions[0].permissible_speakers == ("speaker-a",)
    assert regions[0].words[-1].sentence_suffix == "."


def test_overlap_marker_is_risk_evidence_without_two_measurable_turns() -> None:
    segment = _segment(
        "seg-1",
        "speaker-a",
        (("partly", 0.0, 0.5), ("overlapped", 0.5, 1.0)),
        overlap=True,
    )

    regions = build_speaker_ownership_regions(
        (segment,),
        (("speaker-a", 0.0, 1.0),),
        {},
    )

    assert len(regions) == 1
    assert regions[0].trigger_reasons == ("crosstalk",)


def test_parses_multiple_non_overlapping_assignments() -> None:
    _segments, _turns, regions = _regions()
    region = regions[0]

    plans, errors, _raw = parse_ownership_plans(
        {
            "plans": [
                {
                    "region_id": region.region_id,
                    "action": "assign_spans",
                    "assignments": [
                        {
                            "first_word_index": 0,
                            "last_word_index": 0,
                            "speaker_label": "speaker-a",
                        },
                        {
                            "first_word_index": 1,
                            "last_word_index": 3,
                            "speaker_label": "speaker-b",
                        }
                    ],
                    "missing_speech_suspected": False,
                    "rationale": "We're begins the next sentence.",
                }
            ]
        },
        regions,
    )

    assert errors == {}
    assert plans[region.region_id] == SpeakerOwnershipPlan(
        action="assign_spans",
        assignments=(
            OwnershipAssignment(0, 1, "speaker-a"),
            OwnershipAssignment(1, 4, "speaker-b"),
        ),
        rationale="We're begins the next sentence.",
    )


def test_parses_possible_missing_speech_without_inventing_words() -> None:
    _segments, _turns, regions = _regions()
    region = regions[0]

    plans, errors, _raw = parse_ownership_plans(
        {
            "plans": [
                {
                    "region_id": region.region_id,
                    "action": "possible_missing_speech",
                    "assignments": [],
                    "missing_speech_suspected": True,
                    "rationale": "The overlap contains an untranscribed turn.",
                }
            ]
        },
        regions,
    )

    assert errors == {}
    assert plans[region.region_id] == SpeakerOwnershipPlan(
        action="possible_missing_speech",
        missing_speech_suspected=True,
        rationale="The overlap contains an untranscribed turn.",
    )


def test_rejects_assignment_spans_that_do_not_cover_the_region() -> None:
    _segments, _turns, regions = _regions()
    region = regions[0]

    plans, errors, _raw = parse_ownership_plans(
        {
            "plans": [
                {
                    "region_id": region.region_id,
                    "action": "assign_spans",
                    "assignments": [
                        {
                            "first_word_index": 1,
                            "last_word_index": 1,
                            "speaker_label": "speaker-b",
                        }
                    ],
                    "missing_speech_suspected": False,
                    "rationale": "The response omitted preserved coverage.",
                }
            ]
        },
        regions,
    )

    assert plans[region.region_id] is None
    assert errors[region.region_id] == (
        "ValueError: Speaker ownership spans do not cover the Risk Region"
    )


def test_rejects_unordered_assignment_spans() -> None:
    _segments, _turns, regions = _regions()
    region = regions[0]

    plans, errors, _raw = parse_ownership_plans(
        {
            "plans": [
                {
                    "region_id": region.region_id,
                    "action": "assign_spans",
                    "assignments": [
                        {
                            "first_word_index": 2,
                            "last_word_index": 2,
                            "speaker_label": "speaker-a",
                        },
                        {
                            "first_word_index": 1,
                            "last_word_index": 1,
                            "speaker_label": "speaker-b",
                        },
                    ],
                    "missing_speech_suspected": False,
                    "rationale": "The spans were returned out of order.",
                }
            ]
        },
        regions,
    )

    assert plans[region.region_id] is None
    assert errors[region.region_id] == (
        "ValueError: Speaker ownership spans must be ordered"
    )


@pytest.mark.parametrize(
    ("assignments", "expected_error"),
    [
        (
            [
                {
                    "first_word_index": 0,
                    "last_word_index": 2,
                    "speaker_label": "speaker-a",
                },
                {
                    "first_word_index": 2,
                    "last_word_index": 3,
                    "speaker_label": "speaker-b",
                },
            ],
            "ValueError: Speaker ownership spans overlap",
        ),
        (
            [
                {
                    "first_word_index": -1,
                    "last_word_index": 3,
                    "speaker_label": "speaker-b",
                }
            ],
            "ValueError: Speaker ownership span is outside the Risk Region",
        ),
        (
            [
                {
                    "first_word_index": "zero",
                    "last_word_index": 3,
                    "speaker_label": "speaker-b",
                }
            ],
            "ValueError: Speaker ownership indexes must be integers",
        ),
    ],
)
def test_rejects_malformed_assignment_shapes(
    assignments: list[dict[str, object]],
    expected_error: str,
) -> None:
    _segments, _turns, regions = _regions()
    region = regions[0]

    plans, errors, _raw = parse_ownership_plans(
        {
            "plans": [
                {
                    "region_id": region.region_id,
                    "action": "assign_spans",
                    "assignments": assignments,
                    "missing_speech_suspected": False,
                    "rationale": "Malformed ownership fixture.",
                }
            ]
        },
        regions,
    )

    assert plans[region.region_id] is None
    assert errors[region.region_id] == expected_error


def test_planner_change_applies_when_independent_reviewer_disagrees() -> None:
    segments, turns, regions = _regions()
    region = regions[0]
    planner = SpeakerOwnershipPlan(
        action="assign_spans",
        assignments=_complete_assignments(region, {1: "speaker-b"}),
        rationale="We're begins the incoming sentence.",
    )
    planning = SpeakerOwnershipPlanningResult(plans={region.region_id: planner})
    review = SpeakerOwnershipPlanningResult(
        plans={
            region.region_id: SpeakerOwnershipPlan(
                action="keep",
                rationale="The acoustic boundary is ambiguous.",
            )
        }
    )

    result = _apply_with_invariants(
        segments,
        regions,
        planning,
        review,
        raw_turns=turns,
        speaker_mapping={},
    )

    assert [segment.speaker_label for segment in result.segments] == [
        "speaker-a",
        "speaker-b",
    ]
    assert result.segments[0].text == "dimension"
    assert result.segments[1].text == "We're unclear now"
    assert result.segments[1].review_state == "disputed"
    assert result.changed_word_count == 1
    assert result.disputed_region_count == 1
    assert result.records[0].verdict == "applied-disputed"


def test_reviewer_change_applies_as_disputed_when_planner_keeps() -> None:
    segments, turns, regions = _regions()
    region = regions[0]
    planning = SpeakerOwnershipPlanningResult(
        plans={
            region.region_id: SpeakerOwnershipPlan(
                action="keep",
                rationale="The acoustic handoff could be accepted.",
            )
        }
    )
    reviewer = SpeakerOwnershipPlan(
        action="assign_spans",
        assignments=_complete_assignments(region, {1: "speaker-b"}),
        rationale="We're begins the incoming sentence.",
    )
    review = SpeakerOwnershipPlanningResult(
        plans={region.region_id: reviewer}
    )

    result = _apply_with_invariants(
        segments,
        regions,
        planning,
        review,
        raw_turns=turns,
        speaker_mapping={},
    )

    assert result.segments[0].text == "dimension"
    assert result.segments[1].text == "We're unclear now"
    assert result.segments[1].review_state == "disputed"
    assert result.changed_word_count == 1
    assert result.disputed_region_count == 1
    assert result.records[0].verdict == "applied-disputed"
    assert result.records[0].evidence["applied_plan_source"] == "reviewer"


def test_matching_mutation_is_confirmed_despite_missing_speech_disagreement() -> None:
    segments, turns, regions = _regions()
    region = regions[0]
    assignments = _complete_assignments(region, {1: "speaker-b"})
    planning = SpeakerOwnershipPlanningResult(
        plans={
            region.region_id: SpeakerOwnershipPlan(
                action="assign_spans",
                assignments=assignments,
                rationale="We're begins the incoming sentence.",
            )
        }
    )
    review = SpeakerOwnershipPlanningResult(
        plans={
            region.region_id: SpeakerOwnershipPlan(
                action="assign_spans",
                assignments=assignments,
                missing_speech_suspected=True,
                rationale="The same move is clear; crosstalk may be incomplete.",
            )
        }
    )

    result = _apply_with_invariants(
        segments,
        regions,
        planning,
        review,
        raw_turns=turns,
        speaker_mapping={},
    )

    assert result.changed_word_count == 1
    assert result.disputed_region_count == 0
    assert result.possible_missing_speech_count == 1
    assert result.records[0].verdict == "applied-confirmed"


def test_no_op_assignment_is_not_counted_as_an_applied_mutation() -> None:
    segments, turns, regions = _regions()
    region = regions[0]
    planning = SpeakerOwnershipPlanningResult(
        plans={
            region.region_id: SpeakerOwnershipPlan(
                action="assign_spans",
                assignments=_complete_assignments(region, {}),
                rationale="This repeats the current owner.",
            )
        }
    )
    review = SpeakerOwnershipPlanningResult(
        plans={
            region.region_id: SpeakerOwnershipPlan(
                action="keep",
                rationale="There is no ownership change.",
            )
        }
    )

    result = _apply_with_invariants(
        segments,
        regions,
        planning,
        review,
        raw_turns=turns,
        speaker_mapping={},
    )

    assert result.changed_word_count == 0
    assert result.applied_region_count == 0
    assert result.preserved_region_count == 1
    assert result.records[0].verdict == "planner-degraded"
    assert result.records[0].evidence["planner_validation_error"] == (
        "assign_spans must change at least one word owner"
    )


def test_reviewer_failure_is_visible_when_planner_preserves() -> None:
    segments, turns, regions = _regions()
    region = regions[0]
    planning = SpeakerOwnershipPlanningResult(
        plans={
            region.region_id: SpeakerOwnershipPlan(
                action="keep",
                rationale="The existing ownership is plausible.",
            )
        }
    )
    review = SpeakerOwnershipPlanningResult(
        plans={},
        error="Reviewer provider unavailable.",
    )

    result = _apply_with_invariants(
        segments,
        regions,
        planning,
        review,
        raw_turns=turns,
        speaker_mapping={},
    )

    assert result.changed_word_count == 0
    assert result.records[0].verdict == "reviewer-degraded"
    assert result.records[0].evidence["reviewer_execution"]["error"] == (
        "Reviewer provider unavailable."
    )


def test_two_provider_failures_preserve_every_word_and_owner() -> None:
    segments, turns, regions = _regions()
    planning = SpeakerOwnershipPlanningResult(
        plans={},
        error="Planner provider unavailable.",
    )
    review = SpeakerOwnershipPlanningResult(
        plans={},
        error="Reviewer provider unavailable.",
    )

    result = _apply_with_invariants(
        segments,
        regions,
        planning,
        review,
        raw_turns=turns,
        speaker_mapping={},
    )

    assert [segment.speaker_label for segment in result.segments] == [
        "speaker-a",
        "speaker-b",
    ]
    assert tuple(
        word for segment in result.segments for word in segment.words
    ) == tuple(word for segment in segments for word in segment.words)
    assert result.records[0].verdict == "provider-degraded"


def test_two_missing_outputs_preserve_every_word_and_owner() -> None:
    segments, turns, regions = _regions()
    region = regions[0]
    missing_error = "Speaker ownership output must contain exactly one plan"

    result = _apply_with_invariants(
        segments,
        regions,
        SpeakerOwnershipPlanningResult(
            plans={},
            validation_errors={region.region_id: missing_error},
        ),
        SpeakerOwnershipPlanningResult(
            plans={},
            validation_errors={region.region_id: missing_error},
        ),
        raw_turns=turns,
        speaker_mapping={},
    )

    assert [segment.speaker_label for segment in result.segments] == [
        "speaker-a",
        "speaker-b",
    ]
    assert tuple(
        word for segment in result.segments for word in segment.words
    ) == tuple(word for segment in segments for word in segment.words)
    assert result.records[0].verdict == "provider-degraded"


def test_two_unsure_results_preserve_every_word_and_owner() -> None:
    segments, turns, regions = _regions()
    region = regions[0]
    unsure = SpeakerOwnershipPlan(
        action="unsure",
        rationale="The evidence is insufficient.",
    )

    result = _apply_with_invariants(
        segments,
        regions,
        SpeakerOwnershipPlanningResult(plans={region.region_id: unsure}),
        SpeakerOwnershipPlanningResult(plans={region.region_id: unsure}),
        raw_turns=turns,
        speaker_mapping={},
    )

    assert [segment.speaker_label for segment in result.segments] == [
        "speaker-a",
        "speaker-b",
    ]
    assert tuple(
        word for segment in result.segments for word in segment.words
    ) == tuple(word for segment in segments for word in segment.words)
    assert result.records[0].verdict == "preserved"


def test_long_crosstalk_region_is_not_split_without_stable_context() -> None:
    words = tuple(
        (f"word-{index}", float(index), float(index + 1))
        for index in range(100)
    )
    segment = _segment("seg-long", "speaker-a", words, overlap=True)

    regions = build_speaker_ownership_regions(
        (segment,),
        (("speaker-a", 0.0, 100.0),),
        {},
    )

    assert len(regions) == 1
    assert regions[0].start_word_index == 0
    assert regions[0].end_word_index == 100


def test_batches_regions_below_both_limits() -> None:
    _segments, _turns, regions = _regions()
    repeated = tuple(
        type(regions[0])(
            **{
                **regions[0].__dict__,
                "region_id": f"region-{index}",
            }
        )
        for index in range(5)
    )

    batches = batch_speaker_ownership_regions(
        repeated,
        maximum_regions=2,
        maximum_characters=100_000,
    )

    assert [len(batch) for batch in batches] == [2, 2, 1]


def test_batches_include_the_complete_request_envelope_in_the_limit() -> None:
    _segments, _turns, regions = _regions()
    repeated = tuple(
        type(regions[0])(
            **{
                **regions[0].__dict__,
                "region_id": f"region-{index}",
            }
        )
        for index in range(3)
    )
    limit = ownership_request_character_count(repeated[:2]) - 1

    batches = batch_speaker_ownership_regions(
        repeated,
        maximum_regions=3,
        maximum_characters=limit,
    )

    assert [len(batch) for batch in batches] == [1, 1, 1]
    assert all(
        ownership_request_character_count(batch) <= limit
        for batch in batches
    )
