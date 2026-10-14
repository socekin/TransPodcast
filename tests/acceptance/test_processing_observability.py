from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import (
    AnalyzedSpeaker,
    SourceAnalysisResult,
    SourceArchive,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.errors import ProviderExecutionError
from transpodcast.models import ProcessingAttemptRecord, ProcessingRunRecord
from transpodcast.service import TransPodcastService
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    FakeSpeechAdapter,
    FakeTranslationAdapter,
)


class FlakyTranslationAdapter(FakeTranslationAdapter):
    """Fails once, then succeeds after a measurable delay."""

    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("Codex CLI crashed before producing output")
        time.sleep(0.02)
        return super().translate(candidate)


class RoutedAnalysisAdapter(FakeAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        result = super().analyze(candidate, archive)
        return replace(
            result,
            speakers=(
                *result.speakers,
                AnalyzedSpeaker(
                    local_label="unused-speaker-candidate",
                    reference_audio=b"RIFFunused",
                    embedding=(0.0, 1.0, 0.0),
                    confidence=0.55,
                ),
            ),
            routing_json=json.dumps(
                {
                    "operation": "analyze",
                    "selected_profile": "transpodcast-secondary",
                    "processing_run_id": candidate.processing_run_id,
                    "processing_attempt_id": (
                        candidate.processing_attempt_id
                    ),
                    "correlation_id": (
                        candidate.processing_correlation_id
                    ),
                    "attempts": [
                        {
                            "profile": "transpodcast-primary",
                            "outcome": "failed",
                            "reason": "command_failed",
                        },
                        {
                            "profile": "transpodcast-secondary",
                            "outcome": "succeeded",
                        },
                    ],
                }
            ),
        )


class RoutedSpeechAdapter(FakeSpeechAdapter):
    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        return replace(
            super().render(candidate, translation),
            render_model="IndexTeam/IndexTTS-2.5",
            render_settings_json=json.dumps(
                {
                    "pronunciation_policy_version": (
                        "indextts25-pronunciation-v2"
                    ),
                    "pronunciation_annotation_count": 12,
                    "pronunciation_fallback_count": 1,
                    "chinese_replacement_count": 2,
                }
            ),
            routing_json=(
                '{"operation":"render","selected_profile":"transpodcast",'
                '"attempts":[{"profile":"transpodcast",'
                '"outcome":"succeeded"}]}'
            ),
        )


class BlockingAnalysisAdapter(FakeAnalysisAdapter):
    def __init__(self, started: Event, release: Event) -> None:
        super().__init__()
        self.started = started
        self.release = release

    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        self.started.set()
        self.release.wait(timeout=5)
        return super().analyze(candidate, archive)


class FailedRoutedAnalysisAdapter(FakeAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        del candidate, archive
        raise ProviderExecutionError(
            "Modal speech pipeline failed",
            routing_json=(
                '{"operation":"analyze","attempts":['
                '{"profile":"transpodcast-primary","outcome":"failed",'
                '"reason":"command_failed"}]}'
            ),
        )


def _advance_first_run(client: TestClient) -> dict[str, object]:
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    return client.post("/api/v1/processing-runs:advance").json()


def test_every_stage_records_a_durable_timed_attempt(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        analysis=RoutedAnalysisAdapter(),
        speech=RoutedSpeechAdapter(),
    )
    client = TestClient(create_app(dependencies))

    outcome = _advance_first_run(client)
    assert outcome["processing_state"] == "completed"

    with dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, 1)
        assert run is not None
        assert run.render_preview_key is not None
        fixture_audio = dependencies.storage.read_preview(
            run.render_preview_key
        )
    assert fixture_audio is not None
    assert hashlib.sha256(fixture_audio).hexdigest() == (
        "dd0b4f839e6fcd71b5ecee015af9871f"  # pragma: allowlist secret
        "0da0ed692dd69def42be60f9d38bb00f"  # pragma: allowlist secret
    )

    detail = client.get("/api/v1/processing-runs/1").json()
    attempts = detail["attempts"]
    assert [item["stage"] for item in attempts] == [
        "source_archive",
        "source_analysis",
        "translation",
        "render",
        "quality_gate",
    ]
    assert {item["status"] for item in attempts} == {"completed"}
    by_stage = {item["stage"]: item for item in attempts}
    # Transfer sizes make repeated full-audio movement visible.
    assert by_stage["source_archive"]["transfer_bytes"] > 0
    assert by_stage["source_analysis"]["transfer_bytes"] > 0
    assert by_stage["render"]["transfer_bytes"] > 0
    # Each attempt records the checkpoint a retry would resume from.
    assert by_stage["source_archive"]["resume_checkpoint"] == ("source_archive")
    assert by_stage["translation"]["resume_checkpoint"] == "translation"
    assert all(item["duration_ms"] >= 0 for item in attempts)
    assert all(item["started_at"] for item in attempts)
    assert all(item["completed_at"] for item in attempts)
    assert all(item["completed_at"] >= item["started_at"] for item in attempts)
    assert all(item["correlation_id"] for item in attempts)
    # The stage checkpoints now carry real durations too.
    stage_durations = {item["stage"]: item["duration_ms"] for item in detail["stages"]}
    assert set(stage_durations) == {
        "source_archive",
        "source_analysis",
        "translation",
        "render",
        "quality_gate",
    }
    analysis_stage = next(
        item for item in detail["stages"] if item["stage"] == "source_analysis"
    )
    # Modal per-call inference seconds are preserved in the stage usage.
    assert analysis_stage["usage"]["call_seconds"] == {
        "diarization": 1.2,
        "speaker_embeddings": 0.3,
        "transcription": 2.4,
    }
    analysis_attempt = by_stage["source_analysis"]
    assert analysis_stage["started_at"] == analysis_attempt["started_at"]
    assert analysis_stage["completed_at"] == analysis_attempt["completed_at"]
    assert analysis_attempt["routing"]["selected_profile"] == (
        "transpodcast-secondary"
    )
    pipeline = {item["id"]: item for item in detail["pipeline_steps"]}
    assert pipeline["source_analysis"]["evidence_available"] is True
    assert pipeline["source_analysis"]["metrics"]["segments"] > 0
    assert pipeline["source_analysis"]["metrics"]["speakers"] == 1
    assert pipeline["source_analysis"]["metrics"]["raw_speaker_candidates"] == 2
    assert pipeline["source_analysis"]["metrics"]["call_seconds"] == {
        "diarization": 1.2,
        "speaker_embeddings": 0.3,
        "transcription": 2.4,
    }
    assert pipeline["voice_render"]["metrics"]["rendered_segments"] > 0
    assert pipeline["voice_render"]["metrics"].get(
        "pronunciation_policy_version"
    ) == "indextts25-pronunciation-v2"
    assert pipeline["voice_render"]["metrics"].get(
        "pronunciation_annotation_count"
    ) == 12
    assert pipeline["voice_render"]["metrics"].get(
        "pronunciation_fallback_count"
    ) == 1
    assert pipeline["voice_render"]["metrics"].get(
        "chinese_replacement_count"
    ) == 2
    assert (
        pipeline["source_analysis"]["technical_evidence"]["modal_account"]
        == "transpodcast-secondary"
    )
    assert pipeline["voice_render"]["technical_evidence"]["modal_route"] == [
        "transpodcast"
    ]
    assert pipeline["publication_audio_quality"]["metrics"]["loudness_lufs"] == -16.0
    assert pipeline["translation_review"]["evidence_available"] is False
    started_activity = [
        item for item in detail["activity"] if item["kind"] == "stage_started"
    ]
    assert {item["stage"] for item in started_activity} == {
        "source_archive",
        "source_analysis",
        "translation",
        "render",
        "quality_gate",
    }
    assert all(item["attempt_id"] for item in started_activity)
    failover_activity = [
        item for item in detail["activity"] if item["kind"] == "modal_failover"
    ]
    assert len(failover_activity) == 1
    assert failover_activity[0]["attempt_id"] == analysis_attempt["id"]
    assert failover_activity[0]["metadata"]["correlation_id"] == (
        analysis_attempt["correlation_id"]
    )


def test_failed_attempts_are_appended_and_survive_the_retry(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    flaky = FlakyTranslationAdapter()
    dependencies = replace(harness.dependencies, translation=flaky)
    client = TestClient(create_app(dependencies))

    outcome = _advance_first_run(client)
    assert outcome["processing_state"] == "review_queue"

    detail = client.get("/api/v1/processing-runs/1").json()
    failed = [item for item in detail["attempts"] if item["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["stage"] == "translation"
    assert failed[0]["error_kind"] == "translation_provider"
    assert failed[0]["error_summary"] == ("Translation provider did not complete.")
    # The failed translation resumes from the completed analysis.
    assert failed[0]["resume_checkpoint"] == "source_analysis"

    retried = client.post("/api/v1/processing-runs/1:retry")
    assert retried.status_code == 200
    advanced = client.post("/api/v1/processing-runs:advance").json()
    assert advanced["processing_state"] == "completed"

    detail = client.get("/api/v1/processing-runs/1").json()
    translation_attempts = [
        item for item in detail["attempts"] if item["stage"] == "translation"
    ]
    # Append-only: the failed attempt is preserved next to the
    # successful one, and the retry timing is real wall time.
    assert [item["status"] for item in translation_attempts] == [
        "failed",
        "completed",
    ]
    assert translation_attempts[1]["duration_ms"] >= 10


def test_failed_modal_route_is_retained_on_the_stage_attempt(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        analysis=FailedRoutedAnalysisAdapter(),
    )
    client = TestClient(create_app(dependencies))

    outcome = _advance_first_run(client)
    assert outcome["processing_state"] == "review_queue"

    detail = client.get("/api/v1/processing-runs/1").json()
    attempt = detail["attempts"][-1]
    assert attempt["stage"] == "source_analysis"
    assert attempt["routing"]["attempts"] == [
        {
            "profile": "transpodcast-primary",
            "outcome": "failed",
            "reason": "command_failed",
        }
    ]


def test_analysis_checkpoint_retry_keeps_routing_on_original_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        analysis=RoutedAnalysisAdapter(),
    )
    original = TransPodcastService._complete_boundary_adjudication
    calls = 0

    def fail_local_completion_once(self, session, run, analysis, archive):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Local adjudication failed")
        return original(self, session, run, analysis, archive)

    monkeypatch.setattr(
        TransPodcastService,
        "_complete_boundary_adjudication",
        fail_local_completion_once,
    )
    client = TestClient(create_app(dependencies))

    assert _advance_first_run(client)["processing_state"] == "review_queue"
    failed = client.get("/api/v1/processing-runs/1").json()["attempts"][-1]
    assert failed["routing"]["processing_attempt_id"] == failed["id"]

    assert client.post("/api/v1/processing-runs/1:retry").status_code == 200
    assert (
        client.post("/api/v1/processing-runs:advance").json()[
            "processing_state"
        ]
        == "completed"
    )
    detail = client.get("/api/v1/processing-runs/1").json()
    analysis_attempts = [
        item
        for item in detail["attempts"]
        if item["stage"] == "source_analysis"
    ]
    assert [item["status"] for item in analysis_attempts] == [
        "failed",
        "completed",
    ]
    assert analysis_attempts[1]["routing"] == {}
    analysis_stage = next(
        item for item in detail["stages"] if item["stage"] == "source_analysis"
    )
    assert analysis_stage["usage"]["routing"]["selected_profile"] == (
        "transpodcast-secondary"
    )


def test_active_source_analysis_is_visible_before_provider_completion(
    tmp_path,
) -> None:
    started = Event()
    release = Event()
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        analysis=BlockingAnalysisAdapter(started, release),
    )
    app = create_app(dependencies)
    setup_client = TestClient(app)
    setup_client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    setup_client.post("/api/v1/discovery-runs")

    with ThreadPoolExecutor(max_workers=1) as executor:
        advancing = executor.submit(
            TestClient(app).post,
            "/api/v1/processing-runs:advance",
        )
        assert started.wait(timeout=2)
        with dependencies.database.session() as session:
            run = session.get(ProcessingRunRecord, 1)
            assert run is not None
            run.stage = "translation"
        detail = TestClient(app).get("/api/v1/processing-runs/1").json()

        assert detail["current_step"] == "source_analysis"
        analysis_step = next(
            item for item in detail["pipeline_steps"] if item["id"] == "source_analysis"
        )
        assert analysis_step["state"] == "active"
        assert analysis_step["started_at"] is not None
        assert analysis_step["completed_at"] is None
        assert detail["attempts"][-1]["status"] == "processing"

        release.set()
        assert advancing.result(timeout=5).status_code == 200


def test_active_local_mastering_is_identified_as_non_modal(tmp_path) -> None:
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
    with harness.dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, 1)
        assert run is not None
        run.status = "processing"
        run.stage = "mastering_repair"
        session.add(
            ProcessingAttemptRecord(
                processing_run_id=run.id,
                stage="mastering_repair",
                status="processing",
                duration_ms=0,
                transfer_bytes=0,
                resume_checkpoint="render",
                created_at=harness.dependencies.clock.now(),
                correlation_id="local-mastering-1",
                routing_json="{}",
                timing_provenance="recorded",
            )
        )

    detail = client.get("/api/v1/processing-runs/1").json()
    quality = next(
        step
        for step in detail["pipeline_steps"]
        if step["id"] == "publication_audio_quality"
    )
    assert quality["state"] == "active"
    assert quality["technical_evidence"]["provider"] == "local"
    assert quality["technical_evidence"]["modal_account"] is None
