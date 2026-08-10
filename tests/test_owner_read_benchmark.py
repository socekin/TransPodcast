from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_owner_read_benchmark_reports_selected_column_profiles() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_owner_console_reads.py",
            "--runs",
            "1",
            "--artifact-mib",
            "1",
            "--warm-requests",
            "1",
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )

    results = json.loads(completed.stdout)
    episode_profile = results["endpoints"][
        "/api/v1/episode-library"
    ]["selected_column_profile"]
    run_profile = results["endpoints"][
        "/api/v1/processing-runs"
    ]["selected_column_profile"]

    assert "episodes.title" in episode_profile["selected_columns"]
    assert "processing_runs.status" in run_profile["selected_columns"]
    assert episode_profile["artifact_columns_selected"] == []
    assert run_profile["artifact_columns_selected"] == []
