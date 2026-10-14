"""The Owner selects the Agent provider; each Processing Run freezes it."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from transpodcast.adapters.agent import AgentInvocationError
from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate, TranslationResult
from transpodcast.errors import TranslationProviderError
from transpodcast.models import (
    ProcessingRunActivityRecord,
    ProcessingRunRecord,
    ProcessingStageRecord,
)
from transpodcast.service import TransPodcastService
from transpodcast.testing import (
    AcceptanceHarness,
    FakeTranslationAdapter,
    FakeTriageAdapter,
)


def _catalog() -> list[SourceCandidate]:
    return [
        SourceCandidate(
            youtube_video_id=f"useful-{index:03d}",
            title=f"Useful conversation {index}",
            description=f"Complete notes for conversation {index}.",
            duration_seconds=3600 + index,
            language="en",
            published_at=datetime(2026, 7, 27 - index, 8, 0, tzinfo=UTC),
            source_url=f"https://www.youtube.com/watch?v=useful-{index:03d}",
            captions=f"Captions for conversation {index}.",
        )
        for index in range(3)
    ]


def _import_video(client: TestClient, index: int) -> int:
    resolution = client.post(
        "/api/v1/source-resolutions",
        json={"url": f"https://www.youtube.com/watch?v=useful-{index:03d}"},
    ).json()
    imported = client.post(
        f"/api/v1/source-resolutions/{resolution['resolution_id']}:import-video"
    ).json()
    return int(imported["processing_run_id"])


def _run(harness: AcceptanceHarness, run_id: int) -> ProcessingRunRecord:
    with harness.dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, run_id)
        assert run is not None
        return run


def _advance_discovered_run(client: TestClient) -> dict[str, object]:
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    advanced: dict[str, object] = client.post(
        "/api/v1/processing-runs:advance"
    ).json()
    return advanced


def _completed_episode(client: TestClient) -> tuple[int, int]:
    completed = _advance_discovered_run(client)
    return int(str(completed["episode_id"])), int(
        str(completed["processing_run_id"])
    )


def test_agent_provider_setting_round_trips_and_rejects_unknown_values(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    defaults = client.get("/api/v1/settings").json()
    assert defaults["agent"] == {
        "provider": "codex",
        "readiness": {"codex": "configured", "claude": "configured"},
    }
    assert defaults["secrets"]["agent"] == "configured"

    changed = client.patch("/api/v1/settings", json={"agent_provider": "claude"})
    assert changed.status_code == 200
    assert changed.json()["agent"]["provider"] == "claude"
    assert client.get("/api/v1/settings").json()["agent"]["provider"] == (
        "claude"
    )

    rejected = client.patch("/api/v1/settings", json={"agent_provider": "gemini"})
    assert rejected.status_code == 422
    assert client.get("/api/v1/settings").json()["agent"]["provider"] == (
        "claude"
    )

    service = TransPodcastService(harness.dependencies)
    try:
        service.update_settings({"agent_provider": "gemini"})
    except ValueError as error:
        assert "Unsupported Agent provider" in str(error)
    else:  # pragma: no cover - the assertion above must run
        raise AssertionError("an unsupported Agent provider was accepted")


def test_agent_provider_is_frozen_when_each_run_is_created(tmp_path) -> None:
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=_catalog(),
        triage_results=None,
    )
    client = TestClient(create_app(harness.dependencies))
    client.patch(
        "/api/v1/settings",
        json={"manual_processing": True, "agent_provider": "claude"},
    )
    claude_run_id = _import_video(client, 0)
    client.patch("/api/v1/settings", json={"agent_provider": "codex"})
    codex_run_id = _import_video(client, 1)

    assert _run(harness, claude_run_id).agent_provider == "claude"
    assert _run(harness, codex_run_id).agent_provider == "codex"

    # Changing the setting after queueing never moves a queued run.
    started = client.post(f"/api/v1/processing-runs/{claude_run_id}:start")
    assert started.status_code in {200, 202}
    advanced = client.post("/api/v1/processing-runs:advance").json()
    assert advanced["processing_run_id"] == claude_run_id
    assert advanced["processing_state"] == "completed"
    assert harness.claude.invocation_count == 1
    assert harness.codex.invocation_count == 0

    claude_run = _run(harness, claude_run_id)
    assert claude_run.translation_provider == "fake-claude"
    with harness.dependencies.database.session() as session:
        stage = session.scalar(
            select(ProcessingStageRecord).where(
                ProcessingStageRecord.processing_run_id == claude_run_id,
                ProcessingStageRecord.stage == "translation",
            )
        )
        assert stage is not None
        usage = json.loads(stage.usage_json)
    assert usage["agent_provider"] == "claude"
    assert "cli_version" in usage
    assert "codex_cli_version" not in usage

    client.post(f"/api/v1/processing-runs/{codex_run_id}:start")
    client.post("/api/v1/processing-runs:advance")
    assert harness.codex.invocation_count == 1
    assert harness.claude.invocation_count == 1


def test_historical_runs_without_a_provider_use_codex(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"agent_provider": "claude"})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    with harness.dependencies.database.session() as session:
        run = session.scalars(select(ProcessingRunRecord)).one()
        run.agent_provider = None

    client.post("/api/v1/processing-runs:advance")

    assert harness.codex.invocation_count == 1
    assert harness.claude.invocation_count == 0


def test_explicit_reprocess_rereads_the_agent_provider(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    episode_id, run_id = _completed_episode(client)
    assert _run(harness, run_id).agent_provider == "codex"
    assert harness.codex.invocation_count == 1

    client.patch("/api/v1/settings", json={"agent_provider": "claude"})
    # A retry or a resumed run keeps its provider; only reprocess re-reads.
    assert _run(harness, run_id).agent_provider == "codex"
    requested = client.post(f"/api/v1/episodes/{episode_id}:reprocess")
    assert requested.status_code == 202

    assert _run(harness, run_id).agent_provider == "claude"
    with harness.dependencies.database.session() as session:
        activity = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run_id,
                ProcessingRunActivityRecord.kind == "reprocess_requested",
            )
        ).one()
    metadata = json.loads(activity.metadata_json)
    assert metadata["previous_agent_provider"] == "codex"
    assert metadata["selected_agent_provider"] == "claude"

    rerun = client.post("/api/v1/processing-runs:advance").json()
    assert rerun["processing_state"] == "completed"
    assert harness.claude.invocation_count == 1
    assert harness.codex.invocation_count == 1
    assert _run(harness, run_id).translation_provider == "fake-claude"


def test_triage_uses_the_current_agent_provider(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))
    codex_triage = harness.dependencies.triage
    claude_triage = harness.dependencies.agent_triage["claude"]
    assert isinstance(codex_triage, FakeTriageAdapter)
    assert isinstance(claude_triage, FakeTriageAdapter)
    client.patch("/api/v1/settings", json={"agent_provider": "claude"})
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    client.post("/api/v1/discovery-runs")

    assert claude_triage.invocation_count == 1
    assert codex_triage.invocation_count == 0


class UnreadyTranslationAdapter(FakeTranslationAdapter):
    def readiness(self) -> bool:
        return False

    def provider_status(self) -> dict[str, object]:
        return {
            "ready": False,
            "identity": "claude-code",
            "cli_version": "",
            "auth_method": "",
        }


def test_setup_readiness_reports_the_selected_provider_and_both_clis(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        agent_translations={"claude": UnreadyTranslationAdapter("fake-claude")},
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )

    readiness = client.get("/api/v1/setup-readiness").json()
    assert readiness["ready"] is True
    assert "codex" not in readiness["services"]
    assert readiness["services"]["agent"] is True
    assert readiness["agent"]["provider"] == "codex"
    assert readiness["agent"]["providers"]["codex"]["ready"] is True
    assert readiness["agent"]["providers"]["claude"]["ready"] is False
    assert readiness["agent"]["providers"]["claude"]["identity"] == (
        "claude-code"
    )

    client.patch("/api/v1/settings", json={"agent_provider": "claude"})
    blocked = client.get("/api/v1/setup-readiness").json()
    assert blocked["ready"] is False
    assert blocked["services"]["agent"] is False
    assert blocked["agent"]["provider"] == "claude"
    assert blocked["details"]["agent"]["impact"]
    assert client.get("/api/v1/settings").json()["agent"]["readiness"] == {
        "codex": "configured",
        "claude": "missing",
    }


class ClassifiedFailureTranslation(FakeTranslationAdapter):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        self.invocation_count += 1
        raise self.error


def _fail_translation(tmp_path, error: Exception) -> ProcessingRunRecord:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        translation=ClassifiedFailureTranslation(error),
    )
    client = TestClient(create_app(dependencies))
    run_id = int(str(_advance_discovered_run(client)["processing_run_id"]))
    with dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, run_id)
        assert run is not None
        return run


def test_agent_rate_limit_uses_the_transient_retry_policy(tmp_path) -> None:
    run = _fail_translation(
        tmp_path,
        TranslationProviderError(
            "Claude Code CLI failed (rate_limit): usage limit",
            input_tokens=40,
            output_tokens=5,
            kind="rate_limit",
        ),
    )

    assert run.status == "retry_wait"
    assert run.error_kind == "transient"
    assert run.next_retry_at is not None
    assert run.input_tokens == 40


def test_agent_auth_failure_surfaces_immediately(tmp_path) -> None:
    run = _fail_translation(
        tmp_path,
        AgentInvocationError("Claude Code CLI is not available", kind="auth"),
    )

    assert run.status == "review_queue"
    assert run.error_kind == "agent_auth"
    assert run.next_retry_at is None


def test_agent_schema_and_provider_failures_keep_stage_error_kinds(
    tmp_path,
) -> None:
    schema = _fail_translation(
        tmp_path / "schema",
        TranslationProviderError(
            "structured output retries exhausted",
            input_tokens=1,
            output_tokens=1,
            kind="schema",
        ),
    )
    provider = _fail_translation(
        tmp_path / "provider",
        TranslationProviderError(
            "provider timeout",
            input_tokens=1,
            output_tokens=1,
            kind="provider",
        ),
    )

    assert schema.error_kind == "translation_schema"
    assert provider.error_kind == "translation_provider"
    assert provider.status == "review_queue"
