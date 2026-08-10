"""Read-only comparison of Speaker Boundary Stabilization checkpoints."""

from __future__ import annotations

from collections import Counter
from typing import Any

from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.evaluation import (
    DEFAULT_SHORT_SEGMENT_SECONDS,
    DEFAULT_SHORT_SEGMENT_WORDS,
)
from transpodcast.speaker_boundaries import (
    detect_cross_speaker_repetitions,
    detect_short_speaker_participation,
    detect_speaker_identity_drift,
    stabilize_existing_segments,
    stabilize_speaker_boundaries,
)

_KNOWN_EXAMPLES_BY_RUN: dict[int, tuple[dict[str, Any], ...]] = {
    6: (
        {
            "name": "run-6-what-do-you-want",
            "phrase_segment_ids": ("seg-0002", "seg-0003"),
            "target_segment_id": "seg-0002",
            "target_words": ("what", "do"),
            "phrases": (("what", "do", "you", "want"),),
        },
    ),
    7: (
        {
            "name": "run-7-slow-down-a-bit",
            "phrase_segment_ids": ("seg-0127", "seg-0128"),
            "target_segment_id": "seg-0128",
            "target_words": ("bit",),
            "phrases": (
                ("can", "you", "slow", "down", "a", "bit"),
                ("can", "you", "slow", "down", "a", "little", "bit"),
            ),
        },
        {
            "name": "run-7-so-like",
            "phrase_segment_ids": ("seg-0290",),
            "target_segment_id": "seg-0290",
            "target_words": ("so", "like"),
            "phrases": (("so", "like"),),
        },
    ),
}


def build_boundary_comparison(
    analysis: SourceAnalysisResult,
    *,
    segment_timings: tuple[tuple[float, float], ...] = (),
    run_id: int,
    publication_guid: str,
    title: str,
    audition_limit: int = 12,
) -> dict[str, Any]:
    """Build deterministic comparison evidence without provider access."""

    source_segments = [_segment_value(segment) for segment in analysis.segments]
    complete_evidence = bool(
        analysis.raw_diarization_turns
        and analysis.full_asr_text
        and analysis.aligned_words
    )
    if complete_evidence:
        replay = stabilize_speaker_boundaries(
            [
                {
                    "text": text,
                    "start_seconds": start,
                    "end_seconds": end,
                }
                for text, start, end in analysis.aligned_words
            ],
            [
                {
                    "speaker": speaker,
                    "start_seconds": start,
                    "end_seconds": end,
                }
                for speaker, start, end in analysis.raw_diarization_turns
            ],
            transcript_text=analysis.full_asr_text,
        )
    else:
        replay = stabilize_existing_segments(source_segments)

    before_words = _flatten_source_words(analysis.segments)
    after_words = [
        (
            str(word["text"]),
            float(word["start_seconds"]),
            float(word["end_seconds"]),
        )
        for word in replay["words"]
    ]
    decisions = list(replay["decisions"])
    unresolved = [
        decision
        for decision in decisions
        if decision["outcome"] == "unresolved"
    ]
    corrections = [
        decision
        for decision in decisions
        if decision["outcome"] == "corrected"
    ]
    distribution = Counter(
        (
            str(decision["original_speaker"]),
            str(decision["resolved_speaker"]),
        )
        for decision in corrections
    )
    unresolved_distribution = Counter(
        (
            str(decision["original_speaker"]),
            str(decision["neighbour_speaker"]),
        )
        for decision in unresolved
    )
    known_examples = _known_example_results(
        run_id,
        source_segments,
        corrections,
    )
    audition_candidates = [
        _audition_entry(
            decision,
            source_segments,
            segment_timings,
            publication_guid=publication_guid,
        )
        for decision in sorted(
            unresolved,
            key=_audition_priority,
        )
    ]
    audition_candidates = [
        *[
            _known_example_audition(
                example,
                source_segments,
                segment_timings,
                publication_guid=publication_guid,
            )
            for example in known_examples
            if example["status"] == "unchanged"
        ],
        *audition_candidates,
    ]
    audition = _deduplicate_auditions(audition_candidates)[
        : max(0, audition_limit)
    ]
    return {
        "run_id": run_id,
        "publication_guid": publication_guid,
        "title": title,
        "pipeline_version": analysis.pipeline_version,
        "policy_version": replay["summary"]["policy_version"],
        "evidence": {
            "partial": not complete_evidence,
            "missing": (
                []
                if complete_evidence
                else ["raw_diarization_turns", "full_asr_text"]
            ),
        },
        "before": _sequence_summary(source_segments),
        "after": _sequence_summary(list(replay["segments"])),
        "stabilization": replay["summary"],
        "invariants": {
            "word_coverage_preserved": before_words == after_words,
            "word_count_before": len(before_words),
            "word_count_after": len(after_words),
        },
        "decisions": decisions,
        "corrections_by_speaker": [
            {
                "original_speaker": original,
                "resolved_speaker": resolved,
                "count": count,
            }
            for (original, resolved), count in sorted(distribution.items())
        ],
        "unresolved_by_speaker": [
            {
                "subject_speaker": subject,
                "neighbour_speaker": neighbour,
                "count": count,
            }
            for (subject, neighbour), count
            in sorted(unresolved_distribution.items())
        ],
        "known_examples": known_examples,
        "cross_speaker_repetitions": detect_cross_speaker_repetitions(
            source_segments
        ),
        "identity_drift": detect_speaker_identity_drift(
            {
                speaker.local_label: list(speaker.embedding)
                for speaker in analysis.speakers
                if speaker.embedding
            }
        ),
        "short_speaker_participation": (
            detect_short_speaker_participation(source_segments)
        ),
        "audition": audition,
    }


