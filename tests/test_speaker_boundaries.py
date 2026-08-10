from __future__ import annotations

from transpodcast.speaker_boundaries import (
    LEGACY_SPEAKER_BOUNDARY_POLICY,
    detect_cross_speaker_repetitions,
    detect_short_speaker_participation,
    detect_speaker_identity_drift,
    stabilize_existing_segments,
    stabilize_speaker_boundaries,
)


def test_stabilizer_repairs_confirmed_one_word_boundary_flicker() -> None:
    words = [
        {"text": "can", "start_seconds": 0.00, "end_seconds": 0.20},
        {"text": "you", "start_seconds": 0.20, "end_seconds": 0.40},
        {"text": "slow", "start_seconds": 0.40, "end_seconds": 0.60},
        {"text": "down", "start_seconds": 0.60, "end_seconds": 0.80},
        {"text": "a", "start_seconds": 0.80, "end_seconds": 1.00},
        {"text": "bit", "start_seconds": 1.00, "end_seconds": 1.20},
        {"text": "I", "start_seconds": 1.20, "end_seconds": 1.40},
        {"text": "tried", "start_seconds": 1.40, "end_seconds": 1.80},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.00, "end_seconds": 1.06},
        {"speaker": "speaker-b", "start_seconds": 1.02, "end_seconds": 1.18},
        {"speaker": "speaker-a", "start_seconds": 1.16, "end_seconds": 2.00},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="Can you slow down a bit? I tried.",
    )

    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == ["speaker-a", "speaker-a"]
    assert [segment["text"] for segment in result["segments"]] == [
        "can you slow down a bit?",
        "I tried.",
    ]
    assert result["summary"]["corrected_count"] == 1
    assert result["summary"]["words_reassigned"] == 1
    assert result["decisions"] == [
        {
            "policy_version": "speaker-boundary-stabilization-v2",
            "outcome": "corrected",
            "rule": "short_ambiguous_a_b_a",
            "original_speaker": "speaker-b",
            "resolved_speaker": "speaker-a",
            "neighbour_speaker": "speaker-a",
            "word_start_index": 5,
            "word_end_index": 5,
            "start_seconds": 1.0,
            "end_seconds": 1.2,
            "text": "bit",
            "evidence": {
                "word_count": 1,
                "duration_seconds": 0.2,
                "winner_overlap_seconds": 0.16,
                "outer_overlap_seconds": 0.1,
                "overlap_margin_seconds": 0.06,
                "sentence_continuity": True,
                "cross_speaker_overlap": True,
                "partial_evidence": False,
            },
        }
    ]
    assert result["summary"]["punctuation_recovered"] is True


def test_stabilizer_preserves_a_clear_independent_acknowledgement() -> None:
    words = [
        {"text": "That", "start_seconds": 0.0, "end_seconds": 0.3},
        {"text": "works", "start_seconds": 0.3, "end_seconds": 0.7},
        {"text": "Yeah", "start_seconds": 0.8, "end_seconds": 1.1},
        {"text": "Next", "start_seconds": 1.2, "end_seconds": 1.5},
        {"text": "question", "start_seconds": 1.5, "end_seconds": 2.0},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.7},
        {"speaker": "speaker-b", "start_seconds": 0.8, "end_seconds": 1.1},
        {"speaker": "speaker-a", "start_seconds": 1.2, "end_seconds": 2.0},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="That works. Yeah. Next question.",
    )

    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == ["speaker-a", "speaker-b", "speaker-a"]
    assert result["decisions"][0]["outcome"] == "preserved"
    assert result["decisions"][0]["rule"] == "independent_acknowledgement"


