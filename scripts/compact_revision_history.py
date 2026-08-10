"""Compact duplicated revision artifacts and reclaim disk space.

Historical segment and transcript revision rows duplicated the full
episode JSON per row. This rewrites them to compact content-hash
references (matching the current write path), checkpoints the WAL, and
VACUUMs the database. Revision history fields (text, speaker, emotion,
versions) are untouched.

Usage: stop both services first, then
  uv run python scripts/compact_revision_history.py \
    --backup-only \
    --backup-path data/backups/pre-compaction.sqlite3 \
    --remote-backup-key "migration/pre-compaction-$(date +%F).sqlite3"
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from sqlalchemy import text

from transpodcast.artifacts import artifact_reference, artifact_sha256
from transpodcast.config import TransPodcastSettings
from transpodcast.database import Database

_REVISION_ARTIFACTS = (
    (
        "episode_segment_revisions",
        "translation_json",
        "translation",
    ),
    (
        "canonical_transcript_revisions",
        "transcript_json",
        "canonical_transcript",
    ),
)


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup-only", action="store_true")
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--backup-sha256")
    parser.add_argument("--remote-backup-key")
    parser.add_argument(
        "--allow-local-test-storage",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--batch-size", type=_positive_integer, default=25)
    arguments = parser.parse_args()
    if arguments.dry_run and arguments.backup_only:
        parser.error("--dry-run and --backup-only are mutually exclusive")
    if not arguments.dry_run and arguments.remote_backup_key is None:
        parser.error("--remote-backup-key is required unless --dry-run is used")
    if (
        not arguments.dry_run
        and not arguments.backup_only
        and arguments.backup_sha256 is None
    ):
        parser.error(
            "--backup-sha256 is required for compaction; "
            "create the recovery point with --backup-only first"
        )
    return arguments


def _database_integrity(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row is not None else ""
    finally:
        connection.close()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_sha256(source: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _validated_remote_key(key: str) -> str:
    candidate = PurePosixPath(key)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError("Remote backup key must be a safe relative path")
    return candidate.as_posix()


def _r2_client(settings: TransPodcastSettings) -> tuple[Any, str]:
    import boto3

    if not all(
        (
            settings.r2_endpoint_url,
            settings.r2_bucket,
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
        )
    ):
        raise ValueError("R2 storage is not fully configured")
    client = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    return client, settings.r2_bucket


def _local_remote_path(settings: TransPodcastSettings, key: str) -> Path:
    backup_root = (settings.local_storage_root / "backups").resolve()
    candidate = (backup_root / key).resolve()
    if backup_root not in candidate.parents:
        raise ValueError("Remote backup key escapes the configured storage root")
    return candidate


def _upload_remote_recovery_point(
    settings: TransPodcastSettings,
    backup_path: Path,
    key: str,
    expected_sha256: str,
) -> None:
    size = backup_path.stat().st_size
    if settings.storage_mode == "local":
        destination = _local_remote_path(settings, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            if (
                destination.stat().st_size != size
                or _file_sha256(destination) != expected_sha256
            ):
                raise ValueError(
                    "Configured storage already contains a different "
                    "recovery point"
                )
        else:
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copyfile(backup_path, temporary)
            temporary.replace(destination)
        return

    client, bucket = _r2_client(settings)
    from botocore.exceptions import ClientError

    object_key = f"backups/{key}"
    try:
        response = client.head_object(Bucket=bucket, Key=object_key)
    except ClientError as error:
        status = error.response.get("ResponseMetadata", {}).get(
            "HTTPStatusCode"
        )
        if status != 404:
            raise
    else:
        remote_sha256 = response.get("Metadata", {}).get("sha256")
        if (
            int(response.get("ContentLength", -1)) != size
            or remote_sha256 != expected_sha256
        ):
            raise ValueError(
                "R2 already contains a different recovery point at this key"
            )
        return
    client.upload_file(
        str(backup_path),
        bucket,
        object_key,
        ExtraArgs={
            "ContentType": "application/vnd.sqlite3",
            "CacheControl": "private, no-store",
            "Metadata": {"sha256": expected_sha256},
        },
    )


def _verify_remote_recovery_point(
    settings: TransPodcastSettings,
    backup_path: Path,
    key: str,
    expected_sha256: str,
) -> None:
    expected_size = backup_path.stat().st_size
    if settings.storage_mode == "local":
        destination = _local_remote_path(settings, key)
        if not destination.is_file():
            raise FileNotFoundError(
                f"Remote recovery point is unavailable: {key}"
            )
        actual_size = destination.stat().st_size
        actual_sha256 = _file_sha256(destination)
    else:
        client, bucket = _r2_client(settings)
        response = client.get_object(
            Bucket=bucket,
            Key=f"backups/{key}",
        )
        actual_size = int(response.get("ContentLength", -1))
        body = response["Body"]
        try:
            actual_sha256 = _stream_sha256(body)
        finally:
            body.close()
    if actual_size != expected_size:
        raise ValueError("Remote recovery point size mismatch")
    if actual_sha256 != expected_sha256:
        raise ValueError("Remote recovery point checksum mismatch")


def _create_verified_backup(
    database: Database,
    database_path: Path,
    backup_path: Path,
) -> str:
    resolved_database = database_path.resolve()
    resolved_backup = backup_path.resolve()
    if resolved_backup == resolved_database:
        raise ValueError("Backup path must differ from the database path")
    if backup_path.exists():
        raise FileExistsError(f"Backup already exists: {backup_path}")
    database.backup_to(backup_path)
    if _database_integrity(backup_path) != "ok":
        raise ValueError("Pre-compaction backup failed integrity check")
    return _file_sha256(backup_path)


def _verify_existing_backup(
    backup_path: Path,
    expected_sha256: str,
) -> str:
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup is unavailable: {backup_path}")
    if _database_integrity(backup_path) != "ok":
        raise ValueError("Pre-compaction backup failed integrity check")
    actual_sha256 = _file_sha256(backup_path)
    if actual_sha256 != expected_sha256:
        raise ValueError("Pre-compaction backup checksum mismatch")
    return actual_sha256


def _dry_run(database: Database, size_before: int) -> None:
    with database.session() as session:
        rows = 0
        payload_bytes = 0
        for table, column, _artifact_kind in _REVISION_ARTIFACTS:
            result = session.execute(
                text(
                    f"SELECT count(*), coalesce(sum(length({column})), 0) "
                    f"FROM {table} WHERE length({column}) > 200"
                )
            ).one()
            rows += int(result[0])
            payload_bytes += int(result[1])
    print("dry run: true")
    print(f"rows to rewrite: {rows}")
    print(f"payload bytes to compact: {payload_bytes}")
    print(f"database: {size_before / 1e6:.1f} MB")


def _compact_table(
    database: Database,
    *,
    table: str,
    column: str,
    artifact_kind: str,
    batch_size: int,
) -> int:
    rewritten = 0
    last_id = 0
    while True:
        with database.session() as session:
            rows = session.execute(
                text(
                    f"SELECT id, {column} FROM {table} "
                    "WHERE id > :last_id "
                    f"AND length({column}) > 200 "
                    "ORDER BY id LIMIT :batch_size"
                ),
                {
                    "last_id": last_id,
                    "batch_size": batch_size,
                },
            ).all()
            if not rows:
                return rewritten
            for row_id, payload_value in rows:
                payload = str(payload_value)
                digest = artifact_sha256(payload)
                session.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO structured_artifacts (
                            sha256,
                            artifact_kind,
                            payload_json,
                            created_at
                        ) VALUES (
                            :sha256,
                            :artifact_kind,
                            :payload_json,
                            :created_at
                        )
                        """
                    ),
                    {
                        "sha256": digest,
                        "artifact_kind": artifact_kind,
                        "payload_json": payload,
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                )
                stored_payload = session.scalar(
                    text(
                        "SELECT payload_json FROM structured_artifacts "
                        "WHERE sha256 = :sha256"
                    ),
                    {"sha256": digest},
                )
                if stored_payload != payload:
                    raise ValueError("Structured Artifact checksum collision")
                session.execute(
                    text(
                        f"UPDATE {table} SET {column} = :value "
                        "WHERE id = :row_id"
                    ),
                    {
                        "value": artifact_reference(payload),
                        "row_id": int(row_id),
                    },
                )
                rewritten += 1
            last_id = int(rows[-1][0])


