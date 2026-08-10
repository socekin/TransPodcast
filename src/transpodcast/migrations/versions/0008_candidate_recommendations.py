"""Add persisted Candidate Video recommendation presentation.

Revision ID: 0008_candidate_recommendations
Revises: 0007_processing_run_activity
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from transpodcast.models import Base

revision = "0008_candidate_recommendations"
down_revision = "0007_processing_run_activity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("candidate_videos")}
    additions = (
        sa.Column(
            "triage_decision",
            sa.String(32),
            nullable=False,
            server_default="owner_decision",
        ),
        sa.Column(
            "title_zh",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "summary_zh",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "recommendation_zh",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("candidate_videos", column)
    op.execute(
        "UPDATE candidate_videos SET triage_decision = CASE "
        "WHEN status = 'ignored' THEN 'ignore' "
        "WHEN status IN ('processing_queued', 'processed') THEN 'process' "
        "ELSE 'owner_decision' END"
    )
    inspector = inspect(op.get_bind())
    if "telegram_deliveries" not in inspector.get_table_names():
        Base.metadata.tables["telegram_deliveries"].create(bind=op.get_bind())
    op.execute(
        "UPDATE telegram_actions SET expires_at = CURRENT_TIMESTAMP "
        "WHERE candidate_video_id IS NOT NULL AND applied_at IS NULL"
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "telegram_deliveries" in inspector.get_table_names():
        op.drop_table("telegram_deliveries")
    # Retain additive Candidate Video columns for SQLite downgrade safety.
    # Rebuilding this parent table would violate existing child foreign keys;
    # older application versions ignore the extra nullable-compatible fields.
