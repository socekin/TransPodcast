from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from transpodcast.api import create_app
from transpodcast.domain import (
    AudioQualityReport,
    CanonicalSegment,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.models import (
    ProcessingRunActivityRecord,
    ProcessingRunRecord,
    ProcessingStageRecord,
)
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V15
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    FakeSpeechAdapter,
    FakeTranslationAdapter,
)


class ThreeSegmentTranslationAdapter(FakeTranslationAdapter):
    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        base = super().translate(candidate)
        segment_count = len(
            json.loads(candidate.analysis_json or "{}").get("segments", [])
        )
        return replace(
            base,
            segments=tuple(
                f"第 {ordinal} 段的译文。" for ordinal in range(1, segment_count + 1)
            ),
        )


class MatchingSpeechAdapter(FakeSpeechAdapter):
    @staticmethod
    def _result(translation: TranslationResult) -> SpeechResult:
        base = FakeSpeechAdapter._result(translation)
        return replace(
            base,
            quality=AudioQualityReport(rendered_segments=len(translation.segments)),
        )


class MixedSegmentAnalysisAdapter(FakeAnalysisAdapter):
    def analyze(self, candidate, archive):
        base = super().analyze(candidate, archive)
        segments = (
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker-01",
                start_seconds=0.0,
                end_seconds=10.0,
                text="That sounds surprising",
                words=(("That", 9.0, 9.4), ("surprising", 9.4, 10.0)),
            ),
            CanonicalSegment(
                segment_id="seg-0002",
                speaker_label="speaker-02",
                start_seconds=10.0,
                end_seconds=12.0,
                text="Ah kidding Yes and",
                words=(
                    ("Ah", 10.0, 10.5),
                    ("kidding", 10.5, 11.0),
                    ("Yes", 11.0, 11.5),
                    ("and", 11.5, 12.0),
                ),
                overlap=True,
            ),
            CanonicalSegment(
                segment_id="seg-0003",
                speaker_label="speaker-01",
                start_seconds=12.0,
                end_seconds=13.0,
                text="I did not know",
                words=(
                    ("I", 12.0, 12.25),
                    ("did", 12.25, 12.6),
                    ("not", 12.6, 12.8),
                    ("know", 12.8, 13.0),
                ),
            ),
        )
        boundary = json.loads(base.boundary_adjudication_json)
        boundary["pending"] = {"disputes": [], "voiceprints": {}}
        return replace(
            base,
            segments=segments,
            pipeline_version=SPEECH_PIPELINE_V15,
            diarization_model_revision="fixture-sortformer-v21-revision",
            raw_diarization_turns=(
                ("speaker-02", 10.0, 11.0),
                ("speaker-01", 11.0, 13.0),
            ),
            aligned_words=tuple(word for segment in segments for word in segment.words),
            boundary_adjudication_json=json.dumps(boundary),
        )


def _client(dependencies) -> TestClient:
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    return client


def test_v15_records_ownership_and_segmentation_activity(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        analysis=MixedSegmentAnalysisAdapter(),
        translation=ThreeSegmentTranslationAdapter(),
        speech=MatchingSpeechAdapter(),
    )
    client = _client(dependencies)
    advanced = client.post("/api/v1/processing-runs:advance").json()

    with dependencies.database.session() as session:
        activity = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id
                == advanced["processing_run_id"],
                ProcessingRunActivityRecord.kind
                == "speaker_ownership_adjudication_completed",
            )
        ).one()
        stages = session.scalars(
            select(ProcessingStageRecord).where(
                ProcessingStageRecord.processing_run_id
                == advanced["processing_run_id"]
            )
        ).all()

    metadata = json.loads(activity.metadata_json)
    assert metadata["region_count"] >= 1
    assert metadata["natural_segment_count"] >= 1
    assert metadata["fallback_boundary_count"] == 0
    detail = client.get(
        f"/api/v1/episodes/{advanced['episode_id']}/processing-detail"
    ).json()
    ownership = detail["summary"]["speaker_ownership_v15"]
    assert ownership["region_count"] == metadata["region_count"]
    assert ownership["natural_segmentation"]["segment_count"] == (
        metadata["natural_segment_count"]
    )
    assert [stage.stage for stage in stages].count("source_analysis") == 1
    source_stage = next(stage for stage in stages if stage.stage == "source_analysis")
    source_usage = json.loads(source_stage.usage_json)
    assert "@fixture-sortformer-v21-revision" in source_stage.model
    assert source_usage["diarization_model_revision"] == (
        "fixture-sortformer-v21-revision"
    )


def _advance(
    tmp_path,
    *,
    analysis: FakeAnalysisAdapter | None = None,
    translation: FakeTranslationAdapter | None = None,
    discourse_error: str | None = None,
):
    harness = AcceptanceHarness.ready(tmp_path)
    analysis = analysis or FakeAnalysisAdapter()
    analysis.pending_adjudication = True
    translation = translation or ThreeSegmentTranslationAdapter()
    translation.discourse_error = discourse_error
    dependencies = replace(
        harness.dependencies,
        analysis=analysis,
        translation=translation,
        speech=MatchingSpeechAdapter(),
    )
    client = _client(dependencies)
    advanced = client.post("/api/v1/processing-runs:advance").json()
    return dependencies, translation, advanced


def _stored_analysis(dependencies, advanced) -> dict:
    with dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            int(advanced["processing_run_id"]),
        )
        assert run is not None
        assert run.error_kind is None
        return dict(json.loads(str(run.analysis_json)))


