from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, HttpUrl

from transpodcast.dependencies import AppDependencies
from transpodcast.domain import (
    ChannelMode,
    ChannelSpeakerProfile,
    ChannelSpeakerProfileState,
    ChannelVoiceCandidateDecision,
    EpisodeLibraryAction,
    EpisodeLibraryState,
    TriageDecision,
)
from transpodcast.errors import DiscoveryAlreadyRunningError
from transpodcast.service import TransPodcastService


def _parse_byte_range(
    value: str,
    content_length: int,
) -> tuple[int, int] | None:
    if not value.startswith("bytes=") or "," in value:
        return None
    start_text, separator, end_text = value.removeprefix("bytes=").partition(
        "-"
    )
    if not separator:
        return None
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else content_length - 1
        elif end_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(content_length - suffix_length, 0)
            end = content_length - 1
        else:
            return None
    except ValueError:
        return None
    if start < 0 or start >= content_length or end < start:
        return None
    return start, min(end, content_length - 1)


class SourceChannelCreate(BaseModel):
    url: HttpUrl | None = None
    resolution_id: str | None = Field(default=None, max_length=64)
    mode: ChannelMode
    backfill_video_ids: list[str] = Field(default_factory=list)


class SourceUrlRequest(BaseModel):
    url: HttpUrl


class SourceVideoImport(BaseModel):
    url: HttpUrl | None = None
    youtube_video_id: str | None = Field(default=None, max_length=64)


class ResolvedSourceChannel(BaseModel):
    youtube_channel_id: str
    title: str
    url: str


class SourceVideoSummary(BaseModel):
    youtube_video_id: str
    title: str
    duration_seconds: int
    published_at: datetime
    source_url: str


class SourceChannelResolutionResponse(BaseModel):
    channel: ResolvedSourceChannel
    supported_source_video: SourceVideoSummary | None
    recent_videos: list[SourceVideoSummary]


class SourceChannelResponse(BaseModel):
    id: int
    youtube_channel_id: str
    title: str
    url: str
    mode: ChannelMode
    avatar_url: str


class ResolvedSourceResponseChannel(BaseModel):
    youtube_channel_id: str
    title: str
    url: str
    avatar_url: str
    subscriber_count: int | None
    video_count: int | None


class ResolvedSourceVideo(BaseModel):
    youtube_video_id: str
    title: str
    description: str
    duration_seconds: int
    language: str
    published_at: datetime
    source_url: str


class SourceResolutionResponse(BaseModel):
    resolution_id: str
    expires_at: datetime
    channel: ResolvedSourceResponseChannel
    requested_video: ResolvedSourceVideo | None
    existing_source_channel: SourceChannelResponse | None
    timings_ms: dict[str, int]


class ImportedSourceChannelResponse(BaseModel):
    id: int
    title: str
    mode: ChannelMode
    created: bool


class DirectSourceVideoImportResponse(BaseModel):
    candidate_video_id: int
    youtube_video_id: str
    candidate_status: str
    run_status: str | None
    processing_run_id: int | None
    episode_id: int | None
    reused: bool
    source_channel: ImportedSourceChannelResponse


class EpisodeLibraryItemResponse(BaseModel):
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
    guests: list[str]
    created_at: datetime
    published_at: datetime | None
    can_reprocess: bool
    library_state: EpisodeLibraryState
    available_actions: list[EpisodeLibraryAction]
    artwork_url: str


class EpisodeLibraryResponse(BaseModel):
    items: list[EpisodeLibraryItemResponse]


class CandidateDecisionRequest(BaseModel):
    decision: TriageDecision


class InterestProfileUpdate(BaseModel):
    markdown: str


class SettingsUpdate(BaseModel):
    discovery_interval_minutes: int | None = Field(
        default=None,
        ge=15,
        le=1440,
    )
    duration_guardrail_minutes: int | None = Field(
        default=None,
        ge=15,
        le=1440,
    )
    processing_target_hours: int | None = Field(
        default=None,
        ge=1,
        le=168,
    )
    manual_processing: bool | None = None
    auto_discovery: bool | None = None
    notification_preferences: dict[str, bool] | None = None


class SourceChannelUpdate(BaseModel):
    mode: ChannelMode


class SpeakerCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=512)
    canonical_voice_reference: str = Field(min_length=1, max_length=2048)
    verified: bool = False


class VoiceReferenceCreate(BaseModel):
    artifact_key: str = Field(min_length=1, max_length=2048)


class ChannelVoiceCandidateSave(BaseModel):
    display_name: str | None = Field(default=None, max_length=512)


class ChannelSpeakerProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=512)


class ChannelVoiceCandidateMerge(BaseModel):
    target_profile_id: int = Field(gt=0)
    replace_canonical_reference: bool = False


class VoiceReferencePreviewResponse(BaseModel):
    id: int
    duration_seconds: float | None
    audio_url: str


class ChannelSpeakerSuggestionResponse(BaseModel):
    profile_id: int
    source_channel_id: int
    source_channel_title: str
    speaker_id: int
    display_name: str
    similarity: float


class ChannelSpeakerProfileResponse(BaseModel):
    id: int
    speaker_id: int
    display_name: str
    state: ChannelSpeakerProfileState
    voice_reference: VoiceReferencePreviewResponse


class ChannelVoiceCandidateEpisodeResponse(BaseModel):
    id: int
    title: str


class ChannelVoiceCandidateResponse(BaseModel):
    id: int
    speaker_id: int
    display_name: str
    local_label: str
    segment_count: int
    source_episode: ChannelVoiceCandidateEpisodeResponse
    voice_reference: VoiceReferencePreviewResponse
    possible_matches: list[ChannelSpeakerSuggestionResponse]


class ChannelVoiceLibraryItemResponse(BaseModel):
    source_channel_id: int
    title: str
    avatar_url: str
    saved_profile_count: int
    pending_candidate_count: int


