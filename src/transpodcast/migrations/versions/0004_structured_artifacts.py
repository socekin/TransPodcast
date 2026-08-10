"""Store versioned structured artifacts once by content hash.

Revision ID: 0004_structured_artifacts
Revises: 0003_processing_attempts
Create Date: 2026-07-29
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
from sqlalchemy import inspect, text

from transpodcast.models import Base

revision = "0004_structured_artifacts"
down_revision = "0003_processing_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "structured_artifacts" not in inspector.get_table_names():
        Base.metadata.tables["structured_artifacts"].create(
            bind=op.get_bind()
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table, column in (
        ("episode_segment_revisions", "translation_json"),
        ("canonical_transcript_revisions", "transcript_json"),
    ):
        referenced_payloads = connection.execute(
            text(
                f"""
                SELECT revision.{column}, artifact.payload_json
                FROM {table} AS revision
                LEFT JOIN structured_artifacts AS artifact
                  ON artifact.sha256 = json_extract(
                      revision.{column},
                      '$.artifact_sha256'
                  )
                WHERE json_valid(revision.{column})
                  AND json_extract(
                      revision.{column},
                      '$.artifact_sha256'
                  ) IS NOT NULL
                """
            )
        ).all()
        for reference_json, payload_json in referenced_payloads:
            digest = json.loads(str(reference_json)).get("artifact_sha256")
            if (
                not isinstance(digest, str)
                or payload_json is None
                or hashlib.sha256(str(payload_json).encode()).hexdigest()
                != digest
            ):
                raise RuntimeError(
                    "Cannot downgrade with unresolved or corrupt "
                    "Structured Artifacts"
                )
        connection.execute(
            text(
                f"""
                UPDATE {table} AS revision
                SET {column} = (
                    SELECT artifact.payload_json
                    FROM structured_artifacts AS artifact
                    WHERE artifact.sha256 = json_extract(
                        revision.{column},
                        '$.artifact_sha256'
                    )
                )
                WHERE json_valid(revision.{column})
                  AND json_extract(
                      revision.{column},
                      '$.artifact_sha256'
                  ) IS NOT NULL
                """
            )
        )
    op.drop_table("structured_artifacts")
