from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate, TriageDecision, TriageResult
from transpodcast.testing import AcceptanceHarness


def test_owner_resolves_uncertain_and_overlong_candidates(tmp_path) -> None:
    uncertain = SourceCandidate(
        youtube_video_id="candidate-uncertain",
        title="A broad conversation",
        description="The available evidence is ambiguous.",
        duration_seconds=3600,
        language="en",
        published_at=datetime(2026, 7, 28, 13, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=candidate-uncertain",
        captions="A wide-ranging conversation.",
    )
    irrelevant = SourceCandidate(
        youtube_video_id="candidate-irrelevant",
        title="Celebrity trivia",
        description="Entertainment news.",
        duration_seconds=1800,
        language="en",
        published_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=candidate-irrelevant",
        captions="Celebrity news and trivia.",
    )
    overlong = SourceCandidate(
        youtube_video_id="candidate-overlong",
        title="A five-hour product strategy workshop",
        description="A deep product strategy discussion.",
        duration_seconds=5 * 60 * 60,
        language="en",
        published_at=datetime(2026, 7, 28, 11, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=candidate-overlong",
        captions="A detailed product strategy workshop.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[uncertain, irrelevant, overlong],
        triage_results={
            "candidate-uncertain": TriageResult(
                decision=TriageDecision.OWNER_DECISION,
                confidence=0.52,
                rationale="The evidence matches several interests weakly.",
                matched_interests=("product strategy",),
            ),
            "candidate-irrelevant": TriageResult(
                decision=TriageDecision.IGNORE,
                confidence=0.99,
                rationale="The evidence matches an explicit exclusion.",
                matched_interests=(),
            ),
            "candidate-overlong": TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.98,
                rationale="The topic directly matches product strategy.",
                matched_interests=("product strategy",),
            ),
        },
    )
    client = TestClient(create_app(harness.dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    result = client.post("/api/v1/discovery-runs")
    assert result.json() == {
        "discovered": 3,
        "automatically_accepted": 0,
        "awaiting_decision": 2,
        "ignored": 1,
    }
    assert harness.telegram.messages == []

    decisions = client.get("/api/v1/decision-queue").json()["items"]
    assert [item["youtube_video_id"] for item in decisions] == [
        "candidate-uncertain",
        "candidate-overlong",
    ]
    assert decisions[1]["rationale"] == (
        "The video exceeds the Duration Guardrail."
    )

    processed = client.post(
        f"/api/v1/candidate-videos/{decisions[0]['id']}:decide",
        json={"decision": "process"},
    )
    assert processed.status_code == 200
    assert processed.json()["state"] == "processing_queued"

    ignored = client.post(
        f"/api/v1/candidate-videos/{decisions[1]['id']}:decide",
        json={"decision": "ignore"},
    )
    assert ignored.status_code == 200
    assert ignored.json()["state"] == "ignored"

    interest_profile = client.get("/api/v1/interest-profile").json()
    assert interest_profile["version"] == 1
    assert interest_profile["labeled_examples"] == {
        "positive": 1,
        "negative": 1,
    }

    ignored_items = client.get("/api/v1/ignored-list").json()["items"]
    assert {
        item["youtube_video_id"] for item in ignored_items
    } == {"candidate-irrelevant", "candidate-overlong"}
    searched_items = client.get(
        "/api/v1/ignored-list",
        params={"query": "product"},
    ).json()["items"]
    assert [item["youtube_video_id"] for item in searched_items] == [
        "candidate-overlong"
    ]

    reversed_decision = client.post(
        f"/api/v1/candidate-videos/{ignored_items[0]['id']}:process"
    )
    assert reversed_decision.status_code == 200
    assert reversed_decision.json()["state"] == "processing_queued"

    repeated_discovery = client.post("/api/v1/discovery-runs").json()
    assert repeated_discovery["discovered"] == 0
    assert harness.codex.invocation_count == 0
    assert harness.modal.invocation_count == 0


def test_discovery_ignores_unselected_history_before_channel_follow(
    tmp_path,
) -> None:
    historical = SourceCandidate(
        youtube_video_id="candidate-historical",
        title="An older episode",
        description="Published before the Source Channel was followed.",
        duration_seconds=1800,
        language="en",
        published_at=datetime(2026, 7, 28, 9, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=candidate-historical",
        captions="An older discussion.",
    )
    newly_published = SourceCandidate(
        youtube_video_id="candidate-new",
        title="A newly published episode",
        description="Published after the Source Channel was followed.",
        duration_seconds=1800,
        language="en",
        published_at=datetime(2026, 7, 28, 11, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=candidate-new",
        captions="A new discussion about product strategy.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[historical, newly_published],
        triage_results={
            historical.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.99,
                rationale="A historical match.",
                matched_interests=("product strategy",),
            ),
            newly_published.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.99,
                rationale="A new match.",
                matched_interests=("product strategy",),
            ),
        },
    )
    client = TestClient(create_app(harness.dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    discovered = client.post("/api/v1/discovery-runs").json()

    assert discovered == {
        "discovered": 1,
        "automatically_accepted": 1,
        "awaiting_decision": 0,
        "ignored": 0,
    }
    assert [
        item["youtube_video_id"]
        for item in client.get("/api/v1/processing-queue").json()["items"]
    ] == ["candidate-new"]
