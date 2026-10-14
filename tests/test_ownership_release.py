import json

import pytest

from tests.test_speaker_ownership_continuity import example
from transpodcast.adjudication_completion import complete_boundary_adjudication
from transpodcast.decision_records import DecisionRecord
from transpodcast.domain import SourceAnalysisResult
from transpodcast.ownership_release import disputed_word_indexes
from transpodcast.pipeline_versions import (
    SPEECH_PIPELINE_V15,
    SPEECH_PIPELINE_V16,
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V18,
    SPEECH_PIPELINE_V21,
    SPEECH_PIPELINE_V22,
    pipeline_version_for_speech_model,
    speech_model_for_pipeline_version,
)
from transpodcast.speaker_ownership_v15 import (
    OwnershipAssignment,
    SpeakerOwnershipPlan,
    SpeakerOwnershipPlanningResult,
)
from transpodcast.testing import FakeTranslationAdapter


def test_new_runs_keep_renderer_choice_with_versioned_ownership() -> None:
    assert pipeline_version_for_speech_model("indextts2") == SPEECH_PIPELINE_V21
    assert pipeline_version_for_speech_model("indextts25") == SPEECH_PIPELINE_V22
    assert speech_model_for_pipeline_version(SPEECH_PIPELINE_V15) == "indextts2"
    assert speech_model_for_pipeline_version(SPEECH_PIPELINE_V16) == "indextts25"
    assert speech_model_for_pipeline_version(SPEECH_PIPELINE_V17) == "indextts2"
    assert speech_model_for_pipeline_version(SPEECH_PIPELINE_V18) == "indextts25"


def test_release_uses_final_review_and_resumes_roles_from_acoustic_checkpoint() -> None:
    segments, _ = example()
    analysis = SourceAnalysisResult(
        segments=segments,
        pipeline_version=SPEECH_PIPELINE_V17,
        diarization_model="fixture",
        transcription_model="fixture",
        alignment_model="fixture",
    )

    class Judge(FakeTranslationAdapter):
        def plan_ownership_continuity(self, regions):
            self.planned_speaker_ownership_regions.extend(regions)
            return SpeakerOwnershipPlanningResult(
                plans={
                    r.region_id: SpeakerOwnershipPlan(
                        "assign_spans",
                        (
                            OwnershipAssignment(
                                r.start_word_index, r.end_word_index, "a"
                            ),
                        ),
                        rationale="Propose ownership of the complete local turn.",
                    )
                    for r in regions
                }
            )

        def review_ownership_continuity(self, regions, planning):
            assert planning.plans
            self.reviewed_speaker_ownership_regions.extend(regions)
            return SpeakerOwnershipPlanningResult(
                plans={
                    r.region_id: SpeakerOwnershipPlan(
                        "keep",
                        rationale="Reject the draft and retain the original turns.",
                    )
                    for r in regions
                }
            )

    judge = Judge()
    saved = []
    result = complete_boundary_adjudication(analysis, judge, saved.append)
    assert result.completed and saved
    assert judge.planned_speaker_ownership_regions
    assert judge.reviewed_speaker_ownership_regions
    assert [w for s in result.analysis.segments for w in s.words] == [
        w for s in segments for w in s.words
    ]
    assert [s.speaker_label for s in result.analysis.segments for _ in s.words] == [
        s.speaker_label for s in segments for _ in s.words
    ]
    audit = json.loads(result.analysis.boundary_adjudication_json)
    assert "speaker_ownership_continuity" in audit
    resumed_judge = Judge()
    resumed = complete_boundary_adjudication(saved[-1], resumed_judge)
    assert not resumed_judge.planned_speaker_ownership_regions
    assert not resumed_judge.reviewed_speaker_ownership_regions
    assert resumed.analysis.segments == result.analysis.segments
    completed = complete_boundary_adjudication(result.analysis, Judge())
    assert not completed.completed
    assert completed.analysis == result.analysis


