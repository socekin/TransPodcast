"""Compare V14 and the V15 candidate without mutating Processing Runs.

This read-only structural replay preserves the recorded Speaker ownership,
exercises V15 natural segmentation and batching, and invokes no live provider
or TTS. It is a maintenance script because no new Processing Run uses V15.

Usage:
  uv run python scripts/speech_pipeline_v15_comparison.py --run 101
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from transpodcast.adjudication_completion import complete_boundary_adjudication
from transpodcast.config import TransPodcastSettings
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V15
from transpodcast.service import deserialize_analysis
from transpodcast.source_segmentation import join_words
from transpodcast.speaker_boundaries import recover_sentence_punctuation
from transpodcast.speaker_ownership_v15 import (
    SpeakerOwnershipPlan,
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
)


class KeepOwnershipAdjudicator:
    """Preserve ownership while exercising V15 segmentation and batching."""

    def plan_speaker_ownership(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult:
        return _keep_result(regions, "Structural comparison Planner keep.")

    def review_speaker_ownership(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult:
        return _keep_result(regions, "Structural comparison Reviewer keep.")


def build_v15_structural_comparison(
    analysis: SourceAnalysisResult,
    *,
    include_segments: bool = False,
) -> dict[str, Any]:
    candidate = replace(
        analysis,
        pipeline_version=SPEECH_PIPELINE_V15,
        boundary_adjudication_json="{}",
    )
    completion = complete_boundary_adjudication(
        candidate,
        KeepOwnershipAdjudicator(),
    )
    before_words = _word_sequence(analysis.segments)
    after_words = _word_sequence(completion.analysis.segments)
    before_owned_words = _owned_word_sequence(analysis.segments)
    after_owned_words = _owned_word_sequence(completion.analysis.segments)
    ownership_summary = json.loads(
        completion.analysis.boundary_adjudication_json
    ).get("speaker_ownership_v15") or {}
    batch_records = ownership_summary.get("batches") or []
    payload: dict[str, Any] = {
        "pipeline_version": SPEECH_PIPELINE_V15,
        "before": _segment_metrics(
            analysis.segments,
            full_asr_text=analysis.full_asr_text,
        ),
        "after": _segment_metrics(
            completion.analysis.segments,
            full_asr_text=analysis.full_asr_text,
        ),
        "ownership": {
            "region_count": completion.ownership_region_count,
            "changed_word_count": completion.ownership_changed_word_count,
            "disputed_region_count": (
                completion.ownership_disputed_region_count
            ),
            "batch_count": int(ownership_summary.get("batch_count", 0)),
            "maximum_request_characters": max(
                (
                    int(item.get("request_characters", 0))
                    for item in batch_records
                ),
                default=0,
            ),
            "batch_sizes": [
                len(item.get("region_ids") or []) for item in batch_records
            ],
            "provider_degraded_batch_count": sum(
                bool(item.get("planner_error") or item.get("reviewer_error"))
                for item in batch_records
            ),
        },
        "invariants": {
            "word_count_preserved": len(before_words) == len(after_words),
            "word_sequence_preserved": before_words == after_words,
            "timestamps_preserved": all(
                before[1:] == after[1:]
                for before, after in zip(
                    before_words,
                    after_words,
                    strict=True,
                )
            ),
            "supported_speaker_labels": {
                segment.speaker_label for segment in completion.analysis.segments
            }.issubset({segment.speaker_label for segment in analysis.segments}),
            "one_speaker_per_segment": (
                len(before_owned_words) == len(after_owned_words)
                and all(
                    before_word[:3] == after_word[:3]
                    and before_word[3] == after_word[3]
                    for before_word, after_word in zip(
                        before_owned_words,
                        after_owned_words,
                        strict=True,
                    )
                )
            ),
            "monotonic_segment_time": all(
                left.end_seconds <= right.start_seconds + 1e-6
                for left, right in zip(
                    completion.analysis.segments,
                    completion.analysis.segments[1:],
                    strict=False,
                )
            ),
            "deterministic_segment_ids": [
                segment.segment_id for segment in completion.analysis.segments
            ]
            == [
                f"seg-{index:04d}"
                for index in range(1, len(completion.analysis.segments) + 1)
            ],
        },
    }
    if include_segments:
        payload["before_segments"] = _public_segments(analysis.segments)
        payload["after_segments"] = _public_segments(
            completion.analysis.segments
        )
    return payload


def slice_analysis(
    analysis: SourceAnalysisResult,
    *,
    start_seconds: float,
    end_seconds: float,
) -> SourceAnalysisResult:
    """Return a word-conserving analysis window for bounded live comparison."""
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("Comparison window is invalid")
    punctuation_words: list[dict[str, Any]] = [
        {
            "text": text,
            "start_seconds": start,
            "end_seconds": end,
            "sentence_end": False,
            "sentence_suffix": "",
        }
        for text, start, end in _word_sequence(analysis.segments)
    ]
    recover_sentence_punctuation(
        punctuation_words,
        analysis.full_asr_text,
    )
    suffixes = {
        (
            str(word["text"]),
            float(word["start_seconds"]),
            float(word["end_seconds"]),
        ): str(word.get("sentence_suffix", ""))
        for word in punctuation_words
    }
    segments: list[CanonicalSegment] = []
    for segment in analysis.segments:
        words = tuple(
            word
            for word in segment.words
            if word[2] > start_seconds and word[1] < end_seconds
        )
        if not words:
            continue
        segments.append(
            replace(
                segment,
                start_seconds=words[0][1],
                end_seconds=words[-1][2],
                words=words,
                text=_punctuated_window_text(words, suffixes),
            )
        )
    if not segments:
        raise ValueError("Comparison window contains no transcript words")
    window_words = tuple(word for segment in segments for word in segment.words)
    return replace(
        analysis,
        segments=tuple(segments),
        pipeline_version=SPEECH_PIPELINE_V15,
        full_asr_text=_punctuated_window_text(window_words, suffixes),
        aligned_words=window_words,
        raw_diarization_turns=tuple(
            turn
            for turn in analysis.raw_diarization_turns
            if turn[2] > start_seconds and turn[1] < end_seconds
        ),
        boundary_adjudication_json="{}",
    )


def _keep_result(
    regions: Sequence[SpeakerOwnershipRegion],
    rationale: str,
) -> SpeakerOwnershipPlanningResult:
    return SpeakerOwnershipPlanningResult(
        plans={
            region.region_id: SpeakerOwnershipPlan(
                action="keep",
                rationale=rationale,
            )
            for region in regions
        }
    )


def _segment_metrics(
    segments: Sequence[CanonicalSegment],
    *,
    full_asr_text: str,
) -> dict[str, Any]:
    durations = [
        segment.end_seconds - segment.start_seconds for segment in segments
    ]
    boundary_counts, punctuation_alignment = _boundary_counts(
        segments,
        full_asr_text,
    )
    return {
        "segment_count": len(segments),
        "speaker_count": len({segment.speaker_label for segment in segments}),
        "median_seconds": round(statistics.median(durations), 3)
        if durations
        else 0.0,
        "p10_seconds": round(_quantile(durations, 0.10), 3),
        "p90_seconds": round(_quantile(durations, 0.90), 3),
        "near_fifteen_seconds": sum(duration >= 14.5 for duration in durations),
        "below_two_seconds": sum(duration <= 2.0 for duration in durations),
        "above_eighteen_seconds": sum(duration > 18.0 for duration in durations),
        "boundary_counts": boundary_counts,
        "punctuation_alignment": punctuation_alignment,
    }


def _boundary_counts(
    segments: Sequence[CanonicalSegment],
    full_asr_text: str,
) -> tuple[dict[str, int], dict[str, Any]]:
    words = [
        {
            "text": text,
            "start_seconds": start,
            "end_seconds": end,
            "sentence_end": False,
            "sentence_suffix": "",
        }
        for text, start, end in _word_sequence(segments)
    ]
    punctuation_alignment = recover_sentence_punctuation(words, full_asr_text)
    counts = {"speaker": 0, "sentence": 0, "pause": 0, "fallback": 0}
    offset = 0
    for current, following in zip(segments, segments[1:], strict=False):
        offset += len(current.words)
        if current.speaker_label != following.speaker_label:
            counts["speaker"] += 1
        elif offset and bool(words[offset - 1].get("sentence_end", False)):
            counts["sentence"] += 1
        elif (
            following.start_seconds - current.end_seconds
            >= 0.30
        ):
            counts["pause"] += 1
        else:
            counts["fallback"] += 1
    return counts, punctuation_alignment


def _quantile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _punctuated_window_text(
    words: Sequence[tuple[str, float, float]],
    suffixes: dict[tuple[str, float, float], str],
) -> str:
    return join_words(
        [
            {
                "text": f"{text}{suffixes.get((text, start, end), '')}",
                "start_seconds": start,
                "end_seconds": end,
            }
            for text, start, end in words
        ]
    )


def _word_sequence(
    segments: Sequence[CanonicalSegment],
) -> tuple[tuple[str, float, float], ...]:
    return tuple(word for segment in segments for word in segment.words)


def _owned_word_sequence(
    segments: Sequence[CanonicalSegment],
) -> tuple[tuple[str, float, float, str], ...]:
    return tuple(
        (text, start, end, segment.speaker_label)
        for segment in segments
        for text, start, end in segment.words
    )


def _public_segments(
    segments: Sequence[CanonicalSegment],
) -> list[dict[str, Any]]:
    return [
        {
            "segment_id": segment.segment_id,
            "speaker_label": segment.speaker_label,
            "start_seconds": segment.start_seconds,
            "end_seconds": segment.end_seconds,
            "text": segment.text,
            "review_state": segment.review_state,
        }
        for segment in segments
    ]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", type=int, dest="run_ids")
    parser.add_argument(
        "--window",
        action="append",
        default=[],
        metavar="RUN:START:END",
        help="Limit one run to a source-time window in seconds",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/speech-pipeline-v15-comparison"),
    )
    return parser.parse_args()


def _windows(values: list[str]) -> dict[int, tuple[float, float]]:
    windows: dict[int, tuple[float, float]] = {}
    for value in values:
        try:
            run, start, end = value.split(":", maxsplit=2)
            windows[int(run)] = (float(start), float(end))
        except ValueError as error:
            raise SystemExit(f"Invalid comparison window: {value}") from error
    return windows


def _read_runs(
    path: Path,
    run_ids: list[int],
) -> dict[int, tuple[str, str]]:
    if not path.is_file():
        raise SystemExit(f"System of record is unavailable: {path}")
    placeholders = ", ".join("?" for _ in run_ids)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            f"""
            SELECT runs.id, videos.title, runs.analysis_json
            FROM processing_runs AS runs
            JOIN candidate_videos AS videos
              ON videos.id = runs.candidate_video_id
            WHERE runs.id IN ({placeholders})
            ORDER BY runs.id
            """,
            tuple(run_ids),
        )
        return {
            int(run_id): (str(title), str(analysis_json))
            for run_id, title, analysis_json in rows
            if analysis_json is not None
        }


def _summary(items: list[dict[str, Any]]) -> str:
    lines = [
        "# Speech Pipeline V15 Structural Comparison",
        "",
        "This is a read-only transcript-structure replay. It preserves V14 "
        "Speaker ownership and invokes no live provider or TTS.",
        "",
        "| Run | Speakers | Segments | Median | Fallback cuts | Ownership regions |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in items:
        before = item["comparison"]["before"]
        after = item["comparison"]["after"]
        lines.append(
            f"| {item['run_id']} | {before['speaker_count']} | "
            f"{before['segment_count']} → {after['segment_count']} | "
            f"{before['median_seconds']:.2f}s → "
            f"{after['median_seconds']:.2f}s | "
            f"{before['boundary_counts']['fallback']} → "
            f"{after['boundary_counts']['fallback']} | "
            f"{item['comparison']['ownership']['region_count']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    arguments = _arguments()
    run_ids = list(dict.fromkeys(arguments.run_ids or []))
    if not run_ids:
        raise SystemExit("Provide at least one Processing Run with --run")
    windows = _windows(arguments.window)
    database_path = TransPodcastSettings().database_path
    database_hash_before = _file_sha256(database_path)
    rows = _read_runs(database_path, run_ids)
    missing = [run_id for run_id in run_ids if run_id not in rows]
    if missing:
        raise SystemExit(
            "Processing Runs are unavailable: "
            + ", ".join(str(run_id) for run_id in missing)
        )
    arguments.output.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for run_id in run_ids:
        title, raw_analysis = rows[run_id]
        analysis = deserialize_analysis(raw_analysis)
        window = windows.get(run_id)
        if window is not None:
            analysis = slice_analysis(
                analysis,
                start_seconds=window[0],
                end_seconds=window[1],
            )
        item = {
            "run_id": run_id,
            "title": title,
            "window": list(window) if window else None,
            "comparison": build_v15_structural_comparison(
                analysis,
                include_segments=window is not None,
            ),
        }
        invariants = item["comparison"]["invariants"]
        if not all(invariants.values()):
            raise SystemExit(f"Run {run_id} failed transcript conservation")
        before = item["comparison"]["before"]
        after = item["comparison"]["after"]
        if after["segment_count"] > before["segment_count"] * 1.1:
            raise SystemExit(f"Run {run_id} failed the segment-count guardrail")
        if (
            before["boundary_counts"]["fallback"]
            and after["boundary_counts"]["fallback"]
            > before["boundary_counts"]["fallback"] * 0.1
        ):
            raise SystemExit(f"Run {run_id} failed the fallback-cut guardrail")
        arguments.output.joinpath(f"run-{run_id}.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        items.append(item)
    combined_before = sum(
        int(item["comparison"]["before"]["segment_count"])
        for item in items
    )
    combined_after = sum(
        int(item["comparison"]["after"]["segment_count"])
        for item in items
    )
    if not combined_before * 0.9 <= combined_after <= combined_before * 1.1:
        raise SystemExit("Combined runs failed the segment-count guardrail")
    database_hash_after = _file_sha256(database_path)
    if database_hash_before != database_hash_after:
        raise SystemExit("System-of-record database changed during replay")
    arguments.output.joinpath("verification.json").write_text(
        json.dumps(
            {
                "database_sha256_before": database_hash_before,
                "database_sha256_after": database_hash_after,
                "database_unchanged": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    arguments.output.joinpath("summary.md").write_text(
        _summary(items),
        encoding="utf-8",
    )
    print(arguments.output / "summary.md")


if __name__ == "__main__":
    main()
