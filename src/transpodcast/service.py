from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
import wave
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import and_, delete, exists, func, or_, select, text
from sqlalchemy.orm import Session

from transpodcast.adjudication_completion import (
    AdjudicationCompletion,
    complete_boundary_adjudication,
    supports_speaker_tail_repair,
)
from transpodcast.artifacts import persist_structured_artifact
from transpodcast.artwork import (
    EPISODE_ARTWORK_TEMPLATE_VERSION,
    artwork_cache_key,
    render_episode_artwork,
    validate_episode_artwork,
)
from transpodcast.content_editing import (
    CONTENT_EDIT_POLICY_VERSION,
    RenderPlan,
    build_render_plan,
    content_edit_for_segment,
)
from transpodcast.decision_records import DecisionLedger, DecisionRecord
from transpodcast.dependencies import AppDependencies
from transpodcast.domain import (
    AnalyzedSpeaker,
    AudioQualityReport,
    CanonicalSegment,
    ChannelMode,
    ChannelResolution,
    ChannelSpeakerProfile,
    ChannelSpeakerProfileState,
    ChannelSpeakerSuggestion,
    ChannelVoiceCandidate,
    ChannelVoiceCandidateDecision,
    ChannelVoiceLibraryDetail,
    ChannelVoiceLibrarySummary,
    EpisodeLibraryAction,
    EpisodeLibraryItem,
    EpisodeLibraryState,
    EpisodeState,
    NotificationAction,
    OwnerNotification,
    ProcessingRunSummary,
    PublicationRevisionKind,
    SourceAnalysisResult,
    SourceArchive,
    SourceCandidate,
    SourceVideoPreview,
    SpeechResult,
    TranslationResult,
    TriageDecision,
    TriageResult,
    VoiceReferencePreview,
)
from transpodcast.errors import (
    DiscoveryAlreadyRunningError,
    NotificationDeliveryRejectedError,
    NotificationDeliveryUncertainError,
    SpendGuardrailReached,
    TransientProviderError,
    TranslationProviderError,
    TranslationStalledError,
)
from transpodcast.feed import FeedItem, render_private_feed
from transpodcast.i18n import zh_cn
from transpodcast.maintenance import MaintenanceManager
from transpodcast.models import (
    CandidateVideoRecord,
    CanonicalTranscriptRevisionRecord,
    ChannelSpeakerProfileRecord,
    ChannelVoiceCandidateRecord,
    EpisodeContentEditRevisionRecord,
    EpisodeMetadataRevisionRecord,
    EpisodeRecord,
    EpisodeSegmentRecord,
    EpisodeSegmentRevisionRecord,
    InterestProfileRevisionRecord,
    LabeledExampleRecord,
    ProcessingAttemptRecord,
    ProcessingRunActivityRecord,
    ProcessingRunRecord,
    ProcessingStageRecord,
    PublicationDecisionRecord,
    PublicationNotificationRecord,
    SegmentRevisionJobRecord,
    SettingRecord,
    SourceChannelRecord,
    SpeakerRecord,
    SpeakerVoiceReferenceRecord,
    TelegramActionRecord,
    TelegramDeliveryRecord,
    TranscriptRevisionJobRecord,
)
from transpodcast.pipeline_versions import (
    CURRENT_SPEECH_PIPELINE_VERSION,
    SPEECH_PIPELINE_V13,
    SPEECH_PIPELINE_V14,
)
from transpodcast.publication_container import (
    PublicationChapter,
    embed_publication_metadata,
)
from transpodcast.publication_metadata import (
    PublicationMetadata,
    compose_publication_description,
    degraded_publication_metadata,
    parse_publication_metadata,
    resolve_episode_chapters_with_diagnostics,
    validate_publication_copy,
)
from transpodcast.similarity import cosine_similarity
from transpodcast.transcript import canonical_caption_segments
from transpodcast.translation_projects import (
    mark_translation_project_reprocess,
    read_translation_project,
    reset_translation_project,
)

_DISCOVERY_SCHEDULE_KEY = "discovery_schedule_v1"
_DISCOVERY_LEASE_HOURS = 2
_DISCOVERY_RETRY_MINUTES = 15

_TRUE_PEAK_GATE_MAX_DBTP = -1.0
_LOUDNESS_GATE_MIN_LUFS = -16.5
_LOUDNESS_GATE_MAX_LUFS = -15.5

_RESOLUTION_TTL = timedelta(minutes=15)
_RECENT_VIDEOS_TTL = timedelta(minutes=10)
_RATIONALE_OWNER_URL = "The Owner added this Supported Source Video by URL."
_RATIONALE_OWNER_IMPORT = (
    "The Owner explicitly added this Supported Source Video."
)
_CANDIDATE_RECOMMENDATION_POLICY = "candidate-recommendation-v1"
_LEGACY_CANDIDATE_RECOMMENDATION_PREFERENCE = "candidate_decision"
_RATIONALE_BACKFILL = "The Owner selected this video from Channel Backfill."


def _normalize_publication_copy_for_comparison(value: str) -> str:
    return "\n\n".join(
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", value.strip())
        if paragraph.strip()
    )


def _publication_artwork_identity(
    metadata: Mapping[str, object],
) -> tuple[object, ...]:
    if metadata.get("status") != "completed":
        return ("show_artwork",)
    return (
        "episode_artwork",
        metadata.get("output_sha256"),
        metadata.get("template_version"),
    )


def _is_publication_audio(content: bytes) -> bool:
    return content.startswith(b"M4A!") or (
        len(content) >= 12 and content[4:8] == b"ftyp"
    )


@dataclass
class SourceResolutionRecord:
    """A short-lived server-side resolution the save command references.

    The record removes repeated YouTube work between resolve and save. It
    never contains credentials or downloaded media.
    """

    resolution_id: str
    url: str
    channel: ChannelResolution
    requested_video: SourceCandidate | None
    created_at: datetime
    backfill: list[SourceVideoPreview] | None = None


@dataclass
class RecentVideosCacheEntry:
    """A cached channel recent-video window for the channel page.

    Only the YouTube-derived previews are cached; library state is
    always joined fresh from SQLite.
    """

    previews: list[SourceVideoPreview]
    cached_at: datetime


@dataclass(frozen=True)
class PreparedEpisodeArtwork:
    path: Path
    metadata: dict[str, object]


@dataclass(frozen=True)
class ResolvedPublicationMetadata:
    description: str
    chapters: tuple[tuple[int, str], ...]
    chapter_proposals_json: str


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _wall_duration_ms(started: datetime, completed: datetime) -> int:
    return max(
        0,
        int(
            (
                _utc_instant(completed) - _utc_instant(started)
            ).total_seconds()
            * 1000
        ),
    )


def _wave_duration_seconds(artifact_key: str) -> float | None:
    try:
        with wave.open(artifact_key, "rb") as source:
            frame_rate = source.getframerate()
            if frame_rate <= 0:
                return None
            return round(source.getnframes() / frame_rate, 1)
    except (OSError, EOFError, wave.Error):
        return None


def _channel_speaker_profile(
    profile: ChannelSpeakerProfileRecord,
    speaker: SpeakerRecord,
    reference: SpeakerVoiceReferenceRecord,
) -> ChannelSpeakerProfile:
    return ChannelSpeakerProfile(
        id=profile.id,
        speaker_id=speaker.id,
        display_name=profile.display_name,
        state=ChannelSpeakerProfileState(profile.state),
        voice_reference=VoiceReferencePreview(
            id=reference.id,
            duration_seconds=_wave_duration_seconds(reference.artifact_key),
            audio_url=(
                f"/api/v1/speaker-voice-references/{reference.id}/audio"
            ),
        ),
    )


def _load_channel_speaker_profile(
    session: Session,
    profile_id: int,
) -> tuple[
    ChannelSpeakerProfileRecord,
    SpeakerRecord,
    SpeakerVoiceReferenceRecord,
]:
    profile = session.get(ChannelSpeakerProfileRecord, profile_id)
    if profile is None:
        raise LookupError("Channel Speaker Profile was not found")
    speaker = session.get(SpeakerRecord, profile.speaker_id)
    if speaker is None:
        raise LookupError("Speaker was not found")
    reference = session.scalar(
        select(SpeakerVoiceReferenceRecord).where(
            SpeakerVoiceReferenceRecord.speaker_id == speaker.id,
            SpeakerVoiceReferenceRecord.status == "approved",
        )
    )
    if reference is None:
        raise LookupError("Canonical Voice Reference was not found")
    return profile, speaker, reference


def _resume_checkpoint(run: ProcessingRunRecord) -> str:
    if run.render_json is not None and run.render_preview_key is not None:
        return "render"
    if run.translation_json is not None:
        return "translation"
    if run.analysis_json is not None:
        return "source_analysis"
    if run.source_archive_key is not None:
        return "source_archive"
    return "ingest"


_OWNER_PIPELINE_STEPS = (
    "source_archive",
    "source_analysis",
    "translation_authoring",
    "translation_review",
    "content_editing",
    "voice_render",
    "publication_audio_quality",
    "publication_queue",
)

_SAFE_PROVIDER_ERROR_SUMMARIES = {
    "analysis_provider": "Source analysis provider did not complete.",
    "mastering_provider": "Publication Audio mastering did not complete.",
    "source_provider": "Source media provider did not complete.",
    "speech_provider": "Voice rendering provider did not complete.",
    "source_analysis_provider": "Source analysis provider did not complete.",
    "transient_retry_exhausted": (
        "A provider remained unavailable after automatic retries."
    ),
    "translation_provider": "Translation provider did not complete.",
}
_UNSAFE_ERROR_MARKERS = (
    "traceback",
    'file "',
    "bearer ",
    "hf_token",
    "api_key",
    "authorization:",
    "password=",
    "secret=",
    "signature=",
    "token=",
    "x-amz-",
)


def _safe_error_summary(kind: str | None, summary: str | None) -> str | None:
    """Return bounded owner-facing error text without operational secrets."""
    if not kind or not summary:
        return None
    if kind in _SAFE_PROVIDER_ERROR_SUMMARIES:
        return _SAFE_PROVIDER_ERROR_SUMMARIES[kind]
    if kind == "translation_stalled":
        return "Translation stopped after two turns without progress."
    if kind == "translation_schema":
        return "Translation failed deterministic structure checks."
    if kind == "transient":
        return "A provider is temporarily unavailable."
    normalized = " ".join(summary.split())
    lowered = normalized.lower()
    unsafe = any(marker in lowered for marker in _UNSAFE_ERROR_MARKERS)
    unsafe = unsafe or bool(
        re.search(r"(?:^|\s)(?:/Users/|/home/|/tmp/|[A-Z]:\\)", normalized)
    )
    if unsafe:
        return "Processing stopped; technical details were redacted."
    return normalized[:320]


