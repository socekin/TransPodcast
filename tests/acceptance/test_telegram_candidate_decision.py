from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from transpodcast.api import create_app
from transpodcast.domain import (
    OwnerNotification,
    SourceCandidate,
    TriageDecision,
    TriageResult,
)
from transpodcast.errors import (
    NotificationDeliveryRejectedError,
    NotificationDeliveryUncertainError,
)
from transpodcast.i18n import zh_cn
from transpodcast.models import LabeledExampleRecord, TelegramDeliveryRecord
from transpodcast.service import TransPodcastService
from transpodcast.testing import AcceptanceHarness, FixedClock

_ZH_CN = json.loads(
    (
        Path(__file__).parents[1]
        / "fixtures"
        / "i18n"
        / "candidate-recommendations.zh-CN.json"
    ).read_text(encoding="utf-8")
)


def _localized_fixture(key: str) -> str:
    return str(_ZH_CN[key])


class FailSecondRecommendationOnce:
    def __init__(self) -> None:
        self.messages: list[OwnerNotification] = []
        self.failed = False

    def readiness(self) -> bool:
        return True

    def send(self, notification: OwnerNotification) -> str:
        if (
            notification.title == _localized_fixture("second_title")
            and not self.failed
        ):
            self.failed = True
            raise NotificationDeliveryRejectedError("Telegram send failed")
        self.messages.append(notification)
        return str(len(self.messages))


class DeliverThenLoseResponseOnce:
    def __init__(self) -> None:
        self.messages: list[OwnerNotification] = []

    def readiness(self) -> bool:
        return True

    def send(self, notification: OwnerNotification) -> str:
        self.messages.append(notification)
        raise NotificationDeliveryUncertainError(
            "Telegram response was lost"
        )


