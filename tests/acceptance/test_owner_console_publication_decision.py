from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from transpodcast.api import create_app
from transpodcast.domain import OwnerNotification
from transpodcast.errors import NotificationDeliveryRejectedError
from transpodcast.models import (
    EpisodeRecord,
    PublicationDecisionRecord,
    PublicationNotificationRecord,
)
from transpodcast.testing import AcceptanceHarness


class UnconfiguredNotifications:
    def readiness(self) -> bool:
        return False

    def send(self, notification: OwnerNotification) -> str | None:
        raise AssertionError(
            f"Unconfigured notifications must not send {notification.kind}"
        )

    def complete_message(self, message_id: str, outcome: str) -> None:
        raise AssertionError(f"Unexpected completion: {message_id} {outcome}")


class RejectingNotifications:
    def readiness(self) -> bool:
        return True

    def send(self, notification: OwnerNotification) -> str | None:
        raise NotificationDeliveryRejectedError(
            f"Rejected {notification.kind}"
        )

    def complete_message(self, message_id: str, outcome: str) -> None:
        raise AssertionError(f"Unexpected completion: {message_id} {outcome}")


def _ready_episode(client: TestClient) -> int:
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
    assert outcome.json()["episode_state"] == "publication_queue"
    return int(outcome.json()["episode_id"])


