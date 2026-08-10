from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def _episode_ready_for_publication(
    client: TestClient,
) -> tuple[int, str, str]:
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    outcome = client.post("/api/v1/processing-runs:advance").json()
    return outcome["episode_id"], "publication_queue", "ready"


def test_owner_can_decline_resubmit_delete_and_recover_publication(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id, _, _ = _episode_ready_for_publication(client)

    decline_token = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "不发布"
    )
    declined = client.post(
        f"/api/v1/telegram-actions/{decline_token}:apply"
    )
    assert declined.json()["state"] == "unpublished"
    assert "<item>" not in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text

    resubmitted = client.post(
        f"/api/v1/episodes/{episode_id}:resubmit"
    )
    assert resubmitted.status_code == 200
    assert resubmitted.json()["state"] == "publication_queue"

    publish_token = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{publish_token}:apply")
    assert "<item>" in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text
    media_key = harness.storage.publication_media_paths()[0]
    complete_media = harness.storage.read_publication(media_key)
    assert complete_media is not None
    suffix = client.get(
        f"/media/{harness.feed_token}/{media_key}",
        headers={"Range": "bytes=-5"},
    )
    assert suffix.status_code == 206
    assert suffix.content == complete_media[-5:]
    assert suffix.headers["Content-Range"] == (
        f"bytes {len(complete_media) - 5}-{len(complete_media) - 1}/"
        f"{len(complete_media)}"
    )

    deleted = client.delete(f"/api/v1/episodes/{episode_id}")
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "deleted"
    assert deleted.json()["recoverable_for_days"] == 30
    assert "<item>" not in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text
    assert len(harness.storage.publication_media_paths()) == 1
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["recoverable"] is True
    assert detail["recovery_until"] is not None

    recovered = client.post(f"/api/v1/episodes/{episode_id}:recover")
    assert recovered.status_code == 200
    assert recovered.json()["state"] == "published"
    assert "<item>" in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text
