from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import SourceCandidate, TranslationResult
from transpodcast.models import ProcessingRunActivityRecord, ProcessingRunRecord
from transpodcast.service import (
    _latest_recorded_at,
    _owner_current_step,
    _pipeline_steps,
    _safe_error_summary,
)
from transpodcast.testing import (
    AcceptanceHarness,
    FakeTranslationAdapter,
    capture_sql_statements,
    seed_owner_read_performance_fixture,
)


class FailingTranslationAdapter(FakeTranslationAdapter):
    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        raise RuntimeError("Codex stopped before completing the project")


def test_failed_reviewer_project_stays_on_review_step() -> None:
    run = ProcessingRunRecord(stage="translation", status="review_queue")

    assert _owner_current_step(
        run,
        {"phase": "failed", "failed_role": "reviewer"},
    ) == "translation_review"


def test_pipeline_steps_use_the_owner_facing_state_contract() -> None:
    active = ProcessingRunRecord(stage="translation", status="processing")
    waiting = ProcessingRunRecord(stage="render", status="retry_wait")

    assert {
        item["id"]: item["state"]
        for item in _pipeline_steps(active, [], None)
    }["translation_authoring"] == "active"
    assert {
        item["id"]: item["state"]
        for item in _pipeline_steps(waiting, [], None)
    }["voice_render"] == "waiting_to_retry"


def test_completed_legacy_run_marks_content_editing_not_applicable() -> None:
    legacy = ProcessingRunRecord(
        stage="publication_queue",
        status="completed",
        content_edit_json=None,
    )

    steps = {
        item["id"]: item["state"]
        for item in _pipeline_steps(legacy, [], None)
    }

    assert steps["content_editing"] == "not_applicable"


def test_owner_facing_errors_redact_paths_and_credentials() -> None:
    assert _safe_error_summary(
        "quality_gate",
        "File /Users/owner/check.py failed with token=private",
    ) == "Processing stopped; technical details were redacted."


def test_latest_change_accepts_legacy_naive_timestamps() -> None:
    legacy = datetime(2026, 7, 31, 5, 0)
    current = datetime(2026, 7, 31, 5, 1, tzinfo=UTC)

    assert _latest_recorded_at([legacy, current]) == current


def _failed_translation_run(tmp_path) -> TestClient:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        translation=FailingTranslationAdapter(),
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    outcome = client.post("/api/v1/processing-runs:advance")
    assert outcome.json()["processing_state"] == "review_queue"
    return client


def test_processing_run_detail_projects_pipeline_and_current_failure(
    tmp_path,
) -> None:
    client = _failed_translation_run(tmp_path)

    detail = client.get("/api/v1/processing-runs/1").json()

    assert detail["title"] == "Building products that last"
    assert detail["source_show"] == "Useful Show"
    assert detail["owner_state"] == "action_required"
    assert detail["current_step"] == "translation_authoring"
    assert detail["current_error"] == {
        "kind": "translation_provider",
        "summary": "Translation provider did not complete.",
        "step": "translation_authoring",
        "retryable": True,
    }
    steps = {step["id"]: step for step in detail["pipeline_steps"]}
    assert steps["source_archive"]["state"] == "completed"
    assert steps["source_analysis"]["state"] == "completed"
    assert steps["translation_authoring"]["state"] == "action_required"
    assert steps["translation_review"]["state"] == "not_started"
    assert steps["voice_render"]["state"] == "not_started"
    assert detail["attempt_history"][-1]["is_current_blocker"] is True


def test_processing_run_diagnostics_are_bounded_and_redacted(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)

    class LeakyTranslationAdapter(FakeTranslationAdapter):
        def translate(
            self,
            candidate: SourceCandidate,
        ) -> TranslationResult:
            raise RuntimeError(
                'Traceback: File "/Users/owner/project.py" '
                "HF_TOKEN=private-value"
            )

    client = TestClient(
        create_app(
            replace(
                harness.dependencies,
                translation=LeakyTranslationAdapter(),
            )
        )
    )
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    client.post("/api/v1/processing-runs:advance")

    diagnostics = client.get(
        "/api/v1/processing-runs/1/diagnostics"
    ).json()
    serialized = str(diagnostics)

    assert diagnostics["schema_version"] == (
        "processing-run-diagnostics-v1"
    )
    assert diagnostics["current_error"]["summary"] == (
        "Translation provider did not complete."
    )
    assert "/Users/" not in serialized
    assert "private-value" not in serialized
    assert "Traceback" not in serialized


