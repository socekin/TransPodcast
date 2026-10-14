from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import (
    AudioQualityReport,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.service import TransPodcastService
from transpodcast.testing import AcceptanceHarness


class RecoverableSpeechAdapter:
    def __init__(self) -> None:
        self.valid = False
        self.invocation_count = 0
        self.remaster_invocation_count = 0

    def readiness(self) -> bool:
        return True

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        self.invocation_count += 1
        return SpeechResult(
            publication_audio=b"M4A!recoverable-publication-audio",
            duration_seconds=3600,
            chapters=((0, "Introduction"),),
            pipeline_version="recoverable-fixture-v1",
            quality=AudioQualityReport(
                loudness_lufs=-16.0 if self.valid else -13.0,
                true_peak_dbtp=-1.2 if self.valid else -0.6,
                clipping=not self.valid,
                rendered_segments=len(translation.segments),
            ),
            segment_timings=tuple(
                (float(index), float(index + 1))
                for index in range(len(translation.segments))
            ),
        )

    def remaster(self, speech: SpeechResult) -> SpeechResult:
        self.remaster_invocation_count += 1
        return replace(
            speech,
            publication_audio=b"M4A!remastered-publication-audio",
            pipeline_version="recoverable-fixture-v2",
            quality=replace(
                speech.quality,
                loudness_lufs=-16.0,
                true_peak_dbtp=-1.2,
                clipping=False,
            ),
        )


def test_quality_failure_resumes_from_the_latest_valid_checkpoint(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = RecoverableSpeechAdapter()
    dependencies = replace(harness.dependencies, speech=speech)
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")

    failed = client.post("/api/v1/processing-runs:advance")
    assert failed.status_code == 200
    assert failed.json()["processing_state"] == "review_queue"
    assert failed.json()["error_kind"] == "quality_gate"

    review_items = client.get("/api/v1/review-queue").json()["items"]
    assert len(review_items) == 1
    assert review_items[0]["stage"] == "quality_gate"
    assert review_items[0]["error_summary"] == (
        "Publication Audio failed deterministic checks"
    )
    assert harness.telegram.latest().kind == "processing_failure"
    assert harness.codex.invocation_count == 1
    assert speech.invocation_count == 1

    retried = client.post(
        f"/api/v1/processing-runs/{review_items[0]['id']}:retry"
    )
    assert retried.status_code == 200
    assert retried.json()["state"] == "queued"

    completed = client.post("/api/v1/processing-runs:advance")
    assert completed.status_code == 200
    assert completed.json()["episode_state"] == "publication_queue"
    assert harness.codex.invocation_count == 1
    assert speech.invocation_count == 1
    assert speech.remaster_invocation_count == 1


def test_quality_retry_reuses_artifact_that_passes_the_updated_gate(
    tmp_path,
    monkeypatch,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = RecoverableSpeechAdapter()
    speech.valid = True
    dependencies = replace(harness.dependencies, speech=speech)
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    monkeypatch.setattr(
        speech,
        "render",
        lambda candidate, translation: (
            setattr(speech, "invocation_count", speech.invocation_count + 1)
            or SpeechResult(
                publication_audio=b"M4A!recoverable-publication-audio",
                duration_seconds=3600,
                chapters=((0, "Introduction"),),
                pipeline_version="recoverable-fixture-v1",
                quality=AudioQualityReport(
                    true_peak_dbtp=-0.94,
                    rendered_segments=len(translation.segments),
                ),
                segment_timings=tuple(
                    (float(index), float(index + 1))
                    for index in range(len(translation.segments))
                ),
            )
        ),
    )

    failed = client.post("/api/v1/processing-runs:advance").json()

    assert failed["processing_state"] == "review_queue"
    assert failed["error_kind"] == "quality_gate"
    monkeypatch.setattr(
        TransPodcastService,
        "_validate_speech",
        staticmethod(
            lambda speech, expected_segments, **_validation_options: None
        ),
    )

    retried = client.post(
        f"/api/v1/processing-runs/{failed['processing_run_id']}:retry"
    )
    completed = client.post("/api/v1/processing-runs:advance").json()

    assert retried.json()["state"] == "queued"
    assert completed["processing_state"] == "completed"
    assert speech.invocation_count == 1
    assert speech.remaster_invocation_count == 0