class ChannelVoiceLibraryResponse(BaseModel):
    items: list[ChannelVoiceLibraryItemResponse]


class ChannelVoiceLibraryChannelResponse(BaseModel):
    id: int
    title: str
    avatar_url: str


class ChannelVoiceLibraryDetailResponse(BaseModel):
    channel: ChannelVoiceLibraryChannelResponse
    saved_profiles: list[ChannelSpeakerProfileResponse]
    pending_candidates: list[ChannelVoiceCandidateResponse]


class ChannelVoiceCandidateDecisionResponse(BaseModel):
    id: int
    decision: ChannelVoiceCandidateDecision


def _channel_speaker_profile_response(
    profile: ChannelSpeakerProfile,
) -> ChannelSpeakerProfileResponse:
    return ChannelSpeakerProfileResponse(
        id=profile.id,
        speaker_id=profile.speaker_id,
        display_name=profile.display_name,
        state=profile.state,
        voice_reference=VoiceReferencePreviewResponse(
            id=profile.voice_reference.id,
            duration_seconds=profile.voice_reference.duration_seconds,
            audio_url=profile.voice_reference.audio_url,
        ),
    )


class SpeakerMerge(BaseModel):
    target_speaker_id: int = Field(gt=0)


class SpeakerSplit(BaseModel):
    display_name: str = Field(min_length=1, max_length=512)
    episode_id: int = Field(gt=0)
    segment_ordinals: list[int] = Field(min_length=1)


class SegmentRevisionRequest(BaseModel):
    target_text: str = Field(min_length=1)
    speaker_id: int | None = None
    emotion_reference: str | None = None


class ProcessingSegmentRuling(BaseModel):
    action: Literal["keep", "overturn"]
    speaker_id: int | None = None


class SegmentIssueRequest(BaseModel):
    note: str = ""


class TranscriptRevisionRequest(BaseModel):
    source_text: str = Field(min_length=1)


class ChapterUpdate(BaseModel):
    start_seconds: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=45)


class EpisodeMetadataUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=1024)
    overview: str = Field(min_length=1)
    publication_description: str | None = Field(default=None, min_length=1)
    chapters: list[ChapterUpdate] = Field(default_factory=list)


class PublicationDecisionRequest(BaseModel):
    decision: Literal["publish", "do_not_publish"]
    expected_state: str | None = None


class PublicationDecisionResponse(BaseModel):
    episode_id: int
    state: str
    decision: Literal["publish", "do_not_publish"]
    channel: Literal["owner_console", "telegram"]
    already_applied: bool


class ProcessingRunTranslationResponse(BaseModel):
    provider: str | None
    model: str | None
    input_tokens: int
    output_tokens: int


class ProcessingRunErrorResponse(BaseModel):
    kind: str
    summary: str
    step: str | None = None
    retryable: bool | None = None


class ProcessingRunRetryPlanResponse(BaseModel):
    resume_step: str
    reused_steps: list[str]
    invalidated_steps: list[str]
    preserves_translation_targets: bool


class TranslationProjectProgressResponse(BaseModel):
    phase: str
    policy_version: str
    completed_targets: int
    total_targets: int
    author_turns: int
    reviewer_turns: int
    author_sessions: list[str]
    reviewer_sessions: list[str]


class PipelineStepProgressResponse(BaseModel):
    completed: int
    total: int
    unit: str


class PipelineStepRecoveryResponse(BaseModel):
    retryable: bool
    checkpoint: bool


class PipelineTechnicalEvidenceResponse(BaseModel):
    version: str | None
    provider: str | None
    model: str | list[str] | None
    modal_account: str | None = None
    modal_route: list[str] = Field(default_factory=list)
    parallel_operations: dict[str, float] | None = None


class ProcessingPipelineStepResponse(BaseModel):
    id: str
    state: str
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    summary: str
    progress: PipelineStepProgressResponse | None
    metrics: dict[str, object]
    evidence_available: bool
    recovery: PipelineStepRecoveryResponse
    technical_evidence: PipelineTechnicalEvidenceResponse | None


class ProcessingRunStageResponse(BaseModel):
    stage: str
    version: str
    provider: str | None
    model: str | None
    input_hash: str
    output_hash: str
    usage: dict[str, object]
    duration_ms: int
    started_at: datetime
    completed_at: datetime


class ProcessingRunAttemptResponse(BaseModel):
    id: int
    sequence: int
    stage: str
    status: str
    duration_ms: int
    transfer_bytes: int
    resume_checkpoint: str
    error_kind: str | None
    error_summary: str | None
    created_at: datetime
    started_at: datetime
    completed_at: datetime | None
    correlation_id: str
    routing: dict[str, object]
    supersedes_attempt_id: int | None
    timing_provenance: str
    is_current_blocker: bool
    superseded: bool


class ProcessingRunActivityResponse(BaseModel):
    id: int
    kind: str
    stage: str | None
    summary: str
    metadata: dict[str, object]
    attempt_id: int | None
    created_at: datetime


class ProcessingRunDetailResponse(BaseModel):
    id: int
    revision: str
    candidate_video_id: int
    title: str
    source_show: str
    source_url: str
    status: str
    state: str
    stage: str
    owner_state: str
    current_step: str
    latest_change_at: datetime
    pipeline_version: str
    priority: int
    attempt_count: int
    estimated_cost_usd: float
    translation: ProcessingRunTranslationResponse
    error: ProcessingRunErrorResponse | None
    current_error: ProcessingRunErrorResponse | None
    retry_plan: ProcessingRunRetryPlanResponse
    queued_at: datetime
    first_claimed_at: datetime | None
    started_at: datetime
    completed_at: datetime | None
    translation_project: TranslationProjectProgressResponse | None
    pipeline_steps: list[ProcessingPipelineStepResponse]
    stages: list[ProcessingRunStageResponse]
    attempts: list[ProcessingRunAttemptResponse]
    attempt_history: list[ProcessingRunAttemptResponse]
    activity: list[ProcessingRunActivityResponse]


