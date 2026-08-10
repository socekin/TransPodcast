from __future__ import annotations

import math

from transpodcast.speaker_consolidation import (
    CONSOLIDATION_POLICY_VERSION,
    REFERENCE_SELECTION_POLICY_VERSION,
    ConsolidationPolicy,
    ConsolidationWindow,
    consolidate_episode_speakers,
    select_voice_references,
)


def _exam_windows() -> list[ConsolidationWindow]:
    return [
        _window("intro-a-1", "speaker_0", 0.01, start=0.0),
        _window("intro-a-2", "speaker_0", 0.03, start=8.0),
        _window("intro-a-3", "speaker_0", 0.05, start=16.0),
        _window("main-a-1", "speaker_2", 0.00, start=30.0, duration=12.0),
        _window("main-a-2", "speaker_2", 0.02, start=50.0, duration=12.0),
        _window("main-a-3", "speaker_2", 0.04, start=70.0, duration=12.0),
        _window("intro-b-1", "speaker_1", 2.01, start=90.0),
        _window("intro-b-2", "speaker_1", 2.03, start=98.0),
        _window("intro-b-3", "speaker_1", 2.05, start=106.0),
        _window("main-b-1", "speaker_3", 2.00, start=120.0, duration=12.0),
        _window("main-b-2", "speaker_3", 2.02, start=140.0, duration=12.0),
        _window("main-b-3", "speaker_3", 2.04, start=160.0, duration=12.0),
    ]


def _vector(angle: float) -> tuple[float, ...]:
    return (math.cos(angle), math.sin(angle))


def _window(
    window_id: str,
    label: str,
    angle: float,
    *,
    start: float = 100.0,
    duration: float = 6.0,
    clean: bool = True,
) -> ConsolidationWindow:
    return ConsolidationWindow(
        window_id=window_id,
        speaker_label=label,
        start_seconds=start,
        end_seconds=start + duration,
        clean=clean,
        embedding=_vector(angle),
    )


def _labels(result) -> dict[str, str]:
    return result.mapping


# --- Synthetic regression target ---


def test_synthetic_windows_consolidate_four_labels_into_two() -> None:
    result = consolidate_episode_speakers(_exam_windows())

    assert result.mapping == {
        "speaker_0": "speaker_2",
        "speaker_1": "speaker_3",
        "speaker_2": "speaker_2",
        "speaker_3": "speaker_3",
    }
    verdicts = {
        record.subject["source_label"]: record.verdict
        for record in result.records
        if record.kind == "consolidation"
    }
    assert verdicts["speaker_0"] == "merged"
    assert verdicts["speaker_1"] == "merged"
    assert verdicts["speaker_2"] == "kept"
    assert verdicts["speaker_3"] == "kept"
    for record in result.records:
        assert record.policy_version == CONSOLIDATION_POLICY_VERSION
        assert record.rationale


def test_single_outlier_window_cannot_veto_a_merge() -> None:
    # Median-based evidence must survive one contaminated outlier.
    windows = [
        _window("a-1", "speaker_0", 0.02),
        _window("a-2", "speaker_0", 0.05),
        _window("a-3", "speaker_0", 0.9),  # contaminated but same vote
        _window("b-1", "speaker_1", 0.0, duration=10.0),
        _window("b-2", "speaker_1", 0.03, duration=10.0),
        _window("b-3", "speaker_1", 0.06, duration=10.0),
        _window("c-1", "speaker_2", 2.0, duration=12.0),
        _window("c-2", "speaker_2", 2.03, duration=12.0),
        _window("c-3", "speaker_2", 2.06, duration=12.0),
    ]

    result = consolidate_episode_speakers(windows)

    assert _labels(result)["speaker_0"] == "speaker_1"
    assert _labels(result)["speaker_2"] == "speaker_2"


