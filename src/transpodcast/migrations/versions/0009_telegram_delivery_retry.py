"""Add retry scheduling to Telegram deliveries.

Revision ID: 0009_telegram_delivery_retry
Revises: 0008_candidate_recommendations
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009_telegram_delivery_retry"
down_revision = "0008_candidate_recommendations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {
        column["name"]
        for column in inspector.get_columns("telegram_deliveries")
    }
    if "next_retry_at" not in columns:
        op.add_column(
            "telegram_deliveries",
            sa.Column(
                "next_retry_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {
        column["name"]
        for column in inspector.get_columns("telegram_deliveries")
    }
    if "next_retry_at" in columns:
        op.drop_column("telegram_deliveries", "next_retry_at")
