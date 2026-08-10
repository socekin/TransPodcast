"""Add the immutable published Episode Artwork audit snapshot.

Revision ID: 0012_published_artwork_snapshot
Revises: 0011_episode_publication_presentation
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0012_published_artwork_snapshot"
down_revision = "0011_episode_publication_presentation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("episodes")
    }
    if "published_artwork_json" not in columns:
        op.add_column(
            "episodes",
            sa.Column("published_artwork_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("episodes")
    }
    if "published_artwork_json" in columns:
        op.drop_column("episodes", "published_artwork_json")
