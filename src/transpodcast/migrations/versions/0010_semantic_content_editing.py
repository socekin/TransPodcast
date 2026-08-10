"""Store semantic Content Edit Artifacts separately from translations.

Revision ID: 0010_semantic_content_editing
Revises: 0009_telegram_delivery_retry
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0010_semantic_content_editing"
down_revision = "0009_telegram_delivery_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {
        column["name"]
        for column in inspector.get_columns("processing_runs")
    }
    if "content_edit_json" not in columns:
        op.add_column(
            "processing_runs",
            sa.Column("content_edit_json", sa.Text(), nullable=True),
        )
    if not inspector.has_table("episode_content_edit_revisions"):
        op.create_table(
            "episode_content_edit_revisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=False),
            sa.Column("processing_run_id", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("content_edit_json", sa.Text(), nullable=False),
            sa.Column("artifact_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
            sa.ForeignKeyConstraint(
                ["processing_run_id"],
                ["processing_runs.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "episode_id",
                "version",
                name="uq_episode_content_edit_revision",
            ),
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table("episode_content_edit_revisions"):
        op.drop_table("episode_content_edit_revisions")
    columns = {
        column["name"]
        for column in inspector.get_columns("processing_runs")
    }
    if "content_edit_json" in columns:
        op.drop_column("processing_runs", "content_edit_json")