@pytest.mark.parametrize("valid_model", [True, False])
def test_acoustic_stage_pins_model_and_preserves_word_evidence(
    tmp_path,
    monkeypatch,
    valid_model: bool,
) -> None:
    from transpodcast import modal_pipeline

    segments = [
        {
            "segment_id": str(i),
            "speaker_label": "a" if i < 10 else "b",
            "start_seconds": float(i),
            "end_seconds": i + 0.9,
            "text": "word",
            "words": [
                {"text": "word", "start_seconds": float(i), "end_seconds": i + 0.9}
            ],
        }
        for i in range(12)
    ]
    turns = [
        {"speaker": "a", "start_seconds": 0.0, "end_seconds": 10.0},
        {"speaker": "b", "start_seconds": 10.0, "end_seconds": 12.0},
    ]
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_reference_audio",
        lambda source, dest, **kwargs: dest.write_bytes(b"clip"),
    )
    requests = []

    def remote(app, cls, method, clips):
        requests.append(clips)
        return {
            "model_id": "nvidia/speakerverification_en_titanet_large",
            # pragma: allowlist nextline secret
            "model_revision": "0dc382f40121a5fbd34db10a2bb04d826c2be6a8"
            if valid_model
            else "unexpected",
            "embeddings": {name: [0.0, 1.0] for name in clips},
            "inference_seconds": 0.2,
        }

    monkeypatch.setattr(modal_pipeline, "_remote_method", remote)
    evidence = modal_pipeline._measure_continuity_coverage(
        tmp_path / "source.wav",
        tmp_path,
        segments,
        [
            {"local_label": "a", "embedding": [1.0, 0.0]},
            {"local_label": "b", "embedding": [0.0, 1.0]},
        ],
        turns,
        {"speaker_mapping": {"a": "a", "b": "b"}},
        "",
        {},
        "source-sha",
    )
    assert requests and all(len(r) <= 64 for r in requests)
    if valid_model:
        assert not evidence["errors"] and evidence["voiceprints"]
        assert all(v["owner"] == "b" for v in evidence["voiceprints"])
        reused = modal_pipeline._measure_continuity_coverage(
            tmp_path / "source.wav",
            tmp_path,
            segments,
            [
                {"local_label": "a", "embedding": [1.0, 0.0]},
                {"local_label": "b", "embedding": [0.0, 1.0]},
            ],
            turns,
            {"speaker_mapping": {"a": "a", "b": "b"}},
            "",
            {"continuity_evidence": evidence},
            "source-sha",
        )
        assert len(requests) == 1
        assert reused["inference_seconds"] == 0.0
        assert reused["voiceprints"] == evidence["voiceprints"]
    else:
        assert evidence["errors"] and not evidence["voiceprints"]


@pytest.mark.parametrize("edge_action", ["assign_spans", "keep", "unsure"])
def test_conditional_measurement_is_checkpointed_before_edge_roles(
    monkeypatch, edge_action
) -> None:
    from transpodcast import ownership_release
    from transpodcast.domain import CanonicalSegment
    from transpodcast.speaker_ownership_continuity import expand_continuity_regions
    from transpodcast.speaker_ownership_v15 import OwnershipWord, SpeakerOwnershipRegion

    segments = tuple(
        CanonicalSegment(
            str(i),
            "a" if i < 10 else "b",
            i,
            i + 0.5,
            "word",
            words=(("word", float(i), i + 0.5),),
        )
        for i in range(11)
    )

    def selected(start, end):
        risk = SpeakerOwnershipRegion(
            f"risk-{start}",
            start,
            end,
            tuple(
                OwnershipWord(i, "word", float(i), i + 0.5, "a")
                for i in range(start, end)
            ),
            ("a", "b"),
            ("coverage-edge",),
        )
        return expand_continuity_regions(segments, (risk,), (), {})

    normal, edge = selected(3, 7), selected(0, 10)
    monkeypatch.setattr(
        ownership_release,
        "prepare_continuity_regions",
        lambda *args: edge if len(args) == 7 else normal,
    )

    class Judge(FakeTranslationAdapter):
        def plan_ownership_continuity(self, regions):
            self.planned_speaker_ownership_regions.extend(regions)
            return SpeakerOwnershipPlanningResult(
                plans={
                    r.region_id: SpeakerOwnershipPlan(
                        "assign_spans",
                        (
                            OwnershipAssignment(
                                r.start_word_index, r.end_word_index, "b"
                            ),
                        ),
                        rationale=(
                            f"Measured identity supports turn {r.start_word_index}."
                        ),
                    )
                    for r in regions
                }
            )

        def review_ownership_continuity(self, regions, planning):
            if regions[0].start_word_index == 0 and edge_action != "assign_spans":
                return SpeakerOwnershipPlanningResult(
                    plans={
                        r.region_id: SpeakerOwnershipPlan(
                            edge_action,
                            rationale="Preserve ownership pending stronger evidence.",
                        )
                        for r in regions
                    }
                )
            return planning

    analysis = SourceAnalysisResult(
        segments,
        SPEECH_PIPELINE_V17,
        "fixture",
        "fixture",
        "fixture",
        boundary_adjudication_json=json.dumps({"continuity_evidence": {}}),
    )
    saved = []
    measurements = []

    def measure(ranges):
        measurements.append(ranges)
        return {"voiceprints": [], "inference_seconds": 0.2, "estimated_cost_usd": 0.01}

    def interrupt(checkpoint):
        saved.append(checkpoint)
        phases = json.loads(checkpoint.boundary_adjudication_json)[
            "speaker_ownership_continuity_checkpoint"
        ]
        if "edge_acoustic" in phases:
            raise InterruptedError("Stop after the durable measurement checkpoint")

    with pytest.raises(InterruptedError):
        complete_boundary_adjudication(
            analysis, Judge(), interrupt, measure_edges=measure
        )
    judge = Judge()
    result = complete_boundary_adjudication(saved[-1], judge, measure_edges=measure)
    assert measurements == [[(0, 10)]]
    assert len(judge.planned_speaker_ownership_regions) == 1
    owners = [s.speaker_label for s in result.analysis.segments for _ in s.words]
    assert owners[3:7] == ["b"] * 4
    assert result.analysis.estimated_cost_usd == 0.01
    audit = json.loads(result.analysis.boundary_adjudication_json)
    summary = audit["speaker_ownership_continuity"]
    from transpodcast.service import _matching_adjudication_record

    visible = _matching_adjudication_record(audit["decision_records"], 3.0, 6.5)
    assert visible is not None
    if edge_action == "assign_spans":
        assert all(owner == "b" for owner in owners)
        assert not summary["remaining_edge_ranges"]
        assert visible["rationale"] == "Measured identity supports turn 0."
    else:
        assert owners[:3] == ["a"] * 3
        assert summary["remaining_edge_ranges"] == [[0, 10]]
        assert summary["disputed_region_count"] > 0
        assert all(
            s.review_state == "disputed"
            for s in result.analysis.segments
            if s.start_seconds < 10
        )
        assert visible["rationale"] == "Measured identity supports turn 3."
    assert (
        complete_boundary_adjudication(result.analysis, Judge()).analysis
        == result.analysis
    )


