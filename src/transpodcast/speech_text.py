"""Render-only fixes to the text a speech voice reads.

The Episode Segment keeps its target text unchanged; only the copy sent to the
voice, and the Render Plan identity that names the rendered clip, use the
speech text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# IDEOGRAPHIC NUMBER ZERO. Both IndexTTS voices skip it, so a year written
# with it is heard as 二二五年 or 二十五年. 零 is the same digit and reads
# correctly.
_CIRCLE_ZERO = "〇"
_ZERO = "零"


def speech_text(text: str) -> str:
    """Return the text the voice reads for a Mandarin target."""
    return text.replace(_CIRCLE_ZERO, _ZERO)


def speech_units(units: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy Render Plan units with their speech text as ``target_text``."""
    return [
        {**unit, "target_text": speech_text(str(unit["target_text"]))}
        for unit in units
    ]
