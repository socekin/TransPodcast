from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _create_legacy_database(path: Path) -> str:
    payload = json.dumps(
        {"segments": ["A historical artifact that must remain recoverable."] * 8}
    )
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE episode_segment_revisions (
                id INTEGER PRIMARY KEY,
                translation_json TEXT NOT NULL
            );
            CREATE TABLE canonical_transcript_revisions (
                id INTEGER PRIMARY KEY,
                transcript_json TEXT NOT NULL
            );
            CREATE TABLE structured_artifacts (
                sha256 TEXT PRIMARY KEY,
                artifact_kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at DATETIME NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO episode_segment_revisions(translation_json) VALUES (?)",
            (payload,),
        )
        connection.execute(
            "INSERT INTO canonical_transcript_revisions(transcript_json) VALUES (?)",
            (payload,),
        )
        connection.commit()
    finally:
        connection.close()
    return payload


def _run_compaction(
    database_path: Path,
    *arguments: str,
    allow_local_test_storage: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "TRANSPODCAST_DATABASE_PATH": str(database_path),
        "TRANSPODCAST_STORAGE_MODE": "local",
        "TRANSPODCAST_LOCAL_STORAGE_ROOT": str(
            database_path.parent / "object-storage"
        ),
    }
    return subprocess.run(
        [
            sys.executable,
            "scripts/compact_revision_history.py",
            *(
                ["--allow-local-test-storage"]
                if allow_local_test_storage
                else []
            ),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_compaction_dry_run_never_changes_revision_artifacts(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    payload = _create_legacy_database(database_path)

    completed = _run_compaction(database_path, "--dry-run")

    assert completed.returncode == 0, completed.stderr
    assert "dry run: true" in completed.stdout
    connection = sqlite3.connect(database_path)
    try:
        stored = connection.execute(
            "SELECT translation_json FROM episode_segment_revisions"
        ).fetchone()
        artifacts = connection.execute(
            "SELECT count(*) FROM structured_artifacts"
        ).fetchone()
    finally:
        connection.close()
    assert stored == (payload,)
    assert artifacts == (0,)


def test_compaction_preserves_payloads_and_creates_verified_backup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    backup_path = tmp_path / "pre-compaction.sqlite3"
    payload = _create_legacy_database(database_path)
    digest = hashlib.sha256(payload.encode()).hexdigest()

    completed = _run_compaction(
        database_path,
        "--backup-only",
        "--backup-path",
        str(backup_path),
        "--remote-backup-key",
        "migration/test.sqlite3",
    )
    assert completed.returncode == 0, completed.stderr
    backup_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()

    completed = _run_compaction(
        database_path,
        "--backup-path",
        str(backup_path),
        "--backup-sha256",
        backup_digest,
        "--remote-backup-key",
        "migration/test.sqlite3",
        "--batch-size",
        "1",
    )

    assert completed.returncode == 0, completed.stderr
    assert "rewritten rows: 2" in completed.stdout
    assert "backup verified: true" in completed.stdout
    assert backup_path.is_file()
    backup = sqlite3.connect(backup_path)
    try:
        backup_payload = backup.execute(
            "SELECT translation_json FROM episode_segment_revisions"
        ).fetchone()
        backup_integrity = backup.execute("PRAGMA integrity_check").fetchone()
    finally:
        backup.close()
    assert backup_payload == (payload,)
    assert backup_integrity == ("ok",)

    compacted = sqlite3.connect(database_path)
    try:
        reference = compacted.execute(
            "SELECT translation_json FROM episode_segment_revisions"
        ).fetchone()
        artifact = compacted.execute(
            """
            SELECT sha256, payload_json
            FROM structured_artifacts
            WHERE sha256 = ?
            """,
            (digest,),
        ).fetchone()
        compacted_integrity = compacted.execute(
            "PRAGMA integrity_check"
        ).fetchone()
    finally:
        compacted.close()
    assert reference == (json.dumps({"artifact_sha256": digest}),)
    assert artifact == (digest, payload)
    assert compacted_integrity == ("ok",)


def test_backup_only_creates_recovery_point_without_compacting(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    backup_path = tmp_path / "pre-compaction.sqlite3"
    payload = _create_legacy_database(database_path)

    completed = _run_compaction(
        database_path,
        "--backup-only",
        "--backup-path",
        str(backup_path),
        "--remote-backup-key",
        "migration/test.sqlite3",
    )

    assert completed.returncode == 0, completed.stderr
    assert "backup verified: true" in completed.stdout
    assert backup_path.is_file()
    connection = sqlite3.connect(database_path)
    try:
        stored = connection.execute(
            "SELECT translation_json FROM episode_segment_revisions"
        ).fetchone()
    finally:
        connection.close()
    assert stored == (payload,)

    backup_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    compacted = _run_compaction(
        database_path,
        "--backup-path",
        str(backup_path),
        "--backup-sha256",
        backup_digest,
        "--remote-backup-key",
        "migration/test.sqlite3",
        "--batch-size",
        "1",
    )

    assert compacted.returncode == 0, compacted.stderr
    connection = sqlite3.connect(database_path)
    try:
        reference = connection.execute(
            "SELECT translation_json FROM episode_segment_revisions"
        ).fetchone()
    finally:
        connection.close()
    expected_reference = json.dumps(
        {"artifact_sha256": hashlib.sha256(payload.encode()).hexdigest()}
    )
    assert reference == (expected_reference,)


def test_compaction_refuses_to_run_without_remote_recovery_point(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    backup_path = tmp_path / "pre-compaction.sqlite3"
    _create_legacy_database(database_path)

    completed = _run_compaction(
        database_path,
        "--backup-path",
        str(backup_path),
    )

    assert completed.returncode != 0
    assert "--remote-backup-key is required" in completed.stderr


def test_compaction_requires_r2_outside_tests(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    backup_path = tmp_path / "pre-compaction.sqlite3"
    _create_legacy_database(database_path)

    completed = _run_compaction(
        database_path,
        "--backup-only",
        "--backup-path",
        str(backup_path),
        "--remote-backup-key",
        "migration/test.sqlite3",
        allow_local_test_storage=False,
    )

    assert completed.returncode != 0
    assert "Compaction requires storage_mode=r2" in completed.stderr
    assert not backup_path.exists()


def test_compaction_refuses_corrupted_remote_recovery_point(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    backup_path = tmp_path / "pre-compaction.sqlite3"
    payload = _create_legacy_database(database_path)

    backup = _run_compaction(
        database_path,
        "--backup-only",
        "--backup-path",
        str(backup_path),
        "--remote-backup-key",
        "migration/test.sqlite3",
    )
    assert backup.returncode == 0, backup.stderr
    backup_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    remote_backup = (
        tmp_path
        / "object-storage"
        / "backups"
        / "migration"
        / "test.sqlite3"
    )
    remote_backup.write_bytes(b"corrupted")

    completed = _run_compaction(
        database_path,
        "--backup-path",
        str(backup_path),
        "--backup-sha256",
        backup_digest,
        "--remote-backup-key",
        "migration/test.sqlite3",
    )

    assert completed.returncode != 0
    assert "Remote recovery point size mismatch" in completed.stderr
    connection = sqlite3.connect(database_path)
    try:
        stored = connection.execute(
            "SELECT translation_json FROM episode_segment_revisions"
        ).fetchone()
    finally:
        connection.close()
    assert stored == (payload,)
