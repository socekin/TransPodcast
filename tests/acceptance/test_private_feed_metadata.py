from __future__ import annotations

import hashlib
from dataclasses import replace

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def test_api_startup_refreshes_the_r2_feed_snapshot(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)

    create_app(harness.dependencies)

    feed = (
        harness.storage.root / "feed" / "current.xml"
    ).read_text(encoding="utf-8")
    assert "<itunes:block>Yes</itunes:block>" in feed
    assert '<itunes:category text="Technology" />' in feed


def test_changed_show_artwork_uses_a_new_immutable_publication_key(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    first_artwork = b"first-show-artwork"
    second_artwork = b"second-show-artwork"

    create_app(
        replace(harness.dependencies, show_artwork=first_artwork)
    )
    create_app(
        replace(harness.dependencies, show_artwork=second_artwork)
    )

    first_digest = hashlib.sha256(first_artwork).hexdigest()[:16]
    second_digest = hashlib.sha256(second_artwork).hexdigest()[:16]
    publication_root = harness.storage.publication_root
    assert (
        publication_root
        / f"artwork/show-{first_digest}-3000.png"
    ).read_bytes() == first_artwork
    assert (
        publication_root
        / f"artwork/show-{second_digest}-3000.png"
    ).read_bytes() == second_artwork
    feed = (
        harness.storage.root / "feed" / "current.xml"
    ).read_text(encoding="utf-8")
    assert f"artwork/show-{second_digest}-3000.png" in feed
    assert f"artwork/show-{first_digest}-3000.png" not in feed


def test_private_feed_omits_generic_chapters_without_semantic_metadata(
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
    client.post("/api/v1/processing-runs:advance")
    token = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label == "发布"
    )
    client.post(f"/api/v1/telegram-actions/{token}:apply")

    feed = client.get(f"/feed/{harness.feed_token}.xml").text

    assert 'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"' in feed
    assert 'xmlns:content="http://purl.org/rss/1.0/modules/content/"' in feed
    assert 'xmlns:podcast="https://podcastindex.org/namespace/1.0"' in feed
    assert "<language>zh-CN</language>" in feed
    assert "<title>TransPodcast</title>" in feed
    assert (
        "<description>Private complete Mandarin podcast editions."
        "</description>"
    ) in feed
    assert "<itunes:author>TransPodcast</itunes:author>" in feed
    assert "<itunes:block>Yes</itunes:block>" in feed
    assert '<itunes:category text="Technology" />' in feed
    assert "<link>http://testserver</link>" in feed
    assert "<itunes:image" in feed
    assert "<itunes:duration>3720</itunes:duration>" in feed
    assert 'type="audio/x-m4a"' in feed
    assert "<podcast:chapters" not in feed
    assert "Part 1" not in feed
    assert "youtube.com/watch?v=useful-001" in feed
