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
from transpodcast.pipeline_versions import CURRENT_SPEECH_PIPELINE_VERSION
from transpodcast.speaker_tail_boundary_repair import SpeakerTailRepairVerdict
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
            pipeline_version=CURRENT_SPEECH_PIPELINE_VERSION,
            diarization_model_revision="fixture-sortformer-v21-revision",
            raw_diarization_turns=(
                ("speaker-02", 10.0, 11.0),
                ("speaker-01", 11.0, 13.0),
            ),
            aligned_words=tuple(word for segment in segments for word in segment.words),
            boundary_adjudication_json=json.dumps(boundary),
        )


class RaisingTailRepairTranslation(ThreeSegmentTranslationAdapter):
    def plan_speaker_boundaries(self, candidates):
        self.planned_boundary_candidates.extend(candidates)
        raise RuntimeError("fixture tail repair application defect")


def _advance_mixed_segment(
    tmp_path,
    *,
    tail_repair_error: str | None = None,
    internal_failure: bool = False,
):
    harness = AcceptanceHarness.ready(tmp_path)
    translation = (
        RaisingTailRepairTranslation()
        if internal_failure
        else ThreeSegmentTranslationAdapter()
    )
    translation.tail_repair_error = tail_repair_error
    translation.tail_repair_verdicts["tail-repair::seg-0002::seg-0003::10.000"] = (
        SpeakerTailRepairVerdict(
            action="shift_tail",
            shift_before_word_index=2,
            rationale="A new answer begins with Yes.",
        )
    )
    dependencies = replace(
        harness.dependencies,
        analysis=MixedSegmentAnalysisAdapter(),
        translation=translation,
        speech=MatchingSpeechAdapter(),
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
    advanced = client.post("/api/v1/processing-runs:advance").json()
    return dependencies, translation, advanced


def test_current_pipeline_repairs_the_pair_before_translation_without_a_new_stage(
    tmp_path,
) -> None:
    dependencies, translation, advanced = _advance_mixed_segment(tmp_path)

    assert [
        candidate.current_segment_id
        for candidate in translation.planned_boundary_candidates
    ] == ["seg-0002"]
    with dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, advanced["processing_run_id"])
        assert run is not None
        analysis = json.loads(str(run.analysis_json))
        translated = json.loads(str(run.translation_json))
        stages = session.scalars(
            select(ProcessingStageRecord).where(
                ProcessingStageRecord.processing_run_id == run.id
            )
        ).all()
        boundary_activity = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run.id,
                ProcessingRunActivityRecord.kind
                == "speaker_boundary_adjudication_completed",
            )
        ).one()
    assert [item["segment_id"] for item in analysis["segments"]] == [
        "seg-0001",
        "seg-0002",
        "seg-0003",
    ]
    assert analysis["segments"][1]["text"] == "Ah kidding"
    assert analysis["segments"][2]["text"] == "Yes and I did not know"
    assert len(translated["segments"]) == 3
    assert "unified_boundary_adjudication" in (analysis["boundary_adjudication"])
    assert [stage.stage for stage in stages].count("source_analysis") == 1
    source_usage = json.loads(
        next(stage for stage in stages if stage.stage == "source_analysis").usage_json
    )
    source_stage = next(stage for stage in stages if stage.stage == "source_analysis")
    assert "@fixture-sortformer-v21-revision" in source_stage.model
    assert source_usage["diarization_model_revision"] == (
        "fixture-sortformer-v21-revision"
    )
    assert source_usage["discourse_input_tokens"] == 70
    assert source_usage["discourse_output_tokens"] == 25
    assert json.loads(boundary_activity.metadata_json) == {
        "candidate_count": 1,
        "completed": True,
        "conflict_component_count": 0,
        "locally_preserved_component_count": 0,
        "planner_proposed_count": 1,
        "provider_degraded_component_count": 0,
        "reassign_whole_count": 0,
        "reviewer_approved_count": 1,
        "split_to_next_count": 1,
        "error": None,
    }


def test_current_tail_repair_provider_failure_keeps_the_v10_shape(
    tmp_path,
) -> None:
    dependencies, _, advanced = _advance_mixed_segment(
        tmp_path,
        tail_repair_error="fixture provider unavailable",
    )

    assert advanced["processing_state"] == "completed"
    with dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, advanced["processing_run_id"])
        assert run is not None
        analysis = json.loads(str(run.analysis_json))
        degraded = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run.id,
                ProcessingRunActivityRecord.kind
                == "speaker_boundary_adjudication_degraded",
            )
        ).one()
    assert [item["segment_id"] for item in analysis["segments"]] == [
        "seg-0001",
        "seg-0002",
        "seg-0003",
    ]
    assert "preserved its affected boundaries" in degraded.summary


def test_current_tail_repair_failure_keeps_processing_off_review_queue(
    tmp_path,
) -> None:
    dependencies, _, advanced = _advance_mixed_segment(
        tmp_path,
        internal_failure=True,
    )

    assert advanced["processing_state"] == "completed"
    with dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, advanced["processing_run_id"])
        assert run is not None
        analysis = json.loads(str(run.analysis_json))
        degraded = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run.id,
                ProcessingRunActivityRecord.kind
                == "speaker_boundary_adjudication_degraded",
            )
        ).one()
    assert run.error_kind is None
    assert [item["segment_id"] for item in analysis["segments"]] == [
        "seg-0001",
        "seg-0002",
        "seg-0003",
    ]
    assert "fixture tail repair application defect" in str(
        json.loads(degraded.metadata_json)["error"]
    )


