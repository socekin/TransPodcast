from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_owner_versions_interests_and_updates_safe_automation_settings(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    initial = client.get("/api/v1/interest-profile").json()
    assert initial["version"] == 1

    updated = client.put(
        "/api/v1/interest-profile",
        json={
            "markdown": (
                "# Interests\n\n"
                "Product strategy, developer tools, and AI infrastructure.\n\n"
                "# Exclusions\n\n"
                "Celebrity news and pure entertainment."
            )
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    revisions = client.get(
        "/api/v1/interest-profile/revisions"
    ).json()["items"]
    assert [item["version"] for item in revisions] == [2, 1]
    assert revisions[0]["active"] is True
    assert revisions[1]["active"] is False

    defaults = client.get("/api/v1/settings").json()
    assert defaults["automation"] == {
        "discovery_interval_minutes": 120,
        "duration_guardrail_minutes": 180,
        "processing_target_hours": 24,
        # The AcceptanceHarness seeds automatic mode; production
        # defaults to manual processing.
        "manual_processing": False,
        "auto_discovery": False,
    }
    assert defaults["retention"]["intermediate_days"] == 7
    assert defaults["secrets"] == {
        "codex": "configured",
        "modal": "configured",
        "cloudflare": "configured",
        "telegram": "configured",
        "feed_token": "configured",
    }

    changed = client.patch(
        "/api/v1/settings",
        json={
            "discovery_interval_minutes": 60,
            "duration_guardrail_minutes": 240,
            "notification_preferences": {
                "candidate_decision": True,
                "automatic_ignore": False,
                "processing_failure": True,
                "spend_guardrail": True,
                "publication_decision": True,
            },
        },
    )
    assert changed.status_code == 200
    assert changed.json()["automation"]["discovery_interval_minutes"] == 60
    assert changed.json()["automation"]["duration_guardrail_minutes"] == 240
    assert changed.json()["notifications"]["automatic_ignore"] is False

    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    channels = client.get("/api/v1/source-channels").json()["items"]
    paused = client.patch(
        f"/api/v1/source-channels/{channels[0]['id']}",
        json={"mode": "paused"},
    )
    assert paused.status_code == 200
    assert paused.json()["mode"] == "paused"
    assert client.post("/api/v1/discovery-runs").json()["discovered"] == 0

    serialized = str(client.get("/api/v1/settings").json())
    assert harness.feed_token not in serialized
