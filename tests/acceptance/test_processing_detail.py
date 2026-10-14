from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import select

from transpodcast.api import create_app
from transpodcast.domain import (
    AnalyzedSpeaker,
    AudioQualityReport,
    CanonicalSegment,
    SourceAnalysisResult,
    SourceArchive,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.models import (
    ProcessingRunRecord,
    SegmentRevisionJobRecord,
    StructuredArtifactRecord,
)
from transpodcast.service import _v15_counterpart_label
from transpodcast.testing import (
    AcceptanceHarness,
    FakeSpeechAdapter,
    FakeTranslationAdapter,
)


class DisputedAnalysisAdapter:
    """A three-segment analysis with one disputed ownership island."""

    def __init__(self) -> None:
        self.invocation_count = 0

    def readiness(self) -> bool:
        return True

    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        self.invocation_count += 1
        assert archive.checksum
        duration = float(candidate.duration_seconds)
        segments = (
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker-01",
                start_seconds=0.0,
                end_seconds=10.0,
                text="They did not feel compute bound",
            ),
            CanonicalSegment(
                segment_id="seg-0002",
                speaker_label="speaker-02",
                start_seconds=10.0,
                end_seconds=10.5,
                text="and",
                review_state="disputed",
            ),
            CanonicalSegment(
                segment_id="seg-0003",
                speaker_label="speaker-01",
                start_seconds=10.6,
                end_seconds=duration,
                text="the entire industry feels that now",
            ),
        )
        adjudication_record = {
            "kind": "ownership-adjudication",
            "policy_version": "boundary-ownership-adjudication-v1",
            "subject": {
                "dispute_id": "dispute-10.0-speaker-02",
                "words": ["and"],
                "start_seconds": 10.0,
                "end_seconds": 10.5,
            },
            "evidence": {
                "current_owner": "speaker-02",
                "counterpart": "speaker-01",
                "voiceprint_scores": {
                    "speaker-01": 0.72,
                    "speaker-02": 0.18,
                },
                "discourse_owner": None,
                "discourse_rationale": None,
            },
            "verdict": "kept-discourse-abstained",
            "rationale": (
                "The discourse judge abstained, so only one vote "
                "remains and the original assignment stands."
            ),
        }
        consolidation_record = {
            "kind": "consolidation",
            "policy_version": "speaker-consolidation-v2",
            "subject": {"source_label": "speaker-02"},
            "evidence": {"window_count": 3},
            "verdict": "kept",
            "rationale": "Median similarity below the merge threshold.",
        }
        return SourceAnalysisResult(
            segments=segments,
            pipeline_version="fixture-analysis-v1",
            diarization_model="fixture-sortformer",
            diarization_model_revision="fixture-sortformer-revision",
            transcription_model="fixture-qwen3-asr",
            alignment_model="fixture-qwen3-aligner",
            speakers=(
                AnalyzedSpeaker(
                    local_label="speaker-01",
                    reference_audio=b"RIFFspeaker-01",
                    embedding=(1.0, 0.0, 0.0),
                    confidence=0.98,
                ),
                AnalyzedSpeaker(
                    local_label="speaker-02",
                    reference_audio=b"RIFFspeaker-02",
                    embedding=(0.0, 1.0, 0.0),
                    confidence=0.98,
                ),
            ),
            speaker_consolidation_json=json.dumps(
                {
                    "policy_version": "speaker-consolidation-v2",
                    "speaker_mapping": {
                        "speaker-01": "speaker-01",
                        "speaker-02": "speaker-02",
                    },
                    "reference_selection": {},
                    "decision_records": [consolidation_record],
                },
                sort_keys=True,
            ),
            boundary_adjudication_json=json.dumps(
                {
                    "policy_version": ("boundary-ownership-adjudication-v1"),
                    "dispute_count": 1,
                    "changed_count": 0,
                    "marked_count": 1,
                    "decision_records": [adjudication_record],
                },
                sort_keys=True,
            ),
            checksum=hashlib.sha256(b"fixture").hexdigest(),
            estimated_cost_usd=0.05,
        )


