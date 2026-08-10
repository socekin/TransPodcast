from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier, Event

from fastapi.testclient import TestClient
from sqlalchemy import select

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate
from transpodcast.models import (
    CandidateVideoRecord,
    ProcessingAttemptRecord,
    ProcessingRunRecord,
    SourceChannelRecord,
)
from transpodcast.service import TransPodcastService
from transpodcast.testing import AcceptanceHarness, FakeSourceAdapter
from transpodcast.worker import ProcessingWorker


def test_discovery_due_time_is_durable_across_service_restart(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    first = TransPodcastService(harness.dependencies)

    assert first.claim_scheduled_discovery("worker-a", 120) is True
    first.complete_scheduled_discovery("worker-a", 120)
    first.close()

    restarted = TransPodcastService(harness.dependencies)
    try:
        assert restarted.claim_scheduled_discovery("worker-b", 120) is False
    finally:
        restarted.close()


def test_expired_discovery_lease_can_be_recovered(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    service = TransPodcastService(harness.dependencies)

    try:
        assert service.claim_scheduled_discovery("worker-a", 120) is True
        harness.dependencies.clock.value = harness.dependencies.clock.value.replace(
            hour=18
        )

        assert service.claim_scheduled_discovery("worker-b", 120) is True
    finally:
        service.close()


def test_manual_discovery_does_not_duplicate_an_active_scan(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    service = TransPodcastService(harness.dependencies)
    assert service.claim_scheduled_discovery("worker-a", 120) is True

    response = TestClient(create_app(harness.dependencies)).post(
        "/api/v1/discovery-runs"
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Source Channel discovery is active"
    service.complete_scheduled_discovery("worker-a", 120)
    service.close()


def test_scheduled_manual_and_restarted_discovery_share_one_lease(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    services = [
        TransPodcastService(harness.dependencies),
        TransPodcastService(harness.dependencies),
        TransPodcastService(harness.dependencies),
    ]
    barrier = Barrier(3)

    def claim(index: int) -> bool:
        barrier.wait(timeout=2)
        return services[index].claim_scheduled_discovery(
            f"worker-{index}",
            120,
            force=index == 1,
        )

    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(claim, range(3)))
    finally:
        for service in services:
            service.close()

    assert sum(results) == 1


def test_real_queued_run_advances_while_discovery_holds_its_session(
    tmp_path,
) -> None:
    discovery_started = Event()
    release_discovery = Event()

    class BlockingSource(FakeSourceAdapter):
        def discover(self, youtube_channel_id: str) -> list[SourceCandidate]:
            assert youtube_channel_id == "UC_USEFUL_SHOW"
            discovery_started.set()
            release_discovery.wait(timeout=5)
            return []

    harness = AcceptanceHarness.ready(tmp_path)
    setup_client = TestClient(create_app(harness.dependencies))
    setup_client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    dependencies = replace(
        harness.dependencies,
        source=BlockingSource(),
    )
    service = TransPodcastService(dependencies)
    service.update_settings({"auto_discovery": True})
    worker = ProcessingWorker(service)
    worker.next_maintenance_at = datetime(2026, 7, 29, 15, 0)

    try:
        worker.run_once()
        assert discovery_started.wait(timeout=1)
        with dependencies.database.session() as session:
            channel = session.scalars(select(SourceChannelRecord)).one()
            candidate = CandidateVideoRecord(
                source_channel_id=channel.id,
                youtube_video_id="queued-during-discovery",
                title="Queued during discovery",
                description="A deterministic scheduling fixture.",
                duration_seconds=60,
                language="en",
                published_at=datetime(2026, 8, 3, tzinfo=UTC),
                source_url=(
                    "https://www.youtube.com/watch?"
                    "v=queued-during-discovery"
                ),
                captions="A complete deterministic transcript.",
                status="processing_queued",
                triage_decision="process",
                triage_confidence=1.0,
                triage_rationale="Scheduling fixture",
                matched_interests_json="[]",
                created_at=dependencies.clock.now(),
            )
            session.add(candidate)
            session.flush()
            service._queue_processing(
                session,
                candidate,
                priority=10,
                start_immediately=True,
            )

        worker.run_once()

        with dependencies.database.session() as session:
            run = session.scalars(select(ProcessingRunRecord)).one()
            assert run.first_claimed_at is not None
            assert run.status == "completed"
    finally:
        release_discovery.set()
        worker.close()
        service.close()


def test_worker_restart_recovers_an_interrupted_stage_attempt(
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
    with harness.dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, 1)
        assert run is not None
        run.status = "processing"
        run.stage = "source_archive"
        run.lease_expires_at = harness.dependencies.clock.now()
        interrupted = ProcessingAttemptRecord(
            processing_run_id=run.id,
            stage="source_archive",
            status="processing",
            duration_ms=0,
            transfer_bytes=0,
            resume_checkpoint="ingest",
            created_at=harness.dependencies.clock.now(),
            completed_at=None,
            correlation_id="interrupted-source-archive",
            routing_json="{}",
            timing_provenance="recorded",
        )
        session.add(interrupted)
        session.flush()
        interrupted_id = interrupted.id

    service = TransPodcastService(harness.dependencies)
    assert service.recover_interrupted_processing() == 1

    recovered = client.get("/api/v1/processing-runs/1").json()
    assert recovered["state"] == "queued"
    assert recovered["attempts"][-1]["status"] == "interrupted"

    assert (
        client.post("/api/v1/processing-runs:advance").json()["processing_state"]
        == "completed"
    )
    completed = client.get("/api/v1/processing-runs/1").json()
    source_attempts = [
        item for item in completed["attempts"] if item["stage"] == "source_archive"
    ]
    assert source_attempts[-1]["supersedes_attempt_id"] == interrupted_id
    service.close()
