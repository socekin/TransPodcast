"""Add owner-facing append-only Processing Run Activity.

Revision ID: 0007_processing_run_activity
Revises: 0006_channel_profile_display_name
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

from transpodcast.models import Base

revision = "0007_processing_run_activity"
down_revision = "0006_channel_profile_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "processing_run_activity" not in inspector.get_table_names():
        Base.metadata.tables["processing_run_activity"].create(
            bind=op.get_bind()
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "processing_run_activity" in inspector.get_table_names():
        op.drop_table("processing_run_activity")
