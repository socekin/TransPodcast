"""Store display names on channel-scoped Speaker profiles.

Revision ID: 0006_channel_profile_display_name
Revises: 0005_channel_voice_library
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006_channel_profile_display_name"
down_revision = "0005_channel_voice_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(
            "channel_speaker_profiles"
        )
    }
    if "display_name" in columns:
        return
    op.add_column(
        "channel_speaker_profiles",
        sa.Column(
            "display_name",
            sa.String(length=512),
            nullable=False,
            server_default="",
        ),
    )
    op.execute(
        """
        UPDATE channel_speaker_profiles
        SET display_name = (
            SELECT speakers.display_name
            FROM speakers
            WHERE speakers.id = channel_speaker_profiles.speaker_id
        )
        """
    )
    with op.batch_alter_table("channel_speaker_profiles") as batch:
        batch.alter_column(
            "display_name",
            existing_type=sa.String(length=512),
            server_default=None,
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(
            "channel_speaker_profiles"
        )
    }
    if "display_name" not in columns:
        return
    with op.batch_alter_table("channel_speaker_profiles") as batch:
        batch.drop_column("display_name")
