from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TransPodcastSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRANSPODCAST_",
        env_file=".env",
        extra="ignore",
    )

    database_path: Path = Path("data/transpodcast.sqlite3")
    artifact_root: Path = Path("data/artifacts")
    show_artwork_path: Path = Path(
        "apps/owner-console/public/assets/transpodcast-artwork.png"
    )
    show_title: str = "TransPodcast"
    show_description: str = "Private complete Mandarin podcast editions."
    show_author: str = "TransPodcast"
    owner_console_dist: Path = Path("apps/owner-console/dist/client")
    local_storage_root: Path = Path("data/object-storage")
    publication_domain: str = "http://transpodcast.localhost:8000"
    feed_token: str = ""
    feed_token_path: Path = Path("data/secrets/feed-token")

    codex_executable: str = "codex"
    codex_model: str = ""
    # Claude Code is selectable through the ``agent_provider`` setting. An
    # empty model uses the CLI default under TransPodcast's isolation flags.
    claude_executable: str = "claude"
    claude_model: str = ""
    claude_effort: str = ""
    youtube_proxy_url: str = ""
    youtube_browser_executable: Path = Path(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    modal_command: str = "uv run transpodcast-modal-pipeline"
    modal_environment: str = "transpodcast"
    modal_l40s_usd_per_hour: float = Field(default=1.95, gt=0)
    speaker_match_policy_version: str = "channel-speaker-match-v1"
    speaker_match_minimum_analysis_confidence: float = Field(
        default=0.9,
        ge=0,
        le=1,
    )
    speaker_match_minimum_similarity: float = Field(
        default=0.9,
        ge=-1,
        le=1,
    )

    storage_mode: Literal["local", "r2"] = "local"
    r2_endpoint_url: str = ""
    r2_bucket: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""

    telegram_bot_token: str = ""
    telegram_owner_chat_id: str = ""

    worker_poll_seconds: float = Field(default=5.0, ge=0.5, le=300)