def test_stabilizer_preserves_a_short_complete_crosstalk_turn() -> None:
    words = [
        {"text": "I", "start_seconds": 0.0, "end_seconds": 0.2},
        {"text": "think", "start_seconds": 0.2, "end_seconds": 0.5},
        {"text": "we", "start_seconds": 0.5, "end_seconds": 0.8},
        {"text": "Wait", "start_seconds": 0.72, "end_seconds": 1.0},
        {"text": "should", "start_seconds": 1.0, "end_seconds": 1.3},
        {"text": "continue", "start_seconds": 1.3, "end_seconds": 1.7},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.82},
        {"speaker": "speaker-b", "start_seconds": 0.72, "end_seconds": 1.0},
        {"speaker": "speaker-a", "start_seconds": 0.96, "end_seconds": 1.7},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="I think we Wait! should continue.",
    )

    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == ["speaker-a", "speaker-b", "speaker-a"]
    assert result["decisions"][0]["outcome"] == "preserved"
    assert result["decisions"][0]["rule"] == "complete_crosstalk_turn"


def test_stabilizer_leaves_ambiguous_island_for_review() -> None:
    words = [
        {"text": "We", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "might", "start_seconds": 0.4, "end_seconds": 0.8},
        {"text": "change", "start_seconds": 0.8, "end_seconds": 1.2},
        {"text": "the", "start_seconds": 1.2, "end_seconds": 1.5},
        {"text": "plan", "start_seconds": 1.5, "end_seconds": 1.9},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.82},
        {"speaker": "speaker-b", "start_seconds": 0.8, "end_seconds": 1.2},
        {"speaker": "speaker-a", "start_seconds": 1.18, "end_seconds": 1.9},
    ]

    result = stabilize_speaker_boundaries(words, turns)

    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == ["speaker-a", "speaker-b", "speaker-a"]
    assert result["decisions"][0]["outcome"] == "unresolved"
    assert result["summary"]["unresolved_count"] == 1


def test_stabilizer_preserves_a_clear_exclusive_short_turn() -> None:
    words = [
        {"text": "We", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "change", "start_seconds": 0.5, "end_seconds": 0.8},
        {"text": "Next", "start_seconds": 0.9, "end_seconds": 1.3},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.4},
        {"speaker": "speaker-b", "start_seconds": 0.5, "end_seconds": 0.8},
        {"speaker": "speaker-a", "start_seconds": 0.9, "end_seconds": 1.3},
    ]

    result = stabilize_speaker_boundaries(words, turns)

    assert result["decisions"][0]["outcome"] == "preserved"
    assert result["decisions"][0]["rule"] == "clear_exclusive_turn"


def test_stabilizer_preserves_words_timestamps_order_and_is_idempotent() -> None:
    words = [
        {"text": "We", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "can", "start_seconds": 0.4, "end_seconds": 0.4},
        {"text": "continue", "start_seconds": 0.4, "end_seconds": 1.0},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 1.0}
    ]

    first = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="We can continue.",
    )
    second = stabilize_speaker_boundaries(
        first["words"],
        turns,
        transcript_text="We can continue.",
    )

    expected_words = [
        (word["text"], word["start_seconds"], word["end_seconds"])
        for word in words
    ]
    assert [
        (word["text"], word["start_seconds"], word["end_seconds"])
        for word in first["words"]
    ] == expected_words
    assert first == second


def test_punctuation_recovery_tolerates_a_local_asr_insertion() -> None:
    words = [
        {"text": "Hello", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "world", "start_seconds": 0.4, "end_seconds": 0.8},
        {"text": "Again", "start_seconds": 0.8, "end_seconds": 1.2},
        {"text": "today", "start_seconds": 1.2, "end_seconds": 1.6},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 1.6}
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="Hello brave world. Again today!",
    )

    assert result["summary"]["punctuation_recovered"] is True
    assert result["summary"]["punctuation_alignment"] == {
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
    assert [segment["text"] for segment in result["segments"]] == [
        "Hello world.",
        "Again today!",
    ]
    assert [word["text"] for word in result["words"]] == [
        "Hello",
        "world",
        "Again",
        "today",
    ]


def test_punctuation_recovery_tolerates_a_local_asr_deletion() -> None:
    words = [
        {"text": "Hello", "start_seconds": 0.0, "end_seconds": 0.3},
        {"text": "brave", "start_seconds": 0.3, "end_seconds": 0.6},
        {"text": "world", "start_seconds": 0.6, "end_seconds": 0.9},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.9}
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="Hello world.",
    )

    assert result["summary"]["punctuation_alignment"][
        "matched_word_count"
    ] == 2
    assert result["segments"][0]["text"] == "Hello brave world."


