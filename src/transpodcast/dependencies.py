from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from transpodcast.adapters.agent import AGENT_PROVIDERS, DEFAULT_AGENT_PROVIDER
from transpodcast.database import Database
from transpodcast.domain import (
    ChannelResolution,
    OwnerNotification,
    PublicationMetadataResult,
    RecentVideoWindow,
    SourceAnalysisResult,
    SourceArchive,
    SourceCandidate,
    SourceThumbnail,
    SourceVideoPreview,
    SpeechResult,
    TranslationResult,
    TriageResult,
)
from transpodcast.speaker_ownership_v15 import (
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SourceAdapter(Protocol):
    def readiness(self) -> bool: ...

    def resolve_channel(self, url: str) -> ChannelResolution: ...

    def resolve_source(
        self,
        url: str,
    ) -> tuple[ChannelResolution, SourceCandidate | None]: ...

    def recent_video_previews(
        self,
        youtube_channel_id: str,
    ) -> RecentVideoWindow: ...

    def enrich_video_previews(
        self,
        previews: list[SourceVideoPreview],
    ) -> list[SourceVideoPreview]: ...

    def recent_videos(self, youtube_channel_id: str) -> list[SourceCandidate]: ...

    def discover(self, youtube_channel_id: str) -> list[SourceCandidate]: ...

    def supported_source_video(
        self,
        url: str,
    ) -> SourceCandidate | None: ...

    def video(self, url: str) -> SourceCandidate: ...

    def archive(self, candidate: SourceCandidate) -> SourceArchive: ...

    def thumbnail(self, candidate: SourceCandidate) -> SourceThumbnail: ...


class TriageAdapter(Protocol):
    def evaluate(
        self,
        candidate: SourceCandidate,
        interest_profile: str,
    ) -> TriageResult: ...


class SourceAnalysisAdapter(Protocol):
    def readiness(self) -> bool: ...

    def measure_ownership(
        self, analysis: SourceAnalysisResult, archive: SourceArchive,
        ranges: list[tuple[int, int]],
    ) -> dict[str, Any]: ...

    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult: ...


class TranslationAdapter(Protocol):
    def readiness(self) -> bool: ...

    def translate(self, candidate: SourceCandidate) -> TranslationResult: ...

    def retranslate_segment(
        self,
        candidate: SourceCandidate,
        current: TranslationResult,
        segment_ordinal: int,
    ) -> TranslationResult: ...

    def publication_metadata(
        self,
        candidate: SourceCandidate,
        current: TranslationResult,
    ) -> PublicationMetadataResult: ...

    def plan_ownership_continuity(
        self, regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult: ...

    def review_ownership_continuity(
        self, regions: Sequence[SpeakerOwnershipRegion],
        planning: SpeakerOwnershipPlanningResult,
    ) -> SpeakerOwnershipPlanningResult: ...

    def plan_speaker_ownership(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult: ...

    def review_speaker_ownership(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult: ...


class SpeechAdapter(Protocol):
    def readiness(self) -> bool: ...

    def listening_audit_readiness(self) -> bool: ...

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult: ...

    def render_segment(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
        segment_ordinal: int,
        prior_preview_key: str,
    ) -> SpeechResult: ...

    def remaster(self, speech: SpeechResult) -> SpeechResult: ...


class PublicationStorage(Protocol):
    def readiness(self) -> bool: ...

    def usage_by_class(self) -> dict[str, int]: ...

    def put_preview(self, key: str, content: bytes) -> None: ...

    def read_preview(self, key: str) -> bytes | None: ...

    def create_preview_url(
        self,
        key: str,
        expires_seconds: int,
    ) -> str | None: ...

    def put_source_archive(self, key: str, content: bytes) -> None: ...

    def read_source_archive(self, key: str) -> bytes | None: ...

    def promote_preview(self, preview_key: str, publication_key: str) -> None: ...

    def put_publication(
        self,
        key: str,
        content: bytes,
        content_type: str = "audio/mp4",
    ) -> None: ...

    def read_publication(self, key: str) -> bytes | None: ...

    def publication_media_paths(self) -> list[str]: ...

    def put_feed_snapshot(self, content: bytes) -> None: ...

    def put_feed_token_hash(self, token_hash: str) -> None: ...

    def read_feed_token_hash(self) -> str | None: ...

    def put_backup(self, key: str, content: bytes) -> None: ...

    def read_backup(self, key: str) -> bytes | None: ...

    def backup_paths(self) -> list[str]: ...

    def delete_backup(self, key: str) -> None: ...


class NotificationAdapter(Protocol):
    def readiness(self) -> bool: ...

    def send(self, notification: OwnerNotification) -> str | None: ...

    def complete_message(self, message_id: str, outcome: str) -> None: ...


@dataclass(frozen=True)
class SpeakerMatchPolicy:
    version: str = "channel-speaker-match-v1"
    minimum_analysis_confidence: float = 0.9
    minimum_similarity: float = 0.9


@dataclass(frozen=True)
class AppDependencies:
    database: Database
    source: SourceAdapter
    triage: TriageAdapter
    translation: TranslationAdapter
    speech: SpeechAdapter
    storage: PublicationStorage
    notifications: NotificationAdapter
    clock: Clock
    feed_token: str
    publication_domain: str
    artifact_root: Path
    show_title: str = "TransPodcast"
    show_description: str = "Private complete Mandarin podcast editions."
    show_author: str = "TransPodcast"
    show_artwork: bytes = b""
    analysis: SourceAnalysisAdapter | None = None
    feed_token_path: Path | None = None
    speaker_match_policy: SpeakerMatchPolicy = SpeakerMatchPolicy()
    # ``translation`` and ``triage`` serve the default Agent provider
    # (Codex). Other providers are keyed by provider name; a provider that
    # is absent here is not configured.
    agent_translations: Mapping[str, TranslationAdapter] = field(
        default_factory=dict
    )
    agent_triage: Mapping[str, TriageAdapter] = field(default_factory=dict)

    def translation_for(self, provider: str | None) -> TranslationAdapter:
        """The Translation Adapter of an Agent provider; NULL means Codex."""
        resolved = provider or DEFAULT_AGENT_PROVIDER
        adapter = self.agent_translations.get(resolved)
        if adapter is not None:
            return adapter
        if resolved == DEFAULT_AGENT_PROVIDER:
            return self.translation
        raise AgentProviderNotConfiguredError(
            f"Agent provider {resolved} is not configured"
        )

    def triage_for(self, provider: str | None) -> TriageAdapter:
        resolved = provider or DEFAULT_AGENT_PROVIDER
        adapter = self.agent_triage.get(resolved)
        if adapter is not None:
            return adapter
        if resolved == DEFAULT_AGENT_PROVIDER:
            return self.triage
        raise AgentProviderNotConfiguredError(
            f"Agent provider {resolved} is not configured"
        )

    def configured_agent_providers(self) -> tuple[str, ...]:
        return tuple(
            provider
            for provider in AGENT_PROVIDERS
            if provider == DEFAULT_AGENT_PROVIDER
            or provider in self.agent_translations
        )


class AgentProviderNotConfiguredError(RuntimeError):
    """The selected or frozen Agent provider has no adapter."""
