from __future__ import annotations

from dataclasses import replace

import pytest

from transpodcast.domain import CanonicalSegment
from transpodcast.speaker_ownership_continuity import apply_reviewed_ownership
from transpodcast.speaker_ownership_v15 import (
    OwnershipAssignment,
    OwnershipVoiceprintEvidence,
    OwnershipWord,
    SpeakerOwnershipPlan,
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
)


def example() -> tuple[tuple[CanonicalSegment, ...], SpeakerOwnershipRegion]:
    words = (
        ("I", 0.0, 0.2),
        ("made", 0.2, 0.5),
        ("it", 0.5, 0.5),
        ("and", 0.5, 0.8),
        ("continued", 0.8, 1.3),
    )
    owners = ("a", "b", "b", "c", "b")
    segments = tuple(
        CanonicalSegment(str(i), owner, word[1], word[2], word[0], words=(word,))
        for i, (word, owner) in enumerate(zip(words, owners, strict=True))
    )
    region = SpeakerOwnershipRegion(
        "risk",
        0,
        5,
        tuple(
            OwnershipWord(i, w[0], w[1], w[2], owner)
            for i, (w, owner) in enumerate(zip(words, owners, strict=True))
        ),
        ("a", "b", "c"),
        ("multiple-handoffs",),
    )
    return segments, region


def draft() -> SpeakerOwnershipPlanningResult:
    return SpeakerOwnershipPlanningResult(
        plans={
            "risk": SpeakerOwnershipPlan(
                "assign_spans",
                (OwnershipAssignment(0, 5, "a"),),
                rationale="The entire continuation may belong to a.",
            ),
        }
    )


def test_final_reviewer_rejects_a_valid_planner_mutation() -> None:
    segments, region = example()
    reviewed = SpeakerOwnershipPlanningResult(
        plans={
            "risk": SpeakerOwnershipPlan("keep", rationale="The handoffs are real."),
        }
    )

    result = apply_reviewed_ownership(
        segments,
        (region,),
        draft(),
        reviewed,
        raw_turns=(),
        speaker_mapping={},
    )

    assert [s.speaker_label for s in result.segments for _ in s.words] == [
        "a",
        "b",
        "b",
        "c",
        "b",
    ]
    assert result.changed_word_count == 0
    assert result.records[0].rationale == "The handoffs are real."
    assert result.records[0].evidence["planner_plan"]["action"] == "assign_spans"
    assert result.records[0].evidence["applied_plan_source"] is None


def test_missing_final_answer_is_recorded_as_degradation_without_draft_fallback() -> (
    None
):
    segments, region = example()
    result = apply_reviewed_ownership(
        segments,
        (region,),
        draft(),
        SpeakerOwnershipPlanningResult(plans={}),
        raw_turns=(),
        speaker_mapping={},
    )
    assert result.changed_word_count == 0
    assert result.records[0].verdict == "reviewer-degraded"
    assert result.records[0].evidence["reviewer_validation_error"]


def test_review_can_accept_a_local_repair_while_retaining_uncertain_continuation() -> (
    None
):
    segments, region = example()
    reviewed = SpeakerOwnershipPlanningResult(
        plans={
            "risk": SpeakerOwnershipPlan(
                "assign_spans",
                (
                    OwnershipAssignment(0, 3, "a"),
                    OwnershipAssignment(3, 4, "c"),
                    OwnershipAssignment(4, 5, "b"),
                ),
                rationale="The measured phrase is a; later identity remains uncertain.",
            ),
        }
    )
    result = apply_reviewed_ownership(
        segments,
        (region,),
        draft(),
        reviewed,
        raw_turns=(),
        speaker_mapping={},
    )
    assert [s.speaker_label for s in result.segments for _ in s.words] == [
        "a",
        "a",
        "a",
        "c",
        "b",
    ]
    assert [w for s in result.segments for w in s.words] == [
        w for s in segments for w in s.words
    ]
    assert result.records[0].evidence["applied_plan_source"] == "reviewer"
    assert (
        result.records[0].policy_version == "speaker-ownership-continuity-v1-candidate"
    )


