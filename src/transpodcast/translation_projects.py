from __future__ import annotations

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
    reprocess_after = marker.stat().st_mtime if marker.exists() else None
    states = sorted(
        (
            path
            for path in run_root.glob("*/project.json")
            if reprocess_after is None
            or path.stat().st_mtime >= reprocess_after
        ),
        key=lambda path: path.stat().st_mtime,
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
    (run_root / _REPROCESS_MARKER).touch()
