from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ChannelMode(StrEnum):
    AGENT_FILTERING = "agent_filtering"
    PROCESS_EVERY_VIDEO = "process_every_video"
    PAUSED = "paused"


class TriageDecision(StrEnum):
    PROCESS = "process"
    OWNER_DECISION = "owner_decision"
    IGNORE = "ignore"


class EpisodeState(StrEnum):
    PUBLICATION_QUEUE = "publication_queue"
    REVISION_PROCESSING = "revision_processing"
    REVISION_PENDING = "revision_pending"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    REVIEW_QUEUE = "review_queue"


class EpisodeLibraryState(StrEnum):
    READY_TO_START = "ready_to_start"
    QUEUED = "queued"
    PROCESSING = "processing"
    ACTION_REQUIRED = "action_required"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    TAKEN_DOWN = "taken_down"


class EpisodeLibraryAction(StrEnum):
    START = "start"
    VIEW = "view"
    PLAY = "play"
    PUBLISH = "publish"
    DECLINE = "decline"
    REMOVE = "remove"
    RESTORE = "restore"


class PublicationRevisionKind(StrEnum):
    NONE = "none"
    AUDIO = "audio"
    METADATA = "metadata"


class ChannelSpeakerProfileState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ChannelVoiceCandidateDecision(StrEnum):
    PENDING = "pending"
    SAVED = "saved"
    IGNORED = "ignored"
    MERGED = "merged"


@dataclass(frozen=True)
class EpisodeLibraryItem:
    episode_id: int | None
    processing_run_id: int
    title: str
    overview: str | None
    state: str
    stage: str | None
    duration_seconds: int
    source_show: str
    source_title: str
    source_url: str
    source_published_at: datetime
    guests: tuple[str, ...]
    created_at: datetime
    published_at: datetime | None
    can_reprocess: bool
    library_state: EpisodeLibraryState
    available_actions: tuple[EpisodeLibraryAction, ...]
    artwork_url: str = ""


@dataclass(frozen=True)
class ProcessingRunSummary:
    id: int
    revision: str
    status: str
    title: str
    source_show: str
    owner_state: str
    current_step: str
    estimated_cost_usd: float
    input_tokens: int
    output_tokens: int
    latest_change_at: datetime


@dataclass(frozen=True)
class ChannelVoiceLibrarySummary:
    source_channel_id: int
    title: str
    avatar_url: str
    saved_profile_count: int
    pending_candidate_count: int


@dataclass(frozen=True)
class VoiceReferencePreview:
    id: int
    duration_seconds: float | None
    audio_url: str


@dataclass(frozen=True)
class ChannelSpeakerSuggestion:
    profile_id: int
    source_channel_id: int
    source_channel_title: str
    speaker_id: int
    display_name: str
    similarity: float


@dataclass(frozen=True)
class ChannelVoiceCandidate:
    id: int
    speaker_id: int
    display_name: str
    local_label: str
    segment_count: int
    source_episode_id: int
    source_episode_title: str
    voice_reference: VoiceReferencePreview
    possible_matches: tuple[ChannelSpeakerSuggestion, ...] = ()


@dataclass(frozen=True)
class ChannelSpeakerProfile:
    id: int
    speaker_id: int
    display_name: str
    state: ChannelSpeakerProfileState
    voice_reference: VoiceReferencePreview


@dataclass(frozen=True)
class ChannelVoiceLibraryDetail:
    source_channel_id: int
    title: str
    avatar_url: str
    saved_profiles: tuple[ChannelSpeakerProfile, ...]
    pending_candidates: tuple[ChannelVoiceCandidate, ...]


@dataclass(frozen=True)
class ChannelResolution:
    youtube_channel_id: str
    title: str
    canonical_url: str
    avatar_url: str = ""
    subscriber_count: int | None = None
    video_count: int | None = None


@dataclass(frozen=True)
class SourceVideoPreview:
    """A lightweight Channel Backfill item from the flat playlist.

    The preview never carries captions or a full description; those stay
    deferred until the video is actually imported.
    """

    youtube_video_id: str
    title: str
    duration_seconds: int
    source_url: str
    published_at: datetime | None = None
    view_count: int | None = None


@dataclass(frozen=True)
class RecentVideoWindow:
    """The flat recent-video window of a Source Channel.

    The channel avatar rides along because the flat playlist document
    already contains it; video-URL resolutions cannot obtain it any
    other way without an extra request.
    """

    previews: tuple[SourceVideoPreview, ...]
    channel_avatar_url: str = ""


