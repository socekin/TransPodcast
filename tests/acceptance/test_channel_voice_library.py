from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import (
    AnalyzedSpeaker,
    CanonicalSegment,
    ChannelResolution,
    SourceAnalysisResult,
    SourceCandidate,
)
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    FakeSourceAdapter,
)


class TwoSpeakerAnalysisAdapter(FakeAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        _archive,
    ) -> SourceAnalysisResult:
        return SourceAnalysisResult(
            segments=(
                CanonicalSegment(
                    segment_id="seg-0001",
                    speaker_label="speaker-01",
                    start_seconds=0,
                    end_seconds=10,
                    text="First Speaker.",
                ),
                CanonicalSegment(
                    segment_id="seg-0002",
                    speaker_label="speaker-02",
                    start_seconds=10,
                    end_seconds=20,
                    text="Second Speaker.",
                ),
            ),
            pipeline_version="fixture-analysis-v1",
            diarization_model="fixture-sortformer",
            transcription_model="fixture-qwen3-asr",
            alignment_model="fixture-qwen3-aligner",
            speakers=(
                AnalyzedSpeaker(
                    local_label="speaker-01",
                    reference_audio=b"RIFFfirst-speaker-reference",
                    embedding=(1.0, 0.0, 0.0),
                    confidence=0.98,
                ),
                AnalyzedSpeaker(
                    local_label="speaker-02",
                    reference_audio=b"RIFFsecond-speaker-reference",
                    embedding=(0.0, 1.0, 0.0),
                    confidence=0.98,
                ),
            ),
        )


class TwoChannelSourceAdapter(FakeSourceAdapter):
    def __init__(self) -> None:
        super().__init__(candidates=[])
        self.by_channel = {
            "UC_CHANNEL_A": SourceCandidate(
                youtube_video_id="channel-a-episode",
                title="Channel A episode",
                description="Product strategy.",
                duration_seconds=1800,
                language="en",
                published_at=datetime(2026, 7, 28, 11, 0, tzinfo=UTC),
                source_url="https://youtube.test/watch?v=channel-a",
                captions="A durable product preserves intent.",
            ),
            "UC_CHANNEL_B": SourceCandidate(
                youtube_video_id="channel-b-episode",
                title="Channel B episode",
                description="Product strategy.",
                duration_seconds=1800,
                language="en",
                published_at=datetime(2026, 7, 28, 11, 1, tzinfo=UTC),
                source_url="https://youtube.test/watch?v=channel-b",
                captions="A durable product preserves intent.",
            ),
        }

    def resolve_channel(self, url: str) -> ChannelResolution:
        suffix = "A" if "channel-a" in url else "B"
        return ChannelResolution(
            youtube_channel_id=f"UC_CHANNEL_{suffix}",
            title=f"Channel {suffix}",
            canonical_url=url,
        )

    def discover(self, youtube_channel_id: str) -> list[SourceCandidate]:
        return [self.by_channel[youtube_channel_id]]

    def supported_source_video(
        self,
        _url: str,
    ) -> SourceCandidate | None:
        return None


def test_processed_voice_candidate_appears_under_its_source_channel(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    processed = client.post("/api/v1/processing-runs:advance").json()

    library = client.get("/api/v1/channel-voice-library")
    detail = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    )

    assert library.status_code == 200
    assert library.json() == {
        "items": [
            {
                "source_channel_id": channel["id"],
                "title": "Useful Show",
                "avatar_url": "",
                "saved_profile_count": 0,
                "pending_candidate_count": 1,
            }
        ]
    }
    assert detail.status_code == 200
    assert detail.json() == {
        "channel": {
            "id": channel["id"],
            "title": "Useful Show",
            "avatar_url": "",
        },
        "saved_profiles": [],
        "pending_candidates": [
            {
                "id": 1,
                "speaker_id": 1,
                "display_name": (
                    "speaker-01 · Building products that last"
                ),
                "local_label": "speaker-01",
                "segment_count": 1,
                "source_episode": {
                    "id": processed["episode_id"],
                    "title": (
                        "Useful Show · Building products that last"
                    ),
                },
                "voice_reference": {
                    "id": 1,
                    "duration_seconds": None,
                    "audio_url": (
                        "/api/v1/speaker-voice-references/1/audio"
                    ),
                },
                "possible_matches": [],
            }
        ],
    }
    assert harness.live_provider_invocation_count == 0


