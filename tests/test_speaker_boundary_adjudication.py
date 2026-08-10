from __future__ import annotations

from dataclasses import replace

from transpodcast.boundary_adjudication import OwnershipDispute
from transpodcast.domain import CanonicalSegment
from transpodcast.speaker_boundary_adjudication import (
    BoundaryPlanningResult,
    BoundaryProposal,
    BoundaryReviewResult,
    BoundaryReviewVerdict,
    UnifiedBoundaryCandidate,
    apply_unified_boundary_decisions,
    build_unified_boundary_candidates,
    parse_boundary_proposals,
)
from transpodcast.speaker_tail_boundary_repair import WordSpeakerSupport


def _word(text: str, start: float, owner: str) -> WordSpeakerSupport:
    return WordSpeakerSupport(
        text=text,
        start_seconds=start,
        end_seconds=start + 0.4,
        speaker_seconds=((owner, 0.4),),
    )


def _candidate(
    current_id: str = "seg-a",
    next_id: str = "seg-b",
    current_owner: str = "speaker-a",
    next_owner: str = "speaker-b",
    *,
    current_words: tuple[WordSpeakerSupport, ...] | None = None,
    next_words: tuple[WordSpeakerSupport, ...] | None = None,
    permissible_owners: tuple[str, ...] = (),
) -> UnifiedBoundaryCandidate:
    return UnifiedBoundaryCandidate(
        candidate_id=f"unified-boundary::{current_id}::{next_id}",
        current_segment_id=current_id,
        next_segment_id=next_id,
        current_owner=current_owner,
        next_owner=next_owner,
        current_words=current_words
        or (
            _word("complete", 0.0, current_owner),
            _word("Do", 0.4, current_owner),
            _word("you", 0.8, current_owner),
        ),
        next_words=next_words
        or (
            _word("think", 1.2, next_owner),
            _word("so", 1.6, next_owner),
        ),
        permissible_owners=permissible_owners,
        trigger_reasons=("ownership-dispute",),
    )


def _segments(
    *candidates: UnifiedBoundaryCandidate,
) -> tuple[CanonicalSegment, ...]:
    words_by_id: dict[str, tuple[WordSpeakerSupport, ...]] = {}
    owners: dict[str, str] = {}
    order: list[str] = []
    for candidate in candidates:
        if candidate.current_segment_id not in words_by_id:
            order.append(candidate.current_segment_id)
            words_by_id[candidate.current_segment_id] = candidate.current_words
            owners[candidate.current_segment_id] = candidate.current_owner
        if candidate.next_segment_id not in words_by_id:
            order.append(candidate.next_segment_id)
            words_by_id[candidate.next_segment_id] = candidate.next_words
            owners[candidate.next_segment_id] = candidate.next_owner
    return tuple(
        CanonicalSegment(
            segment_id=segment_id,
            speaker_label=owners[segment_id],
            start_seconds=words_by_id[segment_id][0].start_seconds,
            end_seconds=words_by_id[segment_id][-1].end_seconds,
            text=" ".join(word.text for word in words_by_id[segment_id]),
            words=tuple(
                (word.text, word.start_seconds, word.end_seconds)
                for word in words_by_id[segment_id]
            ),
        )
        for segment_id in order
    )


def _planning(
    candidate: UnifiedBoundaryCandidate,
    proposal: BoundaryProposal,
) -> BoundaryPlanningResult:
    return BoundaryPlanningResult(
        proposals={candidate.candidate_id: proposal},
        provider="fixture-planner",
    )


def _approved(candidate: UnifiedBoundaryCandidate) -> BoundaryReviewResult:
    return BoundaryReviewResult(
        verdicts={
            candidate.candidate_id: BoundaryReviewVerdict(
                verdict="approve",
                rationale="The proposed boundary is complete and clear.",
            )
        },
        provider="fixture-reviewer",
    )


def test_planner_may_select_a_longer_existing_suffix_without_legacy_limits() -> None:
    words = tuple(_word(f"w{index}", index * 0.5, "speaker-a") for index in range(12))
    candidate = _candidate(current_words=words)

    proposals, errors, _ = parse_boundary_proposals(
        {
            "proposals": [
                {
                    "candidate_id": candidate.candidate_id,
                    "action": "split_to_next",
                    "target_segment_id": candidate.current_segment_id,
                    "target_owner": candidate.next_owner,
                    "split_before_word_index": 5,
                    "rationale": "The seven-word suffix begins the next turn.",
                }
            ]
        },
        [candidate],
    )

    assert errors == {}
    assert proposals[candidate.candidate_id] is not None
    assert proposals[candidate.candidate_id].split_before_word_index == 5