def test_manual_discovery_notifies_only_a_high_confidence_recommendation(
    tmp_path,
) -> None:
    high_match = SourceCandidate(
        youtube_video_id="high-match",
        title="How frontier labs build reliable agents",
        description="A technical interview about agent evaluation.",
        duration_seconds=3600,
        language="en",
        published_at=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=high-match",
        captions="A detailed discussion of agent evaluation.",
    )
    weak_match = SourceCandidate(
        youtube_video_id="weak-match",
        title="A broad technology conversation",
        description="The topic may overlap with the profile.",
        duration_seconds=3600,
        language="en",
        published_at=datetime(2026, 8, 2, 6, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=weak-match",
        captions="A broad and inconclusive conversation.",
    )
    missing_evidence = SourceCandidate(
        youtube_video_id="missing-evidence",
        title="An episode without captions",
        description="No source-language caption track is available.",
        duration_seconds=3600,
        language="en",
        published_at=datetime(2026, 8, 2, 5, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=missing-evidence",
        captions=None,
    )
    overlong = SourceCandidate(
        youtube_video_id="overlong",
        title="A five-hour technical workshop",
        description="The source exceeds the Duration Guardrail.",
        duration_seconds=5 * 60 * 60,
        language="en",
        published_at=datetime(2026, 8, 2, 4, 30, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=overlong",
        captions="A long technical workshop.",
    )
    unsupported_language = SourceCandidate(
        youtube_video_id="unsupported-language",
        title="An otherwise relevant Spanish interview",
        description="The source language is outside the quality boundary.",
        duration_seconds=2400,
        language="es",
        published_at=datetime(2026, 8, 2, 4, 15, tzinfo=UTC),
        source_url=(
            "https://www.youtube.com/watch?v=unsupported-language"
        ),
        captions="Una entrevista sobre productos de inteligencia artificial.",
    )
    irrelevant = SourceCandidate(
        youtube_video_id="irrelevant",
        title="Celebrity trivia",
        description="Entertainment news.",
        duration_seconds=1800,
        language="en",
        published_at=datetime(2026, 8, 2, 4, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=irrelevant",
        captions="Celebrity news and trivia.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[
            high_match,
            weak_match,
            missing_evidence,
            overlong,
            unsupported_language,
            irrelevant,
        ],
        triage_results={
            high_match.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.96,
                rationale="A direct match for reliable agent engineering.",
                matched_interests=("AI products and agents",),
                title_zh=_localized_fixture("high_title"),
                summary_zh=_localized_fixture("high_summary"),
                recommendation_zh=_localized_fixture("high_reason"),
            ),
            weak_match.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.85,
                rationale="The match is plausible but not certain.",
                matched_interests=("technology",),
                title_zh=_localized_fixture("weak_title"),
                summary_zh=_localized_fixture("weak_summary"),
                recommendation_zh=_localized_fixture("weak_reason"),
            ),
            irrelevant.youtube_video_id: TriageResult(
                decision=TriageDecision.IGNORE,
                confidence=0.99,
                rationale="The topic matches an explicit exclusion.",
                matched_interests=(),
                title_zh=_localized_fixture("irrelevant_title"),
                summary_zh=_localized_fixture("irrelevant_summary"),
                recommendation_zh="",
            ),
        },
    )
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    response = client.post("/api/v1/discovery-runs")

    assert response.status_code == 202
    assert len(harness.telegram.messages) == 1
    notification = harness.telegram.latest()
    assert notification.kind == "candidate_recommendation"
    assert notification.title == _localized_fixture("high_title")
    assert _localized_fixture("high_summary") in notification.body
    assert _localized_fixture("high_reason") in notification.body
    assert "How frontier labs build reliable agents" in notification.body
    assert "https://www.youtube.com/watch?v=high-match" in notification.body
    assert "Available Triage Evidence is insufficient." not in notification.body
    assert {action.label for action in notification.actions} == {
        zh_cn("candidate_recommendation.process_action"),
        zh_cn("candidate_recommendation.ignore_action"),
    }
    assert client.get("/api/v1/processing-queue").json()["items"] == []
    decision_items = client.get("/api/v1/decision-queue").json()["items"]
    recommended = next(
        item for item in decision_items if item["youtube_video_id"] == "high-match"
    )
    assert recommended["triage_decision"] == "process"
    assert recommended["title_zh"] == _localized_fixture("high_title")
    assert recommended["summary_zh"] == _localized_fixture("high_summary")

    process_token = next(
        action.token
        for action in notification.actions
        if action.label
        == zh_cn("candidate_recommendation.process_action")
    )
    applied = client.post(f"/api/v1/telegram-actions/{process_token}:apply")

    assert applied.status_code == 200
    assert applied.json()["state"] == "processing_queued"
    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert [item["youtube_video_id"] for item in queue] == ["high-match"]
    assert queue[0]["status"] == "queued"


def test_telegram_candidate_action_uses_the_same_idempotent_decision(
    tmp_path,
) -> None:
    candidate = SourceCandidate(
        youtube_video_id="telegram-decision",
        title="An uncertain product conversation",
        description="Evidence is incomplete.",
        duration_seconds=2400,
        language="en",
        published_at=datetime(2026, 7, 28, 11, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=telegram-decision",
        captions="A conversation with mixed evidence.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[candidate],
        triage_results={
            candidate.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.95,
                rationale="The evidence is a direct interest match.",
                matched_interests=("product strategy",),
                title_zh=_localized_fixture("product_title"),
                summary_zh=_localized_fixture("product_summary"),
                recommendation_zh=_localized_fixture("product_reason"),
            )
        },
    )
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")

    notification = harness.telegram.latest()
    assert notification.kind == "candidate_recommendation"
    assert {action.label for action in notification.actions} == {
        zh_cn("candidate_recommendation.process_action"),
        zh_cn("candidate_recommendation.ignore_action"),
    }
    process_token = next(
        action.token
        for action in notification.actions
        if action.label
        == zh_cn("candidate_recommendation.process_action")
    )

    applied = client.post(f"/api/v1/telegram-actions/{process_token}:apply")
    assert applied.status_code == 200
    assert applied.json()["state"] == "processing_queued"
    assert applied.json()["already_applied"] is False

    repeated = client.post(f"/api/v1/telegram-actions/{process_token}:apply")
    assert repeated.status_code == 200
    assert repeated.json()["state"] == "processing_queued"
    assert repeated.json()["already_applied"] is True
    assert client.get("/api/v1/decision-queue").json()["items"] == []


def test_recommendation_delivery_retry_does_not_duplicate_sent_messages(
    tmp_path,
) -> None:
    first = SourceCandidate(
        youtube_video_id="recommendation-first",
        title="First recommendation",
        description="A clear match.",
        duration_seconds=1800,
        language="en",
        published_at=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=recommendation-first",
        captions="A clear match for the profile.",
    )
    second = SourceCandidate(
        youtube_video_id="recommendation-second",
        title="Second recommendation",
        description="Another clear match.",
        duration_seconds=1800,
        language="en",
        published_at=datetime(2026, 8, 2, 6, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=recommendation-second",
        captions="Another clear match for the profile.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[first, second],
        triage_results={
            first.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.96,
                rationale="A clear match.",
                matched_interests=("AI products",),
                title_zh=_localized_fixture("first_title"),
                summary_zh=_localized_fixture("first_summary"),
                recommendation_zh=_localized_fixture("first_reason"),
            ),
            second.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.97,
                rationale="Another clear match.",
                matched_interests=("AI products",),
                title_zh=_localized_fixture("second_title"),
                summary_zh=_localized_fixture("second_summary"),
                recommendation_zh=_localized_fixture("second_reason"),
            ),
        },
    )
    telegram = FailSecondRecommendationOnce()
    dependencies = replace(
        harness.dependencies,
        notifications=telegram,
    )
    client = TestClient(create_app(dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    first_run = client.post("/api/v1/discovery-runs")
    clock = dependencies.clock
    assert isinstance(clock, FixedClock)
    clock.value += timedelta(minutes=5)
    delivered = TransPodcastService(
        dependencies
    ).deliver_pending_candidate_recommendations()

    assert first_run.status_code == 202
    assert delivered == 1
    assert [message.title for message in telegram.messages] == [
        _localized_fixture("first_title"),
        _localized_fixture("second_title"),
    ]
    with dependencies.database.session() as session:
        assert (
            session.scalar(select(func.count()).select_from(TelegramDeliveryRecord))
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramDeliveryRecord)
                .where(TelegramDeliveryRecord.status == "sent")
            )
            == 2
        )
        message_ids = list(
            session.scalars(
                select(TelegramDeliveryRecord.message_id).order_by(
                    TelegramDeliveryRecord.id
                )
            ).all()
        )
    assert message_ids == ["1", "2"]


