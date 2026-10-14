from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

import pytest

from transpodcast.adjudication_completion import (
    DiscourseAdjudicator,
    complete_boundary_adjudication,
)
from transpodcast.boundary_adjudication import OwnershipDispute, VoiceprintVerdict
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V15
from transpodcast.speaker_ownership_v15 import (
    OwnershipAssignment,
    SpeakerOwnershipPlan,
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
)


def _complete_assignments(
    region: SpeakerOwnershipRegion,
    changes: dict[int, str],
) -> tuple[OwnershipAssignment, ...]:
    owners = [changes.get(word.word_index, word.current_owner) for word in region.words]
    assignments: list[OwnershipAssignment] = []
    start = region.start_word_index
    for offset in range(1, len(owners) + 1):
        if offset < len(owners) and owners[offset] == owners[offset - 1]:
            continue
        assignments.append(
            OwnershipAssignment(
                start_word_index=start,
                end_word_index=region.start_word_index + offset,
                speaker_label=owners[offset - 1],
            )
        )
        start = region.start_word_index + offset
    return tuple(assignments)


class SentenceStartOwnershipAdjudicator:
    def __init__(self) -> None:
        self.planner_calls: list[tuple[SpeakerOwnershipRegion, ...]] = []
        self.reviewer_calls: list[tuple[SpeakerOwnershipRegion, ...]] = []

    def plan_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.planner_calls.append(regions)
        return SpeakerOwnershipPlanningResult(
            plans={region.region_id: self._plan(region) for region in regions},
            input_tokens=80,
            output_tokens=20,
            provider="fixture-codex",
            model="fixture-v15-planner",
            cli_version="fixture-cli 1.0",
        )

    def review_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.reviewer_calls.append(regions)
        return SpeakerOwnershipPlanningResult(
            plans={region.region_id: self._plan(region) for region in regions},
            input_tokens=60,
            output_tokens=15,
            provider="fixture-codex",
            model="fixture-v15-reviewer",
            cli_version="fixture-cli 1.0",
        )

    @staticmethod
    def _plan(region: SpeakerOwnershipRegion) -> SpeakerOwnershipPlan:
        word = next(word for word in region.words if word.text == "We're")
        return SpeakerOwnershipPlan(
            action="assign_spans",
            assignments=_complete_assignments(
                region,
                {word.word_index: "speaker_1"},
            ),
            rationale="We're begins speaker_1's complete sentence.",
        )


class ReviewerRecoveryAdjudicator(SentenceStartOwnershipAdjudicator):
    def plan_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.planner_calls.append(regions)
        return SpeakerOwnershipPlanningResult(
            plans={},
            error="Planner provider unavailable.",
        )


class InvalidPlannerAdjudicator(SentenceStartOwnershipAdjudicator):
    def plan_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.planner_calls.append(regions)
        region = regions[0]
        word = next(word for word in region.words if word.text == "We're")
        return SpeakerOwnershipPlanningResult(
            plans={
                region.region_id: SpeakerOwnershipPlan(
                    action="assign_spans",
                    assignments=_complete_assignments(
                        region,
                        {word.word_index: "invented-speaker"},
                    ),
                    rationale="Malformed provider output invented a Speaker.",
                )
            }
        )

    def review_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.reviewer_calls.append(regions)
        return SpeakerOwnershipPlanningResult(
            plans={
                region.region_id: SpeakerOwnershipPlan(
                    action="keep",
                    rationale="The original boundary is safer.",
                )
                for region in regions
            }
        )


