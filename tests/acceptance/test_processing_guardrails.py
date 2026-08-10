from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import (
    AudioQualityReport,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.errors import SpendGuardrailReached, TransientProviderError
from transpodcast.testing import AcceptanceHarness, FakeSpeechAdapter


class ControlledSpeechAdapter:
    def __init__(self, failure: Exception) -> None:
        self.failure: Exception | None = failure
        self.invocation_count = 0

    def readiness(self) -> bool:
        return True

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        self.invocation_count += 1
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            raise failure
        return SpeechResult(
            publication_audio=b"M4A!guardrail-recovery",
            duration_seconds=3600,
            chapters=((0, "Opening"),),
            pipeline_version="fixture-pipeline-v1",
            quality=AudioQualityReport(
                rendered_segments=len(translation.segments)
            ),
            segment_timings=tuple(
                (float(index), float(index + 1))
                for index in range(len(translation.segments))
            ),
        )

    def render_segment(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
        segment_ordinal: int,
        prior_preview_key: str,
    ) -> SpeechResult:
        return self.render(candidate, translation)


class SegmentGuardrailSpeechAdapter(FakeSpeechAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.fail_revision = True

    def render_segment(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
        segment_ordinal: int,
        prior_preview_key: str,
    ) -> SpeechResult:
        if self.fail_revision:
            self.fail_revision = False
            raise SpendGuardrailReached(
                "TransPodcast Modal Environment limit reached"
            )
        return super().render_segment(
            candidate,
            translation,
            segment_ordinal,
            prior_preview_key,
        )


def _queued_client(tmp_path, speech: ControlledSpeechAdapter):
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(
        create_app(replace(harness.dependencies, speech=speech))
    )
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    return harness, client


def test_transient_failure_retries_after_five_minutes_from_checkpoint(
    tmp_path,
) -> None:
    speech = ControlledSpeechAdapter(
        TransientProviderError("Modal capacity is temporarily unavailable")
    )
    harness, client = _queued_client(tmp_path, speech)

    delayed = client.post("/api/v1/processing-runs:advance")

    assert delayed.json()["processing_state"] == "retry_wait"
    assert delayed.json()["retry_in_seconds"] == 300
    assert client.post("/api/v1/processing-runs:advance").status_code == 404
    harness.dependencies.clock.value += timedelta(minutes=5)
    completed = client.post("/api/v1/processing-runs:advance")
    assert completed.json()["episode_state"] == "publication_queue"
    assert harness.codex.invocation_count == 1


def test_spend_guardrail_pauses_processing_until_owner_resumes(
    tmp_path,
) -> None:
    speech = ControlledSpeechAdapter(
        SpendGuardrailReached("TransPodcast Modal Environment limit reached")
    )
    harness, client = _queued_client(tmp_path, speech)

    paused = client.post("/api/v1/processing-runs:advance")

    assert paused.json()["processing_state"] == "spend_guardrail"
    assert client.get("/api/v1/settings").json()["budget"] == {
        "spend_guardrail_paused": True,
        "authority": "modal_environment",
    }
    assert harness.telegram.latest().kind == "spend_guardrail"
    assert harness.telegram.latest().actions == ()

    resumed = client.post("/api/v1/spend-guardrail:resume")
    assert resumed.json()["spend_guardrail_paused"] is False
    completed = client.post("/api/v1/processing-runs:advance")
    assert completed.json()["episode_state"] == "publication_queue"
    assert harness.codex.invocation_count == 1


def test_spend_guardrail_resume_requeues_segment_revision(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = SegmentGuardrailSpeechAdapter()
    client = TestClient(
        create_app(replace(harness.dependencies, speech=speech))
    )
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    episode_id = client.post(
        "/api/v1/processing-runs:advance"
    ).json()["episode_id"]
    client.patch(
        f"/api/v1/episodes/{episode_id}/segments/1",
        json={"target_text": "A revised translation."},
    )

    paused = client.post("/api/v1/processing-runs:advance")
    assert paused.json()["revision_state"] == "spend_guardrail"

    client.post("/api/v1/spend-guardrail:resume")
    completed = client.post("/api/v1/processing-runs:advance")
    assert completed.json()["revision_state"] == "completed"
