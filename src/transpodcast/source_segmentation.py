"""Shared v6 word grouping for Source Analysis and local adjudication."""

from __future__ import annotations

import re
from typing import Any

SENTENCE_END = re.compile(r"[.!?。！？][\"')\]]?$")
MAXIMUM_SEGMENT_SECONDS = 15.0


def group_assigned_words(
    assigned: list[dict[str, Any]],
    *,
    maximum_seconds: float = MAXIMUM_SEGMENT_SECONDS,
) -> list[list[dict[str, Any]]]:
    """Apply the accepted v6 Speaker, duration, and sentence cuts."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in assigned:
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
        if SENTENCE_END.search(str(word["text"]).strip()):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def join_words(words: list[dict[str, Any]]) -> str:
    text = " ".join(str(word["text"]).strip() for word in words)
    return re.sub(r"\s+([,.!?;:])", r"\1", text).strip()