class KeepOwnershipAdjudicator(SentenceStartOwnershipAdjudicator):
    def _keep(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        return SpeakerOwnershipPlanningResult(
            plans={
                region.region_id: SpeakerOwnershipPlan(
                    action="keep",
                    rationale="Existing word ownership is supported.",
                )
                for region in regions
            }
        )

    def plan_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.planner_calls.append(regions)
        return self._keep(regions)

    def review_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.reviewer_calls.append(regions)
        return self._keep(regions)


class MultiHandoffAdjudicator(KeepOwnershipAdjudicator):
    @staticmethod
    def _plan(region: SpeakerOwnershipRegion) -> SpeakerOwnershipPlan:
        target_by_word = {
            "Bstart": "speaker_b",
            "Areturn": "speaker_a",
            "Cstart": "speaker_c",
        }
        changes = {
            word.word_index: target_by_word[word.text]
            for word in region.words
            if word.text in target_by_word
        }
        return SpeakerOwnershipPlan(
            action="assign_spans",
            assignments=_complete_assignments(region, changes),
            rationale="Three exact incoming-turn prefixes are misowned.",
        )

    def _plans(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        return SpeakerOwnershipPlanningResult(
            plans={region.region_id: self._plan(region) for region in regions}
        )

    def plan_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.planner_calls.append(regions)
        return self._plans(regions)

    def review_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.reviewer_calls.append(regions)
        return self._plans(regions)


class FirstBatchFailsAdjudicator(KeepOwnershipAdjudicator):
    def __init__(self) -> None:
        super().__init__()
        self.planner_batch_count = 0
        self.reviewer_batch_count = 0

    @staticmethod
    def _second_batch_plans(
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        plans: dict[str, SpeakerOwnershipPlan | None] = {}
        for region in regions:
            word = next(
                item for item in region.words if item.text.startswith("Bstart-")
            )
            plans[region.region_id] = SpeakerOwnershipPlan(
                action="assign_spans",
                assignments=_complete_assignments(
                    region,
                    {word.word_index: "speaker_b"},
                ),
                rationale="The incoming answer begins one word earlier.",
            )
        return SpeakerOwnershipPlanningResult(plans=plans)

    def plan_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.planner_batch_count += 1
        if self.planner_batch_count == 1:
            raise RuntimeError("Planner batch unavailable")
        return self._second_batch_plans(regions)

    def review_speaker_ownership(
        self,
        regions: tuple[SpeakerOwnershipRegion, ...],
    ) -> SpeakerOwnershipPlanningResult:
        self.reviewer_batch_count += 1
        if self.reviewer_batch_count == 1:
            raise RuntimeError("Reviewer batch unavailable")
        return self._second_batch_plans(regions)


def _segment(
    segment_id: str,
    owner: str,
    words: tuple[tuple[str, float, float], ...],
) -> CanonicalSegment:
    return CanonicalSegment(
        segment_id=segment_id,
        speaker_label=owner,
        start_seconds=words[0][1],
        end_seconds=words[-1][2],
        text=" ".join(word[0] for word in words),
        words=words,
    )


def _run26_opening() -> SourceAnalysisResult:
    current = _segment(
        "seg-0001",
        "speaker_0",
        (("every", 7.92, 8.32), ("dimension", 8.32, 8.80), ("We're", 8.88, 9.04)),
    )
    following = _segment(
        "seg-0002",
        "speaker_1",
        (("unclear", 9.04, 9.52), ("right", 9.52, 9.84), ("now", 9.84, 10.16)),
    )
    return SourceAnalysisResult(
        segments=(current, following),
        pipeline_version=SPEECH_PIPELINE_V15,
        diarization_model="fixture-sortformer",
        transcription_model="fixture-qwen3-asr",
        alignment_model="fixture-qwen3-aligner",
        raw_diarization_turns=(
            ("speaker_0", 7.80, 8.84),
            ("speaker_1", 8.84, 10.20),
        ),
        full_asr_text="every dimension. We're unclear right now.",
        aligned_words=(*current.words, *following.words),
        speaker_consolidation_json=json.dumps(
            {
                "speaker_mapping": {
                    "speaker_0": "speaker_0",
                    "speaker_1": "speaker_1",
                },
                "decision_records": [],
            }
        ),
        boundary_adjudication_json="{}",
    )


def _batching_fixture() -> SourceAnalysisResult:
    segments: list[CanonicalSegment] = []
    raw_turns: list[tuple[str, float, float]] = []
    all_words: list[tuple[str, float, float]] = []
    cursor = 0.0
    for island in range(33):
        a_words: list[tuple[str, float, float]] = []
        for index in range(26):
            word = (f"a-{island}-{index}", cursor, cursor + 0.2)
            cursor += 0.2
            a_words.append(word)
            all_words.append(word)
            raw_turns.append(("speaker_a", word[1], word[2]))
        b_start = (f"Bstart-{island}", cursor, cursor + 0.2)
        cursor += 0.2
        a_words.append(b_start)
        all_words.append(b_start)
        raw_turns.append(("speaker_b", b_start[1], b_start[2]))
        segments.append(_segment(f"a-{island}", "speaker_a", tuple(a_words)))
        b_body = (f"Bbody-{island}", cursor, cursor + 0.2)
        cursor += 0.2
        all_words.append(b_body)
        raw_turns.append(("speaker_b", b_body[1], b_body[2]))
        segments.append(_segment(f"b-{island}", "speaker_b", (b_body,)))
        a_return = (f"Areturn-{island}", cursor, cursor + 0.2)
        cursor += 0.2
        all_words.append(a_return)
        raw_turns.append(("speaker_a", a_return[1], a_return[2]))
        segments.append(
            _segment(f"return-{island}", "speaker_a", (a_return,))
        )
    return SourceAnalysisResult(
        segments=tuple(segments),
        pipeline_version=SPEECH_PIPELINE_V15,
        diarization_model="fixture-sortformer",
        transcription_model="fixture-qwen3-asr",
        alignment_model="fixture-qwen3-aligner",
        raw_diarization_turns=tuple(raw_turns),
        full_asr_text=" ".join(word[0] for word in all_words),
        aligned_words=tuple(all_words),
        speaker_consolidation_json=json.dumps(
            {
                "speaker_mapping": {
                    "speaker_a": "speaker_a",
                    "speaker_b": "speaker_b",
                }
            }
        ),
    )


def test_v15_assigns_words_before_natural_segmentation() -> None:
    analysis = _run26_opening()
    original_words = tuple(
        word for segment in analysis.segments for word in segment.words
    )
    adjudicator = SentenceStartOwnershipAdjudicator()

    completion = complete_boundary_adjudication(
        analysis,
        cast(DiscourseAdjudicator, adjudicator),
    )

    assert len(adjudicator.planner_calls) == 1
    assert len(adjudicator.reviewer_calls) == 1
    assert [segment.speaker_label for segment in completion.analysis.segments] == [
        "speaker_0",
        "speaker_1",
    ]
    assert completion.analysis.segments[0].text == "every dimension."
    assert completion.analysis.segments[1].text == "We're unclear right now."
    assert tuple(
        word for segment in completion.analysis.segments for word in segment.words
    ) == original_words
    assert completion.ownership_changed_word_count == 1
    assert completion.natural_segment_count == 2
    payload = json.loads(completion.analysis.boundary_adjudication_json)
    summary = payload["speaker_ownership_v15"]
    assert summary["region_count"] == 1
    assert summary["changed_word_count"] == 1
    assert summary["disputed_region_count"] == 0
    assert summary["natural_segmentation"]["fallback_boundary_count"] == 0
    assert summary["decision_records"][0]["verdict"] == "applied-confirmed"
    assert any(
        record["kind"] == "natural-segmentation-boundary"
        and record["evidence"]["boundary_kind"] == "speaker"
        for record in summary["decision_records"]
    )

    replay = complete_boundary_adjudication(
        completion.analysis,
        cast(DiscourseAdjudicator, adjudicator),
    )
    assert replay.completed is False
    assert replay.analysis == completion.analysis
    assert len(adjudicator.planner_calls) == 1


def test_v15_preserves_the_run22_do_you_boundary() -> None:
    first = _segment(
        "seg-0001",
        "speaker_0",
        (
            ("pretty", 0.0, 0.3),
            ("large", 0.3, 0.6),
            ("batches", 0.6, 1.0),
        ),
    )
    second = _segment(
        "seg-0002",
        "speaker_1",
        (
            ("Do", 1.0, 1.2),
            ("you", 1.2, 1.4),
            ("think", 1.4, 1.7),
            ("it's", 1.7, 1.9),
            ("possible", 1.9, 2.3),
        ),
    )
    analysis = SourceAnalysisResult(
        segments=(first, second),
        pipeline_version=SPEECH_PIPELINE_V15,
        diarization_model="fixture-sortformer",
        transcription_model="fixture-qwen3-asr",
        alignment_model="fixture-qwen3-aligner",
        raw_diarization_turns=(
            ("speaker_0", 0.0, 1.0),
            ("speaker_1", 1.0, 2.3),
        ),
        full_asr_text="pretty large batches. Do you think it's possible?",
        aligned_words=(*first.words, *second.words),
        speaker_consolidation_json=json.dumps(
            {
                "speaker_mapping": {
                    "speaker_0": "speaker_0",
                    "speaker_1": "speaker_1",
                }
            }
        ),
    )

    completion = complete_boundary_adjudication(
        analysis,
        cast(DiscourseAdjudicator, KeepOwnershipAdjudicator()),
    )

    assert [
        (segment.speaker_label, segment.text)
        for segment in completion.analysis.segments
    ] == [
        ("speaker_0", "pretty large batches."),
        ("speaker_1", "Do you think it's possible?"),
    ]
    assert tuple(
        word for segment in completion.analysis.segments for word in segment.words
    ) == (*first.words, *second.words)


def test_v15_reviewer_mutation_survives_planner_provider_failure() -> None:
    adjudicator = ReviewerRecoveryAdjudicator()

    completion = complete_boundary_adjudication(
        _run26_opening(),
        cast(DiscourseAdjudicator, adjudicator),
    )

    assert len(adjudicator.planner_calls) == 1
    assert len(adjudicator.reviewer_calls) == 1
    assert completion.ownership_changed_word_count == 1
    assert completion.ownership_disputed_region_count == 1
    assert completion.discourse_error == "Planner provider unavailable."
    assert completion.analysis.segments[1].text == "We're unclear right now."
    payload = json.loads(completion.analysis.boundary_adjudication_json)
    record = payload["speaker_ownership_v15"]["decision_records"][0]
    assert record["verdict"] == "applied-disputed"
    assert record["evidence"]["applied_plan_source"] == "reviewer"


def test_v15_revalidates_adapter_plans_before_application() -> None:
    adjudicator = InvalidPlannerAdjudicator()

    completion = complete_boundary_adjudication(
        _run26_opening(),
        cast(DiscourseAdjudicator, adjudicator),
    )

    assert [segment.speaker_label for segment in completion.analysis.segments] == [
        "speaker_0",
        "speaker_1",
    ]
    assert completion.analysis.segments[0].text == "every dimension. We're"
    payload = json.loads(completion.analysis.boundary_adjudication_json)
    record = payload["speaker_ownership_v15"]["decision_records"][0]
    assert record["verdict"] == "planner-degraded"
    assert record["evidence"]["planner_validation_error"] == (
        "Speaker ownership assignment uses an unknown Speaker"
    )


def test_v15_protects_crosstalk_regions_from_optional_segmentation() -> None:
    words = tuple(
        (f"word-{index}", float(index), float(index + 1))
        for index in range(20)
    )
    transcript = " ".join(
        f"word-{index}{'.' if index in {5, 11, 17, 19} else ''}"
        for index in range(20)
    )
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker_0",
                start_seconds=0.0,
                end_seconds=20.0,
                text=" ".join(word[0] for word in words),
                words=words,
                overlap=True,
            ),
        ),
        pipeline_version=SPEECH_PIPELINE_V15,
        diarization_model="fixture-sortformer",
        transcription_model="fixture-qwen3-asr",
        alignment_model="fixture-qwen3-aligner",
        raw_diarization_turns=(("speaker_0", 0.0, 20.0),),
        full_asr_text=transcript,
        aligned_words=words,
        speaker_consolidation_json=json.dumps(
            {"speaker_mapping": {"speaker_0": "speaker_0"}}
        ),
    )
    adjudicator = KeepOwnershipAdjudicator()

    completion = complete_boundary_adjudication(
        analysis,
        cast(DiscourseAdjudicator, adjudicator),
    )

    assert len(completion.analysis.segments) == 1
    summary = json.loads(completion.analysis.boundary_adjudication_json)[
        "speaker_ownership_v15"
    ]
    assert summary["region_count"] == 1
    assert summary["natural_segmentation"]["fallback_boundary_count"] == 0


