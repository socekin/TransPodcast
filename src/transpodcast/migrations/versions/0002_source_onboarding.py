"""Add Source Channel avatars and Candidate Video enrichment state.

Revision ID: 0002_source_onboarding
Revises: 0001_transpodcast_v1
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_source_onboarding"
down_revision = "0001_transpodcast_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The baseline migration creates the complete current schema, so a
    # fresh database already contains these columns.
    inspector = inspect(op.get_bind())
    channel_columns = {
        column["name"]
        for column in inspector.get_columns("source_channels")
    }
    if "avatar_url" not in channel_columns:
        op.add_column(
            "source_channels",
            sa.Column(
                "avatar_url",
                sa.String(2048),
                nullable=False,
                server_default="",
            ),
        )
    candidate_columns = {
        column["name"]
        for column in inspector.get_columns("candidate_videos")
    }
    if "enrichment_state" not in candidate_columns:
        op.add_column(
            "candidate_videos",
            sa.Column(
                "enrichment_state",
                sa.String(32),
                nullable=False,
                server_default="complete",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("candidate_videos") as batch:
        batch.drop_column("enrichment_state")
    with op.batch_alter_table("source_channels") as batch:
        batch.drop_column("avatar_url")
