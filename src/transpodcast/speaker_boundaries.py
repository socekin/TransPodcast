"""Word-level helpers shared by Source Analysis and natural segmentation.

The retired v7 Speaker Boundary Stabilization rules no longer run for any
Processing Run. Only the sentence-punctuation recovery and the
unassigned-word attachment they shared with the active pipeline remain.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

_SENTENCE_SUFFIX = re.compile(r"([.!?。！？]+[\"')\]]*)$")
_TRAILING_PUNCTUATION = re.compile(r"[^\w'’]+$", re.UNICODE)
_LEADING_PUNCTUATION = re.compile(r"^[^\w'’]+", re.UNICODE)
# A mismatch wider than this many tokens requires a local re-anchor before
# recovered punctuation is trusted again.
_MAXIMUM_PUNCTUATION_GAP_TOKENS = 8
_MINIMUM_PUNCTUATION_REANCHOR_WORDS = 3


def recover_sentence_punctuation(
    words: list[dict[str, Any]],
    transcript_text: str,
) -> dict[str, Any]:
    """Annotate aligned words with confidently recovered sentence endings."""
    if not transcript_text.strip() or not words:
        return _empty_punctuation_alignment(
            aligned_word_count=len(words),
            transcript_token_count=len(transcript_text.split()),
        )
    transcript_tokens = transcript_text.split()
    source_tokens = [_normalized_token(str(word["text"])) for word in words]
    target_tokens = [_normalized_token(token) for token in transcript_tokens]
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
            ) > _MAXIMUM_PUNCTUATION_GAP_TOKENS:
                reanchor_matches_remaining = (
                    _MINIMUM_PUNCTUATION_REANCHOR_WORDS
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


def _normalized_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = _LEADING_PUNCTUATION.sub("", normalized)
    normalized = _TRAILING_PUNCTUATION.sub("", normalized)
    return normalized.replace("'", "").replace("’", "")


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
