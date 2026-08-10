from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate
from transpodcast.models import (
    CandidateVideoRecord,
    ProcessingRunRecord,
    TelegramActionRecord,
)
from transpodcast.testing import AcceptanceHarness


def test_application_bootstraps_state_through_alembic(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)

    create_app(harness.dependencies)

    with harness.dependencies.database.session() as session:
        revision = session.scalar(
            text("SELECT version_num FROM alembic_version")
        )
    assert revision == "0014_owner_console_publication_decisions"


def test_observability_migration_does_not_invent_a_pending_claim_time(
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
        run = session.scalars(select(ProcessingRunRecord)).one()
        run.status = "pending_start"
        run_id = run.id

    config = Config()
    migrations = (
        Path(__file__).parents[2] / "src" / "transpodcast" / "migrations"
    )
    config.set_main_option("script_location", str(migrations))
    with harness.dependencies.database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0012_published_artwork_snapshot")
        connection.execute(
            text(
                "INSERT INTO processing_attempts "
                "(processing_run_id, stage, status, duration_ms, "
                "transfer_bytes, resume_checkpoint, error_kind, "
                "error_summary, created_at) VALUES "
                "(:run_id, 'source_archive', 'completed', 1200, 42, "
                "'source_archive', NULL, NULL, :created_at)"
            ),
            {
                "run_id": run_id,
                "created_at": "2026-08-03 00:00:00",
            },
        )
        connection.commit()
        command.upgrade(config, "head")
        first_claimed_at = connection.scalar(
            text(
                "SELECT first_claimed_at FROM processing_runs "
                "WHERE id = :run_id"
            ),
            {"run_id": run_id},
        )

    assert first_claimed_at is None
    detail = client.get(f"/api/v1/processing-runs/{run_id}").json()
    assert detail["attempts"][0]["timing_provenance"] == "legacy"
    assert detail["attempts"][0]["completed_at"] == (
        detail["attempts"][0]["started_at"]
    )


def test_episode_publication_presentation_migration_adds_draft_and_snapshot_fields(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    create_app(harness.dependencies)
    config = Config()
    migrations = (
        Path(__file__).parents[2] / "src" / "transpodcast" / "migrations"
    )
    config.set_main_option("script_location", str(migrations))

    with harness.dependencies.database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0010_semantic_content_editing")
        assert "publication_description" not in {
            column["name"]
            for column in inspect(connection).get_columns("episodes")
        }

        command.upgrade(config, "head")
        episode_columns = {
            column["name"]
            for column in inspect(connection).get_columns("episodes")
        }
        run_columns = {
            column["name"]
            for column in inspect(connection).get_columns("processing_runs")
        }

    assert {
        "publication_description",
        "chapter_proposals_json",
        "artwork_path",
        "artwork_json",
        "publication_artwork_key",
        "published_artwork_json",
        "published_publication_description",
        "revision_kind",
    } <= episode_columns
    assert {
        "publication_metadata_json",
        "artwork_status",
        "artwork_path",
        "artwork_json",
    } <= run_columns


def test_semantic_content_editing_migration_adds_separate_artifact_column(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    create_app(harness.dependencies)
    config = Config()
    migrations = (
        Path(__file__).parents[2] / "src" / "transpodcast" / "migrations"
    )
    config.set_main_option("script_location", str(migrations))

    with harness.dependencies.database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0009_telegram_delivery_retry")
        assert "content_edit_json" not in {
            column["name"]
            for column in inspect(connection).get_columns("processing_runs")
        }
        assert not inspect(connection).has_table(
            "episode_content_edit_revisions"
        )

        command.upgrade(config, "head")

        columns = {
            column["name"]
            for column in inspect(connection).get_columns("processing_runs")
        }
    assert "content_edit_json" in columns
    with harness.dependencies.database.engine.connect() as connection:
        assert inspect(connection).has_table(
            "episode_content_edit_revisions"
        )


def test_telegram_delivery_retry_migration_upgrades_an_existing_table(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    create_app(harness.dependencies)
    config = Config()
    migrations = (
        Path(__file__).parents[2] / "src" / "transpodcast" / "migrations"
    )
    config.set_main_option("script_location", str(migrations))

    with harness.dependencies.database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0008_candidate_recommendations")
        assert "next_retry_at" not in {
            column["name"]
            for column in inspect(connection).get_columns(
                "telegram_deliveries"
            )
        }

        command.upgrade(config, "head")

        columns = {
            column["name"]
            for column in inspect(connection).get_columns(
                "telegram_deliveries"
            )
        }
    assert "next_retry_at" in columns


def test_candidate_recommendation_migration_invalidates_legacy_actions(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[
            SourceCandidate(
                youtube_video_id="legacy-candidate",
                title="A legacy candidate",
                description="A candidate created before the new policy.",
                duration_seconds=1800,
                language="en",
                published_at=datetime(2026, 8, 2, 11, 0, tzinfo=UTC),
                source_url=(
                    "https://www.youtube.com/watch?v=legacy-candidate"
                ),
                captions="A product strategy conversation.",
            )
        ],
        triage_results=None,
    )
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
        candidate = session.scalars(select(CandidateVideoRecord)).one()
        candidate_id = candidate.id
        original_status = candidate.status
        session.add(
            TelegramActionRecord(
                token_hash="legacy-candidate-action",
                candidate_video_id=candidate.id,
                decision="candidate_process",
                expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            )
        )

    config = Config()
    migrations = (
        Path(__file__).parents[2] / "src" / "transpodcast" / "migrations"
    )
    config.set_main_option("script_location", str(migrations))
    with harness.dependencies.database.engine.connect() as connection:
        config.attributes["connection"] = connection
        connection.execute(text("DROP TABLE telegram_deliveries"))
        connection.execute(
            text(
                "UPDATE alembic_version SET version_num = "
                "'0007_processing_run_activity'"
            )
        )
        connection.commit()
        command.upgrade(config, "head")
        row = connection.execute(
            text("SELECT status FROM candidate_videos WHERE id = :id"),
            {"id": candidate_id},
        ).mappings().one()
        action_is_expired = connection.scalar(
            text(
                "SELECT expires_at <= CURRENT_TIMESTAMP "
                "FROM telegram_actions WHERE token_hash = "
                "'legacy-candidate-action'"
            )
        )
        labeled_examples = connection.scalar(
            text(
                "SELECT count(*) FROM labeled_examples "
                "WHERE candidate_video_id = :id"
            ),
            {"id": candidate_id},
        )

    assert row["status"] == original_status
    assert action_is_expired == 1
    assert labeled_examples == 0


def test_channel_voice_migration_preserves_legacy_pending_candidates(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    processed = client.post("/api/v1/processing-runs:advance").json()

    config = Config()
    migrations = (
        Path(__file__).parents[2] / "src" / "transpodcast" / "migrations"
    )
    config.set_main_option("script_location", str(migrations))
    with harness.dependencies.database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0004_structured_artifacts")
        command.upgrade(config, "head")
        candidates = connection.execute(
            text(
                """
                SELECT source_channel_id, source_episode_id, speaker_id,
                       local_label, segment_count, decision
                FROM channel_voice_candidates
                """
            )
        ).mappings().all()
        profiles = connection.scalar(
            text("SELECT count(*) FROM channel_speaker_profiles")
        )

    assert [dict(item) for item in candidates] == [
        {
            "source_channel_id": channel["id"],
            "source_episode_id": processed["episode_id"],
            "speaker_id": 1,
            "local_label": "speaker-01",
            "segment_count": 1,
            "decision": "pending",
        }
    ]
    assert profiles == 0


def test_structured_artifact_downgrade_rehydrates_revision_payloads(
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
    client.post("/api/v1/processing-runs:advance")

    config = Config()
    migrations = (
        Path(__file__).parents[2] / "src" / "transpodcast" / "migrations"
    )
    config.set_main_option("script_location", str(migrations))
    with harness.dependencies.database.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0003_processing_attempts")
        translation_json = connection.scalar(
            text(
                "SELECT translation_json "
                "FROM episode_segment_revisions LIMIT 1"
            )
        )
        artifact_table = connection.scalar(
            text(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'structured_artifacts'"
            )
        )

    assert len(str(translation_json)) > 200
    assert artifact_table == 0


def test_structured_artifact_downgrade_rejects_corrupt_payload(
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
    client.post("/api/v1/processing-runs:advance")
    with harness.dependencies.database.session() as session:
        session.execute(
            text("UPDATE structured_artifacts SET payload_json = '{}'")
        )

    config = Config()
    migrations = (
        Path(__file__).parents[2] / "src" / "transpodcast" / "migrations"
    )
    config.set_main_option("script_location", str(migrations))
    with harness.dependencies.database.engine.connect() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(
            RuntimeError,
            match="unresolved or corrupt Structured Artifacts",
        ):
            command.downgrade(config, "0003_processing_attempts")
