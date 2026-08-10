from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from transpodcast.similarity import cosine_similarity

_SENTENCE_SUFFIX = re.compile(r"([.!?。！？]+[\"')\]]*)$")
_TRAILING_PUNCTUATION = re.compile(r"[^\w'’]+$", re.UNICODE)
_LEADING_PUNCTUATION = re.compile(r"^[^\w'’]+", re.UNICODE)
_ACKNOWLEDGEMENTS = frozenset(
    {
        "absolutely",
        "exactly",
        "go on",
        "mm",
        "no",
        "ok",
        "okay",
        "really",
        "right",
        "sure",
        "thank you",
        "thanks",
        "totally",
        "uh huh",
        "wow",
        "yeah",
        "yep",
        "yes",
    }
)
_CROSSTALK_CUES = frozenset(
    {
        "but",
        "hold on",
        "sorry",
        "wait",
    }
)
_CONTINUATION_FRAGMENTS = frozenset(
    {
        "like",
        "so like",
    }
)
_BACKCHANNEL_BLEED_CONNECTORS = frozenset(
    {
        "and",
        "because",
        "but",
        "so",
        "then",
    }
)


@dataclass(frozen=True)
class SpeakerBoundaryPolicy:
    version: str = "speaker-boundary-stabilization-v2"
    maximum_segment_seconds: float = 15.0
    maximum_island_words: int = 2
    maximum_island_seconds: float = 0.75
    maximum_overlap_margin_seconds: float = 0.12
    maximum_boundary_fragment_words: int = 6
    maximum_boundary_fragment_seconds: float = 2.5
    maximum_backchannel_turn_seconds: float = 0.6
    maximum_punctuation_gap_tokens: int = 8
    minimum_punctuation_reanchor_words: int = 3
    tolerant_punctuation_alignment: bool = True
    backchannel_bleed_enabled: bool = True
    single_word_continuation_enabled: bool = True


DEFAULT_SPEAKER_BOUNDARY_POLICY = SpeakerBoundaryPolicy()
LEGACY_SPEAKER_BOUNDARY_POLICY = SpeakerBoundaryPolicy(
    version="speaker-boundary-stabilization-v1",
    tolerant_punctuation_alignment=False,
    backchannel_bleed_enabled=False,
    single_word_continuation_enabled=False,
)


def stabilize_speaker_boundaries(
    words: list[dict[str, Any]],
    diarization: list[dict[str, Any]],
    *,
    transcript_text: str = "",
    policy: SpeakerBoundaryPolicy = DEFAULT_SPEAKER_BOUNDARY_POLICY,
) -> dict[str, Any]:
    """Return a deterministic, auditable Canonical Transcript.

    This function is intentionally pure. It changes only episode-local
    speaker attribution and segment grouping; source words and timestamps
    remain unchanged.
    """

    assigned = _assign_words(words, diarization)
    resolve_unassigned_speakers(assigned)
    punctuation_alignment = _recover_sentence_punctuation(
        assigned,
        transcript_text,
        policy=policy,
    )
    punctuation_recovered = (
        int(punctuation_alignment["confident_matched_word_count"]) > 0
    )
    decisions = (
        _repair_backchannel_bleed(
            assigned,
            diarization,
            policy=policy,
        )
        if policy.backchannel_bleed_enabled
        else []
    )
    decisions.extend(_stabilize_assigned_words(
        assigned,
        policy=policy,
        partial_evidence=False,
        punctuation_available=punctuation_recovered,
    ))
    segments = _segments_from_assigned_words(
        assigned,
        maximum_seconds=policy.maximum_segment_seconds,
    )
    return _result(
        assigned,
        segments,
        decisions,
        policy=policy,
        raw_turn_count=len(diarization),
        punctuation_recovered=punctuation_recovered,
        punctuation_alignment=punctuation_alignment,
        partial_evidence=False,
    )