class ThreeSegmentTranslationAdapter(FakeTranslationAdapter):
    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        base = super().translate(candidate)
        return replace(
            base,
            segments=(
                "他们当时不觉得算力是瓶颈。",
                "而且",
                "现在整个行业都有这种感觉。",
            ),
        )


class V15OwnershipDetailAdapter(DisputedAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        base = super().analyze(candidate, archive)
        record = {
            "kind": "speaker-ownership-adjudication",
            "policy_version": "speaker-ownership-adjudication-v1",
            "subject": {
                "region_id": "speaker-ownership::0001::1-1",
                "first_word_index": 1,
                "last_word_index": 1,
                "start_seconds": 10.0,
                "end_seconds": 10.5,
                "permissible_speakers": [
                    "speaker-01",
                    "speaker-02",
                    "speaker-03",
                ],
            },
            "evidence": {
                "immutable_words": [
                    {
                        "word_index": 1,
                        "text": "and",
                        "start_seconds": 10.0,
                        "end_seconds": 10.5,
                        "current_owner": "speaker-01",
                    }
                ],
                "voiceprint_evidence": [],
                "planner_plan": {
                    "action": "assign_spans",
                    "assignments": [
                        {
                            "first_word_index": 1,
                            "last_word_index": 1,
                            "speaker_label": "speaker-02",
                        }
                    ],
                    "missing_speech_suspected": False,
                    "rationale": "The conjunction begins the response.",
                },
                "reviewer_plan": {
                    "action": "keep",
                    "assignments": [],
                    "missing_speech_suspected": False,
                    "rationale": "The acoustic boundary is ambiguous.",
                },
                "applied_plan": {
                    "action": "assign_spans",
                    "assignments": [
                        {
                            "first_word_index": 1,
                            "last_word_index": 1,
                            "speaker_label": "speaker-02",
                        }
                    ],
                    "missing_speech_suspected": False,
                    "rationale": "The conjunction begins the response.",
                },
                "applied_plan_source": "planner",
                "acoustic_conflict": True,
            },
            "verdict": "applied-disputed",
            "rationale": "The conjunction begins the response.",
        }
        consolidation = json.loads(base.speaker_consolidation_json)
        consolidation["speaker_mapping"]["speaker-03"] = "speaker-03"
        return replace(
            base,
            pipeline_version="transpodcast-modal-pipeline-v15",
            speakers=(
                *base.speakers,
                AnalyzedSpeaker(
                    local_label="speaker-03",
                    reference_audio=b"RIFFspeaker-03",
                    embedding=(0.0, 0.0, 1.0),
                    confidence=0.98,
                ),
            ),
            speaker_consolidation_json=json.dumps(consolidation),
            boundary_adjudication_json=json.dumps(
                {
                    "policy_version": "speaker-ownership-adjudication-v1",
                    "changed_count": 1,
                    "marked_count": 1,
                    "decision_records": [record],
                    "speaker_ownership_v15": {
                        "policy_version": "speaker-ownership-adjudication-v1",
                        "region_count": 1,
                        "changed_word_count": 1,
                        "disputed_region_count": 1,
                        "possible_missing_speech_count": 0,
                        "batch_count": 1,
                        "natural_segmentation": {},
                        "decision_records": [record],
                    },
                }
            ),
        )


class V15PartialPrefixDetailAdapter(V15OwnershipDetailAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        base = super().analyze(candidate, archive)
        adjudication = json.loads(base.boundary_adjudication_json)
        record = adjudication["decision_records"][0]
        record["subject"].update(
            {
                "first_word_index": 1,
                "last_word_index": 2,
                "start_seconds": 10.0,
                "end_seconds": 11.0,
            }
        )
        record["evidence"]["immutable_words"] = [
            {
                "word_index": 1,
                "text": "and",
                "start_seconds": 10.0,
                "end_seconds": 10.5,
                "current_owner": "speaker-01",
            },
            {
                "word_index": 2,
                "text": "continues",
                "start_seconds": 10.5,
                "end_seconds": 11.0,
                "current_owner": "speaker-02",
            },
        ]
        assignments = [
            {
                "first_word_index": 1,
                "last_word_index": 2,
                "speaker_label": "speaker-02",
            }
        ]
        record["evidence"]["planner_plan"]["assignments"] = assignments
        record["evidence"]["applied_plan"]["assignments"] = assignments
        adjudication["speaker_ownership_v15"]["decision_records"] = [record]
        return replace(
            base,
            segments=(
                base.segments[0],
                replace(
                    base.segments[1],
                    end_seconds=11.0,
                    text="and continues",
                    words=(
                        ("and", 10.0, 10.5),
                        ("continues", 10.5, 11.0),
                    ),
                ),
                replace(base.segments[2], start_seconds=11.0),
            ),
            boundary_adjudication_json=json.dumps(adjudication),
        )


class ThreeSegmentSpeechAdapter(FakeSpeechAdapter):
    @staticmethod
    def _result(translation: TranslationResult) -> SpeechResult:
        base = FakeSpeechAdapter._result(translation)
        return replace(
            base,
            quality=AudioQualityReport(rendered_segments=len(translation.segments)),
        )


class TailRepairAnalysisAdapter(DisputedAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        base = super().analyze(candidate, archive)
        segments = (
            CanonicalSegment(
                segment_id="seg-0034",
                speaker_label="speaker-01",
                start_seconds=345.0,
                end_seconds=346.7,
                text="That was surprising",
            ),
            CanonicalSegment(
                segment_id="seg-0035",
                speaker_label="speaker-02",
                start_seconds=346.864,
                end_seconds=347.904,
                text="Ah kidding",
                review_state="auto_corrected",
            ),
            CanonicalSegment(
                segment_id="seg-0036",
                speaker_label="speaker-01",
                start_seconds=347.904,
                end_seconds=float(candidate.duration_seconds),
                text="Yes and I didn't know what it was",
                review_state="auto_corrected",
            ),
        )
        repair_record = {
            "kind": "speaker-tail-boundary-repair",
            "policy_version": "speaker-tail-boundary-repair-v1",
            "subject": {
                "candidate_id": ("tail-repair::seg-0035::seg-0036::346.864"),
                "current_segment_id": "seg-0035",
                "next_segment_id": "seg-0036",
                "start_seconds": 346.864,
                "end_seconds": float(candidate.duration_seconds),
                "current_words": [
                    "Ah",
                    "kidding",
                    "Yes",
                    "and",
                    "I",
                    "didn't",
                ],
                "next_words": ["know", "what", "it", "was"],
            },
            "evidence": {
                "current_owner": "speaker-02",
                "next_owner": "speaker-01",
                "word_speaker_support": [],
                "shift_before_word_index": 2,
                "shifted_words": ["Yes", "and", "I", "didn't"],
            },
            "verdict": "tail-shifted",
            "rationale": "A new answer begins with Yes.",
        }
        return replace(
            base,
            segments=segments,
            pipeline_version="transpodcast-modal-pipeline-v11",
            boundary_adjudication_json=json.dumps(
                {
                    "policy_version": "boundary-ownership-adjudication-v3",
                    "changed_count": 0,
                    "marked_count": 0,
                    "decision_records": [repair_record],
                    "speaker_tail_boundary_repair": {
                        "policy_version": "speaker-tail-boundary-repair-v1",
                        "candidate_count": 1,
                        "repair_count": 1,
                    },
                },
                sort_keys=True,
            ),
        )


class TailKeepWithV10CorrectionAdapter(DisputedAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        base = super().analyze(candidate, archive)
        ownership_record = {
            "kind": "ownership-adjudication",
            "policy_version": "boundary-ownership-adjudication-v3",
            "subject": {
                "dispute_id": "dispute-10.0-speaker-02",
                "start_seconds": 10.0,
                "end_seconds": 10.5,
                "words": ["and"],
            },
            "evidence": {
                "current_owner": "speaker-02",
                "counterpart": "speaker-01",
                "adopted_owner": "speaker-01",
            },
            "verdict": "reassigned-by-agreement",
            "rationale": "Both judges reassigned the fragment.",
        }
        tail_keep_record = {
            "kind": "speaker-tail-boundary-repair",
            "policy_version": "speaker-tail-boundary-repair-v1",
            "subject": {
                "candidate_id": "tail-repair::seg-0002::seg-0003::10.000",
                "current_segment_id": "seg-0002",
                "next_segment_id": "seg-0003",
                "start_seconds": 10.0,
                "end_seconds": 10.5,
                "words": ["and"],
            },
            "evidence": {
                "current_owner": "speaker-02",
                "next_owner": "speaker-01",
                "word_speaker_support": [],
            },
            "verdict": "keep",
            "rationale": "No internal handoff was found.",
        }
        segments = (
            base.segments[0],
            replace(
                base.segments[1],
                speaker_label="speaker-01",
                review_state="auto_corrected",
            ),
            base.segments[2],
        )
        return replace(
            base,
            segments=segments,
            pipeline_version="transpodcast-modal-pipeline-v11",
            boundary_adjudication_json=json.dumps(
                {
                    "policy_version": "boundary-ownership-adjudication-v3",
                    "changed_count": 1,
                    "marked_count": 0,
                    "decision_records": [
                        ownership_record,
                        tail_keep_record,
                    ],
                    "speaker_tail_boundary_repair": {
                        "policy_version": "speaker-tail-boundary-repair-v1",
                        "candidate_count": 1,
                        "repair_count": 0,
                    },
                },
                sort_keys=True,
            ),
        )


class V12TailRepairSummaryAdapter(TailRepairAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        base = super().analyze(candidate, archive)
        boundary = json.loads(base.boundary_adjudication_json)
        boundary["speaker_tail_boundary_repair"].update(
            {
                "policy_version": "speaker-tail-boundary-repair-v2",
                "candidate_count": 18,
                "repair_count": 7,
            }
        )
        return replace(
            base,
            pipeline_version="transpodcast-modal-pipeline-v12",
            boundary_adjudication_json=json.dumps(boundary, sort_keys=True),
        )


class V14UnifiedBoundaryAdapter(TailRepairAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        base = super().analyze(candidate, archive)
        record = {
            "kind": "speaker-boundary-adjudication",
            "policy_version": "speaker-boundary-adjudication-v1",
            "subject": {
                "candidate_id": "unified-boundary::seg-0035::seg-0036",
                "current_segment_id": "seg-0035",
                "next_segment_id": "seg-0036",
                "current_owner": "speaker-02",
                "next_owner": "speaker-01",
                "start_seconds": 346.864,
                "end_seconds": float(candidate.duration_seconds),
            },
            "evidence": {
                "planner_action": "split_to_next",
                "planner_target_owner": "speaker-01",
                "reviewer_verdict": "approve",
            },
            "verdict": "split-to-next-applied",
            "rationale": "The suffix starts the following complete answer.",
        }
        return replace(
            base,
            pipeline_version="transpodcast-modal-pipeline-v14",
            boundary_adjudication_json=json.dumps(
                {
                    "policy_version": "speaker-boundary-adjudication-v1",
                    "changed_count": 1,
                    "marked_count": 1,
                    "decision_records": [record],
                    "unified_boundary_adjudication": {
                        "policy_version": "speaker-boundary-adjudication-v1",
                        "candidate_count": 1,
                        "split_to_next_count": 1,
                    },
                },
                sort_keys=True,
            ),
        )


class V14WholeReassignmentAdapter(V14UnifiedBoundaryAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        base = super().analyze(candidate, archive)
        boundary = json.loads(base.boundary_adjudication_json)
        record = boundary["decision_records"][0]
        record["evidence"].update(
            {
                "planner_action": "reassign_whole",
                "planner_target_segment_id": "seg-0035",
            }
        )
        record["verdict"] = "whole-reassignment-applied"
        return replace(
            base,
            segments=(
                base.segments[0],
                base.segments[1],
                replace(base.segments[2], review_state="accepted"),
            ),
            boundary_adjudication_json=json.dumps(boundary, sort_keys=True),
        )


def _completed_episode(
    tmp_path,
    *,
    analysis=None,
) -> tuple[TestClient, AcceptanceHarness, int]:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = replace(
        harness.dependencies,
        analysis=analysis or DisputedAnalysisAdapter(),
        translation=ThreeSegmentTranslationAdapter(),
        speech=ThreeSegmentSpeechAdapter(),
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
    return client, harness, int(advanced["episode_id"])


def test_processing_detail_presents_both_sides_of_a_recorded_tail_repair(
    tmp_path,
) -> None:
    client, _, episode_id = _completed_episode(
        tmp_path,
        analysis=TailRepairAnalysisAdapter(),
    )

    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()

    before, current, next_turn = detail["segments"]
    assert detail["summary"]["diarization_model"] == "fixture-sortformer"
    assert detail["summary"]["diarization_model_revision"] == (
        "fixture-sortformer-revision"
    )
    assert detail["summary"]["auto_corrected_count"] == 1
    assert detail["summary"]["speaker_tail_boundary_repair"] == {
        "policy_version": "speaker-tail-boundary-repair-v1",
        "candidate_count": 1,
        "repair_count": 1,
        "degraded": False,
    }
    assert before["adjudication"] is None
    assert current["status"] == "auto_corrected"
    assert next_turn["status"] == "auto_corrected"
    assert current["adjudication"]["verdict"] == "tail-shifted"
    assert next_turn["adjudication"]["verdict"] == "tail-shifted"
    assert next_turn["adjudication"]["kind"] == ("speaker-tail-boundary-repair")
    assert next_turn["adjudication"]["evidence"]["next_owner"] == ("speaker-01")


def test_processing_detail_exposes_uncapped_v12_tail_repair_counts(
    tmp_path,
) -> None:
    client, _, episode_id = _completed_episode(
        tmp_path,
        analysis=V12TailRepairSummaryAdapter(),
    )

    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()

    assert detail["summary"]["pipeline_version"] == ("transpodcast-modal-pipeline-v12")
    assert detail["summary"]["speaker_tail_boundary_repair"] == {
        "policy_version": "speaker-tail-boundary-repair-v2",
        "candidate_count": 18,
        "repair_count": 7,
        "degraded": False,
    }


def test_processing_detail_exposes_v14_boundary_decisions(
    tmp_path,
) -> None:
    client, _, episode_id = _completed_episode(
        tmp_path,
        analysis=V14UnifiedBoundaryAdapter(),
    )

    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()

    current, next_turn = detail["segments"][1:]
    assert current["status"] == "auto_corrected"
    assert next_turn["status"] == "auto_corrected"
    assert current["adjudication"]["verdict"] == "split-to-next-applied"
    assert next_turn["adjudication"]["verdict"] == "split-to-next-applied"
    assert next_turn["counterpart_speaker"]["label"] == "speaker-01"


def test_processing_detail_exposes_v15_plans_and_exact_original_owner(
    tmp_path,
) -> None:
    client, _, episode_id = _completed_episode(
        tmp_path,
        analysis=V15OwnershipDetailAdapter(),
    )

    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()

    disputed = detail["segments"][1]
    assert disputed["status"] == "disputed"
    assert disputed["adjudication"]["kind"] == (
        "speaker-ownership-adjudication"
    )
    assert disputed["adjudication"]["evidence"]["planner_plan"][
        "rationale"
    ] == "The conjunction begins the response."
    assert disputed["adjudication"]["evidence"]["reviewer_plan"][
        "rationale"
    ] == "The acoustic boundary is ambiguous."
    assert disputed["counterpart_speaker"]["label"] == "speaker-01"
    assert detail["summary"]["speaker_ownership_v15"][
        "disputed_region_count"
    ] == 1


def test_v15_partial_prefix_change_has_no_unsafe_segment_overturn() -> None:
    evidence = {
        "immutable_words": [
            {
                "word_index": 10,
                "start_seconds": 4.0,
                "end_seconds": 4.2,
                "current_owner": "speaker-01",
            },
            {
                "word_index": 11,
                "start_seconds": 4.2,
                "end_seconds": 4.8,
                "current_owner": "speaker-02",
            },
        ],
        "applied_plan": {
            "assignments": [
                {
                    "first_word_index": 10,
                    "last_word_index": 11,
                    "speaker_label": "speaker-02",
                }
            ]
        },
    }

    assert (
        _v15_counterpart_label(
            evidence,
            segment_start_seconds=4.0,
            segment_end_seconds=4.8,
            current_speaker_label="speaker-02",
        )
        == ""
    )


def test_v15_partial_prefix_overturn_is_rejected_at_the_api_boundary(
    tmp_path,
) -> None:
    client, harness, episode_id = _completed_episode(
        tmp_path,
        analysis=V15PartialPrefixDetailAdapter(),
    )
    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()
    speaker_id = detail["speakers"][0]["id"]

    response = client.post(
        f"/api/v1/episodes/{episode_id}/segments/2:rule",
        json={"action": "overturn", "speaker_id": speaker_id},
    )

    assert response.status_code == 409
    assert "cannot be safely overturned" in response.json()["detail"]
    with harness.dependencies.database.session() as session:
        assert session.scalars(select(SegmentRevisionJobRecord)).all() == []


def test_v15_exact_segment_overturn_remains_available(tmp_path) -> None:
    client, _, episode_id = _completed_episode(
        tmp_path,
        analysis=V15OwnershipDetailAdapter(),
    )
    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()
    speaker_id = detail["segments"][1]["counterpart_speaker"]["id"]

    response = client.post(
        f"/api/v1/episodes/{episode_id}/segments/2:rule",
        json={"action": "overturn", "speaker_id": speaker_id},
    )

    assert response.status_code == 200
    assert response.json()["revision_job_id"] is not None


def test_processing_detail_marks_only_the_v14_whole_reassignment_target(
    tmp_path,
) -> None:
    client, _, episode_id = _completed_episode(
        tmp_path,
        analysis=V14WholeReassignmentAdapter(),
    )

    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()

    current, next_turn = detail["segments"][1:]
    assert current["status"] == "auto_corrected"
    assert current["adjudication"]["verdict"] == "whole-reassignment-applied"
    assert next_turn["status"] == "accepted"
    assert next_turn["adjudication"] is None


def test_processing_detail_prefers_applied_v10_correction_over_tail_keep(
    tmp_path,
) -> None:
    client, _, episode_id = _completed_episode(
        tmp_path,
        analysis=TailKeepWithV10CorrectionAdapter(),
    )

    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()

    corrected = detail["segments"][1]
    assert corrected["status"] == "auto_corrected"
    assert corrected["adjudication"]["kind"] == "ownership-adjudication"
    assert corrected["adjudication"]["verdict"] == ("reassigned-by-agreement")


def test_processing_detail_lists_every_segment_with_evidence(
    tmp_path,
) -> None:
    client, _, episode_id = _completed_episode(tmp_path)

    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()

    summary = detail["summary"]
    assert summary["segment_count"] == 3
    assert summary["disputed_count"] == 1
    assert summary["decision_ledger_reference"]
    assert detail["speakers"]

    first, disputed, third = detail["segments"]
    assert first["status"] == "accepted"
    assert first["target_text"] == "他们当时不觉得算力是瓶颈。"
    assert first["output_start_seconds"] == 0.0
    assert disputed["status"] == "disputed"
    assert disputed["source_text"] == "and"
    assert disputed["adjudication"]["verdict"] == ("kept-discourse-abstained")
    assert disputed["adjudication"]["evidence"]["voiceprint_scores"] == {
        "speaker-01": 0.72,
        "speaker-02": 0.18,
    }
    assert third["output_start_seconds"] == 2.0


def test_processing_detail_explicitly_reports_missing_historical_evidence(
    tmp_path,
) -> None:
    client, harness, episode_id = _completed_episode(tmp_path)
    initial = client.get(
        f"/api/v1/episodes/{episode_id}/processing-detail"
    ).json()
    with harness.dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            initial["processing_run_id"],
        )
        assert run is not None
        run.analysis_json = json.dumps(
            {"pipeline_version": "transpodcast-modal-pipeline-v6"}
        )
        session.commit()

    detail = client.get(
        f"/api/v1/episodes/{episode_id}/processing-detail"
    ).json()

    assert detail["summary"]["evidence_available"] is False
    assert detail["summary"]["historical_evidence_unavailable"] is True
    assert detail["summary"]["pipeline_version"] == (
        "transpodcast-modal-pipeline-v6"
    )
    assert detail["segments"] == []


def test_processing_detail_does_not_call_empty_v14_evidence_historical(
    tmp_path,
) -> None:
    client, harness, episode_id = _completed_episode(tmp_path)
    initial = client.get(
        f"/api/v1/episodes/{episode_id}/processing-detail"
    ).json()
    with harness.dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            initial["processing_run_id"],
        )
        assert run is not None
        run.analysis_json = json.dumps(
            {"pipeline_version": "transpodcast-modal-pipeline-v14"}
        )
        session.commit()

    detail = client.get(
        f"/api/v1/episodes/{episode_id}/processing-detail"
    ).json()

    assert detail["summary"]["evidence_available"] is False
    assert detail["summary"]["historical_evidence_unavailable"] is False


def test_keep_ruling_archives_the_disputed_segment(tmp_path) -> None:
    client, _, episode_id = _completed_episode(tmp_path)

    ruled = client.post(
        f"/api/v1/episodes/{episode_id}/segments/2:rule",
        json={"action": "keep"},
    )

    assert ruled.status_code == 200
    assert ruled.json()["review_state"] == "accepted"
    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()
    assert detail["summary"]["disputed_count"] == 0
    archived = detail["segments"][1]
    assert archived["status"] == "archived"
    assert archived["owner_ruling"]["action"] == "keep"

    # Only a disputed segment accepts a ruling.
    repeat = client.post(
        f"/api/v1/episodes/{episode_id}/segments/2:rule",
        json={"action": "keep"},
    )
    assert repeat.status_code == 409


def test_overturn_ruling_queues_a_segment_revision(tmp_path) -> None:
    client, _, episode_id = _completed_episode(tmp_path)
    detail = client.get(f"/api/v1/episodes/{episode_id}/processing-detail").json()
    speaker_id = detail["speakers"][0]["id"]

    ruled = client.post(
        f"/api/v1/episodes/{episode_id}/segments/2:rule",
        json={"action": "overturn", "speaker_id": speaker_id},
    )

    assert ruled.status_code == 200
    body = ruled.json()
    assert body["action"] == "overturn"
    assert body["revision_job_id"] is not None

    missing_speaker = client.post(
        f"/api/v1/episodes/{episode_id}/segments/2:rule",
        json={"action": "overturn"},
    )
    assert missing_speaker.status_code == 409


def test_mark_issue_files_a_regression_case(tmp_path) -> None:
    client, harness, episode_id = _completed_episode(tmp_path)

    filed = client.post(
        f"/api/v1/episodes/{episode_id}/segments/3:mark-issue",
        json={"note": "The pacing sounds wrong here."},
    )

    assert filed.status_code == 200
    reference = filed.json()["artifact"]["artifact_sha256"]
    with harness.dependencies.database.session() as session:
        artifact = session.scalar(
            select(StructuredArtifactRecord).where(
                StructuredArtifactRecord.sha256 == reference
            )
        )
        assert artifact is not None
        assert artifact.artifact_kind == "speaker-exam-case"
        case = json.loads(artifact.payload_json)
        assert case["ordinal"] == 3
        assert case["note"] == "The pacing sounds wrong here."