def _segment_value(segment: CanonicalSegment) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "speaker_label": segment.speaker_label,
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
        "text": segment.text,
        "words": list(segment.words),
        "overlap": segment.overlap,
    }


def _flatten_source_words(
    segments: tuple[CanonicalSegment, ...],
) -> list[tuple[str, float, float]]:
    flattened: list[tuple[str, float, float]] = []
    for segment in segments:
        if segment.words:
            flattened.extend(segment.words)
        else:
            flattened.append(
                (
                    segment.text,
                    segment.start_seconds,
                    segment.end_seconds,
                )
            )
    return flattened


def _sequence_summary(segments: list[dict[str, Any]]) -> dict[str, int]:
    transitions = sum(
        str(before["speaker_label"]) != str(after["speaker_label"])
        for before, after in zip(segments, segments[1:], strict=False)
    )
    interleaves = _short_interleave_count(segments)
    return {
        "segment_count": len(segments),
        "speaker_transitions": transitions,
        "a_b_a_interleaves": interleaves,
    }


def _short_interleave_count(segments: list[dict[str, Any]]) -> int:
    count = 0
    for index in range(1, len(segments) - 1):
        before, middle, after = segments[index - 1 : index + 2]
        if (
            str(before["speaker_label"])
            != str(after["speaker_label"])
            or str(middle["speaker_label"])
            == str(before["speaker_label"])
        ):
            continue
        words = list(middle.get("words", []))
        word_count = len(words) if words else len(
            str(middle.get("text", "")).split()
        )
        duration = (
            float(middle["end_seconds"])
            - float(middle["start_seconds"])
        )
        if (
            word_count > DEFAULT_SHORT_SEGMENT_WORDS
            and duration > DEFAULT_SHORT_SEGMENT_SECONDS
        ):
            continue
        count += 1
    return count


def _known_example_results(
    run_id: int,
    source_segments: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        _known_example_result(
            example,
            source_segments,
            corrections,
        )
        for example in _KNOWN_EXAMPLES_BY_RUN.get(run_id, ())
    ]


