from __future__ import annotations

from scripts.speech_pipeline_v15_comparison import build_v15_structural_comparison
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult


def test_structural_comparison_preserves_words_and_replaces_fallback_cuts() -> None:
    words = tuple(
        (
            f"word-{index}",
            float(index) + (0.5 if index >= 12 else 0.0),
            float(index + 1) + (0.5 if index >= 12 else 0.0),
        )
        for index in range(24)
    )
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-1",
                speaker_label="speaker-a",
                start_seconds=0.0,
                end_seconds=15.5,
                text=" ".join(word[0] for word in words[:15]),
                words=words[:15],
            ),
            CanonicalSegment(
                segment_id="seg-2",
                speaker_label="speaker-a",
                start_seconds=15.5,
                end_seconds=24.5,
                text=" ".join(word[0] for word in words[15:]),
                words=words[15:],
            ),
        ),
        pipeline_version="transpodcast-modal-pipeline-v14",
        diarization_model="fixture-sortformer",
        transcription_model="fixture-asr",
        alignment_model="fixture-aligner",
        full_asr_text=" ".join(word[0] for word in words),
        aligned_words=words,
    )

    comparison = build_v15_structural_comparison(analysis)

    assert comparison["before"]["boundary_counts"]["fallback"] == 1
    assert comparison["after"]["segment_count"] == 2
    assert comparison["after"]["boundary_counts"]["fallback"] == 0
    assert all(comparison["invariants"].values())
    assert comparison["ownership"]["batch_count"] == 0
    assert comparison["ownership"]["maximum_request_characters"] == 0