def test_schema_failure_restores_usage_from_the_durable_project(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)

    class InvalidProjectTranslationAdapter(FakeTranslationAdapter):
        def translate(
            self,
            candidate: SourceCandidate,
        ) -> TranslationResult:
            project = (
                harness.dependencies.artifact_root
                / "translation-projects"
                / f"run-{candidate.processing_run_id}"
                / "fixture-project"
            )
            project.mkdir(parents=True)
            project.joinpath("project.json").write_text(
                json.dumps(
                    {
                        "phase": "failed",
                        "policy_version": "translation-policy-v3-project",
                        "completed_targets": 1,
                        "total_targets": 3,
                        "input_tokens": 71,
                        "output_tokens": 16,
                        "author_turns": 1,
                        "reviewer_turns": 0,
                        "author_sessions": ["author-session"],
                        "reviewer_sessions": [],
                        "turns": [
                            {
                                "role": "author",
                                "turn": 1,
                                "session_id": "author-session",
                                "model": "codex-fixture",
                                "input_tokens": 71,
                                "output_tokens": 16,
                                "outcome": "completed",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            raise ValueError("Translation worksheet anchors were modified")

    client = TestClient(
        create_app(
            replace(
                harness.dependencies,
                translation=InvalidProjectTranslationAdapter(),
            )
        )
    )
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    client.post("/api/v1/processing-runs:advance")

    detail = client.get("/api/v1/processing-runs/1").json()
    assert detail["translation"]["input_tokens"] == 71
    assert detail["translation"]["output_tokens"] == 16
    assert [
        item["metadata"]["turn"]
        for item in detail["activity"]
        if item["kind"] == "translation_turn"
    ] == [1]


def test_successful_retry_clears_current_error_but_keeps_history(
    tmp_path,
) -> None:
    client = _failed_translation_run(tmp_path)
    failed_revision = client.get(
        "/api/v1/processing-runs/1"
    ).json()["revision"]

    retried = client.post("/api/v1/processing-runs/1:retry")

    assert retried.status_code == 200
    body = retried.json()
    assert body["revision"] != failed_revision
    assert body["owner_state"] == "queued"
    assert body["current_error"] is None
    assert body["retry_plan"] == {
        "resume_step": "translation_authoring",
        "reused_steps": ["source_archive", "source_analysis"],
        "invalidated_steps": [
            "translation_authoring",
            "translation_review",
            "content_editing",
            "voice_render",
            "publication_audio_quality",
            "publication_queue",
        ],
        "preserves_translation_targets": False,
    }

    detail = client.get("/api/v1/processing-runs/1").json()
    failed = detail["attempt_history"][-1]
    assert failed["status"] == "failed"
    assert failed["is_current_blocker"] is False
    assert failed["superseded"] is True
    assert [
        item["kind"]
        for item in detail["activity"]
        if item["kind"] in {"failure", "retry_requested"}
    ] == ["failure", "retry_requested"]


def test_processing_run_list_has_stable_aggregate_revision(tmp_path) -> None:
    client = _failed_translation_run(tmp_path)

    first = client.get("/api/v1/processing-runs").json()
    second = client.get("/api/v1/processing-runs").json()

    assert first["revision"] == second["revision"]
    assert first["items"][0]["revision"]


def test_processing_run_list_revision_changes_with_visible_state(
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
    queued = client.get("/api/v1/processing-runs").json()

    client.post("/api/v1/processing-runs:advance")
    completed = client.get("/api/v1/processing-runs").json()

    assert queued["items"][0]["id"] == completed["items"][0]["id"]
    assert queued["items"][0]["revision"] != (
        completed["items"][0]["revision"]
    )
    assert queued["revision"] != completed["revision"]


def test_processing_run_list_shows_work_created_after_an_earlier_read(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    empty = client.get("/api/v1/processing-runs").json()
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    queued = client.get("/api/v1/processing-runs").json()

    assert empty["items"] == []
    assert len(queued["items"]) == 1
    assert queued["items"][0]["status"] == "queued"
    assert empty["revision"] != queued["revision"]


def test_processing_run_list_returns_summaries_and_detail_stays_on_demand(
    tmp_path,
) -> None:
    client = _failed_translation_run(tmp_path)

    summary = client.get("/api/v1/processing-runs").json()["items"][0]

    assert set(summary) == {
        "id",
        "revision",
        "status",
        "title",
        "source_show",
        "owner_state",
        "current_step",
        "estimated_cost_usd",
        "translation",
        "latest_change_at",
    }
    assert summary["translation"] == {
        "input_tokens": 0,
        "output_tokens": 0,
    }

    detail = client.get(f"/api/v1/processing-runs/{summary['id']}").json()

    assert detail["attempt_history"]
    assert detail["activity"]
    assert "translation_project" in detail


def _summary_list_client(
    tmp_path,
    run_count: int,
) -> tuple[TestClient, AcceptanceHarness]:
    harness = AcceptanceHarness.ready(tmp_path)
    seed_owner_read_performance_fixture(
        harness.dependencies.database,
        run_count=run_count,
        artifact_payload_bytes=16_384,
        include_episodes=False,
    )
    return (
        TestClient(create_app(harness.dependencies)),
        harness,
    )


def _processing_run_list_statement_count(
    client: TestClient,
    harness: AcceptanceHarness,
) -> tuple[int, dict[str, object], list[str]]:
    with capture_sql_statements(harness.dependencies.database) as statements:
        response = client.get("/api/v1/processing-runs")
    assert response.status_code == 200
    return len(statements), response.json(), statements


def test_processing_run_list_database_work_is_bounded_by_request(
    tmp_path,
) -> None:
    one_client, one_harness = _summary_list_client(tmp_path / "one", 1)
    many_client, many_harness = _summary_list_client(tmp_path / "many", 25)

    one_count, one_response, one_statements = (
        _processing_run_list_statement_count(
            one_client,
            one_harness,
        )
    )
    many_count, many_response, many_statements = (
        _processing_run_list_statement_count(
            many_client,
            many_harness,
        )
    )

    assert len(one_response["items"]) == 1
    assert len(many_response["items"]) == 25
    assert many_count <= one_count + 1
    selected_sql = "\n".join([*one_statements, *many_statements]).lower()
    for detail_column in (
        "processing_runs.analysis_json",
        "processing_runs.translation_json",
        "processing_runs.content_edit_json",
        "processing_runs.publication_metadata_json",
        "processing_runs.artwork_json",
        "processing_runs.render_json",
    ):
        assert detail_column not in selected_sql


def test_processing_run_summary_uses_durable_translation_phase(
    tmp_path,
) -> None:
    client, harness = _summary_list_client(tmp_path, 1)
    recorded_at = datetime(2026, 8, 10, 8, 5, tzinfo=UTC)
    with harness.dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, 1)
        assert run is not None
        run.status = "processing"
        run.stage = "translation"
        session.add(
            ProcessingRunActivityRecord(
                processing_run_id=run.id,
                kind="translation_phase",
                stage="translation_review",
                summary="Translation Review started",
                metadata_json="{}",
                attempt_id=None,
                created_at=recorded_at,
            )
        )
    project = (
        harness.dependencies.artifact_root
        / "translation-projects"
        / "run-1"
        / "unreadable-project"
    )
    project.mkdir(parents=True)
    project.joinpath("project.json").write_text("not json", encoding="utf-8")

    response = client.get("/api/v1/processing-runs")

    assert response.status_code == 200
    assert response.json()["items"][0]["current_step"] == (
        "translation_review"
    )
