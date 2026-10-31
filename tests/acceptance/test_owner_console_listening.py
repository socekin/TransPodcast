from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import (
    AudioQualityReport,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.testing import AcceptanceHarness

RENDERED_AUDIO = b"M4A!clipping-publication-audio"


class ClippingSpeechAdapter:
    """Renders audio that always fails the deterministic Quality Gate."""

    def readiness(self) -> bool:
        return True

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        return SpeechResult(
            publication_audio=RENDERED_AUDIO,
            duration_seconds=3600,
            chapters=((0, "Introduction"),),
            pipeline_version="clipping-fixture-v1",
            quality=AudioQualityReport(
                loudness_lufs=-13.0,
                true_peak_dbtp=-0.6,
                clipping=True,
                rendered_segments=len(translation.segments),
            ),
            segment_timings=tuple(
                (float(index) * 2, float(index) * 2 + 1.5)
                for index in range(len(translation.segments))
            ),
        )


def _review_queue_run(tmp_path) -> tuple[TestClient, int]:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(harness.dependencies, speech=ClippingSpeechAdapter())
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    failed = client.post("/api/v1/processing-runs:advance").json()
    assert failed["processing_state"] == "review_queue"
    return client, int(failed["processing_run_id"])


def test_the_owner_listens_to_a_review_queue_run_before_an_episode_exists(
    tmp_path,
) -> None:
    client, run_id = _review_queue_run(tmp_path)

    whole = client.get(f"/api/v1/processing-runs/{run_id}/rendered-audio")
    ranged = client.get(
        f"/api/v1/processing-runs/{run_id}/rendered-audio",
        headers={"Range": "bytes=0-3"},
    )

    assert whole.status_code == 200
    assert whole.headers["content-type"] == "audio/mp4"
    assert whole.content == RENDERED_AUDIO
    assert ranged.status_code == 206
    assert ranged.content == RENDERED_AUDIO[:4]


def test_the_transcript_places_each_line_in_the_rendered_audio(
    tmp_path,
) -> None:
    client, run_id = _review_queue_run(tmp_path)

    transcript = client.get(f"/api/v1/processing-runs/{run_id}/transcript")

    assert transcript.status_code == 200
    lines = transcript.json()["lines"]
    assert lines
    first = lines[0]
    assert first["ordinal"] == 1
    assert first["segment_id"]
    assert first["speaker_label"]
    assert first["source_text"]
    assert first["target_text"]
    assert first["output_start_seconds"] == 0.0
    assert first["output_end_seconds"] == 1.5
    assert [line["ordinal"] for line in lines] == list(
        range(1, len(lines) + 1)
    )


def test_a_queued_run_has_no_transcript_or_rendered_audio_yet(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    (queued, *_rest) = client.get("/api/v1/processing-queue").json()["items"]
    run_id = queued["processing_run_id"]

    transcript = client.get(f"/api/v1/processing-runs/{run_id}/transcript")
    audio = client.get(f"/api/v1/processing-runs/{run_id}/rendered-audio")

    assert transcript.json() == {"processing_run_id": run_id, "lines": []}
    assert audio.status_code == 404
    assert client.get("/api/v1/processing-runs/999/transcript").status_code == 404


def test_episode_detail_names_the_run_whose_transcript_it_plays(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    completed = client.post("/api/v1/processing-runs:advance").json()
    (item,) = [
        item
        for item in client.get("/api/v1/episode-library").json()["items"]
        if item["episode_id"] is not None
    ]

    episode = client.get(f"/api/v1/episodes/{item['episode_id']}").json()
    transcript = client.get(
        f"/api/v1/processing-runs/{episode['processing_run_id']}/transcript"
    ).json()

    assert episode["processing_run_id"] == completed["processing_run_id"]
    assert [line["target_text"] for line in transcript["lines"]] == [
        segment["target_text"] for segment in episode["segments"]
    ]