def test_owner_can_stream_and_seek_a_voice_reference(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    client.post("/api/v1/processing-runs:advance")
    reference = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"][0]["voice_reference"]

    full = client.get(reference["audio_url"])
    partial = client.get(
        reference["audio_url"],
        headers={"Range": "bytes=4-10"},
    )
    missing = client.get(
        "/api/v1/speaker-voice-references/999/audio"
    )

    assert full.status_code == 200
    assert full.content == b"RIFFfixture-speaker-reference"
    assert full.headers["content-type"] == "audio/wav"
    assert full.headers["accept-ranges"] == "bytes"
    assert partial.status_code == 206
    assert partial.content == b"fixture"
    assert partial.headers["content-range"] == "bytes 4-10/29"
    assert missing.status_code == 404


def test_owner_saves_a_candidate_for_future_channel_reuse(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    client.post("/api/v1/processing-runs:advance")
    candidate = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"][0]
    translation_calls = harness.codex.invocation_count
    speech_calls = harness.modal.invocation_count

    saved = client.post(
        f"/api/v1/channel-voice-candidates/{candidate['id']}:save",
        json={"display_name": "Morgan Lee"},
    )

    assert saved.status_code == 200
    assert saved.json() == {
        "id": 1,
        "speaker_id": candidate["speaker_id"],
        "display_name": "Morgan Lee",
        "state": "active",
        "voice_reference": {
            "id": candidate["voice_reference"]["id"],
            "duration_seconds": None,
            "audio_url": (
                "/api/v1/speaker-voice-references/1/audio"
            ),
        },
    }
    assert client.get("/api/v1/channel-voice-library").json() == {
        "items": [
            {
                "source_channel_id": channel["id"],
                "title": "Useful Show",
                "avatar_url": "",
                "saved_profile_count": 1,
                "pending_candidate_count": 0,
            }
        ]
    }
    detail = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()
    assert detail["saved_profiles"] == [saved.json()]
    assert detail["pending_candidates"] == []
    assert harness.codex.invocation_count == translation_calls
    assert harness.modal.invocation_count == speech_calls


def test_owner_can_name_an_unnamed_saved_speaker_profile_later(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    client.post("/api/v1/processing-runs:advance")
    candidate = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"][0]
    saved = client.post(
        f"/api/v1/channel-voice-candidates/{candidate['id']}:save",
        json={},
    ).json()

    blank_name = client.patch(
        f"/api/v1/channel-speaker-profiles/{saved['id']}",
        json={"display_name": "   "},
    )
    renamed = client.patch(
        f"/api/v1/channel-speaker-profiles/{saved['id']}",
        json={"display_name": "Morgan Lee"},
    )

    assert saved["display_name"] == "Unnamed speaker 1"
    assert blank_name.status_code == 422
    assert renamed.status_code == 200
    assert renamed.json() == {
        **saved,
        "display_name": "Morgan Lee",
    }
    detail = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()
    assert detail["saved_profiles"][0]["display_name"] == "Morgan Lee"


def test_owner_merges_a_candidate_and_can_replace_the_canonical_reference(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        analysis=TwoSpeakerAnalysisAdapter(),
    )
    client = TestClient(create_app(dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    processed = client.post("/api/v1/processing-runs:advance").json()
    candidates = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"]
    saved = client.post(
        f"/api/v1/channel-voice-candidates/{candidates[0]['id']}:save",
        json={"display_name": "Known speaker"},
    ).json()
    before_merge = client.get(
        f"/api/v1/episodes/{processed['episode_id']}"
    ).json()

    merged = client.post(
        f"/api/v1/channel-voice-candidates/{candidates[1]['id']}:merge",
        json={
            "target_profile_id": saved["id"],
            "replace_canonical_reference": True,
        },
    )
    repeated = client.post(
        f"/api/v1/channel-voice-candidates/{candidates[1]['id']}:merge",
        json={
            "target_profile_id": saved["id"],
            "replace_canonical_reference": True,
        },
    )

    assert merged.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == merged.json()
    assert merged.json()["id"] == saved["id"]
    assert merged.json()["speaker_id"] == saved["speaker_id"]
    assert merged.json()["display_name"] == "Known speaker"
    assert (
        merged.json()["voice_reference"]["id"]
        != saved["voice_reference"]["id"]
    )
    detail = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()
    assert detail["pending_candidates"] == []
    assert detail["saved_profiles"] == [merged.json()]
    after_merge = client.get(
        f"/api/v1/episodes/{processed['episode_id']}"
    ).json()
    assert [
        segment["speaker"]["id"]
        for segment in after_merge["segments"]
    ] == [
        segment["speaker"]["id"]
        for segment in before_merge["segments"]
    ]
    replacement_audio = client.get(
        merged.json()["voice_reference"]["audio_url"]
    )
    assert replacement_audio.content == b"RIFFsecond-speaker-reference"
    speakers = client.get("/api/v1/speakers").json()["items"]
    assert [speaker["id"] for speaker in speakers] == [saved["speaker_id"]]


def test_owner_ignores_and_restores_a_voice_candidate_without_reprocessing(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    client.post("/api/v1/processing-runs:advance")
    candidate = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"][0]
    translation_calls = harness.codex.invocation_count
    speech_calls = harness.modal.invocation_count

    ignored = client.post(
        f"/api/v1/channel-voice-candidates/{candidate['id']}:ignore"
    )

    assert ignored.status_code == 200
    assert ignored.json() == {
        "id": candidate["id"],
        "decision": "ignored",
    }
    ignored_library = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()
    assert ignored_library["saved_profiles"] == []
    assert ignored_library["pending_candidates"] == []
    assert harness.codex.invocation_count == translation_calls
    assert harness.modal.invocation_count == speech_calls

    restored = client.post(
        f"/api/v1/channel-voice-candidates/{candidate['id']}:restore"
    )

    assert restored.status_code == 200
    assert restored.json() == {
        "id": candidate["id"],
        "decision": "pending",
    }
    restored_library = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()
    assert [item["id"] for item in restored_library["pending_candidates"]] == [
        candidate["id"]
    ]
    assert harness.codex.invocation_count == translation_calls
    assert harness.modal.invocation_count == speech_calls


def test_stopped_speaker_profile_is_not_reused_for_the_next_episode(
    tmp_path,
) -> None:
    candidates = [
        SourceCandidate(
            youtube_video_id=f"episode-{index}",
            title=f"Episode {index}",
            description="Product strategy.",
            duration_seconds=1800,
            language="en",
            published_at=datetime(2026, 7, 28, 11, index, tzinfo=UTC),
            source_url=f"https://youtube.test/watch?v=episode-{index}",
            captions="A durable product preserves intent.",
        )
        for index in (1, 2)
    ]
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=candidates,
        triage_results=None,
    )
    client = TestClient(create_app(harness.dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    first_episode = client.post(
        "/api/v1/processing-runs:advance"
    ).json()["episode_id"]
    first_candidate = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"][0]
    saved = client.post(
        f"/api/v1/channel-voice-candidates/{first_candidate['id']}:save",
        json={"display_name": "Morgan Lee"},
    ).json()
    speech_calls = harness.modal.invocation_count

    stopped = client.post(
        f"/api/v1/channel-speaker-profiles/{saved['id']}:stop-reuse"
    )
    second_episode = client.post(
        "/api/v1/processing-runs:advance"
    ).json()["episode_id"]

    assert stopped.status_code == 200
    assert stopped.json() == {
        "id": saved["id"],
        "speaker_id": saved["speaker_id"],
        "display_name": "Morgan Lee",
        "state": "inactive",
        "voice_reference": saved["voice_reference"],
    }
    library = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()
    assert library["saved_profiles"] == []
    assert len(library["pending_candidates"]) == 1
    assert (
        library["pending_candidates"][0]["source_episode"]["id"]
        == second_episode
    )
    first_detail = client.get(
        f"/api/v1/episodes/{first_episode}"
    ).json()
    assert first_detail["segments"][0]["speaker"]["id"] == saved["speaker_id"]
    assert harness.modal.invocation_count == speech_calls + 1

    reactivated = client.post(
        f"/api/v1/channel-voice-candidates/{first_candidate['id']}:save",
        json={"display_name": "Morgan Lee"},
    )

    assert reactivated.status_code == 200
    assert reactivated.json()["state"] == "active"


def test_similar_voice_from_another_channel_stays_provisional(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        source=TwoChannelSourceAdapter(),
    )
    client = TestClient(create_app(dependencies))
    channel_a = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@channel-a",
            "mode": "agent_filtering",
        },
    ).json()
    channel_b = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@channel-b",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    client.post("/api/v1/processing-runs:advance")
    candidate_a = client.get(
        f"/api/v1/source-channels/{channel_a['id']}/voice-library"
    ).json()["pending_candidates"][0]
    saved_a = client.post(
        f"/api/v1/channel-voice-candidates/{candidate_a['id']}:save",
        json={"display_name": "Channel A speaker"},
    ).json()

    client.post("/api/v1/processing-runs:advance")

    detail_b = client.get(
        f"/api/v1/source-channels/{channel_b['id']}/voice-library"
    ).json()
    assert detail_b["saved_profiles"] == []
    assert len(detail_b["pending_candidates"]) == 1
    assert (
        detail_b["pending_candidates"][0]["display_name"]
        == "speaker-01 · Channel B episode"
    )
    assert detail_b["pending_candidates"][0]["possible_matches"] == [
        {
            "profile_id": saved_a["id"],
            "source_channel_id": channel_a["id"],
            "source_channel_title": "Channel A",
            "speaker_id": saved_a["speaker_id"],
            "display_name": "Channel A speaker",
            "similarity": 1.0,
        }
    ]
    cross_channel_merge = client.post(
        "/api/v1/channel-voice-candidates/"
        f"{detail_b['pending_candidates'][0]['id']}:merge",
        json={
            "target_profile_id": saved_a["id"],
            "replace_canonical_reference": False,
        },
    )
    assert cross_channel_merge.status_code == 409
