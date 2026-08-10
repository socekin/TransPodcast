from __future__ import annotations

import json

import pytest

from transpodcast.adjudication_completion import (
    complete_boundary_adjudication,
)
from transpodcast.boundary_adjudication import (
    DiscourseAdjudicationResult,
    DiscourseVerdict,
)
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult


class ReassigningAdapter:
    """Always answers that the fragment belongs to the counterpart."""

    def adjudicate_discourse(self, disputes):
        return DiscourseAdjudicationResult(
            verdicts={
                dispute.dispute_id: DiscourseVerdict(
                    owner=dispute.counterpart,
                    rationale="Continues the counterpart's sentence.",
                )
                for dispute in disputes
            },
            input_tokens=10,
            output_tokens=5,
        )


class AbstainingAdapter:
    def adjudicate_discourse(self, disputes):
        del disputes
        return DiscourseAdjudicationResult(
            verdicts={},
            error="fixture provider unavailable",
        )


def _analysis(
    segments: tuple[CanonicalSegment, ...],
) -> SourceAnalysisResult:
    return SourceAnalysisResult(
        segments=segments,
        pipeline_version="v6+consolidation+adjudication",
        diarization_model="d",
        transcription_model="t",
        alignment_model="a",
        boundary_adjudication_json=json.dumps(
            {
                "policy_version": "boundary-ownership-adjudication-v1",
                "dispute_count": 1,
                "changed_count": 0,
                "marked_count": 0,
                "decision_records": [],
                "pending": {
                    "disputes": [
                        {
                            "dispute_id": "dispute-1.1-speaker-2",
                            "words": [
                                {
                                    "text": "and",
                                    "start_seconds": 1.1,
                                    "end_seconds": 1.4,
                                }
                            ],
                            "current_owner": "speaker-2",
                            "counterpart": "speaker-1",
                            "context_before": ["Done."],
                            "context_after": ["Next."],
                        }
                    ],
                    "voiceprints": {
                        "dispute-1.1-speaker-2": None
                    },
                },
            }
        ),
    )


def _segment(
    segment_id: str,
    speaker: str,
    start: float,
    end: float,
    text: str,
    *,
    sponsor: bool = False,
) -> CanonicalSegment:
    return CanonicalSegment(
        segment_id=segment_id,
        speaker_label=speaker,
        start_seconds=start,
        end_seconds=end,
        text=text,
        words=((text, start, end),),
        omitted_as_sponsor=sponsor,
    )


def test_a_repair_preserves_existing_segmentation() -> None:
    analysis = _analysis(
        (
            _segment("seg-1", "speaker-1", 0.0, 1.0, "Done."),
            _segment("seg-2", "speaker-2", 1.1, 1.4, "and"),
            _segment("seg-3", "speaker-1", 1.5, 3.0, "Next."),
        )
    )

    completion = complete_boundary_adjudication(
        analysis,
        ReassigningAdapter(),
    )

    texts = [
        (segment.speaker_label, segment.text)
        for segment in completion.analysis.segments
    ]
    assert texts == [
        ("speaker-1", "Done."),
        ("speaker-1", "and"),
        ("speaker-1", "Next."),
    ]
    repaired = completion.analysis.segments[1]
    assert repaired.review_state == "disputed"
    assert [segment.segment_id for segment in completion.analysis.segments] == [
        "seg-1",
        "seg-2",
        "seg-3",
    ]


def test_a_repair_does_not_modify_legacy_content_flags() -> None:
    analysis = _analysis(
        (
            _segment(
                "seg-1",
                "speaker-1",
                0.0,
                1.0,
                "Buy this now",
                sponsor=True,
            ),
            _segment("seg-2", "speaker-2", 1.1, 1.4, "and"),
            _segment("seg-3", "speaker-2", 1.5, 3.0, "moving on"),
        )
    )

    completion = complete_boundary_adjudication(
        analysis,
        ReassigningAdapter(),
    )

    segments = completion.analysis.segments
    assert [item.text for item in segments] == [
        "Buy this now",
        "and",
        "moving on",
    ]
    assert segments[0].omitted_as_sponsor is True
    assert segments[1].speaker_label == "speaker-1"
    assert segments[1].review_state == "disputed"


def test_a_repair_preserves_a_multiword_segment_and_its_duration() -> None:
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-1",
                speaker_label="speaker-1",
                start_seconds=0.0,
                end_seconds=14.4,
                text="Long",
                words=(("Long", 0.0, 14.4),),
            ),
            CanonicalSegment(
                segment_id="seg-2",
                speaker_label="speaker-2",
                start_seconds=14.5,
                end_seconds=15.4,
                text="and more",
                words=(
                    ("and", 14.5, 14.8),
                    ("more", 15.0, 15.4),
                ),
            ),
            CanonicalSegment(
                segment_id="seg-3",
                speaker_label="speaker-1",
                start_seconds=15.5,
                end_seconds=16.0,
                text="Next.",
                words=(("Next.", 15.5, 16.0),),
            ),
        ),
        pipeline_version="v6+consolidation+adjudication",
        diarization_model="d",
        transcription_model="t",
        alignment_model="a",
        boundary_adjudication_json=json.dumps(
            {
                "policy_version": "boundary-ownership-adjudication-v1",
                "dispute_count": 1,
                "changed_count": 0,
                "marked_count": 0,
                "decision_records": [],
                "pending": {
                    "disputes": [
                        {
                            "dispute_id": "dispute-14.5-speaker-2",
                            "words": [
                                {
                                    "text": "and",
                                    "start_seconds": 14.5,
                                    "end_seconds": 14.8,
                                },
                                {
                                    "text": "more",
                                    "start_seconds": 15.0,
                                    "end_seconds": 15.4,
                                },
                            ],
                            "current_owner": "speaker-2",
                            "counterpart": "speaker-1",
                            "context_before": ["Long"],
                            "context_after": ["Next."],
                        }
                    ],
                    "voiceprints": {
                        "dispute-14.5-speaker-2": None
                    },
                },
            }
        ),
    )

    completion = complete_boundary_adjudication(
        analysis,
        ReassigningAdapter(),
    )

    segments = completion.analysis.segments
    assert [segment.text for segment in segments] == [
        "Long",
        "and more",
        "Next.",
    ]
    assert segments[1].speaker_label == "speaker-1"
    assert segments[1].start_seconds == 14.5
    assert segments[1].end_seconds == 15.4
    assert segments[1].words == (
        ("and", 14.5, 14.8),
        ("more", 15.0, 15.4),
    )


def test_a_changed_verdict_must_match_a_checkpoint_segment() -> None:
    analysis = _analysis(
        (
            _segment("seg-1", "speaker-1", 0.0, 1.0, "Done."),
            _segment("seg-2", "speaker-2", 1.2, 1.4, "and"),
            _segment("seg-3", "speaker-1", 1.5, 3.0, "Next."),
        )
    )

    with pytest.raises(
        ValueError,
        match="does not match a Canonical Transcript segment",
    ):
        complete_boundary_adjudication(
            analysis,
            ReassigningAdapter(),
        )


def test_a_review_marker_must_match_a_checkpoint_segment() -> None:
    analysis = _analysis(
        (
            _segment("seg-1", "speaker-1", 0.0, 1.0, "Done."),
            _segment("seg-3", "speaker-1", 1.5, 3.0, "Next."),
        )
    )

    with pytest.raises(
        ValueError,
        match="Ownership dispute",
    ):
        complete_boundary_adjudication(
            analysis,
            AbstainingAdapter(),
        )