@pytest.mark.parametrize(
    "reviewed",
    [
        SpeakerOwnershipPlanningResult(plans={}, error="Provider unavailable"),
        SpeakerOwnershipPlanningResult(
            plans={
                "risk": SpeakerOwnershipPlan(
                    "assign_spans",
                    (OwnershipAssignment(0, 5, "new-speaker"),),
                    rationale="Invalid speaker.",
                )
            }
        ),
        SpeakerOwnershipPlanningResult(plans=draft().plans, error="Incomplete stream"),
    ],
)
def test_failed_or_invalid_review_does_not_apply_the_draft(
    reviewed: SpeakerOwnershipPlanningResult,
) -> None:
    segments, region = example()
    result = apply_reviewed_ownership(
        segments,
        (region,),
        draft(),
        reviewed,
        raw_turns=(),
        speaker_mapping={},
    )
    assert result.changed_word_count == 0


def test_expansion_covers_the_truncated_continuation_and_merges_a_neighbor() -> None:
    from transpodcast.speaker_ownership_continuity import expand_continuity_regions

    segments, region = example()
    more_words = tuple((f"word-{i}", float(i), i + 0.5) for i in range(5, 12))
    segments += (
        CanonicalSegment("tail", "b", 5, 11.5, "continuation", words=more_words),
    )
    neighbor = replace(
        region,
        region_id="neighbor",
        start_word_index=8,
        end_word_index=10,
        words=tuple(
            OwnershipWord(i, f"word-{i}", float(i), i + 0.5, "b") for i in range(8, 10)
        ),
    )
    expanded = expand_continuity_regions(segments, (region, neighbor), (), {})
    assert len(expanded) == 1
    assert (expanded[0].start_word_index, expanded[0].end_word_index) == (0, 12)
    assert expanded[0].source_region_ids == ("risk", "neighbor")
    assert not expanded[0].continuation_truncated


def test_application_rejects_competing_regions_before_any_word_is_changed() -> None:
    segments, region = example()
    with pytest.raises(ValueError, match="disjoint"):
        apply_reviewed_ownership(
            segments,
            (region, replace(region, region_id="competing")),
            draft(),
            draft(),
            raw_turns=(),
            speaker_mapping={},
        )


def test_compact_request_retains_word_indexes_timing_owners_and_acoustic_evidence() -> (
    None
):
    from transpodcast.speaker_ownership_continuity import continuity_question

    _, region = example()
    region = replace(
        region,
        words=(
            replace(
                region.words[0],
                speaker_seconds=(("a", 0.15), ("b", 0.05)),
                sentence_suffix=".",
            ),
            *region.words[1:],
        ),
    )
    _, data, _ = continuity_question((region,))
    payload = data["regions"][0]
    assert payload["word_columns"] == [
        "word_index",
        "text",
        "start_seconds",
        "end_seconds",
        "current_owner",
        "sentence_suffix",
        "speaker_seconds",
    ]
    assert payload["word_rows"][0] == [
        0,
        "I",
        0.0,
        0.2,
        "a",
        ".",
        [["a", 0.15], ["b", 0.05]],
    ]
    assert payload["word_rows"][2] == [2, "it", 0.5, 0.5, "b", "", []]
    assert "words" not in payload


@pytest.mark.parametrize(
    ("maximum_seconds", "maximum_words", "expected_end"),
    [
        (45.0, 6, 6),
        (3.0, 250, 5),
        (0.2, 2, 5),
    ],
)
def test_context_limits_mark_incomplete_coverage_without_shrinking_original_regions(
    maximum_seconds: float,
    maximum_words: int,
    expected_end: int,
) -> None:
    from transpodcast.speaker_ownership_continuity import expand_continuity_regions

    segments, region = example()
    segments += (
        CanonicalSegment(
            "tail",
            "b",
            5,
            10.5,
            "continued",
            words=tuple((str(i), float(i), i + 0.5) for i in range(5, 11)),
        ),
    )
    expanded = expand_continuity_regions(
        segments,
        (region,),
        (),
        {},
        maximum_seconds=maximum_seconds,
        maximum_words=maximum_words,
    )
    assert expanded[0].end_word_index == expected_end
    assert expanded[0].continuation_truncated
    assert expanded[0].requested_end_word_index == 11


