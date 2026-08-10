from __future__ import annotations

import hashlib

import pytest

from transpodcast.modal_pipeline import (
    align_transcript,
    analyze,
    detect_source_events,
    normalize_aligned_segments,
)


def test_render_rejects_empty_target_before_starting_tts(tmp_path) -> None:
    from transpodcast import modal_pipeline

    request = {
        "source": {
            "audio_path": str(tmp_path / "source.wav"),
            "analysis": {
                "pipeline_version": modal_pipeline.PIPELINE_VERSION,
                "segments": [
                    {
                        "segment_id": "seg-0001",
                        "speaker_label": "speaker-01",
                        "start_seconds": 0.0,
                        "end_seconds": 1.0,
                    }
                ],
                "speakers": [],
            },
        },
        "translation": {"segments": [""]},
        "rendering": {},
    }

    with pytest.raises(
        ValueError,
        match="Translation contains empty target segments: seg-0001",
    ):
        modal_pipeline.render(request, tmp_path / "output")


def test_alignment_uses_speaker_and_sentence_boundaries() -> None:
    words = [
        {"text": "Hello.", "start_seconds": 0.0, "end_seconds": 0.5},
        {"text": "Welcome", "start_seconds": 0.6, "end_seconds": 0.9},
        {"text": "back.", "start_seconds": 0.9, "end_seconds": 1.2},
    ]
    turns = [
        {"speaker": "speaker-01", "start_seconds": 0.0, "end_seconds": 0.55},
        {"speaker": "speaker-02", "start_seconds": 0.55, "end_seconds": 1.3},
    ]

    segments = align_transcript(words, turns)

    assert [item["speaker_label"] for item in segments] == [
        "speaker-01",
        "speaker-02",
    ]
    assert [item["text"] for item in segments] == [
        "Hello.",
        "Welcome back.",
    ]


def test_alignment_attaches_zero_duration_words_to_neighboring_speech() -> None:
    words = [
        {"text": "We", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "can", "start_seconds": 0.4, "end_seconds": 0.4},
        {"text": "continue.", "start_seconds": 0.4, "end_seconds": 1.0},
    ]
    turns = [
        {"speaker": "speaker-01", "start_seconds": 0.0, "end_seconds": 1.0}
    ]

    segments = align_transcript(words, turns)

    assert len(segments) == 1
    assert segments[0]["speaker_label"] == "speaker-01"
    assert segments[0]["start_seconds"] == 0.0
    assert segments[0]["end_seconds"] == 1.0
    assert segments[0]["text"] == "We can continue."


def test_alignment_preserves_raw_diarization_boundaries() -> None:
    # The accepted v6 baseline assigns every word to the turn with the
    # largest positive overlap and never second-guesses boundary jitter;
    # ownership corrections belong to a later, evidence-gated layer.
    words = [
        {"text": "slow", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "down", "start_seconds": 0.4, "end_seconds": 0.8},
        {"text": "a", "start_seconds": 0.8, "end_seconds": 1.0},
        {"text": "bit", "start_seconds": 1.0, "end_seconds": 1.2},
        {"text": "please", "start_seconds": 1.2, "end_seconds": 1.6},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 1.06},
        {"speaker": "speaker-b", "start_seconds": 1.02, "end_seconds": 1.18},
        {"speaker": "speaker-a", "start_seconds": 1.16, "end_seconds": 1.6},
    ]

    segments = align_transcript(words, turns)

    assert [item["speaker_label"] for item in segments] == [
        "speaker-a",
        "speaker-b",
        "speaker-a",
    ]
    assert [item["text"] for item in segments] == [
        "slow down a",
        "bit",
        "please",
    ]


def test_v13_is_the_single_auditable_production_pipeline() -> None:
    from transpodcast import modal_pipeline

    assert modal_pipeline.PIPELINE_VERSION == "transpodcast-modal-pipeline-v14"
    assert (
        modal_pipeline._pipeline_version_for_analysis(
            {"pipeline_version": "transpodcast-modal-pipeline-v8"}
        )
        == "transpodcast-modal-pipeline-v8"
    )


