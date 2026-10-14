from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

_REPROCESS_MARKER = ".reprocess-after"


def reset_translation_project(
    artifact_root: Path,
    processing_run_id: int,
) -> None:
    """Discard incompatible ephemeral work before rebuilding from analysis."""

    run_root = (
        artifact_root / "translation-projects" / f"run-{processing_run_id}"
    )
    if run_root.is_dir():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)


def read_translation_project(
    artifact_root: Path,
    processing_run_id: int,
) -> dict[str, Any] | None:
    run_root = (
        artifact_root / "translation-projects" / f"run-{processing_run_id}"
    )
    marker = run_root / _REPROCESS_MARKER
    stale_digests = _read_reprocess_marker(marker) if marker.exists() else {}
    legacy_reprocess_after = (
        marker.stat().st_mtime_ns
        if marker.exists() and stale_digests is None
        else None
    )
    states = sorted(
        (
            path
            for path in run_root.glob("*/project.json")
            if not _is_stale_project(
                path,
                run_root,
                stale_digests,
                legacy_reprocess_after,
            )
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for path in states:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def mark_translation_project_reprocess(
    artifact_root: Path,
    processing_run_id: int,
    translated_segments: tuple[str, ...] = (),
) -> None:
    """Preserve completed Author work but require an independent re-review."""

    run_root = (
        artifact_root / "translation-projects" / f"run-{processing_run_id}"
    )
    run_root.mkdir(parents=True, exist_ok=True)
    for state_path in run_root.glob("*/project.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        if translated_segments:
            if len(translated_segments) != int(
                state.get("total_targets", 0)
            ):
                continue
            state_path.parent.joinpath("reprocess-seed.json").write_text(
                json.dumps(
                    {"segments": translated_segments},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        turns = state.get("turns")
        author_turns = (
            [
                turn
                for turn in turns
                if isinstance(turn, dict) and turn.get("role") == "author"
            ]
            if isinstance(turns, list)
            else []
        )
        state["turns"] = author_turns
        state["input_tokens"] = sum(
            int(turn.get("input_tokens", 0)) for turn in author_turns
        )
        state["output_tokens"] = sum(
            int(turn.get("output_tokens", 0)) for turn in author_turns
        )
        state["reviewer_sessions"] = []
        state["reviewer_models"] = []
        state["reviewer_turns"] = 0
        state["reviewer_no_progress_turns"] = 0
        state.pop("reviewer_verdict_repair_turns", None)
        state.pop("reviewer_listening_repair_turns", None)
        state["phase"] = "reviewing"
        state["validation_status"] = "author_complete"
        state.pop("failed_role", None)
        state.pop("provider_error", None)
        review_root = state_path.parent / "review"
        if review_root.is_dir():
            shutil.rmtree(review_root)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    stale_states = {
        state_path.relative_to(run_root).as_posix(): digest
        for state_path in run_root.glob("*/project.json")
        if (digest := _file_digest(state_path)) is not None
    }
    (run_root / _REPROCESS_MARKER).write_text(
        json.dumps(
            {"version": 1, "stale_states": stale_states},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _read_reprocess_marker(marker: Path) -> dict[str, str] | None:
    """Return stale project digests, or ``None`` for a legacy marker."""

    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    stale_states = payload.get("stale_states")
    if not isinstance(stale_states, dict):
        return None
    return {
        str(path): str(digest)
        for path, digest in stale_states.items()
        if isinstance(path, str) and isinstance(digest, str)
    }


def _is_stale_project(
    path: Path,
    run_root: Path,
    stale_digests: dict[str, str] | None,
    legacy_reprocess_after: int | None,
) -> bool:
    if stale_digests is not None:
        expected = stale_digests.get(path.relative_to(run_root).as_posix())
        return expected is not None and _file_digest(path) == expected
    return (
        legacy_reprocess_after is not None
        and path.stat().st_mtime_ns <= legacy_reprocess_after
    )


def _file_digest(path: Path) -> str | None:
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(content).hexdigest()


def allow_translation_project_provider_rebind(
    artifact_root: Path,
    processing_run_id: int,
) -> None:
    """Let an explicit reprocess move a run's projects to a new provider.

    A Translation Project never resumes a session created by another Agent
    provider. Outside an explicit full-episode reprocess a provider change
    fails clearly; after this marker, the next turn retires the previous
    provider's sessions and starts fresh ones (see ``codex.py``).
    """

    run_root = (
        artifact_root / "translation-projects" / f"run-{processing_run_id}"
    )
    for state_path in run_root.glob("*/project.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        state["provider_rebind_allowed"] = True
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
