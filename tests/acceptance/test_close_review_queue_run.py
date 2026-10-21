from __future__ import annotations

import io
from dataclasses import replace

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.cli import main
from transpodcast.domain import (
    AudioQualityReport,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.testing import AcceptanceHarness


class ClippingSpeechAdapter:
    """Renders audio that always fails the deterministic Quality Gate."""

    def readiness(self) -> bool:
        return True

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        return SpeechResult(
            publication_audio=b"M4A!clipping-publication-audio",
            duration_seconds=3600,
            chapters=((0, "Introduction"),),
            pipeline_version="clipping-fixture-v1",
            quality=AudioQualityReport(
                loudness_lufs=-13.0,
                true_peak_dbtp=-0.6,
                clipping=True,
                rendered_segments=len(translation.segments),
            ),
            segment_timings=tuple(
                (float(index), float(index + 1))
                for index in range(len(translation.segments))
            ),
        )


def _review_queue_run(tmp_path) -> tuple[TestClient, int]:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(harness.dependencies, speech=ClippingSpeechAdapter())
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    failed = client.post("/api/v1/processing-runs:advance").json()
    assert failed["processing_state"] == "review_queue"
    return client, int(failed["processing_run_id"])


def test_the_owner_closes_a_review_queue_run_and_keeps_its_history(
    tmp_path,
) -> None:
    client, run_id = _review_queue_run(tmp_path)

    closed = client.post(f"/api/v1/processing-runs/{run_id}:close")

    assert closed.status_code == 200
    assert closed.json()["state"] == "closed"
    assert client.get("/api/v1/review-queue").json()["items"] == []
    detail = client.get(f"/api/v1/processing-runs/{run_id}").json()
    assert detail["status"] == "closed"
    assert detail["attempts"]
    assert detail["current_error"]["kind"] == "quality_gate"
    assert detail["current_error"]["retryable"] is False
    (activity,) = [
        item for item in detail["activity"] if item["kind"] == "closed"
    ]
    assert activity["metadata"]["error_kind"] == "quality_gate"
    (item,) = [
        item
        for item in client.get("/api/v1/episode-library").json()["items"]
        if item["processing_run_id"] == run_id
    ]
    assert item["library_state"] == "closed"
    assert item["available_actions"] == ["view"]


def test_a_closed_run_is_never_retried_or_processed_again(tmp_path) -> None:
    client, run_id = _review_queue_run(tmp_path)
    client.post(f"/api/v1/processing-runs/{run_id}:close")

    assert client.post(f"/api/v1/processing-runs/{run_id}:retry").status_code == 409
    assert client.post(f"/api/v1/processing-runs/{run_id}:close").status_code == 409
    client.post("/api/v1/processing-runs:advance")
    detail = client.get(f"/api/v1/processing-runs/{run_id}").json()
    assert detail["status"] == "closed"


def test_only_a_review_queue_run_can_be_closed(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    (queued, *_rest) = client.get("/api/v1/processing-queue").json()["items"]

    closing = client.post(
        f"/api/v1/processing-runs/{queued['processing_run_id']}:close"
    )

    assert closing.status_code == 409
    assert client.post("/api/v1/processing-runs/999:close").status_code == 404


def test_the_agent_cli_reports_a_closed_run(tmp_path) -> None:
    client, run_id = _review_queue_run(tmp_path)
    client.post(f"/api/v1/processing-runs/{run_id}:close")
    output = io.StringIO()

    code = main(["status", str(run_id)], client=client, stdout=output)

    assert code == 0
    assert output.getvalue().strip().endswith(
        "was closed by the Owner without an episode."
    )
    active = io.StringIO()
    main(["status"], client=client, stdout=active)
    assert f"Run {run_id}:" not in active.getvalue()
