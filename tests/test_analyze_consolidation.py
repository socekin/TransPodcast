from __future__ import annotations

import hashlib

from transpodcast.modal_pipeline import analyze


def _turns() -> list[dict]:
    # A phantom cold-open label (speaker-0) that is really the same
    # voice as speaker-1, plus a genuinely distinct speaker-2.
    return [
        {"speaker": "speaker-0", "start_seconds": 0.0, "end_seconds": 4.0},
        {"speaker": "speaker-0", "start_seconds": 4.5, "end_seconds": 8.5},
        {"speaker": "speaker-0", "start_seconds": 9.0, "end_seconds": 13.0},
        {"speaker": "speaker-1", "start_seconds": 100.0, "end_seconds": 112.0},
        {"speaker": "speaker-1", "start_seconds": 200.0, "end_seconds": 212.0},
        {"speaker": "speaker-1", "start_seconds": 300.0, "end_seconds": 312.0},
        {"speaker": "speaker-2", "start_seconds": 150.0, "end_seconds": 162.0},
        {"speaker": "speaker-2", "start_seconds": 250.0, "end_seconds": 262.0},
        {"speaker": "speaker-2", "start_seconds": 350.0, "end_seconds": 362.0},
    ]


def _words() -> list[dict]:
    return [
        {"text": "cold", "start_seconds": 1.0, "end_seconds": 1.4},
        {"text": "open", "start_seconds": 1.5, "end_seconds": 1.9},
        {"text": "main1", "start_seconds": 100.5, "end_seconds": 103.0},
        {"text": "other1", "start_seconds": 150.5, "end_seconds": 153.0},
        {"text": "main2", "start_seconds": 200.5, "end_seconds": 203.0},
        {"text": "other2", "start_seconds": 250.5, "end_seconds": 253.0},
        {"text": "main3", "start_seconds": 300.5, "end_seconds": 303.0},
        {"text": "other3", "start_seconds": 350.5, "end_seconds": 353.0},
    ]


def _vector_for(key: str) -> list[float]:
    if "speaker-2" in key:
        return [0.0, 1.0]
    return [1.0, 0.0]


def test_v10_consolidation_merges_maps_and_records_by_default(
    tmp_path,
    monkeypatch,
) -> None:
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
                "text": (
                    "Cold open main1 other1 main2 other2 main3 other3."
                ),
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
        for label in ("speaker-0", "speaker-1", "speaker-2"):
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
    result = analyze(
        {
            "source": {
                "audio_path": str(source),
                "checksum": checksum,
            }
        },
        tmp_path / "output",
    )

    # The phantom label is merged and every segment is remapped.
    consolidation = result["speaker_consolidation"]
    assert consolidation["speaker_mapping"] == {
        "speaker-0": "speaker-1",
        "speaker-1": "speaker-1",
        "speaker-2": "speaker-2",
    }
    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == [
        "speaker-1",
        "speaker-1",
        "speaker-2",
        "speaker-1",
        "speaker-2",
        "speaker-1",
        "speaker-2",
    ]

    # Speakers collapse to canonical labels with centroid embeddings
    # and freshly selected references.
    speakers = {
        speaker["local_label"]: speaker
        for speaker in result["speakers"]
    }
    assert set(speakers) == {"speaker-1", "speaker-2"}
    assert speakers["speaker-1"]["embedding"] == [1.0, 0.0]
    assert speakers["speaker-2"]["embedding"] == [0.0, 1.0]

    # Reference selection avoided the intro and outro zones.
    selection = consolidation["reference_selection"]
    assert selection["speaker-1"]["start_seconds"] == 100.0
    assert selection["speaker-2"]["start_seconds"] == 150.0

    # Every decision is recorded: consolidation verdicts, selections,
    # and one attribution record per segment.
    kinds = [
        record["kind"] for record in consolidation["decision_records"]
    ]
    assert kinds.count("consolidation") == 3
    assert kinds.count("reference-selection") == 2
    assert kinds.count("segment-attribution") == len(
        result["segments"]
    )
    attribution = [
        record
        for record in consolidation["decision_records"]
        if record["kind"] == "segment-attribution"
    ]
    assert all(
        record["policy_version"] == result["pipeline_version"]
        for record in attribution
    )
