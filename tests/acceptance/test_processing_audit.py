from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_processing_run_exposes_versioned_checkpoint_audit_records(
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
    outcome = client.post("/api/v1/processing-runs:advance").json()

    run = client.get(
        f"/api/v1/processing-runs/{outcome['processing_run_id']}"
    )

    assert run.status_code == 200
    body = run.json()
    assert body["status"] == "completed"
    assert body["translation"]["input_tokens"] == 120
    assert body["translation"]["output_tokens"] == 80
    stages = {item["stage"]: item for item in body["stages"]}
    assert set(stages) >= {
        "source_archive",
        "source_analysis",
        "translation",
        "render",
        "quality_gate",
    }
    assert stages["source_archive"]["output_hash"]
    assert stages["translation"]["provider"] == "fake-codex"
    assert stages["render"]["version"] == "fixture-pipeline-v1"
