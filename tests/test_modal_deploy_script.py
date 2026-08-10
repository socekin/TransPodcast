from __future__ import annotations

import subprocess
from pathlib import Path

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
            "TRANSPODCAST_MODAL_DEPLOY_COMMAND": str(
                repo / "deploy/record-deploy"
            ),
            "TRANSPODCAST_MODAL_PROFILES_COMMAND": f"cat {profiles_file}",
        },
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
    (repo / "data").mkdir(exist_ok=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "data/deploy-state").write_text(head, encoding="utf-8")
    second = _run_deploy(repo, "")

    assert first == [f"none {app}" for app in _APPS]
    assert second == []