@pytest.mark.parametrize("action", ["unsure", "possible_missing_speech", "missing"])
def test_release_preserves_uncertain_final_review_as_disputed(action) -> None:
    segments, _ = example()
    analysis = SourceAnalysisResult(
        segments,
        SPEECH_PIPELINE_V17,
        "fixture",
        "fixture",
        "fixture",
        boundary_adjudication_json=json.dumps({"continuity_evidence": {}}),
    )

    class Judge(FakeTranslationAdapter):
        def review_ownership_continuity(self, regions, planning):
            return SpeakerOwnershipPlanningResult(
                plans={
                    r.region_id: SpeakerOwnershipPlan(
                        action,
                        rationale="Identity remains uncertain in this overlap.",
                        missing_speech_suspected=action == "possible_missing_speech",
                    )
                    for r in regions
                }
                if action != "missing"
                else {}
            )

    result = complete_boundary_adjudication(analysis, Judge())
    assert [s.speaker_label for s in result.analysis.segments for _ in s.words] == [
        s.speaker_label for s in segments for _ in s.words
    ]
    assert all(s.review_state == "disputed" for s in result.analysis.segments)
    summary = json.loads(result.analysis.boundary_adjudication_json)[
        "speaker_ownership_continuity"
    ]
    assert summary["disputed_region_count"] == summary["region_count"] > 0
    if action != "missing":
        assert not summary["degraded_errors"]


def _record(verdict: str, first: int, last: int, **evidence: object) -> DecisionRecord:
    return DecisionRecord(
        kind="speaker-ownership-adjudication",
        policy_version="test",
        subject={"first_word_index": first, "last_word_index": last},
        evidence={
            "reviewer_plan": {"action": "assign_spans"},
            "reviewer_execution": {},
            **evidence,
        },
        verdict=verdict,
        rationale="Fixture rationale.",
    )


def test_v21_marks_only_changed_words_of_acoustically_disputed_repairs() -> None:
    original = ["a"] * 6 + ["b"] * 6
    owners = ["a"] * 5 + ["b"] * 7
    records = [_record("applied-disputed", 0, 11)]
    assert disputed_word_indexes(records, original, owners, [], narrow=True) == {5}
    assert disputed_word_indexes(records, original, owners, [], narrow=False) == set(
        range(12)
    )


def test_v21_keeps_full_marking_for_degraded_or_unresolved_regions() -> None:
    original = ["a"] * 6 + ["b"] * 6
    owners = list(original)
    degraded = _record("provider-degraded", 0, 5)
    unreviewed = _record("applied-disputed", 6, 11, reviewer_plan=None)
    assert disputed_word_indexes(
        [degraded, unreviewed], original, owners, [], narrow=True
    ) == set(range(12))
    edge = _record("applied-disputed", 0, 11)
    assert disputed_word_indexes(
        [edge], original, owners, [(4, 8)], narrow=True
    ) == set(range(12))
