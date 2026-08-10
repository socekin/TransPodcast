"""Add durable Processing Stage attempt and routing evidence.

Revision ID: 0013_processing_execution_observability
Revises: 0012_published_artwork_snapshot
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0013_processing_execution_observability"
down_revision = "0012_published_artwork_snapshot"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    run_columns = _columns("processing_runs")
    if "first_claimed_at" not in run_columns:
        op.add_column(
            "processing_runs",
            sa.Column(
                "first_claimed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.execute(
            "UPDATE processing_runs SET first_claimed_at = started_at "
            "WHERE status NOT IN ('queued', 'pending_start')"
        )

    attempt_columns = _columns("processing_attempts")
    with op.batch_alter_table("processing_attempts") as batch:
        if "completed_at" not in attempt_columns:
            batch.add_column(
                sa.Column(
                    "completed_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )
        if "correlation_id" not in attempt_columns:
            batch.add_column(
                sa.Column(
                    "correlation_id",
                    sa.String(length=64),
                    nullable=False,
                    server_default="",
                )
            )
        if "routing_json" not in attempt_columns:
            batch.add_column(
                sa.Column(
                    "routing_json",
                    sa.Text(),
                    nullable=False,
                    server_default="{}",
                )
            )
        if "supersedes_attempt_id" not in attempt_columns:
            batch.add_column(
                sa.Column(
                    "supersedes_attempt_id",
                    sa.Integer(),
                    nullable=True,
                )
            )
            batch.create_foreign_key(
                "fk_processing_attempts_supersedes_attempt_id",
                "processing_attempts",
                ["supersedes_attempt_id"],
                ["id"],
            )
        if "timing_provenance" not in attempt_columns:
            batch.add_column(
                sa.Column(
                    "timing_provenance",
                    sa.String(length=32),
                    nullable=False,
                    server_default="legacy",
                )
            )
    if "completed_at" not in attempt_columns:
        op.execute("UPDATE processing_attempts SET completed_at = created_at")

    activity_columns = _columns("processing_run_activity")
    if "attempt_id" not in activity_columns:
        with op.batch_alter_table("processing_run_activity") as batch:
            batch.add_column(
                sa.Column("attempt_id", sa.Integer(), nullable=True),
            )
            batch.create_foreign_key(
                "fk_processing_run_activity_attempt_id",
                "processing_attempts",
                ["attempt_id"],
                ["id"],
            )


def downgrade() -> None:
    activity_columns = _columns("processing_run_activity")
    if "attempt_id" in activity_columns:
        with op.batch_alter_table("processing_run_activity") as batch:
            batch.drop_column("attempt_id")

    attempt_columns = _columns("processing_attempts")
    with op.batch_alter_table("processing_attempts") as batch:
        for name in (
            "timing_provenance",
            "supersedes_attempt_id",
            "routing_json",
            "correlation_id",
            "completed_at",
        ):
            if name in attempt_columns:
                batch.drop_column(name)

    run_columns = _columns("processing_runs")
    if "first_claimed_at" in run_columns:
        op.drop_column("processing_runs", "first_claimed_at")
