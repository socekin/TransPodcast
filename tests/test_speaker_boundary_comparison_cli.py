from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _analysis_json() -> str:
    return json.dumps(
        {
            "pipeline_version": "transpodcast-modal-pipeline-v6",
            "diarization_model": "sortformer",
            "transcription_model": "qwen3-asr",
            "alignment_model": "qwen3-forced-aligner",
            "segments": [
                {
                    "segment_id": "seg-0001",
                    "speaker_label": "speaker-a",
                    "start_seconds": 0.0,
                    "end_seconds": 0.4,
                    "text": "slow down a",
                    "words": [
                        ["slow", 0.0, 0.15],
                        ["down", 0.15, 0.3],
                        ["a", 0.3, 0.4],
                    ],
                },
                {
                    "segment_id": "seg-0002",
                    "speaker_label": "speaker-b",
                    "start_seconds": 0.4,
                    "end_seconds": 0.6,
                    "text": "bit",
                    "words": [["bit", 0.4, 0.6]],
                    "overlap": True,
                },
                {
                    "segment_id": "seg-0003",
                    "speaker_label": "speaker-a",
                    "start_seconds": 0.6,
                    "end_seconds": 0.9,
                    "text": "please",
                    "words": [["please", 0.6, 0.9]],
                },
            ],
        }
    )


def test_cli_replays_runs_six_and_seven_without_database_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "transpodcast.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE processing_runs (
            id INTEGER PRIMARY KEY,
            analysis_json TEXT,
            render_json TEXT
        );
        CREATE TABLE episodes (
            processing_run_id INTEGER,
            publication_guid TEXT,
            title TEXT,
            deleted_at TEXT
        );
        """
    )
    for run_id in (6, 7):
        connection.execute(
            "INSERT INTO processing_runs VALUES (?, ?, ?)",
            (
                run_id,
                _analysis_json(),
                json.dumps(
                    {
                        "segment_timings": [
                            [0.0, 0.3],
                            [0.3, 0.5],
                            [0.5, 0.8],
                        ]
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO episodes VALUES (?, ?, ?, NULL)",
            (run_id, f"episode-{run_id}", f"Episode {run_id}"),
        )
    connection.commit()
    connection.close()
    before = _digest(database)
    output = tmp_path / "comparison"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/speaker_boundary_comparison.py",
            "--run",
            "6",
            "--run",
            "7",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TRANSPODCAST_DATABASE_PATH": str(database),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert _digest(database) == before
    assert (output / "run-6.json").is_file()
    assert (output / "run-7.json").is_file()
    assert "Historical Pipeline v6 checkpoints are partial" in (
        output / "summary.md"
    ).read_text(encoding="utf-8")
