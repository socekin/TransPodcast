from __future__ import annotations

from transpodcast.speaker_boundaries import (
    recover_sentence_punctuation,
    resolve_unassigned_speakers,
)


def _words(*items: tuple[str, float, float]) -> list[dict[str, object]]:
    return [
        {"text": text, "start_seconds": start, "end_seconds": end}
        for text, start, end in items
    ]


def _suffixes(words: list[dict[str, object]]) -> list[str]:
    return [str(word.get("sentence_suffix", "")) for word in words]


def test_punctuation_recovery_tolerates_a_local_asr_insertion() -> None:
    words = _words(
        ("Hello", 0.0, 0.4),
        ("world", 0.4, 0.8),
        ("Again", 0.8, 1.2),
        ("today", 1.2, 1.6),
    )

    alignment = recover_sentence_punctuation(
        words,
        "Hello brave world. Again today!",
    )

    assert alignment == {
        "aligned_word_count": 4,
        "transcript_token_count": 5,
        "matched_word_count": 4,
        "confident_matched_word_count": 4,
        "coverage": 1.0,
        "confident_coverage": 1.0,
        "sentence_end_count": 2,
        "mismatch_ranges": [
            {
                "aligned_start": 1,
                "aligned_end": 1,
                "transcript_start": 1,
                "transcript_end": 2,
            }
        ],
        "low_confidence_ranges": [],
    }
    assert _suffixes(words) == ["", ".", "", "!"]
    assert [word["text"] for word in words] == [
        "Hello",
        "world",
        "Again",
        "today",
    ]


def test_punctuation_recovery_tolerates_a_local_asr_deletion() -> None:
    words = _words(
        ("Hello", 0.0, 0.3),
        ("brave", 0.3, 0.6),
        ("world", 0.6, 0.9),
    )

    alignment = recover_sentence_punctuation(words, "Hello world.")

    assert alignment["matched_word_count"] == 2
    assert _suffixes(words) == ["", "", "."]


def test_punctuation_recovery_normalizes_case_and_contractions() -> None:
    words = _words(("DONT", 0.0, 0.4), ("stop", 0.4, 0.8))

    alignment = recover_sentence_punctuation(words, "Don’t stop!")

    assert alignment["matched_word_count"] == 2
    assert _suffixes(words) == ["", "!"]


def test_punctuation_recovery_requires_a_local_reanchor_after_a_large_gap() -> None:
    words = _words(
        ("Before", 0.0, 0.3),
        ("pivot", 0.3, 0.6),
        ("tail", 0.6, 0.9),
    )
    inserted = " ".join(f"noise-{index}" for index in range(12))

    alignment = recover_sentence_punctuation(
        words,
        f"Before {inserted} pivot. tail",
    )

    assert alignment["matched_word_count"] == 3
    assert alignment["confident_matched_word_count"] == 1
    assert alignment["low_confidence_ranges"] == [
        {
            "aligned_start": 1,
            "aligned_end": 3,
            "transcript_start": 13,
            "transcript_end": 15,
        }
    ]
    assert alignment["sentence_end_count"] == 0
    assert not any(word.get("sentence_end") for word in words)


def test_punctuation_recovery_without_a_transcript_is_empty() -> None:
    words = _words(("Hello", 0.0, 0.4))

    alignment = recover_sentence_punctuation(words, "  ")

    assert alignment["aligned_word_count"] == 1
    assert alignment["matched_word_count"] == 0
    assert words == _words(("Hello", 0.0, 0.4))


def test_unassigned_words_join_the_nearest_known_turn() -> None:
    words = [
        {"text": "one", "start_seconds": 0.0, "end_seconds": 0.4,
         "speaker": "speaker-a"},
        {"text": "two", "start_seconds": 0.45, "end_seconds": 0.45,
         "speaker": "speaker-unknown"},
        {"text": "three", "start_seconds": 1.4, "end_seconds": 1.8,
         "speaker": "speaker-b"},
        {"text": "four", "start_seconds": 1.8, "end_seconds": 1.8,
         "speaker": "speaker-unknown"},
    ]

    resolve_unassigned_speakers(words)

    assert [word["speaker"] for word in words] == [
        "speaker-a",
        "speaker-a",
        "speaker-b",
        "speaker-b",
    ]
    assert words[1]["initial_speaker"] == "speaker-a"
    assert "initial_speaker" not in words[0]
