from __future__ import annotations

import json
import shutil

from fastapi.testclient import TestClient
from sqlalchemy import select

from transpodcast.api import create_app
from transpodcast.models import (
    EpisodeRecord,
    ProcessingRunRecord,
    ProcessingStageRecord,
)
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    capture_sql_statements,
)


def test_owner_reprocesses_a_completed_episode_from_source_analysis(
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
    first = client.post("/api/v1/processing-runs:advance").json()
    episode_id = first["episode_id"]
    run_id = first["processing_run_id"]
    analysis = harness.dependencies.analysis
    assert isinstance(analysis, FakeAnalysisAdapter)
    assert harness.dependencies.source.archive_invocation_count == 1
    assert analysis.invocation_count == 1
    assert harness.codex.invocation_count == 1
    assert harness.modal.invocation_count == 1

    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        original_preview_key = episode.preview_key
        assert episode.revision_version == 1

    for project in (
        harness.dependencies.artifact_root / "translation-projects"
    ).glob("run-*/*"):
        for workspace in ("author", "review"):
            path = project / workspace
            if path.is_dir():
                shutil.rmtree(path)

    requested = client.post(f"/api/v1/episodes/{episode_id}:reprocess")

    assert requested.status_code == 202
    assert requested.json() == {
        "episode_id": episode_id,
        "processing_run_id": run_id,
        "state": "queued",
        "stage": "translation",
        "reused_checkpoint": "source_analysis",
    }
    library_item = client.get("/api/v1/episode-library").json()["items"][0]
    assert library_item["state"] == "revision_processing"
    assert library_item["stage"] == "translation"
    assert library_item["can_reprocess"] is False
    with harness.dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, run_id)
        episode = session.get(EpisodeRecord, episode_id)
        assert run is not None
        assert episode is not None
        assert run.source_archive_key is not None
        assert run.analysis_json is not None
        assert run.translation_json is None
        assert run.render_json is None
        assert run.render_preview_key is None
        assert episode.preview_key == original_preview_key
        assert episode.revision_version == 1
        stage_names = session.scalars(
            select(ProcessingStageRecord.stage).where(
                ProcessingStageRecord.processing_run_id == run_id
            )
        ).all()
        assert stage_names == ["source_archive", "source_analysis"]

    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["processing_state"] == "completed"
    assert completed["episode_id"] == episode_id
    assert harness.dependencies.source.archive_invocation_count == 1
    assert analysis.invocation_count == 1
    assert harness.codex.invocation_count == 2
    assert harness.modal.invocation_count == 2
    with harness.dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert episode.preview_key != original_preview_key
        assert episode.revision_version == 2
        assert episode.state == "publication_queue"


def test_episode_library_does_not_load_detail_artifacts(tmp_path) -> None:
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
    completed = client.post("/api/v1/processing-runs:advance").json()
    artifact = json.dumps({"marker": "detail-only", "value": "x" * 16_384})
    with harness.dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            completed["processing_run_id"],
        )
        episode = session.get(EpisodeRecord, completed["episode_id"])
        assert run is not None
        assert episode is not None
        run.analysis_json = artifact
        run.translation_json = artifact
        run.content_edit_json = artifact
        run.publication_metadata_json = artifact
        run.artwork_json = artifact
        run.render_json = artifact
        episode.transcript_json = artifact
        episode.chapters_json = artifact
        episode.quality_json = artifact

    with capture_sql_statements(harness.dependencies.database) as statements:
        response = client.get("/api/v1/episode-library")

    assert response.status_code == 200
    assert response.json()["items"][0]["episode_id"] == completed["episode_id"]
    assert len(statements) == 3
    selected_sql = "\n".join(statements).lower()
    for detail_column in (
        "processing_runs.analysis_json",
        "processing_runs.translation_json",
        "processing_runs.content_edit_json",
        "processing_runs.publication_metadata_json",
        "processing_runs.artwork_json",
        "processing_runs.render_json",
        "episodes.transcript_json",
        "episodes.chapters_json",
        "episodes.quality_json",
    ):
        assert detail_column not in selected_sql


def test_reprocessing_an_old_pipeline_rebuilds_source_analysis(tmp_path) -> None:
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
    completed = client.post("/api/v1/processing-runs:advance").json()
    run_id = completed["processing_run_id"]
    episode_id = completed["episode_id"]
    analysis = harness.dependencies.analysis
    assert isinstance(analysis, FakeAnalysisAdapter)

    with harness.dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, run_id)
        stage = session.scalar(
            select(ProcessingStageRecord).where(
                ProcessingStageRecord.processing_run_id == run_id,
                ProcessingStageRecord.stage == "source_analysis",
            )
        )
        assert run is not None
        assert stage is not None
        payload = json.loads(str(run.analysis_json))
        payload["pipeline_version"] = "transpodcast-modal-pipeline-v8"
        run.analysis_json = json.dumps(payload)
        run.pipeline_version = "transpodcast-modal-pipeline-v8"
        stage.version = "transpodcast-modal-pipeline-v8"

    requested = client.post(f"/api/v1/episodes/{episode_id}:reprocess")

    assert requested.json()["stage"] == "source_analysis"
    assert requested.json()["reused_checkpoint"] == "source_archive"
    with harness.dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, run_id)
        assert run is not None
        assert run.analysis_json is None
        assert run.pipeline_version == "pending-current-pipeline"

    rerun = client.post("/api/v1/processing-runs:advance").json()

    assert rerun["processing_state"] == "completed"
    assert analysis.invocation_count == 2


def test_episode_reprocessing_rejects_an_active_reprocess(tmp_path) -> None:
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
    episode_id = client.post("/api/v1/processing-runs:advance").json()[
        "episode_id"
    ]

    first = client.post(f"/api/v1/episodes/{episode_id}:reprocess")
    second = client.post(f"/api/v1/episodes/{episode_id}:reprocess")

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["detail"] == (
        "Translated Episode is already being reprocessed"
    )


def test_published_episode_stays_in_the_feed_until_reprocess_is_approved(
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
    episode_id = client.post("/api/v1/processing-runs:advance").json()[
        "episode_id"
    ]
    publish = next(
        item.token
        for item in harness.telegram.latest().actions
        if item.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{publish}:apply")
    feed_before = client.get(f"/feed/{harness.feed_token}.xml").text

    requested = client.post(f"/api/v1/episodes/{episode_id}:reprocess")
    feed_while_processing = client.get(f"/feed/{harness.feed_token}.xml").text
    completed = client.post("/api/v1/processing-runs:advance").json()
    feed_pending = client.get(f"/feed/{harness.feed_token}.xml").text

    assert requested.status_code == 202
    assert feed_while_processing == feed_before
    assert completed["episode_state"] == "revision_pending"
    assert feed_pending == feed_before
