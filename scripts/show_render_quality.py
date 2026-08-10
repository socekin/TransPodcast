"""Print the render checkpoint quality record and per-check verdicts.

Usage: uv run python scripts/show_render_quality.py [processing_run_id]
Read-only; defaults to the most recent Processing Run.
"""

from __future__ import annotations

import json
import sys

from sqlalchemy import select

from transpodcast.config import TransPodcastSettings
from transpodcast.database import Database
from transpodcast.models import ProcessingRunRecord

TRUE_PEAK_GATE_MAX_DBTP = -0.95


def main() -> None:
    database = Database(TransPodcastSettings().database_path)
    with database.session() as session:
        if len(sys.argv) > 1:
            run = session.get(ProcessingRunRecord, int(sys.argv[1]))
        else:
            run = session.scalar(
                select(ProcessingRunRecord).order_by(
                    ProcessingRunRecord.id.desc()
                )
            )
        if run is None:
            print("Processing Run was not found")
            return
        print(f"run #{run.id}: {run.status} · {run.stage}")
        if not run.render_json:
            print("No render checkpoint exists yet.")
            return
        checkpoint = json.loads(run.render_json)
        quality = checkpoint.get("quality", {})
        translation = json.loads(run.translation_json or "{}")
        expected = len(translation.get("segments", []))
        print("duration_seconds:", checkpoint.get("duration_seconds"))
        print(json.dumps(quality, indent=2))
        loudness = float(quality.get("loudness_lufs", 0.0))
        true_peak = float(quality.get("true_peak_dbtp", 0.0))
        checks = [
            ("codec AAC-LC", quality.get("codec") == "AAC-LC"),
            ("sample rate 48k", quality.get("sample_rate_hz") == 48_000),
            ("channels 2", quality.get("channels") == 2),
            (
                "bitrate 120-136",
                120 <= int(quality.get("bitrate_kbps", 0)) <= 136,
            ),
            ("loudness -16.5..-15.5", -16.5 <= loudness <= -15.5),
            (
                f"true peak <= {TRUE_PEAK_GATE_MAX_DBTP}",
                true_peak <= TRUE_PEAK_GATE_MAX_DBTP,
            ),
            ("clipping false", not quality.get("clipping", True)),
            (
                f"rendered segments == {expected}",
                quality.get("rendered_segments") == expected,
            ),
            (
                "timeline continuous",
                bool(quality.get("timeline_continuous")),
            ),
            (
                "no boundary artifacts",
                not quality.get("boundary_artifacts", True),
            ),
            (
                "no source language leakage",
                not quality.get("source_language_leakage", True),
            ),
        ]
        print()
        for name, passed in checks:
            print(("PASS" if passed else "FAIL"), "·", name)


if __name__ == "__main__":
    main()