def test_split_votes_keep_the_label() -> None:
    windows = [
        _window("a-1", "speaker_0", 0.0),
        _window("a-2", "speaker_0", 0.05),
        _window("a-3", "speaker_0", 1.95),  # votes speaker_2 instead
        _window("b-1", "speaker_1", 0.0, duration=10.0),
        _window("b-2", "speaker_1", 0.05, duration=10.0),
        _window("b-3", "speaker_1", 0.1, duration=10.0),
        _window("c-1", "speaker_2", 2.0, duration=12.0),
        _window("c-2", "speaker_2", 2.05, duration=12.0),
        _window("c-3", "speaker_2", 2.1, duration=12.0),
    ]

    result = consolidate_episode_speakers(windows)

    assert _labels(result)["speaker_0"] == "speaker_0"
    kept = next(
        record
        for record in result.records
        if record.subject["source_label"] == "speaker_0"
    )
    assert kept.verdict == "kept"
    assert "unanimous" in kept.rationale.lower() or "vote" in (
        kept.rationale.lower()
    )


def test_median_below_threshold_keeps_the_label() -> None:
    # All windows agree on the winner, but none of them are close enough.
    windows = [
        _window("a-1", "speaker_0", 0.9),
        _window("a-2", "speaker_0", 0.95),
        _window("a-3", "speaker_0", 1.0),
        _window("b-1", "speaker_1", 0.0, duration=10.0),
        _window("b-2", "speaker_1", 0.05, duration=10.0),
        _window("b-3", "speaker_1", 0.1, duration=10.0),
        _window("c-1", "speaker_2", 2.4, duration=12.0),
        _window("c-2", "speaker_2", 2.45, duration=12.0),
        _window("c-3", "speaker_2", 2.5, duration=12.0),
    ]

    result = consolidate_episode_speakers(windows)

    assert _labels(result)["speaker_0"] == "speaker_0"


def test_too_few_screened_windows_keep_the_label() -> None:
    windows = [
        _window("a-1", "speaker_0", 0.0),
        _window("a-2", "speaker_0", 0.05, clean=False),
        _window("a-3", "speaker_0", 0.02, duration=1.0),
        _window("b-1", "speaker_1", 0.0, duration=10.0),
        _window("b-2", "speaker_1", 0.05, duration=10.0),
        _window("b-3", "speaker_1", 0.1, duration=10.0),
    ]

    result = consolidate_episode_speakers(windows)

    assert _labels(result)["speaker_0"] == "speaker_0"
    kept = next(
        record
        for record in result.records
        if record.subject["source_label"] == "speaker_0"
    )
    assert kept.verdict == "kept"
    screened_out = kept.evidence["screened_out_windows"]
    assert {item["window_id"] for item in screened_out} == {
        "a-2",
        "a-3",
    }


def test_an_absorbing_label_is_frozen_against_chained_merges() -> None:
    # A is similar to B (0.85) and B to C (0.85), but A and C are NOT
    # similar (0.45). Without freezing, A->B then B->C would resolve
    # A to C without ever comparing them — a transitive false merge.
    windows = [
        _window("a-1", "speaker_0", 0.0, duration=4.0),
        _window("a-2", "speaker_0", 0.02, duration=4.0),
        _window("a-3", "speaker_0", 0.04, duration=4.0),
        _window("b-1", "speaker_1", 0.55, duration=8.0),
        _window("b-2", "speaker_1", 0.57, duration=8.0),
        _window("b-3", "speaker_1", 0.59, duration=8.0),
        _window("c-1", "speaker_2", 1.10, duration=12.0),
        _window("c-2", "speaker_2", 1.12, duration=12.0),
        _window("c-3", "speaker_2", 1.14, duration=12.0),
    ]

    result = consolidate_episode_speakers(windows)

    assert _labels(result) == {
        "speaker_0": "speaker_1",
        "speaker_1": "speaker_1",
        "speaker_2": "speaker_2",
    }
    frozen = next(
        record
        for record in result.records
        if record.subject["source_label"] == "speaker_1"
    )
    assert frozen.verdict == "kept"
    assert "frozen" in frozen.rationale.lower()


