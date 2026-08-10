from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_metadata_revision_does_not_rerender_speech(tmp_path) -> None:
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
    premature_backfill = client.post(
        f"/api/v1/episodes/{episode_id}:prepare-metadata-only-revision"
    )
    assert premature_backfill.status_code == 409
    render_count = harness.modal.invocation_count

    revised = client.patch(
        f"/api/v1/episodes/{episode_id}/metadata",
        json={
            "title": "Useful Show · Corrected Mandarin title",
            "overview": "Corrected complete episode overview.",
            "chapters": [
                {"start_seconds": 0, "title": "Opening"},
                {"start_seconds": 1200, "title": "Main discussion"},
                {"start_seconds": 2400, "title": "Closing discussion"},
            ],
        },
    )

    assert revised.status_code == 200
    assert revised.json()["revision_version"] == 2
    assert revised.json()["state"] == "publication_queue"
    assert harness.modal.invocation_count == render_count
    history = client.get(
        f"/api/v1/episodes/{episode_id}/metadata-revisions"
    ).json()["items"]
    assert [item["version"] for item in history] == [2, 1]


def test_published_metadata_revision_keeps_the_existing_audio_enclosure(
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
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{first_publish}:apply")

    with harness.dependencies.database.session() as session:
        from transpodcast.models import EpisodeRecord

        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        original_guid = episode.publication_guid
        original_key = str(episode.publication_key)
        original_audio = harness.storage.read_publication(original_key)

    revised = client.patch(
        f"/api/v1/episodes/{episode_id}/metadata",
        json={
            "title": "Useful Show · Revised Publication Metadata",
            "overview": "The compact overview remains concise.",
            "publication_description": (
                f"{chr(0x4E00) * 180}\n\n{chr(0x4E01) * 180}"
            ),
            "chapters": [
                {"start_seconds": 0, "title": "Opening topic"},
                {"start_seconds": 1200, "title": "Second topic"},
                {"start_seconds": 2400, "title": "Final topic"},
            ],
        },
    )
    assert revised.status_code == 200
    assert revised.json()["changed"] is True
    pending_detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert pending_detail["publication_presentation"] == {
        "state": "revision_pending",
        "revision_kind": "metadata",
        "changed_fields": [
            "title",
            "overview",
            "publication_description",
            "chapters",
        ],
        "backfill_available": True,
        "degraded_fields": ["artwork", "publication_metadata"],
    }
    notifications_before_overlap = len(harness.telegram.messages)
    overlapping = client.patch(
        f"/api/v1/episodes/{episode_id}/metadata",
        json={
            "title": "Useful Show · Another Pending Edit",
            "overview": "The compact overview remains concise.",
            "publication_description": (
                f"{chr(0x4E02) * 180}\n\n{chr(0x4E03) * 180}"
            ),
            "chapters": [
                {"start_seconds": 0, "title": "Opening topic"},
                {"start_seconds": 1200, "title": "Second topic"},
                {"start_seconds": 2400, "title": "Final topic"},
            ],
        },
    )
    assert overlapping.status_code == 409
    assert len(harness.telegram.messages) == notifications_before_overlap
    overlapping_backfill = client.post(
        f"/api/v1/episodes/{episode_id}:prepare-metadata-only-revision"
    )
    assert overlapping_backfill.status_code == 409
    assert len(harness.telegram.messages) == notifications_before_overlap
    revision_publish = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{revision_publish}:apply")

    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert episode.publication_guid == original_guid
        assert str(episode.publication_key) == original_key
        assert harness.storage.read_publication(original_key) == original_audio
    feed = client.get(f"/feed/{harness.feed_token}.xml").text
    assert chr(0x4E00) * 20 in feed

    notifications_before_whitespace_retry = len(harness.telegram.messages)
    whitespace_retry = client.patch(
        f"/api/v1/episodes/{episode_id}/metadata",
        json={
            "title": "  Useful Show · Revised Publication Metadata  ",
            "overview": "  The compact overview remains concise.  ",
            "publication_description": (
                f"  {chr(0x4E00) * 180}\n \n{chr(0x4E01) * 180}  "
            ),
            "chapters": [
                {"start_seconds": 0, "title": " Opening topic "},
                {"start_seconds": 1200, "title": " Second topic "},
                {"start_seconds": 2400, "title": " Final topic "},
            ],
        },
    )
    assert whitespace_retry.status_code == 200
    assert whitespace_retry.json()["changed"] is False
    assert len(harness.telegram.messages) == notifications_before_whitespace_retry


def test_unchanged_published_metadata_does_not_create_a_revision(
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
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{first_publish}:apply")
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["publication_presentation"] == {
        "state": "published_clean",
        "revision_kind": "none",
        "changed_fields": [],
        "backfill_available": True,
        "degraded_fields": ["artwork", "publication_metadata"],
    }
    revisions_before = client.get(
        f"/api/v1/episodes/{episode_id}/metadata-revisions"
    ).json()["items"]
    notifications_before = len(harness.telegram.messages)

    unchanged = client.patch(
        f"/api/v1/episodes/{episode_id}/metadata",
        json={
            "title": detail["title"],
            "overview": detail["overview"],
            "publication_description": detail["publication_description"],
            "chapters": detail["chapters"],
        },
    )

    assert unchanged.status_code == 200
    assert unchanged.json()["changed"] is False
    assert unchanged.json()["state"] == "published"
    assert client.get(
        f"/api/v1/episodes/{episode_id}/metadata-revisions"
    ).json()["items"] == revisions_before
    assert len(harness.telegram.messages) == notifications_before
