from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_owner_can_publish_a_discovered_candidate_without_live_providers(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    readiness = client.get("/api/v1/setup-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    assert readiness.json()["services"]["source_channel"] is False

    created_channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
            "backfill_video_ids": [],
        },
    )
    assert created_channel.status_code == 201
    assert client.get("/api/v1/setup-readiness").json()["ready"] is True

    discovery = client.post("/api/v1/discovery-runs")
    assert discovery.status_code == 202
    assert discovery.json() == {
        "discovered": 1,
        "automatically_accepted": 1,
        "awaiting_decision": 0,
        "ignored": 0,
    }

    processing = client.post("/api/v1/processing-runs:advance")
    assert processing.status_code == 200
    assert processing.json()["episode_state"] == "publication_queue"

    episodes = client.get("/api/v1/episodes").json()["items"]
    assert len(episodes) == 1
    assert episodes[0]["title"] == "Useful Show · Building products that last"
    assert episodes[0]["state"] == "publication_queue"

    feed_before_publication = client.get(
        f"/feed/{harness.feed_token}.xml"
    )
    assert feed_before_publication.status_code == 200
    assert "<item>" not in feed_before_publication.text

    notification = harness.telegram.latest()
    assert notification.kind == "publication_decision"
    assert {action.label for action in notification.actions} == {
        "发布",
        "不发布",
    }

    publish_token = next(
        action.token
        for action in notification.actions
        if action.label == "发布"
    )
    published = client.post(
        f"/api/v1/telegram-actions/{publish_token}:apply"
    )
    assert published.status_code == 200
    assert published.json()["state"] == "published"

    repeated = client.post(
        f"/api/v1/telegram-actions/{publish_token}:apply"
    )
    assert repeated.status_code == 200
    assert repeated.json()["state"] == "published"
    assert repeated.json()["already_applied"] is True

    feed = client.get(f"/feed/{harness.feed_token}.xml")
    assert feed.status_code == 200
    assert "Building products that last" in feed.text
    assert feed.text.count("<item>") == 1

    media_path = harness.storage.publication_media_paths()[0]
    media = client.get(
        f"/media/{harness.feed_token}/{media_path}",
        headers={"Range": "bytes=0-3"},
    )
    assert media.status_code == 206
    assert media.content == b"M4A!"
    assert media.headers["content-range"].startswith("bytes 0-3/")

    assert harness.codex.invocation_count == 1
    assert harness.modal.invocation_count == 1
    assert harness.live_provider_invocation_count == 0
