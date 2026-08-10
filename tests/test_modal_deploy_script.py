from __future__ import annotations

import subprocess
from pathlib import Path

from transpodcast.config import TransPodcastSettings

_REPO_SCRIPT = Path(__file__).resolve().parents[1] / "deploy/modal-deploy.sh"
_APPS = (
    "experiments/pipeline/modal_sortformer.py",
    "experiments/pipeline/modal_qwen3_asr.py",
    "experiments/pipeline/modal_speaker_embeddings.py",
    "experiments/pipeline/modal_indextts2_batch.py",
)
_RECORD_STUB = """#!/bin/sh
echo "${MODAL_PROFILE:-none} $1" >> "$DEPLOY_LOG"
"""
_UV_STUB = """#!/bin/sh
if [ "$1" = "run" ] && [ "$2" = "python" ]; then
  echo "${STUB_MODAL_ENVIRONMENT:-transpodcast}"
  exit 0
fi
echo "$*" >> "$DEPLOY_LOG"
"""
_FAIL_DEPLOY_STUB = """#!/bin/sh
exit 1
"""


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
    )


def _build_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for app in _APPS:
        path = repo / app
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("app = 1\n", encoding="utf-8")
    script = repo / "deploy/modal-deploy.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_REPO_SCRIPT.read_text(encoding="utf-8"))
    script.chmod(0o755)
    stub = repo / "deploy/record-deploy"
    stub.write_text(_RECORD_STUB, encoding="utf-8")
    stub.chmod(0o755)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _run_deploy(repo: Path, profiles: str) -> list[str]:
    profiles_file = repo / "profiles.txt"
    profiles_file.write_text(profiles, encoding="utf-8")
    log = repo / "deploy.log"
    log.write_text("", encoding="utf-8")
    subprocess.run(
        [str(repo / "deploy/modal-deploy.sh")],
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo),
            "DEPLOY_LOG": str(log),
            "TRANSPODCAST_MODAL_ENVIRONMENT": "transpodcast",
            "TRANSPODCAST_MODAL_DEPLOY_COMMAND": str(
                repo / "deploy/record-deploy"
            ),
            "TRANSPODCAST_MODAL_PROFILES_COMMAND": f"cat {profiles_file}",
        },
    )
    return log.read_text(encoding="utf-8").splitlines()


def _run_default_deploy(
    repo: Path,
    environment: str | None,
    *,
    dotenv_environment: str | None = None,
) -> list[str]:
    log = repo / "deploy.log"
    log.write_text("", encoding="utf-8")
    uv_stub = repo / "deploy/uv"
    uv_stub.write_text(_UV_STUB, encoding="utf-8")
    uv_stub.chmod(0o755)
    command_environment = {
        "PATH": f"{repo / 'deploy'}:/usr/bin:/bin",
        "HOME": str(repo),
        "DEPLOY_LOG": str(log),
        "STUB_MODAL_ENVIRONMENT": dotenv_environment or "transpodcast",
        "TRANSPODCAST_MODAL_PROFILES_COMMAND": "true",
    }
    if environment is not None:
        command_environment["TRANSPODCAST_MODAL_ENVIRONMENT"] = environment
    subprocess.run(
        [str(repo / "deploy/modal-deploy.sh")],
        check=True,
        capture_output=True,
        env=command_environment,
    )
    return log.read_text(encoding="utf-8").splitlines()


def test_new_profiles_receive_every_app(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)

    deploys = _run_deploy(repo, "alpha\nbeta\n")

    assert deploys == [
        *[f"alpha {app}" for app in _APPS],
        *[f"beta {app}" for app in _APPS],
    ]


def test_recorded_profiles_skip_unchanged_apps(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    _run_deploy(repo, "alpha\nbeta\n")

    deploys = _run_deploy(repo, "alpha\nbeta\n")

    assert deploys == []


def test_added_profile_is_fully_deployed_without_changes(
    tmp_path: Path,
) -> None:
    repo = _build_repo(tmp_path)
    _run_deploy(repo, "alpha\nbeta\n")

    deploys = _run_deploy(repo, "alpha\nbeta\ngamma\n")

    assert deploys == [f"gamma {app}" for app in _APPS]


def test_changed_app_redeploys_to_every_profile(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    _run_deploy(repo, "alpha\nbeta\n")
    changed = repo / _APPS[1]
    changed.write_text("app = 2\n", encoding="utf-8")
    _git(repo, "commit", "-aqm", "change one app")

    deploys = _run_deploy(repo, "alpha\nbeta\n")

    assert deploys == [f"alpha {_APPS[1]}", f"beta {_APPS[1]}"]


def test_legacy_mode_without_profiles_deploys_changed_apps(
    tmp_path: Path,
) -> None:
    repo = _build_repo(tmp_path)

    first = _run_deploy(repo, "")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    second = _run_deploy(repo, "")

    assert first == [f"none {app}" for app in _APPS]
    assert second == []
    assert (repo / "data/deploy-state").read_text(
        encoding="utf-8"
    ).strip() == head


def test_failed_legacy_deploy_does_not_record_success(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    failing_stub = repo / "deploy/fail-deploy"
    failing_stub.write_text(_FAIL_DEPLOY_STUB, encoding="utf-8")
    failing_stub.chmod(0o755)

    result = subprocess.run(
        [str(repo / "deploy/modal-deploy.sh")],
        check=False,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo),
            "TRANSPODCAST_MODAL_DEPLOY_COMMAND": str(failing_stub),
            "TRANSPODCAST_MODAL_PROFILES_COMMAND": "true",
        },
    )

    assert result.returncode != 0
    assert not (repo / "data/deploy-state").exists()


def test_default_deploy_command_uses_the_configured_environment(
    tmp_path: Path,
) -> None:
    repo = _build_repo(tmp_path)

    deploys = _run_default_deploy(repo, "research")

    assert deploys == [
        f"run modal deploy --env research {app}" for app in _APPS
    ]


def test_default_deploy_command_reads_environment_from_dotenv(
    tmp_path: Path,
) -> None:
    repo = _build_repo(tmp_path)
    dotenv_key = "TRANSPODCAST_MODAL_ENVIRONMENT"
    (repo / ".env").write_text(
        f"export {dotenv_key}=from-dotenv # deployment target\n",
        encoding="utf-8",
    )

    settings = TransPodcastSettings(_env_file=repo / ".env")
    deploys = _run_default_deploy(
        repo,
        None,
        dotenv_environment=settings.modal_environment,
    )

    assert deploys == [
        f"run modal deploy --env from-dotenv {app}" for app in _APPS
    ]
