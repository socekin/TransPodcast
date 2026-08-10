"""Add channel-scoped voice maintenance records.

Revision ID: 0005_channel_voice_library
Revises: 0004_structured_artifacts
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "0005_channel_voice_library"
down_revision = "0004_structured_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = inspector.get_table_names()
    for table_name in (
        "channel_speaker_profiles",
        "channel_voice_candidates",
    ):
        if table_name in tables:
            continue
        if table_name == "channel_speaker_profiles":
            op.create_table(
                table_name,
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "source_channel_id",
                    sa.Integer(),
                    sa.ForeignKey("source_channels.id"),
                    nullable=False,
                ),
                sa.Column(
                    "speaker_id",
                    sa.Integer(),
                    sa.ForeignKey("speakers.id"),
                    nullable=False,
                ),
                sa.Column(
                    "state",
                    sa.String(length=32),
                    nullable=False,
                ),
                sa.Column(
                    "created_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                ),
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                ),
                sa.UniqueConstraint(
                    "source_channel_id",
                    "speaker_id",
                    name="uq_channel_speaker_profile",
                ),
            )
        else:
            op.create_table(
                table_name,
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "source_channel_id",
                    sa.Integer(),
                    sa.ForeignKey("source_channels.id"),
                    nullable=False,
                ),
                sa.Column(
                    "source_episode_id",
                    sa.Integer(),
                    sa.ForeignKey("episodes.id"),
                    nullable=False,
                ),
                sa.Column(
                    "speaker_id",
                    sa.Integer(),
                    sa.ForeignKey("speakers.id"),
                    nullable=False,
                ),
                sa.Column(
                    "voice_reference_id",
                    sa.Integer(),
                    sa.ForeignKey("speaker_voice_references.id"),
                    nullable=False,
                ),
                sa.Column(
                    "local_label",
                    sa.String(length=128),
                    nullable=False,
                ),
                sa.Column(
                    "segment_count",
                    sa.Integer(),
                    nullable=False,
                ),
                sa.Column(
                    "decision",
                    sa.String(length=32),
                    nullable=False,
                ),
                sa.Column(
                    "created_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                ),
                sa.Column(
                    "decided_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
                sa.UniqueConstraint(
                    "source_episode_id",
                    "speaker_id",
                    name="uq_channel_voice_candidate_episode_speaker",
                ),
            )
    op.get_bind().execute(
        text(
            """
            INSERT INTO channel_voice_candidates (
                source_channel_id,
                source_episode_id,
                speaker_id,
                voice_reference_id,
                local_label,
                segment_count,
                decision,
                created_at,
                decided_at
            )
            SELECT
                candidate.source_channel_id,
                episode.id,
                speaker.id,
                (
                    SELECT reference.id
                    FROM speaker_voice_references AS reference
                    WHERE reference.speaker_id = speaker.id
                    ORDER BY
                        CASE reference.status
                            WHEN 'proposed' THEN 0
                            WHEN 'approved' THEN 1
                            ELSE 2
                        END,
                        reference.created_at DESC
                    LIMIT 1
                ),
                CASE
                    WHEN instr(speaker.display_name, ' · ') > 0
                    THEN substr(
                        speaker.display_name,
                        1,
                        instr(speaker.display_name, ' · ') - 1
                    )
                    ELSE speaker.display_name
                END,
                count(segment.id),
                'pending',
                speaker.created_at,
                NULL
            FROM episode_segments AS segment
            JOIN episodes AS episode
              ON episode.id = segment.episode_id
            JOIN candidate_videos AS candidate
              ON candidate.id = episode.candidate_video_id
            JOIN speakers AS speaker
              ON speaker.id = segment.speaker_id
            WHERE speaker.status = 'provisional'
              AND EXISTS (
                  SELECT 1
                  FROM speaker_voice_references AS available_reference
                  WHERE available_reference.speaker_id = speaker.id
              )
            GROUP BY
                candidate.source_channel_id,
                episode.id,
                speaker.id,
                speaker.display_name,
                speaker.created_at
            ON CONFLICT(source_episode_id, speaker_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = inspector.get_table_names()
    for table_name in (
        "channel_voice_candidates",
        "channel_speaker_profiles",
    ):
        if table_name in tables:
            op.drop_table(table_name)
