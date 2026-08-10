from __future__ import annotations

from transpodcast.transcript import canonical_caption_segments


def test_webvtt_is_normalized_into_stable_complete_segments() -> None:
    captions = """WEBVTT

00:00:00.000 --> 00:00:02.000
<v Host>Welcome to the show.

00:00:02.000 --> 00:00:04.000
Welcome to the show.
Today we discuss durable products.

00:00:04.000 --> 00:00:06.000
Today we discuss durable products.
"""

    assert canonical_caption_segments(captions) == (
        "Welcome to the show.",
        "Today we discuss durable products.",
    )


def test_plain_caption_text_remains_one_complete_segment() -> None:
    assert canonical_caption_segments("A complete sentence.") == (
        "A complete sentence.",
    )