def _json_mapping(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _optional_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _latest_recorded_at(values: list[datetime]) -> datetime:
    """Compare legacy naive and current timezone-aware SQLite timestamps."""
    return max(
        values,
        key=lambda value: (
            value.replace(tzinfo=UTC)
            if value.tzinfo is None
            else value.astimezone(UTC)
        ),
    )


def _translation_step_timing(
    step_id: str,
    project: dict[str, Any],
    attempt: ProcessingAttemptRecord | None,
    observed_at: datetime,
) -> tuple[datetime | None, datetime | None, int | None]:
    turns = project.get("turns")
    turn_rows = turns if isinstance(turns, list) else []

    def recorded_times(role: str) -> list[datetime]:
        values = [
            _optional_iso_datetime(item.get("recorded_at"))
            for item in turn_rows
            if isinstance(item, dict) and item.get("role") == role
        ]
        return sorted(
            (value for value in values if value is not None),
            key=_utc_instant,
        )

    author_times = recorded_times("author")
    reviewer_times = recorded_times("reviewer")
    project_started = _optional_iso_datetime(project.get("created_at"))
    phase = str(project.get("phase") or "")
    if step_id == "translation_authoring":
        started_at = project_started or (
            attempt.created_at if attempt is not None else None
        )
        completed_at = (
            author_times[-1]
            if author_times and phase in {"reviewing", "completed"}
            else None
        )
    else:
        started_at = author_times[-1] if author_times else None
        completed_at = (
            reviewer_times[-1]
            if reviewer_times and phase == "completed"
            else None
        )
    if started_at is None:
        return None, completed_at, None
    duration_end = completed_at or observed_at
    return (
        started_at,
        completed_at,
        _wall_duration_ms(started_at, duration_end),
    )


def _utc_instant(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


def _candidate_status(
    decision: TriageDecision,
    *,
    await_manual_recommendation: bool,
) -> str:
    if decision is TriageDecision.PROCESS and await_manual_recommendation:
        return "awaiting_decision"
    return {
        TriageDecision.PROCESS: "processing_queued",
        TriageDecision.OWNER_DECISION: "awaiting_decision",
        TriageDecision.IGNORE: "ignored",
    }[decision]


def _discovery_count_key(candidate_status: str) -> str:
    return {
        "processing_queued": "automatically_accepted",
        "awaiting_decision": "awaiting_decision",
        "ignored": "ignored",
    }[candidate_status]


def _owner_run_state(run: ProcessingRunRecord) -> str:
    return _owner_state_for_status(run.status)


def _owner_state_for_status(status: str) -> str:
    return {
        "review_queue": "action_required",
        "retry_wait": "waiting_retry",
        "completed": "publication_ready",
        "queued": "queued",
        "pending_start": "queued",
        "processing": "processing",
        "paused": "paused",
    }.get(status, status)


def _processing_run_summary_revision(summary: ProcessingRunSummary) -> str:
    payload: dict[str, object] = {}
    for field in fields(summary):
        if field.name == "revision":
            continue
        value = getattr(summary, field.name)
        payload[field.name] = (
            value.isoformat() if isinstance(value, datetime) else value
        )
    return _content_hash(payload)


def _owner_current_step(
    run: ProcessingRunRecord,
    project: dict[str, Any] | None,
) -> str:
    return _owner_step_for_stage(run.stage, project)


def _owner_step_for_stage(
    stage: str,
    project: dict[str, Any] | None,
) -> str:
    if stage == "translation":
        if project and (
            project.get("phase") == "reviewing"
            or (
                project.get("phase") in {"failed", "stalled"}
                and project.get("failed_role") == "reviewer"
            )
        ):
            return "translation_review"
        return "translation_authoring"
    return {
        "ingest": "source_archive",
        "source_archive": "source_archive",
        "source_analysis": "source_analysis",
        "render": "voice_render",
        "mastering_repair": "publication_audio_quality",
        "quality_gate": "publication_audio_quality",
        "publication_queue": "publication_queue",
    }.get(stage, stage)


def _retry_plan(
    run: ProcessingRunRecord,
    project: dict[str, Any] | None,
) -> dict[str, object]:
    resume_step = _owner_current_step(run, project)
    try:
        resume_index = _OWNER_PIPELINE_STEPS.index(resume_step)
    except ValueError:
        resume_index = 0
        resume_step = _OWNER_PIPELINE_STEPS[0]
    return {
        "resume_step": resume_step,
        "reused_steps": list(_OWNER_PIPELINE_STEPS[:resume_index]),
        "invalidated_steps": list(_OWNER_PIPELINE_STEPS[resume_index:]),
        "preserves_translation_targets": bool(
            project
            and int(project.get("completed_targets", 0)) > 0
            and resume_step == "translation_authoring"
        ),
    }


def _pipeline_steps(
    run: ProcessingRunRecord,
    stages: list[ProcessingStageRecord],
    project: dict[str, Any] | None,
    attempts: list[ProcessingAttemptRecord] | None = None,
    observed_at: datetime | None = None,
) -> list[dict[str, object]]:
    attempts = attempts or []
    observed_at = observed_at or datetime.now(UTC)
    completed_stage_names = {item.stage for item in stages}
    completed: set[str] = set()
    if "source_archive" in completed_stage_names:
        completed.add("source_archive")
    if "source_analysis" in completed_stage_names:
        completed.add("source_analysis")
    if "translation" in completed_stage_names or run.translation_json:
        completed.update({"translation_authoring", "translation_review"})
    elif project:
        phase = str(project.get("phase", ""))
        if phase in {"reviewing", "completed"}:
            completed.add("translation_authoring")
        if phase == "completed":
            completed.add("translation_review")
    if run.content_edit_json is not None:
        completed.add("content_editing")
    if "render" in completed_stage_names:
        completed.add("voice_render")
    if (
        "quality_gate" in completed_stage_names
        or "mastering_repair" in completed_stage_names
    ):
        completed.add("publication_audio_quality")
    if run.status == "completed":
        completed.add("publication_queue")

    active_attempt = next(
        (
            item
            for item in reversed(attempts)
            if item.status == "processing"
        ),
        None,
    )
    current = (
        _owner_step_for_stage(active_attempt.stage, project)
        if active_attempt is not None
        else _owner_current_step(run, project)
    )
    owner_state = _owner_run_state(run)
    latest_stages = {item.stage: item for item in stages}
    latest_attempts = {
        item.stage: item
        for item in attempts
        if item.status == "processing"
    }
    analysis = _json_mapping(run.analysis_json)
    render = _json_mapping(run.render_json)
    quality = render.get("quality", {})
    if not isinstance(quality, dict):
        quality = {}
    retry_plan = _retry_plan(run, project)
    reused_steps = cast(list[str], retry_plan["reused_steps"])
    state_mapping = {
        "processing": "active",
        "waiting_retry": "waiting_to_retry",
    }
    steps: list[dict[str, object]] = []
    legacy_content_edit_not_applicable = (
        run.status == "completed" and run.content_edit_json is None
    )
    for step_id in _OWNER_PIPELINE_STEPS:
        if (
            step_id == "content_editing"
            and legacy_content_edit_not_applicable
        ):
            state = "not_applicable"
        elif step_id in completed:
            state = "completed"
        elif step_id == current:
            state = state_mapping.get(owner_state, owner_state)
        else:
            state = "not_started"
        progress: dict[str, object] | None = None
        evidence_available = False
        metrics: dict[str, object] = {}
        if step_id == "translation_authoring" and project:
            progress = {
                "completed": int(project.get("completed_targets", 0)),
                "total": int(project.get("total_targets", 0)),
                "unit": "targets",
            }
            evidence_available = True
            metrics = {
                "turns": int(project.get("author_turns", 0)),
                "sessions": len(project.get("author_sessions", [])),
                "input_tokens": int(project.get("input_tokens", 0)),
                "output_tokens": int(project.get("output_tokens", 0)),
            }
        elif step_id == "translation_review" and project:
            evidence_available = True
            if project.get("phase") == "completed":
                progress = {
                    "completed": int(project.get("total_targets", 0)),
                    "total": int(project.get("total_targets", 0)),
                    "unit": "targets",
                }
            metrics = {
                "turns": int(project.get("reviewer_turns", 0)),
                "sessions": len(project.get("reviewer_sessions", [])),
            }
        stage_name = {
            "translation_authoring": "translation",
            "translation_review": "translation",
            "voice_render": "render",
            "publication_audio_quality": "quality_gate",
        }.get(step_id, step_id)
        stage = latest_stages.get(stage_name)
        active_attempt = latest_attempts.get(stage_name)
        if (
            step_id == "publication_audio_quality"
            and active_attempt is None
        ):
            active_attempt = latest_attempts.get("mastering_repair")
        if step_id == "source_archive" and stage is not None:
            evidence_available = True
            metrics = {
                "archive_bytes": _json_mapping(stage.usage_json).get(
                    "archive_bytes"
                )
            }
        elif step_id == "source_analysis" and stage is not None:
            usage = _json_mapping(stage.usage_json)
            segments = analysis.get("segments", [])
            speakers = analysis.get("speakers", [])
            used_speaker_labels = (
                {
                    str(item.get("speaker_label"))
                    for item in segments
                    if isinstance(item, dict) and item.get("speaker_label")
                }
                if isinstance(segments, list)
                else set()
            )
            evidence_available = True
            metrics = {
                "segments": len(segments)
                if isinstance(segments, list)
                else None,
                "speakers": len(used_speaker_labels),
                "raw_speaker_candidates": len(speakers)
                if isinstance(speakers, list)
                else None,
                "transfer_bytes": usage.get("upload_bytes"),
                "call_seconds": usage.get("call_seconds"),
                "estimated_cost_usd": usage.get("estimated_cost_usd"),
            }
        elif step_id == "voice_render" and stage is not None:
            usage = _json_mapping(stage.usage_json)
            evidence_available = True
            metrics = {
                "rendered_segments": usage.get("rendered_segments"),
                "output_bytes": usage.get("audio_bytes"),
                "estimated_cost_usd": usage.get("estimated_cost_usd"),
            }
        elif step_id == "publication_audio_quality" and render:
            evidence_available = True
            metrics = {
                "codec": quality.get("codec"),
                "sample_rate_hz": quality.get("sample_rate_hz"),
                "channels": quality.get("channels"),
                "bitrate_kbps": quality.get("bitrate_kbps"),
                "loudness_lufs": quality.get("loudness_lufs"),
                "true_peak_dbtp": quality.get("true_peak_dbtp"),
                "timeline_continuous": quality.get("timeline_continuous"),
                "boundary_artifacts": quality.get("boundary_artifacts"),
                "source_language_leakage": quality.get(
                    "source_language_leakage"
                ),
            }
        technical_evidence: dict[str, object] | None = None
        if stage is not None:
            stage_usage = _json_mapping(stage.usage_json)
            routing = stage_usage.get("routing")
            routing = routing if isinstance(routing, dict) else {}
            route_attempts = routing.get("attempts")
            route_profiles = (
                [
                    str(item.get("profile"))
                    for item in route_attempts
                    if isinstance(item, dict) and item.get("profile")
                ]
                if isinstance(route_attempts, list)
                else []
            )
            technical_evidence = {
                "version": stage.version,
                "provider": stage.provider,
                "model": stage.model,
                "modal_account": routing.get("selected_profile"),
                "modal_route": route_profiles,
                "parallel_operations": (
                    stage_usage.get("call_seconds")
                    if step_id == "source_analysis"
                    else None
                ),
            }
        elif step_id.startswith("translation_") and project:
            technical_evidence = {
                "version": project.get("policy_version"),
                "provider": "codex-cli",
                "model": (
                    project.get("author_models")
                    if step_id == "translation_authoring"
                    else project.get("reviewer_models")
                ),
            }
        elif (
            step_id == "publication_audio_quality"
            and active_attempt is not None
        ):
            technical_evidence = {
                "version": None,
                "provider": "local",
                "model": None,
            }
        started_at = (
            stage.started_at
            if stage
            else active_attempt.created_at
            if active_attempt
            else None
        )
        completed_at = stage.completed_at if stage else None
        duration_ms = (
            stage.duration_ms
            if stage
            else _wall_duration_ms(
                active_attempt.created_at,
                observed_at,
            )
            if active_attempt
            else None
        )
        if step_id.startswith("translation_") and project:
            started_at, completed_at, duration_ms = (
                _translation_step_timing(
                    step_id,
                    project,
                    active_attempt,
                    observed_at,
                )
            )
        steps.append(
            {
                "id": step_id,
                "state": state,
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_ms": duration_ms,
                "summary": (
                    "Checkpoint completed"
                    if state == "completed"
                    else "Current Pipeline step"
                    if step_id == current
                    else "No recorded checkpoint"
                ),
                "progress": progress,
                "metrics": metrics,
                "evidence_available": evidence_available,
                "recovery": {
                    "retryable": (
                        step_id == retry_plan["resume_step"]
                        and run.status == "review_queue"
                    ),
                    "checkpoint": (step_id in reused_steps),
                },
                "technical_evidence": technical_evidence,
            }
        )
    return steps


def _candidate_origin(rationale: str) -> str:
    if rationale in {_RATIONALE_OWNER_URL, _RATIONALE_OWNER_IMPORT}:
        return "manual"
    if rationale == _RATIONALE_BACKFILL:
        return "backfill"
    return "discovery"


def _episode_library_state(state: str) -> EpisodeLibraryState:
    if state == "deleted":
        return EpisodeLibraryState.TAKEN_DOWN
    if state in {
        EpisodeState.PUBLICATION_QUEUE.value,
        EpisodeState.REVISION_PENDING.value,
    }:
        return EpisodeLibraryState.READY_TO_PUBLISH
    if state == EpisodeState.PUBLISHED.value:
        return EpisodeLibraryState.PUBLISHED
    if state == EpisodeState.UNPUBLISHED.value:
        return EpisodeLibraryState.UNPUBLISHED
    if state in {
        EpisodeState.REVIEW_QUEUE.value,
        "spend_guardrail",
        "failed",
    }:
        return EpisodeLibraryState.ACTION_REQUIRED
    return EpisodeLibraryState.PROCESSING


def _processing_library_state(status: str) -> EpisodeLibraryState:
    if status == "pending_start":
        return EpisodeLibraryState.READY_TO_START
    if status == "queued":
        return EpisodeLibraryState.QUEUED
    if status in {"review_queue", "spend_guardrail", "failed"}:
        return EpisodeLibraryState.ACTION_REQUIRED
    return EpisodeLibraryState.PROCESSING


def _episode_library_actions(
    library_state: EpisodeLibraryState,
    *,
    can_restore: bool = True,
) -> tuple[EpisodeLibraryAction, ...]:
    return {
        EpisodeLibraryState.PROCESSING: (EpisodeLibraryAction.VIEW,),
        EpisodeLibraryState.ACTION_REQUIRED: (EpisodeLibraryAction.VIEW,),
        EpisodeLibraryState.READY_TO_PUBLISH: (
            EpisodeLibraryAction.PLAY,
            EpisodeLibraryAction.PUBLISH,
            EpisodeLibraryAction.DECLINE,
        ),
        EpisodeLibraryState.PUBLISHED: (EpisodeLibraryAction.PLAY,),
        EpisodeLibraryState.UNPUBLISHED: (EpisodeLibraryAction.PUBLISH,),
        EpisodeLibraryState.TAKEN_DOWN: (
            (EpisodeLibraryAction.RESTORE,) if can_restore else ()
        ),
    }.get(library_state, ())


class TransPodcastService:
    def __init__(self, dependencies: AppDependencies) -> None:
        self.dependencies = dependencies
        self._resolutions: dict[str, SourceResolutionRecord] = {}
        self._recent_videos: dict[int, RecentVideosCacheEntry] = {}
        self._artwork_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="transpodcast-artwork",
        )
        self._artwork_futures: dict[
            int,
            Future[PreparedEpisodeArtwork],
        ] = {}
        self._artwork_started_at: dict[int, float] = {}

    def close(self) -> None:
        self._artwork_executor.shutdown(wait=True, cancel_futures=False)
        self.dependencies.database.dispose()

    def setup_readiness(self) -> dict[str, Any]:
        with self.dependencies.database.session() as session:
            has_source_channel = (
                session.scalar(select(SourceChannelRecord.id).limit(1))
                is not None
            )
        services = {
            "youtube": self.dependencies.source.readiness(),
            "codex": self.dependencies.translation.readiness(),
            "modal": (
                self.dependencies.speech.readiness()
                and (
                    self.dependencies.analysis is None
                    or self.dependencies.analysis.readiness()
                )
            ),
            "cloudflare": self.dependencies.storage.readiness(),
            "telegram": self.dependencies.notifications.readiness(),
            "private_feed": len(self.current_feed_token()) >= 32,
            "source_channel": has_source_channel,
        }
        impacts = {
            "youtube": "Source Channel resolution and discovery are unavailable.",
            "codex": "Complete Mandarin translation cannot run.",
            "modal": "IndexTTS2 rendering cannot run.",
            "cloudflare": "Private Preview and publication storage are unavailable.",
            "telegram": "Owner decisions cannot be delivered to the iPhone.",
            "private_feed": "Apple Podcasts has no protected subscription URL.",
            "source_channel": "There is no Source Channel to monitor.",
        }
        required_services = {
            name: configured
            for name, configured in services.items()
            if name != "telegram"
        }
        return {
            "ready": all(required_services.values()),
            "services": services,
            "details": {
                name: {
                    "configured": configured,
                    "impact": "" if configured else impacts[name],
                    "optional": name == "telegram",
                }
                for name, configured in services.items()
            },
        }

    def ensure_publication_assets(self) -> None:
        token = self.current_feed_token()
        if (
            self.dependencies.show_artwork
            and self.dependencies.storage.readiness()
        ):
            self.dependencies.storage.put_publication(
                self._show_artwork_key(),
                self.dependencies.show_artwork,
                content_type="image/png",
            )
        if len(token) >= 32 and self.dependencies.storage.readiness():
            self.dependencies.storage.put_feed_token_hash(
                self._hash_token(token)
            )
            with self.dependencies.database.session() as session:
                self._store_feed_snapshot(session)

    def _prepare_episode_artwork(
        self,
        session: Session,
        run: ProcessingRunRecord,
        source: SourceCandidate,
    ) -> None:
        if run.artwork_status != "pending":
            return
        started = time.monotonic()
        try:
            prepared = self._build_episode_artwork(source)
            self._apply_prepared_episode_artwork(
                session,
                run,
                prepared,
                duration_ms=_elapsed_ms(started),
            )
        except Exception as error:
            self._degrade_episode_artwork(session, run, error)

    def _start_episode_artwork(
        self,
        processing_run_id: int,
        source: SourceCandidate,
    ) -> None:
        if processing_run_id in self._artwork_futures:
            return
        self._artwork_started_at[processing_run_id] = time.monotonic()
        self._artwork_futures[processing_run_id] = (
            self._artwork_executor.submit(
                self._build_episode_artwork,
                source,
            )
        )

    def _collect_episode_artwork(
        self,
        session: Session,
        run: ProcessingRunRecord,
        source: SourceCandidate,
        *,
        wait: bool,
    ) -> None:
        if run.artwork_status != "pending":
            return
        self._start_episode_artwork(run.id, source)
        future = self._artwork_futures[run.id]
        if not wait and not future.done():
            return
        started = self._artwork_started_at.get(run.id, time.monotonic())
        try:
            self._apply_prepared_episode_artwork(
                session,
                run,
                future.result(),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception as error:
            self._degrade_episode_artwork(session, run, error)
        finally:
            self._artwork_futures.pop(run.id, None)
            self._artwork_started_at.pop(run.id, None)

    def _build_episode_artwork(
        self,
        source: SourceCandidate,
    ) -> PreparedEpisodeArtwork:
        thumbnail = self.dependencies.source.thumbnail(source)
        cache_key = artwork_cache_key(
            thumbnail,
            self.dependencies.show_artwork,
        )
        path = (
            self.dependencies.artifact_root
            / "episode-artwork"
            / f"{cache_key}.jpg"
        )
        if path.is_file():
            content = path.read_bytes()
            validate_episode_artwork(content)
            metadata: dict[str, object] = {
                "status": "completed",
                "template_version": EPISODE_ARTWORK_TEMPLATE_VERSION,
                "source_url": thumbnail.source_url,
                "source_sha256": hashlib.sha256(
                    thumbnail.content
                ).hexdigest(),
                "source_media_type": thumbnail.media_type,
                "source_width": thumbnail.width,
                "source_height": thumbnail.height,
                "retrieval_result": thumbnail.retrieval_result,
                "show_artwork_sha256": hashlib.sha256(
                    self.dependencies.show_artwork
                ).hexdigest(),
                "output_sha256": hashlib.sha256(content).hexdigest(),
                "width": 3000,
                "height": 3000,
                "media_type": "image/jpeg",
                "bytes": len(content),
                "cache_hit": True,
            }
        else:
            artwork = render_episode_artwork(
                thumbnail,
                self.dependencies.show_artwork,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(artwork.content)
            temporary.replace(path)
            metadata = json.loads(artwork.metadata_json())
            metadata["cache_hit"] = False
        return PreparedEpisodeArtwork(path=path, metadata=metadata)

    def _apply_prepared_episode_artwork(
        self,
        session: Session,
        run: ProcessingRunRecord,
        prepared: PreparedEpisodeArtwork,
        *,
        duration_ms: int,
    ) -> None:
        run.artwork_status = "completed"
        run.artwork_path = str(prepared.path)
        run.artwork_json = json.dumps(prepared.metadata, sort_keys=True)
        self._record_activity(
            session,
            run,
            kind="stage_completed",
            stage="episode_artwork",
            summary="Episode Artwork is ready",
            metadata={
                "template_version": EPISODE_ARTWORK_TEMPLATE_VERSION,
                "cache_hit": bool(prepared.metadata["cache_hit"]),
                "duration_ms": duration_ms,
            },
        )

    def _degrade_episode_artwork(
        self,
        session: Session,
        run: ProcessingRunRecord,
        error: Exception,
    ) -> None:
        run.artwork_status = "fallback"
        run.artwork_path = None
        run.artwork_json = json.dumps(
            {
                "status": "fallback",
                "template_version": EPISODE_ARTWORK_TEMPLATE_VERSION,
                "error": str(error)[:500],
            },
            sort_keys=True,
        )
        self._record_activity(
            session,
            run,
            kind="stage_degraded",
            stage="episode_artwork",
            summary="Episode Artwork fell back to Show Artwork",
            metadata={"error": str(error)[:500]},
        )

    def create_source_channel(
        self,
        url: str,
        mode: ChannelMode,
        backfill_video_ids: list[str] | None = None,
    ) -> SourceChannelRecord:
        resolved = self.dependencies.source.resolve_channel(url)
        supported_source_video = (
            self.dependencies.source.supported_source_video(url)
        )
        with self.dependencies.database.session() as session:
            existing = session.scalar(
                select(SourceChannelRecord).where(
                    SourceChannelRecord.youtube_channel_id
                    == resolved.youtube_channel_id
                )
            )
            if existing is not None:
                existing.mode = mode.value
                channel = existing
            else:
                channel = SourceChannelRecord(
                    youtube_channel_id=resolved.youtube_channel_id,
                    title=resolved.title,
                    canonical_url=resolved.canonical_url,
                    mode=mode.value,
                    created_at=self.dependencies.clock.now(),
                )
                session.add(channel)
                session.flush()
            if supported_source_video is not None:
                self._import_source_candidate(
                    session,
                    channel,
                    supported_source_video,
                    rationale=_RATIONALE_OWNER_URL,
                )
            self._add_backfill(
                session,
                channel,
                backfill_video_ids or [],
            )
            return channel

    def resolve_source_url(self, url: str) -> dict[str, object]:
        """Resolve a URL fast and store a reusable resolution record.

        A video URL resolves the requested Supported Source Video and
        derives its Source Channel from the same metadata document. The
        Channel Backfill window is intentionally not part of this call.
        """
        started = time.monotonic()
        channel, requested_video = self.dependencies.source.resolve_source(url)
        record = SourceResolutionRecord(
            resolution_id=uuid4().hex,
            url=url,
            channel=channel,
            requested_video=requested_video,
            created_at=self.dependencies.clock.now(),
        )
        self._prune_resolutions()
        self._resolutions[record.resolution_id] = record
        with self.dependencies.database.session() as session:
            existing_channel = session.scalar(
                select(SourceChannelRecord).where(
                    SourceChannelRecord.youtube_channel_id
                    == channel.youtube_channel_id
                )
            )
        return {
            "resolution_id": record.resolution_id,
            "expires_at": record.created_at + _RESOLUTION_TTL,
            "channel": _channel_payload(channel),
            "requested_video": (
                _requested_video_payload(requested_video)
                if requested_video is not None
                else None
            ),
            "existing_source_channel": (
                {
                    "id": existing_channel.id,
                    "youtube_channel_id": (
                        existing_channel.youtube_channel_id
                    ),
                    "title": existing_channel.title,
                    "url": existing_channel.canonical_url,
                    "mode": existing_channel.mode,
                    "avatar_url": existing_channel.avatar_url,
                }
                if existing_channel is not None
                else None
            ),
            "timings_ms": {
                "resolve": int((time.monotonic() - started) * 1000),
            },
        }

    def resolution_backfill(
        self,
        resolution_id: str,
    ) -> dict[str, object]:
        """Load the flat Channel Backfill window for a stored resolution.

        The flat playlist is retrieved first; publication dates arrive
        through parallel per-video enrichment. Neither step downloads
        captions or full descriptions.
        """
        record = self._resolution(resolution_id)
        flat_started = time.monotonic()
        window = self.dependencies.source.recent_video_previews(
            record.channel.youtube_channel_id
        )
        flat_ms = int((time.monotonic() - flat_started) * 1000)
        enrich_started = time.monotonic()
        enriched = self.dependencies.source.enrich_video_previews(
            list(window.previews[:10])
        )
        enrich_ms = int((time.monotonic() - enrich_started) * 1000)
        record.backfill = enriched
        if window.channel_avatar_url and not record.channel.avatar_url:
            # A video-URL resolution cannot see the channel avatar; the
            # flat playlist document provides it for free.
            record.channel = replace(
                record.channel,
                avatar_url=window.channel_avatar_url,
            )
        requested_id = (
            record.requested_video.youtube_video_id
            if record.requested_video is not None
            else None
        )
        return {
            "resolution_id": record.resolution_id,
            "channel_avatar_url": record.channel.avatar_url,
            "items": [
                _preview_payload(
                    preview,
                    is_requested=(preview.youtube_video_id == requested_id),
                )
                for preview in enriched
            ],
            "timings_ms": {
                "flat_playlist": flat_ms,
                "date_enrichment": enrich_ms,
            },
        }

    def create_source_channel_from_resolution(
        self,
        resolution_id: str,
        mode: ChannelMode,
        backfill_video_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """Save a Source Channel from a stored resolution record.

        The save performs no YouTube work. The requested video and every
        selected Channel Backfill video enter the Processing Queue
        immediately; enrichment is deferred to the Processing Service.
        """
        record = self._resolution(resolution_id)
        selected = list(backfill_video_ids or [])
        available = {
            preview.youtube_video_id: preview
            for preview in record.backfill or []
        }
        unknown = set(selected) - set(available)
        if record.requested_video is not None:
            unknown.discard(record.requested_video.youtube_video_id)
        if unknown:
            raise ValueError(
                "Selected Channel Backfill video is not in the recent window"
            )
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            channel = session.scalar(
                select(SourceChannelRecord).where(
                    SourceChannelRecord.youtube_channel_id
                    == record.channel.youtube_channel_id
                )
            )
            if channel is None:
                channel = SourceChannelRecord(
                    youtube_channel_id=record.channel.youtube_channel_id,
                    title=record.channel.title,
                    canonical_url=record.channel.canonical_url,
                    mode=mode.value,
                    avatar_url=record.channel.avatar_url,
                    created_at=now,
                )
                session.add(channel)
                session.flush()
            else:
                channel.mode = mode.value
                if record.channel.avatar_url:
                    channel.avatar_url = record.channel.avatar_url
            imported: list[CandidateVideoRecord] = []
            if record.requested_video is not None:
                imported.append(
                    self._import_source_candidate(
                        session,
                        channel,
                        record.requested_video,
                        rationale=_RATIONALE_OWNER_URL,
                    )
                )
            requested_id = (
                record.requested_video.youtube_video_id
                if record.requested_video is not None
                else None
            )
            for video_id in selected:
                if video_id == requested_id:
                    continue
                imported.append(
                    self._import_source_candidate(
                        session,
                        channel,
                        _candidate_from_preview(available[video_id], now),
                        rationale=_RATIONALE_BACKFILL,
                    )
                )
            if record.backfill:
                # Seed the channel page cache so the post-save redirect
                # does not repeat the Channel Backfill YouTube work.
                self._recent_videos[channel.id] = RecentVideosCacheEntry(
                    previews=list(record.backfill),
                    cached_at=now,
                )
            return {
                "id": channel.id,
                "youtube_channel_id": channel.youtube_channel_id,
                "title": channel.title,
                "url": channel.canonical_url,
                "mode": channel.mode,
                "avatar_url": channel.avatar_url,
                "imported_candidate_ids": [
                    candidate.id for candidate in imported
                ],
            }

    def import_source_video_from_resolution(
        self,
        resolution_id: str,
    ) -> dict[str, object]:
        """Import one explicitly resolved video without changing channel policy."""
        record = self._resolution(resolution_id)
        requested_video = record.requested_video
        if requested_video is None:
            raise ValueError(
                "An explicit import requires a Supported Source Video URL"
            )

        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            channel = session.scalar(
                select(SourceChannelRecord).where(
                    SourceChannelRecord.youtube_channel_id
                    == record.channel.youtube_channel_id
                )
            )
            created_channel = channel is None
            if channel is None:
                channel = SourceChannelRecord(
                    youtube_channel_id=record.channel.youtube_channel_id,
                    title=record.channel.title,
                    canonical_url=record.channel.canonical_url,
                    mode=ChannelMode.AGENT_FILTERING.value,
                    avatar_url=record.channel.avatar_url,
                    created_at=now,
                )
                session.add(channel)
                session.flush()
            elif record.channel.avatar_url and not channel.avatar_url:
                channel.avatar_url = record.channel.avatar_url

            existing = session.scalar(
                select(CandidateVideoRecord).where(
                    CandidateVideoRecord.youtube_video_id
                    == requested_video.youtube_video_id
                )
            )
            reused = existing is not None
            candidate = existing or self._import_source_candidate(
                session,
                channel,
                requested_video,
                rationale=_RATIONALE_OWNER_URL,
            )
            session.flush()
            run = session.scalar(
                select(ProcessingRunRecord).where(
                    ProcessingRunRecord.candidate_video_id == candidate.id
                )
            )
            episode = session.scalar(
                select(EpisodeRecord).where(
                    EpisodeRecord.candidate_video_id == candidate.id
                )
            )
            return {
                "candidate_video_id": candidate.id,
                "youtube_video_id": candidate.youtube_video_id,
                "candidate_status": candidate.status,
                "run_status": run.status if run is not None else None,
                "processing_run_id": run.id if run is not None else None,
                "episode_id": episode.id if episode is not None else None,
                "reused": reused,
                "source_channel": {
                    "id": channel.id,
                    "title": channel.title,
                    "mode": channel.mode,
                    "created": created_channel,
                },
            }

    def _resolution(self, resolution_id: str) -> SourceResolutionRecord:
        self._prune_resolutions()
        record = self._resolutions.get(resolution_id)
        if record is None:
            raise LookupError(
                "Source resolution has expired; resolve the URL again"
            )
        return record

    def _prune_resolutions(self) -> None:
        deadline = self.dependencies.clock.now() - _RESOLUTION_TTL
        expired = [
            key
            for key, record in self._resolutions.items()
            if record.created_at <= deadline
        ]
        for key in expired:
            del self._resolutions[key]

    def resolve_source_channel(
        self,
        url: str,
    ) -> tuple[
        ChannelResolution,
        SourceCandidate | None,
        list[SourceCandidate],
    ]:
        resolved = self.dependencies.source.resolve_channel(url)
        supported_source_video = (
            self.dependencies.source.supported_source_video(url)
        )
        if (
            supported_source_video is not None
            and supported_source_video.youtube_channel_id
            and supported_source_video.youtube_channel_id
            != resolved.youtube_channel_id
        ):
            raise ValueError(
                "Supported Source Video does not belong to the resolved Source Channel"
            )
        recent = self.dependencies.source.recent_videos(
            resolved.youtube_channel_id
        )
        return resolved, supported_source_video, recent[:10]

    def _add_backfill(
        self,
        session: Session,
        channel: SourceChannelRecord,
        video_ids: list[str],
    ) -> None:
        selected = set(video_ids)
        if not selected:
            return
        recent = self.dependencies.source.recent_videos(
            channel.youtube_channel_id
        )
        available = {
            candidate.youtube_video_id: candidate for candidate in recent
        }
        unknown = selected - set(available)
        if unknown:
            raise ValueError(
                "Selected Channel Backfill video is not in the recent window"
            )
        for video_id in video_ids:
            candidate = available[video_id]
            self._import_source_candidate(
                session,
                channel,
                candidate,
                rationale=_RATIONALE_BACKFILL,
            )

    def resolve_source_video(self, url: str) -> SourceCandidate:
        return self.dependencies.source.video(url)

    def queue_channel_video(
        self,
        channel_id: int,
        youtube_video_id: str,
    ) -> dict[str, object]:
        """Queue a recent channel video without live YouTube work.

        The video is built from the cached recent-video window, so the
        action responds immediately; complete metadata and captions are
        deferred to the Processing Service. A video outside the cached
        window falls back to one full metadata request.
        """
        channel = self.source_channel_detail(channel_id)
        now = self.dependencies.clock.now()
        cached = self._recent_videos.get(channel_id)
        preview = next(
            (
                item
                for item in (cached.previews if cached else [])
                if item.youtube_video_id == youtube_video_id
            ),
            None,
        )
        source_candidate = (
            _candidate_from_preview(preview, now)
            if preview is not None
            else self.dependencies.source.video(
                f"https://www.youtube.com/watch?v={youtube_video_id}"
            )
        )
        with self.dependencies.database.session() as session:
            channel_record = session.get(SourceChannelRecord, channel.id)
            if channel_record is None:
                raise LookupError("Source Channel was not found")
            candidate = self._import_source_candidate(
                session,
                channel_record,
                source_candidate,
                rationale=_RATIONALE_OWNER_IMPORT,
            )
            session.flush()
            run = session.scalar(
                select(ProcessingRunRecord).where(
                    ProcessingRunRecord.candidate_video_id == candidate.id
                )
            )
            return {
                "id": candidate.id,
                "youtube_video_id": candidate.youtube_video_id,
                "state": candidate.status,
                "run_status": run.status if run is not None else None,
                "processing_run_id": run.id if run is not None else None,
            }

    def import_source_video(
        self,
        channel_id: int,
        url: str,
    ) -> CandidateVideoRecord:
        source_candidate = self.dependencies.source.video(url)
        with self.dependencies.database.session() as session:
            channel = session.get(SourceChannelRecord, channel_id)
            if channel is None:
                raise LookupError("Source Channel was not found")
            return self._import_source_candidate(
                session,
                channel,
                source_candidate,
                rationale=_RATIONALE_OWNER_IMPORT,
            )

    def _import_source_candidate(
        self,
        session: Session,
        channel: SourceChannelRecord,
        source_candidate: SourceCandidate,
        *,
        rationale: str,
    ) -> CandidateVideoRecord:
        if (
            source_candidate.youtube_channel_id is not None
            and source_candidate.youtube_channel_id
            != channel.youtube_channel_id
        ):
            raise ValueError(
                "The Supported Source Video belongs to a different Source Channel"
            )
        existing = session.scalar(
            select(CandidateVideoRecord).where(
                CandidateVideoRecord.youtube_video_id
                == source_candidate.youtube_video_id
            )
        )
        if existing is not None:
            return existing
        record = self._create_candidate_record(
            session,
            channel,
            source_candidate,
            TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=1.0,
                rationale=rationale,
                matched_interests=(),
            ),
        )
        if source_candidate.captions is None:
            record.enrichment_state = "pending"
        self._queue_processing(session, record, priority=10)
        return record

    def discover(self) -> dict[str, int]:
        counts = {
            "discovered": 0,
            "automatically_accepted": 0,
            "awaiting_decision": 0,
            "ignored": 0,
        }
        with self.dependencies.database.session() as session:
            manual_processing = self._manual_processing_enabled(session)
            channels = session.scalars(
                select(SourceChannelRecord).where(
                    SourceChannelRecord.mode != ChannelMode.PAUSED.value
                )
            ).all()
            for channel in channels:
                for source_candidate in self.dependencies.source.discover(
                    channel.youtube_channel_id
                ):
                    if _utc_instant(
                        source_candidate.published_at
                    ) <= _utc_instant(channel.created_at):
                        continue
                    existing = session.scalar(
                        select(CandidateVideoRecord).where(
                            CandidateVideoRecord.youtube_video_id
                            == source_candidate.youtube_video_id
                        )
                    )
                    if existing is not None:
                        continue
                    triage = self._triage(channel, source_candidate)
                    await_manual_recommendation = bool(
                        manual_processing
                        and channel.mode
                        == ChannelMode.AGENT_FILTERING.value
                        and triage.decision is TriageDecision.PROCESS
                    )
                    candidate_status = _candidate_status(
                        triage.decision,
                        await_manual_recommendation=(
                            await_manual_recommendation
                        ),
                    )
                    status_key = _discovery_count_key(candidate_status)
                    counts["discovered"] += 1
                    counts[status_key] += 1
                    record = self._create_candidate_record(
                        session,
                        channel,
                        source_candidate,
                        triage,
                        status=candidate_status,
                    )
                    if triage.decision is TriageDecision.PROCESS:
                        if await_manual_recommendation:
                            self._record_candidate_recommendation(
                                session,
                                record,
                            )
                        else:
                            self._queue_processing(
                                session,
                                record,
                                priority=30,
                            )
        self.deliver_pending_candidate_recommendations()
        return counts

    def run_manual_discovery(self) -> dict[str, int]:
        automation = cast(dict[str, object], self.settings()["automation"])
        interval_minutes = int(
            str(automation["discovery_interval_minutes"])
        )
        lease_owner = f"manual-{uuid4().hex}"
        if not self.claim_scheduled_discovery(
            lease_owner,
            interval_minutes,
            force=True,
        ):
            raise DiscoveryAlreadyRunningError(
                "Source Channel discovery is active"
            )
        error_summary: str | None = None
        try:
            return self.discover()
        except Exception as error:
            error_summary = f"{type(error).__name__}: {error}"[:500]
            raise
        finally:
            self.complete_scheduled_discovery(
                lease_owner,
                interval_minutes,
                error=error_summary,
            )

    def claim_scheduled_discovery(
        self,
        lease_owner: str,
        interval_minutes: int,
        *,
        force: bool = False,
    ) -> bool:
        """Claim one due discovery without relying on process memory."""
        now = self.dependencies.clock.now()
        now_utc = _utc_instant(now)
        with self.dependencies.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(SettingRecord, _DISCOVERY_SCHEDULE_KEY)
            state = _json_mapping(row.value_json) if row is not None else {}
            next_due = _optional_iso_datetime(state.get("next_due_at"))
            lease_expires = _optional_iso_datetime(
                state.get("lease_expires_at")
            )
            active_owner = str(state.get("lease_owner") or "")
            if (
                active_owner
                and lease_expires is not None
                and now_utc < _utc_instant(lease_expires)
            ):
                return False
            if (
                not force
                and next_due is not None
                and now_utc < _utc_instant(next_due)
            ):
                return False
            updated = {
                **state,
                "last_started_at": now.isoformat(),
                "lease_owner": lease_owner,
                "lease_expires_at": (
                    now + timedelta(hours=_DISCOVERY_LEASE_HOURS)
                ).isoformat(),
                "interval_minutes": interval_minutes,
                "last_error": None,
            }
            self._write_setting(
                session,
                _DISCOVERY_SCHEDULE_KEY,
                updated,
            )
            return True

    def complete_scheduled_discovery(
        self,
        lease_owner: str,
        interval_minutes: int,
        *,
        error: str | None = None,
    ) -> None:
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(SettingRecord, _DISCOVERY_SCHEDULE_KEY)
            if row is None:
                return
            state = _json_mapping(row.value_json)
            if str(state.get("lease_owner") or "") != lease_owner:
                return
            retry_minutes = (
                _DISCOVERY_RETRY_MINUTES if error else interval_minutes
            )
            updated = {
                **state,
                "lease_owner": None,
                "lease_expires_at": None,
                "next_due_at": (
                    now + timedelta(minutes=retry_minutes)
                ).isoformat(),
                "last_error": error,
                "interval_minutes": interval_minutes,
            }
            if error is None:
                updated["last_successful_at"] = now.isoformat()
            self._write_setting(
                session,
                _DISCOVERY_SCHEDULE_KEY,
                updated,
            )

    def _create_candidate_record(
        self,
        session: Session,
        channel: SourceChannelRecord,
        source_candidate: SourceCandidate,
        triage: TriageResult,
        *,
        status: str | None = None,
    ) -> CandidateVideoRecord:
        record = CandidateVideoRecord(
            source_channel_id=channel.id,
            youtube_video_id=source_candidate.youtube_video_id,
            title=source_candidate.title,
            description=source_candidate.description,
            duration_seconds=source_candidate.duration_seconds,
            language=source_candidate.language,
            published_at=source_candidate.published_at,
            source_url=source_candidate.source_url,
            captions=source_candidate.captions,
            status=status
            or _candidate_status(
                triage.decision,
                await_manual_recommendation=False,
            ),
            triage_decision=triage.decision.value,
            triage_confidence=triage.confidence,
            triage_rationale=triage.rationale,
            matched_interests_json=json.dumps(triage.matched_interests),
            title_zh=triage.title_zh,
            summary_zh=triage.summary_zh,
            recommendation_zh=triage.recommendation_zh,
            created_at=self.dependencies.clock.now(),
        )
        session.add(record)
        session.flush()
        return record

    def _queue_processing(
        self,
        session: Session,
        candidate: CandidateVideoRecord,
        *,
        priority: int,
        start_immediately: bool = False,
    ) -> ProcessingRunRecord:
        run = ProcessingRunRecord(
            candidate_video_id=candidate.id,
            status=(
                "pending_start"
                if self._manual_processing_enabled(session)
                and not start_immediately
                else "queued"
            ),
            stage="ingest",
            priority=priority,
            pipeline_version="transpodcast-v1",
            started_at=self.dependencies.clock.now(),
        )
        session.add(run)
        session.flush()
        self._record_activity(
            session,
            run,
            kind="queued",
            stage=run.stage,
            summary="Processing Run entered the queue.",
        )
        return run

    def _manual_processing_enabled(self, session: Session) -> bool:
        row = session.get(SettingRecord, "manual_processing")
        if row is None:
            return bool(_default_settings()["manual_processing"])
        return bool(json.loads(row.value_json))

    def processing_queue(self) -> list[dict[str, object]]:
        with self.dependencies.database.session() as session:
            rows = session.execute(
                select(
                    ProcessingRunRecord,
                    CandidateVideoRecord,
                    SourceChannelRecord,
                )
                .join(
                    CandidateVideoRecord,
                    CandidateVideoRecord.id
                    == ProcessingRunRecord.candidate_video_id,
                )
                .join(
                    SourceChannelRecord,
                    SourceChannelRecord.id
                    == CandidateVideoRecord.source_channel_id,
                )
                .where(
                    ProcessingRunRecord.status.in_(
                        (
                            "pending_start",
                            "queued",
                            "retry_wait",
                            "processing",
                        )
                    )
                )
                .order_by(
                    ProcessingRunRecord.priority,
                    ProcessingRunRecord.started_at,
                    ProcessingRunRecord.id,
                )
            ).all()
            return [
                {
                    "processing_run_id": run.id,
                    "candidate_video_id": candidate.id,
                    "youtube_video_id": candidate.youtube_video_id,
                    "title": candidate.title,
                    "source_show": channel.title,
                    "duration_seconds": candidate.duration_seconds,
                    "status": run.status,
                    "stage": run.stage,
                    "origin": _candidate_origin(candidate.triage_rationale),
                    "created_at": run.started_at,
                }
                for run, candidate, channel in rows
            ]

    def start_processing_run(self, run_id: int) -> ProcessingRunRecord:
        with self.dependencies.database.session() as session:
            run = session.get(ProcessingRunRecord, run_id)
            if run is None:
                raise LookupError("Processing Run was not found")
            if run.status != "pending_start":
                raise RuntimeError(
                    "Processing Run is not waiting for a manual start"
                )
            run.status = "queued"
            return run

    def cancel_processing_run(self, run_id: int) -> dict[str, object]:
        """Remove a Processing Run that has not started any real work.

        The Candidate Video is deleted with the run so the video can be
        queued again later; a candidate that already carries decision
        evidence is moved to the Ignored List instead of being deleted.
        """
        with self.dependencies.database.session() as session:
            run = session.get(ProcessingRunRecord, run_id)
            if run is None:
                raise LookupError("Processing Run was not found")
            if (
                run.status not in ("pending_start", "queued")
                or run.first_claimed_at is not None
                or run.source_archive_key is not None
                or session.scalar(
                    select(ProcessingAttemptRecord.id)
                    .where(ProcessingAttemptRecord.processing_run_id == run.id)
                    .limit(1)
                )
                is not None
                or session.scalar(
                    select(ProcessingStageRecord.id)
                    .where(ProcessingStageRecord.processing_run_id == run.id)
                    .limit(1)
                )
                is not None
            ):
                raise RuntimeError(
                    "Only a Processing Run that has not started can be removed"
                )
            candidate = session.get(
                CandidateVideoRecord,
                run.candidate_video_id,
            )
            session.execute(
                delete(ProcessingRunActivityRecord).where(
                    ProcessingRunActivityRecord.processing_run_id == run.id
                )
            )
            session.delete(run)
            outcome = "removed"
            if candidate is not None:
                has_evidence = (
                    session.scalar(
                        select(LabeledExampleRecord.id)
                        .where(
                            LabeledExampleRecord.candidate_video_id
                            == candidate.id
                        )
                        .limit(1)
                    )
                    is not None
                    or session.scalar(
                        select(TelegramActionRecord.id)
                        .where(
                            TelegramActionRecord.candidate_video_id
                            == candidate.id
                        )
                        .limit(1)
                    )
                    is not None
                )
                if has_evidence:
                    candidate.status = "ignored"
                    outcome = "ignored"
                else:
                    session.delete(candidate)
            return {"id": run_id, "state": outcome}

    def start_all_processing_runs(self) -> dict[str, int]:
        with self.dependencies.database.session() as session:
            runs = session.scalars(
                select(ProcessingRunRecord).where(
                    ProcessingRunRecord.status == "pending_start"
                )
            ).all()
            for run in runs:
                run.status = "queued"
            return {"started": len(runs)}

    def enrich_candidate_videos(self, limit: int = 3) -> dict[str, int]:
        """Complete deferred metadata for explicitly imported videos.

        Only Candidate Videos marked ``pending`` are enriched, so
        unselected Channel Backfill previews never trigger YouTube work.
        """
        enriched = 0
        failed = 0
        with self.dependencies.database.session() as session:
            rows = session.scalars(
                select(CandidateVideoRecord)
                .where(CandidateVideoRecord.enrichment_state == "pending")
                .order_by(CandidateVideoRecord.created_at)
                .limit(limit)
            ).all()
            for row in rows:
                try:
                    full = self.dependencies.source.video(row.source_url)
                except (RuntimeError, ValueError):
                    row.enrichment_state = "failed"
                    failed += 1
                    continue
                row.title = full.title or row.title
                row.description = full.description or row.description
                row.duration_seconds = (
                    full.duration_seconds or row.duration_seconds
                )
                row.language = full.language or row.language
                row.published_at = full.published_at
                row.captions = full.captions
                row.enrichment_state = "complete"
                enriched += 1
            remaining = len(
                session.scalars(
                    select(CandidateVideoRecord.id).where(
                        CandidateVideoRecord.enrichment_state == "pending"
                    )
                ).all()
            )
        return {
            "enriched": enriched,
            "failed": failed,
            "pending": remaining,
        }

    def _triage(
        self,
        channel: SourceChannelRecord,
        candidate: SourceCandidate,
    ) -> TriageResult:
        duration_guardrail = int(
            self.settings()["automation"]["duration_guardrail_minutes"]  # type: ignore[index]
        )
        if candidate.duration_seconds > duration_guardrail * 60:
            return TriageResult(
                decision=TriageDecision.OWNER_DECISION,
                confidence=1.0,
                rationale="The video exceeds the Duration Guardrail.",
                matched_interests=(),
            )
        if not candidate.language.lower().startswith("en"):
            return TriageResult(
                decision=TriageDecision.OWNER_DECISION,
                confidence=1.0,
                rationale=(
                    "The Source Language is outside the automatic "
                    "English-to-Mandarin quality boundary."
                ),
                matched_interests=(),
            )
        if channel.mode == ChannelMode.PROCESS_EVERY_VIDEO.value:
            return TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=1.0,
                rationale="The Source Channel processes every video.",
                matched_interests=(),
            )
        if candidate.captions is None:
            return TriageResult(
                decision=TriageDecision.OWNER_DECISION,
                confidence=0.0,
                rationale="Available Triage Evidence is insufficient.",
                matched_interests=(),
            )
        return self.dependencies.triage.evaluate(
            candidate,
            self._triage_context(),
        )

    def _active_interest_profile(self) -> str:
        with self.dependencies.database.session() as session:
            return self._get_or_create_interest_profile(session).markdown

    def _triage_context(self) -> str:
        profile = self._active_interest_profile()
        with self.dependencies.database.session() as session:
            examples = session.execute(
                select(
                    LabeledExampleRecord.label,
                    CandidateVideoRecord.title,
                    CandidateVideoRecord.description,
                )
                .join(
                    CandidateVideoRecord,
                    CandidateVideoRecord.id
                    == LabeledExampleRecord.candidate_video_id,
                )
                .order_by(LabeledExampleRecord.created_at.desc())
                .limit(50)
            ).all()
        if not examples:
            return profile
        rendered = "\n".join(
            (f"- {label}: {title} — {str(description)[:240]}")
            for label, title, description in examples
        )
        return f"{profile}\n\n# Labeled Examples\n\n{rendered}"

    def _get_or_create_interest_profile(
        self,
        session: Session,
    ) -> InterestProfileRevisionRecord:
        profile = session.scalar(
            select(InterestProfileRevisionRecord).where(
                InterestProfileRevisionRecord.active.is_(True)
            )
        )
        if profile is not None:
            return profile
        profile = InterestProfileRevisionRecord(
            version=1,
            markdown=(
                "# Interests\n\n"
                "Product strategy, durable companies, and technology.\n\n"
                "# Exclusions\n\n"
                "Pure entertainment and obvious promotions."
            ),
            active=True,
            source="owner",
            created_at=self.dependencies.clock.now(),
        )
        session.add(profile)
        session.flush()
        return profile

    def advance_next_processing_run(self) -> dict[str, object] | None:
        transcript_job_id = self._claim_next_transcript_revision()
        if transcript_job_id is not None:
            return self._execute_transcript_revision(transcript_job_id)
        revision_job_id = self._claim_next_segment_revision()
        if revision_job_id is not None:
            return self._execute_segment_revision(revision_job_id)
        run_id = self._claim_next_processing_run()
        if run_id is None:
            return None
        return self._execute_processing_run(run_id)

    def recover_interrupted_processing(self) -> int:
        """Return work owned by a terminated Processing Service to queue."""
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            runs = session.scalars(
                select(ProcessingRunRecord).where(
                    ProcessingRunRecord.status == "processing"
                )
            ).all()
            for run in runs:
                active_attempts = session.scalars(
                    select(ProcessingAttemptRecord).where(
                        ProcessingAttemptRecord.processing_run_id == run.id,
                        ProcessingAttemptRecord.status == "processing",
                    )
                ).all()
                for attempt in active_attempts:
                    attempt.status = "interrupted"
                    attempt.completed_at = now
                    attempt.duration_ms = _wall_duration_ms(
                        attempt.created_at,
                        now,
                    )
                    self._record_activity(
                        session,
                        run,
                        kind="stage_interrupted",
                        stage=attempt.stage,
                        summary=(
                            f"{attempt.stage} interrupted by service restart"
                        ),
                        metadata={
                            "correlation_id": attempt.correlation_id,
                            "recovery": "processing_service_restart",
                        },
                        attempt_id=attempt.id,
                    )
                run.status = "queued"
                run.lease_expires_at = None
            return len(runs)

    def _claim_next_transcript_revision(self) -> int | None:
        if self._spend_guardrail_paused():
            return None
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            job = session.scalar(
                select(TranscriptRevisionJobRecord)
                .where(
                    or_(
                        TranscriptRevisionJobRecord.status == "queued",
                        and_(
                            TranscriptRevisionJobRecord.status == "processing",
                            TranscriptRevisionJobRecord.lease_expires_at
                            <= now,
                        ),
                    )
                )
                .order_by(TranscriptRevisionJobRecord.created_at)
            )
            if job is None:
                return None
            job.status = "processing"
            job.lease_expires_at = now + timedelta(hours=25)
            return job.id

    def _claim_next_segment_revision(self) -> int | None:
        if self._spend_guardrail_paused():
            return None
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            job = session.scalar(
                select(SegmentRevisionJobRecord)
                .where(
                    or_(
                        SegmentRevisionJobRecord.status == "queued",
                        and_(
                            SegmentRevisionJobRecord.status == "processing",
                            SegmentRevisionJobRecord.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(SegmentRevisionJobRecord.created_at)
            )
            if job is None:
                return None
            job.status = "processing"
            job.lease_expires_at = now + timedelta(hours=25)
            return job.id

    def _claim_next_processing_run(self) -> int | None:
        if self._spend_guardrail_paused():
            return None
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            run = session.scalar(
                select(ProcessingRunRecord)
                .where(
                    or_(
                        ProcessingRunRecord.status == "queued",
                        and_(
                            ProcessingRunRecord.status == "retry_wait",
                            ProcessingRunRecord.next_retry_at <= now,
                        ),
                        and_(
                            ProcessingRunRecord.status == "processing",
                            ProcessingRunRecord.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(
                    ProcessingRunRecord.priority,
                    ProcessingRunRecord.started_at,
                )
            )
            if run is None:
                return None
            run.status = "processing"
            run.lease_expires_at = now + timedelta(hours=25)
            if run.first_claimed_at is None:
                run.first_claimed_at = now
            return run.id

    def _execute_processing_run(
        self,
        processing_run_id: int,
    ) -> dict[str, object]:
        with self.dependencies.database.session() as session:
            run = session.get(ProcessingRunRecord, processing_run_id)
            if run is None:
                raise RuntimeError("Claimed Processing Run is missing")
            candidate = session.get(
                CandidateVideoRecord,
                run.candidate_video_id,
            )
            if candidate is None:
                raise RuntimeError("Processing Run candidate is missing")
            source = SourceCandidate(
                youtube_video_id=candidate.youtube_video_id,
                title=candidate.title,
                description=candidate.description,
                duration_seconds=candidate.duration_seconds,
                language=candidate.language,
                published_at=candidate.published_at,
                source_url=candidate.source_url,
                captions=candidate.captions,
                analysis_json=run.analysis_json,
                processing_run_id=run.id,
            )
            if run.source_archive_key is None:
                source_archive_attempt = self._begin_stage_attempt(
                    session,
                    run,
                    "source_archive",
                )
                source = replace(
                    source,
                    processing_attempt_id=source_archive_attempt.id,
                    processing_correlation_id=(
                        source_archive_attempt.correlation_id
                    ),
                )
                session.commit()
                stage_started = time.monotonic()
                if run.artwork_status == "pending":
                    self._start_episode_artwork(run.id, source)
                try:
                    archive = self.dependencies.source.archive(source)
                    checksum = hashlib.sha256(archive.content).hexdigest()
                    if checksum != archive.checksum:
                        raise ValueError(
                            "Source Archive checksum does not match content"
                        )
                    source_key = (
                        f"{candidate.youtube_video_id}/"
                        f"source.{archive.extension}"
                    )
                    self.dependencies.storage.put_source_archive(
                        source_key,
                        archive.content,
                    )
                    stored = self.dependencies.storage.read_source_archive(
                        source_key
                    )
                    if (
                        stored is None
                        or hashlib.sha256(stored).hexdigest() != checksum
                    ):
                        raise ValueError(
                            "Source Archive could not be verified in storage"
                        )
                except ValueError as error:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "source_integrity",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                except RuntimeError as error:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "source_provider",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                run.source_archive_key = source_key
                run.source_archive_checksum = checksum
                stage_duration = _elapsed_ms(stage_started)
                self._record_attempt(
                    run,
                    status="completed",
                    duration_ms=stage_duration,
                    transfer_bytes=len(archive.content),
                )
                self._record_stage(
                    session,
                    run,
                    stage="source_archive",
                    version="yt-dlp-source-v1",
                    provider="youtube",
                    model=None,
                    input_hash=_content_hash(
                        {
                            "video_id": candidate.youtube_video_id,
                            "source_url": candidate.source_url,
                        }
                    ),
                    output_hash=checksum,
                    usage={"archive_bytes": len(archive.content)},
                    duration_ms=stage_duration,
                )
                run.stage = "source_analysis"
                session.commit()
            self._collect_episode_artwork(
                session,
                run,
                source,
                wait=False,
            )
            session.commit()
            stored_archive = self.dependencies.storage.read_source_archive(
                str(run.source_archive_key)
            )
            if stored_archive is None or run.source_archive_checksum is None:
                return self._fail_processing_run(
                    run,
                    candidate,
                    "source_integrity",
                    "Source Archive checkpoint is unavailable",
                )
            archive_extension = str(run.source_archive_key).rsplit(".", 1)[-1]
            archive = SourceArchive(
                content=stored_archive,
                extension=archive_extension,
                media_type=(
                    "audio/mp4" if archive_extension == "m4a" else "audio/webm"
                ),
                checksum=run.source_archive_checksum,
            )
            source = replace(source, source_archive=archive)
            if run.analysis_json is None or run.stage == "source_analysis":
                source_analysis_attempt = self._begin_stage_attempt(
                    session,
                    run,
                    "source_analysis",
                )
                source = replace(
                    source,
                    processing_attempt_id=source_analysis_attempt.id,
                    processing_correlation_id=(
                        source_analysis_attempt.correlation_id
                    ),
                )
                session.commit()
                stage_started = time.monotonic()
                checkpoint_reference_paths: tuple[Path, ...] = ()
                analysis_routing_json = "{}"
                if run.analysis_json is None:
                    try:
                        analysis = (
                            self.dependencies.analysis.analyze(source, archive)
                            if self.dependencies.analysis is not None
                            else _caption_analysis(source)
                        )
                        analysis_routing_json = analysis.routing_json
                        source_analysis_attempt.routing_json = (
                            analysis_routing_json
                        )
                        if not analysis.segments:
                            raise ValueError(
                                "Canonical Transcript has no speech segments"
                            )
                        analysis = _normalize_legacy_sponsor_flags(analysis)
                    except SpendGuardrailReached as error:
                        return self._pause_for_spend_guardrail(
                            session,
                            run,
                            candidate,
                            str(error),
                            duration_ms=_elapsed_ms(stage_started),
                            routing_json=error.routing_json,
                        )
                    except TransientProviderError as error:
                        return self._schedule_transient_retry(
                            run,
                            candidate,
                            str(error),
                            duration_ms=_elapsed_ms(stage_started),
                            routing_json=error.routing_json,
                        )
                    except ValueError as error:
                        return self._fail_processing_run(
                            run,
                            candidate,
                            "source_analysis_schema",
                            str(error),
                            duration_ms=_elapsed_ms(stage_started),
                        )
                    except RuntimeError as error:
                        return self._fail_processing_run(
                            run,
                            candidate,
                            "source_analysis_provider",
                            str(error),
                            duration_ms=_elapsed_ms(stage_started),
                            routing_json=getattr(error, "routing_json", None),
                        )
                    # The expensive acoustic result is a durable checkpoint
                    # before the local discourse completion runs. A local
                    # bug can therefore be fixed and retried without another
                    # Modal analysis call.
                    analysis = self._checkpoint_analysis_references(
                        run,
                        analysis,
                    )
                    checkpoint_reference_paths = tuple(
                        Path(speaker.reference_artifact_key)
                        for speaker in analysis.speakers
                        if speaker.reference_audio
                        and speaker.reference_artifact_key
                    )
                    run.analysis_json = _stamp_analysis_checksum(
                        _serialize_analysis(analysis)
                    )
                    session.commit()
                else:
                    try:
                        analysis = deserialize_analysis(
                            str(run.analysis_json),
                            artifact_root=self.dependencies.artifact_root,
                        )
                        analysis = _normalize_legacy_sponsor_flags(analysis)
                        checkpoint_reference_paths = tuple(
                            Path(speaker.reference_artifact_key)
                            for speaker in analysis.speakers
                            if speaker.reference_audio
                            and speaker.reference_artifact_key
                        )
                    except (OSError, ValueError) as error:
                        return self._fail_processing_run(
                            run,
                            candidate,
                            "adjudication_completion",
                            f"{type(error).__name__}: {error}"[:500],
                            duration_ms=_elapsed_ms(stage_started),
                        )
                try:
                    completion = self._complete_boundary_adjudication(
                        session,
                        run,
                        analysis,
                    )
                except Exception as error:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "adjudication_completion",
                        f"{type(error).__name__}: {error}"[:500],
                        duration_ms=_elapsed_ms(stage_started),
                    )
                analysis = completion.analysis
                try:
                    analysis = self._resolve_analysis_speakers(
                        session,
                        candidate,
                        analysis,
                    )
                except ValueError as error:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "source_analysis_schema",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                except RuntimeError as error:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "source_analysis_provider",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                run.analysis_json = _serialize_analysis(analysis)
                self._persist_decision_ledger(session, run)
                run.analysis_json = _stamp_analysis_checksum(
                    str(run.analysis_json)
                )
                analysis = deserialize_analysis(str(run.analysis_json))
                run.estimated_cost_usd += analysis.estimated_cost_usd
                stage_duration = _elapsed_ms(stage_started)
                self._record_attempt(
                    run,
                    status="completed",
                    duration_ms=stage_duration,
                    transfer_bytes=len(archive.content),
                    routing_json=analysis_routing_json,
                )
                stage_usage: dict[str, object] = {
                    "estimated_cost_usd": (analysis.estimated_cost_usd),
                    "upload_bytes": len(archive.content),
                    "call_seconds": dict(analysis.call_seconds),
                    "diarization_model_revision": (
                        analysis.diarization_model_revision
                    ),
                }
                checkpoint_routing = _json_mapping(analysis.routing_json)
                if checkpoint_routing:
                    stage_usage["routing"] = checkpoint_routing
                if completion.completed:
                    stage_usage["discourse_input_tokens"] = (
                        completion.input_tokens
                    )
                    stage_usage["discourse_output_tokens"] = (
                        completion.output_tokens
                    )
                    stage_usage["discourse_provider"] = completion.provider
                    stage_usage["discourse_model"] = completion.model
                    stage_usage["discourse_cli_version"] = (
                        completion.cli_version
                    )
                diarization_identity = analysis.diarization_model
                if analysis.diarization_model_revision:
                    diarization_identity += (
                        f"@{analysis.diarization_model_revision}"
                    )
                self._record_stage(
                    session,
                    run,
                    stage="source_analysis",
                    version=analysis.pipeline_version,
                    provider="modal",
                    model=(
                        f"{diarization_identity};"
                        f"{analysis.transcription_model};"
                        f"{analysis.alignment_model}"
                    ),
                    input_hash=archive.checksum,
                    output_hash=analysis.checksum,
                    usage=stage_usage,
                    duration_ms=stage_duration,
                )
                run.stage = "translation"
                session.commit()
                self._discard_analysis_reference_checkpoints(
                    run,
                    checkpoint_reference_paths,
                )
            analysis = deserialize_analysis(str(run.analysis_json))
            retained_analysis = _normalize_legacy_sponsor_flags(analysis)
            if retained_analysis != analysis:
                analysis = retained_analysis
                run.analysis_json = _stamp_analysis_checksum(
                    _serialize_analysis(analysis)
                )
            if run.translation_json is not None:
                checkpoint_translation = deserialize_translation(
                    run.translation_json
                )
                if len(checkpoint_translation.segments) != len(
                    analysis.segments
                ):
                    mark_translation_project_reprocess(
                        self.dependencies.artifact_root,
                        run.id,
                        checkpoint_translation.segments,
                    )
                    run.translation_json = None
                    run.content_edit_json = None
                    run.translation_provider = None
                    run.translation_model = None
                    run.input_tokens = 0
                    run.output_tokens = 0
                    run.render_json = None
                    run.render_preview_key = None
                    run.stage = "translation"
                    session.execute(
                        delete(ProcessingStageRecord).where(
                            ProcessingStageRecord.processing_run_id == run.id,
                            ProcessingStageRecord.stage.in_(
                                ("translation", "render", "quality_gate")
                            ),
                        )
                    )
                    self._record_activity(
                        session,
                        run,
                        kind="checkpoint_invalidated",
                        stage="translation",
                        summary=(
                            "The translation checkpoint did not cover all "
                            "spoken segments and will be rebuilt."
                        ),
                        metadata={
                            "translated_segments": len(
                                checkpoint_translation.segments
                            ),
                            "spoken_segments": len(analysis.segments),
                            "policy": "all-spoken-content",
                        },
                    )
            session.commit()
            source = replace(
                source,
                analysis_json=run.analysis_json,
                processing_run_id=run.id,
            )
            if run.translation_json is None:
                translation_attempt = self._begin_stage_attempt(
                    session,
                    run,
                    "translation",
                )
                source = replace(
                    source,
                    processing_attempt_id=translation_attempt.id,
                    processing_correlation_id=(
                        translation_attempt.correlation_id
                    ),
                )
                session.commit()
                stage_started = time.monotonic()
                try:
                    translation = self.dependencies.translation.translate(
                        source
                    )
                except TranslationStalledError as error:
                    run.input_tokens = error.input_tokens
                    run.output_tokens = error.output_tokens
                    self._sync_translation_project_activity(session, run)
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "translation_stalled",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                except TranslationProviderError as error:
                    run.input_tokens = error.input_tokens
                    run.output_tokens = error.output_tokens
                    self._sync_translation_project_activity(session, run)
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "translation_provider",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                except ValueError as error:
                    self._restore_translation_usage(run)
                    self._sync_translation_project_activity(session, run)
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "translation_schema",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                except RuntimeError as error:
                    self._restore_translation_usage(run)
                    self._sync_translation_project_activity(session, run)
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "translation_provider",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                run.translation_provider = translation.provider
                run.translation_model = translation.model
                run.input_tokens = translation.input_tokens
                run.output_tokens = translation.output_tokens
                run.translation_json = _serialize_translation(translation)
                run.content_edit_json = translation.content_edit_json
                run.publication_metadata_json = (
                    translation.publication_metadata_json
                )
                self._sync_translation_project_activity(session, run)
                self._record_attempt(
                    run,
                    status="completed",
                    duration_ms=_elapsed_ms(stage_started),
                )
                self._record_stage(
                    session,
                    run,
                    stage="translation",
                    version=translation.policy_version,
                    provider=translation.provider,
                    model=translation.model,
                    input_hash=translation.input_hash
                    or str(run.source_archive_checksum),
                    output_hash=translation.output_hash
                    or _content_hash(translation.segments),
                    usage={
                        "input_tokens": translation.input_tokens,
                        "output_tokens": translation.output_tokens,
                        "elapsed_ms": translation.elapsed_ms,
                        "reviewed": translation.reviewed,
                        "review_model": translation.review_model,
                        "codex_cli_version": translation.cli_version,
                        "content_edit_policy_version": (
                            CONTENT_EDIT_POLICY_VERSION
                        ),
                        "content_edit_artifact_hash": _content_hash(
                            translation.content_edit_json
                        ),
                        "publication_metadata_policy_version": (
                            "publication-metadata-v1"
                        ),
                        "publication_metadata_artifact_hash": _content_hash(
                            translation.publication_metadata_json
                        ),
                    },
                    duration_ms=translation.elapsed_ms,
                )
                run.stage = "render"
                session.commit()
            else:
                translation = deserialize_translation(
                    run.translation_json,
                    run.content_edit_json,
                    run.publication_metadata_json,
                )
            render_plan = build_render_plan(analysis, translation)
            self._record_content_edit_activity(session, run, render_plan)
            session.commit()
            render_source = replace(
                source,
                analysis_json=_serialize_analysis(render_plan.analysis),
            )
            render_translation = render_plan.translation
            if (
                run.content_edit_json is not None
                and run.render_json is not None
                and run.render_preview_key is not None
            ):
                render_stage = session.scalar(
                    select(ProcessingStageRecord)
                    .where(
                        ProcessingStageRecord.processing_run_id == run.id,
                        ProcessingStageRecord.stage == "render",
                    )
                    .order_by(ProcessingStageRecord.id.desc())
                    .limit(1)
                )
                if (
                    render_stage is None
                    or render_stage.input_hash
                    != _render_plan_input_hash(translation, render_plan)
                ):
                    run.render_json = None
                    run.render_preview_key = None
            requires_mastering_repair = (
                run.stage == "mastering_repair"
                and run.render_json is not None
                and run.render_preview_key is not None
            )
            if requires_mastering_repair:
                self._begin_stage_attempt(
                    session,
                    run,
                    "mastering_repair",
                )
                session.commit()
                stage_started = time.monotonic()
                rendered_audio = self.dependencies.storage.read_preview(
                    str(run.render_preview_key)
                )
                if rendered_audio is None:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "render_integrity",
                        "Rendered audio checkpoint is unavailable",
                    )
                prior_speech = _deserialize_speech_checkpoint(
                    str(run.render_json),
                    rendered_audio,
                )
                try:
                    speech = self.dependencies.speech.remaster(prior_speech)
                except RuntimeError as error:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "mastering_provider",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                    )
                audio_hash = hashlib.sha256(
                    speech.publication_audio
                ).hexdigest()
                render_key = f"processing-runs/{run.id}/remaster-{uuid4()}.m4a"
                self.dependencies.storage.put_preview(
                    render_key,
                    speech.publication_audio,
                )
                run.pipeline_version = speech.pipeline_version
                run.render_preview_key = render_key
                run.render_json = _serialize_speech_checkpoint(speech)
                stage_duration = _elapsed_ms(stage_started)
                self._record_attempt(
                    run,
                    status="completed",
                    duration_ms=stage_duration,
                    transfer_bytes=len(speech.publication_audio),
                )
                self._record_stage(
                    session,
                    run,
                    stage="mastering_repair",
                    version=speech.pipeline_version,
                    provider="ffmpeg",
                    model=None,
                    input_hash=hashlib.sha256(rendered_audio).hexdigest(),
                    output_hash=audio_hash,
                    usage={
                        "rendered_segments": (
                            speech.quality.rendered_segments
                        ),
                        "audio_bytes": len(speech.publication_audio),
                    },
                    duration_ms=stage_duration,
                )
                run.stage = "quality_gate"
                session.commit()
            elif run.render_json is None or run.render_preview_key is None:
                render_attempt = self._begin_stage_attempt(
                    session,
                    run,
                    "render",
                )
                render_source = replace(
                    render_source,
                    processing_attempt_id=render_attempt.id,
                    processing_correlation_id=(
                        render_attempt.correlation_id
                    ),
                )
                session.commit()
                stage_started = time.monotonic()
                try:
                    speech = self.dependencies.speech.render(
                        render_source,
                        render_translation,
                    )
                except SpendGuardrailReached as error:
                    return self._pause_for_spend_guardrail(
                        session,
                        run,
                        candidate,
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                        routing_json=error.routing_json,
                    )
                except TransientProviderError as error:
                    return self._schedule_transient_retry(
                        run,
                        candidate,
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                        routing_json=error.routing_json,
                    )
                except RuntimeError as error:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "speech_provider",
                        str(error),
                        duration_ms=_elapsed_ms(stage_started),
                        routing_json=getattr(error, "routing_json", None),
                    )
                run.pipeline_version = speech.pipeline_version
                run.estimated_cost_usd += speech.estimated_cost_usd
                audio_hash = hashlib.sha256(
                    speech.publication_audio
                ).hexdigest()
                render_key = f"processing-runs/{run.id}/render-{uuid4()}.m4a"
                self.dependencies.storage.put_preview(
                    render_key,
                    speech.publication_audio,
                )
                run.render_preview_key = render_key
                run.render_json = _serialize_speech_checkpoint(speech)
                stage_duration = _elapsed_ms(stage_started)
                self._record_attempt(
                    run,
                    status="completed",
                    duration_ms=stage_duration,
                    transfer_bytes=len(speech.publication_audio),
                    routing_json=speech.routing_json,
                )
                self._record_stage(
                    session,
                    run,
                    stage="render",
                    version=speech.pipeline_version,
                    provider="modal",
                    model="IndexTTS2",
                    input_hash=_render_plan_input_hash(
                        translation,
                        render_plan,
                    ),
                    output_hash=audio_hash,
                    usage={
                        "estimated_cost_usd": speech.estimated_cost_usd,
                        "rendered_segments": (
                            speech.quality.rendered_segments
                        ),
                        "audio_bytes": len(speech.publication_audio),
                    },
                    duration_ms=stage_duration,
                )
                run.stage = "quality_gate"
                session.commit()
            else:
                rendered_audio = self.dependencies.storage.read_preview(
                    run.render_preview_key
                )
                if rendered_audio is None:
                    return self._fail_processing_run(
                        run,
                        candidate,
                        "render_integrity",
                        "Rendered audio checkpoint is unavailable",
                    )
                speech = _deserialize_speech_checkpoint(
                    run.render_json,
                    rendered_audio,
                )
                audio_hash = hashlib.sha256(rendered_audio).hexdigest()
            self._begin_stage_attempt(
                session,
                run,
                "quality_gate",
            )
            session.commit()
            stage_started = time.monotonic()
            try:
                self._validate_speech(
                    speech,
                    len(render_translation.segments),
                )
            except ValueError as error:
                self._record_attempt(
                    run,
                    status="failed",
                    duration_ms=_elapsed_ms(stage_started),
                    error_kind="quality_gate",
                    error_summary=str(error),
                )
                run.status = "review_queue"
                run.error_kind = "quality_gate"
                run.error_summary = str(error)
                run.lease_expires_at = None
                if self._notification_enabled("processing_failure"):
                    self.dependencies.notifications.send(
                        OwnerNotification(
                            kind="processing_failure",
                            title=candidate.title,
                            body=str(error),
                            actions=(),
                        )
                    )
                return {
                    "processing_run_id": run.id,
                    "processing_state": run.status,
                    "error_kind": run.error_kind,
                }
            quality_json = _serialize_quality(
                speech,
                len(render_translation.segments),
            )
            stage_duration = _elapsed_ms(stage_started)
            self._record_attempt(
                run,
                status="completed",
                duration_ms=stage_duration,
            )
            self._record_stage(
                session,
                run,
                stage="quality_gate",
                version="publication-audio-quality-v1",
                provider=None,
                model=None,
                input_hash=audio_hash,
                output_hash=_content_hash(json.loads(quality_json)),
                usage={},
                duration_ms=stage_duration,
            )

            self._collect_episode_artwork(
                session,
                run,
                source,
                wait=True,
            )

            try:
                publication_metadata = parse_publication_metadata(
                    run.publication_metadata_json or "{}"
                )
            except ValueError as error:
                publication_metadata = degraded_publication_metadata(
                    translation.overview,
                    str(error),
                )
                self._record_activity(
                    session,
                    run,
                    kind="stage_degraded",
                    stage="publication_metadata",
                    summary=(
                        "Publication Metadata fell back to the short overview"
                    ),
                    metadata={"error": str(error)[:500]},
                )
            publication_metadata_resolution = _resolve_publication_metadata(
                publication_metadata,
                tuple(
                    segment.segment_id
                    for segment in render_plan.analysis.segments
                ),
                speech.segment_timings,
                speech.duration_seconds,
            )
            run.publication_metadata_json = (
                publication_metadata.artifact_json()
            )

            episode = session.scalar(
                select(EpisodeRecord).where(
                    EpisodeRecord.candidate_video_id == candidate.id
                )
            )
            is_reprocess = episode is not None
            if episode is None:
                episode_guid = str(uuid4())
                next_revision = 1
                preview_key = f"episodes/{episode_guid}/v1/preview.m4a"
            else:
                episode_guid = episode.publication_guid
                next_revision = episode.revision_version + 1
                preview_key = (
                    f"episodes/{episode_guid}/"
                    f"reprocess-{next_revision}/preview.m4a"
                )
            self.dependencies.storage.put_preview(
                preview_key,
                speech.publication_audio,
            )
            source_channel = session.get(
                SourceChannelRecord,
                candidate.source_channel_id,
            )
            if episode is None:
                episode = EpisodeRecord(
                    candidate_video_id=candidate.id,
                    processing_run_id=run.id,
                    title=translation.title,
                    overview=translation.overview,
                    publication_description=(
                        publication_metadata_resolution.description
                    ),
                    chapter_proposals_json=(
                        publication_metadata_resolution.chapter_proposals_json
                    ),
                    state=EpisodeState.PUBLICATION_QUEUE.value,
                    duration_seconds=speech.duration_seconds,
                    target_locale=translation.target_locale,
                    source_show=(
                        source_channel.title
                        if source_channel is not None
                        else candidate.title
                    ),
                    source_title=candidate.title,
                    source_url=candidate.source_url,
                    source_published_at=candidate.published_at,
                    guests_json="[]",
                    transcript_json=run.analysis_json,
                    chapters_json=json.dumps(
                        publication_metadata_resolution.chapters
                    ),
                    quality_json=quality_json,
                    preview_key=preview_key,
                    preview_expires_at=(
                        self.dependencies.clock.now() + timedelta(days=7)
                    ),
                    publication_guid=episode_guid,
                    publication_version=0,
                    revision_version=next_revision,
                    revision_kind=PublicationRevisionKind.AUDIO.value,
                    artwork_path=run.artwork_path,
                    artwork_json=run.artwork_json,
                    created_at=self.dependencies.clock.now(),
                )
                session.add(episode)
                session.flush()
            else:
                episode.title = translation.title
                episode.overview = translation.overview
                episode.publication_description = (
                    publication_metadata_resolution.description
                )
                episode.chapter_proposals_json = (
                    publication_metadata_resolution.chapter_proposals_json
                )
                episode.state = (
                    EpisodeState.REVISION_PENDING.value
                    if episode.publication_key is not None
                    else EpisodeState.PUBLICATION_QUEUE.value
                )
                episode.duration_seconds = speech.duration_seconds
                episode.target_locale = translation.target_locale
                episode.transcript_json = run.analysis_json
                episode.chapters_json = json.dumps(
                    publication_metadata_resolution.chapters
                )
                episode.quality_json = quality_json
                episode.preview_key = preview_key
                episode.preview_expires_at = (
                    self.dependencies.clock.now() + timedelta(days=7)
                )
                episode.revision_version = next_revision
                episode.revision_kind = PublicationRevisionKind.AUDIO.value
                episode.artwork_path = run.artwork_path
                episode.artwork_json = run.artwork_json
            translated_analysis_segments = analysis.segments
            source_segments = tuple(
                segment.text for segment in translated_analysis_segments
            )
            if len(source_segments) != len(translation.segments):
                source_segments = (
                    (candidate.captions,)
                    if candidate.captions is not None
                    else ("Source transcript unavailable.",)
                )
            speaker_ids = {
                speaker.local_label: speaker.registry_speaker_id
                for speaker in analysis.speakers
            }
            if run.translation_json is None or run.analysis_json is None:
                raise RuntimeError("Processing artifacts are incomplete")
            translation_artifact_reference = persist_structured_artifact(
                session,
                artifact_kind="translation",
                payload=run.translation_json,
                created_at=self.dependencies.clock.now(),
            )
            transcript_artifact_reference = persist_structured_artifact(
                session,
                artifact_kind="canonical_transcript",
                payload=run.analysis_json,
                created_at=self.dependencies.clock.now(),
            )
            content_edit_payload = run.content_edit_json or "{}"
            content_edit_artifact_reference = persist_structured_artifact(
                session,
                artifact_kind="content_edit",
                payload=content_edit_payload,
                created_at=self.dependencies.clock.now(),
            )
            metadata_version = session.scalar(
                select(func.max(EpisodeMetadataRevisionRecord.version)).where(
                    EpisodeMetadataRevisionRecord.episode_id == episode.id
                )
            )
            session.add(
                EpisodeMetadataRevisionRecord(
                    episode_id=episode.id,
                    version=int(metadata_version or 0) + 1,
                    title=episode.title,
                    overview=episode.overview,
                    publication_description=(
                        episode.publication_description
                    ),
                    chapter_proposals_json=(
                        episode.chapter_proposals_json
                    ),
                    chapters_json=episode.chapters_json,
                    artwork_json=episode.artwork_json,
                    created_at=self.dependencies.clock.now(),
                )
            )
            session.add(
                EpisodeContentEditRevisionRecord(
                    episode_id=episode.id,
                    processing_run_id=run.id,
                    version=next_revision,
                    content_edit_json=content_edit_artifact_reference,
                    artifact_hash=_content_hash(content_edit_payload),
                    created_at=self.dependencies.clock.now(),
                )
            )
            current_segments = {
                segment.ordinal: segment
                for segment in session.scalars(
                    select(EpisodeSegmentRecord).where(
                        EpisodeSegmentRecord.episode_id == episode.id
                    )
                ).all()
            }
            segment_revision_versions = {
                int(ordinal): int(version)
                for ordinal, version in session.execute(
                    select(
                        EpisodeSegmentRevisionRecord.ordinal,
                        func.max(EpisodeSegmentRevisionRecord.version),
                    )
                    .where(
                        EpisodeSegmentRevisionRecord.episode_id == episode.id
                    )
                    .group_by(EpisodeSegmentRevisionRecord.ordinal)
                ).all()
            }
            transcript_revision_versions = {
                int(ordinal): int(version)
                for ordinal, version in session.execute(
                    select(
                        CanonicalTranscriptRevisionRecord.ordinal,
                        func.max(CanonicalTranscriptRevisionRecord.version),
                    )
                    .where(
                        CanonicalTranscriptRevisionRecord.episode_id
                        == episode.id
                    )
                    .group_by(CanonicalTranscriptRevisionRecord.ordinal)
                ).all()
            }
            for ordinal, target_text in enumerate(
                translation.segments,
                start=1,
            ):
                source_text = source_segments[
                    min(ordinal - 1, len(source_segments) - 1)
                ]
                speaker_id = speaker_ids.get(
                    translated_analysis_segments[
                        min(
                            ordinal - 1,
                            len(translated_analysis_segments) - 1,
                        )
                    ].speaker_label
                )
                current_segment = current_segments.pop(ordinal, None)
                segment_version = (
                    max(
                        current_segment.version,
                        segment_revision_versions.get(ordinal, 0),
                    )
                    + 1
                    if current_segment is not None
                    else segment_revision_versions.get(ordinal, 0) + 1
                )
                if current_segment is None:
                    current_segment = EpisodeSegmentRecord(
                        episode_id=episode.id,
                        ordinal=ordinal,
                        source_text=source_text,
                        target_text=target_text,
                        speaker_id=speaker_id,
                        version=segment_version,
                        created_at=self.dependencies.clock.now(),
                        updated_at=self.dependencies.clock.now(),
                    )
                    session.add(current_segment)
                else:
                    current_segment.source_text = source_text
                    current_segment.target_text = target_text
                    current_segment.speaker_id = speaker_id
                    current_segment.emotion_reference = None
                    current_segment.version = segment_version
                    current_segment.updated_at = self.dependencies.clock.now()
                session.add(
                    EpisodeSegmentRevisionRecord(
                        episode_id=episode.id,
                        ordinal=ordinal,
                        version=segment_version,
                        target_text=target_text,
                        speaker_id=speaker_id,
                        emotion_reference=None,
                        preview_key=preview_key,
                        translation_json=translation_artifact_reference,
                        created_at=self.dependencies.clock.now(),
                    )
                )
                session.add(
                    CanonicalTranscriptRevisionRecord(
                        episode_id=episode.id,
                        ordinal=ordinal,
                        version=(
                            transcript_revision_versions.get(ordinal, 0) + 1
                        ),
                        source_text=source_text,
                        transcript_json=transcript_artifact_reference,
                        created_at=self.dependencies.clock.now(),
                    )
                )
            for removed_segment in current_segments.values():
                session.delete(removed_segment)
            if is_reprocess:
                session.execute(
                    delete(ChannelVoiceCandidateRecord).where(
                        ChannelVoiceCandidateRecord.source_episode_id
                        == episode.id,
                        ChannelVoiceCandidateRecord.decision == "pending",
                    )
                )
            retained_speaker_segment_counts: dict[str, int] = {}
            for retained_segment in render_plan.analysis.segments:
                retained_speaker_segment_counts[
                    retained_segment.speaker_label
                ] = (
                    retained_speaker_segment_counts.get(
                        retained_segment.speaker_label,
                        0,
                    )
                    + 1
                )
            for analyzed_speaker in analysis.speakers:
                segment_count = retained_speaker_segment_counts.get(
                    analyzed_speaker.local_label,
                    0,
                )
                if segment_count == 0:
                    continue
                if analyzed_speaker.registry_speaker_id is None:
                    continue
                speaker = session.get(
                    SpeakerRecord,
                    analyzed_speaker.registry_speaker_id,
                )
                if speaker is None or speaker.status != "provisional":
                    continue
                reference = session.scalar(
                    select(SpeakerVoiceReferenceRecord)
                    .where(
                        SpeakerVoiceReferenceRecord.speaker_id == speaker.id,
                        SpeakerVoiceReferenceRecord.status == "proposed",
                    )
                    .order_by(SpeakerVoiceReferenceRecord.created_at.desc())
                )
                if reference is None:
                    continue
                session.add(
                    ChannelVoiceCandidateRecord(
                        source_channel_id=candidate.source_channel_id,
                        source_episode_id=episode.id,
                        speaker_id=speaker.id,
                        voice_reference_id=reference.id,
                        local_label=analyzed_speaker.local_label,
                        segment_count=segment_count,
                        decision="pending",
                        created_at=self.dependencies.clock.now(),
                    )
                )
            run.status = "completed"
            run.stage = "publication_queue"
            run.completed_at = self.dependencies.clock.now()
            run.lease_expires_at = None
            self._record_activity(
                session,
                run,
                kind="completion",
                stage=run.stage,
                summary="Processing Run reached the Publication Queue.",
            )
            candidate.status = "processed"
            self._send_publication_decision(session, episode)
            return {
                "processing_run_id": run.id,
                "processing_state": run.status,
                "episode_id": episode.id,
                "episode_state": episode.state,
            }

    def _resolve_analysis_speakers(
        self,
        session: Session,
        candidate: CandidateVideoRecord,
        analysis: SourceAnalysisResult,
    ) -> SourceAnalysisResult:
        verified = session.execute(
            select(
                SpeakerRecord,
                ChannelSpeakerProfileRecord,
                SpeakerVoiceReferenceRecord,
            )
            .join(
                ChannelSpeakerProfileRecord,
                ChannelSpeakerProfileRecord.speaker_id == SpeakerRecord.id,
            )
            .join(
                SpeakerVoiceReferenceRecord,
                and_(
                    SpeakerVoiceReferenceRecord.speaker_id == SpeakerRecord.id,
                    SpeakerVoiceReferenceRecord.status == "approved",
                ),
            )
            .where(
                SpeakerRecord.status == "verified",
                SpeakerRecord.verified.is_(True),
                ChannelSpeakerProfileRecord.source_channel_id
                == candidate.source_channel_id,
                ChannelSpeakerProfileRecord.state == "active",
            )
        ).all()
        resolved: list[AnalyzedSpeaker] = []
        policy = self.dependencies.speaker_match_policy
        for analyzed in analysis.speakers:
            best: (
                tuple[
                    SpeakerRecord,
                    ChannelSpeakerProfileRecord,
                    SpeakerVoiceReferenceRecord,
                ]
                | None
            ) = None
            best_similarity = -1.0
            for speaker, profile, reference in verified:
                embedding = tuple(
                    float(value)
                    for value in json.loads(speaker.embedding_json)
                )
                similarity = cosine_similarity(
                    analyzed.embedding,
                    embedding,
                )
                if similarity > best_similarity:
                    best = (speaker, profile, reference)
                    best_similarity = similarity
            if (
                best is not None
                and analyzed.confidence >= policy.minimum_analysis_confidence
                and best_similarity >= policy.minimum_similarity
            ):
                speaker, profile, reference = best
                resolved.append(
                    replace(
                        analyzed,
                        registry_speaker_id=speaker.id,
                        reference_artifact_key=(
                            speaker.canonical_voice_reference
                        ),
                        reference_audio=b"",
                        registry_voice_reference_id=reference.id,
                        matched_source_channel_id=profile.source_channel_id,
                        match_similarity=best_similarity,
                        match_policy_version=policy.version,
                    )
                )
                continue
            speaker_id = str(uuid4())
            reference_path = (
                self.dependencies.artifact_root
                / "structured"
                / "speaker-registry"
                / speaker_id
                / "candidate-reference.wav"
            )
            reference_path.parent.mkdir(parents=True, exist_ok=True)
            reference_path.write_bytes(analyzed.reference_audio)
            speaker = SpeakerRecord(
                display_name=(f"{analyzed.local_label} · {candidate.title}"),
                canonical_voice_reference=str(reference_path),
                embedding_json=json.dumps(analyzed.embedding),
                verified=False,
                status="provisional",
                created_at=self.dependencies.clock.now(),
            )
            session.add(speaker)
            session.flush()
            session.add(
                SpeakerVoiceReferenceRecord(
                    speaker_id=speaker.id,
                    artifact_key=str(reference_path),
                    status="proposed",
                    created_at=self.dependencies.clock.now(),
                )
            )
            resolved.append(
                replace(
                    analyzed,
                    registry_speaker_id=speaker.id,
                    reference_artifact_key=str(reference_path),
                    reference_audio=b"",
                    matched_source_channel_id=(
                        candidate.source_channel_id
                        if best is not None
                        else None
                    ),
                    match_similarity=(
                        best_similarity if best is not None else None
                    ),
                    match_policy_version=policy.version,
                )
            )
        return replace(analysis, speakers=tuple(resolved))

    def _checkpoint_analysis_references(
        self,
        run: ProcessingRunRecord,
        analysis: SourceAnalysisResult,
    ) -> SourceAnalysisResult:
        """Persist temporary voice references outside SQLite for retry."""
        root = (
            self.dependencies.artifact_root
            / "structured"
            / "processing-runs"
            / str(run.id)
            / "source-analysis-references"
        )
        speakers: list[AnalyzedSpeaker] = []
        for index, speaker in enumerate(analysis.speakers, start=1):
            if not speaker.reference_audio:
                speakers.append(speaker)
                continue
            path = root / f"speaker-{index:04d}.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(speaker.reference_audio)
            speakers.append(
                replace(speaker, reference_artifact_key=str(path))
            )
        return replace(analysis, speakers=tuple(speakers))

    def _discard_analysis_reference_checkpoints(
        self,
        run: ProcessingRunRecord,
        paths: tuple[Path, ...],
    ) -> None:
        root = (
            self.dependencies.artifact_root
            / "structured"
            / "processing-runs"
            / str(run.id)
            / "source-analysis-references"
        ).resolve()
        for path in paths:
            resolved = path.resolve()
            if root != resolved.parent:
                continue
            with suppress(OSError):
                resolved.unlink(missing_ok=True)

    def _record_attempt(
        self,
        run: ProcessingRunRecord,
        *,
        status: str,
        duration_ms: int,
        transfer_bytes: int = 0,
        error_kind: str | None = None,
        error_summary: str | None = None,
        routing_json: str | None = None,
    ) -> None:
        session = Session.object_session(run)
        if session is None:
            return
        now = self.dependencies.clock.now()
        attempt = session.scalar(
            select(ProcessingAttemptRecord)
            .where(
                ProcessingAttemptRecord.processing_run_id == run.id,
                ProcessingAttemptRecord.stage == run.stage,
                ProcessingAttemptRecord.status == "processing",
            )
            .order_by(ProcessingAttemptRecord.id.desc())
            .limit(1)
        )
        if attempt is None:
            attempt = ProcessingAttemptRecord(
                processing_run_id=run.id,
                stage=run.stage,
                status="processing",
                duration_ms=0,
                transfer_bytes=0,
                resume_checkpoint=_resume_checkpoint(run),
                created_at=now,
                completed_at=None,
                correlation_id=uuid4().hex,
                routing_json="{}",
                timing_provenance="derived",
            )
            session.add(attempt)
            session.flush()
        attempt.status = status
        attempt.duration_ms = duration_ms
        attempt.transfer_bytes = transfer_bytes
        attempt.resume_checkpoint = _resume_checkpoint(run)
        attempt.error_kind = error_kind
        attempt.error_summary = error_summary
        attempt.completed_at = now
        if routing_json is not None:
            attempt.routing_json = routing_json
        routing = _json_mapping(attempt.routing_json)
        routing_attempts = routing.get("attempts")
        route_rows = (
            [item for item in routing_attempts if isinstance(item, dict)]
            if isinstance(routing_attempts, list)
            else []
        )
        route_profiles = [
            str(item["profile"])
            for item in route_rows
            if item.get("profile")
        ]
        if len(route_profiles) > 1:
            self._record_activity(
                session,
                run,
                kind="modal_failover",
                stage=run.stage,
                summary=(
                    "Modal Account routing tried "
                    + " -> ".join(route_profiles)
                    + "."
                ),
                metadata={
                    "correlation_id": attempt.correlation_id,
                    "route": route_profiles,
                    "selected_profile": routing.get("selected_profile"),
                    "outcomes": [
                        {
                            key: item.get(key)
                            for key in ("profile", "outcome", "reason")
                            if item.get(key) is not None
                        }
                        for item in route_rows
                    ],
                },
                attempt_id=attempt.id,
            )
        activity_kind = {
            "completed": "stage_completed",
            "failed": "failure",
        }.get(status, "progress_checkpoint")
        self._record_activity(
            session,
            run,
            kind=activity_kind,
            stage=run.stage,
            summary=(
                error_summary if error_summary else f"{run.stage} {status}"
            ),
            metadata={
                "duration_ms": duration_ms,
                "transfer_bytes": transfer_bytes,
                "error_kind": error_kind,
                "resume_checkpoint": _resume_checkpoint(run),
                "correlation_id": attempt.correlation_id,
            },
            attempt_id=attempt.id,
        )

    def _begin_stage_attempt(
        self,
        session: Session,
        run: ProcessingRunRecord,
        stage: str,
    ) -> ProcessingAttemptRecord:
        now = self.dependencies.clock.now()
        previous = session.scalar(
            select(ProcessingAttemptRecord)
            .where(ProcessingAttemptRecord.processing_run_id == run.id)
            .order_by(ProcessingAttemptRecord.id.desc())
            .limit(1)
        )
        supersedes_attempt_id = None
        if previous is not None and previous.status == "processing":
            previous.status = "interrupted"
            previous.completed_at = now
            previous.duration_ms = _wall_duration_ms(
                previous.created_at,
                now,
            )
            supersedes_attempt_id = previous.id
            self._record_activity(
                session,
                run,
                kind="stage_interrupted",
                stage=previous.stage,
                summary=f"{previous.stage} execution was interrupted.",
                metadata={"correlation_id": previous.correlation_id},
                attempt_id=previous.id,
            )
        elif previous is not None and previous.status == "interrupted":
            supersedes_attempt_id = previous.id
        run.stage = stage
        attempt = ProcessingAttemptRecord(
            processing_run_id=run.id,
            stage=stage,
            status="processing",
            duration_ms=0,
            transfer_bytes=0,
            resume_checkpoint=_resume_checkpoint(run),
            created_at=now,
            completed_at=None,
            correlation_id=uuid4().hex,
            routing_json="{}",
            supersedes_attempt_id=supersedes_attempt_id,
            timing_provenance="recorded",
        )
        session.add(attempt)
        session.flush()
        self._record_activity(
            session,
            run,
            kind="stage_started",
            stage=stage,
            summary=f"{stage} started",
            metadata={
                "correlation_id": attempt.correlation_id,
                "resume_checkpoint": attempt.resume_checkpoint,
            },
            attempt_id=attempt.id,
        )
        return attempt

    def _record_activity(
        self,
        session: Session,
        run: ProcessingRunRecord,
        *,
        kind: str,
        stage: str | None,
        summary: str,
        metadata: dict[str, object] | None = None,
        attempt_id: int | None = None,
    ) -> None:
        safe_summary = summary
        if kind in {"failure", "retry_scheduled"}:
            error_kind = str((metadata or {}).get("error_kind") or "")
            safe_summary = (
                _safe_error_summary(error_kind, summary)
                or "Processing stopped."
            )
        session.add(
            ProcessingRunActivityRecord(
                processing_run_id=run.id,
                kind=kind,
                stage=stage,
                summary=safe_summary,
                metadata_json=json.dumps(metadata or {}),
                attempt_id=attempt_id,
                created_at=self.dependencies.clock.now(),
            )
        )

    def _record_content_edit_activity(
        self,
        session: Session,
        run: ProcessingRunRecord,
        render_plan: RenderPlan,
    ) -> None:
        artifact_hash = _content_hash(
            render_plan.translation.content_edit_json
        )
        existing = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run.id,
                ProcessingRunActivityRecord.kind == "content_edit",
            )
        ).all()
        if any(
            json.loads(item.metadata_json).get("artifact_hash")
            == artifact_hash
            for item in existing
        ):
            return
        category_counts: dict[str, int] = {}
        for exclusion in render_plan.exclusions:
            category_counts[exclusion.category] = (
                category_counts.get(exclusion.category, 0) + 1
            )
        excluded_count = len(render_plan.exclusions)
        if excluded_count:
            summary = (
                f"Semantic content editing excluded {excluded_count} "
                "source span"
                + ("s" if excluded_count != 1 else "")
                + "."
            )
        elif render_plan.status == "degraded":
            summary = (
                "Semantic content editing retained all content because "
                "one or more decisions could not be validated."
            )
        else:
            summary = "Semantic content editing retained all content."
        self._record_activity(
            session,
            run,
            kind="content_edit",
            stage="content_editing",
            summary=summary,
            metadata={
                "policy_version": CONTENT_EDIT_POLICY_VERSION,
                "artifact_hash": artifact_hash,
                "status": render_plan.status,
                "excluded_count": excluded_count,
                "excluded_source_seconds": (
                    render_plan.excluded_source_seconds
                ),
                "category_counts": category_counts,
                "diagnostics": list(render_plan.diagnostics),
            },
        )

    def _restore_translation_usage(
        self,
        run: ProcessingRunRecord,
    ) -> None:
        project = read_translation_project(
            self.dependencies.artifact_root,
            run.id,
        )
        if project is None:
            return
        run.input_tokens = int(project.get("input_tokens", run.input_tokens))
        run.output_tokens = int(
            project.get("output_tokens", run.output_tokens)
        )

    def _sync_translation_project_activity(
        self,
        session: Session,
        run: ProcessingRunRecord,
    ) -> None:
        project = read_translation_project(
            self.dependencies.artifact_root,
            run.id,
        )
        if project is None:
            return
        existing = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run.id,
                ProcessingRunActivityRecord.kind == "translation_turn",
            )
        ).all()
        project_input_hash = str(project.get("input_hash", ""))
        recorded = {
            (
                str(metadata.get("input_hash", "")),
                str(metadata.get("role")),
                int(metadata.get("turn", 0)),
            )
            for item in existing
            if isinstance(
                metadata := _json_mapping(item.metadata_json),
                dict,
            )
        }
        turns = project.get("turns", [])
        if not isinstance(turns, list):
            return
        for item in turns:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", ""))
            turn = int(item.get("turn", 0))
            activity_key = (project_input_hash, role, turn)
            if not role or turn <= 0 or activity_key in recorded:
                continue
            self._record_activity(
                session,
                run,
                kind="translation_turn",
                stage="translation",
                summary=(f"Translation {role.title()} turn {turn} recorded"),
                metadata={
                    "input_hash": project_input_hash,
                    "role": role,
                    "turn": turn,
                    "session_id": item.get("session_id"),
                    "model": item.get("model"),
                    "input_tokens": item.get("input_tokens", 0),
                    "output_tokens": item.get("output_tokens", 0),
                    "outcome": item.get("outcome"),
                    "completed_targets": item.get("completed_targets"),
                    "pending_targets": item.get("pending_targets"),
                },
            )
            recorded.add(activity_key)

    def _fail_processing_run(
        self,
        run: ProcessingRunRecord,
        candidate: CandidateVideoRecord,
        error_kind: str,
        summary: str,
        *,
        duration_ms: int = 0,
        routing_json: str | None = None,
    ) -> dict[str, object]:
        self._record_attempt(
            run,
            status="failed",
            duration_ms=duration_ms,
            error_kind=error_kind,
            error_summary=summary,
            routing_json=routing_json,
        )
        run.status = "review_queue"
        run.error_kind = error_kind
        run.error_summary = summary
        run.lease_expires_at = None
        if self._notification_enabled("processing_failure"):
            self.dependencies.notifications.send(
                OwnerNotification(
                    kind="processing_failure",
                    title=candidate.title,
                    body=summary,
                    actions=(),
                )
            )
        return {
            "processing_run_id": run.id,
            "processing_state": run.status,
            "error_kind": run.error_kind,
        }

    def _schedule_transient_retry(
        self,
        run: ProcessingRunRecord,
        candidate: CandidateVideoRecord,
        summary: str,
        *,
        duration_ms: int = 0,
        routing_json: str | None = None,
    ) -> dict[str, object]:
        delays = (5, 30, 120)
        run.attempt_count += 1
        if run.attempt_count > len(delays):
            return self._fail_processing_run(
                run,
                candidate,
                "transient_retry_exhausted",
                summary,
                duration_ms=duration_ms,
            )
        self._record_attempt(
            run,
            status="retry_wait",
            duration_ms=duration_ms,
            error_kind="transient",
            error_summary=summary,
            routing_json=routing_json,
        )
        delay_minutes = delays[run.attempt_count - 1]
        run.status = "retry_wait"
        run.next_retry_at = self.dependencies.clock.now() + timedelta(
            minutes=delay_minutes
        )
        run.lease_expires_at = None
        run.error_kind = "transient"
        run.error_summary = summary
        return {
            "processing_run_id": run.id,
            "processing_state": run.status,
            "error_kind": run.error_kind,
            "retry_in_seconds": delay_minutes * 60,
        }

    def _pause_for_spend_guardrail(
        self,
        session: Session,
        run: ProcessingRunRecord,
        candidate: CandidateVideoRecord,
        summary: str,
        *,
        duration_ms: int = 0,
        routing_json: str | None = None,
    ) -> dict[str, object]:
        self._record_attempt(
            run,
            status="spend_guardrail",
            duration_ms=duration_ms,
            error_kind="spend_guardrail",
            error_summary=summary,
            routing_json=routing_json,
        )
        self._write_setting(session, "spend_guardrail_paused", True)
        run.status = "spend_guardrail"
        run.lease_expires_at = None
        run.error_kind = "spend_guardrail"
        run.error_summary = summary
        preferences = self.settings()["notifications"]
        if isinstance(preferences, dict) and preferences.get(
            "spend_guardrail", True
        ):
            self.dependencies.notifications.send(
                OwnerNotification(
                    kind="spend_guardrail",
                    title=candidate.title,
                    body=summary,
                    actions=(),
                )
            )
        return {
            "processing_run_id": run.id,
            "processing_state": run.status,
            "error_kind": run.error_kind,
        }

    def decision_queue(self) -> list[CandidateVideoRecord]:
        with self.dependencies.database.session() as session:
            return list(
                session.scalars(
                    select(CandidateVideoRecord)
                    .where(CandidateVideoRecord.status == "awaiting_decision")
                    .order_by(CandidateVideoRecord.published_at.desc())
                ).all()
            )

    def decide_candidate(
        self,
        candidate_id: int,
        decision: TriageDecision,
    ) -> CandidateVideoRecord:
        if decision not in {
            TriageDecision.PROCESS,
            TriageDecision.IGNORE,
        }:
            raise ValueError("Owner decision must process or ignore")
        with self.dependencies.database.session() as session:
            candidate = session.get(CandidateVideoRecord, candidate_id)
            if candidate is None:
                raise LookupError("Candidate Video was not found")
            if candidate.status != "awaiting_decision":
                raise RuntimeError(
                    "Candidate Video is no longer awaiting a decision"
                )
            candidate.status = (
                "processing_queued"
                if decision is TriageDecision.PROCESS
                else "ignored"
            )
            session.add(
                LabeledExampleRecord(
                    candidate_video_id=candidate.id,
                    label=(
                        "positive"
                        if decision is TriageDecision.PROCESS
                        else "negative"
                    ),
                    created_at=self.dependencies.clock.now(),
                )
            )
            if decision is TriageDecision.PROCESS:
                self._queue_processing(
                    session,
                    candidate,
                    priority=10,
                    start_immediately=True,
                )
            self._close_candidate_actions(session, candidate.id)
            return candidate

    def ignored_list(
        self,
        query: str = "",
    ) -> list[CandidateVideoRecord]:
        with self.dependencies.database.session() as session:
            statement = select(CandidateVideoRecord).where(
                CandidateVideoRecord.status == "ignored"
            )
            normalized_query = query.strip()
            if normalized_query:
                pattern = f"%{normalized_query}%"
                statement = statement.where(
                    CandidateVideoRecord.title.ilike(pattern)
                    | CandidateVideoRecord.description.ilike(pattern)
                )
            return list(
                session.scalars(
                    statement.order_by(
                        CandidateVideoRecord.published_at.desc()
                    )
                ).all()
            )

    def process_ignored_candidate(
        self,
        candidate_id: int,
    ) -> CandidateVideoRecord:
        with self.dependencies.database.session() as session:
            candidate = session.get(CandidateVideoRecord, candidate_id)
            if candidate is None:
                raise LookupError("Candidate Video was not found")
            if candidate.status != "ignored":
                raise RuntimeError(
                    "Candidate Video is no longer in the Ignored List"
                )
            candidate.status = "processing_queued"
            label = session.scalar(
                select(LabeledExampleRecord).where(
                    LabeledExampleRecord.candidate_video_id == candidate.id
                )
            )
            if label is None:
                label = LabeledExampleRecord(
                    candidate_video_id=candidate.id,
                    label="positive",
                    created_at=self.dependencies.clock.now(),
                )
                session.add(label)
            else:
                label.label = "positive"
                label.created_at = self.dependencies.clock.now()
            self._queue_processing(session, candidate, priority=10)
            return candidate

    def interest_profile(self) -> dict[str, object]:
        with self.dependencies.database.session() as session:
            profile = self._get_or_create_interest_profile(session)
            labels = session.scalars(select(LabeledExampleRecord)).all()
            return {
                "version": profile.version,
                "markdown": profile.markdown,
                "labeled_examples": {
                    "positive": sum(
                        item.label == "positive" for item in labels
                    ),
                    "negative": sum(
                        item.label == "negative" for item in labels
                    ),
                },
            }

    def review_queue(self) -> list[ProcessingRunRecord]:
        with self.dependencies.database.session() as session:
            return list(
                session.scalars(
                    select(ProcessingRunRecord)
                    .where(ProcessingRunRecord.status == "review_queue")
                    .order_by(ProcessingRunRecord.started_at)
                ).all()
            )

    def has_active_processing_work(self) -> bool:
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            for model in (
                ProcessingRunRecord,
                SegmentRevisionJobRecord,
                TranscriptRevisionJobRecord,
            ):
                active = session.scalar(
                    select(model.id)
                    .where(
                        model.status == "processing",
                        or_(
                            model.lease_expires_at.is_(None),
                            model.lease_expires_at > now,
                        ),
                    )
                    .limit(1)
                )
                if active is not None:
                    return True
        return False

    def retry_processing_run(
        self,
        processing_run_id: int,
    ) -> ProcessingRunRecord:
        with self.dependencies.database.session() as session:
            run = session.get(ProcessingRunRecord, processing_run_id)
            if run is None:
                raise LookupError("Processing Run was not found")
            if run.status != "review_queue":
                raise RuntimeError("Processing Run is not awaiting review")
            preserve_render = False
            repair_mastering = False
            if (
                run.error_kind == "quality_gate"
                and run.render_json is not None
                and run.render_preview_key is not None
                and run.translation_json is not None
            ):
                rendered_audio = self.dependencies.storage.read_preview(
                    run.render_preview_key
                )
                if rendered_audio is not None:
                    speech = _deserialize_speech_checkpoint(
                        run.render_json,
                        rendered_audio,
                    )
                    translation = deserialize_translation(
                        run.translation_json,
                        run.content_edit_json,
                    )
                    checkpoint_matches = True
                    if run.content_edit_json is not None:
                        checkpoint_matches = False
                        if run.analysis_json is not None:
                            render_plan = build_render_plan(
                                deserialize_analysis(run.analysis_json),
                                translation,
                            )
                            render_stage = session.scalar(
                                select(ProcessingStageRecord)
                                .where(
                                    ProcessingStageRecord.processing_run_id
                                    == run.id,
                                    ProcessingStageRecord.stage == "render",
                                )
                                .order_by(ProcessingStageRecord.id.desc())
                                .limit(1)
                            )
                            checkpoint_matches = bool(
                                render_stage is not None
                                and render_stage.input_hash
                                == _render_plan_input_hash(
                                    translation,
                                    render_plan,
                                )
                            )
                            expected_segments = len(
                                render_plan.translation.segments
                            )
                        else:
                            expected_segments = 0
                    else:
                        expected_segments = len(translation.segments)
                    if checkpoint_matches:
                        try:
                            self._validate_speech(
                                speech,
                                expected_segments,
                            )
                        except ValueError:
                            repair_mastering = _is_publication_audio(
                                rendered_audio
                            )
                        else:
                            preserve_render = True
            if repair_mastering:
                run.stage = "mastering_repair"
            if (
                run.error_kind
                in {
                    "quality_gate",
                    "speech_provider",
                    "render_integrity",
                }
                and not preserve_render
                and not repair_mastering
            ):
                run.render_json = None
                run.render_preview_key = None
            self._record_activity(
                session,
                run,
                kind="retry_requested",
                stage=run.stage,
                summary="The Owner requested a checkpoint retry.",
                metadata={
                    "previous_error_kind": run.error_kind,
                    "resume_checkpoint": _resume_checkpoint(run),
                },
            )
            run.status = "queued"
            run.error_kind = None
            run.error_summary = None
            return run

    def update_interest_profile(self, markdown: str) -> dict[str, object]:
        normalized = markdown.strip()
        if not normalized:
            raise ValueError("Interest Profile cannot be empty")
        with self.dependencies.database.session() as session:
            current = self._get_or_create_interest_profile(session)
            current.active = False
            revision = InterestProfileRevisionRecord(
                version=self._next_interest_profile_version(session),
                markdown=normalized,
                active=True,
                source="owner",
                created_at=self.dependencies.clock.now(),
            )
            session.add(revision)
            session.flush()
            return {
                "version": revision.version,
                "markdown": revision.markdown,
                "active": revision.active,
                "source": revision.source,
            }

    def propose_interest_profile(
        self,
        markdown: str,
    ) -> dict[str, object]:
        normalized = markdown.strip()
        if not normalized:
            raise ValueError("Interest Profile cannot be empty")
        with self.dependencies.database.session() as session:
            self._get_or_create_interest_profile(session)
            revision = InterestProfileRevisionRecord(
                version=self._next_interest_profile_version(session),
                markdown=normalized,
                active=False,
                source="agent",
                created_at=self.dependencies.clock.now(),
            )
            session.add(revision)
            session.flush()
            return {
                "version": revision.version,
                "markdown": revision.markdown,
                "active": revision.active,
                "source": revision.source,
            }

    def approve_interest_profile(
        self,
        version: int,
    ) -> dict[str, object]:
        with self.dependencies.database.session() as session:
            revision = session.scalar(
                select(InterestProfileRevisionRecord).where(
                    InterestProfileRevisionRecord.version == version
                )
            )
            if revision is None:
                raise LookupError("Interest Profile revision was not found")
            if revision.source != "agent":
                raise RuntimeError(
                    "Only an Agent-proposed Interest Profile needs approval"
                )
            active = session.scalars(
                select(InterestProfileRevisionRecord).where(
                    InterestProfileRevisionRecord.active.is_(True)
                )
            ).all()
            for item in active:
                item.active = False
            revision.active = True
            return {
                "version": revision.version,
                "markdown": revision.markdown,
                "active": revision.active,
                "source": revision.source,
            }

    @staticmethod
    def _next_interest_profile_version(session: Session) -> int:
        latest = session.scalar(
            select(func.max(InterestProfileRevisionRecord.version))
        )
        return int(latest or 0) + 1

    def interest_profile_revisions(
        self,
    ) -> list[InterestProfileRevisionRecord]:
        with self.dependencies.database.session() as session:
            self._get_or_create_interest_profile(session)
            return list(
                session.scalars(
                    select(InterestProfileRevisionRecord).order_by(
                        InterestProfileRevisionRecord.version.desc()
                    )
                ).all()
            )

    def source_channel_detail(
        self,
        channel_id: int,
    ) -> SourceChannelRecord:
        with self.dependencies.database.session() as session:
            channel = session.get(SourceChannelRecord, channel_id)
            if channel is None:
                raise LookupError("Source Channel was not found")
            return channel

    def channel_recent_videos(
        self,
        channel_id: int,
        *,
        refresh: bool = False,
    ) -> list[dict[str, object]]:
        """Return the channel's recent-video window with library state.

        The YouTube-derived window is cached for ten minutes so
        reopening the channel page does not repeat the flat-playlist and
        date-enrichment requests; ``refresh`` forces a fresh window.
        Library state is always joined fresh from Candidate Videos.
        """
        channel = self.source_channel_detail(channel_id)
        now = self.dependencies.clock.now()
        cached = self._recent_videos.get(channel_id)
        if (
            refresh
            or cached is None
            or cached.cached_at <= now - _RECENT_VIDEOS_TTL
        ):
            window = self.dependencies.source.recent_video_previews(
                channel.youtube_channel_id
            )
            enriched = self.dependencies.source.enrich_video_previews(
                list(window.previews[:10])
            )
            self._recent_videos[channel_id] = RecentVideosCacheEntry(
                previews=enriched,
                cached_at=now,
            )
            if (
                window.channel_avatar_url
                and window.channel_avatar_url != channel.avatar_url
            ):
                with self.dependencies.database.session() as session:
                    row = session.get(SourceChannelRecord, channel_id)
                    if row is not None:
                        row.avatar_url = window.channel_avatar_url
        else:
            enriched = cached.previews
        items: list[dict[str, object]] = []
        with self.dependencies.database.session() as session:
            for preview in enriched:
                candidate = session.scalar(
                    select(CandidateVideoRecord).where(
                        CandidateVideoRecord.youtube_video_id
                        == preview.youtube_video_id
                    )
                )
                run = (
                    session.scalar(
                        select(ProcessingRunRecord).where(
                            ProcessingRunRecord.candidate_video_id
                            == candidate.id
                        )
                    )
                    if candidate is not None
                    else None
                )
                episode = (
                    session.scalar(
                        select(EpisodeRecord).where(
                            EpisodeRecord.candidate_video_id == candidate.id
                        )
                    )
                    if candidate is not None
                    else None
                )
                items.append(
                    {
                        "youtube_video_id": preview.youtube_video_id,
                        "title": preview.title,
                        "duration_seconds": preview.duration_seconds,
                        "source_url": preview.source_url,
                        "published_at": preview.published_at,
                        "view_count": preview.view_count,
                        "candidate_status": (
                            candidate.status if candidate is not None else None
                        ),
                        "run_status": (
                            run.status if run is not None else None
                        ),
                        "processing_run_id": (
                            run.id if run is not None else None
                        ),
                        "episode_id": (
                            episode.id if episode is not None else None
                        ),
                    }
                )
        return items

    def source_channels(self) -> list[SourceChannelRecord]:
        with self.dependencies.database.session() as session:
            return list(
                session.scalars(
                    select(SourceChannelRecord).order_by(
                        SourceChannelRecord.created_at
                    )
                ).all()
            )

    def update_source_channel(
        self,
        channel_id: int,
        mode: ChannelMode,
    ) -> SourceChannelRecord:
        with self.dependencies.database.session() as session:
            channel = session.get(SourceChannelRecord, channel_id)
            if channel is None:
                raise LookupError("Source Channel was not found")
            channel.mode = mode.value
            return channel

    def settings(self) -> dict[str, object]:
        defaults = _default_settings()
        with self.dependencies.database.session() as session:
            rows = session.scalars(select(SettingRecord)).all()
            values = {row.key: json.loads(row.value_json) for row in rows}
        automation = {
            "discovery_interval_minutes": values.get(
                "discovery_interval_minutes",
                defaults["discovery_interval_minutes"],
            ),
            "duration_guardrail_minutes": values.get(
                "duration_guardrail_minutes",
                defaults["duration_guardrail_minutes"],
            ),
            "processing_target_hours": values.get(
                "processing_target_hours",
                defaults["processing_target_hours"],
            ),
            "manual_processing": values.get(
                "manual_processing",
                defaults["manual_processing"],
            ),
            "auto_discovery": values.get(
                "auto_discovery",
                defaults["auto_discovery"],
            ),
        }
        retention = {
            "intermediate_days": 7,
            "operational_log_days": 7,
            "operational_log_max_mb": 500,
            "recovery_window_days": 30,
        }
        return {
            "automation": automation,
            "retention": retention,
            "notifications": values.get(
                "notification_preferences",
                defaults["notification_preferences"],
            ),
            "budget": {
                "spend_guardrail_paused": bool(
                    values.get("spend_guardrail_paused", False)
                ),
                "authority": "modal_environment",
            },
            "secrets": {
                "codex": _readiness_label(
                    self.dependencies.translation.readiness()
                ),
                "modal": _readiness_label(
                    self.dependencies.speech.readiness()
                ),
                "cloudflare": _readiness_label(
                    self.dependencies.storage.readiness()
                ),
                "telegram": _readiness_label(
                    self.dependencies.notifications.readiness()
                ),
                "feed_token": _readiness_label(
                    len(self.current_feed_token()) >= 32
                ),
            },
            "publication": {
                "subscription_url_masked": (
                    f"{self.dependencies.publication_domain}/feed/••••••••••••.xml"
                ),
                "token_replacement_requires_resubscription": True,
            },
        }

    def current_feed_token(self) -> str:
        path = self.dependencies.feed_token_path
        if path is not None and path.is_file():
            return path.read_text(encoding="utf-8").strip()
        token = self.dependencies.feed_token.strip()
        if path is not None and token:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(token, encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
        return token

    def authorize_feed(self, token: str) -> bool:
        expected_hash = self.dependencies.storage.read_feed_token_hash()
        if expected_hash is None:
            expected_hash = self._hash_token(self.current_feed_token())
        return secrets.compare_digest(
            self._hash_token(token),
            expected_hash.strip(),
        )

    def revoke_feed_token(self) -> dict[str, str]:
        path = self.dependencies.feed_token_path
        if path is None:
            raise RuntimeError("Feed Token secret path is not configured")
        token = secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(token, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        self.dependencies.storage.put_feed_token_hash(self._hash_token(token))
        self.dependencies.storage.put_feed_snapshot(
            self.private_feed().encode("utf-8")
        )
        return {
            "feed_token": token,
            "subscription_url": (
                f"{self.dependencies.publication_domain}/feed/{token}.xml"
            ),
        }

    def private_feed_subscription(self) -> dict[str, str]:
        token = self.current_feed_token()
        if len(token) < 32:
            raise RuntimeError("Private Feed is not configured")
        subscription_url = (
            f"{self.dependencies.publication_domain}/feed/{token}.xml"
        )
        return {
            "subscription_url": subscription_url,
            "apple_podcasts_url": (
                "podcast://" + subscription_url.removeprefix("https://")
            ),
        }

    def run_maintenance(self) -> dict[str, object]:
        safe_configuration = self.settings()
        return MaintenanceManager(self.dependencies).run(safe_configuration)

    def storage_usage(self) -> dict[str, int]:
        return MaintenanceManager(self.dependencies).storage_usage()

    def update_settings(
        self,
        updates: dict[str, object],
    ) -> dict[str, object]:
        allowed = set(_default_settings())
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(
                "Unsupported settings: " + ", ".join(sorted(unknown))
            )
        with self.dependencies.database.session() as session:
            for key, value in updates.items():
                row = session.get(SettingRecord, key)
                encoded = json.dumps(value)
                if row is None:
                    session.add(SettingRecord(key=key, value_json=encoded))
                else:
                    row.value_json = encoded
        return self.settings()

    def _write_setting(
        self,
        session: Session,
        key: str,
        value: object,
    ) -> None:
        encoded = json.dumps(value)
        row = session.get(SettingRecord, key)
        if row is None:
            session.add(SettingRecord(key=key, value_json=encoded))
        else:
            row.value_json = encoded

    def _spend_guardrail_paused(self) -> bool:
        with self.dependencies.database.session() as session:
            row = session.get(SettingRecord, "spend_guardrail_paused")
            return bool(json.loads(row.value_json)) if row else False

    def resume_spend_guardrail(self) -> dict[str, bool]:
        with self.dependencies.database.session() as session:
            self._write_setting(
                session,
                "spend_guardrail_paused",
                False,
            )
            runs = session.scalars(
                select(ProcessingRunRecord).where(
                    ProcessingRunRecord.status == "spend_guardrail"
                )
            ).all()
            for run in runs:
                run.status = "queued"
                run.error_kind = None
                run.error_summary = None
            segment_jobs = session.scalars(
                select(SegmentRevisionJobRecord).where(
                    SegmentRevisionJobRecord.status == "spend_guardrail"
                )
            ).all()
            for segment_job in segment_jobs:
                segment_job.status = "queued"
                segment_job.error_summary = None
                segment_job.lease_expires_at = None
            transcript_jobs = session.scalars(
                select(TranscriptRevisionJobRecord).where(
                    TranscriptRevisionJobRecord.status == "spend_guardrail"
                )
            ).all()
            for transcript_job in transcript_jobs:
                transcript_job.status = "queued"
                transcript_job.error_summary = None
                transcript_job.lease_expires_at = None
        return {"spend_guardrail_paused": False}

    def _create_action(
        self,
        session: Session,
        episode_id: int,
        decision: str,
    ) -> str:
        token = secrets.token_urlsafe(32)
        session.add(
            TelegramActionRecord(
                token_hash=self._hash_token(token),
                episode_id=episode_id,
                decision=decision,
                expires_at=self.dependencies.clock.now() + timedelta(days=7),
            )
        )
        return token

    def _create_candidate_action(
        self,
        session: Session,
        candidate_video_id: int,
        decision: str,
    ) -> str:
        token = secrets.token_urlsafe(32)
        session.add(
            TelegramActionRecord(
                token_hash=self._hash_token(token),
                candidate_video_id=candidate_video_id,
                decision=decision,
                expires_at=self.dependencies.clock.now() + timedelta(days=7),
            )
        )
        return token

    @staticmethod
    def _candidate_recommendation_diagnostic(
        candidate: CandidateVideoRecord,
    ) -> str | None:
        if candidate.triage_decision != TriageDecision.PROCESS.value:
            return "triage_decision_is_not_process"
        if candidate.triage_confidence < 0.90:
            return "confidence_below_recommendation_threshold"
        try:
            matched_interests = json.loads(
                candidate.matched_interests_json
            )
        except (TypeError, ValueError):
            return "matched_interests_are_malformed"
        if not matched_interests:
            return "matched_interests_are_empty"
        presentation = (
            candidate.title_zh.strip(),
            candidate.summary_zh.strip(),
            candidate.recommendation_zh.strip(),
        )
        if not all(presentation):
            return "mandarin_presentation_is_incomplete"
        if not all(
            re.search(r"[\u3400-\u9fff]", value)
            for value in presentation
        ):
            return "mandarin_presentation_is_invalid"
        return None

    def _record_candidate_recommendation(
        self,
        session: Session,
        candidate: CandidateVideoRecord,
    ) -> None:
        existing = session.scalar(
            select(TelegramDeliveryRecord.id).where(
                TelegramDeliveryRecord.candidate_video_id == candidate.id,
                TelegramDeliveryRecord.kind == "candidate_recommendation",
                TelegramDeliveryRecord.policy_version
                == _CANDIDATE_RECOMMENDATION_POLICY,
            )
        )
        if existing is not None:
            return
        now = self.dependencies.clock.now()
        diagnostic = self._candidate_recommendation_diagnostic(candidate)
        if not self._candidate_notifications_enabled(session):
            diagnostic = "notification_preference_is_disabled"
        session.add(
            TelegramDeliveryRecord(
                candidate_video_id=candidate.id,
                kind="candidate_recommendation",
                policy_version=_CANDIDATE_RECOMMENDATION_POLICY,
                status="pending" if diagnostic is None else "suppressed",
                attempt_count=0,
                last_error=diagnostic,
                created_at=now,
                updated_at=now,
            )
        )

    def deliver_pending_candidate_recommendations(self) -> int:
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            deliveries = session.scalars(
                select(TelegramDeliveryRecord)
                .where(
                    TelegramDeliveryRecord.status.in_(
                        {"pending", "sending"}
                    )
                )
                .order_by(TelegramDeliveryRecord.created_at)
            ).all()
            delivery_ids: list[int] = []
            for delivery in deliveries:
                if delivery.status == "sending":
                    if _utc_instant(delivery.updated_at) <= _utc_instant(
                        now - timedelta(minutes=5)
                    ):
                        delivery.status = "uncertain"
                        delivery.last_error = "delivery_outcome_is_unknown"
                        delivery.updated_at = now
                    continue
                if (
                    delivery.next_retry_at is None
                    or _utc_instant(delivery.next_retry_at)
                    <= _utc_instant(now)
                ):
                    delivery_ids.append(delivery.id)
        delivered = 0
        for delivery_id in delivery_ids:
            if self._deliver_candidate_recommendation(delivery_id):
                delivered += 1
        return delivered

    def _deliver_candidate_recommendation(self, delivery_id: int) -> bool:
        notification = self._prepare_candidate_recommendation(delivery_id)
        if notification is None:
            return False
        try:
            message_id = self.dependencies.notifications.send(notification)
        except NotificationDeliveryRejectedError as error:
            self._record_rejected_candidate_delivery(delivery_id, error)
            return False
        except (NotificationDeliveryUncertainError, RuntimeError) as error:
            self._record_uncertain_candidate_delivery(delivery_id, error)
            return False
        with self.dependencies.database.session() as session:
            delivery = session.get(TelegramDeliveryRecord, delivery_id)
            if delivery is None or delivery.status != "sending":
                return False
            delivery.status = "sent"
            delivery.message_id = message_id
            delivery.last_error = None
            delivery.updated_at = self.dependencies.clock.now()
        return True

    def _prepare_candidate_recommendation(
        self,
        delivery_id: int,
    ) -> OwnerNotification | None:
        with self.dependencies.database.session() as session:
            delivery = session.get(TelegramDeliveryRecord, delivery_id)
            if delivery is None or delivery.status != "pending":
                return None
            candidate = session.get(
                CandidateVideoRecord,
                delivery.candidate_video_id,
            )
            if (
                candidate is None
                or candidate.status != "awaiting_decision"
                or self._candidate_recommendation_diagnostic(candidate)
                is not None
                or not self._candidate_notifications_enabled(session)
            ):
                delivery.status = "cancelled"
                delivery.updated_at = self.dependencies.clock.now()
                return None
            channel = session.get(
                SourceChannelRecord,
                candidate.source_channel_id,
            )
            process_token = self._create_candidate_action(
                session,
                candidate.id,
                "candidate_process",
            )
            ignore_token = self._create_candidate_action(
                session,
                candidate.id,
                "candidate_ignore",
            )
            channel_line = ""
            if channel is not None:
                channel_line = zh_cn(
                    "candidate_recommendation.source_channel_line"
                ).format(channel=channel.title)
            body_lines = [
                zh_cn("candidate_recommendation.original_title_line").format(
                    title=candidate.title
                )
            ]
            if channel_line:
                body_lines.append(channel_line)
            body_lines.append(
                zh_cn("candidate_recommendation.source_url_line").format(
                    url=candidate.source_url
                )
            )
            delivery.status = "sending"
            delivery.attempt_count += 1
            delivery.next_retry_at = None
            delivery.updated_at = self.dependencies.clock.now()
            return OwnerNotification(
                kind="candidate_recommendation",
                title=candidate.title_zh,
                body=(
                    f"{candidate.summary_zh}\n\n"
                    + zh_cn(
                        "candidate_recommendation.reason_line"
                    ).format(reason=candidate.recommendation_zh)
                    + "\n\n"
                    + "\n".join(body_lines)
                ),
                actions=(
                    NotificationAction(
                        zh_cn(
                            "candidate_recommendation.process_action"
                        ),
                        process_token,
                    ),
                    NotificationAction(
                        zh_cn(
                            "candidate_recommendation.ignore_action"
                        ),
                        ignore_token,
                    ),
                ),
            )

    def _record_rejected_candidate_delivery(
        self,
        delivery_id: int,
        error: RuntimeError,
    ) -> None:
        with self.dependencies.database.session() as session:
            delivery = session.get(TelegramDeliveryRecord, delivery_id)
            if delivery is None or delivery.status != "sending":
                return
            self._close_candidate_actions(
                session,
                delivery.candidate_video_id,
            )
            delivery.status = "pending"
            delivery.last_error = str(error)[:320]
            delivery.next_retry_at = (
                self.dependencies.clock.now() + timedelta(minutes=5)
            )
            delivery.updated_at = self.dependencies.clock.now()

    def _record_uncertain_candidate_delivery(
        self,
        delivery_id: int,
        error: RuntimeError,
    ) -> None:
        with self.dependencies.database.session() as session:
            delivery = session.get(TelegramDeliveryRecord, delivery_id)
            if delivery is None or delivery.status != "sending":
                return
            delivery.status = "uncertain"
            delivery.last_error = str(error)[:320]
            delivery.updated_at = self.dependencies.clock.now()

    @staticmethod
    def _candidate_notifications_enabled(session: Session) -> bool:
        row = session.get(SettingRecord, "notification_preferences")
        if row is None:
            preferences = _default_settings()["notification_preferences"]
        else:
            preferences = json.loads(row.value_json)
        return bool(
            not isinstance(preferences, dict)
            or preferences.get(
                _LEGACY_CANDIDATE_RECOMMENDATION_PREFERENCE,
                True,
            )
        )

    def _send_publication_decision(
        self,
        session: Session,
        episode: EpisodeRecord,
    ) -> None:
        """Record optional delivery work without calling Telegram inline."""
        existing_delivery = session.scalar(
            select(PublicationNotificationRecord).where(
                PublicationNotificationRecord.episode_id == episode.id,
                PublicationNotificationRecord.revision_version
                == episode.revision_version,
            )
        )
        if existing_delivery is not None:
            return
        now = self.dependencies.clock.now()
        enabled = self._publication_notifications_enabled(session)
        delivery = PublicationNotificationRecord(
            episode_id=episode.id,
            revision_version=episode.revision_version,
            status="pending" if enabled else "suppressed",
            attempt_count=0,
            last_error=(
                None if enabled else "notification_preference_is_disabled"
            ),
            created_at=now,
            updated_at=now,
        )
        session.add(delivery)

    def deliver_pending_publication_notifications(self) -> int:
        if not self.dependencies.notifications.readiness():
            return 0
        now = self.dependencies.clock.now()
        with self.dependencies.database.session() as session:
            deliveries = session.scalars(
                select(PublicationNotificationRecord)
                .where(
                    PublicationNotificationRecord.status.in_(
                        {"pending", "sending"}
                    )
                )
                .order_by(PublicationNotificationRecord.created_at)
            ).all()
            delivery_ids: list[int] = []
            for delivery in deliveries:
                if delivery.status == "sending":
                    if _utc_instant(delivery.updated_at) <= _utc_instant(
                        now - timedelta(minutes=5)
                    ):
                        delivery.status = "uncertain"
                        delivery.last_error = "delivery_outcome_is_unknown"
                        delivery.updated_at = now
                    continue
                if (
                    delivery.next_retry_at is None
                    or _utc_instant(delivery.next_retry_at)
                    <= _utc_instant(now)
                ):
                    delivery_ids.append(delivery.id)
        delivered = 0
        for delivery_id in delivery_ids:
            if self._deliver_publication_notification(delivery_id):
                delivered += 1
        return delivered

    def _deliver_publication_notification(self, delivery_id: int) -> bool:
        notification = self._prepare_publication_notification(delivery_id)
        if notification is None:
            return False
        try:
            message_id = self.dependencies.notifications.send(notification)
        except NotificationDeliveryRejectedError as error:
            self._record_rejected_publication_notification(delivery_id, error)
            return False
        except (NotificationDeliveryUncertainError, RuntimeError) as error:
            self._record_uncertain_publication_notification(delivery_id, error)
            return False
        with self.dependencies.database.session() as session:
            delivery = session.get(PublicationNotificationRecord, delivery_id)
            if delivery is None or delivery.status != "sending":
                return False
            delivery.status = "sent"
            delivery.message_id = message_id
            delivery.last_error = None
            delivery.updated_at = self.dependencies.clock.now()
        return True

    def _prepare_publication_notification(
        self,
        delivery_id: int,
    ) -> OwnerNotification | None:
        with self.dependencies.database.session() as session:
            delivery = session.get(PublicationNotificationRecord, delivery_id)
            if delivery is None or delivery.status != "pending":
                return None
            episode = session.get(EpisodeRecord, delivery.episode_id)
            if (
                episode is None
                or episode.revision_version != delivery.revision_version
                or episode.state
                not in {
                    EpisodeState.PUBLICATION_QUEUE.value,
                    EpisodeState.REVISION_PENDING.value,
                }
            ):
                delivery.status = "cancelled"
                delivery.updated_at = self.dependencies.clock.now()
                return None
            if not self._publication_notifications_enabled(session):
                delivery.status = "suppressed"
                delivery.last_error = "notification_preference_is_disabled"
                delivery.updated_at = self.dependencies.clock.now()
                return None
            self._close_episode_actions(session, episode.id)
            publish_token = self._create_action(
                session,
                episode.id,
                "publish",
            )
            decline_token = self._create_action(
                session,
                episode.id,
                "do_not_publish",
            )
            delivery.status = "sending"
            delivery.attempt_count += 1
            delivery.next_retry_at = None
            delivery.updated_at = self.dependencies.clock.now()
            return self._publication_notification(
                episode,
                publish_token=publish_token,
                decline_token=decline_token,
            )

    def _publication_notification(
        self,
        episode: EpisodeRecord,
        *,
        publish_token: str,
        decline_token: str,
    ) -> OwnerNotification:
        preview_seconds = max(
            0,
            int(
                (
                    episode.preview_expires_at - self.dependencies.clock.now()
                ).total_seconds()
            ),
        )
        preview_url = self.dependencies.storage.create_preview_url(
            episode.preview_key,
            preview_seconds,
        )
        quality = json.loads(episode.quality_json)
        audio_quality = quality.get("audio", {})
        sample_rate_khz = (
            int(audio_quality.get("sample_rate_hz", 48000)) // 1000
        )
        quality_summary = zh_cn("publication_decision.quality_summary").format(
            codec=audio_quality.get("codec", "AAC-LC"),
            sample_rate_khz=sample_rate_khz,
            channels=audio_quality.get("channels", 2),
        )
        preview_summary = (
            "\n"
            + zh_cn("publication_decision.preview_url").format(url=preview_url)
            if preview_url
            else "\n" + zh_cn("publication_decision.preview_owner_console")
        )
        return OwnerNotification(
            kind="publication_decision",
            title=episode.title,
            body=(
                f"{episode.overview[:900]}\n\n"
                f"{zh_cn('publication_decision.quality_passed')}\n"
                f"{quality_summary}{preview_summary}"
            ),
            actions=(
                NotificationAction(
                    zh_cn("publication_decision.publish_action"),
                    publish_token,
                ),
                NotificationAction(
                    zh_cn("publication_decision.decline_action"),
                    decline_token,
                ),
            ),
        )

    def _record_rejected_publication_notification(
        self,
        delivery_id: int,
        error: RuntimeError,
    ) -> None:
        with self.dependencies.database.session() as session:
            delivery = session.get(PublicationNotificationRecord, delivery_id)
            if delivery is None or delivery.status != "sending":
                return
            self._close_episode_actions(session, delivery.episode_id)
            delivery.status = "pending"
            delivery.last_error = str(error)[:320]
            delivery.next_retry_at = (
                self.dependencies.clock.now() + timedelta(minutes=5)
            )
            delivery.updated_at = self.dependencies.clock.now()

    def _record_uncertain_publication_notification(
        self,
        delivery_id: int,
        error: RuntimeError,
    ) -> None:
        with self.dependencies.database.session() as session:
            delivery = session.get(PublicationNotificationRecord, delivery_id)
            if delivery is None or delivery.status != "sending":
                return
            delivery.status = "uncertain"
            delivery.last_error = str(error)[:320]
            delivery.updated_at = self.dependencies.clock.now()

    @staticmethod
    def _publication_notifications_enabled(session: Session) -> bool:
        row = session.get(SettingRecord, "notification_preferences")
        if row is None:
            preferences = _default_settings()["notification_preferences"]
        else:
            preferences = json.loads(row.value_json)
        return bool(
            not isinstance(preferences, dict)
            or preferences.get("publication_decision", True)
        )

    def _notification_enabled(self, kind: str) -> bool:
        preferences = self.settings()["notifications"]
        return bool(
            not isinstance(preferences, dict) or preferences.get(kind, True)
        )

    @staticmethod
    def _validate_speech(
        speech: SpeechResult,
        expected_segments: int,
    ) -> None:
        content = speech.publication_audio
        quality = speech.quality
        timings = speech.segment_timings
        timings_valid = (
            len(timings) == expected_segments
            and all(
                math.isfinite(start)
                and math.isfinite(end)
                and start >= 0
                and end > start
                and end <= speech.duration_seconds + 1.0
                for start, end in timings
            )
            and all(
                current[0] >= previous[1] - 0.001
                for previous, current in zip(
                    timings,
                    timings[1:],
                    strict=False,
                )
            )
        )
        valid = (
            len(content) >= 8
            and _is_publication_audio(content)
            and speech.duration_seconds > 0
            and quality.codec == "AAC-LC"
            and quality.sample_rate_hz == 48_000
            and quality.channels == 2
            and 120 <= quality.bitrate_kbps <= 136
            and (
                _LOUDNESS_GATE_MIN_LUFS
                <= quality.loudness_lufs
                <= _LOUDNESS_GATE_MAX_LUFS
            )
            and quality.true_peak_dbtp <= _TRUE_PEAK_GATE_MAX_DBTP
            and not quality.clipping
            and quality.rendered_segments == expected_segments
            and timings_valid
            and quality.timeline_continuous
            and not quality.boundary_artifacts
            and not quality.source_language_leakage
        )
        if not valid:
            raise ValueError("Publication Audio failed deterministic checks")

    def list_episodes(self) -> list[EpisodeRecord]:
        with self.dependencies.database.session() as session:
            return list(
                session.scalars(
                    select(EpisodeRecord).order_by(
                        EpisodeRecord.created_at.desc()
                    )
                ).all()
            )

    def episode_library(self) -> list[EpisodeLibraryItem]:
        with self.dependencies.database.session() as session:
            episodes = session.execute(
                select(
                    EpisodeRecord.id.label("episode_id"),
                    EpisodeRecord.processing_run_id,
                    EpisodeRecord.title,
                    EpisodeRecord.overview,
                    EpisodeRecord.state,
                    EpisodeRecord.duration_seconds,
                    EpisodeRecord.source_show,
                    EpisodeRecord.source_title,
                    EpisodeRecord.source_url,
                    EpisodeRecord.source_published_at,
                    EpisodeRecord.guests_json,
                    EpisodeRecord.created_at,
                    EpisodeRecord.published_at,
                    EpisodeRecord.recovery_until,
                ).order_by(EpisodeRecord.created_at.desc())
            ).mappings().all()
            episode_runs = {
                int(row.id): {
                    "status": str(row.status),
                    "stage": str(row.stage),
                }
                for row in session.execute(
                    select(
                        ProcessingRunRecord.id,
                        ProcessingRunRecord.status,
                        ProcessingRunRecord.stage,
                    ).where(
                        ProcessingRunRecord.id.in_(
                            [
                                int(episode["processing_run_id"])
                                for episode in episodes
                            ]
                        )
                    )
                ).all()
            }
            pending_rows = session.execute(
                select(
                    ProcessingRunRecord.id.label("processing_run_id"),
                    ProcessingRunRecord.status,
                    ProcessingRunRecord.stage,
                    ProcessingRunRecord.started_at,
                    ProcessingRunRecord.first_claimed_at,
                    ProcessingRunRecord.source_archive_key,
                    exists()
                    .where(
                        ProcessingAttemptRecord.processing_run_id
                        == ProcessingRunRecord.id
                    )
                    .label("has_attempt"),
                    exists()
                    .where(
                        ProcessingStageRecord.processing_run_id
                        == ProcessingRunRecord.id
                    )
                    .label("has_stage"),
                    CandidateVideoRecord.title,
                    CandidateVideoRecord.duration_seconds,
                    CandidateVideoRecord.source_url,
                    CandidateVideoRecord.published_at.label(
                        "source_published_at"
                    ),
                    SourceChannelRecord.title.label("source_show"),
                )
                .join(
                    CandidateVideoRecord,
                    CandidateVideoRecord.id
                    == ProcessingRunRecord.candidate_video_id,
                )
                .join(
                    SourceChannelRecord,
                    SourceChannelRecord.id
                    == CandidateVideoRecord.source_channel_id,
                )
                .outerjoin(
                    EpisodeRecord,
                    EpisodeRecord.processing_run_id == ProcessingRunRecord.id,
                )
                .where(
                    EpisodeRecord.id.is_(None),
                    ProcessingRunRecord.status != "completed",
                )
            ).mappings().all()
            items = [
                EpisodeLibraryItem(
                    episode_id=int(episode["episode_id"]),
                    processing_run_id=int(episode["processing_run_id"]),
                    title=str(episode["title"]),
                    overview=str(episode["overview"]),
                    state=str(episode["state"]),
                    stage=(
                        episode_runs[int(episode["processing_run_id"])]["stage"]
                        if int(episode["processing_run_id"]) in episode_runs
                        and episode["state"]
                        == EpisodeState.REVISION_PROCESSING.value
                        else None
                    ),
                    duration_seconds=int(episode["duration_seconds"]),
                    source_show=str(episode["source_show"]),
                    source_title=str(episode["source_title"]),
                    source_url=str(episode["source_url"]),
                    source_published_at=episode["source_published_at"],
                    guests=tuple(json.loads(str(episode["guests_json"]))),
                    created_at=episode["created_at"],
                    published_at=episode["published_at"],
                    can_reprocess=(
                        int(episode["processing_run_id"]) in episode_runs
                        and episode_runs[int(episode["processing_run_id"])][
                            "status"
                        ]
                        == "completed"
                        and episode["state"]
                        != EpisodeState.REVISION_PROCESSING.value
                    ),
                    library_state=_episode_library_state(
                        str(episode["state"])
                    ),
                    available_actions=_episode_library_actions(
                        _episode_library_state(str(episode["state"])),
                        can_restore=(
                            episode["recovery_until"] is not None
                            and _utc_instant(episode["recovery_until"])
                            >= _utc_instant(self.dependencies.clock.now())
                        ),
                    ),
                    artwork_url=(
                        f"/api/v1/episodes/{episode['episode_id']}/artwork"
                    ),
                )
                for episode in episodes
            ]
            items.extend(
                EpisodeLibraryItem(
                    episode_id=None,
                    processing_run_id=int(row["processing_run_id"]),
                    title=str(row["title"]),
                    overview=None,
                    state=(
                        "processing_queued"
                        if row["status"] == "queued"
                        else str(row["status"])
                    ),
                    stage=str(row["stage"]),
                    duration_seconds=int(row["duration_seconds"]),
                    source_show=str(row["source_show"]),
                    source_title=str(row["title"]),
                    source_url=str(row["source_url"]),
                    source_published_at=row["source_published_at"],
                    guests=(),
                    created_at=row["started_at"],
                    published_at=None,
                    can_reprocess=False,
                    library_state=_processing_library_state(
                        str(row["status"])
                    ),
                    available_actions=(
                        (
                            EpisodeLibraryAction.START,
                            EpisodeLibraryAction.REMOVE,
                        )
                        if row["status"] == "pending_start"
                        else (
                            EpisodeLibraryAction.VIEW,
                            EpisodeLibraryAction.REMOVE,
                        )
                        if row["status"] == "queued"
                        and row["first_claimed_at"] is None
                        and row["source_archive_key"] is None
                        and not row["has_attempt"]
                        and not row["has_stage"]
                        else (EpisodeLibraryAction.VIEW,)
                    ),
                    artwork_url="",
                )
                for row in pending_rows
            )
            return sorted(
                items,
                key=lambda item: item.created_at,
                reverse=True,
            )

    def reprocess_episode(self, episode_id: int) -> dict[str, object]:
        """Restart a completed episode from its Source Archive checkpoint.

        The current preview and any published enclosure remain available until
        the replacement passes the Quality Gate. The existing Processing Run
        is reused because Candidate Video, Processing Run, and Translated
        Episode are intentionally one-to-one in the initial product.
        """

        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            candidate = session.get(
                CandidateVideoRecord,
                episode.candidate_video_id,
            )
            if run is None or candidate is None:
                raise RuntimeError("Episode processing history is incomplete")
            if (
                run.status != "completed"
                or episode.state == EpisodeState.REVISION_PROCESSING.value
            ):
                raise RuntimeError(
                    "Translated Episode is already being reprocessed"
                )
            if (
                run.source_archive_key is None
                or run.source_archive_checksum is None
            ):
                raise RuntimeError("Source Archive checkpoint is unavailable")
            active_revision = session.scalar(
                select(SegmentRevisionJobRecord.id)
                .where(
                    SegmentRevisionJobRecord.episode_id == episode.id,
                    SegmentRevisionJobRecord.status.in_(
                        {"queued", "processing"}
                    ),
                )
                .limit(1)
            ) or session.scalar(
                select(TranscriptRevisionJobRecord.id)
                .where(
                    TranscriptRevisionJobRecord.episode_id == episode.id,
                    TranscriptRevisionJobRecord.status.in_(
                        {"queued", "processing"}
                    ),
                )
                .limit(1)
            )
            if active_revision is not None:
                raise RuntimeError(
                    "Translated Episode has an active segment revision"
                )

            previous_pipeline_version = run.pipeline_version
            previous_input_tokens = run.input_tokens
            previous_output_tokens = run.output_tokens
            previous_estimated_cost_usd = run.estimated_cost_usd
            if run.analysis_json is None or run.translation_json is None:
                raise RuntimeError("Episode processing checkpoints are incomplete")
            checkpoint_analysis = deserialize_analysis(run.analysis_json)
            analysis_stage = session.scalar(
                select(ProcessingStageRecord)
                .where(
                    ProcessingStageRecord.processing_run_id == run.id,
                    ProcessingStageRecord.stage == "source_analysis",
                )
                .order_by(ProcessingStageRecord.id.desc())
                .limit(1)
            )
            reusable_analysis = bool(
                analysis_stage is not None
                and analysis_stage.version == checkpoint_analysis.pipeline_version
                and checkpoint_analysis.pipeline_version
                == CURRENT_SPEECH_PIPELINE_VERSION
            )
            preserved_stages = (
                ("source_archive", "source_analysis")
                if reusable_analysis
                else ("source_archive",)
            )
            session.execute(
                delete(ProcessingStageRecord).where(
                    ProcessingStageRecord.processing_run_id == run.id,
                    ProcessingStageRecord.stage.not_in(
                        preserved_stages
                    ),
                )
            )
            checkpoint_translation = deserialize_translation(
                run.translation_json,
                run.content_edit_json,
            )
            if reusable_analysis:
                mark_translation_project_reprocess(
                    self.dependencies.artifact_root,
                    run.id,
                    checkpoint_translation.segments,
                )
            else:
                reset_translation_project(
                    self.dependencies.artifact_root,
                    run.id,
                )
            run.status = "queued"
            run.stage = (
                "translation" if reusable_analysis else "source_analysis"
            )
            run.priority = 10
            run.attempt_count = 0
            run.next_retry_at = None
            run.lease_expires_at = None
            run.translation_provider = None
            run.translation_model = None
            run.input_tokens = 0
            run.output_tokens = 0
            run.estimated_cost_usd = 0.0
            if not reusable_analysis:
                run.pipeline_version = "pending-current-pipeline"
                run.analysis_json = None
            run.translation_json = None
            run.content_edit_json = None
            run.render_json = None
            run.render_preview_key = None
            run.completed_at = None
            run.error_kind = None
            run.error_summary = None
            run.started_at = self.dependencies.clock.now()
            candidate.status = "processing_queued"
            episode.state = EpisodeState.REVISION_PROCESSING.value
            self._close_episode_actions(session, episode.id)
            self._record_activity(
                session,
                run,
                kind="reprocess_requested",
                stage=run.stage,
                summary=(
                    "The Owner requested reprocessing using the latest "
                    "compatible checkpoint."
                ),
                metadata={
                    "previous_pipeline_version": previous_pipeline_version,
                    "previous_input_tokens": previous_input_tokens,
                    "previous_output_tokens": previous_output_tokens,
                    "previous_estimated_cost_usd": (
                        previous_estimated_cost_usd
                    ),
                    "reused_checkpoint": (
                        "source_analysis"
                        if reusable_analysis
                        else "source_archive"
                    ),
                },
            )
            return {
                "episode_id": episode.id,
                "processing_run_id": run.id,
                "state": run.status,
                "stage": run.stage,
                "reused_checkpoint": (
                    "source_analysis"
                    if reusable_analysis
                    else "source_archive"
                ),
            }

    def episode_state(self, episode_id: int) -> str:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            return episode.state

    def published_episode_audio(self, episode_id: int) -> bytes:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            if episode.publication_key is None:
                raise LookupError("Published Episode Audio was not found")
            content = self.dependencies.storage.read_publication(
                episode.publication_key
            )
            if content is None:
                raise LookupError("Published Episode Audio was not found")
            return content

    def revise_episode_metadata(
        self,
        episode_id: int,
        title: str,
        overview: str,
        publication_description: str | None,
        chapters: list[tuple[int, str]],
    ) -> tuple[EpisodeRecord, bool]:
        normalized_title = title.strip()
        normalized_overview = overview.strip()
        if not normalized_title or not normalized_overview:
            raise ValueError("Episode Metadata cannot be empty")
        normalized_chapters = [
            (start_seconds, chapter_title.strip())
            for start_seconds, chapter_title in chapters
        ]
        if chapters and chapters[0][0] != 0:
            raise ValueError("Episode Chapters must begin at zero")
        if chapters and len(chapters) < 3:
            raise ValueError("Episode Chapters require at least three entries")
        starts = [item[0] for item in chapters]
        if starts != sorted(set(starts)) or any(item < 0 for item in starts):
            raise ValueError(
                "Episode Chapters must have unique ascending start times"
            )
        if any(not item[1].strip() for item in chapters):
            raise ValueError("Episode Chapter titles cannot be empty")
        if any(len(item[1].strip()) > 45 for item in chapters):
            raise ValueError("Episode Chapter titles cannot exceed 45 characters")
        if any(
            current - previous < 120
            for previous, current in zip(starts, starts[1:], strict=False)
        ):
            raise ValueError(
                "Episode Chapters must be at least two minutes apart"
            )
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            current_chapters = [
                (int(item[0]), str(item[1]).strip())
                for item in json.loads(episode.chapters_json)
            ]
            normalized_description = (
                _normalize_publication_copy_for_comparison(
                    publication_description
                )
                if publication_description is not None
                else _normalize_publication_copy_for_comparison(
                    episode.publication_description
                )
            )
            if (
                normalized_title == episode.title
                and normalized_overview == episode.overview
                and normalized_description
                == _normalize_publication_copy_for_comparison(
                    episode.publication_description
                )
                and normalized_chapters == current_chapters
            ):
                return episode, False
            if episode.state == EpisodeState.REVISION_PENDING.value:
                raise RuntimeError(
                    "A Publication Revision is already awaiting a decision"
                )
            maximum_chapters = int(
                episode.duration_seconds * 6 / 3600
            )
            if len(chapters) > maximum_chapters:
                raise ValueError(
                    "Episode Chapters exceed the supported hourly density"
                )
            if any(start >= episode.duration_seconds for start in starts):
                raise ValueError(
                    "Episode Chapter start time exceeds episode duration"
                )
            next_revision = episode.revision_version + 1
            episode.title = normalized_title
            episode.overview = normalized_overview
            if publication_description is not None:
                episode.publication_description = (
                    validate_publication_copy(
                        publication_description
                    )
                )
            episode.chapters_json = json.dumps(normalized_chapters)
            episode.revision_version = next_revision
            episode.revision_kind = PublicationRevisionKind.METADATA.value
            episode.state = (
                EpisodeState.REVISION_PENDING.value
                if episode.publication_key is not None
                else EpisodeState.PUBLICATION_QUEUE.value
            )
            session.add(
                EpisodeMetadataRevisionRecord(
                    episode_id=episode.id,
                    version=next_revision,
                    title=episode.title,
                    overview=episode.overview,
                    publication_description=(
                        episode.publication_description
                    ),
                    chapter_proposals_json=(
                        episode.chapter_proposals_json
                    ),
                    chapters_json=episode.chapters_json,
                    artwork_json=episode.artwork_json,
                    created_at=self.dependencies.clock.now(),
                )
            )
            self._send_publication_decision(session, episode)
            return episode, True

    def episode_metadata_revisions(
        self,
        episode_id: int,
    ) -> list[EpisodeMetadataRevisionRecord]:
        with self.dependencies.database.session() as session:
            if session.get(EpisodeRecord, episode_id) is None:
                raise LookupError("Translated Episode was not found")
            return list(
                session.scalars(
                    select(EpisodeMetadataRevisionRecord)
                    .where(
                        EpisodeMetadataRevisionRecord.episode_id == episode_id
                    )
                    .order_by(EpisodeMetadataRevisionRecord.version.desc())
                ).all()
            )

    def prepare_metadata_only_revision(
        self,
        episode_id: int,
    ) -> EpisodeRecord:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            if (
                episode.publication_key is None
                or episode.state != EpisodeState.PUBLISHED.value
            ):
                raise RuntimeError(
                    "Only a published Episode can prepare a metadata revision"
                )
            run = session.get(ProcessingRunRecord, episode.processing_run_id)
            candidate = session.get(
                CandidateVideoRecord,
                episode.candidate_video_id,
            )
            if run is None or candidate is None:
                raise RuntimeError(
                    "Metadata-only Publication Revision inputs are missing"
                )
            source = SourceCandidate(
                youtube_video_id=candidate.youtube_video_id,
                title=candidate.title,
                description=candidate.description,
                duration_seconds=candidate.duration_seconds,
                language=candidate.language,
                published_at=candidate.published_at,
                source_url=candidate.source_url,
                captions=candidate.captions,
                analysis_json=run.analysis_json,
                processing_run_id=run.id,
            )
            previous_artwork_path = episode.artwork_path
            run.artwork_status = "pending"
            self._prepare_episode_artwork(session, run, source)
            if run.artwork_status == "completed":
                episode.artwork_path = run.artwork_path
                episode.artwork_json = run.artwork_json

            metadata_changed = False
            if run.analysis_json and run.translation_json:
                analysis = deserialize_analysis(run.analysis_json)
                translation = deserialize_translation(
                    run.translation_json,
                    run.content_edit_json,
                    run.publication_metadata_json,
                )
                try:
                    metadata = parse_publication_metadata(
                        run.publication_metadata_json or "{}"
                    )
                except ValueError:
                    started = time.monotonic()
                    generated = (
                        self.dependencies.translation.publication_metadata(
                            source,
                            translation,
                        )
                    )
                    run.publication_metadata_json = generated.artifact_json
                    metadata = parse_publication_metadata(
                        generated.artifact_json
                    )
                    self._record_stage(
                        session,
                        run,
                        stage="publication_metadata_backfill",
                        version=metadata.policy_version,
                        provider=generated.provider,
                        model=generated.model,
                        input_hash=_content_hash(
                            {
                                "analysis": run.analysis_json,
                                "translation": run.translation_json,
                                "content_edit": run.content_edit_json,
                            }
                        ),
                        output_hash=_content_hash(generated.artifact_json),
                        usage={
                            "input_tokens": generated.input_tokens,
                            "output_tokens": generated.output_tokens,
                            "cli_version": generated.cli_version,
                            "session_id": generated.session_id,
                        },
                        duration_ms=round(
                            (time.monotonic() - started) * 1000
                        ),
                    )
                retained_segment_ids: tuple[str, ...] = ()
                timings: tuple[tuple[float, float], ...] = ()
                if run.render_json:
                    render_plan = build_render_plan(analysis, translation)
                    render = json.loads(run.render_json)
                    retained_segment_ids = tuple(
                        item.segment_id
                        for item in render_plan.analysis.segments
                    )
                    timings = tuple(
                        (float(item[0]), float(item[1]))
                        for item in render.get("segment_timings", [])
                    )
                resolved = _resolve_publication_metadata(
                    metadata,
                    retained_segment_ids,
                    timings,
                    episode.duration_seconds,
                )
                episode.publication_description = resolved.description
                episode.chapters_json = json.dumps(resolved.chapters)
                episode.chapter_proposals_json = (
                    resolved.chapter_proposals_json
                )
                metadata_changed = True

            if (
                episode.artwork_path == previous_artwork_path
                and not metadata_changed
            ):
                raise ValueError(
                    "No compatible Episode Artwork or Publication Metadata "
                    "could be backfilled"
                )
            next_revision = episode.revision_version + 1
            episode.revision_version = next_revision
            episode.revision_kind = PublicationRevisionKind.METADATA.value
            episode.state = (
                EpisodeState.REVISION_PENDING.value
                if episode.publication_key is not None
                else EpisodeState.PUBLICATION_QUEUE.value
            )
            session.add(
                EpisodeMetadataRevisionRecord(
                    episode_id=episode.id,
                    version=next_revision,
                    title=episode.title,
                    overview=episode.overview,
                    publication_description=(
                        episode.publication_description
                    ),
                    chapter_proposals_json=(
                        episode.chapter_proposals_json
                    ),
                    chapters_json=episode.chapters_json,
                    artwork_json=episode.artwork_json,
                    created_at=self.dependencies.clock.now(),
                )
            )
            self._record_activity(
                session,
                run,
                kind="metadata_only_revision_prepared",
                stage="publication_metadata",
                summary="Metadata-only Publication Revision is ready",
                metadata={
                    "artwork_updated": (
                        episode.artwork_path != previous_artwork_path
                    ),
                    "metadata_updated": metadata_changed,
                },
            )
            self._send_publication_decision(session, episode)
            return episode

    def episode_detail(self, episode_id: int) -> dict[str, object]:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            segments = session.scalars(
                select(EpisodeSegmentRecord)
                .where(EpisodeSegmentRecord.episode_id == episode.id)
                .order_by(EpisodeSegmentRecord.ordinal)
            ).all()
            speaker_ids = {
                segment.speaker_id
                for segment in segments
                if segment.speaker_id is not None
            }
            speakers = {
                speaker.id: speaker
                for speaker in (
                    session.get(SpeakerRecord, speaker_id)
                    for speaker_id in speaker_ids
                )
                if speaker is not None
            }
            chapters = json.loads(episode.chapters_json)
            artwork_metadata = json.loads(episode.artwork_json or "{}")
            presentation_degraded_fields: list[str] = []
            if artwork_metadata.get("status") != "completed":
                presentation_degraded_fields.append("artwork")
            presentation_run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            try:
                parse_publication_metadata(
                    presentation_run.publication_metadata_json or ""
                    if presentation_run is not None
                    else ""
                )
            except ValueError:
                presentation_degraded_fields.append(
                    "publication_metadata"
                )
            chapter_diagnostics = _chapter_resolution_diagnostics(
                episode.chapter_proposals_json,
                chapters,
            )
            published_metadata: dict[str, object] | None = None
            publication_presentation_state = "draft"
            publication_changed_fields: list[str] = []
            if episode.publication_key is not None:
                published_chapters = json.loads(
                    episode.published_chapters_json or "[]"
                )
                published_description = (
                    episode.published_publication_description
                    or episode.published_overview
                    or episode.overview
                )
                published_metadata = {
                    "title": episode.published_title or episode.title,
                    "overview": (
                        episode.published_overview or episode.overview
                    ),
                    "duration_seconds": (
                        episode.published_duration_seconds
                        or episode.duration_seconds
                    ),
                    "description": published_description,
                    "chapters": [
                        {"start_seconds": item[0], "title": item[1]}
                        for item in published_chapters
                    ],
                    "artwork_url": (
                        f"/api/v1/episodes/{episode.id}/artwork?published=true"
                    ),
                    "artwork": json.loads(
                        episode.published_artwork_json or "{}"
                    ),
                }
                publication_presentation_state = (
                    "revision_pending"
                    if episode.state == EpisodeState.REVISION_PENDING.value
                    else "published_clean"
                )
                if publication_presentation_state == "revision_pending":
                    if episode.title != episode.published_title:
                        publication_changed_fields.append("title")
                    if episode.overview != episode.published_overview:
                        publication_changed_fields.append("overview")
                    if (
                        episode.publication_description
                        != published_description
                    ):
                        publication_changed_fields.append(
                            "publication_description"
                        )
                    if chapters != published_chapters:
                        publication_changed_fields.append("chapters")
                    published_artwork = json.loads(
                        episode.published_artwork_json or "{}"
                    )
                    if _publication_artwork_identity(
                        artwork_metadata
                    ) != _publication_artwork_identity(published_artwork):
                        publication_changed_fields.append("artwork")
            recoverable = bool(
                episode.state == "deleted"
                and episode.recovery_until is not None
                and episode.recovery_until
                >= self.dependencies.clock.now()
            )
            latest_publication_decision = session.scalar(
                select(PublicationDecisionRecord)
                .where(PublicationDecisionRecord.episode_id == episode.id)
                .order_by(PublicationDecisionRecord.id.desc())
            )
            return {
                "id": episode.id,
                "title": episode.title,
                "overview": episode.overview,
                "publication_description": (
                    episode.publication_description or episode.overview
                ),
                "artwork_url": f"/api/v1/episodes/{episode.id}/artwork",
                "artwork": artwork_metadata,
                "publication_diagnostics": {
                    "artwork_error": str(
                        artwork_metadata.get("error") or ""
                    ),
                    "chapters": chapter_diagnostics,
                },
                "published_metadata": published_metadata,
                "publication_presentation": {
                    "state": publication_presentation_state,
                    "revision_kind": episode.revision_kind,
                    "changed_fields": publication_changed_fields,
                    "backfill_available": bool(
                        presentation_degraded_fields
                    ),
                    "degraded_fields": presentation_degraded_fields,
                },
                "publication_decision": (
                    {
                        "decision": latest_publication_decision.decision,
                        "channel": latest_publication_decision.channel,
                        "decided_at": latest_publication_decision.decided_at,
                        "publication_version": (
                            latest_publication_decision.publication_version
                        ),
                        "revision_version": (
                            latest_publication_decision.revision_version
                        ),
                    }
                    if latest_publication_decision is not None
                    else None
                ),
                "state": episode.state,
                "duration_seconds": episode.duration_seconds,
                "source_title": episode.source_title,
                "source_show": episode.source_show,
                "source_url": episode.source_url,
                "source_published_at": episode.source_published_at,
                "guests": json.loads(episode.guests_json),
                "published_at": episode.published_at,
                "recovery_until": episode.recovery_until,
                "recoverable": recoverable,
                "preview": {
                    "key": episode.preview_key,
                    "available": (
                        episode.preview_expires_at
                        >= self.dependencies.clock.now()
                    ),
                    "expires_at": episode.preview_expires_at,
                },
                "chapters": [
                    {"start_seconds": item[0], "title": item[1]}
                    for item in chapters
                ],
                "segments": [
                    {
                        "ordinal": segment.ordinal,
                        "source_text": segment.source_text,
                        "target_text": segment.target_text,
                        "emotion_reference": segment.emotion_reference,
                        "version": segment.version,
                        "speaker": (
                            {
                                "id": speakers[segment.speaker_id].id,
                                "display_name": speakers[
                                    segment.speaker_id
                                ].display_name,
                                "canonical_voice_reference": speakers[
                                    segment.speaker_id
                                ].canonical_voice_reference,
                            }
                            if segment.speaker_id in speakers
                            else None
                        ),
                    }
                    for segment in segments
                ],
                "apple_podcasts_preview": {
                    "artwork": f"/api/v1/episodes/{episode.id}/artwork",
                    "audio_format": "48 kHz stereo AAC-LC · 128 kbps",
                    "chapter_count": len(chapters),
                    "duration_seconds": episode.duration_seconds,
                },
                "quality_gates": json.loads(episode.quality_json),
            }

    def episode_artwork(
        self,
        episode_id: int,
        *,
        published: bool = False,
    ) -> tuple[bytes, str]:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            if not published and episode.artwork_path:
                try:
                    content = Path(episode.artwork_path).read_bytes()
                    validate_episode_artwork(content)
                    return content, "image/jpeg"
                except (OSError, ValueError):
                    pass
            if episode.publication_artwork_key:
                published_content = self.dependencies.storage.read_publication(
                    episode.publication_artwork_key
                )
                if published_content is not None:
                    try:
                        validate_episode_artwork(published_content)
                        return published_content, "image/jpeg"
                    except ValueError:
                        pass
            if self.dependencies.show_artwork:
                return self.dependencies.show_artwork, "image/png"
            raise LookupError("Episode Artwork is unavailable")

    def episode_processing_detail(
        self,
        episode_id: int,
    ) -> dict[str, object]:
        """The Processing Detail surface: every segment with its
        attribution, listen spans, and Decision Record status."""
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            if run is None:
                raise LookupError("Processing Run was not found")
            analysis = json.loads(run.analysis_json or "{}")
            if not analysis.get("segments"):
                pipeline_version = str(
                    analysis.get("pipeline_version") or ""
                )
                return {
                    "episode_id": episode.id,
                    "processing_run_id": run.id,
                    "summary": {
                        "evidence_available": False,
                        "historical_evidence_unavailable": (
                            pipeline_version == "v6"
                            or pipeline_version.endswith("-v6")
                        ),
                        "pipeline_version": pipeline_version or None,
                        "diarization_model": analysis.get(
                            "diarization_model"
                        ),
                        "diarization_model_revision": analysis.get(
                            "diarization_model_revision"
                        ),
                        "segment_count": 0,
                        "disputed_count": 0,
                        "auto_corrected_count": 0,
                        "speaker_mapping": {},
                        "reference_selection": {},
                        "consolidation_policy_version": None,
                        "adjudication_policy_version": None,
                        "decision_ledger_reference": None,
                        "content_edit": {
                            "status": "not_requested",
                            "excluded_count": 0,
                            "excluded_source_seconds": 0.0,
                        },
                    },
                    "speakers": [],
                    "segments": [],
                }
            analysis_result = deserialize_analysis(run.analysis_json or "{}")
            translation_result = deserialize_translation(
                run.translation_json or "{}",
                run.content_edit_json,
            )
            render_plan = build_render_plan(
                analysis_result,
                translation_result,
            )
            render = json.loads(run.render_json or "{}")
            timings = [
                (float(item[0]), float(item[1]))
                for item in render.get("segment_timings", [])
            ]
            rows = {
                row.ordinal: row
                for row in session.scalars(
                    select(EpisodeSegmentRecord)
                    .where(
                        EpisodeSegmentRecord.episode_id == episode.id
                    )
                    .order_by(EpisodeSegmentRecord.ordinal)
                ).all()
            }
            speaker_rows = {
                speaker.id: speaker
                for speaker in (
                    session.get(SpeakerRecord, speaker_id)
                    for speaker_id in {
                        row.speaker_id
                        for row in rows.values()
                        if row.speaker_id is not None
                    }
                )
                if speaker is not None
            }
            consolidation = (
                analysis.get("speaker_consolidation") or {}
            )
            adjudication = analysis.get("boundary_adjudication") or {}
            adjudication_records = (
                adjudication.get("decision_records") or []
            )
            registry_by_label = {
                str(item.get("local_label", "")): item.get(
                    "registry_speaker_id"
                )
                for item in analysis.get("speakers") or []
            }
            items: list[dict[str, object]] = []
            disputed_count = 0
            render_spans_by_segment: dict[str, list[tuple[float, float]]] = {}
            for index, render_segment in enumerate(
                render_plan.analysis.segments
            ):
                if index >= len(timings):
                    continue
                lineage = render_segment.segment_id.split(
                    "-retained-",
                    maxsplit=1,
                )[0]
                render_spans_by_segment.setdefault(lineage, []).append(
                    timings[index]
                )
            for ordinal, (segment, canonical_segment) in enumerate(
                zip(
                    analysis.get("segments") or [],
                    analysis_result.segments,
                    strict=True,
                ),
                start=1,
            ):
                row = rows.get(ordinal)
                start = float(segment["start_seconds"])
                end = float(segment["end_seconds"])
                record = _matching_adjudication_record(
                    adjudication_records,
                    start,
                    end,
                    canonical_segment.segment_id,
                )
                review_state = str(
                    segment.get("review_state", "accepted")
                )
                if review_state == "disputed":
                    status = "disputed"
                    disputed_count += 1
                elif segment.get("owner_ruling"):
                    status = "archived"
                elif (
                    record is not None
                    and record.get("verdict")
                    in {
                        "reassigned-by-agreement",
                        "tail-shifted",
                        "split-to-next-applied",
                        "whole-reassignment-applied",
                    }
                ):
                    status = "auto_corrected"
                else:
                    status = "accepted"
                output_spans = render_spans_by_segment.get(
                    canonical_segment.segment_id,
                    [],
                )
                output_span = (
                    (output_spans[0][0], output_spans[-1][1])
                    if output_spans
                    else None
                )
                speaker_row = (
                    speaker_rows.get(row.speaker_id)
                    if row is not None and row.speaker_id is not None
                    else None
                )
                items.append(
                    {
                        "ordinal": ordinal,
                        "segment_id": canonical_segment.segment_id,
                        "speaker_label": str(
                            segment.get("speaker_label", "")
                        ),
                        "speaker": (
                            {
                                "id": speaker_row.id,
                                "display_name": (
                                    speaker_row.display_name
                                ),
                            }
                            if speaker_row is not None
                            else None
                        ),
                        "source_start_seconds": start,
                        "source_end_seconds": end,
                        "output_start_seconds": (
                            output_span[0] if output_span else None
                        ),
                        "output_end_seconds": (
                            output_span[1] if output_span else None
                        ),
                        "source_text": (
                            row.source_text
                            if row is not None
                            else str(segment.get("text", ""))
                        ),
                        "target_text": (
                            row.target_text
                            if row is not None
                            else None
                        ),
                        "review_state": review_state,
                        "status": status,
                        "owner_ruling": segment.get("owner_ruling"),
                        "adjudication": record,
                        "counterpart_speaker": (
                            _counterpart_speaker(
                                record,
                                registry_by_label,
                                speaker_rows,
                            )
                            if record is not None
                            else None
                        ),
                        "content_edit": content_edit_for_segment(
                            render_plan,
                            canonical_segment,
                        ),
                    }
                )
            summary: dict[str, object] = {
                "evidence_available": True,
                "historical_evidence_unavailable": False,
                "pipeline_version": analysis.get("pipeline_version"),
                "diarization_model": analysis.get("diarization_model"),
                "diarization_model_revision": analysis.get(
                    "diarization_model_revision"
                ),
                "segment_count": len(items),
                "disputed_count": disputed_count,
                "auto_corrected_count": int(
                    adjudication.get("changed_count", 0)
                )
                + int(
                    (
                        adjudication.get("speaker_tail_boundary_repair")
                        or {}
                    ).get(
                        "repair_count", 0
                    )
                ),
                "speaker_tail_boundary_repair": {
                    "policy_version": (
                        adjudication.get("speaker_tail_boundary_repair") or {}
                    ).get("policy_version"),
                    "candidate_count": int(
                        (
                            adjudication.get("speaker_tail_boundary_repair")
                            or {}
                        ).get("candidate_count", 0)
                    ),
                    "repair_count": int(
                        (
                            adjudication.get("speaker_tail_boundary_repair")
                            or {}
                        ).get("repair_count", 0)
                    ),
                    "degraded": bool(
                        (
                            adjudication.get("speaker_tail_boundary_repair")
                            or {}
                        ).get("error")
                    ),
                    **(
                        {
                            "proposed_repair_count": int(
                                (
                                    adjudication.get(
                                        "speaker_tail_boundary_repair"
                                    )
                                    or {}
                                ).get("proposed_repair_count", 0)
                            ),
                            "locally_preserved_component_count": int(
                                (
                                    adjudication.get(
                                        "speaker_tail_boundary_repair"
                                    )
                                    or {}
                                ).get(
                                    "locally_preserved_component_count",
                                    0,
                                )
                            ),
                            "locally_degraded_component_count": int(
                                (
                                    adjudication.get(
                                        "speaker_tail_boundary_repair"
                                    )
                                    or {}
                                ).get(
                                    "locally_degraded_component_count",
                                    0,
                                )
                            ),
                            "conflict_component_count": int(
                                (
                                    adjudication.get(
                                        "speaker_tail_boundary_repair"
                                    )
                                    or {}
                                ).get("conflict_component_count", 0)
                            ),
                            "provider_degraded_component_count": int(
                                (
                                    adjudication.get(
                                        "speaker_tail_boundary_repair"
                                    )
                                    or {}
                                ).get(
                                    "provider_degraded_component_count",
                                    0,
                                )
                            ),
                        }
                        if "proposed_repair_count"
                        in (
                            adjudication.get("speaker_tail_boundary_repair")
                            or {}
                        )
                        else {}
                    ),
                },
                "speaker_mapping": (
                    consolidation.get("speaker_mapping") or {}
                ),
                "reference_selection": (
                    consolidation.get("reference_selection") or {}
                ),
                "consolidation_policy_version": consolidation.get(
                    "policy_version"
                ),
                "adjudication_policy_version": adjudication.get(
                    "policy_version"
                ),
                "decision_ledger_reference": (
                    consolidation.get("decision_ledger_reference")
                    or adjudication.get("decision_ledger_reference")
                ),
                "content_edit": {
                    "status": render_plan.status,
                    "excluded_count": len(render_plan.exclusions),
                    "excluded_source_seconds": (
                        render_plan.excluded_source_seconds
                    ),
                },
            }
            return {
                "episode_id": episode.id,
                "processing_run_id": run.id,
                "summary": summary,
                "speakers": [
                    {
                        "id": speaker.id,
                        "display_name": speaker.display_name,
                    }
                    for speaker in speaker_rows.values()
                ],
                "segments": items,
            }

    def rule_processing_segment(
        self,
        episode_id: int,
        ordinal: int,
        action: str,
        speaker_id: int | None = None,
    ) -> dict[str, object]:
        """Resolve one disputed segment: keep it, or overturn the
        Speaker through a Segment Revision. Never blocks publication."""
        if action not in {"keep", "overturn"}:
            raise ValueError("Unknown ruling action")
        if action == "overturn" and speaker_id is None:
            raise ValueError("An overturn ruling requires a Speaker")
        # Validate everything BEFORE any side effect: an invalid request
        # must never leave a queued revision or a changed episode state
        # behind (review finding).
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            if run is None:
                raise LookupError("Processing Run was not found")
            payload = json.loads(run.analysis_json or "{}")
            segment = _canonical_segment_at(payload, ordinal)
            if str(segment.get("review_state")) != "disputed":
                raise ValueError(
                    "This segment is not awaiting a ruling"
                )
            target_text = None
            if action == "overturn":
                row = session.scalar(
                    select(EpisodeSegmentRecord).where(
                        EpisodeSegmentRecord.episode_id == episode_id,
                        EpisodeSegmentRecord.ordinal == ordinal,
                    )
                )
                if row is None:
                    raise LookupError(
                        "Episode segment was not found"
                    )
                target_text = row.target_text
        revision_job_id: int | None = None
        if action == "overturn":
            job = self.queue_segment_revision(
                episode_id,
                ordinal,
                target_text or "",
                speaker_id,
                None,
            )
            revision_job_id = job.id
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            if run is None:
                raise LookupError("Processing Run was not found")
            payload = json.loads(run.analysis_json or "{}")
            segment = _canonical_segment_at(payload, ordinal)
            segment["review_state"] = "accepted"
            segment["owner_ruling"] = {
                "action": action,
                "speaker_id": speaker_id,
                "ruled_at": (
                    self.dependencies.clock.now().isoformat()
                ),
            }
            run.analysis_json = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            )
            session.commit()
        return {
            "episode_id": episode_id,
            "ordinal": ordinal,
            "action": action,
            "review_state": "accepted",
            "revision_job_id": revision_job_id,
        }

    def mark_segment_issue(
        self,
        episode_id: int,
        ordinal: int,
        note: str = "",
    ) -> dict[str, object]:
        """File one segment — with its Decision Records — as a new
        regression exam case."""
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            if run is None:
                raise LookupError("Processing Run was not found")
            payload = json.loads(run.analysis_json or "{}")
            segment = _canonical_segment_at(payload, ordinal)
            start = float(str(segment["start_seconds"]))
            end = float(str(segment["end_seconds"]))
            adjudication = (
                payload.get("boundary_adjudication") or {}
            )
            consolidation = (
                payload.get("speaker_consolidation") or {}
            )
            label_records = [
                record
                for record in (
                    consolidation.get("decision_records") or []
                )
                if record.get("kind")
                in {"consolidation", "reference-selection", "fallback"}
            ]
            case = {
                "kind": "speaker-exam-case",
                "episode_id": episode_id,
                "processing_run_id": run.id,
                "ordinal": ordinal,
                "pipeline_version": payload.get("pipeline_version"),
                "segment": segment,
                "adjudication_record": (
                    _matching_adjudication_record(
                        adjudication.get("decision_records") or [],
                        start,
                        end,
                        str(segment.get("segment_id", "")),
                    )
                ),
                "consolidation_records": label_records,
                "speaker_mapping": consolidation.get(
                    "speaker_mapping"
                ),
                "decision_ledger_reference": (
                    consolidation.get("decision_ledger_reference")
                    or adjudication.get("decision_ledger_reference")
                ),
                "note": note.strip(),
                "filed_at": (
                    self.dependencies.clock.now().isoformat()
                ),
            }
            reference = persist_structured_artifact(
                session,
                artifact_kind="speaker-exam-case",
                payload=json.dumps(
                    case,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                created_at=self.dependencies.clock.now(),
            )
            session.commit()
        return {
            "episode_id": episode_id,
            "ordinal": ordinal,
            "artifact": json.loads(reference),
        }

    def processing_run_summaries(self) -> list[ProcessingRunSummary]:
        with self.dependencies.database.session() as session:
            rows = session.execute(
                select(
                    ProcessingRunRecord.id.label("run_id"),
                    ProcessingRunRecord.status,
                    ProcessingRunRecord.stage,
                    ProcessingRunRecord.estimated_cost_usd,
                    ProcessingRunRecord.input_tokens,
                    ProcessingRunRecord.output_tokens,
                    ProcessingRunRecord.started_at,
                    CandidateVideoRecord.title,
                    SourceChannelRecord.title.label("source_show"),
                )
                .join(
                    CandidateVideoRecord,
                    CandidateVideoRecord.id
                    == ProcessingRunRecord.candidate_video_id,
                )
                .join(
                    SourceChannelRecord,
                    SourceChannelRecord.id
                    == CandidateVideoRecord.source_channel_id,
                )
                .order_by(ProcessingRunRecord.started_at.desc())
            ).mappings().all()
            run_ids = [int(row["run_id"]) for row in rows]
            if not run_ids:
                return []
            attempt_rows = session.execute(
                select(
                    ProcessingAttemptRecord.id,
                    ProcessingAttemptRecord.processing_run_id,
                    ProcessingAttemptRecord.stage,
                    ProcessingAttemptRecord.status,
                    ProcessingAttemptRecord.created_at,
                )
                .where(ProcessingAttemptRecord.processing_run_id.in_(run_ids))
                .order_by(ProcessingAttemptRecord.id)
            ).all()
            activity_rows = session.execute(
                select(
                    ProcessingRunActivityRecord.processing_run_id,
                    ProcessingRunActivityRecord.kind,
                    ProcessingRunActivityRecord.stage,
                    ProcessingRunActivityRecord.created_at,
                )
                .where(
                    ProcessingRunActivityRecord.processing_run_id.in_(run_ids)
                )
                .order_by(ProcessingRunActivityRecord.id)
            ).all()
            stage_rows = session.execute(
                select(
                    ProcessingStageRecord.processing_run_id,
                    func.max(ProcessingStageRecord.completed_at),
                )
                .where(ProcessingStageRecord.processing_run_id.in_(run_ids))
                .group_by(ProcessingStageRecord.processing_run_id)
            ).all()

            latest_attempt_at: dict[int, datetime] = {}
            active_attempt_stage: dict[int, str] = {}
            for attempt in attempt_rows:
                run_id = int(attempt.processing_run_id)
                latest_attempt_at[run_id] = attempt.created_at
                if attempt.status == "processing":
                    active_attempt_stage[run_id] = str(attempt.stage)
            latest_activity_at: dict[int, datetime] = {}
            translation_phase_step: dict[int, str] = {}
            for activity in activity_rows:
                run_id = int(activity.processing_run_id)
                latest_activity_at[run_id] = activity.created_at
                if (
                    activity.kind == "translation_phase"
                    and activity.stage
                    in {"translation_authoring", "translation_review"}
                ):
                    translation_phase_step[run_id] = str(activity.stage)
            latest_stage_at = {
                int(run_id): completed_at
                for run_id, completed_at in stage_rows
                if completed_at is not None
            }

            summaries: list[ProcessingRunSummary] = []
            for row in rows:
                run_id = int(row["run_id"])
                raw_step = active_attempt_stage.get(run_id, str(row["stage"]))
                if raw_step == "translation":
                    raw_step = translation_phase_step.get(run_id, raw_step)
                current_step = _owner_step_for_stage(raw_step, None)
                change_times = [row["started_at"]]
                for value in (
                    latest_attempt_at.get(run_id),
                    latest_activity_at.get(run_id),
                    latest_stage_at.get(run_id),
                ):
                    if value is not None:
                        change_times.append(value)
                latest_change_at = _latest_recorded_at(change_times)
                summary = ProcessingRunSummary(
                    id=run_id,
                    revision="",
                    status=str(row["status"]),
                    title=str(row["title"]),
                    source_show=str(row["source_show"]),
                    owner_state=_owner_state_for_status(str(row["status"])),
                    current_step=current_step,
                    estimated_cost_usd=float(row["estimated_cost_usd"]),
                    input_tokens=int(row["input_tokens"]),
                    output_tokens=int(row["output_tokens"]),
                    latest_change_at=latest_change_at,
                )
                summaries.append(
                    replace(
                        summary,
                        revision=_processing_run_summary_revision(summary),
                    )
                )
            return summaries

    def processing_run_detail(
        self,
        processing_run_id: int,
    ) -> dict[str, object]:
        with self.dependencies.database.session() as session:
            run = session.get(ProcessingRunRecord, processing_run_id)
            if run is None:
                raise LookupError("Processing Run was not found")
            candidate = session.get(
                CandidateVideoRecord,
                run.candidate_video_id,
            )
            if candidate is None:
                raise LookupError("Candidate Video was not found")
            source_channel = session.get(
                SourceChannelRecord,
                candidate.source_channel_id,
            )
            stages = session.scalars(
                select(ProcessingStageRecord)
                .where(
                    ProcessingStageRecord.processing_run_id
                    == processing_run_id
                )
                .order_by(ProcessingStageRecord.id)
            ).all()
            attempts = session.scalars(
                select(ProcessingAttemptRecord)
                .where(
                    ProcessingAttemptRecord.processing_run_id
                    == processing_run_id
                )
                .order_by(ProcessingAttemptRecord.id)
            ).all()
            activity = session.scalars(
                select(ProcessingRunActivityRecord)
                .where(
                    ProcessingRunActivityRecord.processing_run_id
                    == processing_run_id
                )
                .order_by(ProcessingRunActivityRecord.id)
            ).all()
            project = read_translation_project(
                self.dependencies.artifact_root,
                run.id,
            )
            observed_at = self.dependencies.clock.now()
            active_attempt = next(
                (
                    item
                    for item in reversed(attempts)
                    if item.status == "processing"
                ),
                None,
            )
            current_step = (
                _owner_step_for_stage(active_attempt.stage, project)
                if active_attempt is not None
                else _owner_current_step(run, project)
            )
            current_failed_attempt_id = next(
                (
                    item.id
                    for item in reversed(attempts)
                    if item.status == "failed"
                ),
                None,
            )
            safe_current_error = _safe_error_summary(
                run.error_kind,
                run.error_summary,
            )
            current_error = (
                {
                    "kind": run.error_kind,
                    "summary": safe_current_error,
                    "step": current_step,
                    "retryable": run.status == "review_queue",
                }
                if run.error_kind and safe_current_error
                else None
            )
            attempt_history = [
                {
                    "id": item.id,
                    "sequence": index,
                    "stage": item.stage,
                    "status": item.status,
                    "duration_ms": (
                        _wall_duration_ms(item.created_at, observed_at)
                        if item.status == "processing"
                        else item.duration_ms
                    ),
                    "transfer_bytes": item.transfer_bytes,
                    "resume_checkpoint": item.resume_checkpoint,
                    "error_kind": item.error_kind,
                    "error_summary": _safe_error_summary(
                        item.error_kind,
                        item.error_summary,
                    ),
                    "created_at": item.created_at,
                    "started_at": item.created_at,
                    "completed_at": item.completed_at,
                    "correlation_id": item.correlation_id,
                    "routing": _json_mapping(item.routing_json),
                    "supersedes_attempt_id": (
                        item.supersedes_attempt_id
                    ),
                    "timing_provenance": item.timing_provenance,
                    "is_current_blocker": (
                        current_error is not None
                        and item.id == current_failed_attempt_id
                    ),
                    "superseded": (
                        item.status == "failed"
                        and (
                            current_error is None
                            or item.id != current_failed_attempt_id
                        )
                    ),
                }
                for index, item in enumerate(attempts, start=1)
            ]
            latest_change_at = _latest_recorded_at(
                [
                    run.started_at,
                    *[item.created_at for item in attempts],
                    *[item.created_at for item in activity],
                    *[item.completed_at for item in stages],
                ]
            )
            revision = _content_hash(
                {
                    "run_id": run.id,
                    "status": run.status,
                    "stage": run.stage,
                    "latest_change_at": latest_change_at.isoformat(),
                    "error_kind": run.error_kind,
                    "error_summary": run.error_summary,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "project_updated_at": (
                        project.get("updated_at") if project else None
                    ),
                    "last_attempt_id": (attempts[-1].id if attempts else None),
                    "last_activity_id": (
                        activity[-1].id if activity else None
                    ),
                    "last_stage_id": stages[-1].id if stages else None,
                    "active_elapsed_bucket": (
                        int(_utc_instant(observed_at).timestamp()) // 5
                        if active_attempt is not None
                        else None
                    ),
                }
            )
            return {
                "id": run.id,
                "revision": revision,
                "candidate_video_id": run.candidate_video_id,
                "title": candidate.title,
                "source_show": (
                    source_channel.title
                    if source_channel is not None
                    else candidate.title
                ),
                "source_url": candidate.source_url,
                "status": run.status,
                "state": run.status,
                "stage": run.stage,
                "owner_state": _owner_run_state(run),
                "current_step": current_step,
                "latest_change_at": latest_change_at,
                "pipeline_version": run.pipeline_version,
                "priority": run.priority,
                "attempt_count": run.attempt_count,
                "estimated_cost_usd": run.estimated_cost_usd,
                "translation": {
                    "provider": run.translation_provider,
                    "model": run.translation_model,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                },
                "error": (
                    {
                        "kind": run.error_kind,
                        "summary": safe_current_error,
                    }
                    if run.error_kind and safe_current_error
                    else None
                ),
                "current_error": current_error,
                "retry_plan": _retry_plan(run, project),
                "queued_at": run.started_at,
                "first_claimed_at": run.first_claimed_at,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "translation_project": project,
                "pipeline_steps": _pipeline_steps(
                    run,
                    list(stages),
                    project,
                    list(attempts),
                    observed_at,
                ),
                "stages": [
                    {
                        "stage": item.stage,
                        "version": item.version,
                        "provider": item.provider,
                        "model": item.model,
                        "input_hash": item.input_hash,
                        "output_hash": item.output_hash,
                        "usage": json.loads(item.usage_json),
                        "duration_ms": item.duration_ms,
                        "started_at": item.started_at,
                        "completed_at": item.completed_at,
                    }
                    for item in stages
                ],
                "attempts": attempt_history,
                "attempt_history": attempt_history,
                "activity": [
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "stage": item.stage,
                        "summary": (
                            _safe_error_summary(
                                str(
                                    json.loads(item.metadata_json).get(
                                        "error_kind"
                                    )
                                    or ""
                                ),
                                item.summary,
                            )
                            if item.kind == "failure"
                            else item.summary
                        ),
                        "metadata": json.loads(item.metadata_json),
                        "attempt_id": item.attempt_id,
                        "created_at": item.created_at,
                    }
                    for item in activity
                ],
            }

    def processing_run_diagnostics(
        self,
        processing_run_id: int,
    ) -> dict[str, object]:
        """Build a bounded, owner-copyable, secret-redacted support bundle."""
        detail = self.processing_run_detail(processing_run_id)
        detail_stages = cast(list[dict[str, Any]], detail["stages"])
        detail_attempts = cast(
            list[dict[str, Any]],
            detail["attempt_history"],
        )
        stages = [
            {
                "stage": item["stage"],
                "version": item["version"],
                "provider": item["provider"],
                "model": item["model"],
                "duration_ms": item["duration_ms"],
                "started_at": item["started_at"],
                "completed_at": item["completed_at"],
                "usage": item["usage"],
            }
            for item in detail_stages[-12:]
        ]
        attempts = [
            {
                "id": item["id"],
                "sequence": item["sequence"],
                "stage": item["stage"],
                "status": item["status"],
                "duration_ms": item["duration_ms"],
                "transfer_bytes": item["transfer_bytes"],
                "resume_checkpoint": item["resume_checkpoint"],
                "error_kind": item["error_kind"],
                "error_summary": item["error_summary"],
                "created_at": item["created_at"],
                "superseded": item["superseded"],
            }
            for item in detail_attempts[-20:]
        ]
        return {
            "schema_version": "processing-run-diagnostics-v1",
            "run": {
                "id": detail["id"],
                "title": detail["title"],
                "source_show": detail["source_show"],
                "state": detail["owner_state"],
                "current_step": detail["current_step"],
                "pipeline_version": detail["pipeline_version"],
                "latest_change_at": detail["latest_change_at"],
            },
            "current_error": detail["current_error"],
            "retry_plan": detail["retry_plan"],
            "translation": detail["translation"],
            "stages": stages,
            "attempts": attempts,
        }

    def _complete_boundary_adjudication(
        self,
        session: Session,
        run: ProcessingRunRecord,
        analysis: SourceAnalysisResult,
    ) -> AdjudicationCompletion:
        """Complete discourse adjudication after its acoustic checkpoint.

        Provider, checkpoint, and application failures preserve the affected
        V14 boundaries and continue with an explicit degraded audit record.
        Historical pipeline versions retain their original behavior.
        """
        completion = complete_boundary_adjudication(
            analysis,
            self.dependencies.translation,
        )
        if analysis.pipeline_version == SPEECH_PIPELINE_V14:
            degraded = completion.discourse_error is not None
            self._record_activity(
                session,
                run,
                kind=(
                    "speaker_boundary_adjudication_degraded"
                    if degraded
                    else "speaker_boundary_adjudication_completed"
                ),
                stage="source_analysis",
                summary=(
                    "Unified Speaker boundary adjudication preserved its "
                    "affected boundaries because planning or review degraded."
                    if degraded
                    else (
                        "Unified Speaker boundary adjudication reviewed "
                        f"{completion.boundary_candidate_count} candidates, "
                        f"applied {completion.boundary_split_count} suffix "
                        "moves and "
                        f"{completion.boundary_reassign_count} whole "
                        "reassignments."
                    )
                ),
                metadata={
                    "candidate_count": completion.boundary_candidate_count,
                    "planner_proposed_count": (
                        completion.boundary_planner_proposed_count
                    ),
                    "reviewer_approved_count": (
                        completion.boundary_reviewer_approved_count
                    ),
                    "split_to_next_count": completion.boundary_split_count,
                    "reassign_whole_count": completion.boundary_reassign_count,
                    "locally_preserved_component_count": (
                        completion.boundary_locally_preserved_component_count
                    ),
                    "conflict_component_count": (
                        completion.boundary_conflict_component_count
                    ),
                    "provider_degraded_component_count": (
                        completion.boundary_provider_degraded_component_count
                    ),
                    "error": completion.discourse_error,
                    "completed": completion.completed,
                },
            )
            return completion
        if completion.discourse_error is not None:
            self._record_activity(
                session,
                run,
                kind="adjudication_degraded",
                stage="source_analysis",
                summary=(
                    "Discourse judge unavailable; disputed fragments "
                    "kept ownership and were marked for review."
                ),
                metadata={
                    "discourse_error": completion.discourse_error,
                    "dispute_count": completion.dispute_count,
                    "completed": completion.completed,
                },
            )
        if (
            supports_speaker_tail_repair(analysis.pipeline_version)
            and completion.tail_repair_error is None
        ):
            self._record_activity(
                session,
                run,
                kind="speaker_tail_repair_completed",
                stage="source_analysis",
                summary=(
                    "Speaker tail repair reviewed "
                    f"{completion.tail_repair_candidate_count} candidates "
                    f"and applied {completion.tail_repair_count} repairs."
                ),
                metadata={
                    "candidate_count": (
                        completion.tail_repair_candidate_count
                    ),
                    "repair_count": completion.tail_repair_count,
                    "completed": completion.completed,
                    **(
                        _v13_tail_repair_activity_metadata(completion)
                        if analysis.pipeline_version == SPEECH_PIPELINE_V13
                        else {}
                    ),
                },
            )
        elif completion.tail_repair_error is not None:
            self._record_activity(
                session,
                run,
                kind="speaker_tail_repair_degraded",
                stage="source_analysis",
                summary=(
                    (
                        "Speaker tail repair applied "
                        f"{completion.tail_repair_count} repairs and preserved "
                        f"{completion.tail_repair_locally_preserved_component_count} "
                        "local components; each existing segment pair was "
                        "preserved locally."
                    )
                    if analysis.pipeline_version == SPEECH_PIPELINE_V13
                    else (
                        "Speaker tail repair abstained; the existing segment "
                        "pair was preserved."
                    )
                ),
                metadata={
                    "tail_repair_error": completion.tail_repair_error,
                    "candidate_count": (
                        completion.tail_repair_candidate_count
                    ),
                    "repair_count": completion.tail_repair_count,
                    "completed": completion.completed,
                    **(
                        _v13_tail_repair_activity_metadata(completion)
                        if analysis.pipeline_version == SPEECH_PIPELINE_V13
                        else {}
                    ),
                },
            )
        return completion

    def _persist_decision_ledger(
        self,
        session: Session,
        run: ProcessingRunRecord,
    ) -> None:
        """Persist speaker Decision Records as a content-addressed
        Structured Artifact and stamp its reference into the stored
        analysis."""
        payload = json.loads(str(run.analysis_json))
        sections = [
            payload.get("speaker_consolidation") or {},
            payload.get("boundary_adjudication") or {},
        ]
        records = [
            record
            for section in sections
            for record in section.get("decision_records") or []
        ]
        if not records:
            return
        ledger = DecisionLedger(
            processing_run_id=int(run.id),
            records=tuple(
                DecisionRecord.from_payload(record)
                for record in records
            ),
        )
        reference = json.loads(
            ledger.persist(
                session,
                created_at=self.dependencies.clock.now(),
            )
        )
        for key, section in (
            ("speaker_consolidation", sections[0]),
            ("boundary_adjudication", sections[1]),
        ):
            if section.get("decision_records"):
                section["decision_ledger_reference"] = reference
                payload[key] = section
        run.analysis_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )

    def _record_stage(
        self,
        session: Session,
        run: ProcessingRunRecord,
        *,
        stage: str,
        version: str,
        provider: str | None,
        model: str | None,
        input_hash: str,
        output_hash: str,
        usage: dict[str, object],
        duration_ms: int = 0,
    ) -> None:
        now = self.dependencies.clock.now()
        attempt = session.scalar(
            select(ProcessingAttemptRecord)
            .where(
                ProcessingAttemptRecord.processing_run_id == run.id,
                ProcessingAttemptRecord.stage == stage,
                ProcessingAttemptRecord.status == "completed",
            )
            .order_by(ProcessingAttemptRecord.id.desc())
            .limit(1)
        )
        started_at = attempt.created_at if attempt is not None else now
        completed_at = (
            attempt.completed_at
            if attempt is not None and attempt.completed_at is not None
            else now
        )
        recorded_duration_ms = (
            attempt.duration_ms if attempt is not None else duration_ms
        )
        recorded_usage = dict(usage)
        if attempt is not None:
            routing = _json_mapping(attempt.routing_json)
            if routing:
                recorded_usage["routing"] = routing
        session.add(
            ProcessingStageRecord(
                processing_run_id=run.id,
                stage=stage,
                version=version,
                provider=provider,
                model=model,
                input_hash=input_hash,
                output_hash=output_hash,
                usage_json=json.dumps(recorded_usage),
                duration_ms=recorded_duration_ms,
                started_at=started_at,
                completed_at=completed_at,
            )
        )

    def create_speaker(
        self,
        display_name: str,
        canonical_voice_reference: str,
        verified: bool,
    ) -> SpeakerRecord:
        with self.dependencies.database.session() as session:
            speaker = SpeakerRecord(
                display_name=display_name.strip(),
                canonical_voice_reference=canonical_voice_reference,
                verified=verified,
                status="verified" if verified else "provisional",
                created_at=self.dependencies.clock.now(),
            )
            session.add(speaker)
            session.flush()
            session.add(
                SpeakerVoiceReferenceRecord(
                    speaker_id=speaker.id,
                    artifact_key=speaker.canonical_voice_reference,
                    status="approved",
                    created_at=self.dependencies.clock.now(),
                    approved_at=self.dependencies.clock.now(),
                )
            )
            return speaker

    def speakers(self) -> list[SpeakerRecord]:
        with self.dependencies.database.session() as session:
            return list(
                session.scalars(
                    select(SpeakerRecord)
                    .where(SpeakerRecord.status != "merged")
                    .order_by(SpeakerRecord.display_name)
                ).all()
            )

    def channel_voice_library(
        self,
    ) -> tuple[ChannelVoiceLibrarySummary, ...]:
        with self.dependencies.database.session() as session:
            channels = session.scalars(
                select(SourceChannelRecord).order_by(SourceChannelRecord.title)
            ).all()
            summaries: list[ChannelVoiceLibrarySummary] = []
            for channel in channels:
                saved_count = session.scalar(
                    select(func.count(ChannelSpeakerProfileRecord.id)).where(
                        ChannelSpeakerProfileRecord.source_channel_id
                        == channel.id,
                        ChannelSpeakerProfileRecord.state == "active",
                    )
                )
                pending_count = session.scalar(
                    select(func.count(ChannelVoiceCandidateRecord.id)).where(
                        ChannelVoiceCandidateRecord.source_channel_id
                        == channel.id,
                        ChannelVoiceCandidateRecord.decision == "pending",
                    )
                )
                summaries.append(
                    ChannelVoiceLibrarySummary(
                        source_channel_id=channel.id,
                        title=channel.title,
                        avatar_url=channel.avatar_url,
                        saved_profile_count=int(saved_count or 0),
                        pending_candidate_count=int(pending_count or 0),
                    )
                )
            return tuple(summaries)

    def channel_voice_library_detail(
        self,
        source_channel_id: int,
    ) -> ChannelVoiceLibraryDetail:
        with self.dependencies.database.session() as session:
            channel = session.get(SourceChannelRecord, source_channel_id)
            if channel is None:
                raise LookupError("Source Channel was not found")
            candidates = session.execute(
                select(
                    ChannelVoiceCandidateRecord,
                    SpeakerRecord,
                    SpeakerVoiceReferenceRecord,
                    EpisodeRecord,
                )
                .join(
                    SpeakerRecord,
                    SpeakerRecord.id == ChannelVoiceCandidateRecord.speaker_id,
                )
                .join(
                    SpeakerVoiceReferenceRecord,
                    SpeakerVoiceReferenceRecord.id
                    == ChannelVoiceCandidateRecord.voice_reference_id,
                )
                .join(
                    EpisodeRecord,
                    EpisodeRecord.id
                    == ChannelVoiceCandidateRecord.source_episode_id,
                )
                .where(
                    ChannelVoiceCandidateRecord.source_channel_id
                    == source_channel_id,
                    ChannelVoiceCandidateRecord.decision == "pending",
                )
                .order_by(ChannelVoiceCandidateRecord.created_at.desc())
            ).all()
            saved_rows = session.execute(
                select(
                    ChannelSpeakerProfileRecord,
                    SpeakerRecord,
                    SpeakerVoiceReferenceRecord,
                )
                .join(
                    SpeakerRecord,
                    SpeakerRecord.id == ChannelSpeakerProfileRecord.speaker_id,
                )
                .join(
                    SpeakerVoiceReferenceRecord,
                    and_(
                        SpeakerVoiceReferenceRecord.speaker_id
                        == SpeakerRecord.id,
                        SpeakerVoiceReferenceRecord.status == "approved",
                    ),
                )
                .where(
                    ChannelSpeakerProfileRecord.source_channel_id
                    == source_channel_id,
                    ChannelSpeakerProfileRecord.state == "active",
                )
                .order_by(ChannelSpeakerProfileRecord.display_name)
            ).all()
            saved_profiles = tuple(
                _channel_speaker_profile(profile, speaker, reference)
                for profile, speaker, reference in saved_rows
            )
            suggestion_rows = session.execute(
                select(
                    ChannelSpeakerProfileRecord,
                    SpeakerRecord,
                    SourceChannelRecord,
                )
                .join(
                    SpeakerRecord,
                    SpeakerRecord.id == ChannelSpeakerProfileRecord.speaker_id,
                )
                .join(
                    SourceChannelRecord,
                    SourceChannelRecord.id
                    == ChannelSpeakerProfileRecord.source_channel_id,
                )
                .where(
                    ChannelSpeakerProfileRecord.source_channel_id
                    != source_channel_id,
                    ChannelSpeakerProfileRecord.state == "active",
                    SpeakerRecord.status == "verified",
                )
            ).all()
            minimum_similarity = (
                self.dependencies.speaker_match_policy.minimum_similarity
            )
            pending = tuple(
                ChannelVoiceCandidate(
                    id=candidate.id,
                    speaker_id=speaker.id,
                    display_name=speaker.display_name,
                    local_label=candidate.local_label,
                    segment_count=candidate.segment_count,
                    source_episode_id=episode.id,
                    source_episode_title=episode.title,
                    voice_reference=VoiceReferencePreview(
                        id=reference.id,
                        duration_seconds=_wave_duration_seconds(
                            reference.artifact_key
                        ),
                        audio_url=(
                            f"/api/v1/speaker-voice-references/{reference.id}/audio"
                        ),
                    ),
                    possible_matches=tuple(
                        sorted(
                            (
                                ChannelSpeakerSuggestion(
                                    profile_id=possible_profile.id,
                                    source_channel_id=(possible_channel.id),
                                    source_channel_title=(
                                        possible_channel.title
                                    ),
                                    speaker_id=possible_speaker.id,
                                    display_name=(
                                        possible_profile.display_name
                                    ),
                                    similarity=round(similarity, 4),
                                )
                                for (
                                    possible_profile,
                                    possible_speaker,
                                    possible_channel,
                                ) in suggestion_rows
                                if (
                                    similarity := cosine_similarity(
                                        tuple(
                                            float(value)
                                            for value in json.loads(
                                                speaker.embedding_json
                                            )
                                        ),
                                        tuple(
                                            float(value)
                                            for value in json.loads(
                                                possible_speaker.embedding_json
                                            )
                                        ),
                                    )
                                )
                                >= minimum_similarity
                            ),
                            key=lambda match: (
                                -match.similarity,
                                match.profile_id,
                            ),
                        )
                    ),
                )
                for candidate, speaker, reference, episode in candidates
            )
            return ChannelVoiceLibraryDetail(
                source_channel_id=channel.id,
                title=channel.title,
                avatar_url=channel.avatar_url,
                saved_profiles=saved_profiles,
                pending_candidates=pending,
            )

    def save_channel_voice_candidate(
        self,
        candidate_id: int,
        display_name: str | None,
    ) -> ChannelSpeakerProfile:
        with self.dependencies.database.session() as session:
            candidate = session.get(
                ChannelVoiceCandidateRecord,
                candidate_id,
            )
            if candidate is None:
                raise LookupError("Channel voice candidate was not found")
            speaker = session.get(SpeakerRecord, candidate.speaker_id)
            reference = session.get(
                SpeakerVoiceReferenceRecord,
                candidate.voice_reference_id,
            )
            if speaker is None or reference is None:
                raise LookupError("Speaker Voice Reference was not found")
            profile = session.scalar(
                select(ChannelSpeakerProfileRecord).where(
                    ChannelSpeakerProfileRecord.source_channel_id
                    == candidate.source_channel_id,
                    ChannelSpeakerProfileRecord.speaker_id == speaker.id,
                )
            )
            if (
                candidate.decision == ChannelVoiceCandidateDecision.SAVED
                and profile is not None
            ):
                if profile.state == ChannelSpeakerProfileState.INACTIVE:
                    profile.state = ChannelSpeakerProfileState.ACTIVE
                    profile.updated_at = self.dependencies.clock.now()
                return _channel_speaker_profile(profile, speaker, reference)
            if candidate.decision != ChannelVoiceCandidateDecision.PENDING:
                raise RuntimeError(
                    "Channel voice candidate is not awaiting a decision"
                )
            normalized_name = (display_name or "").strip()
            if not normalized_name:
                profile_count = session.scalar(
                    select(func.count(ChannelSpeakerProfileRecord.id)).where(
                        ChannelSpeakerProfileRecord.source_channel_id
                        == candidate.source_channel_id
                    )
                )
                normalized_name = (
                    f"Unnamed speaker {int(profile_count or 0) + 1}"
                )
            approved = session.scalars(
                select(SpeakerVoiceReferenceRecord).where(
                    SpeakerVoiceReferenceRecord.speaker_id == speaker.id,
                    SpeakerVoiceReferenceRecord.status == "approved",
                )
            ).all()
            for current in approved:
                current.status = "superseded"
            reference.status = "approved"
            reference.approved_at = self.dependencies.clock.now()
            speaker.canonical_voice_reference = reference.artifact_key
            speaker.status = "verified"
            speaker.verified = True
            if profile is None:
                profile = ChannelSpeakerProfileRecord(
                    source_channel_id=candidate.source_channel_id,
                    speaker_id=speaker.id,
                    display_name=normalized_name,
                    state="active",
                    created_at=self.dependencies.clock.now(),
                    updated_at=self.dependencies.clock.now(),
                )
                session.add(profile)
                session.flush()
            else:
                profile.display_name = normalized_name
                profile.state = ChannelSpeakerProfileState.ACTIVE
                profile.updated_at = self.dependencies.clock.now()
            candidate.decision = ChannelVoiceCandidateDecision.SAVED
            candidate.decided_at = self.dependencies.clock.now()
            return _channel_speaker_profile(profile, speaker, reference)

    def ignore_channel_voice_candidate(
        self,
        candidate_id: int,
    ) -> ChannelVoiceCandidateRecord:
        return self._set_channel_voice_candidate_decision(
            candidate_id,
            current_decisions={
                ChannelVoiceCandidateDecision.PENDING,
                ChannelVoiceCandidateDecision.IGNORED,
            },
            next_decision=ChannelVoiceCandidateDecision.IGNORED,
        )

    def restore_channel_voice_candidate(
        self,
        candidate_id: int,
    ) -> ChannelVoiceCandidateRecord:
        return self._set_channel_voice_candidate_decision(
            candidate_id,
            current_decisions={
                ChannelVoiceCandidateDecision.IGNORED,
                ChannelVoiceCandidateDecision.PENDING,
            },
            next_decision=ChannelVoiceCandidateDecision.PENDING,
        )

    def _set_channel_voice_candidate_decision(
        self,
        candidate_id: int,
        *,
        current_decisions: set[ChannelVoiceCandidateDecision],
        next_decision: ChannelVoiceCandidateDecision,
    ) -> ChannelVoiceCandidateRecord:
        with self.dependencies.database.session() as session:
            candidate = session.get(
                ChannelVoiceCandidateRecord,
                candidate_id,
            )
            if candidate is None:
                raise LookupError("Channel voice candidate was not found")
            if candidate.decision not in current_decisions:
                raise RuntimeError(
                    f"Channel voice candidate cannot change to {next_decision}"
                )
            candidate.decision = next_decision
            candidate.decided_at = (
                None
                if next_decision == ChannelVoiceCandidateDecision.PENDING
                else self.dependencies.clock.now()
            )
            return candidate

    def stop_channel_speaker_profile(
        self,
        profile_id: int,
    ) -> ChannelSpeakerProfile:
        with self.dependencies.database.session() as session:
            profile, speaker, reference = _load_channel_speaker_profile(
                session,
                profile_id,
            )
            profile.state = ChannelSpeakerProfileState.INACTIVE
            profile.updated_at = self.dependencies.clock.now()
            return _channel_speaker_profile(profile, speaker, reference)

    def rename_channel_speaker_profile(
        self,
        profile_id: int,
        display_name: str,
    ) -> ChannelSpeakerProfile:
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("Speaker display name is required")
        with self.dependencies.database.session() as session:
            profile, speaker, reference = _load_channel_speaker_profile(
                session,
                profile_id,
            )
            profile.display_name = normalized_name
            profile.updated_at = self.dependencies.clock.now()
            return _channel_speaker_profile(profile, speaker, reference)

    def merge_channel_voice_candidate(
        self,
        candidate_id: int,
        target_profile_id: int,
        *,
        replace_canonical_reference: bool,
    ) -> ChannelSpeakerProfile:
        with self.dependencies.database.session() as session:
            candidate = session.get(
                ChannelVoiceCandidateRecord,
                candidate_id,
            )
            profile = session.get(
                ChannelSpeakerProfileRecord,
                target_profile_id,
            )
            if candidate is None or profile is None:
                raise LookupError(
                    "Channel voice candidate or target profile was not found"
                )
            source_speaker = session.get(
                SpeakerRecord,
                candidate.speaker_id,
            )
            if source_speaker is None:
                raise LookupError("Speaker Voice Reference was not found")
            if (
                candidate.decision == ChannelVoiceCandidateDecision.MERGED
                and profile.source_channel_id == candidate.source_channel_id
                and source_speaker.merged_into_id == profile.speaker_id
            ):
                target_speaker = session.get(
                    SpeakerRecord,
                    profile.speaker_id,
                )
                canonical_reference = session.scalar(
                    select(SpeakerVoiceReferenceRecord).where(
                        SpeakerVoiceReferenceRecord.speaker_id
                        == profile.speaker_id,
                        SpeakerVoiceReferenceRecord.status == "approved",
                    )
                )
                if target_speaker is None or canonical_reference is None:
                    raise LookupError(
                        "Canonical Voice Reference was not found"
                    )
                return _channel_speaker_profile(
                    profile,
                    target_speaker,
                    canonical_reference,
                )
            if candidate.decision != ChannelVoiceCandidateDecision.PENDING:
                raise RuntimeError(
                    "Channel voice candidate is not awaiting a decision"
                )
            if (
                profile.source_channel_id != candidate.source_channel_id
                or profile.state != ChannelSpeakerProfileState.ACTIVE
            ):
                raise ValueError(
                    "Target profile must be active in the same Source Channel"
                )
            target_speaker = session.get(SpeakerRecord, profile.speaker_id)
            candidate_reference = session.get(
                SpeakerVoiceReferenceRecord,
                candidate.voice_reference_id,
            )
            if (
                source_speaker is None
                or target_speaker is None
                or candidate_reference is None
            ):
                raise LookupError("Speaker Voice Reference was not found")
            if source_speaker.id == target_speaker.id:
                raise ValueError("A Speaker cannot be merged into itself")
            canonical_reference = session.scalar(
                select(SpeakerVoiceReferenceRecord).where(
                    SpeakerVoiceReferenceRecord.speaker_id
                    == target_speaker.id,
                    SpeakerVoiceReferenceRecord.status == "approved",
                )
            )
            if canonical_reference is None:
                raise LookupError("Canonical Voice Reference was not found")
            if replace_canonical_reference:
                canonical_reference.status = "superseded"
                canonical_reference = SpeakerVoiceReferenceRecord(
                    speaker_id=target_speaker.id,
                    artifact_key=candidate_reference.artifact_key,
                    status="approved",
                    created_at=self.dependencies.clock.now(),
                    approved_at=self.dependencies.clock.now(),
                )
                session.add(canonical_reference)
                session.flush()
                target_speaker.canonical_voice_reference = (
                    canonical_reference.artifact_key
                )
            source_speaker.status = "merged"
            source_speaker.verified = False
            source_speaker.merged_into_id = target_speaker.id
            candidate.decision = ChannelVoiceCandidateDecision.MERGED
            candidate.decided_at = self.dependencies.clock.now()
            profile.updated_at = self.dependencies.clock.now()
            return _channel_speaker_profile(
                profile,
                target_speaker,
                canonical_reference,
            )

    def propose_voice_reference(
        self,
        speaker_id: int,
        artifact_key: str,
    ) -> SpeakerVoiceReferenceRecord:
        with self.dependencies.database.session() as session:
            speaker = session.get(SpeakerRecord, speaker_id)
            if speaker is None or speaker.status == "merged":
                raise LookupError("Speaker was not found")
            reference = SpeakerVoiceReferenceRecord(
                speaker_id=speaker.id,
                artifact_key=artifact_key.strip(),
                status="proposed",
                created_at=self.dependencies.clock.now(),
            )
            session.add(reference)
            session.flush()
            return reference

    def approve_voice_reference(
        self,
        speaker_id: int,
        reference_id: int,
    ) -> SpeakerRecord:
        with self.dependencies.database.session() as session:
            speaker = session.get(SpeakerRecord, speaker_id)
            reference = session.get(
                SpeakerVoiceReferenceRecord,
                reference_id,
            )
            if (
                speaker is None
                or reference is None
                or reference.speaker_id != speaker.id
            ):
                raise LookupError("Speaker Voice Reference was not found")
            if reference.status != "proposed":
                raise RuntimeError(
                    "Speaker Voice Reference is not awaiting approval"
                )
            current = session.scalars(
                select(SpeakerVoiceReferenceRecord).where(
                    SpeakerVoiceReferenceRecord.speaker_id == speaker.id,
                    SpeakerVoiceReferenceRecord.status == "approved",
                )
            ).all()
            for item in current:
                item.status = "superseded"
            reference.status = "approved"
            reference.approved_at = self.dependencies.clock.now()
            speaker.canonical_voice_reference = reference.artifact_key
            return speaker

    def voice_references(
        self,
        speaker_id: int,
    ) -> list[SpeakerVoiceReferenceRecord]:
        with self.dependencies.database.session() as session:
            if session.get(SpeakerRecord, speaker_id) is None:
                raise LookupError("Speaker was not found")
            return list(
                session.scalars(
                    select(SpeakerVoiceReferenceRecord)
                    .where(
                        SpeakerVoiceReferenceRecord.speaker_id == speaker_id
                    )
                    .order_by(SpeakerVoiceReferenceRecord.created_at.desc())
                ).all()
            )

    def voice_reference_audio(
        self,
        reference_id: int,
    ) -> tuple[bytes, str]:
        with self.dependencies.database.session() as session:
            reference = session.get(
                SpeakerVoiceReferenceRecord,
                reference_id,
            )
            if reference is None:
                raise LookupError("Speaker Voice Reference was not found")
            path = Path(reference.artifact_key)
            try:
                content = path.read_bytes()
            except OSError as error:
                raise LookupError(
                    "Speaker Voice Reference audio is unavailable"
                ) from error
            media_type = {
                ".m4a": "audio/mp4",
                ".mp3": "audio/mpeg",
                ".ogg": "audio/ogg",
                ".wav": "audio/wav",
                ".webm": "audio/webm",
            }.get(path.suffix.lower(), "application/octet-stream")
            return content, media_type

    def confirm_speaker(self, speaker_id: int) -> SpeakerRecord:
        with self.dependencies.database.session() as session:
            speaker = session.get(SpeakerRecord, speaker_id)
            if speaker is None or speaker.status == "merged":
                raise LookupError("Speaker was not found")
            approved = session.scalar(
                select(SpeakerVoiceReferenceRecord).where(
                    SpeakerVoiceReferenceRecord.speaker_id == speaker.id,
                    SpeakerVoiceReferenceRecord.status == "approved",
                )
            )
            if approved is None:
                proposed = session.scalar(
                    select(SpeakerVoiceReferenceRecord)
                    .where(
                        SpeakerVoiceReferenceRecord.speaker_id == speaker.id,
                        SpeakerVoiceReferenceRecord.status == "proposed",
                    )
                    .order_by(SpeakerVoiceReferenceRecord.created_at.desc())
                )
                if proposed is None:
                    raise RuntimeError(
                        "Approve a Canonical Voice Reference first"
                    )
                proposed.status = "approved"
                proposed.approved_at = self.dependencies.clock.now()
                speaker.canonical_voice_reference = proposed.artifact_key
            speaker.status = "verified"
            speaker.verified = True
            return speaker

    def merge_speaker(
        self,
        speaker_id: int,
        target_speaker_id: int,
    ) -> SpeakerRecord:
        if speaker_id == target_speaker_id:
            raise ValueError("A Speaker cannot be merged into itself")
        with self.dependencies.database.session() as session:
            speaker = session.get(SpeakerRecord, speaker_id)
            target = session.get(SpeakerRecord, target_speaker_id)
            if (
                speaker is None
                or target is None
                or speaker.status == "merged"
                or target.status == "merged"
            ):
                raise LookupError("Speaker was not found")
            segments = session.scalars(
                select(EpisodeSegmentRecord).where(
                    EpisodeSegmentRecord.speaker_id == speaker.id
                )
            ).all()
            for segment in segments:
                segment.speaker_id = target.id
                segment.updated_at = self.dependencies.clock.now()
            speaker.status = "merged"
            speaker.verified = False
            speaker.merged_into_id = target.id
            return speaker

    def split_speaker(
        self,
        speaker_id: int,
        display_name: str,
        episode_id: int,
        segment_ordinals: list[int],
    ) -> SpeakerRecord:
        normalized_name = display_name.strip()
        ordinals = sorted(set(segment_ordinals))
        if (
            not normalized_name
            or not ordinals
            or any(ordinal <= 0 for ordinal in ordinals)
        ):
            raise ValueError("Speaker split selection is invalid")
        with self.dependencies.database.session() as session:
            speaker = session.get(SpeakerRecord, speaker_id)
            episode = session.get(EpisodeRecord, episode_id)
            if (
                speaker is None
                or speaker.status == "merged"
                or episode is None
            ):
                raise LookupError(
                    "Speaker or Translated Episode was not found"
                )
            segments = session.scalars(
                select(EpisodeSegmentRecord).where(
                    EpisodeSegmentRecord.episode_id == episode_id,
                    EpisodeSegmentRecord.ordinal.in_(ordinals),
                    EpisodeSegmentRecord.speaker_id == speaker.id,
                )
            ).all()
            if len(segments) != len(ordinals):
                raise ValueError(
                    "Every selected segment must belong to the Speaker"
                )
            split = SpeakerRecord(
                display_name=normalized_name,
                canonical_voice_reference=(speaker.canonical_voice_reference),
                embedding_json=speaker.embedding_json,
                verified=False,
                status="provisional",
                created_at=self.dependencies.clock.now(),
            )
            session.add(split)
            session.flush()
            session.add(
                SpeakerVoiceReferenceRecord(
                    speaker_id=split.id,
                    artifact_key=split.canonical_voice_reference,
                    status="proposed",
                    created_at=self.dependencies.clock.now(),
                )
            )
            for segment in segments:
                segment.speaker_id = split.id
                segment.updated_at = self.dependencies.clock.now()
            return split

    def queue_transcript_revision(
        self,
        episode_id: int,
        ordinal: int,
        source_text: str,
    ) -> TranscriptRevisionJobRecord:
        normalized = source_text.strip()
        if not normalized:
            raise ValueError("Canonical Transcript segment cannot be empty")
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            segment = session.scalar(
                select(EpisodeSegmentRecord).where(
                    EpisodeSegmentRecord.episode_id == episode_id,
                    EpisodeSegmentRecord.ordinal == ordinal,
                )
            )
            if segment is None:
                raise LookupError("Episode segment was not found")
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            if (
                run is None
                or run.analysis_json is None
                or run.translation_json is None
            ):
                raise RuntimeError("Transcript Revision inputs are incomplete")
            render_plan = build_render_plan(
                deserialize_analysis(run.analysis_json),
                deserialize_translation(
                    run.translation_json,
                    run.content_edit_json,
                ),
            )
            if render_plan.exclusions:
                raise RuntimeError(
                    "Canonical Transcript revisions for content-edited "
                    "episodes require full episode reprocessing"
                )
            transcript_job = session.scalar(
                select(TranscriptRevisionJobRecord.id).where(
                    TranscriptRevisionJobRecord.episode_id == episode_id,
                    TranscriptRevisionJobRecord.ordinal == ordinal,
                    TranscriptRevisionJobRecord.status.in_(
                        {"queued", "processing"}
                    ),
                )
            )
            render_job = session.scalar(
                select(SegmentRevisionJobRecord.id).where(
                    SegmentRevisionJobRecord.episode_id == episode_id,
                    SegmentRevisionJobRecord.ordinal == ordinal,
                    SegmentRevisionJobRecord.status.in_(
                        {"queued", "processing"}
                    ),
                )
            )
            if transcript_job is not None or render_job is not None:
                raise RuntimeError(
                    "This segment already has a pending revision"
                )
            job = TranscriptRevisionJobRecord(
                episode_id=episode_id,
                ordinal=ordinal,
                source_text=normalized,
                status="queued",
                created_at=self.dependencies.clock.now(),
            )
            session.add(job)
            episode.state = EpisodeState.REVISION_PROCESSING.value
            self._close_episode_actions(session, episode.id)
            session.flush()
            return job

    def _execute_transcript_revision(
        self,
        revision_job_id: int,
    ) -> dict[str, object]:
        with self.dependencies.database.session() as session:
            job = session.get(
                TranscriptRevisionJobRecord,
                revision_job_id,
            )
            if job is None:
                raise RuntimeError("Claimed Transcript Revision is missing")
            episode = session.get(EpisodeRecord, job.episode_id)
            if episode is None:
                raise RuntimeError("Transcript Revision episode is missing")
            segment = session.scalar(
                select(EpisodeSegmentRecord).where(
                    EpisodeSegmentRecord.episode_id == episode.id,
                    EpisodeSegmentRecord.ordinal == job.ordinal,
                )
            )
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            candidate = session.get(
                CandidateVideoRecord,
                episode.candidate_video_id,
            )
            if (
                segment is None
                or run is None
                or candidate is None
                or run.analysis_json is None
                or run.translation_json is None
            ):
                raise RuntimeError("Transcript Revision inputs are incomplete")
            analysis_value = json.loads(run.analysis_json)
            analysis_segments = analysis_value.get("segments")
            if not isinstance(analysis_segments, list):
                raise RuntimeError("Canonical Transcript is malformed")
            translated_segments = list(analysis_segments)
            if not 1 <= job.ordinal <= len(translated_segments):
                raise ValueError("Canonical Transcript segment is missing")
            revised_source = translated_segments[job.ordinal - 1]
            revised_source["text"] = job.source_text
            revised_source["words"] = []
            revised_source["review_state"] = "owner_revised"
            analysis_value["checksum"] = _content_hash(
                {
                    key: value
                    for key, value in analysis_value.items()
                    if key != "checksum"
                }
            )
            analysis_json = json.dumps(
                analysis_value,
                ensure_ascii=False,
            )
            source = SourceCandidate(
                youtube_video_id=candidate.youtube_video_id,
                title=candidate.title,
                description=candidate.description,
                duration_seconds=candidate.duration_seconds,
                language=candidate.language,
                published_at=candidate.published_at,
                source_url=candidate.source_url,
                captions=candidate.captions,
                analysis_json=analysis_json,
            )
            current_translation = deserialize_translation(run.translation_json)
            try:
                revised_translation = (
                    self.dependencies.translation.retranslate_segment(
                        source,
                        current_translation,
                        job.ordinal,
                    )
                )
            except (RuntimeError, ValueError) as error:
                job.status = "review_queue"
                job.error_summary = str(error)
                job.lease_expires_at = None
                if self._notification_enabled("processing_failure"):
                    self.dependencies.notifications.send(
                        OwnerNotification(
                            kind="processing_failure",
                            title=episode.title,
                            body=str(error),
                            actions=(),
                        )
                    )
                return {
                    "revision_job_id": job.id,
                    "revision_state": job.status,
                    "episode_id": episode.id,
                }
            previous_analysis_hash = _content_hash(
                json.loads(run.analysis_json)
            )
            previous_translation_hash = _content_hash(
                json.loads(run.translation_json)
            )
            run.analysis_json = analysis_json
            run.translation_json = _serialize_translation(revised_translation)
            run.translation_provider = revised_translation.provider
            run.translation_model = revised_translation.model
            run.input_tokens = revised_translation.input_tokens
            run.output_tokens = revised_translation.output_tokens
            episode.transcript_json = analysis_json
            segment.source_text = job.source_text
            segment.updated_at = self.dependencies.clock.now()
            latest_version = session.scalar(
                select(
                    func.max(CanonicalTranscriptRevisionRecord.version)
                ).where(
                    CanonicalTranscriptRevisionRecord.episode_id == episode.id,
                    CanonicalTranscriptRevisionRecord.ordinal == job.ordinal,
                )
            )
            session.add(
                CanonicalTranscriptRevisionRecord(
                    episode_id=episode.id,
                    ordinal=job.ordinal,
                    version=int(latest_version or 0) + 1,
                    source_text=job.source_text,
                    transcript_json=persist_structured_artifact(
                        session,
                        artifact_kind="canonical_transcript",
                        payload=analysis_json,
                        created_at=self.dependencies.clock.now(),
                    ),
                    created_at=self.dependencies.clock.now(),
                )
            )
            self._record_stage(
                session,
                run,
                stage="transcript_revision",
                version="canonical-transcript-revision-v1",
                provider=None,
                model=None,
                input_hash=previous_analysis_hash,
                output_hash=_content_hash(analysis_value),
                usage={"segment_ordinal": job.ordinal},
            )
            self._record_stage(
                session,
                run,
                stage="translation_revision",
                version=revised_translation.policy_version,
                provider=revised_translation.provider,
                model=revised_translation.model,
                input_hash=previous_translation_hash,
                output_hash=revised_translation.output_hash
                or _content_hash(revised_translation.segments),
                usage={
                    "input_tokens": revised_translation.input_tokens,
                    "output_tokens": revised_translation.output_tokens,
                    "elapsed_ms": revised_translation.elapsed_ms,
                    "segment_ordinal": job.ordinal,
                    "codex_cli_version": (revised_translation.cli_version),
                },
                duration_ms=revised_translation.elapsed_ms,
            )
            render_job = SegmentRevisionJobRecord(
                episode_id=episode.id,
                ordinal=job.ordinal,
                target_text=revised_translation.segments[job.ordinal - 1],
                speaker_id=segment.speaker_id,
                emotion_reference=segment.emotion_reference,
                status="queued",
                created_at=self.dependencies.clock.now(),
            )
            session.add(render_job)
            session.flush()
            job.status = "completed"
            job.completed_at = self.dependencies.clock.now()
            job.lease_expires_at = None
            return {
                "revision_job_id": job.id,
                "revision_state": "render_queued",
                "render_job_id": render_job.id,
                "episode_id": episode.id,
                "segment_ordinal": job.ordinal,
            }

    def transcript_revisions(
        self,
        episode_id: int,
        ordinal: int,
    ) -> list[CanonicalTranscriptRevisionRecord]:
        with self.dependencies.database.session() as session:
            segment = session.scalar(
                select(EpisodeSegmentRecord.id).where(
                    EpisodeSegmentRecord.episode_id == episode_id,
                    EpisodeSegmentRecord.ordinal == ordinal,
                )
            )
            if segment is None:
                raise LookupError("Episode segment was not found")
            return list(
                session.scalars(
                    select(CanonicalTranscriptRevisionRecord)
                    .where(
                        CanonicalTranscriptRevisionRecord.episode_id
                        == episode_id,
                        CanonicalTranscriptRevisionRecord.ordinal == ordinal,
                    )
                    .order_by(CanonicalTranscriptRevisionRecord.version.desc())
                ).all()
            )

    def queue_segment_revision(
        self,
        episode_id: int,
        ordinal: int,
        target_text: str,
        speaker_id: int | None,
        emotion_reference: str | None,
    ) -> SegmentRevisionJobRecord:
        normalized = target_text.strip()
        if not normalized:
            raise ValueError("Segment translation cannot be empty")
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            segment = session.scalar(
                select(EpisodeSegmentRecord).where(
                    EpisodeSegmentRecord.episode_id == episode_id,
                    EpisodeSegmentRecord.ordinal == ordinal,
                )
            )
            if segment is None:
                raise LookupError("Episode segment was not found")
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            if (
                run is None
                or run.analysis_json is None
                or run.translation_json is None
            ):
                raise RuntimeError("Revision inputs are incomplete")
            analysis = deserialize_analysis(run.analysis_json)
            if not 1 <= ordinal <= len(analysis.segments):
                raise ValueError("Revision analysis segment is missing")
            render_plan = build_render_plan(
                analysis,
                deserialize_translation(
                    run.translation_json,
                    run.content_edit_json,
                ),
            )
            if (
                _render_ordinal_for_canonical_segment(
                    render_plan,
                    analysis.segments[ordinal - 1].segment_id,
                )
                is None
            ):
                raise RuntimeError(
                    "Segments removed or trimmed by Content Editing require "
                    "full episode reprocessing"
                )
            if speaker_id is not None:
                speaker = session.get(SpeakerRecord, speaker_id)
                if speaker is None:
                    raise ValueError("Speaker was not found")
            active = session.scalar(
                select(SegmentRevisionJobRecord.id).where(
                    SegmentRevisionJobRecord.episode_id == episode_id,
                    SegmentRevisionJobRecord.ordinal == ordinal,
                    SegmentRevisionJobRecord.status.in_(
                        {"queued", "processing"}
                    ),
                )
            )
            if active is not None:
                raise RuntimeError(
                    "This segment already has a pending revision"
                )
            job = SegmentRevisionJobRecord(
                episode_id=episode_id,
                ordinal=ordinal,
                target_text=normalized,
                speaker_id=speaker_id,
                emotion_reference=emotion_reference,
                status="queued",
                created_at=self.dependencies.clock.now(),
            )
            session.add(job)
            episode.state = EpisodeState.REVISION_PROCESSING.value
            self._close_episode_actions(session, episode.id)
            session.flush()
            return job

    def _execute_segment_revision(
        self,
        revision_job_id: int,
    ) -> dict[str, object]:
        with self.dependencies.database.session() as session:
            job = session.get(
                SegmentRevisionJobRecord,
                revision_job_id,
            )
            if job is None:
                raise RuntimeError("Claimed Segment Revision is missing")
            episode = session.get(EpisodeRecord, job.episode_id)
            if episode is None:
                raise RuntimeError("Segment Revision episode is missing")
            segment = session.scalar(
                select(EpisodeSegmentRecord).where(
                    EpisodeSegmentRecord.episode_id == episode.id,
                    EpisodeSegmentRecord.ordinal == job.ordinal,
                )
            )
            if segment is None:
                raise RuntimeError("Segment Revision segment is missing")
            target_text = job.target_text
            speaker_id = job.speaker_id
            emotion_reference = job.emotion_reference
            run = session.get(ProcessingRunRecord, episode.processing_run_id)
            candidate = session.get(
                CandidateVideoRecord,
                episode.candidate_video_id,
            )
            if (
                run is None
                or candidate is None
                or run.translation_json is None
            ):
                raise RuntimeError("Revision inputs are incomplete")
            if run.analysis_json is None:
                raise RuntimeError("Revision analysis is incomplete")
            analysis = deserialize_analysis(run.analysis_json)
            translation = deserialize_translation(
                run.translation_json,
                run.content_edit_json,
            )
            translated_segments = list(translation.segments)
            if (
                job.ordinal > len(translated_segments)
                or job.ordinal > len(analysis.segments)
            ):
                raise ValueError("Translation segment is missing")
            translated_segments[job.ordinal - 1] = target_text.strip()
            revised_translation = replace(
                translation,
                segments=tuple(translated_segments),
                output_hash="",
            )
            render_plan = build_render_plan(analysis, revised_translation)
            render_ordinal = _render_ordinal_for_canonical_segment(
                render_plan,
                analysis.segments[job.ordinal - 1].segment_id,
            )
            if render_ordinal is None:
                job.status = "review_queue"
                job.error_summary = (
                    "Segments removed or trimmed by Content Editing require "
                    "full episode reprocessing"
                )
                job.lease_expires_at = None
                return {
                    "revision_job_id": job.id,
                    "revision_state": job.status,
                    "episode_id": episode.id,
                }
            source = SourceCandidate(
                youtube_video_id=candidate.youtube_video_id,
                title=candidate.title,
                description=candidate.description,
                duration_seconds=candidate.duration_seconds,
                language=candidate.language,
                published_at=candidate.published_at,
                source_url=candidate.source_url,
                captions=candidate.captions,
            )
            archive_content = (
                self.dependencies.storage.read_source_archive(
                    run.source_archive_key
                )
                if run.source_archive_key is not None
                else None
            )
            prior_preview = self.dependencies.storage.read_preview(
                episode.preview_key
            )
            if (
                archive_content is None
                or run.source_archive_key is None
                or run.source_archive_checksum is None
                or prior_preview is None
                or run.analysis_json is None
            ):
                raise RuntimeError("Revision artifacts are incomplete")
            render_analysis = json.loads(
                _serialize_analysis(render_plan.analysis)
            )
            render_segments = list(render_analysis.get("segments", []))
            if not 1 <= render_ordinal <= len(render_segments):
                raise ValueError("Revision analysis segment is missing")
            render_segment = render_segments[render_ordinal - 1]
            if speaker_id is not None:
                selected_speaker = session.get(SpeakerRecord, speaker_id)
                if selected_speaker is None:
                    raise ValueError("Revision Speaker is unavailable")
                local_label = f"registry-speaker-{selected_speaker.id}"
                render_segment["speaker_label"] = local_label
                analysis_speakers = list(render_analysis.get("speakers", []))
                analysis_speakers = [
                    item
                    for item in analysis_speakers
                    if str(item.get("local_label")) != local_label
                ]
                analysis_speakers.append(
                    {
                        "local_label": local_label,
                        "registry_speaker_id": selected_speaker.id,
                        "reference_artifact_key": (
                            selected_speaker.canonical_voice_reference
                        ),
                        "embedding": json.loads(
                            selected_speaker.embedding_json
                        ),
                        "confidence": 1.0,
                    }
                )
                render_analysis["speakers"] = analysis_speakers
            if emotion_reference:
                emotion_path = Path(emotion_reference).resolve()
                artifact_root = self.dependencies.artifact_root.resolve()
                if not emotion_path.is_file() or (
                    emotion_path != artifact_root
                    and artifact_root not in emotion_path.parents
                ):
                    raise ValueError(
                        "Emotion Reference must be a stored TransPodcast artifact"
                    )
                render_segment["emotion_reference_artifact_key"] = str(
                    emotion_path
                )
            archive_extension = run.source_archive_key.rsplit(".", 1)[-1]
            source = replace(
                source,
                analysis_json=json.dumps(
                    render_analysis,
                    ensure_ascii=False,
                ),
                source_archive=SourceArchive(
                    content=archive_content,
                    extension=archive_extension,
                    media_type=(
                        "audio/mp4"
                        if archive_extension == "m4a"
                        else "audio/webm"
                    ),
                    checksum=run.source_archive_checksum,
                ),
                prior_preview_audio=prior_preview,
                prior_render_json=run.render_json,
            )
            try:
                speech = self.dependencies.speech.render_segment(
                    source,
                    render_plan.translation,
                    render_ordinal,
                    episode.preview_key,
                )
                self._validate_speech(
                    speech,
                    len(render_plan.translation.segments),
                )
            except SpendGuardrailReached as error:
                self._write_setting(
                    session,
                    "spend_guardrail_paused",
                    True,
                )
                job.status = "spend_guardrail"
                job.error_summary = str(error)
                job.lease_expires_at = None
                return {
                    "revision_job_id": job.id,
                    "revision_state": job.status,
                    "episode_id": episode.id,
                }
            except (RuntimeError, ValueError) as error:
                job.status = "review_queue"
                job.error_summary = str(error)
                job.lease_expires_at = None
                if self._notification_enabled("processing_failure"):
                    self.dependencies.notifications.send(
                        OwnerNotification(
                            kind="processing_failure",
                            title=episode.title,
                            body=str(error),
                            actions=(),
                        )
                    )
                return {
                    "revision_job_id": job.id,
                    "revision_state": job.status,
                    "episode_id": episode.id,
                }
            next_revision = episode.revision_version + 1
            preview_key = (
                f"episodes/{episode.publication_guid}/"
                f"revision-{next_revision}/preview.m4a"
            )
            self.dependencies.storage.put_preview(
                preview_key,
                speech.publication_audio,
            )
            run.translation_json = _serialize_translation(revised_translation)
            episode.preview_key = preview_key
            episode.preview_expires_at = (
                self.dependencies.clock.now() + timedelta(days=7)
            )
            episode.duration_seconds = speech.duration_seconds
            try:
                publication_metadata = parse_publication_metadata(
                    run.publication_metadata_json or "{}"
                )
            except ValueError as error:
                publication_metadata = degraded_publication_metadata(
                    episode.overview,
                    str(error),
                )
            revised_metadata = _resolve_publication_metadata(
                publication_metadata,
                tuple(
                    item.segment_id
                    for item in render_plan.analysis.segments
                ),
                speech.segment_timings,
                speech.duration_seconds,
            )
            episode.chapters_json = json.dumps(
                revised_metadata.chapters
            )
            episode.quality_json = _serialize_quality(
                speech,
                len(render_plan.translation.segments),
            )
            run.render_json = _serialize_speech_checkpoint(speech)
            run.render_preview_key = preview_key
            episode.revision_version = next_revision
            episode.revision_kind = PublicationRevisionKind.AUDIO.value
            episode.state = (
                EpisodeState.REVISION_PENDING.value
                if episode.publication_key is not None
                else EpisodeState.PUBLICATION_QUEUE.value
            )
            segment.target_text = target_text.strip()
            segment.speaker_id = speaker_id
            segment.emotion_reference = emotion_reference
            segment.version += 1
            segment.updated_at = self.dependencies.clock.now()
            session.add(
                EpisodeSegmentRevisionRecord(
                    episode_id=episode.id,
                    ordinal=segment.ordinal,
                    version=segment.version,
                    target_text=segment.target_text,
                    speaker_id=segment.speaker_id,
                    emotion_reference=segment.emotion_reference,
                    preview_key=episode.preview_key,
                    translation_json=persist_structured_artifact(
                        session,
                        artifact_kind="translation",
                        payload=run.translation_json,
                        created_at=self.dependencies.clock.now(),
                    ),
                    created_at=self.dependencies.clock.now(),
                )
            )
            self._send_publication_decision(session, episode)
            job.status = "completed"
            job.completed_at = self.dependencies.clock.now()
            job.lease_expires_at = None
            return {
                "revision_job_id": job.id,
                "revision_state": job.status,
                "episode_id": episode.id,
                "segment_ordinal": segment.ordinal,
                "segment_version": segment.version,
                "episode_state": episode.state,
            }

    def segment_revisions(
        self,
        episode_id: int,
        ordinal: int,
    ) -> list[EpisodeSegmentRevisionRecord]:
        with self.dependencies.database.session() as session:
            segment = session.scalar(
                select(EpisodeSegmentRecord).where(
                    EpisodeSegmentRecord.episode_id == episode_id,
                    EpisodeSegmentRecord.ordinal == ordinal,
                )
            )
            if segment is None:
                raise LookupError("Episode segment was not found")
            return list(
                session.scalars(
                    select(EpisodeSegmentRevisionRecord)
                    .where(
                        EpisodeSegmentRevisionRecord.episode_id == episode_id,
                        EpisodeSegmentRevisionRecord.ordinal == ordinal,
                    )
                    .order_by(EpisodeSegmentRevisionRecord.version.desc())
                ).all()
            )

    def resubmit_episode(self, episode_id: int) -> EpisodeRecord:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            if episode.state != EpisodeState.UNPUBLISHED.value:
                raise RuntimeError("Only a declined draft can be resubmitted")
            episode.revision_version += 1
            episode.state = EpisodeState.PUBLICATION_QUEUE.value
            self._send_publication_decision(session, episode)
            return episode

    def send_publication_decision(
        self,
        episode_id: int,
    ) -> EpisodeRecord:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            if episode.state not in {
                EpisodeState.PUBLICATION_QUEUE.value,
                EpisodeState.REVISION_PENDING.value,
            }:
                raise RuntimeError(
                    "Episode is not awaiting a publication decision"
                )
            self._send_publication_decision(session, episode)
        self.deliver_pending_publication_notifications()
        return episode

    def delete_episode(self, episode_id: int) -> EpisodeRecord:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            if episode.state == "deleted":
                return episode
            episode.state = "deleted"
            episode.deleted_at = self.dependencies.clock.now()
            episode.recovery_until = self.dependencies.clock.now() + timedelta(
                days=30
            )
            self._store_feed_snapshot(session)
            return episode

    def recover_episode(self, episode_id: int) -> EpisodeRecord:
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            if episode.state != "deleted":
                raise RuntimeError("Translated Episode is not deleted")
            if (
                episode.recovery_until is None
                or episode.recovery_until < self.dependencies.clock.now()
            ):
                raise PermissionError("Recovery Window has expired")
            episode.state = (
                EpisodeState.PUBLISHED.value
                if episode.publication_key is not None
                else EpisodeState.UNPUBLISHED.value
            )
            episode.deleted_at = None
            episode.recovery_until = None
            self._store_feed_snapshot(session)
            return episode

    def apply_publication_decision(
        self,
        episode_id: int,
        decision: str,
        *,
        channel: str,
        expected_state: str | None = None,
    ) -> dict[str, object]:
        if decision not in {"publish", "do_not_publish"}:
            raise ValueError("Publication decision is not supported")
        if channel not in {"owner_console", "telegram"}:
            raise ValueError("Publication decision channel is not supported")
        completed_message_id: str | None = None
        with self.dependencies.database.session() as session:
            # Publication is a single-owner operation, but Web and Telegram
            # can submit concurrently. Serialize the read/validate/apply
            # transaction before any immutable storage promotion occurs.
            session.execute(text("BEGIN IMMEDIATE"))
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            latest = session.scalar(
                select(PublicationDecisionRecord)
                .where(PublicationDecisionRecord.episode_id == episode.id)
                .order_by(PublicationDecisionRecord.id.desc())
            )
            if expected_state is not None and episode.state != expected_state:
                if (
                    latest is not None
                    and latest.decision == decision
                    and latest.resulting_state == episode.state
                ):
                    self._close_episode_actions(session, episode.id)
                    return {
                        "episode_id": episode.id,
                        "state": episode.state,
                        "decision": latest.decision,
                        "channel": latest.channel,
                        "already_applied": True,
                    }
                raise RuntimeError(
                    "Episode publication state changed; refresh before deciding"
                )
            eligible_states = {
                EpisodeState.PUBLICATION_QUEUE.value,
                EpisodeState.REVISION_PENDING.value,
            }
            if decision == "publish":
                eligible_states.add(EpisodeState.UNPUBLISHED.value)
            if episode.state not in eligible_states:
                if (
                    latest is not None
                    and latest.decision == decision
                    and latest.resulting_state == episode.state
                ):
                    self._close_episode_actions(session, episode.id)
                    return {
                        "episode_id": episode.id,
                        "state": episode.state,
                        "decision": latest.decision,
                        "channel": latest.channel,
                        "already_applied": True,
                    }
                raise RuntimeError(
                    "Episode is no longer awaiting this publication decision"
                )

            previous_state = episode.state
            prior_publication_version = episode.publication_version
            decision_key = _content_hash(
                {
                    "episode_id": episode.id,
                    "state": previous_state,
                    "publication_version": prior_publication_version,
                    "revision_version": episode.revision_version,
                }
            )
            existing = session.scalar(
                select(PublicationDecisionRecord).where(
                    PublicationDecisionRecord.decision_key == decision_key
                )
            )
            if existing is not None:
                self._close_episode_actions(session, episode.id)
                return {
                    "episode_id": episode.id,
                    "state": existing.resulting_state,
                    "decision": existing.decision,
                    "channel": existing.channel,
                    "already_applied": True,
                }

            if decision == "publish":
                self._publish_episode(session, episode)
            else:
                self._decline_episode_publication(episode)
            decided_at = self.dependencies.clock.now()
            session.add(
                PublicationDecisionRecord(
                    decision_key=decision_key,
                    episode_id=episode.id,
                    decision=decision,
                    channel=channel,
                    owner_identity="owner",
                    previous_state=previous_state,
                    resulting_state=episode.state,
                    prior_publication_version=prior_publication_version,
                    publication_version=episode.publication_version,
                    revision_version=episode.revision_version,
                    artifact_json=json.dumps(
                        {
                            "preview_key": episode.preview_key,
                            "publication_key": episode.publication_key,
                            "publication_artwork_key": (
                                episode.publication_artwork_key
                            ),
                            "publication_chapters_key": (
                                episode.publication_chapters_key
                            ),
                        },
                        sort_keys=True,
                    ),
                    quality_json=episode.quality_json,
                    decided_at=decided_at,
                )
            )
            self._close_episode_actions(session, episode.id)
            notification = session.scalar(
                select(PublicationNotificationRecord).where(
                    PublicationNotificationRecord.episode_id == episode.id,
                    PublicationNotificationRecord.revision_version
                    == episode.revision_version,
                )
            )
            if notification is not None:
                notification.status = "resolved"
                notification.resolution = decision
                notification.resolved_at = decided_at
                notification.updated_at = decided_at
                if channel == "owner_console":
                    completed_message_id = notification.message_id
            self._store_feed_snapshot(session)
            outcome = {
                "episode_id": episode.id,
                "state": episode.state,
                "decision": decision,
                "channel": channel,
                "already_applied": False,
            }
        if completed_message_id is not None:
            with suppress(RuntimeError):
                self.dependencies.notifications.complete_message(
                    completed_message_id,
                    decision,
                )
        return outcome

    def _publish_episode(
        self,
        session: Session,
        episode: EpisodeRecord,
    ) -> None:
        self._validate_publication_inputs(episode)
        version = episode.publication_version + 1
        metadata_only = bool(
            episode.revision_kind == PublicationRevisionKind.METADATA.value
            and episode.publication_key is not None
        )
        if not metadata_only:
            key = (
                f"episodes/{episode.publication_guid}/"
                f"v{version}/audio.m4a"
            )
            try:
                self.dependencies.storage.promote_preview(
                    episode.preview_key,
                    key,
                )
            except (OSError, RuntimeError) as error:
                raise RuntimeError(
                    "Qualified publication audio is unavailable; reprocess "
                    "the episode before publishing"
                ) from error
            episode.publication_key = key
        episode.publication_version = version
        media = self.dependencies.storage.read_publication(
            str(episode.publication_key)
        )
        if media is None or not _is_publication_audio(media):
            raise RuntimeError("Published audio could not be verified in storage")
        episode.publication_bytes = len(media)
        episode.publication_chapters_key = None
        chapters = json.loads(episode.chapters_json)
        if chapters:
            chapters_key = (
                f"episodes/{episode.publication_guid}/"
                f"v{version}/chapters.json"
            )
            chapter_payload = _podcast_chapters(chapters)
            self.dependencies.storage.put_publication(
                chapters_key,
                chapter_payload,
                content_type="application/json+chapters",
            )
            if (
                self.dependencies.storage.read_publication(chapters_key)
                != chapter_payload
            ):
                raise RuntimeError(
                    "Published Episode Chapters could not be verified in storage"
                )
            episode.publication_chapters_key = chapters_key
        artwork_key = episode.publication_artwork_key if metadata_only else None
        artwork_snapshot = (
            episode.published_artwork_json if metadata_only else None
        )
        if episode.artwork_path:
            try:
                artwork = Path(episode.artwork_path).read_bytes()
                validate_episode_artwork(artwork)
                artwork_key = (
                    f"episodes/{episode.publication_guid}/"
                    f"v{version}/artwork.jpg"
                )
                self.dependencies.storage.put_publication(
                    artwork_key,
                    artwork,
                    content_type="image/jpeg",
                )
                artwork_snapshot = episode.artwork_json
            except (OSError, ValueError) as error:
                raise RuntimeError(
                    "Qualified Episode Artwork is unavailable; reprocess the "
                    "episode before publishing"
                ) from error
            stored_artwork = self.dependencies.storage.read_publication(
                artwork_key
            )
            try:
                if stored_artwork is None:
                    raise ValueError("Episode Artwork is missing")
                validate_episode_artwork(stored_artwork)
            except ValueError as error:
                raise RuntimeError(
                    "Published Episode Artwork could not be verified in storage"
                ) from error
        episode.publication_artwork_key = artwork_key
        episode.published_artwork_json = artwork_snapshot
        episode.published_title = episode.title
        episode.published_overview = episode.overview
        episode.published_publication_description = (
            episode.publication_description
        )
        episode.published_duration_seconds = episode.duration_seconds
        episode.published_chapters_json = episode.chapters_json
        episode.revision_kind = PublicationRevisionKind.NONE.value
        episode.state = EpisodeState.PUBLISHED.value
        episode.published_at = self.dependencies.clock.now()

    def _validate_publication_inputs(self, episode: EpisodeRecord) -> None:
        try:
            quality = json.loads(episode.quality_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Quality Gate evidence is unavailable; reprocess the episode "
                "before publishing"
            ) from error
        if not isinstance(quality, dict) or quality.get("eligible") is not True:
            raise RuntimeError(
                "Publication Audio has not passed the Quality Gate; reprocess "
                "the episode before publishing"
            )
        if not all(
            value.strip()
            for value in (
                episode.title,
                episode.overview,
                episode.publication_description,
            )
        ):
            raise RuntimeError(
                "Required publication metadata is incomplete; edit the episode "
                "before publishing"
            )
        try:
            chapters = json.loads(episode.chapters_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Episode Chapters are invalid; edit the episode before publishing"
            ) from error
        if not isinstance(chapters, list):
            raise RuntimeError(
                "Episode Chapters are invalid; edit the episode before publishing"
            )

        metadata_only = bool(
            episode.revision_kind == PublicationRevisionKind.METADATA.value
            and episode.publication_key is not None
        )
        if metadata_only:
            audio = self.dependencies.storage.read_publication(
                str(episode.publication_key)
            )
        else:
            audio = self.dependencies.storage.read_preview(episode.preview_key)
        if audio is None or not _is_publication_audio(audio):
            raise RuntimeError(
                "Qualified publication audio is unavailable; reprocess the "
                "episode before publishing"
            )

        try:
            artwork_metadata = json.loads(episode.artwork_json or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Episode Artwork evidence is invalid; reprocess the episode "
                "before publishing"
            ) from error
        if not isinstance(artwork_metadata, dict):
            raise RuntimeError(
                "Episode Artwork evidence is invalid; reprocess the episode "
                "before publishing"
            )
        if artwork_metadata.get("status") == "completed":
            if not episode.artwork_path:
                raise RuntimeError(
                    "Qualified Episode Artwork is unavailable; reprocess the "
                    "episode before publishing"
                )
            try:
                validate_episode_artwork(Path(episode.artwork_path).read_bytes())
            except (OSError, ValueError) as error:
                raise RuntimeError(
                    "Qualified Episode Artwork is unavailable; reprocess the "
                    "episode before publishing"
                ) from error
        elif metadata_only and episode.publication_artwork_key is not None:
            artwork = self.dependencies.storage.read_publication(
                episode.publication_artwork_key
            )
            try:
                if artwork is None:
                    raise ValueError("Episode Artwork is missing")
                validate_episode_artwork(artwork)
            except ValueError as error:
                raise RuntimeError(
                    "Published Episode Artwork is unavailable; reprocess the "
                    "episode before publishing"
                ) from error

    @staticmethod
    def _decline_episode_publication(episode: EpisodeRecord) -> None:
        if episode.publication_key is not None:
            episode.title = episode.published_title or episode.title
            episode.overview = episode.published_overview or episode.overview
            episode.publication_description = (
                episode.published_publication_description
                or episode.published_overview
                or episode.overview
            )
            episode.duration_seconds = (
                episode.published_duration_seconds or episode.duration_seconds
            )
            episode.chapters_json = episode.published_chapters_json or "[]"
            episode.artwork_json = episode.published_artwork_json or "{}"
            episode.artwork_path = None
            episode.revision_kind = PublicationRevisionKind.NONE.value
            episode.state = EpisodeState.PUBLISHED.value
        else:
            episode.state = EpisodeState.UNPUBLISHED.value

    def apply_telegram_action(
        self,
        token: str,
    ) -> dict[str, object]:
        token_hash = self._hash_token(token)
        with self.dependencies.database.session() as session:
            action = session.scalar(
                select(TelegramActionRecord).where(
                    TelegramActionRecord.token_hash == token_hash
                )
            )
            if action is None:
                raise LookupError("Owner action was not found")
            if action.applied_at is not None:
                return {
                    "state": self._action_subject_state(session, action),
                    "already_applied": True,
                }
            if action.expires_at < self.dependencies.clock.now():
                raise PermissionError("Owner action has expired")
            if action.candidate_video_id is not None:
                candidate = session.get(
                    CandidateVideoRecord,
                    action.candidate_video_id,
                )
                if candidate is None:
                    raise RuntimeError(
                        "Owner action Candidate Video is missing"
                    )
                if candidate.status != "awaiting_decision":
                    raise RuntimeError(
                        "Candidate Video is no longer awaiting a decision"
                    )
                process = action.decision == "candidate_process"
                candidate.status = (
                    "processing_queued" if process else "ignored"
                )
                session.add(
                    LabeledExampleRecord(
                        candidate_video_id=candidate.id,
                        label="positive" if process else "negative",
                        created_at=self.dependencies.clock.now(),
                    )
                )
                if process:
                    self._queue_processing(
                        session,
                        candidate,
                        priority=10,
                        start_immediately=True,
                    )
                self._close_candidate_actions(session, candidate.id)
                return {
                    "candidate_video_id": candidate.id,
                    "state": candidate.status,
                    "already_applied": False,
                }
            if action.episode_id is None:
                raise RuntimeError("Owner action subject is missing")
            episode_id = action.episode_id
            decision = action.decision
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise RuntimeError("Owner action episode is missing")
            expected_state = episode.state
        outcome = self.apply_publication_decision(
            episode_id,
            decision,
            channel="telegram",
            expected_state=expected_state,
        )
        return {
            "episode_id": outcome["episode_id"],
            "state": outcome["state"],
            "already_applied": outcome["already_applied"],
        }

    def _close_candidate_actions(
        self,
        session: Session,
        candidate_video_id: int,
    ) -> None:
        actions = session.scalars(
            select(TelegramActionRecord).where(
                TelegramActionRecord.candidate_video_id == candidate_video_id,
                TelegramActionRecord.applied_at.is_(None),
            )
        ).all()
        for action in actions:
            action.applied_at = self.dependencies.clock.now()

    def _close_episode_actions(
        self,
        session: Session,
        episode_id: int,
    ) -> None:
        actions = session.scalars(
            select(TelegramActionRecord).where(
                TelegramActionRecord.episode_id == episode_id,
                TelegramActionRecord.applied_at.is_(None),
            )
        ).all()
        for action in actions:
            action.applied_at = self.dependencies.clock.now()

    @staticmethod
    def _action_subject_state(
        session: Session,
        action: TelegramActionRecord,
    ) -> str:
        if action.candidate_video_id is not None:
            candidate = session.get(
                CandidateVideoRecord,
                action.candidate_video_id,
            )
            if candidate is None:
                raise RuntimeError("Owner action Candidate Video is missing")
            return candidate.status
        if action.episode_id is not None:
            episode = session.get(EpisodeRecord, action.episode_id)
            if episode is None:
                raise RuntimeError("Owner action episode is missing")
            return episode.state
        raise RuntimeError("Owner action subject is missing")

    def private_feed(self) -> str:
        with self.dependencies.database.session() as session:
            return self._private_feed_from_session(session)

    def prepare_embedded_media_revision(
        self,
        episode_id: int,
        *,
        expected_chapter_count: int,
    ) -> dict[str, object]:
        """Prepare an embedded-metadata audio revision for owner review."""
        with self.dependencies.database.session() as session:
            episode = session.get(EpisodeRecord, episode_id)
            if episode is None:
                raise LookupError("Translated Episode was not found")
            if (
                episode.state != EpisodeState.PUBLISHED.value
                or episode.publication_key is None
            ):
                raise ValueError("Episode must already be published")
            if episode.publication_artwork_key is None:
                raise ValueError("Published Episode Artwork is unavailable")
            audio = self.dependencies.storage.read_publication(
                str(episode.publication_key)
            )
            artwork = self.dependencies.storage.read_publication(
                episode.publication_artwork_key
            )
            if audio is None or artwork is None:
                raise RuntimeError("Published media is unavailable")
            raw_chapters = json.loads(
                episode.published_chapters_json or "[]"
            )
            chapters = tuple(
                PublicationChapter(
                    start_seconds=int(item[0]),
                    title=str(item[1]),
                )
                for item in raw_chapters
            )
            if len(chapters) != expected_chapter_count:
                raise ValueError(
                    "Published Episode Chapter count does not match the "
                    "revision contract"
                )
            if any(
                (
                    episode.title != episode.published_title,
                    episode.overview != episode.published_overview,
                    episode.publication_description
                    != episode.published_publication_description,
                    episode.duration_seconds
                    != episode.published_duration_seconds,
                    episode.chapters_json
                    != episode.published_chapters_json,
                )
            ):
                raise ValueError(
                    "Episode has unpublished metadata changes; resolve them "
                    "before preparing the container revision"
                )
            duration_seconds = int(
                episode.published_duration_seconds
                or episode.duration_seconds
            )
            embedded = embed_publication_metadata(
                audio,
                artwork=artwork,
                chapters=chapters,
                duration_seconds=duration_seconds,
            )

            old_audio_key = str(episode.publication_key)
            next_revision = max(
                episode.publication_version,
                episode.revision_version,
            ) + 1
            preview_key = (
                f"episodes/{episode.publication_guid}/"
                f"revision-{next_revision}/embedded-metadata-preview.m4a"
            )
            self.dependencies.storage.put_preview(
                preview_key,
                embedded.audio,
            )
            episode.preview_key = preview_key
            episode.preview_expires_at = (
                self.dependencies.clock.now() + timedelta(days=7)
            )
            episode.revision_version = next_revision
            episode.revision_kind = PublicationRevisionKind.AUDIO.value
            episode.state = EpisodeState.REVISION_PENDING.value
            run = session.get(
                ProcessingRunRecord,
                episode.processing_run_id,
            )
            if run is not None:
                self._record_activity(
                    session,
                    run,
                    kind="publication_container_revision_prepared",
                    stage="publication",
                    summary=(
                        "Prepared embedded Episode Artwork and Chapters for "
                        "Owner publication decision"
                    ),
                    metadata={
                        "policy_version": (
                            "publication-container-metadata-revision-v1"
                        ),
                        "old_audio_key": old_audio_key,
                        "preview_key": preview_key,
                        "audio_bytes": len(embedded.audio),
                        "chapter_count": embedded.chapter_count,
                        "artwork_embedded": embedded.artwork_embedded,
                        "source_audio_packet_hash": (
                            embedded.source_audio_packet_hash
                        ),
                        "output_audio_packet_hash": (
                            embedded.output_audio_packet_hash
                        ),
                    },
                )
            self._send_publication_decision(session, episode)
            return {
                "episode_id": episode.id,
                "episode_state": episode.state,
                "revision_version": next_revision,
                "publication_version": episode.publication_version,
                "audio_bytes": len(embedded.audio),
                "chapter_count": embedded.chapter_count,
            }

    def _store_feed_snapshot(self, session: Session) -> None:
        self.dependencies.storage.put_feed_snapshot(
            self._private_feed_from_session(session).encode("utf-8")
        )

    def _private_feed_from_session(self, session: Session) -> str:
        episodes = session.scalars(
            select(EpisodeRecord)
            .where(
                EpisodeRecord.publication_key.is_not(None),
                EpisodeRecord.state != "deleted",
            )
            .order_by(EpisodeRecord.published_at.desc())
        ).all()
        return render_private_feed(
            items=[
                FeedItem(
                    guid=episode.publication_guid,
                    title=episode.published_title or episode.title,
                    description=(
                        compose_publication_description(
                            episode.published_publication_description
                            or episode.published_overview
                            or episode.overview,
                            tuple(
                                (int(item[0]), str(item[1]))
                                for item in json.loads(
                                    episode.published_chapters_json or "[]"
                                )
                            ),
                            episode.source_url,
                        )
                    ),
                    source_url=episode.source_url,
                    publication_key=str(episode.publication_key),
                    publication_bytes=episode.publication_bytes,
                    chapters_key=episode.publication_chapters_key,
                    artwork_key=episode.publication_artwork_key,
                    duration_seconds=(
                        episode.published_duration_seconds
                        or episode.duration_seconds
                    ),
                    published_at=(
                        episode.published_at or self.dependencies.clock.now()
                    ),
                )
                for episode in episodes
            ],
            publication_domain=self.dependencies.publication_domain,
            feed_token=self.current_feed_token(),
            show_title=self.dependencies.show_title,
            show_description=self.dependencies.show_description,
            show_author=self.dependencies.show_author,
            show_artwork_key=self._show_artwork_key(),
        )

    def _show_artwork_key(self) -> str:
        if not self.dependencies.show_artwork:
            return "artwork/transpodcast-3000.png"
        digest = hashlib.sha256(self.dependencies.show_artwork).hexdigest()
        return f"artwork/show-{digest[:16]}-3000.png"

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