def test_v15_records_each_required_crosstalk_fallback_boundary() -> None:
    words = tuple(
        (f"word-{index}", float(index), float(index + 1))
        for index in range(30)
    )
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker_0",
                start_seconds=0.0,
                end_seconds=30.0,
                text=" ".join(word[0] for word in words),
                words=words,
                overlap=True,
            ),
        ),
        pipeline_version=SPEECH_PIPELINE_V15,
        diarization_model="fixture-sortformer",
        transcription_model="fixture-qwen3-asr",
        alignment_model="fixture-qwen3-aligner",
        raw_diarization_turns=(("speaker_0", 0.0, 30.0),),
        full_asr_text=" ".join(word[0] for word in words),
        aligned_words=words,
        speaker_consolidation_json=json.dumps(
            {"speaker_mapping": {"speaker_0": "speaker_0"}}
        ),
    )

    completion = complete_boundary_adjudication(
        analysis,
        cast(DiscourseAdjudicator, KeepOwnershipAdjudicator()),
    )

    summary = json.loads(completion.analysis.boundary_adjudication_json)[
        "speaker_ownership_v15"
    ]
    fallback_records = [
        record
        for record in summary["decision_records"]
        if record["kind"] == "natural-segmentation-boundary"
        and record["verdict"] == "fallback-cut"
    ]
    assert len(fallback_records) == summary["natural_segmentation"][
        "fallback_boundary_count"
    ]
    assert fallback_records
    assert all(
        record["subject"]["before_word_index"]
        == record["subject"]["after_word_index"] + 1
        for record in fallback_records
    )