def test_punctuation_recovery_normalizes_case_and_contractions() -> None:
    words = [
        {"text": "DONT", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "stop", "start_seconds": 0.4, "end_seconds": 0.8},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.8}
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="Don’t stop!",
    )

    assert result["summary"]["punctuation_alignment"][
        "matched_word_count"
    ] == 2
    assert result["segments"][0]["text"] == "DONT stop!"


def test_punctuation_recovery_requires_a_local_reanchor_after_a_large_gap() -> None:
    words = [
        {"text": "Before", "start_seconds": 0.0, "end_seconds": 0.3},
        {"text": "pivot", "start_seconds": 0.3, "end_seconds": 0.6},
        {"text": "tail", "start_seconds": 0.6, "end_seconds": 0.9},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.9}
    ]
    inserted = " ".join(f"noise-{index}" for index in range(12))

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text=f"Before {inserted} pivot. tail",
    )

    alignment = result["summary"]["punctuation_alignment"]
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
    assert result["segments"][0]["text"] == "Before pivot tail"


def test_run_8_backchannel_does_not_steal_connecting_words() -> None:
    words = [
        {"text": "compute", "start_seconds": 0.0, "end_seconds": 0.3},
        {"text": "bound", "start_seconds": 0.3, "end_seconds": 0.7},
        {"text": "right", "start_seconds": 0.7, "end_seconds": 0.9},
        {"text": "And", "start_seconds": 0.9, "end_seconds": 1.1},
        {"text": "the", "start_seconds": 1.1, "end_seconds": 1.3},
        {"text": "industry", "start_seconds": 1.3, "end_seconds": 1.7},
        {"text": "feels", "start_seconds": 1.7, "end_seconds": 2.0},
        {"text": "that", "start_seconds": 2.0, "end_seconds": 2.2},
        {"text": "now", "start_seconds": 2.2, "end_seconds": 2.5},
    ]
    turns = [
        {"speaker": "speaker_2", "start_seconds": 0.0, "end_seconds": 0.7},
        {"speaker": "speaker_3", "start_seconds": 0.7, "end_seconds": 1.1},
        {"speaker": "speaker_2", "start_seconds": 1.1, "end_seconds": 2.5},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text=(
            "Compute bound right? And the industry feels that now."
        ),
    )

    assert {word["speaker"] for word in result["words"]} == {"speaker_2"}
    assert result["decisions"][0]["rule"] == (
        "untranscribed_backchannel_bleed"
    )
    assert result["decisions"][0]["text"] == "right And"
    assert [
        (word["text"], word["start_seconds"], word["end_seconds"])
        for word in result["words"]
    ] == [
        (word["text"], word["start_seconds"], word["end_seconds"])
        for word in words
    ]


def test_pipeline_v7_policy_preserves_pre_v8_boundary_behavior() -> None:
    words = [
        {"text": "keep", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "And", "start_seconds": 0.4, "end_seconds": 0.7},
        {"text": "going", "start_seconds": 0.7, "end_seconds": 1.1},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.4},
        {"speaker": "speaker-b", "start_seconds": 0.4, "end_seconds": 0.7},
        {"speaker": "speaker-a", "start_seconds": 0.7, "end_seconds": 1.1},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="Keep omitted And going.",
        policy=LEGACY_SPEAKER_BOUNDARY_POLICY,
    )

    assert result["summary"]["policy_version"] == (
        "speaker-boundary-stabilization-v1"
    )
    assert result["summary"]["punctuation_recovered"] is False
    assert [
        word["speaker"]
        for word in result["words"]
    ] == ["speaker-a", "speaker-b", "speaker-a"]


