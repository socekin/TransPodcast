from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_published_version_stays_active_until_revision_is_approved(
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
    first_publish = next(
        item.token
        for item in harness.telegram.latest().actions
        if item.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{first_publish}:apply")
    feed_before = client.get(f"/feed/{harness.feed_token}.xml").text
    assert "/v1/audio.m4a" in feed_before
    active_audio_before = client.get(
        f"/api/v1/episodes/{episode_id}/preview?published=true"
    ).content

    revised = client.patch(
        f"/api/v1/episodes/{episode_id}/segments/1",
        json={"target_text": "A corrected complete translation."},
    )

    assert revised.status_code == 202
    assert revised.json()["revision_state"] == "queued"
    completed_revision = client.post("/api/v1/processing-runs:advance")
    assert completed_revision.json()["episode_state"] == "revision_pending"
    assert client.get(
        f"/api/v1/episodes/{episode_id}/preview?published=true"
    ).content == active_audio_before
    feed_pending = client.get(f"/feed/{harness.feed_token}.xml").text
    assert "/v1/audio.m4a" in feed_pending

    revision_publish = next(
        item.token
        for item in harness.telegram.latest().actions
        if item.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{revision_publish}:apply")
    feed_after = client.get(f"/feed/{harness.feed_token}.xml").text
    assert "/v2/audio.m4a" in feed_after
    assert "/v1/audio.m4a" not in feed_after


def test_declined_metadata_revision_never_changes_active_feed(tmp_path) -> None:
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
    publish = next(
        item.token
        for item in harness.telegram.latest().actions
        if item.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{publish}:apply")
    original_feed = client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text

    client.patch(
        f"/api/v1/episodes/{episode_id}/metadata",
        json={
            "title": "UNAPPROVED TITLE",
            "overview": "Unapproved draft overview.",
            "chapters": [
                {"start_seconds": 0, "title": "Draft opening"},
                {"start_seconds": 1200, "title": "Draft middle"},
                {"start_seconds": 2400, "title": "Draft ending"},
            ],
        },
    )
    pending_feed = client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text
    assert pending_feed == original_feed

    decline = next(
        item.token
        for item in harness.telegram.latest().actions
        if item.label == "不发布"
    )
    client.post(f"/api/v1/telegram-actions/{decline}:apply")
    declined_feed = client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text
    assert declined_feed == original_feed
    assert "UNAPPROVED TITLE" not in declined_feed
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["state"] == "published"
    assert detail["publication_presentation"]["revision_kind"] == "none"
    assert detail["title"] != "UNAPPROVED TITLE"
    assert detail["overview"] != "Unapproved draft overview."
    assert all(
        chapter["title"] != "Draft opening"
        for chapter in detail["chapters"]
    )
    assert detail["publication_presentation"]["state"] == "published_clean"

    resubmitted = client.post(
        f"/api/v1/episodes/{episode_id}:resubmit"
    )
    assert resubmitted.status_code == 409


def test_queued_revision_closes_previous_publication_actions(tmp_path) -> None:
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
    stale_publish = next(
        item.token
        for item in harness.telegram.latest().actions
        if item.label == "发布"
    )

    queued = client.patch(
        f"/api/v1/episodes/{episode_id}/segments/1",
        json={"target_text": "A revised translation."},
    )
    assert queued.status_code == 202

    stale = client.post(
        f"/api/v1/telegram-actions/{stale_publish}:apply"
    )
    assert stale.json() == {
        "state": "revision_processing",
        "already_applied": True,
    }
    assert "<item>" not in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text
