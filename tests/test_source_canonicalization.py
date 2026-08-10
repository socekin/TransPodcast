from __future__ import annotations

import pytest

from transpodcast.source_canonicalization import (
    SourceCanonicalizationRequest,
    canonicalize_source_analysis,
    validate_canonical_word_invariants,
)


def _canonicalize(
    *,
    words: list[dict[str, object]],
    diarization: list[dict[str, object]],
    transcript_text: str,
    speaker_windows: list[dict[str, object]],
) -> dict[str, object]:
    return canonicalize_source_analysis(
        SourceCanonicalizationRequest.from_payloads(
            words=words,
            diarization=diarization,
            transcript_text=transcript_text,
            speaker_windows=speaker_windows,
        )
    ).to_payload()


def _window(
    window_id: str,
    label: str,
    embedding: tuple[float, ...],
    start: float,
    *,
    clean: bool = True,
    duration: float = 3.0,
) -> dict[str, object]:
    return {
        "window_id": window_id,
        "speaker_label": label,
        "start_seconds": start,
        "end_seconds": start + duration,
        "content_hash": f"hash-{window_id}",
        "embedding": list(embedding),
        "clean": clean,
    }


def test_cold_open_labels_consolidate_into_two_episode_speakers() -> None:
    turns = [
        {"speaker": "speaker_0", "start_seconds": 0.0, "end_seconds": 14.0},
        {"speaker": "speaker_1", "start_seconds": 14.0, "end_seconds": 35.0},
        {"speaker": "speaker_2", "start_seconds": 35.0, "end_seconds": 190.0},
        {"speaker": "speaker_3", "start_seconds": 190.0, "end_seconds": 420.0},
    ]
    windows = [
        _window("s0-1", "speaker_0", (0.99, 0.01), 0.0),
        _window("s0-2", "speaker_0", (0.98, 0.02), 3.0),
        _window("s0-3", "speaker_0", (0.97, 0.03), 82.0),
        _window("s1-1", "speaker_1", (0.02, 0.98), 14.0),
        _window("s1-2", "speaker_1", (0.01, 0.99), 18.0),
        _window("s1-3", "speaker_1", (0.03, 0.97), 22.0),
        _window("s2-1", "speaker_2", (1.0, 0.0), 35.0),
        _window("s2-2", "speaker_2", (0.99, 0.01), 65.0),
        _window("s2-3", "speaker_2", (0.98, 0.02), 181.0),
        _window("s3-1", "speaker_3", (0.0, 1.0), 213.0),
        _window("s3-2", "speaker_3", (0.01, 0.99), 331.0),
        _window("s3-3", "speaker_3", (0.02, 0.98), 388.0),
    ]

    result = _canonicalize(
        words=[],
        diarization=turns,
        transcript_text="",
        speaker_windows=windows,
    )

    assert result["speaker_mapping"] == {
        "speaker_0": "speaker_2",
        "speaker_1": "speaker_3",
        "speaker_2": "speaker_2",
        "speaker_3": "speaker_3",
    }
    assert [speaker["local_label"] for speaker in result["speakers"]] == [
        "speaker_2",
        "speaker_3",
    ]
    selected = {
        speaker["local_label"]: speaker["selected_reference_window_id"]
        for speaker in result["speakers"]
    }
    assert selected["speaker_2"] in {
        "s0-1",
        "s0-2",
        "s0-3",
        "s2-1",
        "s2-2",
        "s2-3",
    }
    assert selected["speaker_3"] in {
        "s1-1",
        "s1-2",
        "s1-3",
        "s3-1",
        "s3-2",
        "s3-3",
    }
    assert result["consolidation"]["summary"] == {
        "policy_version": "source-analysis-canonicalization-v1",
        "raw_label_count": 4,
        "episode_speaker_count": 2,
        "consolidated_label_count": 2,
        "unresolved_label_count": 0,
    }
    assert {
        decision["outcome"]
        for decision in result["consolidation"]["decisions"]
        if decision["source_label"] in {"speaker_0", "speaker_1"}
    } == {"consolidated"}


def test_short_discourse_continuation_uses_the_surrounding_speaker() -> None:
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
    windows = [
        _window("a", "speaker-a", (1.0, 0.0), 0.0),
        _window("b", "speaker-b", (0.0, 1.0), 0.0),
    ]

    result = _canonicalize(
        words=words,
        diarization=turns,
        transcript_text="Scores so like continue.",
        speaker_windows=windows,
    )

    assert {word["speaker"] for word in result["words"]} == {"speaker-a"}


