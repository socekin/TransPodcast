from __future__ import annotations

import hashlib

import pytest

from transpodcast.modal_pipeline import analyze


def _turns() -> list[dict]:
    # speaker-1 keeps talking; a 0.4s speaker-2 sliver steals 'and'.
    return [
        {"speaker": "speaker-1", "start_seconds": 100.0, "end_seconds": 110.8},
        {"speaker": "speaker-2", "start_seconds": 110.9, "end_seconds": 111.3},
        {"speaker": "speaker-1", "start_seconds": 111.4, "end_seconds": 122.0},
        {"speaker": "speaker-1", "start_seconds": 200.0, "end_seconds": 212.0},
        {"speaker": "speaker-1", "start_seconds": 300.0, "end_seconds": 312.0},
        {"speaker": "speaker-2", "start_seconds": 150.0, "end_seconds": 162.0},
        {"speaker": "speaker-2", "start_seconds": 250.0, "end_seconds": 262.0},
        {"speaker": "speaker-2", "start_seconds": 350.0, "end_seconds": 362.0},
    ]


def _words() -> list[dict]:
    return [
        {"text": "we", "start_seconds": 100.5, "end_seconds": 103.0},
        {"text": "and", "start_seconds": 110.95, "end_seconds": 111.25},
        {"text": "continue", "start_seconds": 111.5, "end_seconds": 114.0},
        {"text": "other", "start_seconds": 150.5, "end_seconds": 153.0},
        {"text": "steady", "start_seconds": 200.5, "end_seconds": 203.0},
        {"text": "stable", "start_seconds": 250.5, "end_seconds": 253.0},
        {"text": "speaker", "start_seconds": 300.5, "end_seconds": 303.0},
        {"text": "turn", "start_seconds": 350.5, "end_seconds": 353.0},
    ]


def _vector_for(key: str) -> list[float]:
    if key.startswith("dispute-"):
        # The disputed fragment sounds like speaker-1.
        return [1.0, 0.0]
    if "speaker-2" in key:
        return [0.0, 1.0]
    return [1.0, 0.0]


@pytest.fixture()
def analyzed(tmp_path, monkeypatch):
    from transpodcast import modal_pipeline

    source = tmp_path / "source.webm"
    source.write_bytes(b"source-audio")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    turns = _turns()
    words = _words()

    def fake_transcode(source_path, destination, **kwargs):
        del source_path, kwargs
        destination.write_bytes(b"wav")

    def fake_remote(app_name, class_name, method_name, payload):
        del class_name, method_name
        if app_name == modal_pipeline.SORTFORMER_APP:
            return {
                "segments": turns,
                "model_id": "sortformer",
                "inference_seconds": 1.0,
            }
        if app_name == modal_pipeline.QWEN_ASR_APP:
            return {
                "text": "We and continue other steady stable speaker turn.",
                "words": words,
                "asr_model_id": "qwen3-asr",
                "aligner_model_id": "qwen3-forced-aligner",
                "inference_seconds": 1.0,
            }
        return {
            "embeddings": {
                key: _vector_for(key) for key in payload
            },
            "inference_seconds": 1.0,
        }

    def fake_reference_audio(
        source_wav,
        destination,
        *,
        start_seconds,
        duration_seconds,
    ):
        del source_wav, start_seconds, duration_seconds
        destination.write_bytes(b"clip")

    def fake_references(source_wav, diarization, reference_root):
        del source_wav, diarization
        references = {}
        for label in ("speaker-1", "speaker-2"):
            path = reference_root / f"{label}.wav"
            path.write_bytes(label.encode())
            references[label] = path
        return references

    monkeypatch.setattr(modal_pipeline, "_transcode", fake_transcode)
    monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_reference_audio",
        fake_reference_audio,
    )
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_speaker_references",
        fake_references,
    )
    return analyze(
        {
            "source": {
                "audio_path": str(source),
                "checksum": checksum,
            }
        },
        tmp_path / "output",
    )


# --- Source Analysis stays acoustic: it emits, it never asks ---


def test_analysis_emits_pending_disputes_without_changing_ownership(
    analyzed,
) -> None:
    adjudication = analyzed["boundary_adjudication"]
    assert adjudication["dispute_count"] == 1
    assert adjudication["candidate_count"] == 1
    assert adjudication["short_boundary_candidate_count"] == 1
    assert adjudication["sparse_label_candidate_count"] == 0
    assert adjudication["stable_segment_count"] == 7
    assert adjudication["changed_count"] == 0
    assert adjudication["decision_records"] == []
    pending = adjudication["pending"]
    assert len(pending["disputes"]) == 1
    dispute = pending["disputes"][0]
    assert dispute["current_owner"] == "speaker-2"
    assert dispute["counterpart"] == "speaker-1"
    voiceprint = pending["voiceprints"][dispute["dispute_id"]]
    assert voiceprint["owner"] == "speaker-1"
    stolen = next(
        segment
        for segment in analyzed["segments"]
        if segment["text"] == "and"
    )
    assert stolen["speaker_label"] == "speaker-2"
    assert stolen.get("review_state", "accepted") == "accepted"


def test_analysis_defers_attribution_records_while_pending(
    analyzed,
) -> None:
    kinds = {
        record["kind"]
        for record in analyzed["speaker_consolidation"][
            "decision_records"
        ]
    }
    assert "segment-attribution" not in kinds
