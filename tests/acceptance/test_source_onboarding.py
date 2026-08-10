from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate
from transpodcast.models import ProcessingRunRecord, ProcessingStageRecord
from transpodcast.service import TransPodcastService, _default_settings
from transpodcast.testing import AcceptanceHarness, FakeSourceAdapter


def _catalog() -> list[SourceCandidate]:
    return [
        SourceCandidate(
            youtube_video_id=f"useful-{index:03d}",
            title=f"Useful conversation {index}",
            description=f"Complete notes for conversation {index}.",
            duration_seconds=3600 + index,
            language="en",
            published_at=datetime(2026, 7, 27 - index, 8, 0, tzinfo=UTC),
            source_url=(
                f"https://www.youtube.com/watch?v=useful-{index:03d}"
            ),
            captions=f"Captions for conversation {index}.",
        )
        for index in range(12)
    ]


def _harness(tmp_path) -> AcceptanceHarness:
    return AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=_catalog(),
        triage_results=None,
    )


def _source(harness: AcceptanceHarness) -> FakeSourceAdapter:
    source = harness.dependencies.source
    assert isinstance(source, FakeSourceAdapter)
    return source


def test_video_url_resolves_fast_without_backfill_enrichment(
    tmp_path,
) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    source = _source(harness)

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    )
    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["channel"]["youtube_channel_id"] == "UC_USEFUL_SHOW"
    assert payload["channel"]["avatar_url"]
    assert payload["channel"]["subscriber_count"] == 892_000
    requested = payload["requested_video"]
    assert requested["youtube_video_id"] == "useful-000"
    assert requested["description"]
    assert requested["published_at"]
    assert "resolve" in payload["timings_ms"]
    # The visible resolution needs exactly one YouTube request; the
    # ten-video enrichment work from the aggregate path must be gone.
    assert source.operation_log == ["resolve_source"]

    backfill = client.post(
        "/api/v1/source-resolutions/"
        f"{payload['resolution_id']}:backfill"
    )
    assert backfill.status_code == 200
    items = backfill.json()["items"]
    assert [item["youtube_video_id"] for item in items] == [
        f"useful-{index:03d}" for index in range(10)
    ]
    assert items[0]["is_requested"] is True
    assert all(item["published_at"] for item in items)
    assert source.operation_log == [
        "resolve_source",
        "recent_video_previews",
        "enrich_video_previews",
    ]
    # Simulated latency stays far below the measured 35-40 s aggregate.
    assert source.simulated_elapsed_ms() <= 7_000


def test_save_by_resolution_reuses_the_stored_record(tmp_path) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    source = _source(harness)
    assert _default_settings()["manual_processing"] is True
    client.patch("/api/v1/settings", json={"manual_processing": True})

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    resolution_id = resolved["resolution_id"]
    client.post(f"/api/v1/source-resolutions/{resolution_id}:backfill")
    operations_before_save = list(source.operation_log)

    unknown = client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": ["not-in-window"],
        },
    )
    assert unknown.status_code == 422

    saved = client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": ["useful-002"],
        },
    )
    assert saved.status_code == 201
    assert len(saved.json()["imported_candidate_ids"]) == 2
    assert saved.json()["avatar_url"]
    # Save must not repeat any YouTube work.
    assert source.operation_log == operations_before_save

    repeated = client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": ["useful-002"],
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == saved.json()["id"]
    assert source.operation_log == operations_before_save

    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert [item["youtube_video_id"] for item in queue] == [
        "useful-000",
        "useful-002",
    ]
    assert {item["status"] for item in queue} == {"pending_start"}
    assert [item["origin"] for item in queue] == ["manual", "backfill"]

    library = client.get("/api/v1/episode-library").json()["items"]
    assert {item["state"] for item in library} == {"pending_start"}
    assert {item["library_state"] for item in library} == {
        "ready_to_start"
    }
    assert {tuple(item["available_actions"]) for item in library} == {
        ("start", "remove")
    }