def test_owner_console_publishes_without_telegram_configuration(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        notifications=UnconfiguredNotifications(),
    )
    client = TestClient(create_app(dependencies))

    episode_id = _ready_episode(client)
    readiness = client.get("/api/v1/setup-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert readiness.json()["services"]["telegram"] is False
    assert readiness.json()["details"]["telegram"]["optional"] is True

    decision = client.post(
        f"/api/v1/episodes/{episode_id}:decide-publication",
        json={"decision": "publish"},
    )

    assert decision.status_code == 200
    assert decision.json() == {
        "episode_id": episode_id,
        "state": "published",
        "decision": "publish",
        "channel": "owner_console",
        "already_applied": False,
    }
    feed = client.get(f"/feed/{harness.feed_token}.xml")
    assert feed.status_code == 200
    assert "<item>" in feed.text


def test_owner_console_publication_decision_is_idempotent(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)
    endpoint = f"/api/v1/episodes/{episode_id}:decide-publication"

    first = client.post(endpoint, json={"decision": "publish"})
    repeated = client.post(endpoint, json={"decision": "publish"})

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == {
        "episode_id": episode_id,
        "state": "published",
        "decision": "publish",
        "channel": "owner_console",
        "already_applied": True,
    }
    assert len(harness.storage.publication_media_paths()) == 1
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["publication_decision"]["decision"] == "publish"
    assert detail["publication_decision"]["channel"] == "owner_console"


def test_competing_web_publication_decisions_apply_exactly_once(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    app = create_app(harness.dependencies)
    with TestClient(app) as client:
        episode_id = _ready_episode(client)
        endpoint = f"/api/v1/episodes/{episode_id}:decide-publication"

        def decide(decision: str) -> tuple[int, dict[str, object]]:
            response = client.post(
                endpoint,
                json={
                    "decision": decision,
                    "expected_state": "publication_queue",
                },
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(decide, ("publish", "do_not_publish"))
            )

        assert sorted(status for status, _ in outcomes) == [200, 409]
        with harness.dependencies.database.session() as session:
            decision_count = session.scalar(
                select(func.count(PublicationDecisionRecord.id)).where(
                    PublicationDecisionRecord.episode_id == episode_id
                )
            )
        assert decision_count == 1


def test_telegram_and_owner_console_share_one_publication_decision(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)
    telegram_publish = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    notification = harness.telegram.latest()
    assert "本期成品已通过全部质量检查" in notification.body
    assert {action.label for action in notification.actions} == {
        "发布",
        "不发布",
    }

    web = client.post(
        f"/api/v1/episodes/{episode_id}:decide-publication",
        json={"decision": "publish"},
    )
    stale_telegram = client.post(
        f"/api/v1/telegram-actions/{telegram_publish}:apply"
    )

    assert web.status_code == 200
    assert stale_telegram.status_code == 200
    assert stale_telegram.json() == {
        "state": "published",
        "already_applied": True,
    }
    assert len(harness.storage.publication_media_paths()) == 1
    assert harness.telegram.completed_messages == [("1", "publish")]


def test_owner_console_rejects_ineligible_quality_without_mutating_feed(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)
    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        episode.quality_json = '{"eligible": false}'

    response = client.post(
        f"/api/v1/episodes/{episode_id}:decide-publication",
        json={
            "decision": "publish",
            "expected_state": "publication_queue",
        },
    )

    assert response.status_code == 409
    assert "Quality Gate" in response.json()["detail"]
    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert episode.state == "publication_queue"
    assert "<item>" not in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text


def test_owner_console_rejects_missing_completed_episode_artwork(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)
    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        episode.artwork_json = '{"status": "completed"}'
        episode.artwork_path = str(tmp_path / "missing-artwork.jpg")

    response = client.post(
        f"/api/v1/episodes/{episode_id}:decide-publication",
        json={
            "decision": "publish",
            "expected_state": "publication_queue",
        },
    )

    assert response.status_code == 409
    assert "Episode Artwork" in response.json()["detail"]
    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert episode.state == "publication_queue"
    assert "<item>" not in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text


def test_owner_console_declines_new_episode_without_publishing(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)

    decision = client.post(
        f"/api/v1/episodes/{episode_id}:decide-publication",
        json={"decision": "do_not_publish"},
    )

    assert decision.status_code == 200
    assert decision.json()["state"] == "unpublished"
    assert "<item>" not in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text


def test_owner_console_can_publish_an_unpublished_episode_directly(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)
    endpoint = f"/api/v1/episodes/{episode_id}:decide-publication"
    client.post(endpoint, json={"decision": "do_not_publish"})

    published = client.post(endpoint, json={"decision": "publish"})

    assert published.status_code == 200
    assert published.json()["state"] == "published"
    assert "<item>" in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text


def test_unpublished_episode_with_missing_audio_stays_unpublished(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)
    endpoint = f"/api/v1/episodes/{episode_id}:decide-publication"
    client.post(endpoint, json={"decision": "do_not_publish"})
    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        preview_path = harness.storage.preview_root / episode.preview_key
    preview_path.unlink()

    published = client.post(
        endpoint,
        json={
            "decision": "publish",
            "expected_state": "unpublished",
        },
    )

    assert published.status_code == 409
    assert "reprocess" in published.json()["detail"]
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["state"] == "unpublished"
    assert "<item>" not in client.get(
        f"/feed/{harness.feed_token}.xml"
    ).text


def test_owner_console_publishes_a_metadata_revision_in_place(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)
    endpoint = f"/api/v1/episodes/{episode_id}:decide-publication"
    client.post(
        endpoint,
        json={
            "decision": "publish",
            "expected_state": "publication_queue",
        },
    )
    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        original_guid = episode.publication_guid
        original_key = episode.publication_key

    revised = client.patch(
        f"/api/v1/episodes/{episode_id}/metadata",
        json={
            "title": "Revised Owner Console title",
            "overview": "A revised compact overview.",
            "publication_description": f"{'一' * 180}\n\n{'丁' * 180}",
            "chapters": [
                {"start_seconds": 0, "title": "Opening"},
                {"start_seconds": 1200, "title": "Middle"},
                {"start_seconds": 2400, "title": "Closing"},
            ],
        },
    )
    assert revised.status_code == 200

    published = client.post(
        endpoint,
        json={
            "decision": "publish",
            "expected_state": "revision_pending",
        },
    )

    assert published.status_code == 200
    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert episode.publication_guid == original_guid
        assert episode.publication_key == original_key
        assert episode.publication_version == 2
    feed = client.get(f"/feed/{harness.feed_token}.xml").text
    assert "Revised Owner Console title" in feed


def test_owner_console_declines_a_revision_without_changing_the_feed(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)
    endpoint = f"/api/v1/episodes/{episode_id}:decide-publication"
    client.post(
        endpoint,
        json={
            "decision": "publish",
            "expected_state": "publication_queue",
        },
    )
    original_feed = client.get(f"/feed/{harness.feed_token}.xml").text
    revised = client.patch(
        f"/api/v1/episodes/{episode_id}/metadata",
        json={
            "title": "Declined Owner Console title",
            "overview": "This revision must remain private.",
            "publication_description": f"{'一' * 180}\n\n{'丁' * 180}",
            "chapters": [
                {"start_seconds": 0, "title": "Opening"},
                {"start_seconds": 1200, "title": "Middle"},
                {"start_seconds": 2400, "title": "Closing"},
            ],
        },
    )
    assert revised.status_code == 200

    declined = client.post(
        endpoint,
        json={
            "decision": "do_not_publish",
            "expected_state": "revision_pending",
        },
    )

    assert declined.status_code == 200
    assert declined.json()["state"] == "published"
    assert client.get(f"/feed/{harness.feed_token}.xml").text == original_feed


def test_rejected_publication_notification_does_not_fail_processing(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        notifications=RejectingNotifications(),
    )
    client = TestClient(create_app(dependencies))

    episode_id = _ready_episode(client)

    detail = client.get(f"/api/v1/episodes/{episode_id}")
    assert detail.status_code == 200
    assert detail.json()["state"] == "publication_queue"
    with dependencies.database.session() as session:
        delivery = session.scalars(
            select(PublicationNotificationRecord)
        ).one()
        assert delivery.status == "pending"
        assert delivery.attempt_count == 1
        assert delivery.next_retry_at is not None


def test_episode_library_exposes_simplified_publication_actions(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)

    ready = client.get("/api/v1/episode-library").json()["items"][0]
    assert ready["library_state"] == "ready_to_publish"
    assert ready["available_actions"] == ["play", "publish", "decline"]

    client.post(
        f"/api/v1/episodes/{episode_id}:decide-publication",
        json={"decision": "publish"},
    )
    published = client.get("/api/v1/episode-library").json()["items"][0]
    assert published["library_state"] == "published"
    assert published["available_actions"] == ["play"]

    client.delete(f"/api/v1/episodes/{episode_id}")
    taken_down = client.get("/api/v1/episode-library").json()["items"][0]
    assert taken_down["library_state"] == "taken_down"
    assert taken_down["available_actions"] == ["restore"]


def test_episode_library_maps_internal_episode_states_to_owner_actions(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id = _ready_episode(client)

    for internal_state, library_state, actions in (
        ("revision_processing", "processing", ["view"]),
        ("review_queue", "action_required", ["view"]),
        ("unpublished", "unpublished", ["publish"]),
    ):
        with harness.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            assert episode is not None
            episode.state = internal_state
        item = client.get("/api/v1/episode-library").json()["items"][0]
        assert item["library_state"] == library_state
        assert item["available_actions"] == actions

    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        episode.state = "deleted"
        episode.recovery_until = (
            harness.dependencies.clock.now() - timedelta(seconds=1)
        )
    expired = client.get("/api/v1/episode-library").json()["items"][0]
    assert expired["library_state"] == "taken_down"
    assert expired["available_actions"] == []