def _vacuum_and_verify(database: Database, database_path: Path) -> None:
    database.dispose()
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        row = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if row != ("ok",):
        raise ValueError("Compacted database failed integrity check")


def main() -> None:
    arguments = _arguments()
    settings = TransPodcastSettings()
    database = Database(settings.database_path)
    size_before = settings.database_path.stat().st_size
    if arguments.dry_run:
        _dry_run(database, size_before)
        return
    if arguments.backup_path is None:
        raise ValueError("--backup-path is required unless --dry-run is used")
    if (
        settings.storage_mode != "r2"
        and not arguments.allow_local_test_storage
    ):
        raise ValueError(
            "Compaction requires storage_mode=r2 so the recovery point "
            "survives local disk failure"
        )
    remote_backup_key = _validated_remote_key(arguments.remote_backup_key)
    if arguments.backup_path.exists():
        if arguments.backup_sha256 is None:
            raise ValueError(
                "--backup-sha256 is required to reuse an existing backup"
            )
        backup_sha256 = _verify_existing_backup(
            arguments.backup_path,
            arguments.backup_sha256,
        )
    else:
        backup_sha256 = _create_verified_backup(
            database,
            settings.database_path,
            arguments.backup_path,
        )
    print("backup verified: true")
    print(f"backup sha256: {backup_sha256}")
    if arguments.backup_only:
        _upload_remote_recovery_point(
            settings,
            arguments.backup_path,
            remote_backup_key,
            backup_sha256,
        )
        _verify_remote_recovery_point(
            settings,
            arguments.backup_path,
            remote_backup_key,
            backup_sha256,
        )
        print("remote recovery point verified: true")
        return
    if arguments.backup_sha256 != backup_sha256:
        raise ValueError(
            "Provided backup checksum does not match the recovery point"
        )
    _verify_remote_recovery_point(
        settings,
        arguments.backup_path,
        remote_backup_key,
        backup_sha256,
    )
    print("remote recovery point verified: true")
    database.create_schema()
    rewritten = sum(
        _compact_table(
            database,
            table=table,
            column=column,
            artifact_kind=artifact_kind,
            batch_size=arguments.batch_size,
        )
        for table, column, artifact_kind in _REVISION_ARTIFACTS
    )
    _vacuum_and_verify(database, settings.database_path)
    size_after = settings.database_path.stat().st_size
    print(f"rewritten rows: {rewritten}")
    print(f"database: {size_before / 1e6:.1f} MB -> {size_after / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