def test_v14_acoustic_selector_keeps_long_suffixes_and_wide_gaps_eligible() -> None:
    current_words = tuple(
        (f"w{index}", index * 0.5, (index + 1) * 0.5) for index in range(12)
    )
    current = CanonicalSegment(
        segment_id="seg-a",
        speaker_label="speaker-a",
        start_seconds=0.0,
        end_seconds=6.0,
        text=" ".join(word[0] for word in current_words),
        words=current_words,
    )
    following = CanonicalSegment(
        segment_id="seg-b",
        speaker_label="speaker-b",
        start_seconds=7.2,
        end_seconds=8.0,
        text="next sentence",
        words=(("next", 7.2, 7.6), ("sentence", 7.6, 8.0)),
    )

    candidates = build_unified_boundary_candidates(
        (current, following),
        (),
        {},
        (),
        (
            ("speaker-a", 0.0, 2.5),
            ("speaker-b", 2.5, 8.0),
        ),
        {"speaker-a": "speaker-a", "speaker-b": "speaker-b"},
    )

    assert len(candidates) == 1
    assert candidates[0].trigger_reasons == ("acoustic-tail",)
    assert len(candidates[0].current_words) == 12


def test_stable_boundaries_and_event_segments_are_not_candidates() -> None:
    stable = (
        CanonicalSegment(
            segment_id="seg-a",
            speaker_label="speaker-a",
            start_seconds=0.0,
            end_seconds=0.4,
            text="stable",
            words=(("stable", 0.0, 0.4),),
        ),
        CanonicalSegment(
            segment_id="seg-b",
            speaker_label="speaker-b",
            start_seconds=0.5,
            end_seconds=0.9,
            text="answer",
            words=(("answer", 0.5, 0.9),),
        ),
    )
    eventful = (
        replace(stable[0], events=("laughter",)),
        stable[1],
    )
    raw_turns = (
        ("speaker-a", 0.0, 0.4),
        ("speaker-b", 0.5, 0.9),
    )

    assert build_unified_boundary_candidates(
        stable, (), {}, (), raw_turns, {}
    ) == ()
    assert build_unified_boundary_candidates(
        eventful,
        (),
        {},
        (),
        (("speaker-b", 0.0, 0.9),),
        {},
    ) == ()
    event_dispute = OwnershipDispute(
        dispute_id="event-dispute",
        words=(
            {
                "text": "stable",
                "start_seconds": 0.0,
                "end_seconds": 0.4,
            },
        ),
        current_owner="speaker-a",
        counterpart="speaker-b",
    )
    assert build_unified_boundary_candidates(
        eventful,
        (event_dispute,),
        {},
        (),
        raw_turns,
        {},
    ) == ()


def test_ownership_dispute_prefers_the_neighbor_with_the_counterpart_owner() -> None:
    segments = (
        CanonicalSegment(
            segment_id="seg-a",
            speaker_label="speaker-a",
            start_seconds=0.0,
            end_seconds=0.4,
            text="before",
            words=(("before", 0.0, 0.4),),
        ),
        CanonicalSegment(
            segment_id="seg-b1",
            speaker_label="speaker-b",
            start_seconds=0.4,
            end_seconds=0.8,
            text="disputed",
            words=(("disputed", 0.4, 0.8),),
        ),
        CanonicalSegment(
            segment_id="seg-b2",
            speaker_label="speaker-b",
            start_seconds=0.8,
            end_seconds=1.2,
            text="continues",
            words=(("continues", 0.8, 1.2),),
        ),
    )
    dispute = OwnershipDispute(
        dispute_id="dispute-0.4-speaker-b",
        words=(
            {
                "text": "disputed",
                "start_seconds": 0.4,
                "end_seconds": 0.8,
            },
        ),
        current_owner="speaker-b",
        counterpart="speaker-a",
        candidate_owners=("speaker-a", "speaker-b"),
    )

    candidates = build_unified_boundary_candidates(
        segments,
        (dispute,),
        {},
        (),
        (),
        {},
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.current_segment_id, candidate.next_segment_id) == (
        "seg-a",
        "seg-b1",
    )
    result = apply_unified_boundary_decisions(
        segments,
        candidates,
        _planning(
            candidate,
            BoundaryProposal(
                action="reassign_whole",
                target_segment_id="seg-b1",
                target_owner="speaker-a",
                rationale="The disputed word continues the prior Speaker.",
            ),
        ),
        _approved(candidate),
    )
    assert result.segments[1].speaker_label == "speaker-a"


