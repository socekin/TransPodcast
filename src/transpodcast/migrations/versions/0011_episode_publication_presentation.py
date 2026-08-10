"""Add Episode Artwork and rich publication metadata.

Revision ID: 0011_episode_publication_presentation
Revises: 0010_semantic_content_editing
Create Date: 2026-08-02
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0011_episode_publication_presentation"
down_revision = "0010_semantic_content_editing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_columns(
        "processing_runs",
        (
            sa.Column("publication_metadata_json", sa.Text(), nullable=True),
            sa.Column(
                "artwork_status",
                sa.String(32),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("artwork_path", sa.String(2048), nullable=True),
            sa.Column("artwork_json", sa.Text(), nullable=True),
        ),
    )
    _add_columns(
        "episodes",
        (
            sa.Column(
                "publication_description",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "chapter_proposals_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column("artwork_path", sa.String(2048), nullable=True),
            sa.Column("artwork_json", sa.Text(), nullable=True),
            sa.Column(
                "publication_artwork_key",
                sa.String(1024),
                nullable=True,
            ),
            sa.Column(
                "published_publication_description",
                sa.Text(),
                nullable=True,
            ),
            sa.Column(
                "revision_kind",
                sa.String(32),
                nullable=False,
                server_default="audio",
            ),
        ),
    )
    _add_columns(
        "episode_metadata_revisions",
        (
            sa.Column(
                "publication_description",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "chapter_proposals_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column("artwork_json", sa.Text(), nullable=True),
        ),
    )


def downgrade() -> None:
    for table, names in (
        (
            "episode_metadata_revisions",
            ("artwork_json", "chapter_proposals_json", "publication_description"),
        ),
        (
            "episodes",
            (
                "revision_kind",
                "published_publication_description",
                "publication_artwork_key",
                "artwork_json",
                "artwork_path",
                "chapter_proposals_json",
                "publication_description",
            ),
        ),
        (
            "processing_runs",
            (
                "artwork_json",
                "artwork_path",
                "artwork_status",
                "publication_metadata_json",
            ),
        ),
    ):
        columns = {
            column["name"]
            for column in inspect(op.get_bind()).get_columns(table)
        }
        for name in names:
            if name in columns:
                op.drop_column(table, name)


def _add_columns(table: str, additions: tuple[sa.Column[Any], ...]) -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(table)
    }
    for column in additions:
        if column.name not in columns:
            op.add_column(table, column)