def _default_settings() -> dict[str, object]:
    return {
        "discovery_interval_minutes": 120,
        "duration_guardrail_minutes": 180,
        "processing_target_hours": 24,
        "manual_processing": True,
        "auto_discovery": False,
        "notification_preferences": {
            "candidate_decision": True,
            "automatic_ignore": False,
            "processing_failure": True,
            "spend_guardrail": True,
            "publication_decision": True,
        },
    }


def _channel_payload(channel: ChannelResolution) -> dict[str, object]:
    return {
        "youtube_channel_id": channel.youtube_channel_id,
        "title": channel.title,
        "url": channel.canonical_url,
        "avatar_url": channel.avatar_url,
        "subscriber_count": channel.subscriber_count,
        "video_count": channel.video_count,
    }


def _requested_video_payload(
    candidate: SourceCandidate,
) -> dict[str, object]:
    return {
        "youtube_video_id": candidate.youtube_video_id,
        "title": candidate.title,
        "description": candidate.description[:500],
        "duration_seconds": candidate.duration_seconds,
        "language": candidate.language,
        "published_at": candidate.published_at,
        "source_url": candidate.source_url,
    }


def _preview_payload(
    preview: SourceVideoPreview,
    *,
    is_requested: bool,
) -> dict[str, object]:
    return {
        "youtube_video_id": preview.youtube_video_id,
        "title": preview.title,
        "duration_seconds": preview.duration_seconds,
        "source_url": preview.source_url,
        "published_at": preview.published_at,
        "view_count": preview.view_count,
        "is_requested": is_requested,
    }