def test_retired_speaker_flags_are_gone_and_stale_env_is_ignored(
    monkeypatch,
) -> None:
    from transpodcast.config import TransPodcastSettings

    monkeypatch.setenv(
        "TRANSPODCAST_SPEAKER_BOUNDARY_STABILIZATION_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "TRANSPODCAST_SOURCE_CANONICALIZATION_ENABLED",
        "true",
    )

    monkeypatch.setenv(
        "TRANSPODCAST_SPEAKER_CONSOLIDATION_ENABLED",
        "false",
    )
    monkeypatch.setenv(
        "TRANSPODCAST_BOUNDARY_ADJUDICATION_ENABLED",
        "false",
    )

    settings = TransPodcastSettings(_env_file=None)

    assert not hasattr(settings, "speaker_boundary_stabilization_enabled")
    assert not hasattr(settings, "source_canonicalization_enabled")
    assert not hasattr(settings, "speaker_consolidation_enabled")
    assert not hasattr(settings, "boundary_adjudication_enabled")


def test_source_analysis_always_persists_replay_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    from transpodcast import modal_pipeline

    source = tmp_path / "source.webm"
    source.write_bytes(b"source-audio")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    words = [
        {"text": "slow", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "down", "start_seconds": 0.4, "end_seconds": 0.8},
        {"text": "a", "start_seconds": 0.8, "end_seconds": 1.0},
        {"text": "bit", "start_seconds": 1.0, "end_seconds": 1.2},
        {"text": "please", "start_seconds": 1.2, "end_seconds": 1.6},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 1.06},
        {"speaker": "speaker-b", "start_seconds": 1.02, "end_seconds": 1.18},
        {"speaker": "speaker-a", "start_seconds": 1.16, "end_seconds": 1.6},
    ]

    def fake_transcode(source_path, destination, **kwargs):
        del source_path, kwargs
        destination.write_bytes(b"wav")

    def fake_remote(app_name, class_name, method_name, payload):
        del class_name, method_name, payload
        if app_name == modal_pipeline.SORTFORMER_APP:
            return {
                "segments": turns,
                "model_id": "sortformer",
                "model_revision": "fixture-sortformer-revision",
                "inference_seconds": 1.0,
            }
        if app_name == modal_pipeline.QWEN_ASR_APP:
            return {
                "text": "Slow down a bit please.",
                "words": words,
                "asr_model_id": "qwen3-asr",
                "aligner_model_id": "qwen3-forced-aligner",
                "inference_seconds": 1.0,
            }
        return {
            "embeddings": {
                "speaker-a": [1.0, 0.0],
                "speaker-b": [0.0, 1.0],
                "speaker-a-window": [1.0, 0.0],
                "speaker-b-window": [0.0, 1.0],
            },
            "inference_seconds": 1.0,
        }

    def fake_references(source_wav, diarization, reference_root):
        del source_wav, diarization
        references = {}
        for label in ("speaker-a", "speaker-b"):
            path = reference_root / f"{label}.wav"
            path.write_bytes(label.encode())
            references[label] = path
        return references

    monkeypatch.setattr(modal_pipeline, "_transcode", fake_transcode)
    monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_speaker_references",
        fake_references,
    )
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_reference_audio",
        lambda source_wav, destination, **kwargs: destination.write_bytes(
            b"clip"
        ),
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

    assert result["pipeline_version"] == "transpodcast-modal-pipeline-v14"
    assert result["boundary_adjudication"]["policy_version"] == (
        "boundary-ownership-adjudication-v4"
    )
    assert result["diarization_model_revision"] == (
        "fixture-sortformer-revision"
    )
    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == ["speaker-a", "speaker-b", "speaker-a"]
    assert "boundary_stabilization" not in result
    assert "source_canonicalization" not in result
    assert result["raw_diarization_turns"] == turns
    assert result["full_asr_text"] == "Slow down a bit please."
    assert result["aligned_words"] == words


