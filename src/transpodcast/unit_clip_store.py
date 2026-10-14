"""Unit Clip Store for Speech Pipelines V19 and later.

The store keeps each raw IndexTTS unit clip of a Processing Run as lossless
FLAC so assembly and Segment Revision can rebuild Publication Audio from the
original clips and master it once. Clips are content addressed and manifests
are immutable, so a Segment Revision writes a new manifest version instead of
overwriting accepted renders.

Retention follows tiered local storage: per-unit renders are large rebuildable
intermediates, so the store lives under ``intermediates/`` and the seven-day
local cleanup removes it. A Pinned Artifact copy under ``pinned/`` takes
precedence and is never cleaned. Neither location is part of the State Backup.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

UNIT_CLIP_STORE_SCHEMA_VERSION = "unit-clip-store-v1"
_RUN_SCOPE = "processing-runs"
_STORE_NAME = "unit-clips"


def unit_clip_store_root(artifact_root: Path, processing_run_id: int) -> Path:
    """Resolve a run's store, preferring a Pinned Artifact copy."""
    relative = Path(_RUN_SCOPE) / str(processing_run_id) / _STORE_NAME
    pinned = artifact_root / "pinned" / relative
    if pinned.is_dir():
        return pinned
    return artifact_root / "intermediates" / relative


@dataclass(frozen=True)
class UnitClipEntry:
    unit_id: str
    parent_segment_id: str
    source_segment_id: str
    speaker_id: str
    source_start_seconds: float
    source_end_seconds: float
    file: str
    sha256: str
    source_wav_sha256: str
    duration_seconds: float
    tts_identity: str

    def to_payload(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> UnitClipEntry:
        try:
            return cls(
                unit_id=str(value["unit_id"]),
                parent_segment_id=str(value["parent_segment_id"]),
                source_segment_id=str(value["source_segment_id"]),
                speaker_id=str(value["speaker_id"]),
                source_start_seconds=float(value["source_start_seconds"]),
                source_end_seconds=float(value["source_end_seconds"]),
                file=str(value["file"]),
                sha256=str(value["sha256"]),
                source_wav_sha256=str(value["source_wav_sha256"]),
                duration_seconds=float(value["duration_seconds"]),
                tts_identity=str(value["tts_identity"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Unit Clip Store entry is malformed") from error


@dataclass(frozen=True)
class UnitClipManifestReference:
    manifest: str
    sha256: str
    unit_count: int
    schema_version: str = UNIT_CLIP_STORE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_payload(cls, value: object) -> UnitClipManifestReference | None:
        if not isinstance(value, Mapping):
            return None
        try:
            reference = cls(
                manifest=str(value["manifest"]),
                sha256=str(value["sha256"]),
                unit_count=int(value["unit_count"]),
                schema_version=str(value["schema_version"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if reference.schema_version != UNIT_CLIP_STORE_SCHEMA_VERSION:
            return None
        return reference


class UnitClipStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put_clip(
        self,
        wav_path: Path,
        *,
        unit: Mapping[str, Any],
        tts_identity: str,
        duration_seconds: float,
    ) -> UnitClipEntry:
        """Store one raw unit clip losslessly and describe it."""
        wav_sha256 = _file_sha256(wav_path)
        unit_id = str(unit["segment_id"])
        relative = f"clips/{_safe_name(unit_id)}-{wav_sha256[:16]}.flac"
        destination = self._path(relative)
        if destination.is_file():
            os.utime(destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".partial.flac")
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(wav_path),
                    "-map_metadata",
                    "-1",
                    "-fflags",
                    "+bitexact",
                    "-flags:a",
                    "+bitexact",
                    "-c:a",
                    "flac",
                    "-compression_level",
                    "8",
                    str(temporary),
                ],
                check=True,
                capture_output=True,
            )
            temporary.replace(destination)
        return UnitClipEntry(
            unit_id=unit_id,
            parent_segment_id=str(unit["parent_segment_id"]),
            source_segment_id=str(unit["source_segment_id"]),
            speaker_id=str(unit["speaker_id"]),
            source_start_seconds=float(unit["source_start_seconds"]),
            source_end_seconds=float(unit["source_end_seconds"]),
            file=relative,
            sha256=_file_sha256(destination),
            source_wav_sha256=wav_sha256,
            duration_seconds=float(duration_seconds),
            tts_identity=tts_identity,
        )

    def write_manifest(
        self,
        entries: Iterable[UnitClipEntry],
        *,
        pipeline_version: str,
    ) -> UnitClipManifestReference:
        units = [entry.to_payload() for entry in entries]
        payload = json.dumps(
            {
                "schema_version": UNIT_CLIP_STORE_SCHEMA_VERSION,
                "pipeline_version": pipeline_version,
                "units": units,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        relative = f"manifests/{digest}.json"
        destination = self._path(relative)
        if destination.is_file():
            os.utime(destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".partial")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(destination)
        return UnitClipManifestReference(
            manifest=relative,
            sha256=digest,
            unit_count=len(units),
        )

    def load_manifest(
        self,
        reference: UnitClipManifestReference,
    ) -> list[UnitClipEntry] | None:
        """Return the manifest entries, or ``None`` when it is unusable."""
        try:
            path = self._path(reference.manifest)
            payload = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        if hashlib.sha256(payload.encode()).hexdigest() != reference.sha256:
            return None
        try:
            parsed = json.loads(payload)
            if parsed.get("schema_version") != UNIT_CLIP_STORE_SCHEMA_VERSION:
                return None
            entries = [UnitClipEntry.from_payload(item) for item in parsed["units"]]
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        if len(entries) != reference.unit_count:
            return None
        return entries

    def verified_clip(self, entry: UnitClipEntry) -> Path | None:
        """Return the clip path when it still exists with its recorded hash."""
        try:
            path = self._path(entry.file)
        except ValueError:
            return None
        if not path.is_file() or _file_sha256(path) != entry.sha256:
            return None
        return path

    def touch(
        self,
        entries: Iterable[UnitClipEntry],
        reference: UnitClipManifestReference,
    ) -> None:
        """Restart the local retention window after the store is reused."""
        for relative in [*(entry.file for entry in entries), reference.manifest]:
            path = self._path(relative)
            if path.is_file():
                os.utime(path)

    def _path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        root = self.root.resolve()
        if root not in candidate.parents:
            raise ValueError("Unit Clip Store path escapes its root")
        return candidate


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "unit"