def _candidate_from_preview(
    preview: SourceVideoPreview,
    fallback_published_at: datetime,
) -> SourceCandidate:
    return SourceCandidate(
        youtube_video_id=preview.youtube_video_id,
        title=preview.title,
        description="",
        duration_seconds=preview.duration_seconds,
        language="en",
        published_at=preview.published_at or fallback_published_at,
        source_url=preview.source_url,
        captions=None,
    )


def _readiness_label(ready: bool) -> str:
    return "configured" if ready else "missing"


def _serialize_translation(translation: TranslationResult) -> str:
    return json.dumps(
        {
            "title": translation.title,
            "overview": translation.overview,
            "target_locale": translation.target_locale,
            "segments": translation.segments,
            "provider": translation.provider,
            "model": translation.model,
            "input_tokens": translation.input_tokens,
            "output_tokens": translation.output_tokens,
            "reviewed": translation.reviewed,
            "review_model": translation.review_model,
            "policy_version": translation.policy_version,
            "elapsed_ms": translation.elapsed_ms,
            "input_hash": translation.input_hash,
            "output_hash": translation.output_hash,
            "cli_version": translation.cli_version,
            "audit_json": translation.audit_json,
        }
    )


def deserialize_translation(
    value: str,
    content_edit_json: str | None = None,
    publication_metadata_json: str | None = None,
) -> TranslationResult:
    parsed = json.loads(value)
    return TranslationResult(
        title=str(parsed["title"]),
        overview=str(parsed["overview"]),
        target_locale=str(parsed["target_locale"]),
        segments=tuple(str(item) for item in parsed["segments"]),
        provider=str(parsed["provider"]),
        model=str(parsed["model"]),
        input_tokens=int(parsed["input_tokens"]),
        output_tokens=int(parsed["output_tokens"]),
        reviewed=bool(parsed.get("reviewed", False)),
        review_model=str(parsed.get("review_model", "")),
        policy_version=str(
            parsed.get("policy_version", "translation-policy-v1")
        ),
        elapsed_ms=int(parsed.get("elapsed_ms", 0)),
        input_hash=str(parsed.get("input_hash", "")),
        output_hash=str(parsed.get("output_hash", "")),
        cli_version=str(parsed.get("cli_version", "")),
        audit_json=str(parsed.get("audit_json", "{}")),
        content_edit_json=(
            content_edit_json
            if content_edit_json is not None
            else str(parsed.get("content_edit_json", "{}"))
        ),
        publication_metadata_json=(
            publication_metadata_json
            if publication_metadata_json is not None
            else str(parsed.get("publication_metadata_json", "{}"))
        ),
    )


