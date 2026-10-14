"""The speech adapter requests the Listening Audit and reads measured fields."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from transpodcast.adapters import speech
from transpodcast.domain import (
    AudioQualityReport,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.pipeline_versions import (
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V19,
    SPEECH_PIPELINE_V20,
)


def _candidate(version: str) -> SourceCandidate:
    return SourceCandidate(
        youtube_video_id="video",
        title="Fixture",
        description="",
        duration_seconds=1,
        language="en",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_url="https://example.test/video",
        captions=None,
        analysis_json=json.dumps({"segments": []}),
        processing_run_id=31,
        speech_pipeline_version=version,
    )


def test_adapter_requests_the_audit_only_for_rhythm_pipelines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_pipeline(command, *, operation, request_path, response_path):
        del command, operation, response_path
        captured.append(json.loads(request_path.read_text(encoding="utf-8")))
        media_path = tmp_path / "publication.m4a"
        media_path.write_bytes(b"audio")
        return {
            "publication_audio_path": str(media_path),
            "duration_seconds": 1,
            "pipeline_version": "fixture",
            "quality": {"timeline_continuous": True},
        }

    monkeypatch.setattr(speech, "_pipeline_readiness", lambda command: True)
    monkeypatch.setattr(speech, "_run_pipeline_command", fake_pipeline)
    translation = TranslationResult(
        title="Title",
        overview="Overview",
        target_locale="zh-CN",
        segments=("你好。",),
        provider="fixture",
        model="fixture",
        input_tokens=0,
        output_tokens=0,
    )
    adapter = speech.ModalCommandSpeechAdapter("python")
    for version in (SPEECH_PIPELINE_V17, SPEECH_PIPELINE_V19, SPEECH_PIPELINE_V20):
        adapter.render(_candidate(version), translation)

    assert [item["rendering"]["listening_audit"] for item in captured] == [
        False,
        True,
        True,
    ]


def test_quality_report_trusts_only_measured_flags() -> None:
    historical = speech._audio_quality_report(
        {
            "timeline_continuous": True,
            "boundary_artifacts": False,
            "source_language_leakage": False,
        }
    )
    measured = speech._audio_quality_report(
        {"timeline_continuous": True, "source_language_leakage": True}
    )
    malformed = speech._audio_quality_report({})

    assert (historical.boundary_artifacts, historical.source_language_leakage) == (
        False,
        False,
    )
    assert measured.boundary_artifacts is None
    assert measured.source_language_leakage is True
    assert malformed.timeline_continuous is False
    assert malformed.source_language_leakage is None


def test_remaster_keeps_the_render_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_pipeline(command, *, operation, request_path, response_path):
        del command, request_path, response_path
        assert operation == "remaster"
        media_path = tmp_path / "remastered.m4a"
        media_path.write_bytes(b"remastered")
        return {
            "publication_audio_path": str(media_path),
            "duration_seconds": 10,
            "pipeline_version": SPEECH_PIPELINE_V19,
            "quality": {
                "codec": "AAC-LC",
                "sample_rate_hz": 48000,
                "channels": 2,
                "bitrate_kbps": 118,
                "loudness_lufs": -16.0,
                "true_peak_dbtp": -1.2,
                "clipping": False,
                "rendered_segments": 2,
            },
        }

    monkeypatch.setattr(speech, "_run_pipeline_command", fake_pipeline)
    rendered = SpeechResult(
        publication_audio=b"M4A!rendered",
        duration_seconds=10,
        chapters=(),
        pipeline_version=SPEECH_PIPELINE_V19,
        quality=AudioQualityReport(
            true_peak_dbtp=-0.5,
            timeline_continuous=True,
            source_language_leakage=True,
        ),
        render_settings_json=json.dumps({"listening_audit": {"status": "complete"}}),
    )

    repaired = speech.ModalCommandSpeechAdapter("python").remaster(rendered)

    assert repaired.quality.bitrate_kbps == 118
    assert repaired.quality.timeline_continuous is True
    assert repaired.quality.source_language_leakage is True
    assert repaired.render_settings_json == rendered.render_settings_json


@pytest.mark.parametrize(("available", "expected"), [(True, True), (False, False)])
def test_audit_readiness_comes_from_the_pipeline_readiness_operation(
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
    expected: bool,
) -> None:
    calls: list[str] = []

    def fake_pipeline(command, *, operation, request_path, response_path):
        del command, request_path, response_path
        calls.append(operation)
        return {"ready": True, "listening_audit": {"available": available}}

    monkeypatch.setattr(speech, "_READINESS_CACHE", {})
    monkeypatch.setattr(speech.shutil, "which", lambda _name: "/usr/bin/fixture")
    monkeypatch.setattr(speech, "_run_pipeline_command", fake_pipeline)
    adapter = speech.ModalCommandSpeechAdapter("fixture-pipeline")

    assert adapter.readiness() is True
    assert adapter.listening_audit_readiness() is expected
    # One cached readiness operation answers both questions.
    assert calls == ["readiness"]
