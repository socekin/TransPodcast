from __future__ import annotations

from transpodcast.boundary_adjudication import (
    ADJUDICATION_POLICY_VERSION,
    LEGACY_ADJUDICATION_POLICY_VERSION,
    DiscourseVerdict,
    VoiceprintVerdict,
    adjudicate_ownership,
    find_ownership_disputes,
)


class ScriptedVoiceprint:
    """Deterministic stand-in for the fragment voiceprint judge."""

    def __init__(self, verdicts: dict[str, VoiceprintVerdict | None]):
        self._verdicts = verdicts

    def measure(self, dispute) -> VoiceprintVerdict | None:
        return self._verdicts[dispute.dispute_id]


class ScriptedDiscourse:
    """Deterministic stand-in for the translation-agent judge."""

    def __init__(self, verdicts: dict[str, DiscourseVerdict | None]):
        self._verdicts = verdicts

    def judge(self, dispute) -> DiscourseVerdict | None:
        return self._verdicts[dispute.dispute_id]


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


def _synthetic_dispute():
    segments = [
        _candidate_segment("speaker_2", 0.0, 4.0, "We should continue"),
        _candidate_segment("speaker_3", 4.1, 4.4, "and"),
        _candidate_segment("speaker_2", 4.5, 8.5, "finish this thought"),
        _candidate_segment("speaker_3", 8.6, 12.6, "I have another idea"),
        _candidate_segment("speaker_2", 12.7, 16.7, "Let us compare both"),
        _candidate_segment("speaker_3", 16.8, 20.8, "That sounds useful"),
    ]
    disputes = find_ownership_disputes(segments)
    return next(item for item in disputes if item.start_seconds == 4.1)


def test_short_boundary_candidates_do_not_require_an_aba_island() -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 3.0, "opening"),
        _candidate_segment("speaker-b", 3.1, 6.1, "response"),
        _candidate_segment("speaker-c", 6.2, 9.2, "context"),
        _candidate_segment("speaker-a", 9.3, 12.3, "follow up"),
        _candidate_segment("speaker-b", 12.4, 13.2, "What do we"),
        _candidate_segment("speaker-c", 13.3, 16.3, "learn next"),
        _candidate_segment("speaker-a", 16.4, 19.4, "summary"),
        _candidate_segment("speaker-b", 19.5, 22.5, "detail"),
        _candidate_segment("speaker-c", 22.6, 25.6, "closing"),
    ]

    candidate = next(
        item
        for item in find_ownership_disputes(segments)
        if item.start_seconds == 12.4
    )

    assert candidate.current_owner == "speaker-b"
    assert candidate.candidate_owners == (
        "speaker-a",
        "speaker-b",
        "speaker-c",
    )
    assert candidate.trigger_reasons == ("short-boundary",)


def test_sparse_episode_label_is_a_candidate_even_when_long() -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 4.0, "before"),
        _candidate_segment("speaker-c", 4.1, 7.1, "working there"),
        _candidate_segment("speaker-a", 7.2, 11.2, "after"),
        _candidate_segment("speaker-a", 11.3, 15.3, "more"),
    ]

    candidate = next(
        item
        for item in find_ownership_disputes(segments)
        if item.current_owner == "speaker-c"
    )

    assert candidate.candidate_owners == ("speaker-a", "speaker-c")
    assert candidate.trigger_reasons == ("sparse-label",)


def test_adjacent_candidates_share_a_local_decision_group() -> None:
    segments = [
        _candidate_segment("speaker-a", 0.0, 4.0, "before"),
        _candidate_segment("speaker-b", 4.1, 4.8, "short one"),
        _candidate_segment("speaker-c", 4.9, 5.5, "short two"),
        _candidate_segment("speaker-a", 5.6, 9.6, "after"),
        _candidate_segment("speaker-a", 9.7, 13.7, "more"),
    ]

    adjacent = [
        item
        for item in find_ownership_disputes(segments)
        if item.current_owner in {"speaker-b", "speaker-c"}
    ]

    assert len(adjacent) == 2
    assert adjacent[0].group_id == adjacent[1].group_id
    assert adjacent[0].group_id


