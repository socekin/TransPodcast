from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from transpodcast.api import create_app
from transpodcast.artifacts import resolve_structured_artifact
from transpodcast.models import EpisodeSegmentRevisionRecord
from transpodcast.testing import AcceptanceHarness


def test_segment_revision_preserves_the_prior_translation_and_render(
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
    original = client.get(f"/api/v1/episodes/{episode_id}").json()

    revised = client.patch(
        f"/api/v1/episodes/{episode_id}/segments/1",
        json={"target_text": "A revised complete translation."},
    )

    assert revised.status_code == 202
    client.post("/api/v1/processing-runs:advance")
    history = client.get(
        f"/api/v1/episodes/{episode_id}/segments/1/revisions"
    )
    assert history.status_code == 200
    items = history.json()["items"]
    assert [item["version"] for item in items] == [2, 1]
    assert items[1]["target_text"] == original["segments"][0]["target_text"]
    assert items[1]["preview_key"] == original["preview"]["key"]
    assert items[0]["target_text"] == "A revised complete translation."
    assert items[0]["preview_key"] != items[1]["preview_key"]

    with harness.dependencies.database.session() as session:
        revisions = session.scalars(
            select(EpisodeSegmentRevisionRecord)
            .where(
                EpisodeSegmentRevisionRecord.episode_id == episode_id,
                EpisodeSegmentRevisionRecord.ordinal == 1,
            )
            .order_by(EpisodeSegmentRevisionRecord.version.desc())
        ).all()
        preserved_artifacts = [
            json.loads(
                resolve_structured_artifact(
                    session,
                    revision.translation_json,
                )
            )
            for revision in revisions
        ]
    assert preserved_artifacts[1]["segments"][0] == (
        original["segments"][0]["target_text"]
    )
    assert preserved_artifacts[0]["segments"][0] == (
        "A revised complete translation."
    )
