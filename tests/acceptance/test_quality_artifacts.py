from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_completed_episode_exposes_deterministic_quality_evidence(
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

    detail = client.get(
        f"/api/v1/episodes/{outcome['episode_id']}"
    ).json()

    assert detail["quality_gates"]["eligible"] is True
    assert detail["quality_gates"]["audio"] == {
        "codec": "AAC-LC",
        "sample_rate_hz": 48000,
        "channels": 2,
        "bitrate_kbps": 128,
        "loudness_lufs": -16.0,
        "true_peak_dbtp": -1.0,
        "clipping": False,
    }
    assert detail["quality_gates"]["segment_coverage"] == {
        "expected": 1,
        "rendered": 1,
    }