def test_v15_locally_preserves_an_oversized_dense_risk_region() -> None:
    words = tuple(
        (f"word-{index}-{'x' * 3000}", float(index), float(index + 1))
        for index in range(100)
    )
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker_0",
                start_seconds=0.0,
                end_seconds=100.0,
                text="oversized crosstalk fixture",
                words=words,
                overlap=True,
            ),
        ),
        pipeline_version=SPEECH_PIPELINE_V15,
        diarization_model="fixture-sortformer",
        transcription_model="fixture-qwen3-asr",
        alignment_model="fixture-qwen3-aligner",
        raw_diarization_turns=(("speaker_0", 0.0, 100.0),),
        full_asr_text="",
        aligned_words=words,
        speaker_consolidation_json=json.dumps(
            {"speaker_mapping": {"speaker_0": "speaker_0"}}
        ),
    )
    adjudicator = KeepOwnershipAdjudicator()

    completion = complete_boundary_adjudication(
        analysis,
        cast(DiscourseAdjudicator, adjudicator),
    )

    assert adjudicator.planner_calls == []
    assert adjudicator.reviewer_calls == []
    assert completion.completed is True
    assert completion.discourse_error is not None
    summary = json.loads(completion.analysis.boundary_adjudication_json)[
        "speaker_ownership_v15"
    ]
    assert summary["oversized_region_count"] == 1
    assert summary["batch_count"] == 0
    assert summary["decision_records"][0]["verdict"] == "provider-degraded"
    assert tuple(
        word for segment in completion.analysis.segments for word in segment.words
    ) == words


