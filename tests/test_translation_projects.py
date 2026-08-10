from __future__ import annotations

import json
import os

from transpodcast.translation_projects import (
    mark_translation_project_reprocess,
    read_translation_project,
)


def test_reprocess_marker_hides_stale_translation_progress(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    state_path = (
        artifact_root
        / "translation-projects"
        / "run-8"
        / "old-input"
        / "project.json"
    )
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({"phase": "completed", "input_hash": "old"}),
        encoding="utf-8",
    )
    os.utime(state_path, (1, 1))
    assert read_translation_project(artifact_root, 8) is not None

    mark_translation_project_reprocess(artifact_root, 8)

    assert read_translation_project(artifact_root, 8) is None
    state_path.write_text(
        json.dumps({"phase": "completed", "input_hash": "new"}),
        encoding="utf-8",
    )
    assert read_translation_project(artifact_root, 8) == {
        "phase": "completed",
        "input_hash": "new",
    }


def test_reprocess_marker_does_not_depend_on_timestamp_precision(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    run_root = artifact_root / "translation-projects" / "run-8"
    state_path = run_root / "old-input" / "project.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({"phase": "completed", "input_hash": "old"}),
        encoding="utf-8",
    )

    mark_translation_project_reprocess(artifact_root, 8)

    marker_mtime = (run_root / ".reprocess-after").stat().st_mtime_ns
    state_path.write_text(
        json.dumps({"phase": "completed", "input_hash": "new"}),
        encoding="utf-8",
    )
    os.utime(state_path, ns=(marker_mtime, marker_mtime))

    assert read_translation_project(artifact_root, 8) == {
        "phase": "completed",
        "input_hash": "new",
    }