def test_normalization_repairs_existing_unknown_micro_segments() -> None:
    segments = [
        {
            "speaker_label": "speaker-01",
            "start_seconds": 0.0,
            "end_seconds": 0.4,
            "text": "We",
            "words": [
                {
                    "text": "We",
                    "start_seconds": 0.0,
                    "end_seconds": 0.4,
                }
            ],
        },
        {
            "speaker_label": "speaker-unknown",
            "start_seconds": 0.4,
            "end_seconds": 0.4,
            "text": "can",
            "words": [["can", 0.4, 0.4]],
        },
        {
            "speaker_label": "speaker-01",
            "start_seconds": 0.4,
            "end_seconds": 1.0,
            "text": "continue.",
            "words": [
                {
                    "text": "continue.",
                    "start_seconds": 0.4,
                    "end_seconds": 1.0,
                }
            ],
        },
    ]

    normalized = normalize_aligned_segments(segments)

    assert len(normalized) == 1
    assert normalized[0]["speaker_label"] == "speaker-01"
    assert normalized[0]["text"] == "We can continue."


def test_only_standalone_laughter_is_safe_to_preserve() -> None:
    segments = [
        {
            "text": "[laughter]",
            "start_seconds": 1.0,
            "end_seconds": 1.8,
            "events": [],
        },
        {
            "text": "That was funny (laughter).",
            "start_seconds": 2.0,
            "end_seconds": 3.0,
            "events": [],
        },
    ]

    events = detect_source_events(segments)

    assert events[0]["safe_to_preserve"] is True
    assert events[1]["safe_to_preserve"] is False


def test_mastering_uses_gentle_compression_and_dynamic_loudness(
    tmp_path,
    monkeypatch,
) -> None:
    from transpodcast import modal_pipeline

    captured: list[str] = []

    def capture_run(arguments: list[str]):
        captured.extend(arguments)
        return None

    monkeypatch.setattr(modal_pipeline, "_run", capture_run)

    modal_pipeline._compress(
        tmp_path / "source.wav",
        tmp_path / "compressed.wav",
    )
    compressor_filter = captured[captured.index("-af") + 1]
    assert "acompressor=" in compressor_filter
    assert "ratio=1.5" in compressor_filter

    captured.clear()
    modal_pipeline._normalize(
        tmp_path / "compressed.wav",
        tmp_path / "master.wav",
        {
            "input_i": "-20.0",
            "input_tp": "-2.0",
            "input_lra": "5.0",
            "input_thresh": "-30.0",
            "target_offset": "0.0",
        },
    )

    audio_filter = captured[captured.index("-af") + 1]
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in audio_filter
    assert "linear=false" in audio_filter


def test_assembly_measures_the_final_encoded_m4a(
    tmp_path,
    monkeypatch,
) -> None:
    import shutil
    import subprocess

    from transpodcast import modal_pipeline

    segment = tmp_path / "segment.wav"
    publication = tmp_path / "publication.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=0.25",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(segment),
        ],
        check=True,
    )
    measured_paths: list[str] = []

    def fake_loudness(path, **kwargs):
        del kwargs
        measured_paths.append(path.name)
        if path.name == "compressed-master-stereo.wav":
            return {
                "input_i": "-20.0",
                "input_tp": "-3.0",
                "input_lra": "4.0",
                "input_thresh": "-30.0",
                "target_offset": "0.0",
            }
        if path.name == "normalized-master-1.wav":
            return {"input_i": "-16.1", "input_tp": "-1.5"}
        return {"input_i": "-15.9", "input_tp": "-1.3"}

    monkeypatch.setattr(modal_pipeline, "_loudness", fake_loudness)
    monkeypatch.setattr(
        modal_pipeline,
        "_compress",
        lambda source, destination: shutil.copyfile(
            source,
            destination,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        modal_pipeline,
        "_to_stereo_pcm",
        lambda source, destination: shutil.copyfile(
            source,
            destination,
        ),
    )
    monkeypatch.setattr(
        modal_pipeline,
        "_normalize",
        lambda source, destination, measurement, **kwargs: shutil.copyfile(
            source,
            destination,
        ),
    )

    result = modal_pipeline.assemble_publication([segment], publication)

    assert measured_paths == [
        "compressed-master-stereo.wav",
        "normalized-master-1.wav",
        "publication.m4a",
    ]
    assert result["quality"]["pre_encode_loudness_lufs"] == -16.1
    assert result["quality"]["pre_encode_true_peak_dbtp"] == -1.5
    assert result["quality"]["loudness_lufs"] == -15.9
    assert result["quality"]["true_peak_dbtp"] == -1.3


def test_real_assembly_meets_final_encoded_delivery_targets(
    tmp_path,
) -> None:
    import subprocess

    from transpodcast import modal_pipeline

    segment = tmp_path / "segment.wav"
    publication = tmp_path / "publication.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=12",
            "-af",
            "volume=4.0",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(segment),
        ],
        check=True,
    )

    result = modal_pipeline.assemble_publication(
        [segment],
        publication,
    )
    quality = result["quality"]

    assert publication.read_bytes()[4:8] == b"ftyp"
    assert -16.5 <= quality["loudness_lufs"] <= -15.5
    assert quality["true_peak_dbtp"] <= -1.0
    assert quality["clipping"] is False
    assert quality["pre_encode_loudness_lufs"] is not None
    assert quality["pre_encode_true_peak_dbtp"] is not None
    assert (
        abs(
            quality["loudness_lufs"]
            - quality["pre_encode_loudness_lufs"]
        )
        <= 0.5
    )


