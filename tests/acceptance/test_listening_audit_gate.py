"""The Quality Gate applies the Listening Audit D3 policy for V19/V20.

Remaining hard findings send the run to the Review Queue; soft findings and
an unavailable audit are listed on the Publication Queue item and never
block. The speech adapter is a fake; no test reaches Modal.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import (
    AudioQualityReport,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V19
from transpodcast.service import (
    TransPodcastService,
    _deserialize_speech_checkpoint,
    _serialize_speech_checkpoint,
)
from transpodcast.testing import AcceptanceHarness, FakeSpeechAdapter


def _finding(kind: str, severity: str, detail: str) -> dict[str, Any]:
    return {
        "unit_id": "seg-0001",
        "parent_segment_id": "seg-0001",
        "kind": kind,
        "severity": severity,
        "detail": detail,
        "output_start_seconds": 0.0,
        "output_end_seconds": 1.0,
    }


def _audit(
    findings: list[dict[str, Any]],
    *,
    blocking: list[str] | None = None,
) -> dict[str, Any]:
    hard = sorted({item["unit_id"] for item in findings if item["severity"] == "hard"})
    return {
        "policy_version": "listening-audit-v1",
        "status": "complete",
        "scope": "episode",
        "unit_count": 1,
        "audited_unit_count": 1,
        "transcribed_unit_count": 1,
        "mean_cer": 0.6,
        "hard_unit_ids": hard,
        "blocking_unit_ids": blocking if blocking is not None else hard,
        "findings": findings,
        "retry": (
            {
                "seed": 43,
                "status": "rendered",
                "unit_ids": hard,
                "resolved_unit_ids": [],
            }
            if hard
            else None
        ),
        "usage": {"gpu_seconds": 4.2},
    }


class _AuditedSpeechAdapter(FakeSpeechAdapter):
    def __init__(
        self,
        audit: dict[str, Any] | None,
        *,
        audit_ready: bool = True,
    ) -> None:
        super().__init__()
        self.audit = audit
        self.revision_audit: dict[str, Any] | None = audit
        self.audit_ready = audit_ready

    def listening_audit_readiness(self) -> bool:
        return self.audit_ready

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        return self._audited(super().render(candidate, translation), self.audit)

    def render_segment(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
        segment_ordinal: int,
        prior_preview_key: str,
    ) -> SpeechResult:
        result = super().render_segment(
            candidate, translation, segment_ordinal, prior_preview_key
        )
        return self._audited(result, self.revision_audit)

    @staticmethod
    def _audited(result: SpeechResult, audit: dict[str, Any] | None) -> SpeechResult:
        settings = {"listening_audit": audit} if audit is not None else {}
        return replace(
            result,
            pipeline_version=SPEECH_PIPELINE_V19,
            render_settings_json=json.dumps(settings),
            # The measured average bitrate of speech with pauses.
            quality=replace(result.quality, bitrate_kbps=117),
        )


def _client(
    tmp_path: Any,
    speech: FakeSpeechAdapter,
) -> tuple[TestClient, AcceptanceHarness]:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(replace(harness.dependencies, speech=speech)))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    return client, harness


def _quality_step(run: dict[str, Any]) -> dict[str, Any]:
    return next(
        step
        for step in run["pipeline_steps"]
        if step["id"] == "publication_audio_quality"
    )


def test_remaining_hard_findings_wait_in_the_review_queue_for_the_owner(
    tmp_path: Any,
) -> None:
    speech = _AuditedSpeechAdapter(_audit([_finding("high_cer", "hard", "CER 0.62")]))
    client, harness = _client(tmp_path, speech)

    failed = client.post("/api/v1/processing-runs:advance").json()

    assert failed["processing_state"] == "review_queue"
    assert failed["error_kind"] == "listening_audit"
    review = client.get("/api/v1/review-queue").json()["items"]
    assert review[0]["error_kind"] == "listening_audit"
    assert "seg-0001 (high_cer: CER 0.62)" in review[0]["error_summary"]
    assert harness.telegram.latest().kind == "processing_failure"
    run_id = failed["processing_run_id"]
    run = client.get(f"/api/v1/processing-runs/{run_id}").json()
    assert run["current_error"]["kind"] == "listening_audit"
    assert run["current_error"]["retryable"] is True
    audit = _quality_step(run)["metrics"]["listening_audit"]
    assert audit["blocking_unit_ids"] == ["seg-0001"]
    assert audit["owner_acknowledged"] is False
    assert audit["findings"][0]["kind"] == "high_cer"

    retried = client.post(f"/api/v1/processing-runs/{run_id}:retry")
    assert retried.status_code == 200
    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["episode_state"] == "publication_queue"
    assert speech.invocation_count == 1
    episode = client.get(f"/api/v1/episodes/{completed['episode_id']}").json()
    gate = episode["quality_gates"]
    assert gate["eligible"] is True
    assert gate["listening_audit"]["owner_acknowledged"] is True
    assert gate["listening_audit"]["hard_count"] == 1
    assert gate["audio"]["bitrate_kbps"] == 117
    detail = client.get(
        f"/api/v1/episodes/{completed['episode_id']}/processing-detail"
    ).json()
    assert detail["summary"]["listening_audit"]["status"] == "complete"
    findings = detail["segments"][0]["listening_audit_findings"]
    assert [(item["kind"], item["severity"]) for item in findings] == [
        ("high_cer", "hard")
    ]
    activity = client.get(f"/api/v1/processing-runs/{run_id}").json()["activity"]
    assert "listening_audit_acknowledged" in {item["kind"] for item in activity}


def test_soft_findings_are_listed_on_the_publication_queue_item(
    tmp_path: Any,
) -> None:
    speech = _AuditedSpeechAdapter(
        _audit(
            [
                _finding("english_leakage", "soft", "untranslated English: so anyway"),
                _finding("internal_silence", "soft", "1.4s silence"),
            ]
        )
    )
    client, _ = _client(tmp_path, speech)

    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["episode_state"] == "publication_queue"
    gate = client.get(f"/api/v1/episodes/{completed['episode_id']}").json()[
        "quality_gates"
    ]
    assert gate["listening_audit"]["soft_count"] == 2
    assert gate["listening_audit"]["hard_count"] == 0
    assert gate["listening_audit"]["blocking_unit_ids"] == []
    assert [item["kind"] for item in gate["listening_audit"]["findings"]] == [
        "english_leakage",
        "internal_silence",
    ]


def test_an_unavailable_audit_is_explicit_and_does_not_block(
    tmp_path: Any,
) -> None:
    speech = _AuditedSpeechAdapter(None)
    client, _ = _client(tmp_path, speech)

    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["episode_state"] == "publication_queue"
    audit = client.get(f"/api/v1/episodes/{completed['episode_id']}").json()[
        "quality_gates"
    ]["listening_audit"]
    assert audit["status"] == "unavailable"
    assert audit["unavailable_count"] == 1
    assert audit["findings"][0]["kind"] == "audit_unavailable"


def test_historical_pipelines_report_no_listening_audit(tmp_path: Any) -> None:
    client, _ = _client(tmp_path, FakeSpeechAdapter())

    completed = client.post("/api/v1/processing-runs:advance").json()

    episode_id = completed["episode_id"]
    gate = client.get(f"/api/v1/episodes/{episode_id}").json()["quality_gates"]
    assert "listening_audit" not in gate
    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()
    assert "listening_audit" not in detail["summary"]
    assert all("listening_audit_findings" not in item for item in detail["segments"])


def test_segment_revision_with_remaining_hard_findings_is_not_applied(
    tmp_path: Any,
) -> None:
    speech = _AuditedSpeechAdapter(_audit([]))
    client, _ = _client(tmp_path, speech)
    episode_id = client.post("/api/v1/processing-runs:advance").json()["episode_id"]
    prior_preview = client.get(f"/api/v1/episodes/{episode_id}").json()["preview"]
    speech.revision_audit = _audit([_finding("truncation", "hard", "tail missing")])

    queued = client.patch(
        f"/api/v1/episodes/{episode_id}/segments/1",
        json={"target_text": "一个修订过的句子。"},
    )
    assert queued.status_code == 202
    outcome = client.post("/api/v1/processing-runs:advance").json()

    assert outcome["revision_state"] == "review_queue"
    episode = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert episode["preview"]["key"] == prior_preview["key"]
    assert episode["segments"][0]["version"] == 1


def test_setup_readiness_reports_the_listening_audit_as_optional(
    tmp_path: Any,
) -> None:
    client, _ = _client(tmp_path, _AuditedSpeechAdapter(None, audit_ready=False))

    readiness = client.get("/api/v1/setup-readiness").json()

    assert readiness["services"]["listening_audit"] is False
    assert readiness["details"]["listening_audit"]["optional"] is True
    assert "audit_unavailable" in readiness["details"]["listening_audit"]["impact"]
    assert readiness["ready"] is True


# --- Quality Gate shapes ----------------------------------------------------------


def _speech(quality: AudioQualityReport) -> SpeechResult:
    return SpeechResult(
        publication_audio=b"M4A!fixture",
        duration_seconds=10,
        chapters=(),
        pipeline_version=SPEECH_PIPELINE_V19,
        quality=quality,
        segment_timings=((0.0, 4.0), (4.5, 9.0)),
    )


def test_the_gate_reads_historical_checkpoints_with_literal_flags() -> None:
    historical = {
        "duration_seconds": 10,
        "chapters": [],
        "pipeline_version": "transpodcast-modal-pipeline-v17",
        "segment_timings": [[0.0, 4.0], [4.5, 9.0]],
        "quality": {
            "codec": "AAC-LC",
            "sample_rate_hz": 48000,
            "channels": 2,
            "bitrate_kbps": 128,
            "loudness_lufs": -16.0,
            "true_peak_dbtp": -1.2,
            "clipping": False,
            "rendered_segments": 2,
            "timeline_continuous": True,
            "boundary_artifacts": False,
            "source_language_leakage": False,
        },
    }

    speech = _deserialize_speech_checkpoint(
        json.dumps(historical), b"M4A!fixture-audio"
    )

    assert speech.quality.boundary_artifacts is False
    assert speech.quality.source_language_leakage is False
    TransPodcastService._validate_speech(speech, 2)


def test_the_gate_reads_measured_checkpoints_and_never_blocks_on_leakage() -> None:
    measured = AudioQualityReport(
        bitrate_kbps=117,
        true_peak_dbtp=-1.2,
        rendered_segments=2,
        source_language_leakage=True,
    )
    checkpoint = json.loads(_serialize_speech_checkpoint(_speech(measured)))

    assert "boundary_artifacts" not in checkpoint["quality"]
    restored = _deserialize_speech_checkpoint(
        json.dumps(checkpoint), b"M4A!fixture-audio"
    )
    assert restored.quality.boundary_artifacts is None
    assert restored.quality.source_language_leakage is True
    TransPodcastService._validate_speech(restored, 2)


@pytest.mark.parametrize(
    "change",
    [
        {"bitrate_kbps": 64},
        {"bitrate_kbps": 192},
        {"boundary_artifacts": True},
        {"timeline_continuous": False},
        {"codec": "MP3"},
    ],
)
def test_the_gate_still_rejects_measured_failures(change: dict[str, Any]) -> None:
    quality = replace(
        AudioQualityReport(true_peak_dbtp=-1.2, rendered_segments=2),
        **change,
    )

    with pytest.raises(ValueError, match="deterministic checks"):
        TransPodcastService._validate_speech(_speech(quality), 2)