def _matching_adjudication_record(
    records: list[dict[str, object]],
    start_seconds: float,
    end_seconds: float,
    segment_id: str = "",
) -> dict[str, object] | None:
    """Find the adjudication record whose disputed span sits inside
    the segment span."""
    ordered_records = sorted(
        records,
        key=lambda item: (
            not (
                (
                    item.get("kind") == "speaker-tail-boundary-repair"
                    and item.get("verdict") == "tail-shifted"
                )
                or (
                    item.get("kind") == "speaker-boundary-adjudication"
                    and item.get("verdict")
                    in {
                        "split-to-next-applied",
                        "whole-reassignment-applied",
                    }
                )
            )
        ),
    )
    for record in ordered_records:
        subject = record.get("subject")
        if not isinstance(subject, dict):
            continue
        try:
            subject_start = float(subject["start_seconds"])
            subject_end = float(subject["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if record.get("kind") == "speaker-boundary-adjudication" and segment_id:
            evidence = record.get("evidence")
            if not isinstance(evidence, dict):
                continue
            verdict = record.get("verdict")
            if verdict == "whole-reassignment-applied":
                if segment_id == evidence.get("planner_target_segment_id"):
                    return record
                continue
            if segment_id in {
                subject.get("current_segment_id"),
                subject.get("next_segment_id"),
            }:
                return record
            continue
        if record.get("kind") in {
            "speaker-tail-boundary-repair",
            "speaker-boundary-adjudication",
        }:
            if (
                subject_start <= start_seconds + 1e-6
                and end_seconds <= subject_end + 1e-6
            ):
                return record
            continue
        midpoint = (subject_start + subject_end) / 2
        if start_seconds <= midpoint <= end_seconds:
            return record
    return None


def _counterpart_speaker(
    record: dict[str, object],
    registry_by_label: dict[str, object],
    speaker_rows: Mapping[int, SpeakerRecord],
) -> dict[str, object] | None:
    """Resolve the adjudication record's actual counterpart label to a
    registry Speaker, so an overturn can never point at the wrong
    person in a three-plus-person episode (review finding)."""
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        return None
    label = str(
        evidence.get("counterpart")
        or evidence.get("planner_target_owner")
        or ""
    )
    speaker_id = registry_by_label.get(label)
    if not isinstance(speaker_id, int):
        return None
    row = speaker_rows.get(speaker_id)
    if row is None:
        return None
    return {
        "id": row.id,
        "display_name": row.display_name,
        "label": label,
    }


def _canonical_segment_at(
    payload: dict[str, object],
    ordinal: int,
) -> dict[str, object]:
    """Locate the canonical segment for a translated ordinal."""
    segments = payload.get("segments")
    counter = 0
    for segment in segments if isinstance(segments, list) else []:
        if not isinstance(segment, dict):
            continue
        counter += 1
        if counter == ordinal:
            return segment
    raise LookupError("Episode segment was not found")


def _normalize_legacy_sponsor_flags(
    analysis: SourceAnalysisResult,
) -> SourceAnalysisResult:
    """Normalize the retired sponsor-omission flag on active runs."""
    if not any(segment.omitted_as_sponsor for segment in analysis.segments):
        return analysis
    return replace(
        analysis,
        segments=tuple(
            replace(segment, omitted_as_sponsor=False)
            for segment in analysis.segments
        ),
    )


def _stamp_analysis_checksum(analysis_json: str) -> str:
    payload = json.loads(analysis_json)
    payload["checksum"] = _content_hash(
        {key: value for key, value in payload.items() if key != "checksum"}
    )
    return json.dumps(payload, ensure_ascii=False)


def _serialize_analysis(analysis: SourceAnalysisResult) -> str:
    return json.dumps(
        {
            "pipeline_version": analysis.pipeline_version,
            "diarization_model": analysis.diarization_model,
            "diarization_model_revision": analysis.diarization_model_revision,
            "transcription_model": analysis.transcription_model,
            "alignment_model": analysis.alignment_model,
            "source_events": analysis.source_events,
            "background_regions": analysis.background_regions,
            "raw_diarization_turns": [
                {
                    "speaker": speaker,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                }
                for speaker, start_seconds, end_seconds in (
                    analysis.raw_diarization_turns
                )
            ],
            "full_asr_text": analysis.full_asr_text,
            "aligned_words": [
                {
                    "text": text,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                }
                for text, start_seconds, end_seconds in analysis.aligned_words
            ],
            "boundary_stabilization": json.loads(
                analysis.boundary_stabilization_json
            ),
            "source_canonicalization": json.loads(
                analysis.source_canonicalization_json
            ),
            "speaker_consolidation": json.loads(
                analysis.speaker_consolidation_json
            ),
            "boundary_adjudication": json.loads(
                analysis.boundary_adjudication_json
            ),
            "speakers": [
                {
                    "local_label": speaker.local_label,
                    "embedding": speaker.embedding,
                    "confidence": speaker.confidence,
                    "checkpoint_reference_audio_path": (
                        speaker.reference_artifact_key
                        if speaker.reference_audio
                        else ""
                    ),
                    "registry_speaker_id": speaker.registry_speaker_id,
                    "reference_artifact_key": (speaker.reference_artifact_key),
                    "registry_voice_reference_id": (
                        speaker.registry_voice_reference_id
                    ),
                    "matched_source_channel_id": (
                        speaker.matched_source_channel_id
                    ),
                    "match_similarity": speaker.match_similarity,
                    "match_policy_version": speaker.match_policy_version,
                }
                for speaker in analysis.speakers
            ],
            "checksum": analysis.checksum,
            "estimated_cost_usd": analysis.estimated_cost_usd,
            "call_seconds": dict(analysis.call_seconds),
            "routing": _json_mapping(analysis.routing_json),
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "speaker_label": segment.speaker_label,
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds,
                    "text": segment.text,
                    "words": segment.words,
                    "overlap": segment.overlap,
                    "events": segment.events,
                    "confidence": segment.confidence,
                    "review_state": segment.review_state,
                    "omitted_as_sponsor": segment.omitted_as_sponsor,
                }
                for segment in analysis.segments
            ],
        },
        ensure_ascii=False,
    )


