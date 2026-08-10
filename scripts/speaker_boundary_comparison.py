"""Replay Speaker Boundary Stabilization against durable checkpoints.

The database is opened in SQLite read-only mode. The command never invokes
Modal, Codex, YouTube, Cloudflare, Telegram, or TTS.

Usage:
  uv run python scripts/speaker_boundary_comparison.py --run 101 --run 102
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from transpodcast.boundary_comparison import build_boundary_comparison
from transpodcast.config import TransPodcastSettings
from transpodcast.service import deserialize_analysis


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        action="append",
        type=int,
        dest="run_ids",
        help="Processing Run id; repeat to compare multiple runs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/speaker-boundary-comparison"),
    )
    parser.add_argument("--audition-limit", type=int, default=12)
    return parser.parse_args()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"System of record is unavailable: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(
    connection: sqlite3.Connection,
    run_ids: list[int],
) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in run_ids)
    return list(
        connection.execute(
            f"""
            SELECT
                runs.id AS run_id,
                runs.analysis_json AS analysis_json,
                runs.render_json AS render_json,
                episodes.publication_guid AS publication_guid,
                episodes.title AS title
            FROM processing_runs AS runs
            JOIN episodes ON episodes.processing_run_id = runs.id
            WHERE runs.id IN ({placeholders})
              AND episodes.deleted_at IS NULL
            ORDER BY runs.id
            """,
            tuple(run_ids),
        )
    )


def _segment_timings(value: str | None) -> tuple[tuple[float, float], ...]:
    if value is None:
        return ()
    parsed = json.loads(value)
    return tuple(
        (float(item[0]), float(item[1]))
        for item in parsed.get("segment_timings", [])
    )


def _summary_markdown(comparisons: list[dict[str, Any]]) -> str:
    lines = [
        "# Speaker Boundary Stabilization Comparison",
        "",
        "Historical Pipeline v6 checkpoints are partial because they do not "
        "retain raw Sortformer turns or complete ASR text.",
        "",
        "| Run | Evidence | Segments | Transitions | A-B-A | Corrected | "
        "Unresolved | Audition |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in comparisons:
        evidence = "partial" if item["evidence"]["partial"] else "complete"
        lines.append(
            f"| {item['run_id']} | {evidence} | "
            f"{item['before']['segment_count']} → "
            f"{item['after']['segment_count']} | "
            f"{item['before']['speaker_transitions']} → "
            f"{item['after']['speaker_transitions']} | "
            f"{item['before']['a_b_a_interleaves']} → "
            f"{item['after']['a_b_a_interleaves']} | "
            f"{item['stabilization']['corrected_count']} | "
            f"{item['stabilization']['unresolved_count']} | "
            f"{len(item['audition'])} |"
        )
    lines.extend(["", "## Bounded audition list", ""])
    auditions = [
        (int(item["run_id"]), audition)
        for item in comparisons
        for audition in item["audition"]
    ]
    if not auditions:
        lines.append("No unresolved candidate requires listening.")
    for run_id, audition in auditions:
        publication = audition["publication_seconds"]
        publication_label = (
            f"{float(publication):.2f}s"
            if publication is not None
            else "unavailable"
        )
        lines.append(
            f"- Run {run_id}, `{audition['segment_id']}`, source "
            f"{float(audition['source_seconds']):.2f}s, publication "
            f"{publication_label}: “{audition['text']}” "
            f"([Evaluation Report]({audition['report_path']}))"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    arguments = _arguments()
    run_ids = list(dict.fromkeys(arguments.run_ids or []))
    if not run_ids:
        raise SystemExit("Provide at least one Processing Run with --run")
    settings = TransPodcastSettings()
    connection = _read_only_connection(settings.database_path)
    try:
        rows = _rows(connection, run_ids)
    finally:
        connection.close()
    found = {int(row["run_id"]) for row in rows}
    missing = [run_id for run_id in run_ids if run_id not in found]
    if missing:
        raise SystemExit(
            "Completed Translated Episodes are unavailable for runs: "
            + ", ".join(str(run_id) for run_id in missing)
        )
    arguments.output.mkdir(parents=True, exist_ok=True)
    comparisons: list[dict[str, Any]] = []
    for row in rows:
        analysis_json = row["analysis_json"]
        if analysis_json is None:
            raise SystemExit(
                f"Processing Run {row['run_id']} has no analysis checkpoint"
            )
        comparison = build_boundary_comparison(
            deserialize_analysis(str(analysis_json)),
            segment_timings=_segment_timings(row["render_json"]),
            run_id=int(row["run_id"]),
            publication_guid=str(row["publication_guid"]),
            title=str(row["title"]),
            audition_limit=arguments.audition_limit,
        )
        if not comparison["invariants"]["word_coverage_preserved"]:
            raise SystemExit(
                f"Processing Run {row['run_id']} failed word coverage"
            )
        arguments.output.joinpath(
            f"run-{row['run_id']}.json"
        ).write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        comparisons.append(comparison)
    arguments.output.joinpath("summary.md").write_text(
        _summary_markdown(comparisons),
        encoding="utf-8",
    )
    for item in comparisons:
        print(
            f"run {item['run_id']}: "
            f"{item['before']['speaker_transitions']} -> "
            f"{item['after']['speaker_transitions']} transitions; "
            f"{item['stabilization']['corrected_count']} corrected; "
            f"{item['stabilization']['unresolved_count']} unresolved"
        )
    print(f"summary: {arguments.output / 'summary.md'}")


if __name__ == "__main__":
    main()
