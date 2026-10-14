from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from transpodcast.adapters.speech import (
    ModalCommandAnalysisAdapter,
    ModalCommandSpeechAdapter,
)
from transpodcast.domain import SourceArchive, SourceCandidate, TranslationResult
from transpodcast.errors import ProviderExecutionError
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V16


@pytest.mark.parametrize(
    "version,analysis_version",
    [
        ("transpodcast-modal-pipeline-v16", "transpodcast-modal-pipeline-v15"),
        ("transpodcast-modal-pipeline-v18", "transpodcast-modal-pipeline-v17"),
    ],
)
def test_analysis_adapter_preserves_canonicalization_evidence(
    monkeypatch,
    version,
    analysis_version,
) -> None:
    from transpodcast.adapters import speech

    monkeypatch.setattr(speech, "_pipeline_readiness", lambda command: True)

    request_payload: dict[str, object] = {}

    def fake_pipeline(
        command,
        *,
        operation,
        request_path,
        response_path,
    ):
        del command, operation, response_path
        request_payload.update(json.loads(request_path.read_text()))
        reference_path = request_path.parent / "speaker-a.wav"
        reference_path.write_bytes(b"reference")
        return {
            "pipeline_version": "transpodcast-modal-pipeline-v8",
            "diarization_model": "sortformer",
            "diarization_model_revision": "sortformer-revision",
            "transcription_model": "qwen3-asr",
            "alignment_model": "qwen3-forced-aligner",
            "segments": [
                {
                    "segment_id": "seg-0001",
                    "speaker_label": "speaker-a",
                    "start_seconds": 0.0,
                    "end_seconds": 1.0,
                    "text": "Hello.",
                    "words": [
                        {
                            "text": "Hello",
                            "start_seconds": 0.0,
                            "end_seconds": 1.0,
                        }
                    ],
                    "omitted_as_sponsor": True,
                }
            ],
            "speakers": [
                {
                    "local_label": "speaker-a",
                    "reference_audio_path": str(reference_path),
                    "embedding": [1.0, 0.0],
                    "confidence": 0.98,
                }
            ],
            "raw_diarization_turns": [
                {
                    "speaker": "speaker-raw",
                    "start_seconds": 0.0,
                    "end_seconds": 1.0,
                }
            ],
            "full_asr_text": "Hello.",
            "aligned_words": [
                {
                    "text": "Hello",
                    "start_seconds": 0.0,
                    "end_seconds": 1.0,
                }
            ],
            "boundary_stabilization": {
                "summary": {"policy_version": "speaker-boundary-stabilization-v2"},
                "decisions": [],
            },
            "source_canonicalization": {
                "policy_version": "source-analysis-canonicalization-v1",
                "speaker_mapping": {"speaker-raw": "speaker-a"},
                "selected_reference_windows": {"speaker-a": "speaker-raw-w0001"},
            },
            "checksum": "analysis-checksum",
            "estimated_cost_usd": 0.01,
            "call_seconds": {"speaker_embeddings": 0.2},
            "routing": {
                "operation": "analyze",
                "selected_profile": "transpodcast-secondary",
                "correlation_id": "analysis-attempt-7",
                "attempts": [
                    {
                        "profile": "transpodcast-primary",
                        "outcome": "failed",
                        "reason": "command_failed",
                    },
                    {
                        "profile": "transpodcast-secondary",
                        "outcome": "succeeded",
                    },
                ],
            },
        }

    monkeypatch.setattr(speech, "_run_pipeline_command", fake_pipeline)
    source_audio = b"source"
    archive = SourceArchive(
        content=source_audio,
        extension="m4a",
        media_type="audio/mp4",
        checksum=hashlib.sha256(source_audio).hexdigest(),
    )
    candidate = SourceCandidate(
        speech_pipeline_version=version,
        youtube_video_id="video-1",
        title="Fixture",
        description="",
        duration_seconds=1,
        language="en",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_url="https://example.test/video-1",
        captions=None,
        processing_run_id=15,
        processing_attempt_id=7,
        processing_correlation_id="analysis-attempt-7",
    )

    result = ModalCommandAnalysisAdapter("python").analyze(
        candidate,
        archive,
    )

    assert result.pipeline_version == "transpodcast-modal-pipeline-v8"
    assert result.diarization_model_revision == "sortformer-revision"
    assert result.speakers[0].reference_audio == b"reference"
    assert result.segments[0].omitted_as_sponsor is False
    assert request_payload["analysis"]["pipeline_version"] == analysis_version
    assert "detect_sponsors" not in request_payload["analysis"]
    assert request_payload["routing_context"] == {
        "processing_run_id": 15,
        "processing_attempt_id": 7,
        "correlation_id": "analysis-attempt-7",
        "stage": "source_analysis",
    }
    assert json.loads(result.routing_json)["selected_profile"] == (
        "transpodcast-secondary"
    )
    assert json.loads(result.source_canonicalization_json) == {
        "policy_version": "source-analysis-canonicalization-v1",
        "selected_reference_windows": {"speaker-a": "speaker-raw-w0001"},
        "speaker_mapping": {"speaker-raw": "speaker-a"},
    }