def test_run_8_single_like_is_a_short_discourse_continuation() -> None:
    words = [
        {"text": "interesting", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "Like", "start_seconds": 0.4, "end_seconds": 0.8},
        {"text": "people", "start_seconds": 0.8, "end_seconds": 1.1},
        {"text": "change", "start_seconds": 1.1, "end_seconds": 1.5},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.56},
        {"speaker": "speaker-b", "start_seconds": 0.44, "end_seconds": 0.76},
        {"speaker": "speaker-a", "start_seconds": 0.68, "end_seconds": 1.5},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="Interesting. Like people change.",
    )

    assert {word["speaker"] for word in result["words"]} == {"speaker-a"}
    assert result["decisions"][0]["rule"] == "short_discourse_continuation"


def test_historical_replay_repairs_confirmed_partial_evidence_case() -> None:
    segments = [
        {
            "segment_id": "seg-0127",
            "speaker_label": "speaker-a",
            "start_seconds": 0.0,
            "end_seconds": 0.8,
            "text": "can you slow down a",
            "words": [
                ("can", 0.0, 0.15),
                ("you", 0.15, 0.3),
                ("slow", 0.3, 0.5),
                ("down", 0.5, 0.7),
                ("a", 0.7, 0.8),
            ],
            "overlap": False,
        },
        {
            "segment_id": "seg-0128",
            "speaker_label": "speaker-b",
            "start_seconds": 0.8,
            "end_seconds": 1.0,
            "text": "bit",
            "words": [("bit", 0.8, 1.0)],
            "overlap": True,
        },
        {
            "segment_id": "seg-0129",
            "speaker_label": "speaker-a",
            "start_seconds": 1.0,
            "end_seconds": 1.4,
            "text": "I tried",
            "words": [("I", 1.0, 1.15), ("tried", 1.15, 1.4)],
            "overlap": False,
        },
    ]

    result = stabilize_existing_segments(segments)

    assert result["summary"]["partial_evidence"] is True
    assert result["summary"]["punctuation_recovered"] is False
    assert result["summary"]["corrected_count"] == 1
    assert result["decisions"][0]["source_segment_ids"] == ["seg-0128"]
    assert {word["speaker"] for word in result["words"]} == {"speaker-a"}
    assert [word["text"] for word in result["words"]] == [
        "can",
        "you",
        "slow",
        "down",
        "a",
        "bit",
        "I",
        "tried",
    ]


def test_historical_replay_repairs_confirmed_short_continuation() -> None:
    segments = [
        {
            "segment_id": "seg-0289",
            "speaker_label": "speaker-a",
            "start_seconds": 0.0,
            "end_seconds": 0.8,
            "text": "the scores have gotten wildly different",
            "words": [
                ("the", 0.0, 0.1),
                ("scores", 0.1, 0.2),
                ("have", 0.2, 0.3),
                ("gotten", 0.3, 0.45),
                ("wildly", 0.45, 0.6),
                ("different", 0.6, 0.8),
            ],
            "overlap": False,
        },
        {
            "segment_id": "seg-0290",
            "speaker_label": "speaker-b",
            "start_seconds": 0.8,
            "end_seconds": 1.2,
            "text": "So like",
            "words": [("So", 0.8, 1.0), ("like", 1.0, 1.2)],
            "overlap": True,
        },
        {
            "segment_id": "seg-0291",
            "speaker_label": "speaker-a",
            "start_seconds": 1.2,
            "end_seconds": 1.8,
            "text": "we need another test",
            "words": [
                ("we", 1.2, 1.35),
                ("need", 1.35, 1.5),
                ("another", 1.5, 1.65),
                ("test", 1.65, 1.8),
            ],
            "overlap": False,
        },
    ]

    result = stabilize_existing_segments(segments)

    assert result["summary"]["corrected_count"] == 1
    assert result["summary"]["unresolved_count"] == 0
    assert (
        result["decisions"][0]["rule"] == "short_discourse_continuation"
    )
    assert result["decisions"][0]["source_segment_ids"] == ["seg-0290"]
    assert {word["speaker"] for word in result["words"]} == {"speaker-a"}


