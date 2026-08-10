from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select

from transpodcast.dependencies import AppDependencies
from transpodcast.models import SpeakerRecord


class MaintenanceManager:
    def __init__(self, dependencies: AppDependencies) -> None:
        self.dependencies = dependencies

    def run(self, safe_configuration: dict[str, object]) -> dict[str, object]:
        deleted = self._cleanup_intermediates(retention_days=7)
        deleted_episode_artwork = self._cleanup_episode_artwork(
            retention_days=7
        )
        deleted_logs = self._cleanup_logs(
            retention_days=7,
            maximum_bytes=500 * 1024 * 1024,
        )
        deleted_translation_project_files = (
            self._cleanup_translation_projects(retention_days=7)
        )
        backup = self._create_state_backup(safe_configuration)
        self._prune_backups("daily/", retain=30)
        self._prune_backups("monthly/", retain=12)
        self._prune_migration_backups(retention_days=7)
        return {
            "deleted_intermediates": deleted,
            "deleted_episode_artwork": deleted_episode_artwork,
            "deleted_logs": deleted_logs,
            "deleted_translation_project_files": (
                deleted_translation_project_files
            ),
            "backup": backup,
            "storage_usage": self.storage_usage(),
        }

    def storage_usage(self) -> dict[str, int]:
        root = self.dependencies.artifact_root
        return {
            "intermediates": _tree_size(root / "intermediates"),
            "structured": _tree_size(root / "structured"),
            "baselines": _tree_size(root / "baselines"),
            "pinned": _tree_size(root / "pinned"),
            "translation_projects": _tree_size(
                root / "translation-projects"
            ),
            "episode_artwork": _tree_size(root / "episode-artwork"),
            **self.dependencies.storage.usage_by_class(),
        }

    def _cleanup_intermediates(self, retention_days: int) -> int:
        return self._cleanup_rebuildable_files(
            self.dependencies.artifact_root / "intermediates",
            retention_days,
        )

    def _cleanup_episode_artwork(self, retention_days: int) -> int:
        return self._cleanup_rebuildable_files(
            self.dependencies.artifact_root / "episode-artwork",
            retention_days,
        )

    def _cleanup_rebuildable_files(
        self,
        root: Path,
        retention_days: int,
    ) -> int:
        cutoff = (
            self.dependencies.clock.now().timestamp()
            - retention_days * 24 * 60 * 60
        )
        deleted = 0
        if not root.exists():
            return deleted
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        return deleted

    def _cleanup_logs(
        self,
        *,
        retention_days: int,
        maximum_bytes: int,
    ) -> int:
        root = self.dependencies.artifact_root.parent / "logs"
        if not root.exists():
            return 0
        cutoff = (
            self.dependencies.clock.now().timestamp()
            - retention_days * 24 * 60 * 60
        )
        files = [path for path in root.rglob("*") if path.is_file()]
        deleted = 0
        for path in files:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        remaining = sorted(
            (path for path in files if path.exists()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        total = sum(path.stat().st_size for path in remaining)
        for path in reversed(remaining):
            if total <= maximum_bytes:
                break
            total -= path.stat().st_size
            path.unlink()
            deleted += 1
        return deleted

    def _cleanup_translation_projects(self, retention_days: int) -> int:
        root = self.dependencies.artifact_root / "translation-projects"
        cutoff = (
            self.dependencies.clock.now().timestamp()
            - retention_days * 24 * 60 * 60
        )
        deleted = 0
        for state_path in root.glob("run-*/*/project.json"):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                state.get("phase") != "completed"
                or state_path.stat().st_mtime >= cutoff
            ):
                continue
            for name in ("author", "review", "events"):
                working_root = state_path.parent / name
                if not working_root.is_dir():
                    continue
                deleted += sum(
                    path.is_file() for path in working_root.rglob("*")
                )
                shutil.rmtree(working_root)
        return deleted

    def _create_state_backup(
        self,
        safe_configuration: dict[str, object],
    ) -> dict[str, object]:
        now = self.dependencies.clock.now()
        with TemporaryDirectory(prefix="transpodcast-backup-") as temp:
            database_path = Path(temp) / "state.sqlite3"
            self.dependencies.database.backup_to(database_path)
            files: dict[str, bytes] = {
                "state.sqlite3": database_path.read_bytes(),
                "configuration.json": json.dumps(
                    safe_configuration,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode(),
                "speakers.json": self._speaker_registry(),
            }
            structured = self.dependencies.artifact_root / "structured"
            if structured.exists():
                for path in structured.rglob("*"):
                    if path.is_file():
                        relative = path.relative_to(structured)
                        files[f"structured/{relative}"] = path.read_bytes()
            translation_projects = (
                self.dependencies.artifact_root / "translation-projects"
            )
            for state_path in translation_projects.glob(
                "run-*/*/project.json"
            ):
                try:
                    state = json.loads(
                        state_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    continue
                project_root = state_path.parent
                candidates = [state_path]
                if state.get("phase") != "completed":
                    candidates.extend(
                        path
                        for path in project_root.rglob("*")
                        if path.is_file() and "events" not in path.parts
                    )
                for path in candidates:
                    relative = path.relative_to(translation_projects)
                    files[f"translation-projects/{relative}"] = (
                        path.read_bytes()
                    )
            manifest = {
                "schema_version": 1,
                "created_at": now.isoformat(),
                "files": {
                    name: {
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "bytes": len(content),
                    }
                    for name, content in sorted(files.items())
                },
                "excluded": [
                    "credentials",
                    "authentication material",
                    "feed token",
                ],
            }
            files["manifest.json"] = json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            archive = _tar_gzip(files)
        if not self._verify_backup(archive):
            raise ValueError("State Backup failed restore verification")
        key = f"daily/{now.date().isoformat()}.tar.gz"
        self.dependencies.storage.put_backup(key, archive)
        stored = self.dependencies.storage.read_backup(key)
        if stored is None or not self._verify_backup(stored):
            raise ValueError("Stored State Backup failed restore verification")
        monthly = f"monthly/{now:%Y-%m}.tar.gz"
        if monthly not in self.dependencies.storage.backup_paths():
            self.dependencies.storage.put_backup(monthly, archive)
            monthly_stored = self.dependencies.storage.read_backup(monthly)
            if monthly_stored is None or not self._verify_backup(
                monthly_stored
            ):
                raise ValueError(
                    "Stored monthly State Backup failed restore verification"
                )
        return {
            "key": key,
            "bytes": len(archive),
            "verified": True,
        }

    def _speaker_registry(self) -> bytes:
        with self.dependencies.database.session() as session:
            speakers = session.scalars(
                select(SpeakerRecord).order_by(SpeakerRecord.id)
            ).all()
            payload = [
                {
                    "id": speaker.id,
                    "display_name": speaker.display_name,
                    "canonical_voice_reference": (
                        speaker.canonical_voice_reference
                    ),
                    "verified": speaker.verified,
                    "created_at": speaker.created_at.isoformat(),
                }
                for speaker in speakers
            ]
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def _verify_backup(self, archive: bytes) -> bool:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            members: dict[str, bytes] = {}
            for member in bundle.getmembers():
                if not member.isfile():
                    continue
                extracted = bundle.extractfile(member)
                if extracted is not None:
                    members[member.name] = extracted.read()
        manifest = json.loads(members["manifest.json"])
        for name, metadata in manifest["files"].items():
            content = members.get(name)
            if content is None:
                return False
            if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
                return False
        configuration = json.loads(members["configuration.json"])
        speakers = json.loads(members["speakers.json"])
        if not isinstance(configuration, dict) or not isinstance(
            speakers,
            list,
        ):
            return False
        with TemporaryDirectory(prefix="transpodcast-restore-check-") as temp:
            database_path = Path(temp) / "state.sqlite3"
            database_path.write_bytes(members["state.sqlite3"])
            connection = sqlite3.connect(database_path)
            try:
                row = connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()
                integrity = str(row[0]) if row is not None else ""
                if integrity == "ok":
                    _verify_revision_artifacts(connection)
            finally:
                connection.close()
        return integrity == "ok"

    def _prune_backups(self, prefix: str, retain: int) -> None:
        keys = sorted(
            (
                key
                for key in self.dependencies.storage.backup_paths()
                if key.startswith(prefix)
            ),
            reverse=True,
        )
        for key in keys[retain:]:
            self.dependencies.storage.delete_backup(key)

    def _prune_migration_backups(self, retention_days: int) -> None:
        prefix = "migration/pre-compaction-"
        suffix = ".sqlite3"
        cutoff = self.dependencies.clock.now().date() - timedelta(
            days=retention_days
        )
        for key in self.dependencies.storage.backup_paths():
            if not key.startswith(prefix) or not key.endswith(suffix):
                continue
            date_value = key[len(prefix) : -len(suffix)]
            try:
                created_on = date.fromisoformat(date_value)
            except ValueError:
                continue
            if created_on < cutoff:
                self.dependencies.storage.delete_backup(key)


def _tar_gzip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o600
            bundle.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _tree_size(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _verify_revision_artifacts(connection: sqlite3.Connection) -> None:
    table_names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    revision_columns = (
        ("episode_segment_revisions", "translation_json"),
        ("canonical_transcript_revisions", "transcript_json"),
    )
    referenced_digests: set[str] = set()
    for table, column in revision_columns:
        if table not in table_names:
            continue
        rows = connection.execute(f"SELECT DISTINCT {column} FROM {table}")
        for row in rows:
            raw_value = str(row[0])
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError as error:
                raise ValueError(
                    "State Backup contains malformed revision artifact JSON"
                ) from error
            if not isinstance(parsed, dict):
                raise ValueError(
                    "State Backup contains a malformed legacy revision payload"
                )
            if "artifact_sha256" not in parsed:
                if not isinstance(parsed.get("segments"), list):
                    raise ValueError(
                        "State Backup contains a malformed legacy revision "
                        "payload"
                    )
                continue
            digest = parsed["artifact_sha256"]
            if (
                set(parsed) != {"artifact_sha256"}
                or
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(
                    "State Backup contains a malformed revision artifact "
                    "reference"
                )
            referenced_digests.add(digest)
    if not referenced_digests:
        return
    if "structured_artifacts" not in table_names:
        raise ValueError(
            "State Backup contains an unresolved revision artifact reference"
        )
    for digest in referenced_digests:
        row = connection.execute(
            """
            SELECT payload_json
            FROM structured_artifacts
            WHERE sha256 = ?
            """,
            (digest,),
        ).fetchone()
        if row is None:
            raise ValueError(
                "State Backup contains an unresolved revision artifact "
                "reference"
            )
        payload = str(row[0])
        if hashlib.sha256(payload.encode()).hexdigest() != digest:
            raise ValueError(
                "State Backup contains a corrupt Structured Artifact"
            )