def test_independent_reviewer_rejection_preserves_the_original_pair() -> None:
    candidate = _candidate()
    segments = _segments(candidate)
    planning = _planning(
        candidate,
        BoundaryProposal(
            action="split_to_next",
            target_segment_id=candidate.current_segment_id,
            target_owner=candidate.next_owner,
            split_before_word_index=1,
            rationale="Planner proposal.",
        ),
    )
    review = BoundaryReviewResult(
        verdicts={
            candidate.candidate_id: BoundaryReviewVerdict(
                verdict="reject",
                rationale="The suffix remains with the current thought.",
            )
        }
    )

    result = apply_unified_boundary_decisions(segments, [candidate], planning, review)

    assert result.segments == segments
    assert result.records[0].verdict == "review-preserved"


def test_keep_and_unsure_proposals_preserve_the_original_pair() -> None:
    candidate = _candidate()
    segments = _segments(candidate)
    for action in ("keep", "unsure"):
        result = apply_unified_boundary_decisions(
            segments,
            [candidate],
            _planning(
                candidate,
                BoundaryProposal(action=action, rationale="Preserve this pair."),
            ),
            BoundaryReviewResult(verdicts={}),
        )
        assert result.segments == segments
        assert result.planner_proposed_count == 0
        assert result.records[0].verdict == "preserved"


def test_malformed_reviewer_output_preserves_the_connected_component() -> None:
    candidate = _candidate()
    segments = _segments(candidate)
    proposal = BoundaryProposal(
        action="split_to_next",
        target_segment_id=candidate.current_segment_id,
        target_owner=candidate.next_owner,
        split_before_word_index=1,
        rationale="Proposed split.",
    )
    result = apply_unified_boundary_decisions(
        segments,
        [candidate],
        _planning(candidate, proposal),
        BoundaryReviewResult(
            verdicts={candidate.candidate_id: None},
            validation_errors={candidate.candidate_id: "Malformed Reviewer verdict."},
        ),
    )
    assert result.segments == segments
    assert result.locally_preserved_component_count == 1
    assert result.records[0].verdict == "reviewer-degraded"


def test_reviewer_provider_failure_preserves_only_actionable_components() -> None:
    actionable = _candidate()
    preserving = _candidate(
        "seg-c",
        "seg-d",
        "speaker-c",
        "speaker-d",
        current_words=(
            _word("already", 3.0, "speaker-c"),
            _word("complete", 3.4, "speaker-c"),
        ),
        next_words=(_word("next", 3.8, "speaker-d"),),
    )
    segments = _segments(actionable, preserving)
    planning = BoundaryPlanningResult(
        proposals={
            actionable.candidate_id: BoundaryProposal(
                action="split_to_next",
                target_segment_id=actionable.current_segment_id,
                target_owner=actionable.next_owner,
                split_before_word_index=1,
                rationale="Proposed split.",
            ),
            preserving.candidate_id: BoundaryProposal(
                action="keep",
                rationale="This boundary is already correct.",
            ),
        }
    )
    result = apply_unified_boundary_decisions(
        segments,
        [actionable, preserving],
        planning,
        BoundaryReviewResult(verdicts={}, error="Reviewer unavailable."),
    )
    assert result.segments == segments
    assert result.provider_degraded_component_count == 1
    assert [record.verdict for record in result.records] == [
        "provider-degraded",
        "preserved",
    ]


def test_reviewed_whole_reassignment_changes_only_the_named_segment_owner() -> None:
    candidate = _candidate()
    segments = _segments(candidate)
    proposal = BoundaryProposal(
        action="reassign_whole",
        target_segment_id=candidate.current_segment_id,
        target_owner=candidate.next_owner,
        rationale="Every current word continues the next Speaker's thought.",
    )

    result = apply_unified_boundary_decisions(
        segments,
        [candidate],
        _planning(candidate, proposal),
        _approved(candidate),
    )

    assert result.segments[0].speaker_label == candidate.next_owner
    assert result.segments[0].words == segments[0].words
    assert result.segments[1] == segments[1]
    assert result.reassign_whole_count == 1