def test_stabilizer_repairs_ambiguous_short_continuation() -> None:
    words = [
        {"text": "scores", "start_seconds": 0.0, "end_seconds": 0.45},
        {"text": "So", "start_seconds": 0.5, "end_seconds": 0.7},
        {"text": "like", "start_seconds": 0.7, "end_seconds": 0.9},
        {"text": "continue", "start_seconds": 0.95, "end_seconds": 1.2},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.66},
        {"speaker": "speaker-b", "start_seconds": 0.46, "end_seconds": 0.94},
        {"speaker": "speaker-a", "start_seconds": 0.74, "end_seconds": 1.3},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="Scores so like continue.",
    )

    assert result["summary"]["corrected_count"] == 1
    assert result["summary"]["unresolved_count"] == 0
    assert result["decisions"][0]["rule"] == "short_discourse_continuation"
    assert {word["speaker"] for word in result["words"]} == {"speaker-a"}


def test_stabilizer_does_not_reassign_clear_short_continuation() -> None:
    words = [
        {"text": "scores", "start_seconds": 0.0, "end_seconds": 0.45},
        {"text": "So", "start_seconds": 0.5, "end_seconds": 0.7},
        {"text": "like", "start_seconds": 0.7, "end_seconds": 0.9},
        {"text": "continue", "start_seconds": 0.95, "end_seconds": 1.2},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.52},
        {"speaker": "speaker-b", "start_seconds": 0.48, "end_seconds": 0.92},
        {"speaker": "speaker-a", "start_seconds": 0.88, "end_seconds": 1.3},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="Scores so like continue.",
    )

    assert result["summary"]["corrected_count"] == 0
    assert result["summary"]["unresolved_count"] == 1
    assert result["decisions"][0]["rule"] == "ambiguous_short_a_b_a"
    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == ["speaker-a", "speaker-b", "speaker-a"]


def test_historical_replay_does_not_guess_without_boundary_evidence() -> None:
    segments = [
        {
            "segment_id": "seg-0002",
            "speaker_label": "speaker-a",
            "start_seconds": 0.0,
            "end_seconds": 0.4,
            "text": "I have things to share What do",
            "words": [
                ("share", 0.0, 0.2),
                ("What", 0.2, 0.3),
                ("do", 0.3, 0.4),
            ],
            "overlap": False,
        },
        {
            "segment_id": "seg-0003",
            "speaker_label": "speaker-b",
            "start_seconds": 0.4,
            "end_seconds": 2.0,
            "text": "you want to say to your mothers right now",
            "words": [
                ("you", 0.4, 0.55),
                ("want", 0.55, 0.7),
                ("to", 0.7, 0.85),
                ("say", 0.85, 1.0),
                ("to", 1.0, 1.15),
                ("your", 1.15, 1.3),
                ("mothers", 1.3, 1.55),
                ("right", 1.55, 1.75),
                ("now", 1.75, 2.0),
            ],
            "overlap": True,
        },
        {
            "segment_id": "seg-0004",
            "speaker_label": "speaker-a",
            "start_seconds": 2.0,
            "end_seconds": 2.4,
            "text": "Do you",
            "words": [("Do", 2.0, 2.2), ("you", 2.2, 2.4)],
            "overlap": False,
        },
    ]

    result = stabilize_existing_segments(segments)

    assert result["summary"]["corrected_count"] == 0
    assert result["summary"]["unresolved_count"] == 0
    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == ["speaker-a", "speaker-b", "speaker-a"]
    assert [
        word["speaker"]
        for word in result["words"]
        if word["text"] in {"What", "do"}
    ] == ["speaker-a", "speaker-a"]


def test_stabilizer_repairs_a_multiword_mid_sentence_fragment() -> None:
    words = [
        {"text": "we", "start_seconds": 0.0, "end_seconds": 0.2},
        {"text": "should", "start_seconds": 0.2, "end_seconds": 0.4},
        {"text": "really", "start_seconds": 0.4, "end_seconds": 0.6},
        {"text": "change", "start_seconds": 0.6, "end_seconds": 0.8},
        {"text": "the", "start_seconds": 0.8, "end_seconds": 1.0},
        {"text": "plan", "start_seconds": 1.0, "end_seconds": 1.2},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 0.52},
        {"speaker": "speaker-b", "start_seconds": 0.42, "end_seconds": 0.76},
        {"speaker": "speaker-a", "start_seconds": 0.68, "end_seconds": 1.2},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="We should really change the plan.",
    )

    assert result["summary"]["words_reassigned"] == 2
    assert {word["speaker"] for word in result["words"]} == {"speaker-a"}


