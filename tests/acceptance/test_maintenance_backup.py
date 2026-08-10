from __future__ import annotations

import io
import json
import os
import sqlite3
import tarfile
from contextlib import closing
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_maintenance_bounds_intermediates_and_creates_verified_backup(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    harness.storage.put_backup(
        "migration/pre-compaction-2026-07-20.sqlite3",
        b"expired recovery point",
    )
    harness.storage.put_backup(
        "migration/pre-compaction-2026-07-22.sqlite3",
        b"retained recovery point",
    )
    intermediate = (
        harness.dependencies.artifact_root
        / "intermediates"
        / "expired"
        / "render.wav"
    )
    intermediate.parent.mkdir(parents=True)
    intermediate.write_bytes(b"rebuildable")
    old_timestamp = (
        harness.dependencies.clock.now() - timedelta(days=8)
    ).timestamp()
    os.utime(intermediate, (old_timestamp, old_timestamp))
    artwork = (
        harness.dependencies.artifact_root
        / "episode-artwork"
        / "expired.jpg"
    )
    artwork.parent.mkdir(parents=True)
    artwork.write_bytes(b"rebuildable-artwork")
    os.utime(artwork, (old_timestamp, old_timestamp))
    baseline = (
        harness.dependencies.artifact_root
        / "baselines"
        / "accepted"
        / "render.wav"
    )
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(b"pinned")
    os.utime(baseline, (old_timestamp, old_timestamp))

    result = client.post("/api/v1/maintenance-runs")

    assert result.status_code == 200
    assert result.json()["deleted_intermediates"] == 1
    assert result.json()["deleted_episode_artwork"] == 1
    assert not intermediate.exists()
    assert not artwork.exists()
    assert baseline.exists()
    backup_paths = harness.storage.backup_paths()
    assert backup_paths == [
        "daily/2026-07-28.tar.gz",
        "migration/pre-compaction-2026-07-22.sqlite3",
        "monthly/2026-07.tar.gz",
    ]
    backup = harness.storage.read_backup("daily/2026-07-28.tar.gz")
    assert backup is not None
    assert harness.feed_token.encode() not in backup
    assert result.json()["backup"]["verified"] is True


def test_state_backup_preserves_channel_voice_library_state(
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
    client.post("/api/v1/processing-runs:advance")
    candidate = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"][0]
    client.post(
        f"/api/v1/channel-voice-candidates/{candidate['id']}:save",
        json={"display_name": "Known speaker"},
    )

    result = client.post("/api/v1/maintenance-runs").json()
    backup = harness.storage.read_backup(result["backup"]["key"])

    assert backup is not None
    with tarfile.open(fileobj=io.BytesIO(backup), mode="r:gz") as bundle:
        members = {
            member.name: bundle.extractfile(member).read()
            for member in bundle.getmembers()
            if member.isfile() and bundle.extractfile(member) is not None
        }
    assert any(
        name.endswith("/candidate-reference.wav")
        for name in members
    )
    restored_database = tmp_path / "restored-channel-voices.sqlite3"
    restored_database.write_bytes(members["state.sqlite3"])
    with closing(sqlite3.connect(restored_database)) as connection:
        profile_count = connection.execute(
            "SELECT count(*) FROM channel_speaker_profiles "
            "WHERE state = 'active'"
        ).fetchone()
        saved_count = connection.execute(
            "SELECT count(*) FROM channel_voice_candidates "
            "WHERE decision = 'saved'"
        ).fetchone()
    assert profile_count == (1,)
    assert saved_count == (1,)


def test_translation_project_cleanup_retains_recovery_and_audit_state(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    projects = harness.dependencies.artifact_root / "translation-projects"
    completed = projects / "run-1" / "completed-project"
    active = projects / "run-2" / "active-project"
    old_timestamp = (
        harness.dependencies.clock.now() - timedelta(days=8)
    ).timestamp()
    for root, phase in ((completed, "completed"), (active, "authoring")):
        (root / "author").mkdir(parents=True)
        (root / "events").mkdir()
        (root / "author" / "translation.md").write_text(
            "TARGET: example",
            encoding="utf-8",
        )
        (root / "events" / "author-turn-001.jsonl").write_text(
            '{"type":"turn.completed"}',
            encoding="utf-8",
        )
        state = root / "project.json"
        state.write_text(
            json.dumps({"phase": phase, "input_hash": phase}),
            encoding="utf-8",
        )
        os.utime(state, (old_timestamp, old_timestamp))

    result = client.post("/api/v1/maintenance-runs").json()

    assert result["deleted_translation_project_files"] == 2
    assert (completed / "project.json").exists()
    assert not (completed / "author").exists()
    assert not (completed / "events").exists()
    assert (active / "author" / "translation.md").exists()
    assert (active / "events" / "author-turn-001.jsonl").exists()
    backup = harness.storage.read_backup(result["backup"]["key"])
    assert backup is not None
    with tarfile.open(fileobj=io.BytesIO(backup), mode="r:gz") as bundle:
        names = {
            member.name
            for member in bundle.getmembers()
            if member.isfile()
        }
    assert (
        "translation-projects/run-1/completed-project/project.json"
        in names
    )
    assert (
        "translation-projects/run-2/active-project/author/translation.md"
        in names
    )
    assert not any(
        name.startswith("translation-projects/") and "/events/" in name
        for name in names
    )


def test_restore_verification_rejects_missing_revision_artifact(
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
        session.execute(text("DELETE FROM structured_artifacts"))

    with pytest.raises(
        ValueError,
        match="revision artifact reference",
    ):
        client.post("/api/v1/maintenance-runs")


def test_restore_verification_rejects_malformed_revision_reference(
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
            text(
                "UPDATE episode_segment_revisions "
                "SET translation_json = "
                '\'{"artifact_sha256":""}\' '
                "WHERE id = (SELECT min(id) FROM episode_segment_revisions)"
            )
        )

    with pytest.raises(
        ValueError,
        match="malformed revision artifact reference",
    ):
        client.post("/api/v1/maintenance-runs")