def test_question_opening_stays_with_the_incoming_speaker() -> None:
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
    windows = [
        _window("a", "speaker-a", (1.0, 0.0), 0.0),
        _window("b", "speaker-b", (0.0, 1.0), 0.0),
    ]

    result = _canonicalize(
        words=words,
        diarization=turns,
        transcript_text="I agree. What do you think?",
        speaker_windows=windows,
    )

    assert [
        word["speaker"]
        for word in result["words"]
        if word["text"] in {"What", "do"}
    ] == ["speaker-b", "speaker-b"]


def test_conflicting_window_votes_preserve_a_separate_speaker() -> None:
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 100.0},
        {"speaker": "speaker-b", "start_seconds": 100.0, "end_seconds": 200.0},
        {"speaker": "speaker-c", "start_seconds": 200.0, "end_seconds": 210.0},
    ]
    windows = [
        _window("a-1", "speaker-a", (1.0, 0.0), 0.0),
        _window("a-2", "speaker-a", (0.99, 0.01), 20.0),
        _window("a-3", "speaker-a", (0.98, 0.02), 40.0),
        _window("b-1", "speaker-b", (0.0, 1.0), 100.0),
        _window("b-2", "speaker-b", (0.01, 0.99), 120.0),
        _window("b-3", "speaker-b", (0.02, 0.98), 140.0),
        _window("c-1", "speaker-c", (0.95, 0.05), 201.0),
        _window("c-2", "speaker-c", (0.05, 0.95), 205.0),
    ]

    result = _canonicalize(
        words=[],
        diarization=turns,
        transcript_text="",
        speaker_windows=windows,
    )

    assert result["speaker_mapping"]["speaker-c"] == "speaker-c"
    unresolved = next(
        decision
        for decision in result["consolidation"]["decisions"]
        if decision["source_label"] == "speaker-c"
    )
    assert unresolved["outcome"] == "preserved"
    assert unresolved["reason"] == "inconsistent_window_votes"
    assert result["consolidation"]["summary"]["unresolved_label_count"] == 1


def test_genuine_three_speaker_episode_is_not_forced_to_two() -> None:
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 100.0},
        {"speaker": "speaker-b", "start_seconds": 100.0, "end_seconds": 200.0},
        {"speaker": "speaker-c", "start_seconds": 200.0, "end_seconds": 300.0},
    ]
    windows = [
        _window(f"a-{index}", "speaker-a", (1.0, 0.0, 0.0), index * 4.0)
        for index in range(3)
    ] + [
        _window(f"b-{index}", "speaker-b", (0.0, 1.0, 0.0), 100 + index * 4.0)
        for index in range(3)
    ] + [
        _window(f"c-{index}", "speaker-c", (0.0, 0.0, 1.0), 200 + index * 4.0)
        for index in range(3)
    ]

    result = _canonicalize(
        words=[],
        diarization=turns,
        transcript_text="",
        speaker_windows=windows,
    )

    assert result["speaker_mapping"] == {
        "speaker-a": "speaker-a",
        "speaker-b": "speaker-b",
        "speaker-c": "speaker-c",
    }
    assert result["consolidation"]["summary"]["episode_speaker_count"] == 3


def test_voice_reference_prefers_clean_centroid_consistent_window() -> None:
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 60.0}
    ]
    windows = [
        _window("clean-1", "speaker-a", (1.0, 0.0), 0.0),
        _window("clean-2", "speaker-a", (0.99, 0.01), 10.0),
        _window(
            "overlapped-long",
            "speaker-a",
            (0.0, 1.0),
            20.0,
            clean=False,
            duration=12.0,
        ),
    ]

    result = _canonicalize(
        words=[],
        diarization=turns,
        transcript_text="",
        speaker_windows=windows,
    )

    assert result["speakers"][0]["selected_reference_window_id"] in {
        "clean-1",
        "clean-2",
    }


def test_fallback_window_remains_available_as_an_episode_reference() -> None:
    turns = [
        {"speaker": "speaker-a", "start_seconds": 0.0, "end_seconds": 10.0}
    ]
    windows = [
        _window(
            "fallback",
            "speaker-a",
            (1.0, 0.0),
            0.0,
            clean=False,
        )
    ]

    result = _canonicalize(
        words=[],
        diarization=turns,
        transcript_text="",
        speaker_windows=windows,
    )

    assert result["speaker_mapping"] == {"speaker-a": "speaker-a"}
    assert result["speakers"][0]["selected_reference_window_id"] == "fallback"


