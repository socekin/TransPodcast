"""Generate a post-hoc Evaluation Report for Translated Episodes.

Reads the system of record strictly read-only and writes a
self-contained page per episode plus an index. It never opens a
writable database connection and never touches the Processing Service,
so it is safe to run at any time.

Usage:
  uv run python scripts/episode_evaluation_report.py --all
  uv run python scripts/episode_evaluation_report.py --run 12
  uv run python scripts/episode_evaluation_report.py --run <publication-guid>
  uv run python scripts/episode_evaluation_report.py \
    --all --verdicts reports/verdicts-ep-guid-1.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from transpodcast.config import TransPodcastSettings
from transpodcast.evaluation import (
    DEFAULT_SHORT_SEGMENT_SECONDS,
    DEFAULT_SHORT_SEGMENT_WORDS,
    INTERLEAVE_SUSPECT,
    MIDSPLIT_SUSPECT,
    EpisodeInfo,
    EvaluationReport,
    PartialReason,
    build_report,
)
from transpodcast.evaluation_html import (
    render_index_html,
    render_report_html,
    report_payload,
)
from transpodcast.service import deserialize_analysis, deserialize_translation

_AUDIO_FILENAME = "publication.m4a"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument(
        "--run",
        metavar="RUN_ID_OR_GUID",
        help="Processing Run id or episode publication guid",
    )
    selection.add_argument(
        "--episode",
        metavar="RUN_ID_OR_GUID",
        help="Same as --run; accepts either identifier",
    )
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--verdicts", type=Path)
    parser.add_argument(
        "--short-segment-words",
        type=int,
        default=DEFAULT_SHORT_SEGMENT_WORDS,
    )
    parser.add_argument(
        "--short-segment-seconds",
        type=float,
        default=DEFAULT_SHORT_SEGMENT_SECONDS,
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Skip copying Publication Audio next to the report",
    )
    parser.add_argument(
        "--audio-file",
        type=Path,
        help="Use a local audio file instead of reading object storage",
    )
    return parser.parse_args()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"System of record is unavailable: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> list[sqlite3.Row]:
    return list(connection.execute(query, parameters))


def _episode_rows(
    connection: sqlite3.Connection,
    selector: str | None,
) -> list[sqlite3.Row]:
    """Select every episode, or the one a Processing Run id or guid names."""
    query = """
        SELECT
            episodes.publication_guid AS guid,
            episodes.title AS title,
            episodes.source_show AS source_show,
            episodes.source_title AS source_title,
            episodes.duration_seconds AS duration_seconds,
            episodes.preview_key AS preview_key,
            episodes.publication_key AS publication_key,
            runs.id AS run_id,
            runs.analysis_json AS analysis_json,
            runs.translation_json AS translation_json,
            runs.content_edit_json AS content_edit_json,
            runs.render_json AS render_json
        FROM episodes
        JOIN processing_runs AS runs
          ON runs.id = episodes.processing_run_id
        WHERE episodes.deleted_at IS NULL
    """
    if selector is None:
        return _rows(connection, query + " ORDER BY episodes.id")
    if selector.isdigit():
        return _rows(connection, query + " AND runs.id = ?", (int(selector),))
    return _rows(
        connection,
        query + " AND episodes.publication_guid = ?",
        (selector,),
    )


def _registry_names(connection: sqlite3.Connection) -> dict[int, str]:
    """Speaker display names, with the Channel Speaker Profile winning."""
    names: dict[int, str] = {}
    for row in _rows(
        connection,
        "SELECT id, display_name FROM speakers WHERE merged_into_id IS NULL",
    ):
        names[int(row["id"])] = str(row["display_name"])
    for row in _rows(
        connection,
        """
        SELECT speaker_id, display_name
        FROM channel_speaker_profiles
        WHERE state = 'active'
        """,
    ):
        names[int(row["speaker_id"])] = str(row["display_name"])
    return names


def _label_names(
    analysis: Any,
    registry_names: dict[int, str],
) -> dict[str, str]:
    return {
        speaker.local_label: registry_names[speaker.registry_speaker_id]
        for speaker in analysis.speakers
        if speaker.registry_speaker_id is not None
        and speaker.registry_speaker_id in registry_names
    }


def _recorded_verdicts(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise SystemExit(f"Verdict export is malformed: {path}")
    return parsed


def _publication_audio(
    settings: TransPodcastSettings,
    publication_key: str | None,
    preview_key: str,
) -> bytes | None:
    """Read published audio, falling back to the Private Preview.

    Storage is constructed directly so no writable Database connection
    is ever opened by this tool.
    """
    from transpodcast.adapters.r2 import R2PublicationStorage
    from transpodcast.storage import LocalPublicationStorage

    storage: Any = (
        R2PublicationStorage(
            endpoint_url=settings.r2_endpoint_url,
            bucket=settings.r2_bucket,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
        )
        if settings.storage_mode == "r2"
        else LocalPublicationStorage(settings.local_storage_root)
    )
    if publication_key:
        published = storage.read_publication(publication_key)
        if published is not None:
            return bytes(published)
    preview = storage.read_preview(preview_key)
    return bytes(preview) if preview is not None else None


def _report_for(
    row: sqlite3.Row,
    *,
    registry_names: dict[int, str],
    verdicts: dict[str, object] | None,
    generated_at: str,
    short_words: int,
    short_seconds: float,
) -> EvaluationReport:
    analysis_json = row["analysis_json"]
    translation_json = row["translation_json"]
    if analysis_json is None or translation_json is None:
        raise SystemExit(
            f"Processing Run {row['run_id']} kept no analysis or translation "
            "checkpoint, so there is no evidence to review"
        )
    analysis = deserialize_analysis(str(analysis_json))
    translation = deserialize_translation(
        str(translation_json),
        (
            str(row["content_edit_json"])
            if row["content_edit_json"] is not None
            else None
        ),
    )
    timings: tuple[tuple[float, float], ...] = ()
    if row["render_json"] is not None:
        parsed = json.loads(str(row["render_json"]))
        timings = tuple(
            (float(item[0]), float(item[1]))
            for item in parsed.get("segment_timings", [])
        )
    return build_report(
        analysis=analysis,
        translation=translation,
        segment_timings=timings,
        episode=EpisodeInfo(
            guid=str(row["guid"]),
            title=str(row["title"]),
            source_show=str(row["source_show"]),
            source_title=str(row["source_title"]),
            run_id=int(row["run_id"]),
            generated_at=generated_at,
            publication_duration_seconds=int(row["duration_seconds"]),
        ),
        speaker_names=_label_names(analysis, registry_names),
        verdicts=verdicts,
        short_segment_words=short_words,
        short_segment_seconds=short_seconds,
    )


def _audio_for(
    arguments: argparse.Namespace,
    settings: TransPodcastSettings,
    row: sqlite3.Row,
    directory: Path,
) -> str | None:
    if arguments.no_audio:
        return None
    if arguments.audio_file is not None:
        directory.joinpath(arguments.audio_file.name).write_bytes(
            arguments.audio_file.read_bytes()
        )
        return arguments.audio_file.name
    content = _publication_audio(
        settings,
        row["publication_key"],
        str(row["preview_key"]),
    )
    if content is None:
        return None
    directory.joinpath(_AUDIO_FILENAME).write_bytes(content)
    return _AUDIO_FILENAME


def main() -> None:
    arguments = _arguments()
    settings = TransPodcastSettings()
    connection = _read_only_connection(settings.database_path)
    try:
        selector = arguments.run or arguments.episode
        rows = _episode_rows(connection, selector)
        registry_names = _registry_names(connection)
    finally:
        connection.close()
    if not rows:
        raise SystemExit(
            f"No Translated Episode matched {selector!r}. Run --all to list "
            "the available Processing Run ids and episode guids."
            if selector
            else "No Translated Episode is available to review"
        )
    verdicts = _recorded_verdicts(arguments.verdicts)
    generated_at = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    arguments.output.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[EvaluationReport, str]] = []
    for row in rows:
        report = _report_for(
            row,
            registry_names=registry_names,
            verdicts=verdicts,
            generated_at=generated_at,
            short_words=arguments.short_segment_words,
            short_seconds=arguments.short_segment_seconds,
        )
        directory = arguments.output / report.episode.guid
        directory.mkdir(parents=True, exist_ok=True)
        audio_filename = _audio_for(arguments, settings, row, directory)
        directory.joinpath("report.html").write_text(
            render_report_html(report, audio_filename=audio_filename),
            encoding="utf-8",
        )
        directory.joinpath("report.json").write_text(
            json.dumps(report_payload(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        entries.append((report, f"{report.episode.guid}/report.html"))
        counts = report.summary.finding_counts
        withheld = (
            " · midsplit withheld (no sentence punctuation)"
            if PartialReason.SENTENCE_PUNCTUATION in report.partial_reasons
            else ""
        )
        print(
            f"{report.episode.guid} (run {report.episode.run_id}): "
            f"{report.summary.segment_count} segments · "
            f"interleave {counts.get(str(INTERLEAVE_SUSPECT), 0)} "
            f"(high {report.summary.high_severity_count}) · "
            f"midsplit {counts.get(str(MIDSPLIT_SUSPECT), 0)} · "
            f"crosstalk {report.summary.crosstalk_count}{withheld}"
        )
    arguments.output.joinpath("index.html").write_text(
        render_index_html(entries),
        encoding="utf-8",
    )
    print(f"index: {arguments.output / 'index.html'}")


if __name__ == "__main__":
    main()