@dataclass(frozen=True)
class SourceCandidate:
    youtube_video_id: str
    title: str
    description: str
    duration_seconds: int
    language: str
    published_at: datetime
    source_url: str
    captions: str | None
    youtube_channel_id: str | None = None
    analysis_json: str | None = None
    source_archive: SourceArchive | None = None
    prior_preview_audio: bytes | None = None
    prior_render_json: str | None = None
    processing_run_id: int | None = None
    processing_attempt_id: int | None = None
    processing_correlation_id: str = ""


@dataclass(frozen=True)
class SourceArchive:
    content: bytes
    extension: str
    media_type: str
    checksum: str


@dataclass(frozen=True)
class SourceThumbnail:
    content: bytes
    source_url: str
    media_type: str
    width: int = 0
    height: int = 0
    retrieval_result: str = "selected"


@dataclass(frozen=True)
class CanonicalSegment:
    segment_id: str
    speaker_label: str
    start_seconds: float
    end_seconds: float
    text: str
    words: tuple[tuple[str, float, float], ...] = ()
    overlap: bool = False
    events: tuple[str, ...] = ()
    confidence: float = 1.0
    review_state: str = "accepted"
    omitted_as_sponsor: bool = False


@dataclass(frozen=True)
class AnalyzedSpeaker:
    local_label: str
    reference_audio: bytes
    embedding: tuple[float, ...]
    confidence: float
    registry_speaker_id: int | None = None
    reference_artifact_key: str = ""
    registry_voice_reference_id: int | None = None
    matched_source_channel_id: int | None = None
    match_similarity: float | None = None
    match_policy_version: str = ""


@dataclass(frozen=True)
class SourceAnalysisResult:
    segments: tuple[CanonicalSegment, ...]
    pipeline_version: str
    diarization_model: str
    transcription_model: str
    alignment_model: str
    diarization_model_revision: str = ""
    source_events: tuple[tuple[str, float, float], ...] = ()
    background_regions: tuple[tuple[float, float], ...] = ()
    speakers: tuple[AnalyzedSpeaker, ...] = ()
    raw_diarization_turns: tuple[tuple[str, float, float], ...] = ()
    full_asr_text: str = ""
    aligned_words: tuple[tuple[str, float, float], ...] = ()
    boundary_stabilization_json: str = "{}"
    source_canonicalization_json: str = "{}"
    speaker_consolidation_json: str = "{}"
    boundary_adjudication_json: str = "{}"
    checksum: str = ""
    estimated_cost_usd: float = 0.0
    call_seconds: tuple[tuple[str, float], ...] = ()
    routing_json: str = "{}"


@dataclass(frozen=True)
class TriageResult:
    decision: TriageDecision
    confidence: float
    rationale: str
    matched_interests: tuple[str, ...]
    title_zh: str = ""
    summary_zh: str = ""
    recommendation_zh: str = ""


@dataclass(frozen=True)
class TranslationResult:
    title: str
    overview: str
    target_locale: str
    segments: tuple[str, ...]
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    reviewed: bool = False
    review_model: str = ""
    policy_version: str = "translation-policy-v1"
    elapsed_ms: int = 0
    input_hash: str = ""
    output_hash: str = ""
    cli_version: str = ""
    audit_json: str = "{}"
    content_edit_json: str = "{}"
    publication_metadata_json: str = "{}"


@dataclass(frozen=True)
class PublicationMetadataResult:
    artifact_json: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cli_version: str = ""
    session_id: str = ""


@dataclass(frozen=True)
class AudioQualityReport:
    codec: str = "AAC-LC"
    sample_rate_hz: int = 48_000
    channels: int = 2
    bitrate_kbps: int = 128
    loudness_lufs: float = -16.0
    true_peak_dbtp: float = -1.0
    pre_encode_loudness_lufs: float | None = None
    pre_encode_true_peak_dbtp: float | None = None
    clipping: bool = False
    rendered_segments: int = 1
    timeline_continuous: bool = True
    boundary_artifacts: bool = False
    source_language_leakage: bool = False


@dataclass(frozen=True)
class SpeechResult:
    publication_audio: bytes
    duration_seconds: int
    chapters: tuple[tuple[int, str], ...]
    pipeline_version: str
    audio_format: str = "audio/mp4"
    quality: AudioQualityReport = AudioQualityReport()
    estimated_cost_usd: float = 0.0
    segment_timings: tuple[tuple[float, float], ...] = ()
    routing_json: str = "{}"


@dataclass(frozen=True)
class NotificationAction:
    label: str
    token: str


@dataclass(frozen=True)
class OwnerNotification:
    kind: str
    title: str
    body: str
    actions: tuple[NotificationAction, ...]