def _activity(dependencies, advanced, kind: str) -> list[dict]:
    with dependencies.database.session() as session:
        return [
            json.loads(item.metadata_json)
            for item in session.scalars(
                select(ProcessingRunActivityRecord).where(
                    ProcessingRunActivityRecord.processing_run_id
                    == int(advanced["processing_run_id"]),
                    ProcessingRunActivityRecord.kind == kind,
                )
            ).all()
        ]


def test_worker_completes_pending_adjudication_through_the_adapter(
    tmp_path,
) -> None:
    dependencies, translation, advanced = _advance(tmp_path)

    assert translation.planned_speaker_ownership_regions
    assert translation.reviewed_speaker_ownership_regions
    analysis = _stored_analysis(dependencies, advanced)
    expected_checksum = hashlib.sha256(
        json.dumps(
            {key: value for key, value in analysis.items() if key != "checksum"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert analysis["checksum"] == expected_checksum
    adjudication = analysis["boundary_adjudication"]
    assert "pending" not in adjudication
    assert "speaker_ownership_v15_checkpoint" not in adjudication
    assert adjudication["speaker_ownership_v15"]["region_count"] >= 1
    assert adjudication["decision_ledger_reference"]
    attribution = [
        item
        for item in analysis["speaker_consolidation"]["decision_records"]
        if item["kind"] == "segment-attribution"
    ]
    assert len(attribution) == len(analysis["segments"])


def test_ownership_usage_is_accounted_on_the_stage(tmp_path) -> None:
    dependencies, _, advanced = _advance(tmp_path)

    with dependencies.database.session() as session:
        stage = session.scalars(
            select(ProcessingStageRecord).where(
                ProcessingStageRecord.processing_run_id
                == int(advanced["processing_run_id"]),
                ProcessingStageRecord.stage == "source_analysis",
            )
        ).one()
        usage = json.loads(stage.usage_json)
    assert usage["discourse_input_tokens"] == 70
    assert usage["discourse_output_tokens"] == 25
    assert usage["discourse_provider"] == "fake-codex"
    assert usage["discourse_model"] == "deterministic-ownership-planner"
    assert usage["discourse_cli_version"] == "fixture-cli 1.0"


class CorruptPendingAnalysisAdapter(FakeAnalysisAdapter):
    """Emits a pending payload the completion cannot parse."""

    def analyze(self, candidate, archive):
        result = super().analyze(candidate, archive)
        payload = json.loads(result.boundary_adjudication_json)
        del payload["pending"]["disputes"][0]["words"]
        return replace(
            result,
            boundary_adjudication_json=json.dumps(payload),
        )


class OneShotCompletionFailureAdapter(ThreeSegmentTranslationAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.adjudication_attempts = 0

    def plan_speaker_ownership(self, regions):
        self.adjudication_attempts += 1
        if self.adjudication_attempts == 1:
            raise RuntimeError("fixture local completion defect")
        return super().plan_speaker_ownership(regions)


def test_malformed_ownership_checkpoint_degrades_without_repeating_analysis(
    tmp_path,
) -> None:
    analysis = CorruptPendingAnalysisAdapter()
    dependencies, _, advanced = _advance(tmp_path, analysis=analysis)

    assert advanced["processing_state"] == "completed"
    assert analysis.invocation_count == 1
    _stored_analysis(dependencies, advanced)
    degraded = _activity(
        dependencies,
        advanced,
        "speaker_ownership_adjudication_degraded",
    )
    assert len(degraded) == 1
    assert "words" in str(degraded[0]["error"])


def test_ownership_failure_preserves_voice_references_without_repeating_analysis(
    tmp_path,
) -> None:
    analysis = FakeAnalysisAdapter()
    translation = OneShotCompletionFailureAdapter()
    dependencies, _, completed = _advance(
        tmp_path,
        analysis=analysis,
        translation=translation,
    )

    assert completed["processing_state"] == "completed"
    assert analysis.invocation_count == 1
    assert translation.adjudication_attempts == 1
    final = _stored_analysis(dependencies, completed)
    degraded = _activity(
        dependencies,
        completed,
        "speaker_ownership_adjudication_degraded",
    )
    assert len(degraded) == 1
    assert "fixture local completion defect" in str(degraded[0]["error"])
    references = [item["reference_artifact_key"] for item in final["speakers"]]
    assert len(references) == 2
    assert all(Path(value).read_bytes().startswith(b"RIFF") for value in references)


def test_ownership_provider_failure_degrades_and_is_logged(tmp_path) -> None:
    dependencies, _, advanced = _advance(
        tmp_path,
        discourse_error="RuntimeError: provider unavailable",
    )

    assert advanced["episode_id"]
    analysis = _stored_analysis(dependencies, advanced)
    ownership = analysis["boundary_adjudication"]["speaker_ownership_v15"]
    assert ownership["changed_word_count"] == 0
    assert "provider unavailable" in " ".join(ownership["degraded_errors"])
    stolen = next(
        segment
        for segment in analysis["segments"]
        if "and" in segment["text"].split()
    )
    assert stolen["speaker_label"] == "speaker-02"
    degraded = _activity(
        dependencies,
        advanced,
        "speaker_ownership_adjudication_degraded",
    )
    assert len(degraded) == 1
    assert "provider unavailable" in str(degraded[0]["error"])
