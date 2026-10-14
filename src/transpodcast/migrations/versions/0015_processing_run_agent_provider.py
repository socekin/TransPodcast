"""Freeze the selected Agent provider on each Processing Run.

Historical rows stay NULL, which means Codex.

Revision ID: 0015_processing_run_agent_provider
Revises: 0014_owner_console_publication_decisions
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0015_processing_run_agent_provider"
down_revision = "0014_owner_console_publication_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("processing_runs")
    }
    if "agent_provider" not in columns:
        op.add_column(
            "processing_runs",
            sa.Column("agent_provider", sa.String(length=32), nullable=True),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("processing_runs")
    }
    if "agent_provider" in columns:
        op.drop_column("processing_runs", "agent_provider")
