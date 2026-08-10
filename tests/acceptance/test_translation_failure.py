from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate, TranslationResult
from transpodcast.testing import AcceptanceHarness


class InvalidTranslationAdapter:
    def readiness(self) -> bool:
        return True

    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        raise ValueError("Translation Artifact is missing segment seg-0001")


def test_malformed_translation_is_durable_and_never_reaches_speech(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        translation=InvalidTranslationAdapter(),
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")

    outcome = client.post("/api/v1/processing-runs:advance")

    assert outcome.status_code == 200
    assert outcome.json()["processing_state"] == "review_queue"
    assert outcome.json()["error_kind"] == "translation_schema"
    assert harness.modal.invocation_count == 0
    review = client.get("/api/v1/review-queue").json()["items"]
    assert review[0]["error_summary"] == (
        "Translation Artifact is missing segment seg-0001"
    )