def test_agreeing_judges_reassign_without_a_marker() -> None:
    dispute = _synthetic_dispute()
    outcomes = adjudicate_ownership(
        [dispute],
        voiceprint_judge=ScriptedVoiceprint(
            {
                dispute.dispute_id: VoiceprintVerdict(
                    owner="speaker_2",
                    scores={"speaker_2": 0.72, "speaker_3": 0.18},
                )
            }
        ),
        discourse_judge=ScriptedDiscourse(
            {
                dispute.dispute_id: DiscourseVerdict(
                    owner="speaker_2",
                    rationale="Continuation of the surrounding sentence.",
                )
            }
        ),
    )

    outcome = outcomes[0]
    assert outcome.final_owner == "speaker_2"
    assert outcome.changed is True
    assert outcome.disputed_marker is False
    assert outcome.record.policy_version == ADJUDICATION_POLICY_VERSION
    assert outcome.record.verdict == "reassigned-by-agreement"


def test_disagreeing_judges_follow_discourse_and_mark() -> None:
    dispute = _synthetic_dispute()
    outcomes = adjudicate_ownership(
        [dispute],
        voiceprint_judge=ScriptedVoiceprint(
            {
                dispute.dispute_id: VoiceprintVerdict(
                    owner="speaker_2",
                    scores={"speaker_2": 0.72, "speaker_3": 0.18},
                )
            }
        ),
        discourse_judge=ScriptedDiscourse(
            {
                dispute.dispute_id: DiscourseVerdict(
                    owner="speaker_3",
                    rationale="This is an independent interjection.",
                )
            }
        ),
    )

    outcome = outcomes[0]
    assert outcome.final_owner == "speaker_3"
    assert outcome.changed is False
    assert outcome.disputed_marker is True
    assert outcome.record.verdict == "discourse-preferred"


def test_legacy_disagreement_keeps_voiceprint_precedence() -> None:
    dispute = _synthetic_dispute()
    outcomes = adjudicate_ownership(
        [dispute],
        voiceprint_judge=ScriptedVoiceprint(
            {
                dispute.dispute_id: VoiceprintVerdict(
                    owner="speaker_2",
                    scores={"speaker_2": 0.72, "speaker_3": 0.18},
                )
            }
        ),
        discourse_judge=ScriptedDiscourse(
            {
                dispute.dispute_id: DiscourseVerdict(
                    owner="speaker_3",
                    rationale="This is an independent interjection.",
                )
            }
        ),
        policy_version=LEGACY_ADJUDICATION_POLICY_VERSION,
    )

    outcome = outcomes[0]
    assert outcome.final_owner == "speaker_2"
    assert outcome.record.verdict == "voiceprint-preferred"


def test_voiceprint_abstention_adopts_clear_discourse_verdict() -> None:
    dispute = _synthetic_dispute()
    outcomes = adjudicate_ownership(
        [dispute],
        voiceprint_judge=ScriptedVoiceprint({dispute.dispute_id: None}),
        discourse_judge=ScriptedDiscourse(
            {
                dispute.dispute_id: DiscourseVerdict(
                    owner="speaker_2",
                    rationale="The fragment opens the surrounding sentence.",
                )
            }
        ),
    )

    outcome = outcomes[0]
    assert outcome.final_owner == "speaker_2"
    assert outcome.changed is True
    assert outcome.disputed_marker is True
    assert outcome.record.verdict == "reassigned-by-discourse"


def test_both_judges_abstaining_keeps_owner_and_marks() -> None:
    dispute = _synthetic_dispute()
    outcomes = adjudicate_ownership(
        [dispute],
        voiceprint_judge=ScriptedVoiceprint({dispute.dispute_id: None}),
        discourse_judge=ScriptedDiscourse({dispute.dispute_id: None}),
    )

    outcome = outcomes[0]
    assert outcome.final_owner == dispute.current_owner
    assert outcome.changed is False
    assert outcome.disputed_marker is True
    assert outcome.record.verdict == "kept-voiceprint-abstained"


def test_every_outcome_carries_a_decision_record() -> None:
    dispute = _synthetic_dispute()
    outcome = adjudicate_ownership(
        [dispute],
        voiceprint_judge=ScriptedVoiceprint(
            {
                dispute.dispute_id: VoiceprintVerdict(
                    owner="speaker_2",
                    scores={"speaker_2": 0.7, "speaker_3": 0.2},
                )
            }
        ),
        discourse_judge=ScriptedDiscourse(
            {
                dispute.dispute_id: DiscourseVerdict(
                    owner="speaker_2",
                    rationale="Continues the sentence.",
                )
            }
        ),
    )[0]

    record = outcome.record
    assert record.kind == "ownership-adjudication"
    assert record.subject["words"] == ["and"]
    assert record.evidence["voiceprint_scores"] == {
        "speaker_2": 0.7,
        "speaker_3": 0.2,
    }
    assert record.evidence["candidate_owners"] == list(
        dispute.permissible_owners
    )
    assert record.rationale