def _known_example_result(
    example: dict[str, Any],
    source_segments: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    target_segment_id = str(example["target_segment_id"])
    phrase_segment_ids = {
        str(value) for value in example["phrase_segment_ids"]
    }
    anchored_segments = [
        segment
        for segment in source_segments
        if str(segment["segment_id"]) in phrase_segment_ids
    ]
    if {
        str(segment["segment_id"]) for segment in anchored_segments
    } != phrase_segment_ids:
        return {
            "name": str(example["name"]),
            "status": "absent",
        }
    anchored_words = [
        word
        for segment in anchored_segments
        for word in _segment_words(segment)
    ]
    lexical = [_normalize_word(word[0]) for word in anchored_words]
    matched = next(
        (
            (match, phrase)
            for phrase in example["phrases"]
            if (match := _find_phrase(lexical, phrase)) is not None
        ),
        None,
    )
    anchored_correction = next(
        (
            decision
            for decision in corrections
            if target_segment_id
            in {
                str(value)
                for value in decision.get("source_segment_ids", [])
            }
            and str(decision["resolved_speaker"])
            == str(decision["neighbour_speaker"])
            != str(decision["original_speaker"])
            and tuple(
                _normalize_word(word)
                for word in str(decision["text"]).split()
            )
            == tuple(example["target_words"])
        ),
        None,
    )
    status = (
        "absent"
        if matched is None
        else ("corrected" if anchored_correction is not None else "unchanged")
    )
    result: dict[str, Any] = {
        "name": str(example["name"]),
        "status": status,
        "segment_id": target_segment_id,
    }
    if matched is not None:
        result["source_seconds"] = float(anchored_words[matched[0]][1])
    return result


def _segment_words(
    segment: dict[str, Any],
) -> list[tuple[str, float, float]]:
    values: list[tuple[str, float, float]] = []
    for word in segment.get("words", []):
        if isinstance(word, dict):
            values.append(
                (
                    str(word["text"]),
                    float(word["start_seconds"]),
                    float(word["end_seconds"]),
                )
            )
        else:
            values.append(
                (str(word[0]), float(word[1]), float(word[2]))
            )
    if values:
        return values
    return [
        (
            str(segment.get("text", "")),
            float(segment["start_seconds"]),
            float(segment["end_seconds"]),
        )
    ]


def _find_phrase(words: list[str], phrase: tuple[str, ...]) -> int | None:
    for index in range(0, len(words) - len(phrase) + 1):
        if tuple(words[index : index + len(phrase)]) == phrase:
            return index
    return None


def _normalize_word(value: str) -> str:
    return value.casefold().strip(".,!?;:\"'()[]{}")


def _audition_priority(decision: dict[str, Any]) -> tuple[int, float, int]:
    evidence = dict(decision["evidence"])
    return (
        0 if bool(evidence["cross_speaker_overlap"]) else 1,
        float(evidence["overlap_margin_seconds"]),
        int(decision["word_start_index"]),
    )


def _audition_entry(
    decision: dict[str, Any],
    source_segments: list[dict[str, Any]],
    segment_timings: tuple[tuple[float, float], ...],
    *,
    publication_guid: str,
) -> dict[str, Any]:
    segment_id = _decision_segment_id(decision, source_segments)
    publication_seconds: float | None = None
    ordinal = _segment_ordinal(segment_id)
    if ordinal is not None and ordinal <= len(segment_timings):
        publication_seconds = float(segment_timings[ordinal - 1][0])
    return {
        "reason": "unresolved_boundary",
        "segment_id": segment_id,
        "source_seconds": float(decision["start_seconds"]),
        "publication_seconds": publication_seconds,
        "text": str(decision["text"]),
        "original_speaker": str(decision["original_speaker"]),
        "neighbour_speaker": str(decision["neighbour_speaker"]),
        "report_path": f"../{publication_guid}/report.html",
    }


def _known_example_audition(
    example: dict[str, Any],
    source_segments: list[dict[str, Any]],
    segment_timings: tuple[tuple[float, float], ...],
    *,
    publication_guid: str,
) -> dict[str, Any]:
    source_seconds = float(example["source_seconds"])
    segment_id = ""
    text = ""
    for segment in source_segments:
        if (
            float(segment["start_seconds"])
            <= source_seconds
            <= float(segment["end_seconds"])
        ):
            segment_id = str(segment["segment_id"])
            text = str(segment["text"])
            break
    publication_seconds: float | None = None
    ordinal = _segment_ordinal(segment_id)
    if ordinal is not None and ordinal <= len(segment_timings):
        publication_seconds = float(segment_timings[ordinal - 1][0])
    return {
        "reason": str(example["name"]),
        "segment_id": segment_id,
        "source_seconds": source_seconds,
        "publication_seconds": publication_seconds,
        "text": text,
        "original_speaker": "",
        "neighbour_speaker": "",
        "report_path": f"../{publication_guid}/report.html",
    }


def _deduplicate_auditions(
    auditions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    for audition in auditions:
        key = (
            str(audition["segment_id"]),
            float(audition["source_seconds"]),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(audition)
    return result


def _decision_segment_id(
    decision: dict[str, Any],
    source_segments: list[dict[str, Any]],
) -> str:
    source_ids = list(decision.get("source_segment_ids", []))
    if source_ids:
        return str(source_ids[0])
    start = float(decision["start_seconds"])
    for segment in source_segments:
        if (
            float(segment["start_seconds"])
            <= start
            <= float(segment["end_seconds"])
        ):
            return str(segment["segment_id"])
    return ""


def _segment_ordinal(segment_id: str) -> int | None:
    try:
        return int(segment_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None
