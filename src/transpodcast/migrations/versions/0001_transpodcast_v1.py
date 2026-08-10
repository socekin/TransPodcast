"""Create the TransPodcast v1 schema.

Revision ID: 0001_transpodcast_v1
Revises:
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op

from transpodcast.models import Base

revision = "0001_transpodcast_v1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