def test_overlapping_words_and_duplicate_turns_do_not_inflate_overlap() -> None:
    from transpodcast.speaker_ownership_continuity import expand_continuity_regions

    owners = ("a", "a", "b", "b", "a", "a", "a")
    segments = tuple(
        CanonicalSegment(str(i), owner, 0, 0.3, "word", words=(("word", 0, 0.3),))
        for i, owner in enumerate(owners)
    )
    region = SpeakerOwnershipRegion(
        "overlap",
        0,
        6,
        tuple(
            OwnershipWord(i, "word", 0, 0.3, owner)
            for i, owner in enumerate(owners[:6])
        ),
        ("a", "b"),
        ("crosstalk",),
    )
    turns = (("a", 0, 1), ("b", 0, 0.3), ("b", 0, 0.3), ("b", 0, 0.3))
    expanded = expand_continuity_regions(segments, (region,), turns, {})
    assert expanded[0].end_word_index == 6


def test_short_acknowledgment_does_not_expand_on_inherited_overlap_flags() -> None:
    from transpodcast.speaker_ownership_continuity import expand_continuity_regions

    segments, region = example()
    region = replace(
        region,
        words=tuple(
            replace(w, current_owner="a" if i != 1 else "b")
            for i, w in enumerate(region.words)
        ),
    )
    segments = tuple(
        replace(s, speaker_label=region.words[i].current_owner, overlap=True)
        for i, s in enumerate(segments)
    )
    segments += (CanonicalSegment("tail", "a", 2, 3, "tail", words=(("tail", 2, 3),)),)
    expanded = expand_continuity_regions(segments, (region,), (), {})
    assert expanded[0].end_word_index == 5


def test_stable_partition_includes_its_metadata_within_the_request_limit() -> None:
    from transpodcast.speaker_ownership_continuity import (
        continuity_request_characters,
        expand_continuity_regions,
    )

    words = tuple(("word", float(i), i + 0.5) for i in range(100))
    segments = (CanonicalSegment("stable", "a", 0, 99.5, "stable", words=words),)
    region = SpeakerOwnershipRegion(
        "long-region",
        0,
        100,
        tuple(OwnershipWord(i, w[0], w[1], w[2], "a") for i, w in enumerate(words)),
        ("a",),
        ("crosstalk",),
    )
    full = expand_continuity_regions(segments, (region,), (), {})[0]
    half = replace(
        full,
        end_word_index=50,
        words=full.words[:50],
        context_after=("word",) * 12,
        continuation_truncated=True,
    )
    limit = continuity_request_characters((half,))
    parts = expand_continuity_regions(
        segments, (region,), (), {}, maximum_characters=limit
    )
    assert len(parts) > 1
    assert all(p.preservation_reason is None for p in parts)
    assert [w.word_index for p in parts for w in p.words] == list(range(100))
    assert all(continuity_request_characters((p,)) <= limit for p in parts)


def test_bounded_selection_keeps_voiceprints_from_an_unselected_neighbor() -> None:
    from transpodcast.speaker_ownership_continuity import (
        continuity_question,
        expand_continuity_regions,
    )

    segments, region = example()
    segments += (
        CanonicalSegment(
            "tail", "b", 5, 11.5, "continuation",
            words=tuple((f"word-{i}", float(i), i + 0.5) for i in range(5, 12)),
        ),
    )
    neighbor_evidence = OwnershipVoiceprintEvidence(
        "neighbor-proof", 8, 10, "a", (("a", 0.9), ("b", 0.2)),
    )

    expanded = expand_continuity_regions(
        segments, (region,), (), {}, voiceprint_evidence=(neighbor_evidence,),
    )

    assert expanded[0].end_word_index == 12
    assert expanded[0].source_region_ids == ("risk",)
    assert expanded[0].voiceprint_evidence == (neighbor_evidence,)
    _, evidence, _ = continuity_question(expanded)
    assert evidence["regions"][0]["voiceprint_evidence"][0]["scores"] == {
        "a": 0.9, "b": 0.2,
    }
