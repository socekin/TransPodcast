from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient
from PIL import Image

from transpodcast.api import create_app
from transpodcast.domain import (
    AudioQualityReport,
    CanonicalSegment,
    SourceThumbnail,
    SpeechResult,
)
from transpodcast.pipeline_versions import CURRENT_SPEECH_PIPELINE_VERSION
from transpodcast.publication_container import EmbeddedPublication
from transpodcast.service import TransPodcastService
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    FakeSourceAdapter,
    FakeSpeechAdapter,
    FakeTranslationAdapter,
)


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 9), (240, 80, 40)).save(output, format="PNG")
    return output.getvalue()


_PNG = _png()


class ArtworkSource(FakeSourceAdapter):
    def thumbnail(self, candidate):  # type: ignore[no-untyped-def]
        return SourceThumbnail(
            content=_PNG,
            source_url=(
                "https://i.ytimg.com/vi/"
                f"{candidate.youtube_video_id}/maxresdefault.jpg"
            ),
            media_type="image/png",
            width=16,
            height=9,
        )


class BrokenArtworkSource(FakeSourceAdapter):
    def thumbnail(self, candidate):  # type: ignore[no-untyped-def]
        del candidate
        raise RuntimeError("Thumbnail provider is unavailable")


class RecoveringArtworkSource(ArtworkSource):
    def __init__(self) -> None:
        super().__init__()
        self.available = False

    def thumbnail(self, candidate):  # type: ignore[no-untyped-def]
        if not self.available:
            raise RuntimeError("Thumbnail provider is unavailable")
        return super().thumbnail(candidate)


class ParallelArtworkSource(ArtworkSource):
    def __init__(self, analysis_started: Event) -> None:
        super().__init__()
        self.analysis_started = analysis_started

    def thumbnail(self, candidate):  # type: ignore[no-untyped-def]
        assert self.analysis_started.wait(timeout=2)
        return super().thumbnail(candidate)


class SignalingAnalysis(FakeAnalysisAdapter):
    def __init__(self, analysis_started: Event) -> None:
        super().__init__()
        self.analysis_started = analysis_started

    def analyze(self, candidate, archive):  # type: ignore[no-untyped-def]
        self.analysis_started.set()
        return super().analyze(candidate, archive)


class ChapterAnalysis(FakeAnalysisAdapter):
    def analyze(self, candidate, archive):  # type: ignore[no-untyped-def]
        result = super().analyze(candidate, archive)
        segments = tuple(
            CanonicalSegment(
                segment_id=f"seg-{index:04d}",
                speaker_label="speaker-01",
                start_seconds=float((index - 1) * 180),
                end_seconds=float(index * 180),
                text=f"Source topic {index}",
            )
            for index in range(1, 5)
        )
        return replace(
            result,
            segments=segments,
            checksum="chapter-analysis-checksum",
        )


class PublicationMetadataTranslation(FakeTranslationAdapter):
    def translate(self, candidate):  # type: ignore[no-untyped-def]
        result = super().translate(candidate)
        description = f"{chr(0x4E00) * 180}\n\n{chr(0x4E01) * 180}"
        metadata = {
            "policy_version": "publication-metadata-v1",
            "status": "accepted",
            "description": description,
            "participant_line": "",
            "chapters": [
                {
                    "anchor_segment_id": f"seg-{index:04d}",
                    "title": chr(0x4E10 + index) * 4,
                    "rationale": f"Topic {index}",
                }
                for index in range(1, 5)
            ],
        }
        return replace(
            result,
            segments=tuple(f"Target topic {index}" for index in range(1, 5)),
            publication_metadata_json=json.dumps(metadata),
        )


class ChapterSpeech(FakeSpeechAdapter):
    def render(self, candidate, translation):  # type: ignore[no-untyped-def]
        self.invocation_count += 1
        return SpeechResult(
            publication_audio=b"M4A!episode-presentation-audio",
            duration_seconds=1800,
            chapters=(),
            pipeline_version=CURRENT_SPEECH_PIPELINE_VERSION,
            quality=AudioQualityReport(rendered_segments=4),
            segment_timings=(
                (0.0, 90.0),
                (180.0, 270.0),
                (360.0, 450.0),
                (540.0, 630.0),
            ),
        )


