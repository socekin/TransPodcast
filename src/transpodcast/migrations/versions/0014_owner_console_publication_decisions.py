"""Add channel-neutral publication decision audit records.

Revision ID: 0014_owner_console_publication_decisions
Revises: 0013_processing_execution_observability
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0014_owner_console_publication_decisions"
down_revision = "0013_processing_execution_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("publication_decisions"):
        op.create_table(
            "publication_decisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("decision_key", sa.String(length=64), nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=False),
            sa.Column("decision", sa.String(length=32), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False),
            sa.Column(
                "owner_identity",
                sa.String(length=64),
                nullable=False,
                server_default="owner",
            ),
            sa.Column("previous_state", sa.String(length=64), nullable=False),
            sa.Column("resulting_state", sa.String(length=64), nullable=False),
            sa.Column("prior_publication_version", sa.Integer(), nullable=False),
            sa.Column("publication_version", sa.Integer(), nullable=False),
            sa.Column("revision_version", sa.Integer(), nullable=False),
            sa.Column("artifact_json", sa.Text(), nullable=False),
            sa.Column("quality_json", sa.Text(), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "decision_key",
                name="uq_publication_decision_key",
            ),
            sa.UniqueConstraint(
                "episode_id",
                "previous_state",
                "prior_publication_version",
                "revision_version",
                name="uq_publication_decision_opportunity",
            ),
        )
    if not inspector.has_table("publication_notifications"):
        op.create_table(
            "publication_notifications",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=False),
            sa.Column("revision_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("message_id", sa.String(length=128), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column(
                "next_retry_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column("resolution", sa.String(length=32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "episode_id",
                "revision_version",
                name="uq_publication_notification_episode_revision",
            ),
        )


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("publication_notifications"):
        op.drop_table("publication_notifications")
    if inspect(op.get_bind()).has_table("publication_decisions"):
        op.drop_table("publication_decisions")
