from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate
from transpodcast.testing import AcceptanceHarness


def test_owner_revision_runs_before_queued_candidate_processing(
    tmp_path,
) -> None:
    candidates = [
        SourceCandidate(
            youtube_video_id=f"priority-{index}",
            title=f"Priority episode {index}",
            description="Product strategy.",
            duration_seconds=1800,
            language="en",
            published_at=datetime(2026, 7, 28, 11, index, tzinfo=UTC),
            source_url=f"https://youtube.test/watch?v=priority-{index}",
            captions="A durable product preserves intent.",
        )
        for index in (1, 2)
    ]
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=candidates,
        triage_results=None,
    )
    client = TestClient(create_app(harness.dependencies))
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
        json={"target_text": "A high-priority owner revision."},
    )

    next_work = client.post("/api/v1/processing-runs:advance").json()

    assert next_work["revision_state"] == "completed"
    assert next_work["episode_id"] == episode_id
    assert len(client.get("/api/v1/episodes").json()["items"]) == 1
