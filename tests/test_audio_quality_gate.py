import pytest

from transpodcast.domain import AudioQualityReport, SpeechResult
from transpodcast.service import TransPodcastService


@pytest.mark.parametrize("loudness_lufs", [-16.5, -15.5])
def test_quality_gate_allows_final_encoded_loudness_tolerance(
    loudness_lufs: float,
) -> None:
    speech = SpeechResult(
        publication_audio=b"M4A!publication-audio",
        duration_seconds=60,
        chapters=((0, "Part 1"),),
        pipeline_version="test-pipeline-v1",
        quality=AudioQualityReport(
            loudness_lufs=loudness_lufs,
            rendered_segments=1,
        ),
        segment_timings=((0.0, 60.0),),
    )

    TransPodcastService._validate_speech(speech, expected_segments=1)


@pytest.mark.parametrize("loudness_lufs", [-16.51, -15.49])
def test_quality_gate_rejects_final_encoded_loudness_outside_tolerance(
    loudness_lufs: float,
) -> None:
    speech = SpeechResult(
        publication_audio=b"M4A!publication-audio",
        duration_seconds=60,
        chapters=((0, "Part 1"),),
        pipeline_version="test-pipeline-v1",
        quality=AudioQualityReport(
            loudness_lufs=loudness_lufs,
            rendered_segments=1,
        ),
        segment_timings=((0.0, 60.0),),
    )

    with pytest.raises(
        ValueError,
        match="Publication Audio failed deterministic checks",
    ):
        TransPodcastService._validate_speech(speech, expected_segments=1)


def test_quality_gate_allows_safe_final_encoded_true_peak() -> None:
    speech = SpeechResult(
        publication_audio=b"M4A!publication-audio",
        duration_seconds=60,
        chapters=((0, "Part 1"),),
        pipeline_version="test-pipeline-v1",
        quality=AudioQualityReport(
            true_peak_dbtp=-1.0,
            rendered_segments=1,
        ),
        segment_timings=((0.0, 60.0),),
    )

    TransPodcastService._validate_speech(speech, expected_segments=1)


def test_quality_gate_rejects_final_encoded_true_peak_without_headroom() -> None:
    speech = SpeechResult(
        publication_audio=b"M4A!publication-audio",
        duration_seconds=60,
        chapters=((0, "Part 1"),),
        pipeline_version="test-pipeline-v1",
        quality=AudioQualityReport(
            true_peak_dbtp=-0.99,
            rendered_segments=1,
        ),
        segment_timings=((0.0, 60.0),),
    )

    with pytest.raises(
        ValueError,
        match="Publication Audio failed deterministic checks",
    ):
        TransPodcastService._validate_speech(speech, expected_segments=1)


@pytest.mark.parametrize(
    "timings",
    [
        (),
        ((0.0, 30.0), (29.0, 60.0)),
        ((0.0, 61.1),),
        ((10.0, 9.0),),
    ],
)
def test_quality_gate_rejects_invalid_render_plan_timings(
    timings: tuple[tuple[float, float], ...],
) -> None:
    speech = SpeechResult(
        publication_audio=b"M4A!publication-audio",
        duration_seconds=60,
        chapters=((0, "Part 1"),),
        pipeline_version="test-pipeline-v1",
        quality=AudioQualityReport(rendered_segments=1),
        segment_timings=timings,
    )

    with pytest.raises(
        ValueError,
        match="Publication Audio failed deterministic checks",
    ):
        TransPodcastService._validate_speech(speech, expected_segments=1)
