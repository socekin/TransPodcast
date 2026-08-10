from __future__ import annotations

from transpodcast.boundary_comparison import build_boundary_comparison
from transpodcast.domain import (
    AnalyzedSpeaker,
    CanonicalSegment,
    SourceAnalysisResult,
)


def test_comparison_is_partial_bounded_and_preserves_word_coverage() -> None:
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0127",
                speaker_label="speaker-a",
                start_seconds=0.0,
                end_seconds=0.8,
                text="can you slow down a",
                words=(
                    ("can", 0.0, 0.15),
                    ("you", 0.15, 0.3),
                    ("slow", 0.3, 0.5),
                    ("down", 0.5, 0.7),
                    ("a", 0.7, 0.8),
                ),
            ),
            CanonicalSegment(
                segment_id="seg-0128",
                speaker_label="speaker-b",
                start_seconds=0.8,
                end_seconds=1.0,
                text="bit",
                words=(("bit", 0.8, 1.0),),
                overlap=True,
            ),
            CanonicalSegment(
                segment_id="seg-0129",
                speaker_label="speaker-a",
                start_seconds=1.0,
                end_seconds=1.4,
                text="I tried",
                words=(("I", 1.0, 1.15), ("tried", 1.15, 1.4)),
            ),
        ),
        pipeline_version="transpodcast-modal-pipeline-v6",
        diarization_model="sortformer",
        transcription_model="qwen3-asr",
        alignment_model="qwen3-forced-aligner",
        speakers=(
            AnalyzedSpeaker(
                local_label="speaker-a",
                reference_audio=b"",
                embedding=(1.0, 0.0),
                confidence=0.9,
            ),
            AnalyzedSpeaker(
                local_label="speaker-b",
                reference_audio=b"",
                embedding=(0.99, 0.01),
                confidence=0.9,
            ),
        ),
    )

    comparison = build_boundary_comparison(
        analysis,
        segment_timings=((0.0, 0.7), (0.7, 0.9), (0.9, 1.3)),
        run_id=7,
        publication_guid="episode-guid",
        title="Episode",
        audition_limit=2,
    )

    assert comparison["evidence"]["partial"] is True
    assert comparison["invariants"]["word_coverage_preserved"] is True
    assert comparison["before"]["speaker_transitions"] == 2
    assert comparison["after"]["speaker_transitions"] == 0
    assert comparison["stabilization"]["corrected_count"] == 1
    assert comparison["known_examples"][0]["status"] == "corrected"
    assert comparison["identity_drift"][0]["speaker_labels"] == [
        "speaker-a",
        "speaker-b",
    ]
    assert comparison["unresolved_by_speaker"] == []
    assert len(comparison["audition"]) <= 2


def test_comparison_anchors_owner_confirmed_so_like_example() -> None:
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0289",
                speaker_label="speaker-a",
                start_seconds=0.0,
                end_seconds=0.8,
                text="scores have gotten different",
                words=(
                    ("scores", 0.0, 0.2),
                    ("have", 0.2, 0.4),
                    ("gotten", 0.4, 0.6),
                    ("different", 0.6, 0.8),
                ),
            ),
            CanonicalSegment(
                segment_id="seg-0290",
                speaker_label="speaker-b",
                start_seconds=0.8,
                end_seconds=1.2,
                text="So like",
                words=(("So", 0.8, 1.0), ("like", 1.0, 1.2)),
                overlap=True,
            ),
            CanonicalSegment(
                segment_id="seg-0291",
                speaker_label="speaker-a",
                start_seconds=1.2,
                end_seconds=1.6,
                text="we continue",
                words=(("we", 1.2, 1.4), ("continue", 1.4, 1.6)),
            ),
        ),
        pipeline_version="transpodcast-modal-pipeline-v6",
        diarization_model="sortformer",
        transcription_model="qwen3-asr",
        alignment_model="qwen3-forced-aligner",
    )

    comparison = build_boundary_comparison(
        analysis,
        run_id=7,
        publication_guid="episode-guid",
        title="Episode",
    )
    known_examples = {
        item["name"]: item for item in comparison["known_examples"]
    }

    assert known_examples["run-7-slow-down-a-bit"]["status"] == "absent"
    assert known_examples["run-7-so-like"]["status"] == "corrected"