def test_connected_split_chain_is_materialized_once_from_original_words() -> None:
    first = _candidate(
        "seg-a",
        "seg-b",
        "speaker-a",
        "speaker-b",
        current_words=(
            _word("A", 0.0, "speaker-a"),
            _word("Bstart", 0.4, "speaker-b"),
        ),
        next_words=(
            _word("B", 0.8, "speaker-b"),
            _word("Cstart", 1.2, "speaker-c"),
        ),
    )
    second = _candidate(
        "seg-b",
        "seg-c",
        "speaker-b",
        "speaker-c",
        current_words=first.next_words,
        next_words=(_word("C", 1.6, "speaker-c"),),
    )
    segments = _segments(first, second)
    proposals = {
        first.candidate_id: BoundaryProposal(
            action="split_to_next",
            target_segment_id="seg-a",
            target_owner="speaker-b",
            split_before_word_index=1,
            rationale="B begins here.",
        ),
        second.candidate_id: BoundaryProposal(
            action="split_to_next",
            target_segment_id="seg-b",
            target_owner="speaker-c",
            split_before_word_index=1,
            rationale="C begins here.",
        ),
    }
    reviews = {
        candidate.candidate_id: BoundaryReviewVerdict(
            verdict="approve",
            rationale="Clear adjacent handoff.",
        )
        for candidate in (first, second)
    }

    result = apply_unified_boundary_decisions(
        segments,
        [first, second],
        BoundaryPlanningResult(proposals=proposals),
        BoundaryReviewResult(verdicts=reviews),
    )

    assert [segment.text for segment in result.segments] == [
        "A",
        "Bstart B",
        "Cstart C",
    ]
    assert tuple(word for segment in result.segments for word in segment.words) == (
        tuple(word for segment in segments for word in segment.words)
    )
    assert result.split_to_next_count == 2


def test_whole_reassignment_and_split_on_one_component_are_preserved() -> None:
    candidate = _candidate()
    proposals = {
        candidate.candidate_id: BoundaryProposal(
            action="split_to_next",
            target_segment_id=candidate.current_segment_id,
            target_owner=candidate.next_owner,
            split_before_word_index=1,
            rationale="Proposed split.",
        )
    }
    review = _approved(candidate)
    # A second candidate touching the same current segment proposes a whole
    # reassignment, which makes the connected component incompatible.
    second = _candidate(
        "seg-b",
        "seg-c",
        "speaker-b",
        "speaker-c",
        current_words=candidate.next_words,
        next_words=(_word("later", 2.0, "speaker-c"),),
    )
    proposals[second.candidate_id] = BoundaryProposal(
        action="reassign_whole",
        target_segment_id="seg-b",
        target_owner="speaker-c",
        rationale="Proposed whole reassignment.",
    )
    reviews = {
        **review.verdicts,
        second.candidate_id: BoundaryReviewVerdict(
            verdict="approve", rationale="Approved for fixture."
        ),
    }
    connected_segments = _segments(candidate, second)

    result = apply_unified_boundary_decisions(
        connected_segments,
        [candidate, second],
        BoundaryPlanningResult(proposals=proposals),
        BoundaryReviewResult(verdicts=reviews),
    )

    assert result.segments == connected_segments
    assert result.conflict_component_count == 1
    assert {record.verdict for record in result.records} == {"conflict-preserved"}


def test_meaningful_third_speaker_evidence_preserves_an_approved_split() -> None:
    candidate = _candidate(
        current_words=(
            _word("complete", 0.0, "speaker-a"),
            WordSpeakerSupport(
                text="mixed",
                start_seconds=0.4,
                end_seconds=1.2,
                speaker_seconds=(
                    ("speaker-b", 0.2),
                    ("speaker-c", 0.6),
                ),
            ),
        )
    )
    segments = _segments(candidate)
    proposal = BoundaryProposal(
        action="split_to_next",
        target_segment_id=candidate.current_segment_id,
        target_owner=candidate.next_owner,
        split_before_word_index=1,
        rationale="Proposed split.",
    )

    result = apply_unified_boundary_decisions(
        segments,
        [candidate],
        _planning(candidate, proposal),
        _approved(candidate),
    )

    assert result.segments == segments
    assert result.records[0].verdict == "conflict-preserved"


def test_three_permissible_owners_preserve_an_approved_split() -> None:
    candidate = _candidate(permissible_owners=("speaker-a", "speaker-b", "speaker-c"))
    segments = _segments(candidate)
    proposal = BoundaryProposal(
        action="split_to_next",
        target_segment_id=candidate.current_segment_id,
        target_owner=candidate.next_owner,
        split_before_word_index=1,
        rationale="Proposed split.",
    )

    result = apply_unified_boundary_decisions(
        segments,
        [candidate],
        _planning(candidate, proposal),
        _approved(candidate),
    )

    assert result.segments == segments
    assert result.records[0].verdict == "conflict-preserved"


