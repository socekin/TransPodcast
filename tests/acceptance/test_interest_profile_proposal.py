from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_agent_proposed_interest_profile_is_inactive_until_approved(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    original = client.get("/api/v1/interest-profile").json()

    proposal = client.post(
        "/api/v1/interest-profile/proposals",
        json={"markdown": "# Interests\n\nAI infrastructure."},
    )

    assert proposal.status_code == 201
    assert proposal.json()["active"] is False
    assert proposal.json()["source"] == "agent"
    assert client.get("/api/v1/interest-profile").json() == original

    approved = client.post(
        f"/api/v1/interest-profile/revisions/"
        f"{proposal.json()['version']}:approve"
    )
    assert approved.status_code == 200
    assert approved.json()["active"] is True
    assert client.get("/api/v1/interest-profile").json()["markdown"] == (
        "# Interests\n\nAI infrastructure."
    )
