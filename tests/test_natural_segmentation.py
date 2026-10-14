from __future__ import annotations

from transpodcast.domain import CanonicalSegment
from transpodcast.natural_segmentation import (
    NaturalSegmentationResult,
    segment_naturally,
)


def _segment(
    segment_id: str,
    speaker: str,
    words: tuple[tuple[str, float, float], ...],
) -> CanonicalSegment:
    return CanonicalSegment(
        segment_id=segment_id,
        speaker_label=speaker,
        start_seconds=words[0][1],
        end_seconds=words[-1][2],
        text=" ".join(word[0] for word in words),
        words=words,
    )


def _assert_segmentation_invariants(
    original: tuple[CanonicalSegment, ...],
    result: NaturalSegmentationResult,
    *,
    full_asr_text: str,
    protected_word_ranges: tuple[tuple[int, int], ...] = (),
    source_event_ranges: tuple[tuple[float, float], ...] = (),
) -> None:
    original_words = tuple(
        word for segment in original for word in segment.words
    )
    assert tuple(
        word for segment in result.segments for word in segment.words
    ) == original_words
    assert all(segment.speaker_label for segment in result.segments)
    assert all(
        left.end_seconds <= right.start_seconds + 1e-6
        for left, right in zip(
            result.segments,
            result.segments[1:],
            strict=False,
        )
    )
    assert [segment.segment_id for segment in result.segments] == [
        f"seg-{index:04d}"
        for index in range(1, len(result.segments) + 1)
    ]
    replay = segment_naturally(
        result.segments,
        full_asr_text=full_asr_text,
        protected_word_ranges=protected_word_ranges,
        source_event_ranges=source_event_ranges,
    )
    assert replay.segments == result.segments


def test_coalesces_short_sentences_and_cuts_at_a_natural_boundary() -> None:
    words = (
        ("This", 0.0, 0.8),
        ("works", 0.8, 1.6),
        ("well", 1.6, 2.4),
        ("It", 2.8, 3.3),
        ("also", 3.3, 4.0),
        ("sounds", 4.0, 4.8),
        ("natural", 4.8, 5.6),
        ("Now", 6.2, 6.8),
        ("we", 6.8, 7.2),
        ("continue", 7.2, 8.0),
    )

    original = (_segment("old-1", "speaker-a", words),)
    transcript = "This works well. It also sounds natural. Now we continue."
    result = segment_naturally(original, full_asr_text=transcript)

    assert len(result.segments) == 1
    assert result.segments[0].text == (
        "This works well. It also sounds natural. Now we continue."
    )
    assert result.fallback_boundary_count == 0
    _assert_segmentation_invariants(
        original,
        result,
        full_asr_text=transcript,
    )


def test_speaker_change_is_a_hard_boundary_even_for_short_turns() -> None:
    original = (
        _segment("old-1", "speaker-a", (("I", 0.0, 0.2), ("think", 0.2, 0.8))),
        _segment("old-2", "speaker-b", (("yeah", 0.8, 1.1),)),
        _segment(
            "old-3",
            "speaker-a",
            (("this", 1.1, 1.5), ("works", 1.5, 2.0)),
        ),
    )

    result = segment_naturally(
        original,
        full_asr_text="I think. yeah. this works.",
    )

    assert [segment.speaker_label for segment in result.segments] == [
        "speaker-a",
        "speaker-b",
        "speaker-a",
    ]
    assert result.speaker_boundary_count == 2
    _assert_segmentation_invariants(
        original,
        result,
        full_asr_text="I think. yeah. this works.",
    )