def _serialize_speech_checkpoint(speech: SpeechResult) -> str:
    quality = speech.quality
    return json.dumps(
        {
            "duration_seconds": speech.duration_seconds,
            "chapters": speech.chapters,
            "pipeline_version": speech.pipeline_version,
            "audio_format": speech.audio_format,
            "estimated_cost_usd": speech.estimated_cost_usd,
            "segment_timings": speech.segment_timings,
            "quality": {
                "codec": quality.codec,
                "sample_rate_hz": quality.sample_rate_hz,
                "channels": quality.channels,
                "bitrate_kbps": quality.bitrate_kbps,
                "loudness_lufs": quality.loudness_lufs,
                "true_peak_dbtp": quality.true_peak_dbtp,
                "pre_encode_loudness_lufs": (quality.pre_encode_loudness_lufs),
                "pre_encode_true_peak_dbtp": (
                    quality.pre_encode_true_peak_dbtp
                ),
                "clipping": quality.clipping,
                "rendered_segments": quality.rendered_segments,
                "timeline_continuous": quality.timeline_continuous,
                "boundary_artifacts": quality.boundary_artifacts,
                "source_language_leakage": (quality.source_language_leakage),
            },
        }
    )


def _deserialize_speech_checkpoint(
    value: str,
    publication_audio: bytes,
) -> SpeechResult:
    parsed = json.loads(value)
    quality = parsed["quality"]
    return SpeechResult(
        publication_audio=publication_audio,
        duration_seconds=int(parsed["duration_seconds"]),
        chapters=tuple(
            (int(item[0]), str(item[1])) for item in parsed["chapters"]
        ),
        pipeline_version=str(parsed["pipeline_version"]),
        audio_format=str(parsed.get("audio_format", "audio/mp4")),
        quality=AudioQualityReport(
            codec=str(quality["codec"]),
            sample_rate_hz=int(quality["sample_rate_hz"]),
            channels=int(quality["channels"]),
            bitrate_kbps=int(quality["bitrate_kbps"]),
            loudness_lufs=float(quality["loudness_lufs"]),
            true_peak_dbtp=float(quality["true_peak_dbtp"]),
            pre_encode_loudness_lufs=(
                float(quality["pre_encode_loudness_lufs"])
                if quality.get("pre_encode_loudness_lufs") is not None
                else None
            ),
            pre_encode_true_peak_dbtp=(
                float(quality["pre_encode_true_peak_dbtp"])
                if quality.get("pre_encode_true_peak_dbtp") is not None
                else None
            ),
            clipping=bool(quality["clipping"]),
            rendered_segments=int(quality["rendered_segments"]),
            timeline_continuous=bool(quality["timeline_continuous"]),
            boundary_artifacts=bool(quality["boundary_artifacts"]),
            source_language_leakage=bool(quality["source_language_leakage"]),
        ),
        estimated_cost_usd=float(parsed.get("estimated_cost_usd", 0.0)),
        segment_timings=tuple(
            (float(item[0]), float(item[1]))
            for item in parsed.get("segment_timings", [])
        ),
    )