def test_mastering_reencodes_with_more_headroom_after_codec_overshoot(
    tmp_path,
    monkeypatch,
) -> None:
    from transpodcast import modal_pipeline

    measurements = iter(
        [
            {
                "input_i": "-18.0",
                "input_tp": "-2.0",
                "input_lra": "4.0",
                "input_thresh": "-28.0",
                "target_offset": "0.0",
            },
            {"input_i": "-16.0", "input_tp": "-1.5"},
            {"input_i": "-16.0", "input_tp": "-0.6"},
            {
                "input_i": "-18.0",
                "input_tp": "-2.0",
                "input_lra": "4.0",
                "input_thresh": "-28.0",
                "target_offset": "0.0",
            },
            {"input_i": "-16.0", "input_tp": "-2.0"},
            {"input_i": "-16.0", "input_tp": "-1.1"},
        ]
    )
    true_peak_targets: list[float] = []
    encode_count = 0

    monkeypatch.setattr(
        modal_pipeline,
        "_loudness",
        lambda path, **kwargs: next(measurements),
    )

    def fake_normalize(
        source,
        destination,
        measurement,
        *,
        true_peak_dbtp,
    ):
        del source, destination, measurement
        true_peak_targets.append(true_peak_dbtp)

    def fake_encode(source, destination):
        nonlocal encode_count
        del source, destination
        encode_count += 1

    monkeypatch.setattr(modal_pipeline, "_normalize", fake_normalize)
    monkeypatch.setattr(modal_pipeline, "_encode_aac", fake_encode)
    monkeypatch.setattr(
        modal_pipeline,
        "_probe",
        lambda path: {"format": {"duration": "60.0"}},
    )

    result = modal_pipeline._master_stereo_pcm(
        tmp_path / "master.wav",
        tmp_path / "publication.m4a",
        tmp_path,
        rendered_segment_count=10,
    )

    # A 0.4 dB miss escalates by at least double the miss plus the
    # safety margin (historical calibration: matching-size corrections can
    # change nothing against codec overshoot).
    assert true_peak_targets == [-1.5, -2.4]
    assert encode_count == 2
    assert result["quality"]["loudness_lufs"] == -16.0
    assert result["quality"]["true_peak_dbtp"] == -1.1
    assert result["quality"]["clipping"] is False


def test_remaster_repairs_an_existing_encoded_publication(
    tmp_path,
) -> None:
    import subprocess

    from transpodcast import modal_pipeline

    source = tmp_path / "source.m4a"
    repaired = tmp_path / "repaired.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=12",
            "-af",
            "volume=4.0",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(source),
        ],
        check=True,
    )

    result = modal_pipeline.remaster_publication(
        source,
        repaired,
        rendered_segment_count=250,
    )
    quality = result["quality"]

    assert repaired.read_bytes()[4:8] == b"ftyp"
    assert -16.5 <= quality["loudness_lufs"] <= -15.5
    assert quality["true_peak_dbtp"] <= -1.0
    assert quality["clipping"] is False
    assert quality["rendered_segments"] == 250


