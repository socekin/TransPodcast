"""Crosstalk candidates for Crosstalk Edits (crosstalk WS-B).

A crosstalk zone is a stretch where three or more Speaker changes start
within 6 s; zones closer than 1 s merge, and every zone covers whole
segments. The dub never overlaps voices, so heavy crosstalk plays as a row
of one- or two-word fragments in several Mandarin voices. The Translation
Reviewer decides each candidate (Owner decision D5): keep it, reassemble
each speaker's complete sentences when attribution and order are clear, or,
only where they are not, trim the fragments that are both unattributable
and empty of information. The rule only proposes candidates, so it may be
generous; ``translation_project_validator`` checks every decision.

A 94-minute recorded episode with four hosts has 19 zones, 6.6 minutes:
opening and closing banter and about as much substantive debate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

CROSSTALK_CANDIDATE_POLICY_VERSION = "crosstalk-candidates-v1"
CROSSTALK_CANDIDATES_PATH = "source/crosstalk-candidates.json"
WINDOW_SECONDS = 6.0
MINIMUM_SPEAKER_CHANGES = 3
MERGE_GAP_SECONDS = 1.0
# Opening and closing banter often continues past the dense crosstalk (one
# recorded episode ends with 34 s of sign-off and outro song after its last
# zone), so the
# first and last candidates reach the episode's edges when they come this
# close.
OPENING_EXTENSION_SECONDS = 15.0
CLOSING_EXTENSION_SECONDS = 40.0


def crosstalk_candidates(
    segments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Candidate zones over Translation Project segments, in order.

    Segments need ``id``, ``speaker``, ``source_start_seconds`` and
    ``source_end_seconds``; ``source_text`` is copied for the Reviewer.
    """
    timed = [
        segment
        for segment in segments
        if isinstance(segment.get("source_start_seconds"), int | float)
        and isinstance(segment.get("source_end_seconds"), int | float)
    ]
    zones: list[list[int]] = []
    for first in range(len(timed)):
        start = float(timed[first]["source_start_seconds"])
        last = first
        changes = 0
        while (
            last + 1 < len(timed)
            and float(timed[last + 1]["source_start_seconds"]) - start
            <= WINDOW_SECONDS
        ):
            if timed[last + 1]["speaker"] != timed[last]["speaker"]:
                changes += 1
            last += 1
        if changes < MINIMUM_SPEAKER_CHANGES:
            continue
        if zones and (
            first <= zones[-1][1]
            or float(timed[first]["source_start_seconds"])
            - float(timed[zones[-1][1]]["source_end_seconds"])
            <= MERGE_GAP_SECONDS
        ):
            zones[-1][1] = max(zones[-1][1], last)
        else:
            zones.append([first, last])
    if zones and (
        float(timed[zones[0][0]]["source_start_seconds"])
        - float(timed[0]["source_start_seconds"])
        <= OPENING_EXTENSION_SECONDS
    ):
        zones[0][0] = 0
    if zones and (
        float(timed[-1]["source_end_seconds"])
        - float(timed[zones[-1][1]]["source_end_seconds"])
        <= CLOSING_EXTENSION_SECONDS
    ):
        zones[-1][1] = len(timed) - 1
    return [
        _candidate(
            number,
            timed[first : last + 1],
            opening=first == 0,
            closing=last == len(timed) - 1,
        )
        for number, (first, last) in enumerate(zones, start=1)
    ]


def _candidate(
    number: int,
    members: Sequence[Mapping[str, Any]],
    *,
    opening: bool,
    closing: bool,
) -> dict[str, Any]:
    return {
        "candidate_id": f"xt-{number:03d}",
        "opening": opening,
        "closing": closing,
        "start_segment_id": str(members[0]["id"]),
        "end_segment_id": str(members[-1]["id"]),
        "segment_ids": [str(segment["id"]) for segment in members],
        "start_seconds": round(float(members[0]["source_start_seconds"]), 3),
        "end_seconds": round(float(members[-1]["source_end_seconds"]), 3),
        "speaker_changes": sum(
            members[index]["speaker"] != members[index - 1]["speaker"]
            for index in range(1, len(members))
        ),
        "speakers": sorted({str(segment["speaker"]) for segment in members}),
        "lines": [
            {
                "segment_id": str(segment["id"]),
                "speaker": str(segment["speaker"]),
                "source_text": str(segment.get("source_text", "")),
            }
            for segment in members
        ],
    }


def crosstalk_candidates_payload(
    segments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "policy_version": CROSSTALK_CANDIDATE_POLICY_VERSION,
        "window_seconds": WINDOW_SECONDS,
        "minimum_speaker_changes": MINIMUM_SPEAKER_CHANGES,
        "candidates": crosstalk_candidates(segments),
    }