def test_a_genuinely_distinct_third_speaker_is_never_forced() -> None:
    windows = [
        _window("a-1", "speaker_0", 0.0, duration=12.0),
        _window("a-2", "speaker_0", 0.05, duration=12.0),
        _window("a-3", "speaker_0", 0.1, duration=12.0),
        _window("b-1", "speaker_1", 2.0, duration=10.0),
        _window("b-2", "speaker_1", 2.05, duration=10.0),
        _window("b-3", "speaker_1", 2.1, duration=10.0),
        _window("c-1", "speaker_2", 4.0, duration=8.0),
        _window("c-2", "speaker_2", 4.05, duration=8.0),
        _window("c-3", "speaker_2", 4.1, duration=8.0),
    ]

    result = consolidate_episode_speakers(windows)

    assert _labels(result) == {
        "speaker_0": "speaker_0",
        "speaker_1": "speaker_1",
        "speaker_2": "speaker_2",
    }


def test_policy_thresholds_are_recorded_in_evidence() -> None:
    result = consolidate_episode_speakers(
        _exam_windows(),
        policy=ConsolidationPolicy(),
    )

    merged = next(
        record
        for record in result.records
        if record.subject["source_label"] == "speaker_0"
    )
    assert merged.evidence["median_similarity"] >= 0.65
    assert merged.evidence["margin"] > 0.0
    assert merged.evidence["window_votes"]


# --- Voice Reference selection ---


def _turn(speaker: str, start: float, end: float) -> dict:
    return {
        "speaker": speaker,
        "start_seconds": start,
        "end_seconds": end,
    }


def test_selection_prefers_the_longest_mid_episode_turn() -> None:
    turns = [
        _turn("speaker_2", 30.0, 38.0),
        _turn("speaker_2", 200.0, 216.0),
        _turn("speaker_3", 100.0, 109.0),
    ]

    result = select_voice_references(
        turns,
        source_duration_seconds=4000.0,
    )

    spans = result.selections
    assert spans["speaker_2"]["start_seconds"] == 200.0
    assert spans["speaker_2"]["duration_seconds"] == 12.0
    assert spans["speaker_3"]["start_seconds"] == 100.0
    assert spans["speaker_3"]["duration_seconds"] == 9.0
    for record in result.records:
        assert (
            record.policy_version == REFERENCE_SELECTION_POLICY_VERSION
        )


def test_selection_avoids_the_outro_zone() -> None:
    # The v8 defect: speaker_3's reference landed at 66:00, beside the
    # outro music, because position was never measured.
    turns = [
        _turn("speaker_3", 212.0, 225.0),
        _turn("speaker_3", 3960.4, 3967.7),
    ]

    result = select_voice_references(
        turns,
        source_duration_seconds=3989.3,
    )

    assert result.selections["speaker_3"]["start_seconds"] == 212.0


def test_selection_avoids_the_intro_zone() -> None:
    turns = [
        _turn("speaker_0", 0.0, 7.8),
        _turn("speaker_0", 120.0, 126.0),
    ]

    result = select_voice_references(
        turns,
        source_duration_seconds=4000.0,
    )

    assert result.selections["speaker_0"]["start_seconds"] == 120.0


def test_selection_rejects_turns_overlapping_another_speaker() -> None:
    turns = [
        _turn("speaker_2", 200.0, 214.0),
        _turn("speaker_3", 205.0, 210.0),
        _turn("speaker_2", 300.0, 308.0),
    ]

    result = select_voice_references(
        turns,
        source_duration_seconds=4000.0,
    )

    assert result.selections["speaker_2"]["start_seconds"] == 300.0


def test_selection_falls_back_to_the_plain_v6_rule_and_logs_it() -> None:
    # Every turn for this speaker is inside the intro zone, so filtered
    # selection is impossible; the plain v6 rule must still produce a
    # reference and the fallback must be recorded.
    turns = [
        _turn("speaker_1", 7.8, 19.8),
        _turn("speaker_1", 22.8, 28.2),
    ]

    result = select_voice_references(
        turns,
        source_duration_seconds=4000.0,
    )

    assert result.selections["speaker_1"]["start_seconds"] == 7.8
    fallback = next(
        record
        for record in result.records
        if record.kind == "fallback"
    )
    assert fallback.subject["speaker"] == "speaker_1"
