"""Add the append-only Processing Run attempt history.

Revision ID: 0003_processing_attempts
Revises: 0002_source_onboarding
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

from transpodcast.models import Base

revision = "0003_processing_attempts"
down_revision = "0002_source_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The baseline migration creates the complete current schema, so a
    # fresh database already contains this table.
    inspector = inspect(op.get_bind())
    if "processing_attempts" not in inspector.get_table_names():
        Base.metadata.tables["processing_attempts"].create(
            bind=op.get_bind()
        )


def downgrade() -> None:
    op.drop_table("processing_attempts")
