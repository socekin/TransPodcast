from __future__ import annotations

from pathlib import Path

from transpodcast.config import TransPodcastSettings


def test_single_account_modal_pipeline_is_the_runtime_default() -> None:
    settings = TransPodcastSettings(_env_file=None)

    assert settings.modal_command == "uv run transpodcast-modal-pipeline"


def test_example_configuration_uses_a_deployment_neutral_show_author() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = TransPodcastSettings(_env_file=root / ".env.example")

    assert settings.show_author == "TransPodcast"
