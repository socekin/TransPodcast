from __future__ import annotations

import math
from array import array

import pytest

from transpodcast.pipeline_versions import (
    CONTINUITY_PIPELINES,
    CURRENT_SPEECH_PIPELINE_VERSION,
    RHYTHM_PIPELINES,
    SPEECH_PIPELINE_V15,
    SPEECH_PIPELINE_V16,
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V18,
    SPEECH_PIPELINE_V19,
    SPEECH_PIPELINE_V20,
    WORD_OWNERSHIP_PIPELINES,
    analysis_version_for_pipeline,
    pipeline_version_for_speech_model,
    speech_model_for_pipeline_version,
)
from transpodcast.rhythm import (
    RHYTHM_POLICY,
    RHYTHM_POLICY_VERSION,
    RhythmBoundary,
    apply_fades,
    cap_internal_silences,
    classify_boundary,
    crosses_cut,
    inserted_silence_seconds,
    trim_window,
)

RATE = 48_000


def _signal(*parts: tuple[float, float]) -> array[float]:
    """Build a clip from (seconds, amplitude) parts; amplitude 0 is silence."""
    samples = array("f")
    for seconds, amplitude in parts:
        count = round(seconds * RATE)
        samples.extend(
            amplitude * math.sin(2 * math.pi * 220 * index / RATE)
            for index in range(count)
        )
    return samples


def test_rhythm_versions_map_to_renderers_and_v17_source_analysis() -> None:
    assert SPEECH_PIPELINE_V19 == "transpodcast-modal-pipeline-v19"
    assert SPEECH_PIPELINE_V20 == "transpodcast-modal-pipeline-v20"
    assert {SPEECH_PIPELINE_V19, SPEECH_PIPELINE_V20} <= RHYTHM_PIPELINES
    assert speech_model_for_pipeline_version(SPEECH_PIPELINE_V19) == "indextts2"
    assert speech_model_for_pipeline_version(SPEECH_PIPELINE_V20) == "indextts25"
    assert analysis_version_for_pipeline(SPEECH_PIPELINE_V19) == SPEECH_PIPELINE_V17
    assert analysis_version_for_pipeline(SPEECH_PIPELINE_V20) == SPEECH_PIPELINE_V17
    assert RHYTHM_PIPELINES <= CONTINUITY_PIPELINES <= WORD_OWNERSHIP_PIPELINES


def test_new_runs_select_rhythm_pipelines() -> None:
    assert pipeline_version_for_speech_model("indextts2") in RHYTHM_PIPELINES
    assert pipeline_version_for_speech_model("indextts25") in RHYTHM_PIPELINES
    assert CURRENT_SPEECH_PIPELINE_VERSION in RHYTHM_PIPELINES
    assert not RHYTHM_PIPELINES & {
        SPEECH_PIPELINE_V15,
        SPEECH_PIPELINE_V16,
        SPEECH_PIPELINE_V17,
        SPEECH_PIPELINE_V18,
    }


def test_policy_is_versioned_with_the_calibrated_values() -> None:
    assert RHYTHM_POLICY.version == RHYTHM_POLICY_VERSION == "rhythm-policy-v2"
    assert RHYTHM_POLICY.head_keep_seconds == 0.04
    assert RHYTHM_POLICY.tail_keep_seconds == 0.06
    assert RHYTHM_POLICY.speaker_change_min_seconds == 0.25
    assert RHYTHM_POLICY.speaker_change_max_seconds == 1.2
    assert RHYTHM_POLICY.same_speaker_max_seconds == 1.0
    assert RHYTHM_POLICY.overlap_change_seconds == 0.08
    assert RHYTHM_POLICY.cut_pause_seconds == 0.8
    assert RHYTHM_POLICY.to_payload()["version"] == "rhythm-policy-v2"


@pytest.mark.parametrize(
    ("gap", "speaker_change", "crosses", "expected"),
    [
        # Same Speaker: clamp(g, same_min, 1.0).
        (0.32, False, False, 0.32),
        (0.02, False, False, RHYTHM_POLICY.same_speaker_min_seconds),
        (-0.2, False, False, RHYTHM_POLICY.same_speaker_min_seconds),
        (1.7, False, False, 1.0),
        (4.5, False, False, 1.0),
        # Speaker change: clamp(g, 0.25, 1.2).
        (0.24, True, False, 0.25),
        (0.6, True, False, 0.6),
        (2.0, True, False, 1.2),
        (8.0, True, False, 1.2),
        # Overlap at a Speaker change.
        (-0.3, True, False, 0.08),
        # A Content Edit removal always becomes the fixed cut transition.
        (0.1, True, True, 0.8),
        (45.0, False, True, 0.8),
    ],
)
def test_pause_mapping(
    gap: float,
    speaker_change: bool,
    crosses: bool,
    expected: float,
) -> None:
    boundary = RhythmBoundary(
        source_gap_seconds=gap,
        speaker_change=speaker_change,
        crosses_cut=crosses,
    )

    assert RHYTHM_POLICY.pause_seconds(boundary) == pytest.approx(expected)