def test_uncertain_delivery_outcome_is_not_sent_again(tmp_path) -> None:
    candidate = SourceCandidate(
        youtube_video_id="uncertain-delivery",
        title="A recommendation with a lost response",
        description="A clear match.",
        duration_seconds=1800,
        language="en",
        published_at=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=uncertain-delivery",
        captions="A clear match for AI product interests.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[candidate],
        triage_results={
            candidate.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.98,
                rationale="A direct match.",
                matched_interests=("AI products",),
                title_zh=_localized_fixture("uncertain_title"),
                summary_zh=_localized_fixture("uncertain_summary"),
                recommendation_zh=_localized_fixture("ai_reason"),
            )
        },
    )
    telegram = DeliverThenLoseResponseOnce()
    dependencies = replace(
        harness.dependencies,
        notifications=telegram,
    )
    client = TestClient(create_app(dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    first_run = client.post("/api/v1/discovery-runs")
    delivered = TransPodcastService(
        dependencies
    ).deliver_pending_candidate_recommendations()

    assert first_run.status_code == 202
    assert delivered == 0
    assert len(telegram.messages) == 1
    with dependencies.database.session() as session:
        delivery = session.scalars(select(TelegramDeliveryRecord)).one()
    assert delivery.status == "uncertain"
    assert delivery.attempt_count == 1


def test_automatic_processing_queues_a_clear_match_without_telegram(
    tmp_path,
) -> None:
    candidate = SourceCandidate(
        youtube_video_id="automatic-match",
        title="A strong automatic match",
        description="A clear match for the active profile.",
        duration_seconds=2400,
        language="en",
        published_at=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=automatic-match",
        captions="A detailed discussion about reliable AI products.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[candidate],
        triage_results={
            candidate.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.98,
                rationale="A direct match.",
                matched_interests=("AI products",),
                title_zh=_localized_fixture("automatic_title"),
                summary_zh=_localized_fixture("automatic_summary"),
                recommendation_zh=_localized_fixture("ai_reason"),
            )
        },
    )
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": False})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    response = client.post("/api/v1/discovery-runs")

    assert response.status_code == 202
    assert harness.telegram.messages == []
    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert [item["youtube_video_id"] for item in queue] == ["automatic-match"]
    assert queue[0]["status"] == "queued"


def test_manual_processing_preserves_process_every_video_channel_mode(
    tmp_path,
) -> None:
    candidate = SourceCandidate(
        youtube_video_id="process-every-video",
        title="A video from an always-process channel",
        description="The channel mode bypasses Agent filtering.",
        duration_seconds=2400,
        language="en",
        published_at=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=process-every-video",
        captions="A complete source transcript.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[candidate],
        triage_results=None,
    )
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "process_every_video",
        },
    )

    response = client.post("/api/v1/discovery-runs")

    assert response.json() == {
        "discovered": 1,
        "automatically_accepted": 1,
        "awaiting_decision": 0,
        "ignored": 0,
    }
    assert harness.telegram.messages == []
    assert client.get("/api/v1/decision-queue").json()["items"] == []
    queue = client.get("/api/v1/processing-queue").json()["items"]
    assert [item["youtube_video_id"] for item in queue] == [
        "process-every-video"
    ]
    assert queue[0]["status"] == "pending_start"