def test_stabilizer_moves_a_short_sentence_start_across_a_boundary() -> None:
    words = [
        {"text": "I", "start_seconds": 0.0, "end_seconds": 0.2},
        {"text": "agree", "start_seconds": 0.2, "end_seconds": 0.6},
        {"text": "What", "start_seconds": 0.8, "end_seconds": 1.0},
        {"text": "do", "start_seconds": 1.0, "end_seconds": 1.2},
        {"text": "you", "start_seconds": 1.2, "end_seconds": 1.4},
        {"text": "think", "start_seconds": 1.4, "end_seconds": 1.6},
    ]
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 1.2},
        {"speaker": "speaker-b", "start_seconds": 0.8, "end_seconds": 1.6},
    ]

    result = stabilize_speaker_boundaries(
        words,
        turns,
        transcript_text="I agree. What do you think?",
    )

    assert [segment["text"] for segment in result["segments"]] == [
        "I agree.",
        "What do you think?",
    ]
    assert [
        segment["speaker_label"] for segment in result["segments"]
    ] == ["speaker-a", "speaker-b"]
    assert result["decisions"][0]["rule"] == "short_sentence_tail"
    assert result["summary"]["words_reassigned"] == 2


def test_cross_speaker_repetition_is_reported_without_deletion() -> None:
    segments = [
        {
            "segment_id": "seg-0001",
            "speaker_label": "speaker-a",
            "text": "we should take that back",
        },
        {
            "segment_id": "seg-0002",
            "speaker_label": "speaker-b",
            "text": "take that back tomorrow",
        },
    ]

    findings = detect_cross_speaker_repetitions(segments)

    assert findings == [
        {
            "kind": "cross_speaker_repetition",
            "segment_ids": ["seg-0001", "seg-0002"],
            "text": "take that back",
            "word_count": 3,
        }
    ]
    assert segments[0]["text"] == "we should take that back"
    assert segments[1]["text"] == "take that back tomorrow"


def test_similar_local_speaker_embeddings_only_create_a_finding() -> None:
    embeddings = {
        "speaker-a": [1.0, 0.0],
        "speaker-b": [0.99, 0.01],
        "speaker-c": [0.0, 1.0],
    }

    findings = detect_speaker_identity_drift(
        embeddings,
        minimum_similarity=0.7,
    )

    assert findings == [
        {
            "kind": "speaker_identity_drift",
            "speaker_labels": ["speaker-a", "speaker-b"],
            "similarity": 0.9999,
        }
    ]
    assert set(embeddings) == {"speaker-a", "speaker-b", "speaker-c"}


def test_short_episode_local_speaker_participation_is_reported() -> None:
    segments = [
        {
            "speaker_label": "speaker-a",
            "start_seconds": 0.0,
            "end_seconds": 10.0,
            "text": "A sustained main Speaker turn with enough words",
            "words": [
                (f"word-{index}", float(index), float(index + 1))
                for index in range(10)
            ],
        },
        {
            "speaker_label": "speaker-drift",
            "start_seconds": 10.0,
            "end_seconds": 11.44,
            "text": "one two three four five six",
            "words": [
                ("one", 10.0, 10.2),
                ("two", 10.2, 10.4),
                ("three", 10.4, 10.7),
                ("four", 10.7, 10.9),
                ("five", 10.9, 11.1),
                ("six", 11.1, 11.44),
            ],
        },
    ]

    findings = detect_short_speaker_participation(segments)

    assert findings == [
        {
            "kind": "short_speaker_participation",
            "speaker_label": "speaker-drift",
            "word_count": 6,
            "duration_seconds": 1.44,
        }
    ]
