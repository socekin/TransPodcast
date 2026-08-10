from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate
from transpodcast.models import ProcessingRunRecord
from transpodcast.testing import AcceptanceHarness


def test_provisional_speaker_and_voice_reference_require_owner_approval(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    speaker = client.post(
        "/api/v1/speakers",
        json={
            "display_name": "Episode guest",
            "canonical_voice_reference": "voices/guest/candidate-v1.wav",
            "verified": False,
        },
    ).json()
    assert speaker["status"] == "provisional"
    assert speaker["canonical_voice_reference"] == (
        "voices/guest/candidate-v1.wav"
    )

    proposal = client.post(
        f"/api/v1/speakers/{speaker['id']}/voice-references",
        json={"artifact_key": "voices/guest/candidate-v2.wav"},
    )
    assert proposal.status_code == 201
    assert proposal.json()["status"] == "proposed"
    unchanged = client.get("/api/v1/speakers").json()["items"][0]
    assert unchanged["canonical_voice_reference"] == (
        "voices/guest/candidate-v1.wav"
    )

    approved = client.post(
        f"/api/v1/speakers/{speaker['id']}/voice-references/"
        f"{proposal.json()['id']}:approve"
    )
    assert approved.status_code == 200
    assert approved.json()["canonical_voice_reference"] == (
        "voices/guest/candidate-v2.wav"
    )

    confirmed = client.post(f"/api/v1/speakers/{speaker['id']}:confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "verified"


def test_speaker_merge_preserves_lineage_and_reassigns_current_segments(
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
    episode_id = client.post(
        "/api/v1/processing-runs:advance"
    ).json()["episode_id"]
    provisional = client.post(
        "/api/v1/speakers",
        json={
            "display_name": "Guest candidate",
            "canonical_voice_reference": "voices/guest.wav",
        },
    ).json()
    verified = client.post(
        "/api/v1/speakers",
        json={
            "display_name": "Known guest",
            "canonical_voice_reference": "voices/known.wav",
            "verified": True,
        },
    ).json()
    client.patch(
        f"/api/v1/episodes/{episode_id}/segments/1",
        json={
            "target_text": "A translated segment.",
            "speaker_id": provisional["id"],
        },
    )
    client.post("/api/v1/processing-runs:advance")

    merged = client.post(
        f"/api/v1/speakers/{provisional['id']}:merge",
        json={"target_speaker_id": verified["id"]},
    )

    assert merged.status_code == 200
    assert merged.json()["merged_into_id"] == verified["id"]
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["segments"][0]["speaker"]["id"] == verified["id"]
    assert all(
        item["id"] != provisional["id"]
        for item in client.get("/api/v1/speakers").json()["items"]
    )


def test_saved_channel_host_reuses_the_same_speaker_across_episodes(
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
    client.post("/api/v1/processing-runs:advance")
    provisional = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"][0]
    client.post(
        f"/api/v1/channel-voice-candidates/{provisional['id']}:save",
        json={"display_name": "Known speaker"},
    )
    approved_reference = client.get("/api/v1/speakers").json()["items"][0][
        "canonical_voice_reference"
    ]

    second_run = client.post("/api/v1/processing-runs:advance").json()

    speakers = client.get("/api/v1/speakers").json()["items"]
    assert harness.dependencies.speaker_match_policy.minimum_similarity == 0.9
    assert len(speakers) == 1
    assert speakers[0]["id"] == provisional["speaker_id"]
    assert speakers[0]["status"] == "verified"
    assert speakers[0]["canonical_voice_reference"] == approved_reference
    with harness.dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            second_run["processing_run_id"],
        )
        assert run is not None
        analysis = json.loads(run.analysis_json or "{}")
    matched_speaker = analysis["speakers"][0]
    assert matched_speaker["registry_speaker_id"] == provisional["speaker_id"]
    assert (
        matched_speaker["matched_source_channel_id"]
        == channel["id"]
    )
    assert matched_speaker["registry_voice_reference_id"] == (
        provisional["voice_reference"]["id"]
    )
    assert matched_speaker["reference_artifact_key"] == approved_reference
    assert matched_speaker["match_similarity"] == 1.0
    assert (
        matched_speaker["match_policy_version"]
        == "channel-speaker-match-v1"
    )


def test_owner_can_split_selected_segments_into_a_provisional_speaker(
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
    episode_id = client.post(
        "/api/v1/processing-runs:advance"
    ).json()["episode_id"]
    original = client.get("/api/v1/speakers").json()["items"][0]

    split = client.post(
        f"/api/v1/speakers/{original['id']}:split",
        json={
            "display_name": "Corrected guest",
            "episode_id": episode_id,
            "segment_ordinals": [1],
        },
    )

    assert split.status_code == 201
    assert split.json()["status"] == "provisional"
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["segments"][0]["speaker"]["id"] == split.json()["id"]