def test_non_mandarin_presentation_remains_in_owner_console_only(
    tmp_path,
) -> None:
    candidate = SourceCandidate(
        youtube_video_id="invalid-mandarin",
        title="A strong match with invalid presentation",
        description="The content matches, but localization is malformed.",
        duration_seconds=2400,
        language="en",
        published_at=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=invalid-mandarin",
        captions="A detailed discussion about reliable AI products.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[candidate],
        triage_results={
            candidate.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.98,
                rationale="A direct match.",
                matched_interests=("AI products",),
                title_zh="English-only title",
                summary_zh="English-only summary",
                recommendation_zh="English-only recommendation",
            )
        },
    )
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    response = client.post("/api/v1/discovery-runs")

    assert response.status_code == 202
    assert harness.telegram.messages == []
    assert [
        item["youtube_video_id"]
        for item in client.get("/api/v1/decision-queue").json()["items"]
    ] == ["invalid-mandarin"]
    with harness.dependencies.database.session() as session:
        delivery = session.scalars(select(TelegramDeliveryRecord)).one()
    assert delivery.status == "suppressed"
    assert delivery.last_error == "mandarin_presentation_is_invalid"


def test_ignoring_a_recommendation_records_one_negative_example(
    tmp_path,
) -> None:
    candidate = SourceCandidate(
        youtube_video_id="ignored-recommendation",
        title="A recommendation to ignore",
        description="A clear match that the Owner rejects.",
        duration_seconds=2400,
        language="en",
        published_at=datetime(2026, 8, 2, 7, 0, tzinfo=UTC),
        source_url=("https://www.youtube.com/watch?v=ignored-recommendation"),
        captions="A detailed discussion about AI products.",
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[candidate],
        triage_results={
            candidate.youtube_video_id: TriageResult(
                decision=TriageDecision.PROCESS,
                confidence=0.98,
                rationale="A direct match.",
                matched_interests=("AI products",),
                title_zh=_localized_fixture("ignored_title"),
                summary_zh=_localized_fixture("ignored_summary"),
                recommendation_zh=_localized_fixture("ai_reason"),
            )
        },
    )
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    ignore_token = next(
        action.token
        for action in harness.telegram.latest().actions
        if action.label
        == zh_cn("candidate_recommendation.ignore_action")
    )

    response = client.post(f"/api/v1/telegram-actions/{ignore_token}:apply")

    assert response.status_code == 200
    assert response.json()["state"] == "ignored"
    with harness.dependencies.database.session() as session:
        examples = list(session.scalars(select(LabeledExampleRecord)).all())
    assert [example.label for example in examples] == ["negative"]