def test_multiple_acoustic_handoffs_preserve_an_approved_split() -> None:
    candidate = _candidate(
        current_words=(
            _word("first", 0.0, "speaker-a"),
            _word("reply", 0.4, "speaker-b"),
            _word("returns", 0.8, "speaker-a"),
        )
    )
    segments = _segments(candidate)
    proposal = BoundaryProposal(
        action="split_to_next",
        target_segment_id=candidate.current_segment_id,
        target_owner=candidate.next_owner,
        split_before_word_index=1,
        rationale="Proposed split.",
    )

    result = apply_unified_boundary_decisions(
        segments,
        [candidate],
        _planning(candidate, proposal),
        _approved(candidate),
    )

    assert result.segments == segments
    assert result.records[0].verdict == "conflict-preserved"


def test_malformed_edge_preserves_its_entire_connected_component() -> None:
    first = _candidate(
        "seg-a",
        "seg-b",
        "speaker-a",
        "speaker-b",
        current_words=(
            _word("A", 0.0, "speaker-a"),
            _word("Bstart", 0.4, "speaker-b"),
        ),
        next_words=(
            _word("B", 0.8, "speaker-b"),
            _word("Cstart", 1.2, "speaker-c"),
        ),
    )
    second = _candidate(
        "seg-b",
        "seg-c",
        "speaker-b",
        "speaker-c",
        current_words=first.next_words,
        next_words=(_word("C", 1.6, "speaker-c"),),
    )
    segments = _segments(first, second)
    planning = BoundaryPlanningResult(
        proposals={
            first.candidate_id: BoundaryProposal(
                action="split_to_next",
                target_segment_id="seg-a",
                target_owner="speaker-b",
                split_before_word_index=1,
                rationale="B begins here.",
            ),
            second.candidate_id: None,
        },
        validation_errors={second.candidate_id: "Missing Planner proposal."},
    )
    review = BoundaryReviewResult(
        verdicts={
            first.candidate_id: BoundaryReviewVerdict(
                verdict="approve",
                rationale="The first boundary is clear.",
            )
        }
    )

    result = apply_unified_boundary_decisions(
        segments,
        [first, second],
        planning,
        review,
    )

    assert result.segments == segments
    assert result.locally_preserved_component_count == 1
    assert result.conflict_component_count == 0
    assert [record.verdict for record in result.records] == [
        "component-degraded",
        "planner-degraded",
    ]


def test_two_malformed_edges_count_as_one_preserved_component() -> None:
    first = _candidate("seg-a", "seg-b")
    second = _candidate(
        "seg-b",
        "seg-c",
        "speaker-b",
        "speaker-c",
        current_words=first.next_words,
        next_words=(_word("later", 2.0, "speaker-c"),),
    )
    errors = {
        first.candidate_id: "Malformed first proposal.",
        second.candidate_id: "Malformed second proposal.",
    }

    result = apply_unified_boundary_decisions(
        _segments(first, second),
        [first, second],
        BoundaryPlanningResult(
            proposals={first.candidate_id: None, second.candidate_id: None},
            validation_errors=errors,
        ),
        BoundaryReviewResult(verdicts={}),
    )

    assert result.locally_preserved_component_count == 1


def test_malformed_identifiable_planner_proposal_is_retained_and_preserved() -> None:
    candidate = _candidate()
    raw = {
        "candidate_id": candidate.candidate_id,
        "action": "split_to_next",
        "target_segment_id": candidate.current_segment_id,
        "target_owner": candidate.next_owner,
        "split_before_word_index": 99,
        "rationale": "Invalid exact boundary.",
    }
    proposals, errors, raw_proposals = parse_boundary_proposals(
        {"proposals": [raw]}, [candidate]
    )
    planning = BoundaryPlanningResult(
        proposals=proposals,
        validation_errors=errors,
        raw_proposals=raw_proposals,
    )

    result = apply_unified_boundary_decisions(
        _segments(candidate),
        [candidate],
        planning,
        BoundaryReviewResult(verdicts={}),
    )

    assert result.records[0].verdict == "planner-degraded"
    assert result.records[0].evidence["raw_planner_proposals"] == [raw]