def test_long_punctuation_free_speech_uses_a_bounded_fallback() -> None:
    words = tuple(
        (f"word-{index}", index * 1.0, (index + 1) * 1.0)
        for index in range(30)
    )

    original = (_segment("old-1", "speaker-a", words),)
    result = segment_naturally(original, full_asr_text="")

    assert len(result.segments) == 2
    assert result.fallback_boundary_count == 1
    assert all(
        segment.end_seconds - segment.start_seconds <= 24.0
        for segment in result.segments
    )
    assert tuple(
        word for segment in result.segments for word in segment.words
    ) == words
    _assert_segmentation_invariants(original, result, full_asr_text="")


def test_protected_crosstalk_region_avoids_optional_internal_cuts() -> None:
    words = tuple(
        (f"word-{index}", float(index), float(index + 1))
        for index in range(20)
    )
    transcript = " ".join(
        f"word-{index}{'.' if index in {5, 11, 17, 19} else ''}"
        for index in range(20)
    )

    original = (_segment("old-1", "speaker-a", words),)
    result = segment_naturally(
        original,
        full_asr_text=transcript,
        protected_word_ranges=((0, 20),),
    )

    assert len(result.segments) == 1
    assert result.fallback_boundary_count == 0
    _assert_segmentation_invariants(
        original,
        result,
        full_asr_text=transcript,
        protected_word_ranges=((0, 20),),
    )


def test_source_event_is_a_boundary_inside_one_speaker_turn() -> None:
    before = _segment(
        "old-1",
        "speaker-a",
        (("That", 0.0, 1.0), ("works", 1.0, 2.0)),
    )
    after = _segment(
        "old-2",
        "speaker-a",
        (("We", 2.0, 3.0), ("continue", 3.0, 4.0)),
    )
    before = CanonicalSegment(
        **{**before.__dict__, "events": ("laughter",)}
    )

    result = segment_naturally(
        (before, after),
        full_asr_text="That works. We continue.",
    )

    assert [segment.text for segment in result.segments] == [
        "That works.",
        "We continue.",
    ]
    assert result.source_event_boundary_count == 1
    _assert_segmentation_invariants(
        (before, after),
        result,
        full_asr_text="That works. We continue.",
    )


def test_removed_source_event_still_forces_a_boundary_by_time() -> None:
    words = (
        ("That", 0.0, 1.0),
        ("works", 1.0, 2.0),
        ("We", 3.0, 4.0),
        ("continue", 4.0, 5.0),
    )

    original = (_segment("old-1", "speaker-a", words),)
    result = segment_naturally(
        original,
        full_asr_text="That works. We continue.",
        source_event_ranges=((2.0, 3.0),),
    )

    assert [segment.text for segment in result.segments] == [
        "That works.",
        "We continue.",
    ]
    assert result.source_event_boundary_count == 1
    _assert_segmentation_invariants(
        original,
        result,
        full_asr_text="That works. We continue.",
        source_event_ranges=((2.0, 3.0),),
    )


def test_pause_only_candidate_is_selected_without_punctuation() -> None:
    words = tuple(
        (
            f"word-{index}",
            index * 0.8 + (0.5 if index >= 12 else 0.0),
            (index + 1) * 0.8 + (0.5 if index >= 12 else 0.0),
        )
        for index in range(24)
    )
    original = (_segment("old-1", "speaker-a", words),)

    result = segment_naturally(original, full_asr_text="")

    assert result.pause_boundary_count == 1
    assert result.fallback_boundary_count == 0
    _assert_segmentation_invariants(original, result, full_asr_text="")


def test_punctuation_mismatch_falls_back_without_changing_words() -> None:
    words = tuple(
        (f"word-{index}", float(index), float(index + 1))
        for index in range(30)
    )
    original = (_segment("old-1", "speaker-a", words),)

    result = segment_naturally(
        original,
        full_asr_text="Completely unrelated punctuation. Nothing aligns!",
    )

    assert result.punctuation_alignment["confident_matched_word_count"] == 0
    assert result.fallback_boundary_count == 1
    _assert_segmentation_invariants(
        original,
        result,
        full_asr_text="Completely unrelated punctuation. Nothing aligns!",
    )
