from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from transpodcast.adapters.codex import (
    CodexRunner,
    CodexTranslationAdapter,
    CodexTriageAdapter,
)
from transpodcast.adapters.r2 import R2PublicationStorage
from transpodcast.adapters.speech import (
    ModalCommandAnalysisAdapter,
    ModalCommandSpeechAdapter,
)
from transpodcast.adapters.telegram import TelegramBotAdapter
from transpodcast.adapters.youtube import YtDlpSourceAdapter
from transpodcast.config import TransPodcastSettings
from transpodcast.database import Database
from transpodcast.dependencies import AppDependencies, SpeakerMatchPolicy
from transpodcast.processing_activity import ProcessingRunActivityRepository
from transpodcast.storage import LocalPublicationStorage


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC).replace(tzinfo=None)


def build_dependencies(
    settings: TransPodcastSettings | None = None,
) -> AppDependencies:
    resolved = settings or TransPodcastSettings()
    database = Database(resolved.database_path)
    activity_repository = ProcessingRunActivityRepository(database)
    codex = CodexRunner(
        executable=resolved.codex_executable,
        model=resolved.codex_model,
    )
    storage = (
        R2PublicationStorage(
            endpoint_url=resolved.r2_endpoint_url,
            bucket=resolved.r2_bucket,
            access_key_id=resolved.r2_access_key_id,
            secret_access_key=resolved.r2_secret_access_key,
        )
        if resolved.storage_mode == "r2"
        else LocalPublicationStorage(resolved.local_storage_root)
    )
    return AppDependencies(
        database=database,
        source=YtDlpSourceAdapter(
            executable=str(
                Path(sys.executable).with_name("yt-dlp")
            ),
            proxy_url=resolved.youtube_proxy_url,
        ),
        triage=CodexTriageAdapter(codex),
        translation=CodexTranslationAdapter(
            codex,
            project_root=resolved.artifact_root / "translation-projects",
            progress_reporter=activity_repository.record_translation_phase,
        ),
        speech=ModalCommandSpeechAdapter(resolved.modal_command),
        storage=storage,
        notifications=TelegramBotAdapter(
            resolved.telegram_bot_token,
            resolved.telegram_owner_chat_id,
        ),
        clock=SystemClock(),
        feed_token=resolved.feed_token,
        publication_domain=resolved.publication_domain.rstrip("/"),
        artifact_root=resolved.artifact_root,
        show_title=resolved.show_title,
        show_description=resolved.show_description,
        show_author=resolved.show_author,
        show_artwork=(
            resolved.show_artwork_path.read_bytes()
            if resolved.show_artwork_path.is_file()
            else b""
        ),
        analysis=ModalCommandAnalysisAdapter(resolved.modal_command),
        feed_token_path=resolved.feed_token_path,
        speaker_match_policy=SpeakerMatchPolicy(
            version=resolved.speaker_match_policy_version,
            minimum_analysis_confidence=(
                resolved.speaker_match_minimum_analysis_confidence
            ),
            minimum_similarity=(
                resolved.speaker_match_minimum_similarity
            ),
        ),
    )
