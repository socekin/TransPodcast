from __future__ import annotations

import json

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_owner_can_manage_speaker_identity_and_rerender_one_revision(
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

    speaker = client.post(
        "/api/v1/speakers",
        json={
            "display_name": "Morgan Lee",
            "canonical_voice_reference": (
                "speakers/morgan-lee/reference-neutral.wav"
            ),
            "verified": True,
        },
    )
    assert speaker.status_code == 201
    speaker_id = speaker.json()["id"]
    emotion_reference = (
        harness.dependencies.artifact_root
        / "structured"
        / "emotion-references"
        / "segment-001.wav"
    )
    emotion_reference.parent.mkdir(parents=True)
    emotion_reference.write_bytes(b"RIFFfixture-emotion-reference")

    detail = client.get(f"/api/v1/episodes/{episode_id}")
    assert detail.status_code == 200
    assert detail.json()["apple_podcasts_preview"] == {
        "artwork": f"/api/v1/episodes/{episode_id}/artwork",
        "audio_format": "48 kHz stereo AAC-LC · 128 kbps",
        "chapter_count": 0,
        "duration_seconds": 3720,
    }
    assert detail.json()["segments"][0]["version"] == 1

    revised = client.patch(
        f"/api/v1/episodes/{episode_id}/segments/1",
        json={
            "target_text": "A durable product preserves its original intent.",
            "speaker_id": speaker_id,
            "emotion_reference": str(emotion_reference),
        },
    )
    assert revised.status_code == 202
    assert revised.json() == {
        "episode_id": episode_id,
        "segment_ordinal": 1,
        "revision_job_id": 1,
        "revision_state": "queued",
    }
    assert harness.codex.invocation_count == 1
    assert harness.modal.invocation_count == 1

    completed = client.post("/api/v1/processing-runs:advance")
    assert completed.json()["revision_state"] == "completed"
    assert completed.json()["segment_version"] == 2
    assert harness.modal.invocation_count == 2
    assert harness.modal.last_render_scope == ("segment", 1)
    rendered_analysis = json.loads(
        harness.modal.last_analysis_json or "{}"
    )
    assert rendered_analysis["segments"][0]["speaker_label"] == (
        f"registry-speaker-{speaker_id}"
    )
    assert rendered_analysis["segments"][0][
        "emotion_reference_artifact_key"
    ] == str(emotion_reference)

    updated = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert updated["segments"][0]["target_text"] == (
        "A durable product preserves its original intent."
    )
    assert updated["segments"][0]["speaker"]["display_name"] == "Morgan Lee"
    assert updated["segments"][0]["version"] == 2
    assert harness.telegram.latest().kind == "publication_decision"
