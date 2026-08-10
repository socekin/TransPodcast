from __future__ import annotations

import json

from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.service import _serialize_analysis, deserialize_analysis


def test_source_analysis_round_trip_retains_boundary_evidence() -> None:
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker-a",
                start_seconds=0.0,
                end_seconds=0.5,
                text="Hello.",
                words=(("Hello", 0.0, 0.5),),
            ),
        ),
        pipeline_version="transpodcast-modal-pipeline-v7",
        diarization_model="sortformer",
        diarization_model_revision="sortformer-revision",
        transcription_model="qwen3-asr",
        alignment_model="qwen3-forced-aligner",
        raw_diarization_turns=(("speaker-a", 0.0, 0.5),),
        full_asr_text="Hello.",
        aligned_words=(("Hello", 0.0, 0.5),),
        boundary_stabilization_json=json.dumps(
            {
                "summary": {
                    "policy_version": "speaker-boundary-stabilization-v1",
                    "corrected_count": 0,
                },
                "decisions": [],
            },
            sort_keys=True,
        ),
        source_canonicalization_json=json.dumps(
            {
                "policy_version": "source-analysis-canonicalization-v1",
                "speaker_mapping": {"speaker-a": "speaker-a"},
                "selected_reference_windows": {
                    "speaker-a": "speaker-a-w0001"
                },
            },
            sort_keys=True,
        ),
    )

    restored = deserialize_analysis(_serialize_analysis(analysis))

    assert restored.raw_diarization_turns == (("speaker-a", 0.0, 0.5),)
    assert restored.diarization_model_revision == "sortformer-revision"
    assert restored.full_asr_text == "Hello."
    assert restored.aligned_words == (("Hello", 0.0, 0.5),)
    assert json.loads(restored.boundary_stabilization_json) == {
        "summary": {
            "policy_version": "speaker-boundary-stabilization-v1",
            "corrected_count": 0,
        },
        "decisions": [],
    }
    assert json.loads(restored.source_canonicalization_json) == {
        "policy_version": "source-analysis-canonicalization-v1",
        "speaker_mapping": {"speaker-a": "speaker-a"},
        "selected_reference_windows": {
            "speaker-a": "speaker-a-w0001"
        },
    }


def test_old_source_analysis_defaults_to_partial_boundary_evidence() -> None:
    old_checkpoint = json.dumps(
        {
            "pipeline_version": "transpodcast-modal-pipeline-v6",
            "diarization_model": "sortformer",
            "transcription_model": "qwen3-asr",
            "alignment_model": "qwen3-forced-aligner",
            "segments": [],
        }
    )

    restored = deserialize_analysis(old_checkpoint)

    assert restored.raw_diarization_turns == ()
    assert restored.diarization_model_revision == ""
    assert restored.full_asr_text == ""
    assert restored.aligned_words == ()
    assert restored.boundary_stabilization_json == "{}"
    assert restored.source_canonicalization_json == "{}"
