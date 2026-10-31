from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import (
    FICTIONAL_PODCAST_EPISODE_URL,
    AcceptanceHarness,
    FakeSourceAdapter,
)


def _client(tmp_path) -> tuple[TestClient, AcceptanceHarness]:
    harness = AcceptanceHarness.ready(tmp_path)
    return TestClient(create_app(harness.dependencies)), harness


def _add(client: TestClient, url: str) -> dict:
    resolved = client.post("/api/v1/source-resolutions", json={"url": url})
    assert resolved.status_code == 200
    imported = client.post(
        f"/api/v1/source-resolutions/{resolved.json()['resolution_id']}"
        ":import-video"
    )
    assert imported.status_code in {200, 201}
    return {"resolution": resolved.json(), "import": imported.json()}


def test_a_podcast_link_resolves_to_its_episode_and_platform(tmp_path) -> None:
    client, _harness = _client(tmp_path)

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": FICTIONAL_PODCAST_EPISODE_URL},
    ).json()

    video = resolved["requested_video"]
    assert video["youtube_video_id"] == "applepodcasts:1000000000001"
    assert video["platform"] == "applepodcasts"
    assert video["thumbnail_url"] == "https://podcasts.example.com/episode-1.jpg"
    assert resolved["channel"]["title"] == "Fictional Podcast"


def test_a_podcast_episode_is_processed_without_following_its_show(
    tmp_path,
) -> None:
    client, harness = _client(tmp_path)
    client.patch("/api/v1/settings", json={"manual_processing": False})

    added = _add(client, FICTIONAL_PODCAST_EPISODE_URL)["import"]

    assert added["youtube_video_id"] == "applepodcasts:1000000000001"
    assert added["source_channel"]["mode"] == "paused"
    completed = client.post("/api/v1/processing-runs:advance").json()
    assert completed["processing_run_id"] == added["processing_run_id"]
    detail = client.get(
        f"/api/v1/processing-runs/{added['processing_run_id']}"
    ).json()
    (archive,) = [
        step for step in detail["pipeline_steps"] if step["id"] == "source_archive"
    ]
    assert archive["state"] == "completed"
    assert archive["technical_evidence"]["provider"] == "applepodcasts"
    assert archive["technical_evidence"]["version"] == "yt-dlp-source-v2"
    source = harness.dependencies.source
    assert isinstance(source, FakeSourceAdapter)
    assert source.archive_invocation_count == 1


def test_adding_the_same_podcast_link_again_reuses_the_episode(tmp_path) -> None:
    client, _harness = _client(tmp_path)

    first = _add(client, FICTIONAL_PODCAST_EPISODE_URL)["import"]
    second = _add(client, FICTIONAL_PODCAST_EPISODE_URL)["import"]

    assert second["reused"] is True
    assert second["processing_run_id"] == first["processing_run_id"]


def test_discovery_never_treats_a_podcast_show_as_a_youtube_channel(
    tmp_path,
) -> None:
    client, harness = _client(tmp_path)
    _add(client, FICTIONAL_PODCAST_EPISODE_URL)
    source = harness.dependencies.source
    assert isinstance(source, FakeSourceAdapter)

    client.post("/api/v1/discovery-runs")

    assert "discover" not in source.operation_log


def test_only_youtube_channels_can_be_followed(tmp_path) -> None:
    client, _harness = _client(tmp_path)

    followed = client.post(
        "/api/v1/source-channels",
        json={"url": FICTIONAL_PODCAST_EPISODE_URL, "mode": "agent_filtering"},
    )

    assert followed.status_code == 422
