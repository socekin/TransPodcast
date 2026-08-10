from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness, FixedClock


def test_private_preview_expires_but_owner_console_keeps_the_episode(
    tmp_path,
) -> None:
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
    episode_id = client.post(
        "/api/v1/processing-runs:advance"
    ).json()["episode_id"]

    before = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert before["preview"]["available"] is True

    clock = harness.dependencies.clock
    assert isinstance(clock, FixedClock)
    clock.value += timedelta(days=8)

    after = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert after["preview"]["available"] is False
    assert after["state"] == "publication_queue"
    assert after["segments"]
    durable_preview = client.get(
        f"/api/v1/episodes/{episode_id}/preview"
    )
    assert durable_preview.status_code == 200
    assert durable_preview.headers["X-TransPodcast-Preview-Expired"] == "true"