def test_speech_adapter_routes_v16_to_indextts25_and_records_identity(
    tmp_path,
    monkeypatch,
) -> None:
    from transpodcast.adapters import speech

    monkeypatch.setattr(speech, "_pipeline_readiness", lambda command: True)
    captured: dict[str, object] = {}

    def fake_pipeline(command, *, operation, request_path, response_path):
        del command, operation, response_path
        captured.update(json.loads(request_path.read_text(encoding="utf-8")))
        media_path = tmp_path / "publication.m4a"
        media_path.write_bytes(b"audio")
        return {
            "publication_audio_path": str(media_path),
            "duration_seconds": 1,
            "pipeline_version": SPEECH_PIPELINE_V16,
            "render_model": "IndexTeam/IndexTTS-2.5",
            "render_model_revision": "model-revision",
            "render_code_commit": "code-commit",
            "render_settings": {
                "renderer": "indextts25",
                "lang": "zh",
                "speech_text_hashes": [
                    {
                        "render_unit_id": "seg-0001",
                        "sha256": "adapted-text-sha",
                    }
                ],
            },
            "quality": {
                "codec": "AAC-LC",
                "sample_rate_hz": 48000,
                "channels": 2,
                "bitrate_kbps": 128,
                "loudness_lufs": -16,
                "true_peak_dbtp": -1,
                "clipping": False,
                "rendered_segments": 1,
                "timeline_continuous": True,
                "boundary_artifacts": False,
                "source_language_leakage": False,
            },
            "segment_timings": [{"start_seconds": 0.0, "end_seconds": 1.0}],
        }

    monkeypatch.setattr(speech, "_run_pipeline_command", fake_pipeline)
    candidate = SourceCandidate(
        youtube_video_id="video-25",
        title="Fixture",
        description="",
        duration_seconds=1,
        language="en",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_url="https://example.test/video-25",
        captions=None,
        analysis_json=json.dumps({"segments": []}),
        source_archive=SourceArchive(
            content=b"source",
            extension="m4a",
            media_type="audio/mp4",
            checksum=hashlib.sha256(b"source").hexdigest(),
        ),
        speech_pipeline_version=SPEECH_PIPELINE_V16,
    )
    translation = TranslationResult(
        title="Title",
        overview="Overview",
        target_locale="zh-CN",
        segments=("Tesla 和 SpaceX。",),
        provider="fixture",
        model="fixture",
        input_tokens=0,
        output_tokens=0,
    )

    result = ModalCommandSpeechAdapter("python").render(
        candidate,
        translation,
    )

    assert captured["rendering"]["renderer"] == "indextts25"
    assert captured["rendering"]["pipeline_version"] == SPEECH_PIPELINE_V16
    assert result.render_model == "IndexTeam/IndexTTS-2.5"
    assert result.render_model_revision == "model-revision"
    assert result.render_code_commit == "code-commit"
    assert json.loads(result.render_settings_json)["lang"] == "zh"
    assert json.loads(result.render_settings_json)["speech_text_hashes"] == [
        {
            "render_unit_id": "seg-0001",
            "sha256": "adapted-text-sha",
        }
    ]


def test_failed_modal_command_preserves_safe_routing_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    from transpodcast.adapters import speech

    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text("{}", encoding="utf-8")
    response_path.write_text(
        json.dumps(
            {
                "routing": {
                    "operation": "analyze",
                    "correlation_id": "attempt-42",
                    "processing_run_id": 15,
                    "processing_attempt_id": 42,
                    "stage": "source_analysis",
                    "attempts": [
                        {
                            "profile": "transpodcast-primary",
                            "outcome": "failed",
                            "reason": "command_failed",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        speech.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="secret provider detail",
        ),
    )

    with pytest.raises(ProviderExecutionError) as caught:
        speech._run_pipeline_command(
            ["pipeline"],
            operation="analyze",
            request_path=request_path,
            response_path=response_path,
        )

    routing = json.loads(caught.value.routing_json)
    assert routing["processing_run_id"] == 15
    assert routing["processing_attempt_id"] == 42
    assert routing["attempts"][0]["profile"] == "transpodcast-primary"
    assert "secret provider detail" not in str(caught.value)