class ProcessingRunSummaryTranslationResponse(BaseModel):
    input_tokens: int
    output_tokens: int


class ProcessingRunSummaryResponse(BaseModel):
    id: int
    revision: str
    status: str
    title: str
    source_show: str
    owner_state: str
    current_step: str
    estimated_cost_usd: float
    translation: ProcessingRunSummaryTranslationResponse
    latest_change_at: datetime


class ProcessingRunsResponse(BaseModel):
    revision: str
    items: list[ProcessingRunSummaryResponse]


class ProcessingRunDiagnosticsResponse(BaseModel):
    schema_version: str
    run: dict[str, object]
    current_error: ProcessingRunErrorResponse | None
    retry_plan: ProcessingRunRetryPlanResponse
    translation: ProcessingRunTranslationResponse
    stages: list[dict[str, object]]
    attempts: list[dict[str, object]]


def create_app(dependencies: AppDependencies) -> FastAPI:
    dependencies.database.create_schema()
    service = TransPodcastService(dependencies)
    service.ensure_publication_assets()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            service.close()

    app = FastAPI(
        title="TransPodcast",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/api/v1/setup-readiness")
    def setup_readiness() -> dict[str, object]:
        return service.setup_readiness()

    @app.post(
        "/api/v1/source-channels",
        status_code=status.HTTP_201_CREATED,
    )
    def create_source_channel(
        payload: SourceChannelCreate,
    ) -> dict[str, object]:
        try:
            if payload.resolution_id is not None:
                return service.create_source_channel_from_resolution(
                    payload.resolution_id,
                    payload.mode,
                    payload.backfill_video_ids,
                )
            if payload.url is None:
                raise ValueError("Either url or resolution_id is required")
            channel = service.create_source_channel(
                str(payload.url),
                payload.mode,
                payload.backfill_video_ids,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "id": channel.id,
            "youtube_channel_id": channel.youtube_channel_id,
            "title": channel.title,
            "url": channel.canonical_url,
            "mode": channel.mode,
        }

    @app.post(
        "/api/v1/source-resolutions",
        response_model=SourceResolutionResponse,
    )
    def resolve_source(payload: SourceUrlRequest) -> SourceResolutionResponse:
        try:
            return SourceResolutionResponse.model_validate(
                service.resolve_source_url(str(payload.url))
            )
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/source-resolutions/{resolution_id}:backfill")
    def resolution_backfill(resolution_id: str) -> dict[str, object]:
        try:
            return service.resolution_backfill(resolution_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/v1/source-resolutions/{resolution_id}:import-video",
        status_code=status.HTTP_201_CREATED,
        response_model=DirectSourceVideoImportResponse,
    )
    def import_resolved_source_video(
        resolution_id: str,
        response: Response,
    ) -> DirectSourceVideoImportResponse:
        try:
            result = service.import_source_video_from_resolution(resolution_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if result["reused"]:
            response.status_code = status.HTTP_200_OK
        return DirectSourceVideoImportResponse.model_validate(result)

    @app.get(
        "/api/v1/source-channels/{channel_id}",
        response_model=SourceChannelResponse,
    )
    def source_channel_detail(channel_id: int) -> SourceChannelResponse:
        try:
            channel = service.source_channel_detail(channel_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return SourceChannelResponse(
            id=channel.id,
            youtube_channel_id=channel.youtube_channel_id,
            title=channel.title,
            url=channel.canonical_url,
            mode=ChannelMode(channel.mode),
            avatar_url=channel.avatar_url,
        )

    @app.get("/api/v1/source-channels/{channel_id}/recent-videos")
    def channel_recent_videos(
        channel_id: int,
        refresh: bool = False,
    ) -> dict[str, object]:
        try:
            return {
                "items": service.channel_recent_videos(
                    channel_id,
                    refresh=refresh,
                )
            }
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/processing-queue")
    def processing_queue() -> dict[str, object]:
        return {"items": service.processing_queue()}

    @app.post("/api/v1/processing-runs/{processing_run_id}:start")
    def start_processing_run(
        processing_run_id: int,
    ) -> dict[str, object]:
        try:
            run = service.start_processing_run(processing_run_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"id": run.id, "state": run.status, "stage": run.stage}

    @app.post("/api/v1/processing-runs:start-all")
    def start_all_processing_runs() -> dict[str, int]:
        return service.start_all_processing_runs()

    @app.delete("/api/v1/processing-runs/{processing_run_id}")
    def cancel_processing_run(
        processing_run_id: int,
    ) -> dict[str, object]:
        try:
            return service.cancel_processing_run(processing_run_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/api/v1/source-channels:resolve",
        response_model=SourceChannelResolutionResponse,
    )
    def resolve_source_channel(
        payload: SourceUrlRequest,
    ) -> SourceChannelResolutionResponse:
        try:
            (
                resolution,
                supported_source_video,
                recent,
            ) = service.resolve_source_channel(str(payload.url))
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return SourceChannelResolutionResponse(
            channel=ResolvedSourceChannel(
                youtube_channel_id=resolution.youtube_channel_id,
                title=resolution.title,
                url=resolution.canonical_url,
            ),
            supported_source_video=(
                SourceVideoSummary(
                    youtube_video_id=(supported_source_video.youtube_video_id),
                    title=supported_source_video.title,
                    duration_seconds=(supported_source_video.duration_seconds),
                    published_at=supported_source_video.published_at,
                    source_url=supported_source_video.source_url,
                )
                if supported_source_video is not None
                else None
            ),
            recent_videos=[
                SourceVideoSummary(
                    youtube_video_id=candidate.youtube_video_id,
                    title=candidate.title,
                    duration_seconds=candidate.duration_seconds,
                    published_at=candidate.published_at,
                    source_url=candidate.source_url,
                )
                for candidate in recent
            ],
        )

    @app.post("/api/v1/source-videos:resolve")
    def resolve_source_video(
        payload: SourceUrlRequest,
    ) -> dict[str, object]:
        try:
            candidate = service.resolve_source_video(str(payload.url))
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "youtube_video_id": candidate.youtube_video_id,
            "title": candidate.title,
            "duration_seconds": candidate.duration_seconds,
            "language": candidate.language,
            "published_at": candidate.published_at,
            "source_url": candidate.source_url,
        }

    @app.post(
        "/api/v1/source-channels/{channel_id}/candidate-videos",
        status_code=status.HTTP_201_CREATED,
    )
    def import_source_video(
        channel_id: int,
        payload: SourceVideoImport,
    ) -> dict[str, object]:
        try:
            if payload.youtube_video_id is not None:
                return service.queue_channel_video(
                    channel_id,
                    payload.youtube_video_id,
                )
            if payload.url is None:
                raise ValueError("Either url or youtube_video_id is required")
            candidate = service.import_source_video(
                channel_id,
                str(payload.url),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "id": candidate.id,
            "youtube_video_id": candidate.youtube_video_id,
            "state": candidate.status,
        }

    @app.get("/api/v1/source-channels")
    def list_source_channels() -> dict[str, object]:
        return {
            "items": [
                {
                    "id": channel.id,
                    "youtube_channel_id": channel.youtube_channel_id,
                    "title": channel.title,
                    "url": channel.canonical_url,
                    "mode": channel.mode,
                    "avatar_url": channel.avatar_url,
                }
                for channel in service.source_channels()
            ]
        }

    @app.patch(
        "/api/v1/source-channels/{channel_id}",
        response_model=SourceChannelResponse,
    )
    def update_source_channel(
        channel_id: int,
        payload: SourceChannelUpdate,
    ) -> SourceChannelResponse:
        try:
            channel = service.update_source_channel(
                channel_id,
                payload.mode,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return SourceChannelResponse(
            id=channel.id,
            youtube_channel_id=channel.youtube_channel_id,
            title=channel.title,
            url=channel.canonical_url,
            mode=ChannelMode(channel.mode),
            avatar_url=channel.avatar_url,
        )

    @app.post(
        "/api/v1/discovery-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_discovery() -> dict[str, int]:
        try:
            return service.run_manual_discovery()
        except DiscoveryAlreadyRunningError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get(
        "/api/v1/processing-runs",
        response_model=ProcessingRunsResponse,
    )
    def list_processing_runs() -> ProcessingRunsResponse:
        items = [
            ProcessingRunSummaryResponse(
                id=item.id,
                revision=item.revision,
                status=item.status,
                title=item.title,
                source_show=item.source_show,
                owner_state=item.owner_state,
                current_step=item.current_step,
                estimated_cost_usd=item.estimated_cost_usd,
                translation=ProcessingRunSummaryTranslationResponse(
                    input_tokens=item.input_tokens,
                    output_tokens=item.output_tokens,
                ),
                latest_change_at=item.latest_change_at,
            )
            for item in service.processing_run_summaries()
        ]
        revision = hashlib.sha256(
            json.dumps(
                [item.revision for item in items],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return ProcessingRunsResponse(
            revision=revision,
            items=items,
        )

    @app.get(
        "/api/v1/processing-runs/{processing_run_id}",
        response_model=ProcessingRunDetailResponse,
    )
    def processing_run_detail(
        processing_run_id: int,
    ) -> ProcessingRunDetailResponse:
        try:
            return ProcessingRunDetailResponse.model_validate(
                service.processing_run_detail(processing_run_id)
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get(
        "/api/v1/processing-runs/{processing_run_id}/diagnostics",
        response_model=ProcessingRunDiagnosticsResponse,
    )
    def processing_run_diagnostics(
        processing_run_id: int,
    ) -> ProcessingRunDiagnosticsResponse:
        try:
            return ProcessingRunDiagnosticsResponse.model_validate(
                service.processing_run_diagnostics(processing_run_id)
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/processing-runs:advance")
    def advance_processing() -> dict[str, object]:
        outcome = service.advance_next_processing_run()
        if outcome is None:
            raise HTTPException(
                status_code=404,
                detail="No Processing Run is ready.",
            )
        service.deliver_pending_publication_notifications()
        return outcome

    @app.get("/api/v1/review-queue")
    def review_queue() -> dict[str, object]:
        return {
            "items": [
                {
                    "id": run.id,
                    "candidate_video_id": run.candidate_video_id,
                    "stage": run.stage,
                    "error_kind": run.error_kind,
                    "error_summary": run.error_summary,
                }
                for run in service.review_queue()
            ]
        }

    @app.post(
        "/api/v1/processing-runs/{processing_run_id}:retry",
        response_model=ProcessingRunDetailResponse,
    )
    def retry_processing_run(
        processing_run_id: int,
    ) -> ProcessingRunDetailResponse:
        try:
            run = service.retry_processing_run(processing_run_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return ProcessingRunDetailResponse.model_validate(
            service.processing_run_detail(run.id)
        )

    @app.get("/api/v1/decision-queue")
    def decision_queue() -> dict[str, object]:
        return {
            "items": [
                {
                    "id": candidate.id,
                    "youtube_video_id": candidate.youtube_video_id,
                    "title": candidate.title,
                    "description": candidate.description,
                    "duration_seconds": candidate.duration_seconds,
                    "source_url": candidate.source_url,
                    "confidence": candidate.triage_confidence,
                    "rationale": candidate.triage_rationale,
                    "matched_interests": candidate.matched_interests_json,
                    "triage_decision": candidate.triage_decision,
                    "title_zh": candidate.title_zh,
                    "summary_zh": candidate.summary_zh,
                    "recommendation_zh": candidate.recommendation_zh,
                }
                for candidate in service.decision_queue()
            ]
        }

    @app.post("/api/v1/candidate-videos/{candidate_id}:decide")
    def decide_candidate(
        candidate_id: int,
        payload: CandidateDecisionRequest,
    ) -> dict[str, object]:
        try:
            candidate = service.decide_candidate(
                candidate_id,
                payload.decision,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"id": candidate.id, "state": candidate.status}

    @app.get("/api/v1/interest-profile")
    def interest_profile() -> dict[str, object]:
        return service.interest_profile()

    @app.put("/api/v1/interest-profile")
    def update_interest_profile(
        payload: InterestProfileUpdate,
    ) -> dict[str, object]:
        try:
            return service.update_interest_profile(payload.markdown)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/v1/interest-profile/proposals",
        status_code=status.HTTP_201_CREATED,
    )
    def propose_interest_profile(
        payload: InterestProfileUpdate,
    ) -> dict[str, object]:
        try:
            return service.propose_interest_profile(payload.markdown)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/interest-profile/revisions/{version}:approve")
    def approve_interest_profile(version: int) -> dict[str, object]:
        try:
            return service.approve_interest_profile(version)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/v1/interest-profile/revisions")
    def interest_profile_revisions() -> dict[str, object]:
        return {
            "items": [
                {
                    "version": revision.version,
                    "markdown": revision.markdown,
                    "active": revision.active,
                    "source": revision.source,
                    "created_at": revision.created_at,
                }
                for revision in service.interest_profile_revisions()
            ]
        }

    @app.get("/api/v1/settings")
    def get_settings() -> dict[str, object]:
        return service.settings()

    @app.patch("/api/v1/settings")
    def update_settings(
        payload: SettingsUpdate,
    ) -> dict[str, object]:
        updates = payload.model_dump(exclude_none=True)
        try:
            return service.update_settings(updates)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/maintenance-runs")
    def run_maintenance() -> dict[str, object]:
        return service.run_maintenance()

    @app.post("/api/v1/spend-guardrail:resume")
    def resume_spend_guardrail() -> dict[str, bool]:
        return service.resume_spend_guardrail()

    @app.get("/api/v1/storage-usage")
    def storage_usage() -> dict[str, int]:
        return service.storage_usage()

    @app.get("/api/v1/ignored-list")
    def ignored_list(
        query: Annotated[str, Query(max_length=512)] = "",
    ) -> dict[str, object]:
        return {
            "items": [
                {
                    "id": candidate.id,
                    "youtube_video_id": candidate.youtube_video_id,
                    "title": candidate.title,
                    "rationale": candidate.triage_rationale,
                }
                for candidate in service.ignored_list(query)
            ]
        }

    @app.post("/api/v1/candidate-videos/{candidate_id}:process")
    def process_ignored_candidate(candidate_id: int) -> dict[str, object]:
        try:
            candidate = service.process_ignored_candidate(candidate_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"id": candidate.id, "state": candidate.status}

    @app.get("/api/v1/episodes")
    def list_episodes() -> dict[str, object]:
        return {
            "items": [
                {
                    "id": episode.id,
                    "title": episode.title,
                    "overview": episode.overview,
                    "state": episode.state,
                    "duration_seconds": episode.duration_seconds,
                    "source_show": episode.source_show,
                    "source_title": episode.source_title,
                    "source_url": episode.source_url,
                    "source_published_at": episode.source_published_at,
                    "guests": json.loads(episode.guests_json),
                    "created_at": episode.created_at,
                    "published_at": episode.published_at,
                }
                for episode in service.list_episodes()
            ]
        }

    @app.get(
        "/api/v1/episode-library",
        response_model=EpisodeLibraryResponse,
    )
    def episode_library() -> EpisodeLibraryResponse:
        return EpisodeLibraryResponse(
            items=[
                EpisodeLibraryItemResponse(
                    episode_id=item.episode_id,
                    processing_run_id=item.processing_run_id,
                    title=item.title,
                    overview=item.overview,
                    state=item.state,
                    stage=item.stage,
                    duration_seconds=item.duration_seconds,
                    source_show=item.source_show,
                    source_title=item.source_title,
                    source_url=item.source_url,
                    source_published_at=item.source_published_at,
                    guests=list(item.guests),
                    created_at=item.created_at,
                    published_at=item.published_at,
                    can_reprocess=item.can_reprocess,
                    library_state=item.library_state,
                    available_actions=list(item.available_actions),
                    artwork_url=item.artwork_url,
                )
                for item in service.episode_library()
            ]
        )

    @app.post(
        "/api/v1/episodes/{episode_id}:reprocess",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def reprocess_episode(episode_id: int) -> dict[str, object]:
        try:
            return service.reprocess_episode(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/v1/episodes/{episode_id}")
    def episode_detail(episode_id: int) -> dict[str, object]:
        try:
            return service.episode_detail(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/v1/episodes/{episode_id}/artwork")
    def episode_artwork(
        episode_id: int,
        published: bool = False,
    ) -> Response:
        try:
            content, media_type = service.episode_artwork(
                episode_id,
                published=published,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=300"},
        )

    @app.post(
        "/api/v1/episodes/{episode_id}:prepare-metadata-only-revision",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def prepare_metadata_only_revision(
        episode_id: int,
    ) -> dict[str, object]:
        try:
            episode = service.prepare_metadata_only_revision(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        service.deliver_pending_publication_notifications()
        return {
            "episode_id": episode.id,
            "state": episode.state,
            "revision_version": episode.revision_version,
        }

    @app.api_route(
        "/api/v1/episodes/{episode_id}/preview",
        methods=["GET", "HEAD"],
    )
    def episode_preview(
        episode_id: int,
        published: bool = False,
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> Response:
        if published:
            try:
                published_content = service.published_episode_audio(episode_id)
            except LookupError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            return _media_response(published_content, range_header)
        try:
            detail = service.episode_detail(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        preview = detail["preview"]
        if not isinstance(preview, dict):
            raise HTTPException(
                status_code=500,
                detail="Private Preview metadata is invalid.",
            )
        content = dependencies.storage.read_preview(str(preview["key"]))
        if content is None:
            raise HTTPException(
                status_code=404,
                detail="Private Preview was not found.",
            )
        response = _media_response(content, range_header)
        if preview.get("available") is not True:
            response.headers["X-TransPodcast-Preview-Expired"] = "true"
        return response

    @app.patch(
        "/api/v1/episodes/{episode_id}/segments/{ordinal}",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def revise_segment(
        episode_id: int,
        ordinal: int,
        payload: SegmentRevisionRequest,
    ) -> dict[str, object]:
        try:
            job = service.queue_segment_revision(
                episode_id,
                ordinal,
                payload.target_text,
                payload.speaker_id,
                payload.emotion_reference,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "episode_id": episode_id,
            "segment_ordinal": job.ordinal,
            "revision_job_id": job.id,
            "revision_state": job.status,
        }

    @app.get("/api/v1/episodes/{episode_id}/segments/{ordinal}/revisions")
    def segment_revisions(
        episode_id: int,
        ordinal: int,
    ) -> dict[str, object]:
        try:
            revisions = service.segment_revisions(episode_id, ordinal)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "items": [
                {
                    "version": revision.version,
                    "target_text": revision.target_text,
                    "speaker_id": revision.speaker_id,
                    "emotion_reference": revision.emotion_reference,
                    "preview_key": revision.preview_key,
                    "created_at": revision.created_at,
                }
                for revision in revisions
            ]
        }

    @app.get("/api/v1/episodes/{episode_id}/processing-detail")
    def episode_processing_detail(episode_id: int) -> dict[str, object]:
        try:
            return service.episode_processing_detail(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post(
        "/api/v1/episodes/{episode_id}/segments/{ordinal}:rule",
    )
    def rule_processing_segment(
        episode_id: int,
        ordinal: int,
        payload: ProcessingSegmentRuling,
    ) -> dict[str, object]:
        try:
            return service.rule_processing_segment(
                episode_id,
                ordinal,
                payload.action,
                payload.speaker_id,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/api/v1/episodes/{episode_id}/segments/{ordinal}:mark-issue",
    )
    def mark_segment_issue(
        episode_id: int,
        ordinal: int,
        payload: SegmentIssueRequest,
    ) -> dict[str, object]:
        try:
            return service.mark_segment_issue(
                episode_id,
                ordinal,
                payload.note,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.patch(
        "/api/v1/episodes/{episode_id}/transcript-segments/{ordinal}",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def revise_transcript_segment(
        episode_id: int,
        ordinal: int,
        payload: TranscriptRevisionRequest,
    ) -> dict[str, object]:
        try:
            job = service.queue_transcript_revision(
                episode_id,
                ordinal,
                payload.source_text,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "episode_id": episode_id,
            "segment_ordinal": job.ordinal,
            "revision_job_id": job.id,
            "revision_state": job.status,
        }

    @app.get(
        "/api/v1/episodes/{episode_id}/transcript-segments/{ordinal}/revisions"
    )
    def transcript_revisions(
        episode_id: int,
        ordinal: int,
    ) -> dict[str, object]:
        try:
            revisions = service.transcript_revisions(
                episode_id,
                ordinal,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "items": [
                {
                    "version": revision.version,
                    "source_text": revision.source_text,
                    "created_at": revision.created_at,
                }
                for revision in revisions
            ]
        }

    @app.patch("/api/v1/episodes/{episode_id}/metadata")
    def revise_episode_metadata(
        episode_id: int,
        payload: EpisodeMetadataUpdate,
    ) -> dict[str, object]:
        try:
            episode, changed = service.revise_episode_metadata(
                episode_id,
                payload.title,
                payload.overview,
                payload.publication_description,
                [
                    (chapter.start_seconds, chapter.title)
                    for chapter in payload.chapters
                ],
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if changed:
            service.deliver_pending_publication_notifications()
        return {
            "id": episode.id,
            "revision_version": episode.revision_version,
            "state": episode.state,
            "changed": changed,
        }

    @app.get("/api/v1/episodes/{episode_id}/metadata-revisions")
    def episode_metadata_revisions(
        episode_id: int,
    ) -> dict[str, object]:
        try:
            revisions = service.episode_metadata_revisions(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "items": [
                {
                    "version": revision.version,
                    "title": revision.title,
                    "overview": revision.overview,
                    "publication_description": (
                        revision.publication_description
                    ),
                    "chapters": json.loads(revision.chapters_json),
                    "created_at": revision.created_at,
                }
                for revision in revisions
            ]
        }

    @app.post(
        "/api/v1/speakers",
        status_code=status.HTTP_201_CREATED,
    )
    def create_speaker(payload: SpeakerCreate) -> dict[str, object]:
        speaker = service.create_speaker(
            payload.display_name,
            payload.canonical_voice_reference,
            payload.verified,
        )
        return {
            "id": speaker.id,
            "display_name": speaker.display_name,
            "canonical_voice_reference": (speaker.canonical_voice_reference),
            "verified": speaker.verified,
            "status": speaker.status,
        }

    @app.get("/api/v1/speakers")
    def list_speakers() -> dict[str, object]:
        return {
            "items": [
                {
                    "id": speaker.id,
                    "display_name": speaker.display_name,
                    "canonical_voice_reference": (
                        speaker.canonical_voice_reference
                    ),
                    "verified": speaker.verified,
                    "status": speaker.status,
                }
                for speaker in service.speakers()
            ]
        }

    @app.get("/api/v1/channel-voice-library")
    def channel_voice_library() -> ChannelVoiceLibraryResponse:
        return ChannelVoiceLibraryResponse(
            items=[
                ChannelVoiceLibraryItemResponse(
                    source_channel_id=item.source_channel_id,
                    title=item.title,
                    avatar_url=item.avatar_url,
                    saved_profile_count=item.saved_profile_count,
                    pending_candidate_count=item.pending_candidate_count,
                )
                for item in service.channel_voice_library()
            ]
        )

    @app.get("/api/v1/source-channels/{source_channel_id}/voice-library")
    def channel_voice_library_detail(
        source_channel_id: int,
    ) -> ChannelVoiceLibraryDetailResponse:
        try:
            detail = service.channel_voice_library_detail(source_channel_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return ChannelVoiceLibraryDetailResponse(
            channel=ChannelVoiceLibraryChannelResponse(
                id=detail.source_channel_id,
                title=detail.title,
                avatar_url=detail.avatar_url,
            ),
            saved_profiles=[
                _channel_speaker_profile_response(profile)
                for profile in detail.saved_profiles
            ],
            pending_candidates=[
                ChannelVoiceCandidateResponse(
                    id=candidate.id,
                    speaker_id=candidate.speaker_id,
                    display_name=candidate.display_name,
                    local_label=candidate.local_label,
                    segment_count=candidate.segment_count,
                    source_episode=ChannelVoiceCandidateEpisodeResponse(
                        id=candidate.source_episode_id,
                        title=candidate.source_episode_title,
                    ),
                    voice_reference=VoiceReferencePreviewResponse(
                        id=candidate.voice_reference.id,
                        duration_seconds=(
                            candidate.voice_reference.duration_seconds
                        ),
                        audio_url=candidate.voice_reference.audio_url,
                    ),
                    possible_matches=[
                        ChannelSpeakerSuggestionResponse(
                            profile_id=match.profile_id,
                            source_channel_id=match.source_channel_id,
                            source_channel_title=(match.source_channel_title),
                            speaker_id=match.speaker_id,
                            display_name=match.display_name,
                            similarity=match.similarity,
                        )
                        for match in candidate.possible_matches
                    ],
                )
                for candidate in detail.pending_candidates
            ],
        )

    @app.post("/api/v1/channel-voice-candidates/{candidate_id}:save")
    def save_channel_voice_candidate(
        candidate_id: int,
        payload: ChannelVoiceCandidateSave,
    ) -> ChannelSpeakerProfileResponse:
        try:
            profile = service.save_channel_voice_candidate(
                candidate_id,
                payload.display_name,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _channel_speaker_profile_response(profile)

    @app.post("/api/v1/channel-voice-candidates/{candidate_id}:ignore")
    def ignore_channel_voice_candidate(
        candidate_id: int,
    ) -> ChannelVoiceCandidateDecisionResponse:
        try:
            candidate = service.ignore_channel_voice_candidate(candidate_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return ChannelVoiceCandidateDecisionResponse(
            id=candidate.id,
            decision=ChannelVoiceCandidateDecision(candidate.decision),
        )

    @app.post("/api/v1/channel-voice-candidates/{candidate_id}:restore")
    def restore_channel_voice_candidate(
        candidate_id: int,
    ) -> ChannelVoiceCandidateDecisionResponse:
        try:
            candidate = service.restore_channel_voice_candidate(candidate_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return ChannelVoiceCandidateDecisionResponse(
            id=candidate.id,
            decision=ChannelVoiceCandidateDecision(candidate.decision),
        )

    @app.post("/api/v1/channel-speaker-profiles/{profile_id}:stop-reuse")
    def stop_channel_speaker_profile(
        profile_id: int,
    ) -> ChannelSpeakerProfileResponse:
        try:
            profile = service.stop_channel_speaker_profile(profile_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return _channel_speaker_profile_response(profile)

    @app.patch("/api/v1/channel-speaker-profiles/{profile_id}")
    def update_channel_speaker_profile(
        profile_id: int,
        payload: ChannelSpeakerProfileUpdate,
    ) -> ChannelSpeakerProfileResponse:
        try:
            profile = service.rename_channel_speaker_profile(
                profile_id,
                payload.display_name,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _channel_speaker_profile_response(profile)

    @app.post("/api/v1/channel-voice-candidates/{candidate_id}:merge")
    def merge_channel_voice_candidate(
        candidate_id: int,
        payload: ChannelVoiceCandidateMerge,
    ) -> ChannelSpeakerProfileResponse:
        try:
            profile = service.merge_channel_voice_candidate(
                candidate_id,
                payload.target_profile_id,
                replace_canonical_reference=(
                    payload.replace_canonical_reference
                ),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _channel_speaker_profile_response(profile)

    @app.post(
        "/api/v1/speakers/{speaker_id}/voice-references",
        status_code=status.HTTP_201_CREATED,
    )
    def propose_voice_reference(
        speaker_id: int,
        payload: VoiceReferenceCreate,
    ) -> dict[str, object]:
        try:
            reference = service.propose_voice_reference(
                speaker_id,
                payload.artifact_key,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "id": reference.id,
            "artifact_key": reference.artifact_key,
            "status": reference.status,
        }

    @app.post(
        "/api/v1/speakers/{speaker_id}/voice-references/{reference_id}:approve"
    )
    def approve_voice_reference(
        speaker_id: int,
        reference_id: int,
    ) -> dict[str, object]:
        try:
            speaker = service.approve_voice_reference(
                speaker_id,
                reference_id,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "id": speaker.id,
            "canonical_voice_reference": (speaker.canonical_voice_reference),
            "status": speaker.status,
        }

    @app.get("/api/v1/speakers/{speaker_id}/voice-references")
    def list_voice_references(
        speaker_id: int,
    ) -> dict[str, object]:
        try:
            references = service.voice_references(speaker_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "items": [
                {
                    "id": reference.id,
                    "artifact_key": reference.artifact_key,
                    "status": reference.status,
                    "created_at": reference.created_at,
                    "approved_at": reference.approved_at,
                }
                for reference in references
            ]
        }

    @app.get("/api/v1/speaker-voice-references/{reference_id}/audio")
    def voice_reference_audio(
        reference_id: int,
        range_header: Annotated[
            str | None,
            Header(alias="Range"),
        ] = None,
    ) -> Response:
        try:
            content, media_type = service.voice_reference_audio(reference_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        headers = {"Accept-Ranges": "bytes"}
        if range_header is None:
            return Response(
                content=content,
                media_type=media_type,
                headers=headers,
            )
        byte_range = _parse_byte_range(range_header, len(content))
        if byte_range is None:
            return Response(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                headers={
                    **headers,
                    "Content-Range": f"bytes */{len(content)}",
                },
            )
        start, end = byte_range
        return Response(
            content=content[start : end + 1],
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=media_type,
            headers={
                **headers,
                "Content-Range": (f"bytes {start}-{end}/{len(content)}"),
                "Content-Length": str(end - start + 1),
            },
        )

    @app.post("/api/v1/speakers/{speaker_id}:confirm")
    def confirm_speaker(speaker_id: int) -> dict[str, object]:
        try:
            speaker = service.confirm_speaker(speaker_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "id": speaker.id,
            "display_name": speaker.display_name,
            "status": speaker.status,
            "verified": speaker.verified,
        }

    @app.post("/api/v1/speakers/{speaker_id}:merge")
    def merge_speaker(
        speaker_id: int,
        payload: SpeakerMerge,
    ) -> dict[str, object]:
        try:
            speaker = service.merge_speaker(
                speaker_id,
                payload.target_speaker_id,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "id": speaker.id,
            "status": speaker.status,
            "merged_into_id": speaker.merged_into_id,
        }

    @app.post(
        "/api/v1/speakers/{speaker_id}:split",
        status_code=status.HTTP_201_CREATED,
    )
    def split_speaker(
        speaker_id: int,
        payload: SpeakerSplit,
    ) -> dict[str, object]:
        try:
            speaker = service.split_speaker(
                speaker_id,
                payload.display_name,
                payload.episode_id,
                payload.segment_ordinals,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "id": speaker.id,
            "display_name": speaker.display_name,
            "status": speaker.status,
            "verified": speaker.verified,
        }

    @app.post("/api/v1/episodes/{episode_id}:resubmit")
    def resubmit_episode(episode_id: int) -> dict[str, object]:
        try:
            episode = service.resubmit_episode(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        service.deliver_pending_publication_notifications()
        return {"id": episode.id, "state": episode.state}

    @app.post("/api/v1/episodes/{episode_id}:notify-publication")
    def notify_publication(episode_id: int) -> dict[str, object]:
        try:
            episode = service.send_publication_decision(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"id": episode.id, "state": episode.state}

    @app.post(
        "/api/v1/episodes/{episode_id}:decide-publication",
        response_model=PublicationDecisionResponse,
    )
    def decide_publication(
        episode_id: int,
        request: PublicationDecisionRequest,
    ) -> PublicationDecisionResponse:
        try:
            return PublicationDecisionResponse.model_validate(
                service.apply_publication_decision(
                    episode_id,
                    request.decision,
                    channel="owner_console",
                    expected_state=request.expected_state,
                )
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.delete("/api/v1/episodes/{episode_id}")
    def delete_episode(episode_id: int) -> dict[str, object]:
        try:
            episode = service.delete_episode(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "id": episode.id,
            "state": episode.state,
            "recoverable_for_days": 30,
        }

    @app.post("/api/v1/episodes/{episode_id}:recover")
    def recover_episode(episode_id: int) -> dict[str, object]:
        try:
            episode = service.recover_episode(episode_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=410, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"id": episode.id, "state": episode.state}

    @app.post("/api/v1/telegram-actions/{token}:apply")
    def apply_telegram_action(token: str) -> dict[str, object]:
        try:
            return service.apply_telegram_action(token)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=410, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/v1/feed-token:revoke")
    def revoke_feed_token() -> dict[str, str]:
        try:
            return service.revoke_feed_token()
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/v1/private-feed-subscription")
    def private_feed_subscription() -> dict[str, str]:
        try:
            return service.private_feed_subscription()
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.api_route(
        "/feed/{feed_token}.xml",
        methods=["GET", "HEAD"],
    )
    def private_feed(feed_token: str) -> Response:
        if not service.authorize_feed(feed_token):
            raise HTTPException(
                status_code=404,
                detail="Feed was not found.",
            )
        content = service.private_feed()
        return Response(
            content=content,
            media_type="application/rss+xml",
        )

    @app.api_route(
        "/media/{feed_token}/{media_key:path}",
        methods=["GET", "HEAD"],
    )
    def publication_media(
        feed_token: str,
        media_key: str,
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> Response:
        if not service.authorize_feed(feed_token):
            raise HTTPException(
                status_code=404,
                detail="Feed was not found.",
            )
        content = dependencies.storage.read_publication(media_key)
        if content is None:
            raise HTTPException(status_code=404, detail="Media was not found.")
        return _media_response(content, range_header)

    return app


def _media_response(content: bytes, range_header: str | None) -> Response:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(len(content)),
    }
    if range_header is None:
        return Response(
            content=content, media_type="audio/mp4", headers=headers
        )
    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="Invalid byte range.")
    requested = range_header.removeprefix("bytes=")
    if "," in requested or not content:
        raise HTTPException(status_code=416, detail="Invalid byte range.")
    start_text, _, end_text = requested.partition("-")
    try:
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError
            start = max(0, len(content) - suffix_length)
            end = len(content) - 1
        else:
            start = int(start_text)
            end = (
                min(int(end_text), len(content) - 1)
                if end_text
                else len(content) - 1
            )
    except ValueError as error:
        raise HTTPException(
            status_code=416, detail="Invalid byte range."
        ) from error
    if start < 0 or start >= len(content) or end < start:
        raise HTTPException(status_code=416, detail="Invalid byte range.")
    ranged = content[start : end + 1]
    headers.update(
        {
            "Content-Length": str(len(ranged)),
            "Content-Range": f"bytes {start}-{end}/{len(content)}",
        }
    )
    return Response(
        content=ranged,
        status_code=206,
        media_type="audio/mp4",
        headers=headers,
    )