def deserialize_analysis(
    value: str,
    *,
    artifact_root: Path | None = None,
) -> SourceAnalysisResult:
    parsed = json.loads(value)
    return SourceAnalysisResult(
        segments=tuple(
            CanonicalSegment(
                segment_id=str(item["segment_id"]),
                speaker_label=str(item["speaker_label"]),
                start_seconds=float(item["start_seconds"]),
                end_seconds=float(item["end_seconds"]),
                text=str(item["text"]),
                words=tuple(
                    (
                        str(word[0]),
                        float(word[1]),
                        float(word[2]),
                    )
                    for word in item.get("words", [])
                ),
                overlap=bool(item.get("overlap", False)),
                events=tuple(str(event) for event in item.get("events", [])),
                confidence=float(item.get("confidence", 1.0)),
                review_state=str(item.get("review_state", "accepted")),
                omitted_as_sponsor=bool(item.get("omitted_as_sponsor", False)),
            )
            for item in parsed["segments"]
        ),
        pipeline_version=str(parsed["pipeline_version"]),
        diarization_model=str(parsed["diarization_model"]),
        diarization_model_revision=str(
            parsed.get("diarization_model_revision", "")
        ),
        transcription_model=str(parsed["transcription_model"]),
        alignment_model=str(parsed["alignment_model"]),
        source_events=tuple(
            (str(item[0]), float(item[1]), float(item[2]))
            for item in parsed.get("source_events", [])
        ),
        background_regions=tuple(
            (float(item[0]), float(item[1]))
            for item in parsed.get("background_regions", [])
        ),
        raw_diarization_turns=tuple(
            (
                str(item["speaker"]),
                float(item["start_seconds"]),
                float(item["end_seconds"]),
            )
            for item in parsed.get("raw_diarization_turns", [])
        ),
        full_asr_text=str(parsed.get("full_asr_text", "")),
        aligned_words=tuple(
            (
                str(item["text"]),
                float(item["start_seconds"]),
                float(item["end_seconds"]),
            )
            for item in parsed.get("aligned_words", [])
        ),
        boundary_stabilization_json=json.dumps(
            parsed.get("boundary_stabilization", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        source_canonicalization_json=json.dumps(
            parsed.get("source_canonicalization", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        speaker_consolidation_json=json.dumps(
            parsed.get("speaker_consolidation", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        boundary_adjudication_json=json.dumps(
            parsed.get("boundary_adjudication", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
        speakers=tuple(
            AnalyzedSpeaker(
                local_label=str(item["local_label"]),
                reference_audio=_read_checkpoint_reference_audio(
                    item,
                    artifact_root,
                ),
                embedding=tuple(
                    float(value) for value in item.get("embedding", [])
                ),
                confidence=float(item.get("confidence", 0.0)),
                registry_speaker_id=(
                    int(item["registry_speaker_id"])
                    if item.get("registry_speaker_id") is not None
                    else None
                ),
                reference_artifact_key=str(
                    item.get("reference_artifact_key", "")
                ),
                registry_voice_reference_id=(
                    int(item["registry_voice_reference_id"])
                    if item.get("registry_voice_reference_id") is not None
                    else None
                ),
                matched_source_channel_id=(
                    int(item["matched_source_channel_id"])
                    if item.get("matched_source_channel_id") is not None
                    else None
                ),
                match_similarity=(
                    float(item["match_similarity"])
                    if item.get("match_similarity") is not None
                    else None
                ),
                match_policy_version=str(item.get("match_policy_version", "")),
            )
            for item in parsed.get("speakers", [])
        ),
        checksum=str(parsed.get("checksum", "")),
        estimated_cost_usd=float(parsed.get("estimated_cost_usd", 0.0)),
        call_seconds=tuple(
            (str(name), float(seconds))
            for name, seconds in dict(
                parsed.get("call_seconds", {})
            ).items()
        ),
        routing_json=json.dumps(
            parsed.get("routing", {}),
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _read_checkpoint_reference_audio(
    item: dict[str, Any],
    artifact_root: Path | None,
) -> bytes:
    path_value = str(item.get("checkpoint_reference_audio_path", ""))
    if not path_value or artifact_root is None:
        return b""
    root = artifact_root.resolve()
    path = Path(path_value).resolve()
    if path != root and root not in path.parents:
        raise ValueError(
            "Source Analysis reference checkpoint is outside artifact root"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise ValueError(
            "Source Analysis reference checkpoint is unavailable"
        ) from error


def _caption_analysis(candidate: SourceCandidate) -> SourceAnalysisResult:
    values = canonical_caption_segments(candidate.captions or "")
    if not values:
        raise ValueError("Canonical Transcript is unavailable")
    duration = max(float(candidate.duration_seconds), float(len(values)))
    segment_duration = duration / len(values)
    segments = tuple(
        CanonicalSegment(
            segment_id=f"seg-{index:04d}",
            speaker_label="speaker-unknown",
            start_seconds=(index - 1) * segment_duration,
            end_seconds=index * segment_duration,
            text=text,
            confidence=0.5,
            review_state="caption_fallback",
        )
        for index, text in enumerate(values, start=1)
    )
    checksum = _content_hash([segment.text for segment in segments])
    return SourceAnalysisResult(
        segments=segments,
        pipeline_version="caption-fallback-v1",
        diarization_model="unavailable",
        transcription_model="youtube-captions",
        alignment_model="caption-timestamps",
        checksum=checksum,
    )


def _serialize_quality(
    speech: SpeechResult,
    expected_segments: int,
) -> str:
    quality = speech.quality
    return json.dumps(
        {
            "eligible": True,
            "audio": {
                "codec": quality.codec,
                "sample_rate_hz": quality.sample_rate_hz,
                "channels": quality.channels,
                "bitrate_kbps": quality.bitrate_kbps,
                "loudness_lufs": quality.loudness_lufs,
                "true_peak_dbtp": quality.true_peak_dbtp,
                "clipping": quality.clipping,
            },
            "pre_encode": {
                "loudness_lufs": quality.pre_encode_loudness_lufs,
                "true_peak_dbtp": quality.pre_encode_true_peak_dbtp,
            },
            "segment_coverage": {
                "expected": expected_segments,
                "rendered": quality.rendered_segments,
            },
            "timeline_continuous": quality.timeline_continuous,
            "boundary_artifacts": quality.boundary_artifacts,
            "source_language_leakage": quality.source_language_leakage,
        }
    )


def _podcast_chapters(chapters: list[list[Any]]) -> bytes:
    return json.dumps(
        {
            "version": "1.2.0",
            "chapters": [
                {
                    "startTime": int(item[0]),
                    "title": str(item[1]),
                }
                for item in chapters
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _content_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _chapter_resolution_diagnostics(
    proposals_json: str,
    chapters: list[list[Any]],
) -> dict[str, object]:
    try:
        raw_proposals = json.loads(proposals_json)
    except json.JSONDecodeError:
        raw_proposals = []
    proposals = raw_proposals if isinstance(raw_proposals, list) else []
    proposal_count = sum(
        1
        for proposal in proposals
        if isinstance(proposal, dict)
        and proposal.get("diagnostic_only") is not True
    )
    reasons: dict[str, int] = {}
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        if proposal.get("resolution_status") == "included":
            continue
        reason = str(
            proposal.get("resolution_reason")
            or "legacy_resolution_unavailable"
        )
        reasons[reason] = reasons.get(reason, 0) + 1
    if not proposals and not chapters:
        reasons["no_chapter_proposals"] = 1
    return {
        "status": "ready" if chapters else "omitted",
        "proposed_count": proposal_count,
        "included_count": len(chapters),
        "excluded_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reasons.items())
        ],
    }


def _resolve_publication_metadata(
    metadata: PublicationMetadata,
    retained_segment_ids: tuple[str, ...] = (),
    segment_timings: tuple[tuple[float, float], ...] = (),
    duration_seconds: int = 0,
) -> ResolvedPublicationMetadata:
    description = metadata.description
    if metadata.participant_line:
        description += "\n\n" + metadata.participant_line
    chapters: tuple[tuple[int, str], ...] = ()
    if retained_segment_ids and segment_timings and duration_seconds > 0:
        chapters, chapter_diagnostics = (
            resolve_episode_chapters_with_diagnostics(
                metadata,
                retained_segment_ids,
                segment_timings,
                duration_seconds,
            )
        )
    else:
        chapter_diagnostics = tuple(
            {
                "status": "excluded",
                "reason": "timing_evidence_unavailable",
            }
            for _proposal in metadata.chapters
        )
    proposal_records: list[dict[str, object]] = [
        {
            "anchor_segment_id": item.anchor_segment_id,
            "title": item.title,
            "rationale": item.rationale,
            "resolution_status": diagnostic["status"],
            "resolution_reason": diagnostic["reason"],
        }
        for item, diagnostic in zip(
            metadata.chapters,
            chapter_diagnostics,
            strict=True,
        )
    ]
    if metadata.status != "accepted" and not proposal_records:
        proposal_records.append(
            {
                "diagnostic_only": True,
                "resolution_status": "excluded",
                "resolution_reason": "metadata_degraded",
                "resolution_detail": metadata.error,
            }
        )
    proposals = json.dumps(
        proposal_records,
        ensure_ascii=False,
    )
    return ResolvedPublicationMetadata(
        description=description,
        chapters=chapters,
        chapter_proposals_json=proposals,
    )


def _render_plan_input_hash(
    translation: TranslationResult,
    render_plan: RenderPlan,
) -> str:
    return _content_hash(
        {
            "translation_output_hash": (
                translation.output_hash
                or _content_hash(translation.segments)
            ),
            "render_unit_ids": [
                segment.segment_id
                for segment in render_plan.analysis.segments
            ],
            "render_segments": render_plan.translation.segments,
            "content_edit_artifact": translation.content_edit_json,
        }
    )


def _render_ordinal_for_canonical_segment(
    render_plan: RenderPlan,
    canonical_segment_id: str,
) -> int | None:
    matches = [
        (index, segment)
        for index, segment in enumerate(render_plan.analysis.segments, start=1)
        if segment.segment_id.split("-retained-", maxsplit=1)[0]
        == canonical_segment_id
    ]
    if len(matches) != 1 or matches[0][1].segment_id != canonical_segment_id:
        return None
    return matches[0][0]


def _v13_tail_repair_activity_metadata(
    completion: AdjudicationCompletion,
) -> dict[str, int]:
    return {
        "proposed_repair_count": completion.tail_repair_proposed_count,
        "locally_preserved_component_count": (
            completion.tail_repair_locally_preserved_component_count
        ),
        "locally_degraded_component_count": (
            completion.tail_repair_locally_degraded_component_count
        ),
        "conflict_component_count": (
            completion.tail_repair_conflict_component_count
        ),
        "provider_degraded_component_count": (
            completion.tail_repair_provider_degraded_component_count
        ),
    }