def _advance(tmp_path, *, discourse_error: str | None = None):
    harness = AcceptanceHarness.ready(tmp_path)
    analysis = FakeAnalysisAdapter()
    analysis.pending_adjudication = True
    translation = ThreeSegmentTranslationAdapter()
    translation.discourse_owner = "speaker-01"
    translation.discourse_error = discourse_error
    dependencies = replace(
        harness.dependencies,
        analysis=analysis,
        translation=translation,
        speech=MatchingSpeechAdapter(),
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
    advanced = client.post("/api/v1/processing-runs:advance").json()
    return dependencies, translation, advanced


def test_worker_completes_pending_adjudication_through_the_adapter(
    tmp_path,
) -> None:
    dependencies, translation, advanced = _advance(tmp_path)

    assert [
        candidate.ownership_dispute_ids
        for candidate in translation.planned_boundary_candidates
    ] == [("dispute-10.1-speaker-02",)]
    with dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            int(advanced["processing_run_id"]),
        )
        assert run is not None
        analysis = json.loads(str(run.analysis_json))
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
    assert adjudication["changed_count"] == 1
    assert adjudication["marked_count"] == 1
    record = adjudication["decision_records"][0]
    assert record["verdict"] == "whole-reassignment-applied"
    assert adjudication["decision_ledger_reference"]
    assert adjudication["discourse_usage"] == {
        "input_tokens": 70,
        "output_tokens": 25,
    }
    assert adjudication["discourse_execution"] == {
        "provider": "fake-codex",
        "model": "deterministic-boundary-planner",
        "cli_version": "fixture-cli 1.0",
    }
    repaired = next(
        segment for segment in analysis["segments"] if "and" in segment["text"].split()
    )
    assert repaired["speaker_label"] == "speaker-01"
    assert repaired["review_state"] == "disputed"
    attribution = [
        item
        for item in analysis["speaker_consolidation"]["decision_records"]
        if item["kind"] == "segment-attribution"
    ]
    assert len(attribution) == len(analysis["segments"])
    assert attribution[0]["subject"]["speaker"] == "speaker-01"


def test_discourse_usage_is_accounted_on_the_stage(tmp_path) -> None:
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
    assert usage["discourse_model"] == "deterministic-boundary-planner"
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

    def plan_speaker_boundaries(self, candidates):
        self.adjudication_attempts += 1
        if self.adjudication_attempts == 1:
            raise RuntimeError("fixture local completion defect")
        return super().plan_speaker_boundaries(candidates)


def test_malformed_boundary_checkpoint_degrades_without_repeating_analysis(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    analysis = CorruptPendingAnalysisAdapter()
    analysis.pending_adjudication = True
    dependencies = replace(
        harness.dependencies,
        analysis=analysis,
        translation=ThreeSegmentTranslationAdapter(),
        speech=MatchingSpeechAdapter(),
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
    advanced = client.post("/api/v1/processing-runs:advance").json()

    assert advanced["processing_state"] == "completed"
    assert analysis.invocation_count == 1
    with dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            int(advanced["processing_run_id"]),
        )
        assert run is not None
        assert run.analysis_json is not None
        assert run.error_kind is None
        completed_analysis = json.loads(run.analysis_json)
        degraded = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run.id,
                ProcessingRunActivityRecord.kind
                == "speaker_boundary_adjudication_degraded",
            )
        ).one()
    assert [item["segment_id"] for item in completed_analysis["segments"]] == [
        "seg-0001",
        "seg-0002",
        "seg-0003",
        "seg-0004",
    ]
    assert "words" in str(json.loads(degraded.metadata_json)["error"])


def test_boundary_failure_preserves_voice_references_without_repeating_analysis(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    analysis = FakeAnalysisAdapter()
    analysis.pending_adjudication = True
    translation = OneShotCompletionFailureAdapter()
    dependencies = replace(
        harness.dependencies,
        analysis=analysis,
        translation=translation,
        speech=MatchingSpeechAdapter(),
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
    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["processing_state"] == "completed"
    assert analysis.invocation_count == 1
    with dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            int(completed["processing_run_id"]),
        )
        assert run is not None
        final = json.loads(str(run.analysis_json))
        degraded = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run.id,
                ProcessingRunActivityRecord.kind
                == "speaker_boundary_adjudication_degraded",
            )
        ).one()
    assert translation.adjudication_attempts == 1
    assert json.loads(degraded.metadata_json)["error"]
    references = [item["reference_artifact_key"] for item in final["speakers"]]
    assert len(references) == 2
    assert all(Path(value).read_bytes().startswith(b"RIFF") for value in references)


def test_discourse_failure_degrades_and_is_logged(tmp_path) -> None:
    dependencies, _, advanced = _advance(
        tmp_path,
        discourse_error="RuntimeError: provider unavailable",
    )

    with dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            int(advanced["processing_run_id"]),
        )
        assert run is not None
        analysis = json.loads(str(run.analysis_json))
        activity = session.scalars(
            select(ProcessingRunActivityRecord).where(
                ProcessingRunActivityRecord.processing_run_id == run.id,
                ProcessingRunActivityRecord.kind
                == "speaker_boundary_adjudication_degraded",
            )
        ).all()
    assert advanced["episode_id"]
    adjudication = analysis["boundary_adjudication"]
    assert adjudication["changed_count"] == 0
    assert adjudication["marked_count"] == 0
    assert adjudication["decision_records"][0]["verdict"] == "provider-degraded"
    assert (
        "provider unavailable"
        in (adjudication["unified_boundary_adjudication"]["error"])
    )
    stolen = next(
        segment for segment in analysis["segments"] if segment["text"] == "and"
    )
    assert stolen["speaker_label"] == "speaker-02"
    assert stolen["review_state"] == "accepted"
    assert len(activity) == 1
    metadata = json.loads(activity[0].metadata_json)
    assert metadata["candidate_count"] == 1
