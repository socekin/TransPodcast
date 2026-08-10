from __future__ import annotations

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_feed_token_revocation_immediately_replaces_the_private_url(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    assert client.get(f"/feed/{harness.feed_token}.xml").status_code == 200

    revoked = client.post("/api/v1/feed-token:revoke")

    assert revoked.status_code == 200
    new_token = revoked.json()["feed_token"]
    assert len(new_token) >= 32
    assert new_token != harness.feed_token
    assert client.get(f"/feed/{harness.feed_token}.xml").status_code == 404
    assert client.get(f"/feed/{new_token}.xml").status_code == 200
    snapshot = (
        harness.storage.root / "feed" / "current.xml"
    ).read_text(encoding="utf-8")
    assert new_token in snapshot
    assert harness.feed_token not in snapshot
    settings = client.get("/api/v1/settings").json()
    assert new_token not in str(settings)