def test_explicit_supported_source_video_import_creates_safe_channel_and_pending_run(
    tmp_path,
) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    source = _source(harness)
    client.patch("/api/v1/settings", json={"manual_processing": True})

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    operations_before_import = list(source.operation_log)

    imported = client.post(
        f"/api/v1/source-resolutions/{resolved['resolution_id']}:import-video"
    )

    assert imported.status_code == 201
    payload = imported.json()
    assert payload["reused"] is False
    assert payload["youtube_video_id"] == "useful-000"
    assert payload["candidate_status"] == "processing_queued"
    assert payload["run_status"] == "pending_start"
    assert payload["processing_run_id"] is not None
    assert payload["episode_id"] is None
    assert payload["source_channel"]["mode"] == "agent_filtering"
    assert payload["source_channel"]["created"] is True
    assert source.operation_log == operations_before_import

    channels = client.get("/api/v1/source-channels").json()["items"]
    assert len(channels) == 1
    assert channels[0]["mode"] == "agent_filtering"
    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert [item["youtube_video_id"] for item in queue] == ["useful-000"]
    assert [item["origin"] for item in queue] == ["manual"]


def test_explicit_source_video_import_preserves_channel_mode_and_is_idempotent(
    tmp_path,
) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": False})

    channel_resolution = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/@usefulshow"},
    ).json()
    saved_channel = client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": channel_resolution["resolution_id"],
            "mode": "paused",
            "backfill_video_ids": [],
        },
    ).json()
    video_resolution = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-003"},
    ).json()
    assert video_resolution["existing_source_channel"]["id"] == (saved_channel["id"])
    assert video_resolution["existing_source_channel"]["mode"] == "paused"
    endpoint = (
        f"/api/v1/source-resolutions/{video_resolution['resolution_id']}:import-video"
    )

    first = client.post(endpoint)
    second = client.post(endpoint)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["reused"] is False
    assert second.json()["reused"] is True
    assert first.json()["candidate_video_id"] == second.json()["candidate_video_id"]
    assert first.json()["processing_run_id"] == second.json()["processing_run_id"]
    assert first.json()["run_status"] == "queued"
    assert first.json()["source_channel"] == {
        "id": saved_channel["id"],
        "title": saved_channel["title"],
        "mode": "paused",
        "created": False,
    }
    assert (
        client.get(f"/api/v1/source-channels/{saved_channel['id']}").json()["mode"]
        == "paused"
    )
    assert len(client.get("/api/v1/processing-queue").json()["items"]) == 1


def test_explicit_source_video_import_rejects_channel_only_resolution(tmp_path) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/@usefulshow"},
    ).json()

    response = client.post(
        f"/api/v1/source-resolutions/{resolved['resolution_id']}:import-video"
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "An explicit import requires a Supported Source Video URL"
    )


def test_channel_mode_update_changes_only_future_discovery_policy(
    tmp_path,
) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    imported = client.post(
        f"/api/v1/source-resolutions/{resolved['resolution_id']}:import-video"
    ).json()
    run_id = imported["processing_run_id"]
    channel_id = imported["source_channel"]["id"]

    changed = client.patch(
        f"/api/v1/source-channels/{channel_id}",
        json={"mode": "process_every_video"},
    )

    assert changed.status_code == 200
    assert changed.json()["mode"] == "process_every_video"
    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert len(queue) == 1
    assert queue[0]["processing_run_id"] == run_id
    assert queue[0]["status"] == "pending_start"


