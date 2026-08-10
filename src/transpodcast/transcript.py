from __future__ import annotations

import html
import re

_TAG = re.compile(r"<[^>]+>")
_TIMING = re.compile(
    r"^\s*(?:\d{2}:)?\d{2}:\d{2}\.\d{3}\s+-->\s+"
    r"(?:\d{2}:)?\d{2}:\d{2}\.\d{3}"
)


def canonical_caption_segments(captions: str) -> tuple[str, ...]:
    normalized = captions.strip()
    if not normalized:
        return ()
    if "WEBVTT" not in normalized and "-->" not in normalized:
        return (_normalize_text(normalized),)

    segments: list[str] = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [
            line.strip()
            for line in block.splitlines()
            if line.strip()
        ]
        if not lines or lines[0] == "WEBVTT":
            continue
        timing_index = next(
            (
                index
                for index, line in enumerate(lines)
                if _TIMING.match(line)
            ),
            None,
        )
        if timing_index is None:
            continue
        for line in lines[timing_index + 1 :]:
            text = _normalize_text(line)
            if text and (not segments or segments[-1] != text):
                segments.append(text)
    return tuple(segments)


def _normalize_text(value: str) -> str:
    without_tags = _TAG.sub("", value)
    return " ".join(html.unescape(without_tags).split())
