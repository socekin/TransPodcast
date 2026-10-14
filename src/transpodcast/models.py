from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceChannelRecord(Base):
    __tablename__ = "source_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    mode: Mapped[str] = mapped_column(String(32))
    avatar_url: Mapped[str] = mapped_column(
        String(2048),
        default="",
        server_default="",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InterestProfileRevisionRecord(Base):
    __tablename__ = "interest_profile_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    markdown: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str] = mapped_column(String(32), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SettingRecord(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)


class StructuredArtifactRecord(Base):
    __tablename__ = "structured_artifacts"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_kind: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CandidateVideoRecord(Base):
    __tablename__ = "candidate_videos"
    __table_args__ = (
        UniqueConstraint(
            "youtube_video_id",
            name="uq_candidate_video_youtube_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_channel_id: Mapped[int] = mapped_column(
        ForeignKey("source_channels.id"),
    )
    youtube_video_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(32))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(String(2048))
    captions: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64))
    triage_decision: Mapped[str] = mapped_column(
        String(32),
        default="owner_decision",
        server_default="owner_decision",
    )
    triage_confidence: Mapped[float] = mapped_column(Float)
    triage_rationale: Mapped[str] = mapped_column(Text)
    matched_interests_json: Mapped[str] = mapped_column(Text)
    title_zh: Mapped[str] = mapped_column(Text, default="", server_default="")
    summary_zh: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
    )
    recommendation_zh: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
    )
    enrichment_state: Mapped[str] = mapped_column(
        String(32),
        default="complete",
        server_default="complete",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProcessingRunRecord(Base):
    __tablename__ = "processing_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_video_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_videos.id"),
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=30)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    pipeline_version: Mapped[str] = mapped_column(String(128))
    # The Agent provider frozen when the run was queued or explicitly
    # reprocessed; NULL on historical rows, which mean Codex.
    agent_provider: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    translation_provider: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    translation_model: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    translation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_edit_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_metadata_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    artwork_status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default="pending",
    )
    artwork_path: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    artwork_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_archive_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    source_archive_checksum: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    render_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    render_preview_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessingAttemptRecord(Base):
    """One append-only execution attempt of a Processing Run stage.

    Failed attempts are never overwritten, so the durable history keeps
    the wall-clock, transfer, and error evidence that Operational Logs
    lose after seven days.
    """

    __tablename__ = "processing_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    processing_run_id: Mapped[int] = mapped_column(ForeignKey("processing_runs.id"))
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    transfer_bytes: Mapped[int] = mapped_column(Integer, default=0)
    resume_checkpoint: Mapped[str] = mapped_column(
        String(64),
        default="ingest",
    )
    error_kind: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    correlation_id: Mapped[str] = mapped_column(
        String(64),
        default="",
        server_default="",
    )
    routing_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        server_default="{}",
    )
    supersedes_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_attempts.id"),
        nullable=True,
    )
    timing_provenance: Mapped[str] = mapped_column(
        String(32),
        default="recorded",
        server_default="recorded",
    )