def test_one_dominant_speaker_cannot_supply_a_real_winning_margin() -> None:
    turns = [
        {"speaker": "host", "start_seconds": 0.0, "end_seconds": 200.0},
        {"speaker": "short-guest", "start_seconds": 200.0, "end_seconds": 210.0},
    ]
    windows = [
        _window(f"host-{index}", "host", (1.0, 0.0), index * 10.0)
        for index in range(3)
    ] + [
        _window("guest-1", "short-guest", (0.98, 0.02), 201.0),
        _window("guest-2", "short-guest", (0.97, 0.03), 205.0),
    ]

    result = _canonicalize(
        words=[],
        diarization=turns,
        transcript_text="",
        speaker_windows=windows,
    )

    assert result["speaker_mapping"]["short-guest"] == "short-guest"
    decision = next(
        item
        for item in result["consolidation"]["decisions"]
        if item["source_label"] == "short-guest"
    )
    assert decision["reason"] == "insufficient_dominant_comparison"


def test_consolidated_label_window_remains_voice_reference_eligible() -> None:
    turns = [
        {"speaker": "teaser", "start_seconds": 0.0, "end_seconds": 12.0},
        {"speaker": "host", "start_seconds": 12.0, "end_seconds": 150.0},
        {"speaker": "guest", "start_seconds": 150.0, "end_seconds": 300.0},
    ]
    windows = [
        _window(
            "teaser-best",
            "teaser",
            (1.0, 0.0),
            0.0,
            duration=12.0,
        ),
        _window("teaser-2", "teaser", (0.99, 0.01), 20.0),
        _window("host-1", "host", (1.0, 0.0), 40.0),
        _window("host-2", "host", (0.99, 0.01), 60.0),
        _window("host-3", "host", (0.98, 0.02), 80.0),
        _window("guest-1", "guest", (0.0, 1.0), 160.0),
        _window("guest-2", "guest", (0.01, 0.99), 180.0),
        _window("guest-3", "guest", (0.02, 0.98), 200.0),
    ]

    result = _canonicalize(
        words=[],
        diarization=turns,
        transcript_text="",
        speaker_windows=windows,
    )

    host = next(
        speaker
        for speaker in result["speakers"]
        if speaker["local_label"] == "host"
    )
    assert result["speaker_mapping"]["teaser"] == "host"
    assert host["selected_reference_window_id"] == "teaser-best"
    assert host["reference_selection"]["candidates"][0][
        "quality_score"
    ] == 1.0


def test_canonicalization_serialization_is_byte_equivalent_on_retry() -> None:
    request = SourceCanonicalizationRequest.from_payloads(
        words=[
            {
                "text": "Stable",
                "start_seconds": 0.0,
                "end_seconds": 0.5,
            }
        ],
        diarization=[
            {
                "speaker": "speaker-a",
                "start_seconds": 0.0,
                "end_seconds": 60.0,
            }
        ],
        transcript_text="Stable.",
        speaker_windows=[
            _window(f"a-{index}", "speaker-a", (1.0, 0.0), index * 10.0)
            for index in range(3)
        ],
    )

    first = canonicalize_source_analysis(request)
    second = canonicalize_source_analysis(request)

    assert first.payload_json == second.payload_json


@pytest.mark.parametrize(
    "mutation",
    [
        lambda words: words[:-1],
        lambda words: [words[0], words[0], *words[1:]],
        lambda words: list(reversed(words)),
        lambda words: [{**words[0], "text": "rewritten"}, *words[1:]],
        lambda words: [
            {**words[0], "start_seconds": 0.25},
            *words[1:],
        ],
    ],
    ids=["drop", "duplicate", "reorder", "rewrite", "retime"],
)
def test_canonical_word_invariants_reject_lexical_mutation(mutation) -> None:
    source_words = [
        {"text": "first", "start_seconds": 0.0, "end_seconds": 0.5},
        {"text": "second", "start_seconds": 0.5, "end_seconds": 1.0},
    ]

    with pytest.raises(
        ValueError,
        match="changed aligned lexical data",
    ):
        validate_canonical_word_invariants(
            source_words,
            mutation(source_words),
        )