def test_cut_boundary_is_a_removal_that_starts_between_two_units() -> None:
    exclusions = [(10.0, 40.0)]

    assert crosses_cut(9.5, 40.2, exclusions)
    assert crosses_cut(10.0, 40.2, exclusions)
    assert not crosses_cut(40.5, 41.0, exclusions)
    assert not crosses_cut(2.0, 3.0, exclusions)
    boundary = classify_boundary(
        previous_end_seconds=9.5,
        previous_speaker="speaker-01",
        next_start_seconds=40.2,
        next_speaker="speaker-01",
        exclusions=exclusions,
    )
    assert boundary == RhythmBoundary(
        source_gap_seconds=pytest.approx(30.7),
        speaker_change=False,
        crosses_cut=True,
    )


def test_inserted_silence_subtracts_the_kept_clip_edges() -> None:
    assert inserted_silence_seconds(
        0.32,
        previous_tail_seconds=0.06,
        next_head_seconds=0.04,
    ) == pytest.approx(0.22)
    assert (
        inserted_silence_seconds(
            0.08,
            previous_tail_seconds=0.06,
            next_head_seconds=0.04,
        )
        == 0.0
    )


@pytest.mark.parametrize(
    ("source", "rendered", "expected"),
    [
        (1.0, 1.5, 1.5 / 1.4),
        (0.5, 2.0, 1.25),
        (1.0, 1.42, 1.0),
        (1.0, 1.3, 1.0),
        (1.0, 0.5, 1.0),
        (1.6, 4.0, 1.0),
        (0.0, 1.0, 1.0),
    ],
)
def test_short_reply_tempo_is_bounded_and_never_slows_speech(
    source: float,
    rendered: float,
    expected: float,
) -> None:
    factor = RHYTHM_POLICY.tempo_factor(source, rendered)

    assert factor == pytest.approx(expected)
    assert 1.0 <= factor <= RHYTHM_POLICY.maximum_tempo


def test_trim_keeps_the_policy_head_and_tail_around_speech() -> None:
    clip = _signal((0.3, 0.0), (0.5, 0.3), (0.4, 0.0))

    window = trim_window(clip, RATE)

    assert window.voiced
    assert window.start == round(0.26 * RATE)
    assert window.end == round(0.86 * RATE)
    assert window.head_silence == round(0.04 * RATE)
    assert window.tail_silence == round(0.06 * RATE)


def test_trim_ignores_a_low_noise_floor_in_the_padding() -> None:
    clip = _signal((0.3, 0.001), (0.5, 0.3), (0.4, 0.001))

    window = trim_window(clip, RATE)

    assert window.start == round(0.26 * RATE)
    assert window.end == round(0.86 * RATE)


def test_trim_cannot_keep_silence_the_clip_does_not_have() -> None:
    clip = _signal((0.5, 0.3), (0.02, 0.0))

    window = trim_window(clip, RATE)

    assert window.start == 0
    assert window.head_silence == 0
    assert window.end == len(clip)
    assert window.tail_silence == round(0.02 * RATE)


def test_trim_keeps_a_silent_clip_unchanged() -> None:
    clip = array("f", bytes(4 * RATE))

    window = trim_window(clip, RATE)

    assert not window.voiced
    assert (window.start, window.end) == (0, len(clip))


def test_fades_ramp_both_edges_and_never_exceed_half_the_clip() -> None:
    samples = array("f", [1.0] * 2000)

    apply_fades(samples, fade_in=480, fade_out=1440)

    assert samples[0] == 0.0
    assert samples[-1] == 0.0
    assert samples[240] == pytest.approx(240 / 479)
    assert samples[479] == pytest.approx(1.0)
    assert samples[999] == 1.0
    assert samples[1001] < 1.0


def test_over_long_internal_silence_is_capped() -> None:
    # Owner spot-check: 1.8-2.2 s pauses inside a rendered sentence are defects.
    clip = _signal((0.5, 0.3), (2.0, 0.0), (0.5, 0.3))
    capped, removed = cap_internal_silences(clip, RATE)
    assert removed == (pytest.approx(1.2, abs=0.01),)
    assert len(capped) / RATE == pytest.approx(1.8, abs=0.01)


def test_natural_internal_pause_and_edges_are_kept() -> None:
    clip = _signal((1.0, 0.0), (0.5, 0.3), (0.6, 0.0), (0.5, 0.3), (1.0, 0.0))
    capped, removed = cap_internal_silences(clip, RATE)
    assert removed == ()
    assert capped is clip


def test_multiple_internal_silences_are_each_capped() -> None:
    clip = _signal(
        (0.3, 0.3),
        (1.5, 0.0),
        (0.3, 0.3),
        (0.5, 0.0),
        (0.3, 0.3),
        (3.0, 0.0),
        (0.3, 0.3),
    )
    capped, removed = cap_internal_silences(clip, RATE)
    assert removed == (
        pytest.approx(0.7, abs=0.01),
        pytest.approx(2.2, abs=0.01),
    )
    assert len(capped) / RATE == pytest.approx(len(clip) / RATE - 2.9, abs=0.01)