def stabilize_existing_segments(
    segments: list[dict[str, Any]],
    *,
    policy: SpeakerBoundaryPolicy = DEFAULT_SPEAKER_BOUNDARY_POLICY,
) -> dict[str, Any]:
    """Replay old Canonical Transcript checkpoints without mutating them.

    Pipeline v6 checkpoints do not include raw Sortformer turns or complete
    ASR text. The result is therefore explicitly partial and only corrects a
    one-word A-B-A island when the old segment already carries overlap
    evidence.
    """

    assigned: list[dict[str, Any]] = []
    source_index = 0
    for segment in segments:
        raw_words = list(segment.get("words", []))
        if not raw_words:
            raw_words = [
                (
                    str(segment.get("text", "")),
                    float(segment["start_seconds"]),
                    float(segment["end_seconds"]),
                )
            ]
        for raw_word in raw_words:
            if isinstance(raw_word, dict):
                text = str(raw_word["text"])
                start = float(raw_word["start_seconds"])
                end = float(raw_word["end_seconds"])
            else:
                text = str(raw_word[0])
                start = float(raw_word[1])
                end = float(raw_word[2])
            sentence_match = _SENTENCE_SUFFIX.search(text.strip())
            speaker = str(segment["speaker_label"])
            assigned.append(
                {
                    "source_index": source_index,
                    "source_segment_id": str(segment["segment_id"]),
                    "text": text,
                    "start_seconds": start,
                    "end_seconds": end,
                    "speaker": speaker,
                    "initial_speaker": speaker,
                    "speaker_candidates": {
                        speaker: max(0.0, end - start)
                    },
                    "overlap": bool(segment.get("overlap", False)),
                    "sentence_suffix": (
                        sentence_match.group(1)
                        if sentence_match is not None
                        else ""
                    ),
                    "sentence_end": sentence_match is not None,
                    "punctuation_confident": sentence_match is not None,
                }
            )
            source_index += 1
    decisions = _stabilize_assigned_words(
        assigned,
        policy=policy,
        partial_evidence=True,
        punctuation_available=False,
    )
    stabilized_segments = _segments_from_assigned_words(
        assigned,
        maximum_seconds=policy.maximum_segment_seconds,
    )
    return _result(
        assigned,
        stabilized_segments,
        decisions,
        policy=policy,
        raw_turn_count=0,
        punctuation_recovered=False,
        punctuation_alignment=_empty_punctuation_alignment(
            aligned_word_count=len(assigned),
        ),
        partial_evidence=True,
    )


def detect_cross_speaker_repetitions(
    segments: list[dict[str, Any]],
    *,
    minimum_words: int = 2,
    maximum_words: int = 8,
) -> list[dict[str, Any]]:
    """Report exact adjacent suffix/prefix repetition without editing text."""

    findings: list[dict[str, Any]] = []
    for before, after in zip(segments, segments[1:], strict=False):
        if str(before["speaker_label"]) == str(after["speaker_label"]):
            continue
        before_words = _lexical_words(str(before.get("text", "")))
        after_words = _lexical_words(str(after.get("text", "")))
        limit = min(maximum_words, len(before_words), len(after_words))
        for size in range(limit, minimum_words - 1, -1):
            if before_words[-size:] != after_words[:size]:
                continue
            findings.append(
                {
                    "kind": "cross_speaker_repetition",
                    "segment_ids": [
                        str(before["segment_id"]),
                        str(after["segment_id"]),
                    ],
                    "text": " ".join(after_words[:size]),
                    "word_count": size,
                }
            )
            break
    return findings


def detect_speaker_identity_drift(
    embeddings: dict[str, list[float]],
    *,
    minimum_similarity: float = 0.7,
) -> list[dict[str, Any]]:
    """Report similar episode-local labels without merging identities."""

    findings: list[dict[str, Any]] = []
    for left, right in combinations(sorted(embeddings), 2):
        similarity = cosine_similarity(
            embeddings[left],
            embeddings[right],
        )
        if similarity < minimum_similarity:
            continue
        findings.append(
            {
                "kind": "speaker_identity_drift",
                "speaker_labels": [left, right],
                "similarity": round(similarity, 4),
            }
        )
    return findings


def detect_short_speaker_participation(
    segments: list[dict[str, Any]],
    *,
    maximum_words: int = 6,
    maximum_seconds: float = 3.0,
) -> list[dict[str, Any]]:
    """Report tiny episode-local labels without changing Speaker identity."""

    totals: dict[str, dict[str, float | int]] = {}
    for segment in segments:
        label = str(segment["speaker_label"])
        words = list(segment.get("words", []))
        word_count = len(words) if words else len(
            str(segment.get("text", "")).split()
        )
        duration = max(
            0.0,
            float(segment["end_seconds"])
            - float(segment["start_seconds"]),
        )
        value = totals.setdefault(
            label,
            {"word_count": 0, "duration_seconds": 0.0},
        )
        value["word_count"] = int(value["word_count"]) + word_count
        value["duration_seconds"] = (
            float(value["duration_seconds"]) + duration
        )
    if len(totals) < 2:
        return []
    return [
        {
            "kind": "short_speaker_participation",
            "speaker_label": label,
            "word_count": int(value["word_count"]),
            "duration_seconds": round(
                float(value["duration_seconds"]),
                3,
            ),
        }
        for label, value in sorted(totals.items())
        if int(value["word_count"]) <= maximum_words
        and float(value["duration_seconds"]) <= maximum_seconds
    ]