def test_manual_queue_requires_an_explicit_start(tmp_path) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    resolution_id = resolved["resolution_id"]
    client.post(f"/api/v1/source-resolutions/{resolution_id}:backfill")
    client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": ["useful-002"],
        },
    )
    queue = client.get("/api/v1/processing-queue").json()["items"]
    first_run = queue[0]["processing_run_id"]

    started = client.post(f"/api/v1/processing-runs/{first_run}:start")
    assert started.status_code == 200
    assert started.json()["state"] == "queued"
    queued_library = client.get("/api/v1/episode-library").json()["items"]
    queued_item = next(
        item
        for item in queued_library
        if item["processing_run_id"] == first_run
    )
    assert queued_item["library_state"] == "queued"
    assert queued_item["available_actions"] == ["view", "remove"]
    conflict = client.post(f"/api/v1/processing-runs/{first_run}:start")
    assert conflict.status_code == 409

    remaining = client.post("/api/v1/processing-runs:start-all")
    assert remaining.json() == {"started": 1}
    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert {item["status"] for item in queue} == {"queued"}

    # Automatic mode restores the previous direct-queue behavior.
    client.patch("/api/v1/settings", json={"manual_processing": False})
    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-005"},
    ).json()
    client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolved["resolution_id"],
            "mode": "agent_filtering",
            "backfill_video_ids": [],
        },
    )
    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert {item["status"] for item in queue} == {"queued"}


def test_enrichment_touches_only_imported_videos(tmp_path) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    source = _source(harness)

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    resolution_id = resolved["resolution_id"]
    client.post(f"/api/v1/source-resolutions/{resolution_id}:backfill")
    client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": ["useful-002"],
        },
    )
    assert source.operation_log.count("video") == 0

    service = TransPodcastService(harness.dependencies)
    outcome = service.enrich_candidate_videos(limit=5)
    assert outcome == {"enriched": 2, "failed": 0, "pending": 0}
    # Only the requested and the selected Channel Backfill videos are
    # enriched; the eight unselected previews never reach YouTube.
    assert source.operation_log.count("video") == 2

    repeat = service.enrich_candidate_videos(limit=5)
    assert repeat == {"enriched": 0, "failed": 0, "pending": 0}
    assert source.operation_log.count("video") == 2


def test_channel_page_reuses_the_cached_recent_window(tmp_path) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    source = _source(harness)

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    resolution_id = resolved["resolution_id"]
    client.post(f"/api/v1/source-resolutions/{resolution_id}:backfill")
    saved = client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": [],
        },
    ).json()
    channel_id = saved["id"]
    operations_after_save = list(source.operation_log)

    # The post-save redirect is served from the save-time seeded cache,
    # and reopening the page later still performs no YouTube work.
    for _ in range(2):
        listed = client.get(
            f"/api/v1/source-channels/{channel_id}/recent-videos"
        )
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 10
        assert source.operation_log == operations_after_save

    refreshed = client.get(
        f"/api/v1/source-channels/{channel_id}/recent-videos"
        "?refresh=true"
    )
    assert refreshed.status_code == 200
    assert source.operation_log == [
        *operations_after_save,
        "recent_video_previews",
        "enrich_video_previews",
    ]


