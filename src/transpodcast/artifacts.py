from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.orm import Session

from transpodcast.models import StructuredArtifactRecord


def artifact_sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def artifact_reference(payload: str) -> str:
    return json.dumps({"artifact_sha256": artifact_sha256(payload)})


def persist_structured_artifact(
    session: Session,
    *,
    artifact_kind: str,
    payload: str,
    created_at: datetime,
) -> str:
    digest = artifact_sha256(payload)
    existing = session.get(StructuredArtifactRecord, digest)
    if existing is None:
        session.add(
            StructuredArtifactRecord(
                sha256=digest,
                artifact_kind=artifact_kind,
                payload_json=payload,
                created_at=created_at,
            )
        )
    elif existing.payload_json != payload:
        raise ValueError("Structured Artifact hash collision")
    return artifact_reference(payload)


def resolve_structured_artifact(
    session: Session,
    reference_json: str,
) -> str:
    reference = json.loads(reference_json)
    digest = str(reference["artifact_sha256"])
    artifact = session.get(StructuredArtifactRecord, digest)
    if artifact is None:
        raise LookupError("Structured Artifact is unavailable")
    if artifact_sha256(artifact.payload_json) != digest:
        raise ValueError("Structured Artifact checksum mismatch")
    return artifact.payload_json
