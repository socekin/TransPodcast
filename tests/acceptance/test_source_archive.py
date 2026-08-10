from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_source_is_archived_once_before_paid_processing(tmp_path) -> None:
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

    client.post("/api/v1/processing-runs:advance")

    archives = harness.storage.source_archive_paths()
    assert archives == ["useful-001/source.m4a"]
    assert harness.dependencies.source.archive_invocation_count == 1