def test_add_to_queue_from_the_channel_page_is_immediate(
    tmp_path,
) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    source = _source(harness)
    client.patch("/api/v1/settings", json={"manual_processing": True})

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    resolution_id = resolved["resolution_id"]
    client.post(f"/api/v1/source-resolutions/{resolution_id}:backfill")
    saved = client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": [],
        },
    ).json()
    channel_id = saved["id"]
    operations_after_save = list(source.operation_log)

    # A cached recent video queues without any live YouTube request.
    queued = client.post(
        f"/api/v1/source-channels/{channel_id}/candidate-videos",
        json={"youtube_video_id": "useful-003"},
    )
    assert queued.status_code == 201
    assert queued.json()["run_status"] == "pending_start"
    assert source.operation_log == operations_after_save

    repeated = client.post(
        f"/api/v1/source-channels/{channel_id}/candidate-videos",
        json={"youtube_video_id": "useful-003"},
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == queued.json()["id"]
    assert source.operation_log == operations_after_save

    # A video outside the cached window falls back to one full
    # metadata request.
    older = client.post(
        f"/api/v1/source-channels/{channel_id}/candidate-videos",
        json={"youtube_video_id": "useful-011"},
    )
    assert older.status_code == 201
    assert source.operation_log == [*operations_after_save, "video"]

    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert [item["youtube_video_id"] for item in queue] == [
        "useful-000",
        "useful-003",
        "useful-011",
    ]


def test_pending_run_can_be_removed_and_requeued(tmp_path) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    resolution_id = resolved["resolution_id"]
    client.post(f"/api/v1/source-resolutions/{resolution_id}:backfill")
    saved = client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": ["useful-002"],
        },
    ).json()
    channel_id = saved["id"]

    # The saved channel exposes its avatar for the channel list.
    channels = client.get("/api/v1/source-channels").json()["items"]
    assert channels[0]["avatar_url"]

    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert len(queue) == 2
    run_id = queue[0]["processing_run_id"]

    removed = client.delete(f"/api/v1/processing-runs/{run_id}")
    assert removed.status_code == 200
    assert removed.json()["state"] == "removed"
    assert (
        client.delete("/api/v1/processing-runs/999").status_code == 404
    )

    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert [item["youtube_video_id"] for item in queue] == ["useful-002"]

    # The removed video returns to a queueable state on the channel
    # page and can be added again.
    recent = client.get(
        f"/api/v1/source-channels/{channel_id}/recent-videos"
    ).json()["items"]
    removed_row = next(
        item
        for item in recent
        if item["youtube_video_id"] == "useful-000"
    )
    assert removed_row["candidate_status"] is None
    assert removed_row["run_status"] is None

    requeued = client.post(
        f"/api/v1/source-channels/{channel_id}/candidate-videos",
        json={"youtube_video_id": "useful-000"},
    )
    assert requeued.status_code == 201
    assert requeued.json()["run_status"] == "pending_start"

    started_run_id = requeued.json()["processing_run_id"]
    with harness.dependencies.database.session() as session:
        started_run = session.get(ProcessingRunRecord, started_run_id)
        assert started_run is not None
        started_run.status = "queued"
        started_run.first_claimed_at = harness.dependencies.clock.now()

    stale_remove = client.delete(
        f"/api/v1/processing-runs/{started_run_id}"
    )
    assert stale_remove.status_code == 409
    assert "has not started" in stale_remove.json()["detail"]

    with harness.dependencies.database.session() as session:
        started_run = session.get(ProcessingRunRecord, started_run_id)
        assert started_run is not None
        started_run.first_claimed_at = None
        now = harness.dependencies.clock.now()
        session.add(
            ProcessingStageRecord(
                processing_run_id=started_run_id,
                stage="source_archive",
                version="test",
                provider=None,
                model=None,
                input_hash="a" * 64,
                output_hash="b" * 64,
                usage_json="{}",
                duration_ms=1,
                started_at=now,
                completed_at=now,
            )
        )

    item = next(
        item
        for item in client.get("/api/v1/episode-library").json()["items"]
        if item["processing_run_id"] == started_run_id
    )
    assert item["available_actions"] == ["view"]


def test_expired_resolution_requires_a_fresh_resolve(tmp_path) -> None:
    harness = _harness(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    resolved = client.post(
        "/api/v1/source-resolutions",
        json={"url": "https://www.youtube.com/watch?v=useful-000"},
    ).json()
    resolution_id = resolved["resolution_id"]
    harness.dependencies.clock.value += timedelta(minutes=16)

    expired_backfill = client.post(
        f"/api/v1/source-resolutions/{resolution_id}:backfill"
    )
    assert expired_backfill.status_code == 404
    expired_save = client.post(
        "/api/v1/source-channels",
        json={
            "resolution_id": resolution_id,
            "mode": "agent_filtering",
            "backfill_video_ids": [],
        },
    )
    assert expired_save.status_code == 404