def test_private_publication_uses_episode_artwork_and_semantic_metadata(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        source=ArtworkSource(),
        analysis=ChapterAnalysis(),
        translation=PublicationMetadataTranslation(),
        speech=ChapterSpeech(),
        show_artwork=_PNG,
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    processed = client.post("/api/v1/processing-runs:advance").json()
    episode_id = processed["episode_id"]

    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    artwork = client.get(detail["artwork_url"])
    assert artwork.status_code == 200
    assert artwork.headers["content-type"] == "image/jpeg"
    assert detail["artwork"]["source_width"] == 16
    assert detail["artwork"]["source_height"] == 9
    assert detail["artwork"]["retrieval_result"] == "selected"
    assert detail["publication_description"].count("\n\n") == 1
    assert [chapter["start_seconds"] for chapter in detail["chapters"]] == [
        0,
        180,
        360,
    ]
    assert detail["publication_diagnostics"]["chapters"] == {
        "status": "ready",
        "proposed_count": 4,
        "included_count": 3,
        "excluded_reasons": [
            {"reason": "hourly_density_limit", "count": 1}
        ],
    }

    publish_token = next(
        action.token
        for action in dependencies.notifications.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{publish_token}:apply")

    feed = client.get(f"/feed/{harness.feed_token}.xml").text
    assert "00:00:00" in feed
    assert "00:03:00" in feed
    assert "00:06:00" in feed
    assert "episodes/" in feed
    assert "/v1/artwork.jpg" in feed
    assert "<podcast:chapters" in feed
    assert "Part 1" not in feed

    with dependencies.database.session() as session:
        from transpodcast.models import EpisodeRecord

        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert dependencies.storage.read_publication(
            str(episode.publication_key)
        ) == b"M4A!episode-presentation-audio"
        assert dependencies.storage.read_publication(
            str(episode.publication_artwork_key)
        ) == artwork.content
        assert episode.artwork_path is not None
        published_artwork_snapshot = episode.published_artwork_json
        assert published_artwork_snapshot == episode.artwork_json
        Path(episode.artwork_path).unlink()
        episode.artwork_json = json.dumps(
            {"status": "fallback", "error": "Later draft failed"}
        )
        chapters = json.loads(
            dependencies.storage.read_publication(
                str(episode.publication_chapters_key)
            )
            or b"{}"
        )
    assert [item["startTime"] for item in chapters["chapters"]] == [
        0,
        180,
        360,
    ]
    assert client.get(detail["artwork_url"]).content == artwork.content
    published_detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert published_detail["published_metadata"]["artwork"] == json.loads(
        published_artwork_snapshot or "{}"
    )


def test_embedded_media_revision_waits_for_owner_before_replacing_audio(
    tmp_path,
    monkeypatch,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        source=ArtworkSource(),
        analysis=ChapterAnalysis(),
        translation=PublicationMetadataTranslation(),
        speech=ChapterSpeech(),
        show_artwork=_PNG,
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    processed = client.post("/api/v1/processing-runs:advance").json()
    episode_id = processed["episode_id"]
    publish_token = next(
        action.token
        for action in dependencies.notifications.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{publish_token}:apply")
    with dependencies.database.session() as session:
        from transpodcast.models import EpisodeRecord

        published = session.get(EpisodeRecord, episode_id)
        assert published is not None
        original_publication_key = str(published.publication_key)
        chapter_count = len(json.loads(published.published_chapters_json or "[]"))
    monkeypatch.setattr(
        "transpodcast.service.embed_publication_metadata",
        lambda *args, **kwargs: EmbeddedPublication(
            audio=b"M4A!embedded-publication-audio",
            source_audio_packet_hash="SHA256=unchanged",
            output_audio_packet_hash="SHA256=unchanged",
            chapter_count=chapter_count,
            artwork_embedded=True,
        ),
    )

    service = TransPodcastService(dependencies)
    result = service.prepare_embedded_media_revision(
        episode_id,
        expected_chapter_count=chapter_count,
    )
    service.deliver_pending_publication_notifications()
    service.close()

    assert result["episode_state"] == "revision_pending"
    assert result["publication_version"] == 1
    assert result["revision_version"] == 2
    assert result["audio_bytes"] == len(b"M4A!embedded-publication-audio")
    with dependencies.database.session() as session:
        from transpodcast.models import EpisodeRecord, ProcessingRunActivityRecord

        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert str(episode.publication_key) == original_publication_key
        assert dependencies.storage.read_preview(
            episode.preview_key
        ) == b"M4A!embedded-publication-audio"
        activities = session.query(ProcessingRunActivityRecord).filter_by(
            processing_run_id=episode.processing_run_id,
            kind="publication_container_revision_prepared",
        )
        assert activities.count() == 1
    pending_feed = client.get(f"/feed/{harness.feed_token}.xml").text
    assert original_publication_key in pending_feed
    assert "/v2/audio.m4a" not in pending_feed

    experiment_publish_token = next(
        action.token
        for action in dependencies.notifications.latest().actions
        if action.label == "发布"
    )
    published = client.post(
        f"/api/v1/telegram-actions/{experiment_publish_token}:apply"
    )
    assert published.json()["state"] == "published"
    with dependencies.database.session() as session:
        from transpodcast.models import EpisodeRecord

        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert str(episode.publication_key).endswith("/v2/audio.m4a")
        assert dependencies.storage.read_publication(
            str(episode.publication_key)
        ) == b"M4A!embedded-publication-audio"
    published_feed = client.get(f"/feed/{harness.feed_token}.xml").text
    assert "/v2/audio.m4a" in published_feed
    assert original_publication_key not in published_feed


def test_artwork_failure_falls_back_without_blocking_the_processing_run(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        source=BrokenArtworkSource(),
        show_artwork=_PNG,
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    processed = client.post("/api/v1/processing-runs:advance")

    assert processed.status_code == 200
    assert processed.json()["episode_state"] == "publication_queue"
    episode_id = processed.json()["episode_id"]
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    artwork = client.get(detail["artwork_url"])
    assert artwork.content == _PNG
    assert artwork.headers["content-type"] == "image/png"
    assert detail["publication_diagnostics"]["chapters"] == {
        "status": "omitted",
        "proposed_count": 0,
        "included_count": 0,
        "excluded_reasons": [
            {"reason": "metadata_degraded", "count": 1}
        ],
    }
    activity = client.get(
        f"/api/v1/processing-runs/{processed.json()['processing_run_id']}"
    ).json()["activity"]
    assert any(
        item["stage"] == "episode_artwork"
        and item["kind"] == "stage_degraded"
        for item in activity
    )


def test_source_analysis_does_not_wait_for_episode_artwork(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    analysis_started = Event()
    dependencies = replace(
        harness.dependencies,
        source=ParallelArtworkSource(analysis_started),
        analysis=SignalingAnalysis(analysis_started),
        show_artwork=_PNG,
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")

    processed = client.post("/api/v1/processing-runs:advance")

    assert processed.status_code == 200
    assert processed.json()["episode_state"] == "publication_queue"


def test_published_episode_can_backfill_artwork_without_rerendering_audio(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    source = RecoveringArtworkSource()
    dependencies = replace(
        harness.dependencies,
        source=source,
        show_artwork=_PNG,
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    processed = client.post("/api/v1/processing-runs:advance").json()
    episode_id = processed["episode_id"]
    first_publish = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{first_publish}:apply")
    with dependencies.database.session() as session:
        from transpodcast.models import EpisodeRecord

        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        original_audio_key = str(episode.publication_key)
        original_audio = dependencies.storage.read_publication(
            original_audio_key
        )
    translation_count = harness.codex.invocation_count
    metadata_count = harness.codex.metadata_invocation_count
    speech_count = harness.modal.invocation_count
    presentation = client.get(
        f"/api/v1/episodes/{episode_id}"
    ).json()["publication_presentation"]
    assert presentation["backfill_available"] is True
    assert "artwork" in presentation["degraded_fields"]

    source.available = True
    backfill = client.post(
        f"/api/v1/episodes/{episode_id}:prepare-metadata-only-revision"
    )

    assert backfill.status_code == 202
    assert backfill.json()["state"] == "revision_pending"
    assert harness.codex.invocation_count == translation_count
    assert harness.codex.metadata_invocation_count == metadata_count + 1
    assert harness.modal.invocation_count == speech_count
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["publication_description"] != detail["overview"]
    assert detail["published_metadata"]["description"] == (
        detail["overview"]
    )
    assert client.get(
        detail["published_metadata"]["artwork_url"]
    ).content == _PNG
    revision_publish = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{revision_publish}:apply")

    with dependencies.database.session() as session:
        episode = session.get(EpisodeRecord, episode_id)
        assert episode is not None
        assert str(episode.publication_key) == original_audio_key
        assert dependencies.storage.read_publication(
            original_audio_key
        ) == original_audio
        assert episode.publication_artwork_key is not None


def test_historical_description_backfills_without_final_timing_evidence(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        source=BrokenArtworkSource(),
        show_artwork=_PNG,
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    processed = client.post("/api/v1/processing-runs:advance").json()
    episode_id = processed["episode_id"]
    publish = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{publish}:apply")
    with dependencies.database.session() as session:
        from transpodcast.models import ProcessingRunRecord

        run = session.get(
            ProcessingRunRecord,
            processed["processing_run_id"],
        )
        assert run is not None
        run.render_json = None
    speech_count = harness.modal.invocation_count

    response = client.post(
        f"/api/v1/episodes/{episode_id}:prepare-metadata-only-revision"
    )

    assert response.status_code == 202
    assert harness.modal.invocation_count == speech_count
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["publication_description"] != detail["overview"]
    assert detail["chapters"] == []