def _assign_words(
    words: list[dict[str, Any]],
    diarization: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    for index, source_word in enumerate(words):
        start = float(source_word["start_seconds"])
        end = float(source_word["end_seconds"])
        source_text = str(source_word["text"])
        sentence_match = _SENTENCE_SUFFIX.search(source_text.strip())
        candidates: dict[str, float] = {}
        for turn in diarization:
            overlap = max(
                0.0,
                min(end, float(turn["end_seconds"]))
                - max(start, float(turn["start_seconds"])),
            )
            if overlap <= 0:
                continue
            speaker = str(turn["speaker"])
            candidates[speaker] = candidates.get(speaker, 0.0) + overlap
        speaker = (
            max(candidates, key=lambda label: candidates[label])
            if candidates
            else "speaker-unknown"
        )
        assigned.append(
            {
                "source_index": index,
                "text": source_text,
                "start_seconds": start,
                "end_seconds": end,
                "speaker": speaker,
                "initial_speaker": speaker,
                "speaker_candidates": candidates,
                "overlap": len(candidates) > 1,
                "sentence_suffix": (
                    sentence_match.group(1)
                    if sentence_match is not None
                    else ""
                ),
                "sentence_end": sentence_match is not None,
                "punctuation_confident": sentence_match is not None,
            }
        )
    return assigned


def _recover_sentence_punctuation(
    words: list[dict[str, Any]],
    transcript_text: str,
    *,
    policy: SpeakerBoundaryPolicy,
) -> dict[str, Any]:
    if not transcript_text.strip() or not words:
        return _empty_punctuation_alignment(
            aligned_word_count=len(words),
            transcript_token_count=len(transcript_text.split()),
        )
    transcript_tokens = transcript_text.split()
    source_tokens = [
        _normalized_token(
            str(word["text"]),
            normalize_contractions=policy.tolerant_punctuation_alignment,
        )
        for word in words
    ]
    target_tokens = [
        _normalized_token(
            token,
            normalize_contractions=policy.tolerant_punctuation_alignment,
        )
        for token in transcript_tokens
    ]
    if (
        not policy.tolerant_punctuation_alignment
        and source_tokens != target_tokens
    ):
        return {
            **_empty_punctuation_alignment(
                aligned_word_count=len(words),
                transcript_token_count=len(transcript_tokens),
            ),
            "mismatch_ranges": [
                {
                    "aligned_start": 0,
                    "aligned_end": len(words),
                    "transcript_start": 0,
                    "transcript_end": len(transcript_tokens),
                }
            ],
        }
    matcher = difflib.SequenceMatcher(
        None,
        source_tokens,
        target_tokens,
        autojunk=False,
    )
    matched_word_count = 0
    confident_matched_word_count = 0
    sentence_end_count = 0
    mismatch_ranges: list[dict[str, int]] = []
    low_confidence_ranges: list[dict[str, int]] = []
    reanchor_matches_remaining = 0
    for tag, source_start, source_end, target_start, target_end in (
        matcher.get_opcodes()
    ):
        if tag != "equal":
            mismatch_ranges.append(
                {
                    "aligned_start": source_start,
                    "aligned_end": source_end,
                    "transcript_start": target_start,
                    "transcript_end": target_end,
                }
            )
            if max(
                source_end - source_start,
                target_end - target_start,
            ) > policy.maximum_punctuation_gap_tokens:
                reanchor_matches_remaining = (
                    policy.minimum_punctuation_reanchor_words
                )
            continue
        low_confidence_source_start: int | None = None
        low_confidence_source_end: int | None = None
        low_confidence_target_start: int | None = None
        low_confidence_target_end: int | None = None
        for source_index, target_index in zip(
            range(source_start, source_end),
            range(target_start, target_end),
            strict=True,
        ):
            matched_word_count += 1
            locally_confident = True
            if reanchor_matches_remaining > 0:
                reanchor_matches_remaining -= 1
                locally_confident = reanchor_matches_remaining == 0
            if not locally_confident:
                if low_confidence_source_start is None:
                    low_confidence_source_start = source_index
                    low_confidence_target_start = target_index
                low_confidence_source_end = source_index + 1
                low_confidence_target_end = target_index + 1
                continue
            confident_matched_word_count += 1
            words[source_index]["punctuation_confident"] = True
            match = _SENTENCE_SUFFIX.search(
                transcript_tokens[target_index]
            )
            if match is None:
                continue
            words[source_index]["sentence_suffix"] = match.group(1)
            words[source_index]["sentence_end"] = True
            sentence_end_count += 1
        if low_confidence_source_start is not None:
            assert low_confidence_source_end is not None
            assert low_confidence_target_start is not None
            assert low_confidence_target_end is not None
            low_confidence_ranges.append(
                {
                    "aligned_start": low_confidence_source_start,
                    "aligned_end": low_confidence_source_end,
                    "transcript_start": low_confidence_target_start,
                    "transcript_end": low_confidence_target_end,
                }
            )
    return {
        "aligned_word_count": len(words),
        "transcript_token_count": len(transcript_tokens),
        "matched_word_count": matched_word_count,
        "confident_matched_word_count": confident_matched_word_count,
        "coverage": (
            matched_word_count / len(words)
            if words
            else 0.0
        ),
        "confident_coverage": (
            confident_matched_word_count / len(words)
            if words
            else 0.0
        ),
        "sentence_end_count": sentence_end_count,
        "mismatch_ranges": mismatch_ranges,
        "low_confidence_ranges": low_confidence_ranges,
    }


def _empty_punctuation_alignment(
    *,
    aligned_word_count: int,
    transcript_token_count: int = 0,
) -> dict[str, Any]:
    return {
        "aligned_word_count": aligned_word_count,
        "transcript_token_count": transcript_token_count,
        "matched_word_count": 0,
        "confident_matched_word_count": 0,
        "coverage": 0.0,
        "confident_coverage": 0.0,
        "sentence_end_count": 0,
        "mismatch_ranges": [],
        "low_confidence_ranges": [],
    }


def _normalized_token(
    value: str,
    *,
    normalize_contractions: bool = True,
) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = _LEADING_PUNCTUATION.sub("", normalized)
    normalized = _TRAILING_PUNCTUATION.sub("", normalized)
    if normalize_contractions:
        return normalized.replace("'", "").replace("’", "")
    return normalized


def _stabilize_assigned_words(
    words: list[dict[str, Any]],
    *,
    policy: SpeakerBoundaryPolicy,
    partial_evidence: bool,
    punctuation_available: bool,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for start, end in _speaker_runs(words):
        if start == 0 or end == len(words) - 1:
            continue
        outer_speaker = str(words[start - 1]["speaker"])
        middle_speaker = str(words[start]["speaker"])
        if outer_speaker != str(words[end + 1]["speaker"]):
            continue
        if outer_speaker == middle_speaker:
            continue
        island = words[start : end + 1]
        duration = max(
            0.0,
            float(island[-1]["end_seconds"])
            - float(island[0]["start_seconds"]),
        )
        if (
            len(island) > policy.maximum_island_words
            or duration > policy.maximum_island_seconds
        ):
            continue
        phrase = " ".join(
            _normalized_token(str(word["text"])) for word in island
        ).strip()
        winner_overlap = sum(
            float(word["speaker_candidates"].get(middle_speaker, 0.0))
            for word in island
        )
        outer_overlap = sum(
            float(word["speaker_candidates"].get(outer_speaker, 0.0))
            for word in island
        )
        margin = winner_overlap - outer_overlap
        cross_speaker_overlap = any(
            len(word["speaker_candidates"]) > 1 or bool(word["overlap"])
            for word in island
        )
        sentence_continuity = not bool(words[start - 1]["sentence_end"])
        evidence = {
            "word_count": len(island),
            "duration_seconds": round(duration, 6),
            "winner_overlap_seconds": round(winner_overlap, 6),
            "outer_overlap_seconds": round(outer_overlap, 6),
            "overlap_margin_seconds": round(margin, 6),
            "sentence_continuity": sentence_continuity,
            "cross_speaker_overlap": cross_speaker_overlap,
            "partial_evidence": partial_evidence,
        }
        phrase_words = phrase.split()
        if phrase in _ACKNOWLEDGEMENTS or all(
            word in _ACKNOWLEDGEMENTS for word in phrase_words
        ):
            outcome = "preserved"
            rule = "independent_acknowledgement"
            resolved_speaker = middle_speaker
        elif (
            phrase in _CROSSTALK_CUES
            and cross_speaker_overlap
        ):
            outcome = "preserved"
            rule = "complete_crosstalk_turn"
            resolved_speaker = middle_speaker
        elif (
            phrase in _CONTINUATION_FRAGMENTS
            and (
                phrase != "like"
                or policy.single_word_continuation_enabled
            )
            and cross_speaker_overlap
            and (
                partial_evidence
                or (
                    outer_overlap > 0
                    and margin
                    <= policy.maximum_overlap_margin_seconds
                )
            )
        ):
            outcome = "corrected"
            rule = "short_discourse_continuation"
            resolved_speaker = outer_speaker
        elif sentence_continuity and cross_speaker_overlap and (
            (
                not partial_evidence
                and outer_overlap > 0
                and margin <= policy.maximum_overlap_margin_seconds
            )
            or (partial_evidence and len(island) == 1)
        ):
            outcome = "corrected"
            rule = (
                "short_partial_overlap_a_b_a"
                if partial_evidence
                else "short_ambiguous_a_b_a"
            )
            resolved_speaker = outer_speaker
        elif not cross_speaker_overlap:
            outcome = "preserved"
            rule = "clear_exclusive_turn"
            resolved_speaker = middle_speaker
        else:
            outcome = "unresolved"
            rule = "ambiguous_short_a_b_a"
            resolved_speaker = middle_speaker
        decision = {
                "policy_version": policy.version,
                "outcome": outcome,
                "rule": rule,
                "original_speaker": middle_speaker,
                "resolved_speaker": resolved_speaker,
                "neighbour_speaker": outer_speaker,
                "word_start_index": int(island[0]["source_index"]),
                "word_end_index": int(island[-1]["source_index"]),
                "start_seconds": float(island[0]["start_seconds"]),
                "end_seconds": float(island[-1]["end_seconds"]),
                "text": " ".join(str(word["text"]) for word in island),
                "evidence": evidence,
            }
        source_segment_ids = list(
            dict.fromkeys(
                str(word["source_segment_id"])
                for word in island
                if word.get("source_segment_id") is not None
            )
        )
        if source_segment_ids:
            decision["source_segment_ids"] = source_segment_ids
        decisions.append(decision)
        if outcome == "corrected":
            for word in island:
                word["speaker"] = outer_speaker
    if punctuation_available and not partial_evidence:
        decisions.extend(
            _stabilize_sentence_boundaries(
                words,
                policy=policy,
                prior_decisions=decisions,
            )
        )
    return decisions


def _repair_backchannel_bleed(
    words: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    *,
    policy: SpeakerBoundaryPolicy,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    while True:
        corrected = False
        for start, end in _speaker_runs(words):
            if start == 0 or end == len(words) - 1:
                continue
            outer_speaker = str(words[start - 1]["speaker"])
            middle_speaker = str(words[start]["speaker"])
            if (
                outer_speaker != str(words[end + 1]["speaker"])
                or outer_speaker == middle_speaker
            ):
                continue
            island = words[start : end + 1]
            duration = max(
                0.0,
                float(island[-1]["end_seconds"])
                - float(island[0]["start_seconds"]),
            )
            if (
                len(island) > policy.maximum_island_words
                or duration > policy.maximum_island_seconds
            ):
                continue
            tokens = [
                _normalized_token(str(word["text"]))
                for word in island
            ]
            phrase = " ".join(tokens).strip()
            if (
                not tokens
                or phrase in _ACKNOWLEDGEMENTS
                or phrase in _CROSSTALK_CUES
                or all(token in _ACKNOWLEDGEMENTS for token in tokens)
                or tokens[-1] not in _BACKCHANNEL_BLEED_CONNECTORS
            ):
                continue
            raw_turn_seconds = _shortest_overlapping_turn_seconds(
                island,
                turns,
                middle_speaker,
            )
            has_zero_duration_word = any(
                float(word["end_seconds"])
                <= float(word["start_seconds"])
                for word in island
            )
            if (
                raw_turn_seconds is None
                or raw_turn_seconds
                > policy.maximum_backchannel_turn_seconds
                or not (
                    has_zero_duration_word
                    or not bool(words[start - 1]["sentence_end"])
                )
            ):
                continue
            decision = {
                "policy_version": policy.version,
                "outcome": "corrected",
                "rule": "untranscribed_backchannel_bleed",
                "original_speaker": middle_speaker,
                "resolved_speaker": outer_speaker,
                "neighbour_speaker": outer_speaker,
                "word_start_index": int(island[0]["source_index"]),
                "word_end_index": int(island[-1]["source_index"]),
                "start_seconds": float(island[0]["start_seconds"]),
                "end_seconds": float(island[-1]["end_seconds"]),
                "text": " ".join(str(word["text"]) for word in island),
                "evidence": {
                    "word_count": len(island),
                    "duration_seconds": round(duration, 6),
                    "raw_turn_seconds": round(raw_turn_seconds, 6),
                    "sentence_continuity": not bool(
                        words[start - 1]["sentence_end"]
                    ),
                    "zero_duration_word": has_zero_duration_word,
                    "partial_evidence": False,
                },
            }
            decisions.append(decision)
            for word in island:
                word["speaker"] = outer_speaker
            corrected = True
            break
        if not corrected:
            return decisions


def _shortest_overlapping_turn_seconds(
    words: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    speaker: str,
) -> float | None:
    start = float(words[0]["start_seconds"])
    end = float(words[-1]["end_seconds"])
    durations: list[float] = []
    for turn in turns:
        if str(turn["speaker"]) != speaker:
            continue
        turn_start = float(turn["start_seconds"])
        turn_end = float(turn["end_seconds"])
        overlaps = min(end, turn_end) - max(start, turn_start) > 0
        contains_zero_duration_word = (
            start == end and turn_start <= start <= turn_end
        )
        if overlaps or contains_zero_duration_word:
            durations.append(max(0.0, turn_end - turn_start))
    return min(durations) if durations else None


def _stabilize_sentence_boundaries(
    words: list[dict[str, Any]],
    *,
    policy: SpeakerBoundaryPolicy,
    prior_decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    decided_indexes = {
        index
        for decision in prior_decisions
        for index in range(
            int(decision["word_start_index"]),
            int(decision["word_end_index"]) + 1,
        )
    }
    while True:
        corrected = False
        unresolved: list[dict[str, Any]] = []
        for left_run, right_run in zip(
            _speaker_runs(words),
            _speaker_runs(words)[1:],
            strict=False,
        ):
            left_start, left_end = left_run
            right_start, right_end = right_run
            left_speaker = str(words[left_end]["speaker"])
            right_speaker = str(words[right_start]["speaker"])
            last_sentence_end = next(
                (
                    index
                    for index in range(left_end - 1, left_start - 1, -1)
                    if bool(words[index]["sentence_end"])
                ),
                None,
            )
            if last_sentence_end is not None:
                tail = words[last_sentence_end + 1 : left_end + 1]
                decision = _sentence_fragment_decision(
                    tail,
                    original_speaker=left_speaker,
                    resolved_speaker=right_speaker,
                    rule="short_sentence_tail",
                    policy=policy,
                    decided_indexes=decided_indexes,
                )
                if decision is not None:
                    if decision["outcome"] == "corrected":
                        _apply_decision(words, decision)
                        decisions.append(decision)
                        decided_indexes.update(
                            range(
                                int(decision["word_start_index"]),
                                int(decision["word_end_index"]) + 1,
                            )
                        )
                        corrected = True
                        break
                    unresolved.append(decision)
            first_sentence_end = next(
                (
                    index
                    for index in range(right_start, right_end + 1)
                    if bool(words[index]["sentence_end"])
                ),
                None,
            )
            if first_sentence_end is not None:
                prefix = words[right_start : first_sentence_end + 1]
                decision = _sentence_fragment_decision(
                    prefix,
                    original_speaker=right_speaker,
                    resolved_speaker=left_speaker,
                    rule="short_sentence_prefix",
                    policy=policy,
                    decided_indexes=decided_indexes,
                )
                if decision is not None:
                    if decision["outcome"] == "corrected":
                        _apply_decision(words, decision)
                        decisions.append(decision)
                        decided_indexes.update(
                            range(
                                int(decision["word_start_index"]),
                                int(decision["word_end_index"]) + 1,
                            )
                        )
                        corrected = True
                        break
                    unresolved.append(decision)
        if corrected:
            continue
        decisions.extend(unresolved)
        return decisions


def _sentence_fragment_decision(
    fragment: list[dict[str, Any]],
    *,
    original_speaker: str,
    resolved_speaker: str,
    rule: str,
    policy: SpeakerBoundaryPolicy,
    decided_indexes: set[int],
) -> dict[str, Any] | None:
    if not fragment or any(
        int(word["source_index"]) in decided_indexes for word in fragment
    ):
        return None
    if any(
        not bool(word.get("punctuation_confident", False))
        for word in fragment
    ):
        return None
    duration = max(
        0.0,
        float(fragment[-1]["end_seconds"])
        - float(fragment[0]["start_seconds"]),
    )
    if (
        len(fragment) > policy.maximum_boundary_fragment_words
        or duration > policy.maximum_boundary_fragment_seconds
    ):
        return None
    target_overlap = sum(
        float(word["speaker_candidates"].get(resolved_speaker, 0.0))
        for word in fragment
    )
    if target_overlap <= 0 or any(
        float(word["speaker_candidates"].get(resolved_speaker, 0.0)) <= 0
        for word in fragment
    ):
        return None
    winner_overlap = sum(
        float(word["speaker_candidates"].get(original_speaker, 0.0))
        for word in fragment
    )
    margin = winner_overlap - target_overlap
    phrase = " ".join(
        _normalized_token(str(word["text"])) for word in fragment
    ).strip()
    phrase_words = phrase.split()
    protected_turn = (
        phrase in _ACKNOWLEDGEMENTS
        or all(word in _ACKNOWLEDGEMENTS for word in phrase_words)
        or phrase in _CROSSTALK_CUES
    )
    outcome = (
        "preserved"
        if protected_turn
        else (
            "corrected"
            if margin <= policy.maximum_overlap_margin_seconds
            else "unresolved"
        )
    )
    return {
        "policy_version": policy.version,
        "outcome": outcome,
        "rule": (
            "independent_boundary_turn" if protected_turn else rule
        ),
        "original_speaker": original_speaker,
        "resolved_speaker": (
            resolved_speaker if outcome == "corrected" else original_speaker
        ),
        "neighbour_speaker": resolved_speaker,
        "word_start_index": int(fragment[0]["source_index"]),
        "word_end_index": int(fragment[-1]["source_index"]),
        "start_seconds": float(fragment[0]["start_seconds"]),
        "end_seconds": float(fragment[-1]["end_seconds"]),
        "text": " ".join(str(word["text"]) for word in fragment),
        "evidence": {
            "word_count": len(fragment),
            "duration_seconds": round(duration, 6),
            "winner_overlap_seconds": round(winner_overlap, 6),
            "outer_overlap_seconds": round(target_overlap, 6),
            "overlap_margin_seconds": round(margin, 6),
            "sentence_continuity": True,
            "cross_speaker_overlap": True,
            "partial_evidence": False,
        },
    }


def _apply_decision(
    words: list[dict[str, Any]],
    decision: dict[str, Any],
) -> None:
    for index in range(
        int(decision["word_start_index"]),
        int(decision["word_end_index"]) + 1,
    ):
        words[index]["speaker"] = str(decision["resolved_speaker"])


def _speaker_runs(words: list[dict[str, Any]]) -> list[tuple[int, int]]:
    if not words:
        return []
    runs: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(words)):
        if words[index]["speaker"] != words[index - 1]["speaker"]:
            runs.append((start, index - 1))
            start = index
    runs.append((start, len(words) - 1))
    return runs


def _segments_from_assigned_words(
    words: list[dict[str, Any]],
    *,
    maximum_seconds: float,
) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        speaker_changed = bool(
            current and current[-1]["speaker"] != word["speaker"]
        )
        too_long = bool(
            current
            and float(word["end_seconds"])
            - float(current[0]["start_seconds"])
            > maximum_seconds
        )
        if current and (speaker_changed or too_long):
            groups.append(current)
            current = []
        current.append(word)
        if word["sentence_end"]:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [
        {
            "segment_id": f"seg-{index:04d}",
            "speaker_label": str(group[0]["speaker"]),
            "start_seconds": float(group[0]["start_seconds"]),
            "end_seconds": float(group[-1]["end_seconds"]),
            "text": _group_text(group),
            "words": [
                {
                    "text": str(word["text"]),
                    "start_seconds": float(word["start_seconds"]),
                    "end_seconds": float(word["end_seconds"]),
                }
                for word in group
            ],
            "overlap": any(bool(word["overlap"]) for word in group),
            "events": [],
            "confidence": 0.95,
            "review_state": "accepted",
            "omitted_as_sponsor": False,
        }
        for index, group in enumerate(groups, start=1)
    ]


def _group_text(group: list[dict[str, Any]]) -> str:
    text = " ".join(str(word["text"]).strip() for word in group)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text).strip()
    suffix = str(group[-1]["sentence_suffix"])
    if suffix and not _SENTENCE_SUFFIX.search(text):
        text += suffix
    return text


def _public_word(word: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": str(word["text"]),
        "start_seconds": float(word["start_seconds"]),
        "end_seconds": float(word["end_seconds"]),
        "speaker": str(word["speaker"]),
        "initial_speaker": str(word["initial_speaker"]),
        "overlap": bool(word["overlap"]),
    }


def _result(
    assigned: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    *,
    policy: SpeakerBoundaryPolicy,
    raw_turn_count: int,
    punctuation_recovered: bool,
    punctuation_alignment: dict[str, Any],
    partial_evidence: bool,
) -> dict[str, Any]:
    corrected = [
        decision
        for decision in decisions
        if decision["outcome"] == "corrected"
    ]
    return {
        "words": [_public_word(word) for word in assigned],
        "segments": segments,
        "decisions": decisions,
        "summary": {
            "policy_version": policy.version,
            "policy": {
                "maximum_segment_seconds": (
                    policy.maximum_segment_seconds
                ),
                "maximum_island_words": policy.maximum_island_words,
                "maximum_island_seconds": policy.maximum_island_seconds,
                "maximum_overlap_margin_seconds": (
                    policy.maximum_overlap_margin_seconds
                ),
                "maximum_boundary_fragment_words": (
                    policy.maximum_boundary_fragment_words
                ),
                "maximum_boundary_fragment_seconds": (
                    policy.maximum_boundary_fragment_seconds
                ),
                "maximum_backchannel_turn_seconds": (
                    policy.maximum_backchannel_turn_seconds
                ),
                "maximum_punctuation_gap_tokens": (
                    policy.maximum_punctuation_gap_tokens
                ),
                "minimum_punctuation_reanchor_words": (
                    policy.minimum_punctuation_reanchor_words
                ),
                "tolerant_punctuation_alignment": (
                    policy.tolerant_punctuation_alignment
                ),
                "backchannel_bleed_enabled": (
                    policy.backchannel_bleed_enabled
                ),
                "single_word_continuation_enabled": (
                    policy.single_word_continuation_enabled
                ),
            },
            "raw_turn_count": raw_turn_count,
            "punctuation_recovered": punctuation_recovered,
            "punctuation_alignment": punctuation_alignment,
            "candidate_count": len(decisions),
            "corrected_count": len(corrected),
            "preserved_count": sum(
                decision["outcome"] == "preserved"
                for decision in decisions
            ),
            "unresolved_count": sum(
                decision["outcome"] == "unresolved"
                for decision in decisions
            ),
            "words_reassigned": sum(
                int(decision["evidence"]["word_count"])
                for decision in corrected
            ),
            "partial_evidence": partial_evidence,
        },
    }


def _lexical_words(value: str) -> list[str]:
    return [
        token
        for raw_token in value.split()
        if (token := _normalized_token(raw_token))
    ]


def resolve_unassigned_speakers(words: list[dict[str, Any]]) -> None:
    """Attach zero-duration or uncovered words to the nearest known turn."""
    previous: list[tuple[int, str] | None] = []
    closest: tuple[int, str] | None = None
    for index, word in enumerate(words):
        if str(word["speaker"]) != "speaker-unknown":
            closest = (index, str(word["speaker"]))
        previous.append(closest)

    following: list[tuple[int, str] | None] = [None] * len(words)
    closest = None
    for index in range(len(words) - 1, -1, -1):
        word = words[index]
        if str(word["speaker"]) != "speaker-unknown":
            closest = (index, str(word["speaker"]))
        following[index] = closest

    for index, word in enumerate(words):
        if str(word["speaker"]) != "speaker-unknown":
            continue
        before = previous[index]
        after = following[index]
        if before is None and after is None:
            continue
        if before is None:
            word["speaker"] = after[1] if after is not None else word["speaker"]
        elif after is None or before[1] == after[1]:
            word["speaker"] = before[1]
        else:
            previous_word = words[before[0]]
            following_word = words[after[0]]
            previous_gap = abs(
                float(word["start_seconds"])
                - float(previous_word["end_seconds"])
            )
            following_gap = abs(
                float(following_word["start_seconds"])
                - float(word["end_seconds"])
            )
            word["speaker"] = (
                before[1] if previous_gap <= following_gap else after[1]
            )
        word["initial_speaker"] = str(word["speaker"])