def test_v15_retains_voiceprint_conflict_as_disputed_evidence() -> None:
    analysis = _run26_opening()
    dispute = OwnershipDispute(
        dispute_id="voiceprint-we-are",
        words=(
            {
                "text": "We're",
                "start_seconds": 8.88,
                "end_seconds": 9.04,
            },
        ),
        current_owner="speaker_0",
        counterpart="speaker_1",
        candidate_owners=("speaker_0", "speaker_1"),
        trigger_reasons=("short-boundary",),
    )
    voiceprint = VoiceprintVerdict(
        owner="speaker_0",
        scores={"speaker_0": 0.88, "speaker_1": 0.41},
    )
    analysis = replace(
        analysis,
        boundary_adjudication_json=json.dumps(
            {
                "pending": {
                    "disputes": [dispute.to_payload()],
                    "voiceprints": {
                        dispute.dispute_id: voiceprint.to_payload()
                    },
                }
            }
        ),
    )
    adjudicator = SentenceStartOwnershipAdjudicator()

    completion = complete_boundary_adjudication(
        analysis,
        cast(DiscourseAdjudicator, adjudicator),
    )

    assert completion.ownership_changed_word_count == 1
    assert completion.ownership_disputed_region_count == 1
    record = json.loads(completion.analysis.boundary_adjudication_json)[
        "speaker_ownership_v15"
    ]["decision_records"][0]
    assert record["evidence"]["acoustic_conflict"] is True
    assert record["evidence"]["voiceprint_evidence"] == [
        {
            "dispute_id": "voiceprint-we-are",
            "first_word_index": 2,
            "last_word_index": 2,
            "owner": "speaker_0",
            "scores": {"speaker_0": 0.88, "speaker_1": 0.41},
        }
    ]