def test_analyze_overlaps_independent_model_calls(
    tmp_path, monkeypatch
) -> None:
    import hashlib
    import subprocess
    import time

    from transpodcast import modal_pipeline

    source = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=3",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(source),
        ],
        check=True,
    )
    timeline: dict[str, tuple[float, float]] = {}

    def fake_remote(app: str, cls: str, method: str, *args, **kwargs):
        started = time.monotonic()
        if app == modal_pipeline.SORTFORMER_APP:
            time.sleep(0.05)
            payload = {
                "model_id": "fixture-sortformer",
                "inference_seconds": 1.0,
                "segments": [
                    {
                        "start_seconds": 0.0,
                        "end_seconds": 2.0,
                        "speaker": "speaker_0",
                    }
                ],
            }
        elif app == modal_pipeline.QWEN_ASR_APP:
            time.sleep(0.3)
            payload = {
                "asr_model_id": "fixture-asr",
                "aligner_model_id": "fixture-aligner",
                "inference_seconds": 2.0,
                "words": [
                    {
                        "text": "Hello.",
                        "start_seconds": 0.2,
                        "end_seconds": 0.9,
                    }
                ],
            }
        else:
            time.sleep(0.05)
            references = args[0]
            payload = {
                "inference_seconds": 0.5,
                "embeddings": {
                    window_id: [0.1, 0.2, 0.3]
                    for window_id in references
                },
            }
        timeline[app] = (started, time.monotonic())
        return payload

    monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
    request = {
        "source": {
            "youtube_video_id": "fixture-video",
            "title": "Fixture",
            "description": "",
            "language": "en",
            "audio_path": str(source),
            "checksum": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        "analysis": {},
    }

    result = modal_pipeline.analyze(request, tmp_path / "out")

    diarization = timeline[modal_pipeline.SORTFORMER_APP]
    transcription = timeline[modal_pipeline.QWEN_ASR_APP]
    speakers = timeline[modal_pipeline.SPEAKER_APP]
    # Transcription started before diarization finished, and the
    # speaker call overlapped the still-running transcription.
    assert transcription[0] < diarization[1]
    assert speakers[0] < transcription[1]
    assert result["call_seconds"] == {
        "diarization": 1.0,
        "transcription": 2.0,
        "speaker_embeddings": 0.5,
    }
    assert [item["speaker_label"] for item in result["segments"]] == [
        "speaker_0"
    ]


def test_split_render_plan_is_contiguous_and_balanced() -> None:
    from transpodcast.modal_pipeline import split_render_plan

    plan = [
        {"segment_id": f"seg-{index:04d}", "target_text": "x" * length}
        for index, length in enumerate(
            [40, 10, 90, 25, 60, 15, 80, 35, 20, 45], start=1
        )
    ]

    shards = split_render_plan(plan, 4)

    assert len(shards) == 4
    assert [item for shard in shards for item in shard] == plan
    assert all(shard for shard in shards)
    weights = [
        sum(len(item["target_text"]) for item in shard)
        for shard in shards
    ]
    assert max(weights) <= 2.5 * min(weights)


def test_split_render_plan_gives_every_shard_work_despite_skew() -> None:
    from transpodcast.modal_pipeline import split_render_plan

    plan = [
        {"segment_id": f"seg-{index:04d}", "target_text": text}
        for index, text in enumerate(
            ["y" * 500, "a", "b", "c", "d"], start=1
        )
    ]

    shards = split_render_plan(plan, 4)

    assert [item for shard in shards for item in shard] == plan
    assert all(shard for shard in shards)


def test_missing_segment_files_reports_gaps_only() -> None:
    from transpodcast.modal_pipeline import missing_segment_files

    plan = [
        {"segment_id": "seg-0001"},
        {"segment_id": "seg-0002"},
        {"segment_id": "seg-0003"},
    ]
    manifest = {"seg-0001.wav": {}, "seg-0003.wav": {}}

    assert missing_segment_files(plan, manifest) == ["seg-0002"]
