from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate
from transpodcast.testing import AcceptanceHarness


def test_channel_is_resolved_before_save_and_backfill_is_explicit(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    resolved = client.post(
        "/api/v1/source-channels:resolve",
        json={"url": "https://www.youtube.com/watch?v=useful-001"},
    )

    assert resolved.status_code == 200
    assert resolved.json()["channel"]["youtube_channel_id"] == (
        "UC_USEFUL_SHOW"
    )
    assert len(resolved.json()["recent_videos"]) == 1
    assert client.get("/api/v1/source-channels").json()["items"] == []

    saved = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/watch?v=useful-001",
            "mode": "agent_filtering",
            "backfill_video_ids": ["useful-001"],
        },
    )

    assert saved.status_code == 201
    assert client.post("/api/v1/discovery-runs").json()["discovered"] == 0
    assert client.post(
        "/api/v1/processing-runs:advance"
    ).json()["episode_state"] == "publication_queue"


def test_supported_source_video_is_separate_from_channel_backfill(
    tmp_path,
) -> None:
    candidates = [
        SourceCandidate(
            youtube_video_id=f"video-{index:03d}",
            title=f"Episode {index}",
            description="A source episode.",
            duration_seconds=3600,
            language="en",
            published_at=datetime(2026, 7, index, tzinfo=UTC),
            source_url=(
                "https://www.youtube.com/watch?"
                f"v=video-{index:03d}"
            ),
            captions="Source captions.",
            youtube_channel_id="UC_USEFUL_SHOW",
        )
        for index in range(1, 12)
    ]
    supported_source_video = candidates[-1]
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=candidates,
        triage_results=None,
    )
    client = TestClient(create_app(harness.dependencies))

    response = client.post(
        "/api/v1/source-channels:resolve",
        json={"url": supported_source_video.source_url},
    )

    assert response.status_code == 200
    assert response.json()["supported_source_video"]["youtube_video_id"] == (
        supported_source_video.youtube_video_id
    )
    assert supported_source_video.youtube_video_id not in {
        video["youtube_video_id"]
        for video in response.json()["recent_videos"]
    }

    created = client.post(
        "/api/v1/source-channels",
        json={
            "url": supported_source_video.source_url,
            "mode": "agent_filtering",
            "backfill_video_ids": [],
        },
    )

    assert created.status_code == 201
    library = client.get("/api/v1/episode-library")
    assert library.status_code == 200
    assert [
        (
            item["source_url"],
            item["state"],
            item["episode_id"],
        )
        for item in library.json()["items"]
    ] == [
        (
            supported_source_video.source_url,
            "processing_queued",
            None,
        )
    ]

    processed = client.post("/api/v1/processing-runs:advance")
    assert processed.status_code == 200
    episodes = client.get("/api/v1/episodes").json()["items"]
    assert [episode["source_url"] for episode in episodes] == [
        supported_source_video.source_url
    ]
    completed_library = client.get("/api/v1/episode-library").json()["items"]
    assert len(completed_library) == 1
    assert completed_library[0]["episode_id"] == episodes[0]["id"]
    assert completed_library[0]["state"] == "publication_queue"