class ProcessingRunActivityRecord(Base):
    """One owner-facing append-only Processing Run lifecycle event."""

    __tablename__ = "processing_run_activity"

    id: Mapped[int] = mapped_column(primary_key=True)
    processing_run_id: Mapped[int] = mapped_column(ForeignKey("processing_runs.id"))
    kind: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_attempts.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProcessingStageRecord(Base):
    __tablename__ = "processing_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    processing_run_id: Mapped[int] = mapped_column(ForeignKey("processing_runs.id"))
    stage: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(256))
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str] = mapped_column(String(64))
    usage_json: Mapped[str] = mapped_column(Text, default="{}")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LabeledExampleRecord(Base):
    __tablename__ = "labeled_examples"
    __table_args__ = (
        UniqueConstraint(
            "candidate_video_id",
            name="uq_labeled_example_candidate",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_video_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_videos.id"),
    )
    label: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SpeakerRecord(Base):
    __tablename__ = "speakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(512))
    canonical_voice_reference: Mapped[str] = mapped_column(String(2048))
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    verified: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(32), default="provisional")
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("speakers.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SpeakerVoiceReferenceRecord(Base):
    __tablename__ = "speaker_voice_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    speaker_id: Mapped[int] = mapped_column(ForeignKey("speakers.id"))
    artifact_key: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ChannelSpeakerProfileRecord(Base):
    __tablename__ = "channel_speaker_profiles"
    __table_args__ = (
        UniqueConstraint(
            "source_channel_id",
            "speaker_id",
            name="uq_channel_speaker_profile",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_channel_id: Mapped[int] = mapped_column(
        ForeignKey("source_channels.id"),
    )
    speaker_id: Mapped[int] = mapped_column(ForeignKey("speakers.id"))
    display_name: Mapped[str] = mapped_column(String(512))
    state: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodeRecord(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_video_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_videos.id"),
        unique=True,
    )
    processing_run_id: Mapped[int] = mapped_column(
        ForeignKey("processing_runs.id"),
        unique=True,
    )
    title: Mapped[str] = mapped_column(String(1024))
    overview: Mapped[str] = mapped_column(Text)
    publication_description: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
    )
    chapter_proposals_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
        server_default="[]",
    )
    state: Mapped[str] = mapped_column(String(64))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    target_locale: Mapped[str] = mapped_column(String(32))
    source_show: Mapped[str] = mapped_column(String(512))
    source_title: Mapped[str] = mapped_column(String(1024))
    source_url: Mapped[str] = mapped_column(String(2048))
    source_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    guests_json: Mapped[str] = mapped_column(Text, default="[]")
    transcript_json: Mapped[str] = mapped_column(Text)
    chapters_json: Mapped[str] = mapped_column(Text)
    quality_json: Mapped[str] = mapped_column(Text)
    preview_key: Mapped[str] = mapped_column(String(1024))
    preview_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    publication_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    publication_guid: Mapped[str] = mapped_column(String(64), unique=True)
    publication_version: Mapped[int] = mapped_column(Integer, default=0)
    publication_bytes: Mapped[int] = mapped_column(Integer, default=0)
    publication_chapters_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    artwork_path: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    artwork_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_artwork_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    published_artwork_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    published_title: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    published_overview: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    published_publication_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    published_duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    published_chapters_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    revision_version: Mapped[int] = mapped_column(Integer, default=1)
    revision_kind: Mapped[str] = mapped_column(
        String(32),
        default="audio",
        server_default="audio",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    recovery_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ChannelVoiceCandidateRecord(Base):
    __tablename__ = "channel_voice_candidates"
    __table_args__ = (
        UniqueConstraint(
            "source_episode_id",
            "speaker_id",
            name="uq_channel_voice_candidate_episode_speaker",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_channel_id: Mapped[int] = mapped_column(
        ForeignKey("source_channels.id"),
    )
    source_episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id"),
    )
    speaker_id: Mapped[int] = mapped_column(ForeignKey("speakers.id"))
    voice_reference_id: Mapped[int] = mapped_column(
        ForeignKey("speaker_voice_references.id"),
    )
    local_label: Mapped[str] = mapped_column(String(128))
    segment_count: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class EpisodeSegmentRecord(Base):
    __tablename__ = "episode_segments"
    __table_args__ = (
        UniqueConstraint(
            "episode_id",
            "ordinal",
            name="uq_episode_segment_ordinal",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    source_text: Mapped[str] = mapped_column(Text)
    target_text: Mapped[str] = mapped_column(Text)
    speaker_id: Mapped[int | None] = mapped_column(
        ForeignKey("speakers.id"),
        nullable=True,
    )
    emotion_reference: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodeSegmentRevisionRecord(Base):
    __tablename__ = "episode_segment_revisions"
    __table_args__ = (
        UniqueConstraint(
            "episode_id",
            "ordinal",
            "version",
            name="uq_episode_segment_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    target_text: Mapped[str] = mapped_column(Text)
    speaker_id: Mapped[int | None] = mapped_column(
        ForeignKey("speakers.id"),
        nullable=True,
    )
    emotion_reference: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    preview_key: Mapped[str] = mapped_column(String(1024))
    translation_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SegmentRevisionJobRecord(Base):
    __tablename__ = "segment_revision_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    target_text: Mapped[str] = mapped_column(Text)
    speaker_id: Mapped[int | None] = mapped_column(
        ForeignKey("speakers.id"),
        nullable=True,
    )
    emotion_reference: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TranscriptRevisionJobRecord(Base):
    __tablename__ = "transcript_revision_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    source_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class CanonicalTranscriptRevisionRecord(Base):
    __tablename__ = "canonical_transcript_revisions"
    __table_args__ = (
        UniqueConstraint(
            "episode_id",
            "ordinal",
            "version",
            name="uq_canonical_transcript_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    source_text: Mapped[str] = mapped_column(Text)
    transcript_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodeMetadataRevisionRecord(Base):
    __tablename__ = "episode_metadata_revisions"
    __table_args__ = (
        UniqueConstraint(
            "episode_id",
            "version",
            name="uq_episode_metadata_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(1024))
    overview: Mapped[str] = mapped_column(Text)
    publication_description: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default="",
    )
    chapter_proposals_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
        server_default="[]",
    )
    chapters_json: Mapped[str] = mapped_column(Text)
    artwork_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodeContentEditRevisionRecord(Base):
    __tablename__ = "episode_content_edit_revisions"
    __table_args__ = (
        UniqueConstraint(
            "episode_id",
            "version",
            name="uq_episode_content_edit_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    processing_run_id: Mapped[int] = mapped_column(ForeignKey("processing_runs.id"))
    version: Mapped[int] = mapped_column(Integer)
    content_edit_json: Mapped[str] = mapped_column(Text)
    artifact_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TelegramActionRecord(Base):
    __tablename__ = "telegram_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id"),
        nullable=True,
    )
    candidate_video_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate_videos.id"),
        nullable=True,
    )
    decision: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class PublicationDecisionRecord(Base):
    __tablename__ = "publication_decisions"
    __table_args__ = (
        UniqueConstraint(
            "episode_id",
            "previous_state",
            "prior_publication_version",
            "revision_version",
            name="uq_publication_decision_opportunity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_key: Mapped[str] = mapped_column(String(64), unique=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    decision: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(32))
    owner_identity: Mapped[str] = mapped_column(
        String(64),
        default="owner",
        server_default="owner",
    )
    previous_state: Mapped[str] = mapped_column(String(64))
    resulting_state: Mapped[str] = mapped_column(String(64))
    prior_publication_version: Mapped[int] = mapped_column(Integer)
    publication_version: Mapped[int] = mapped_column(Integer)
    revision_version: Mapped[int] = mapped_column(Integer)
    artifact_json: Mapped[str] = mapped_column(Text)
    quality_json: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationNotificationRecord(Base):
    __tablename__ = "publication_notifications"
    __table_args__ = (
        UniqueConstraint(
            "episode_id",
            "revision_version",
            name="uq_publication_notification_episode_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"))
    revision_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    message_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TelegramDeliveryRecord(Base):
    __tablename__ = "telegram_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "candidate_video_id",
            "kind",
            "policy_version",
            name="uq_telegram_delivery_candidate_kind_policy",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_video_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_videos.id"),
    )
    kind: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    message_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