def test_v15_applies_a_b_a_and_three_speaker_handoffs_together() -> None:
    original_segments = (
        _segment(
            "seg-1",
            "speaker_a",
            (("Astart", 0.0, 0.4), ("Bstart", 0.4, 0.8)),
        ),
        _segment(
            "seg-2",
            "speaker_b",
            (("Bbody", 0.8, 1.2), ("Areturn", 1.2, 1.6)),
        ),
        _segment(
            "seg-3",
            "speaker_a",
            (("Abody", 1.6, 2.0), ("Cstart", 2.0, 2.4)),
        ),
        _segment(
            "seg-4",
            "speaker_c",
            (("Cbody", 2.4, 2.8),),
        ),
    )
    analysis = SourceAnalysisResult(
        segments=original_segments,
        pipeline_version=SPEECH_PIPELINE_V15,
        diarization_model="fixture-sortformer",
        transcription_model="fixture-qwen3-asr",
        alignment_model="fixture-qwen3-aligner",
        raw_diarization_turns=(
            ("speaker_a", 0.0, 0.4),
            ("speaker_b", 0.4, 1.2),
            ("speaker_a", 1.2, 2.0),
            ("speaker_c", 2.0, 2.8),
        ),
        full_asr_text=(
            "Astart. Bstart Bbody. Areturn Abody. Cstart Cbody."
        ),
        aligned_words=tuple(
            word for segment in original_segments for word in segment.words
        ),
        speaker_consolidation_json=json.dumps(
            {
                "speaker_mapping": {
                    "speaker_a": "speaker_a",
                    "speaker_b": "speaker_b",
                    "speaker_c": "speaker_c",
                }
            }
        ),
    )
    original_words = tuple(
        word for segment in original_segments for word in segment.words
    )
    adjudicator = MultiHandoffAdjudicator()

    completion = complete_boundary_adjudication(
        analysis,
        cast(DiscourseAdjudicator, adjudicator),
    )

    assert [
        (segment.speaker_label, segment.text)
        for segment in completion.analysis.segments
    ] == [
        ("speaker_a", "Astart."),
        ("speaker_b", "Bstart Bbody."),
        ("speaker_a", "Areturn Abody."),
        ("speaker_c", "Cstart Cbody."),
    ]
    assert tuple(
        word
        for segment in completion.analysis.segments
        for word in segment.words
    ) == original_words
    assert completion.ownership_changed_word_count == 3
    assert completion.ownership_disputed_region_count == 0


def test_v15_isolates_provider_failure_to_one_bounded_batch() -> None:
    analysis = _batching_fixture()
    adjudicator = FirstBatchFailsAdjudicator()

    completion = complete_boundary_adjudication(
        analysis,
        cast(DiscourseAdjudicator, adjudicator),
    )

    assert adjudicator.planner_batch_count == 2
    assert adjudicator.reviewer_batch_count == 2
    assert completion.ownership_region_count == 33
    assert completion.ownership_changed_word_count == 1
    assert completion.discourse_error == "RuntimeError: Planner batch unavailable"
    assert any(
        segment.text.startswith("Bstart-32 Bbody-32")
        and segment.speaker_label == "speaker_b"
        for segment in completion.analysis.segments
    )
    summary = json.loads(completion.analysis.boundary_adjudication_json)[
        "speaker_ownership_v15"
    ]
    assert summary["batch_count"] == 2
    assert summary["batches"][0]["planner_error"] is not None
    assert summary["batches"][1]["planner_error"] is None


def test_v15_resumes_completed_batches_without_reinvoking_judges() -> None:
    analysis = _batching_fixture()
    first_attempt = KeepOwnershipAdjudicator()
    checkpoints: list[SourceAnalysisResult] = []

    def interrupt_after_checkpoint(checkpoint: SourceAnalysisResult) -> None:
        checkpoints.append(checkpoint)
        raise RuntimeError("simulated worker interruption")

    with pytest.raises(RuntimeError, match="simulated worker interruption"):
        complete_boundary_adjudication(
            analysis,
            cast(DiscourseAdjudicator, first_attempt),
            interrupt_after_checkpoint,
        )

    assert len(first_attempt.planner_calls) == 1
    assert len(first_attempt.planner_calls[0]) == 32
    resumed_adjudicator = KeepOwnershipAdjudicator()
    completion = complete_boundary_adjudication(
        checkpoints[-1],
        cast(DiscourseAdjudicator, resumed_adjudicator),
    )

    assert len(resumed_adjudicator.planner_calls) == 1
    assert len(resumed_adjudicator.planner_calls[0]) == 1
    summary = json.loads(completion.analysis.boundary_adjudication_json)[
        "speaker_ownership_v15"
    ]
    assert [
        batch["resumed_from_checkpoint"] for batch in summary["batches"]
    ] == [True, False]
    assert "speaker_ownership_v15_checkpoint" not in json.loads(
        completion.analysis.boundary_adjudication_json
    )
