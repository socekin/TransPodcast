from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from transpodcast.api import create_app
from transpodcast.artifacts import resolve_structured_artifact
from transpodcast.models import CanonicalTranscriptRevisionRecord
from transpodcast.testing import AcceptanceHarness


def test_transcript_revision_retranslates_and_rerenders_only_one_segment(
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

    queued = client.patch(
        f"/api/v1/episodes/{episode_id}/transcript-segments/1",
        json={"source_text": "The corrected source transcript."},
    )
    assert queued.status_code == 202

    translated = client.post("/api/v1/processing-runs:advance")
    assert translated.json()["revision_state"] == "render_queued"
    assert harness.codex.invocation_count == 2
    assert harness.modal.invocation_count == 1

    rendered = client.post("/api/v1/processing-runs:advance")
    assert rendered.json()["revision_state"] == "completed"
    assert harness.modal.invocation_count == 2
    assert harness.modal.last_render_scope == ("segment", 1)

    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["segments"][0]["source_text"] == (
        "The corrected source transcript."
    )
    assert detail["segments"][0]["target_text"].startswith(
        "Corrected translation:"
    )
    revisions = client.get(
        f"/api/v1/episodes/{episode_id}/transcript-segments/1/revisions"
    ).json()["items"]
    assert [item["version"] for item in revisions] == [2, 1]

    with harness.dependencies.database.session() as session:
        records = session.scalars(
            select(CanonicalTranscriptRevisionRecord)
            .where(
                CanonicalTranscriptRevisionRecord.episode_id == episode_id,
                CanonicalTranscriptRevisionRecord.ordinal == 1,
            )
            .order_by(CanonicalTranscriptRevisionRecord.version.desc())
        ).all()
        preserved_artifacts = [
            json.loads(
                resolve_structured_artifact(
                    session,
                    record.transcript_json,
                )
            )
            for record in records
        ]
    assert preserved_artifacts[0]["segments"][0]["text"] == (
        "The corrected source transcript."
    )
    assert preserved_artifacts[1]["segments"][0]["text"] != (
        "The corrected source transcript."
    )
