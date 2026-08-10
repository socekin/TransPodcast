from __future__ import annotations

import json
from dataclasses import replace

from transpodcast.adapters.codex import CodexExecution, CodexTranslationAdapter
from transpodcast.adjudication_completion import complete_boundary_adjudication
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V13, SPEECH_PIPELINE_V14


class Run22BoundaryRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, *, instructions, source_data, schema):
        del schema
        self.calls.append(source_data)
        candidate = source_data["candidates"][0]
        assert candidate["current_segment_id"] == "seg-0081"
        assert candidate["next_segment_id"] == "seg-0082"
        assert [word["text"] for word in candidate["current_words"]] == [
            "pretty",
            "large",
            "batches",
            "Do",
            "you",
        ]
        assert [word["text"] for word in candidate["next_words"][:3]] == [
            "think",
            "it's",
            "possible",
        ]
        if instructions.startswith("You are the Boundary Planner"):
            return CodexExecution(
                output={
                    "proposals": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "action": "split_to_next",
                            "target_segment_id": "seg-0081",
                            "target_owner": "speaker_0",
                            "split_before_word_index": 3,
                            "rationale": (
                                "Do you supplies the missing beginning of "
                                "the following question."
                            ),
                        }
                    ]
                },
                model="deterministic-v14-planner",
                input_tokens=80,
                output_tokens=20,
                cli_version="fixture-cli 1.0",
            )
        assert "independent Boundary Reviewer" in instructions
        proposal = candidate["proposal"]
        assert proposal["action"] == "split_to_next"
        assert proposal["split_before_word_index"] == 3
        return CodexExecution(
            output={
                "verdicts": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "verdict": "approve",
                        "rationale": (
                            "The split conserves every word and restores the "
                            "complete question."
                        ),
                    }
                ]
            },
            model="deterministic-v14-reviewer",
            input_tokens=60,
            output_tokens=15,
            cli_version="fixture-cli 1.0",
        )


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


def _run22_analysis() -> SourceAnalysisResult:
    current = _segment(
        "seg-0081",
        "speaker_1",
        (
            ("pretty", 863.880, 864.080),
            ("large", 864.080, 864.360),
            ("batches", 864.360, 864.760),
            ("Do", 865.200, 865.200),
            ("you", 865.200, 865.319),
        ),
    )
    following = _segment(
        "seg-0082",
        "speaker_0",
        (
            ("think", 866.359, 866.680),
            ("it's", 866.680, 866.860),
            ("possible", 866.860, 867.300),
            ("to", 867.300, 867.420),
            ("train", 867.420, 867.760),
        ),
    )
    dispute_id = "dispute-863.880-speaker_1"
    boundary_payload = {
        "policy_version": "boundary-ownership-adjudication-v4",
        "pending": {
            "disputes": [
                {
                    "dispute_id": dispute_id,
                    "words": [
                        {
                            "text": text,
                            "start_seconds": start,
                            "end_seconds": end,
                        }
                        for text, start, end in current.words
                    ],
                    "current_owner": "speaker_1",
                    "counterpart": "speaker_0",
                    "candidate_owners": ["speaker_0", "speaker_1"],
                    "trigger_reasons": ["short-boundary"],
                    "group_id": "group-863.880",
                    "context_before_owner": "speaker_1",
                    "context_after_owner": "speaker_0",
                    "context_before": ["in", "pretty", "large", "batches"],
                    "context_after": [
                        "think",
                        "it's",
                        "possible",
                        "to",
                        "train",
                    ],
                }
            ],
            "voiceprints": {
                dispute_id: {
                    "owner": "speaker_1",
                    "scores": {"speaker_0": 0.03, "speaker_1": 0.54},
                }
            },
        },
    }
    return SourceAnalysisResult(
        segments=(current, following),
        pipeline_version=SPEECH_PIPELINE_V14,
        diarization_model="nvidia/diar_streaming_sortformer_4spk-v2.1",
        transcription_model="Qwen/Qwen3-ASR-1.7B",
        alignment_model="Qwen/Qwen3-ForcedAligner-0.6B",
        diarization_model_revision="fixture-sortformer-v21",
        raw_diarization_turns=(
            ("speaker_1", 848.240, 865.840),
            ("speaker_0", 866.320, 881.600),
        ),
        aligned_words=(*current.words, *following.words),
        speaker_consolidation_json=json.dumps(
            {
                "speaker_mapping": {
                    "speaker_0": "speaker_0",
                    "speaker_1": "speaker_1",
                }
            }
        ),
        boundary_adjudication_json=json.dumps(boundary_payload),
    )


def test_v14_applies_the_reviewed_run22_boundary_from_the_immutable_snapshot(
    tmp_path,
) -> None:
    analysis = _run22_analysis()
    original_words = tuple(
        word for segment in analysis.segments for word in segment.words
    )
    runner = Run22BoundaryRunner()
    adapter = CodexTranslationAdapter(runner, tmp_path)  # type: ignore[arg-type]

    completion = complete_boundary_adjudication(analysis, adapter)

    current, following = completion.analysis.segments
    assert len(runner.calls) == 2
    assert current.segment_id == "seg-0081"
    assert current.speaker_label == "speaker_1"
    assert current.text == "pretty large batches"
    assert following.segment_id == "seg-0082"
    assert following.speaker_label == "speaker_0"
    assert following.text == "Do you think it's possible to train"
    assert current.words + following.words == original_words
    assert len(completion.analysis.segments) == 2

    payload = json.loads(completion.analysis.boundary_adjudication_json)
    summary = payload["unified_boundary_adjudication"]
    assert summary["policy_version"] == "speaker-boundary-adjudication-v1"
    assert summary["candidate_count"] == 1
    assert summary["planner_proposed_count"] == 1
    assert summary["reviewer_approved_count"] == 1
    assert summary["split_to_next_count"] == 1
    assert summary["reassign_whole_count"] == 0
    record = summary["decision_records"][0]
    assert record["evidence"]["planner_action"] == "split_to_next"
    assert record["evidence"]["split_before_word_index"] == 3
    assert record["evidence"]["reviewer_verdict"] == "approve"
    assert record["verdict"] == "split-to-next-applied"
    assert record["subject"]["start_seconds"] == 863.88
    assert record["subject"]["end_seconds"] == 867.76
    assert [word["text"] for word in record["evidence"]["immutable_current_words"]] == [
        "pretty",
        "large",
        "batches",
        "Do",
        "you",
    ]
    assert record["evidence"]["planner_execution"] == {
        "provider": "codex-cli",
        "model": "deterministic-v14-planner",
        "cli_version": "fixture-cli 1.0",
        "input_tokens": 80,
        "output_tokens": 20,
    }
    assert record["evidence"]["reviewer_execution"] == {
        "provider": "codex-cli",
        "model": "deterministic-v14-reviewer",
        "cli_version": "fixture-cli 1.0",
        "input_tokens": 60,
        "output_tokens": 15,
    }
    assert summary["planner_usage"] == {
        "input_tokens": 80,
        "output_tokens": 20,
    }
    assert summary["reviewer_usage"] == {
        "input_tokens": 60,
        "output_tokens": 15,
    }

    replay = complete_boundary_adjudication(completion.analysis, adapter)
    assert replay.completed is False
    assert replay.analysis == completion.analysis
    assert len(runner.calls) == 2


def test_v14_promotion_leaves_a_completed_v13_summary_byte_identical(
    tmp_path,
) -> None:
    serialized = (
        '{"policy_version":"boundary-ownership-adjudication-v4",'
        '"decision_records":[],"speaker_tail_boundary_repair":'
        '{"policy_version":"speaker-tail-boundary-repair-v3",'
        '"candidate_count":2,"repair_count":1}}'
    )
    historical = replace(
        _run22_analysis(),
        pipeline_version=SPEECH_PIPELINE_V13,
        boundary_adjudication_json=serialized,
    )
    runner = Run22BoundaryRunner()
    adapter = CodexTranslationAdapter(runner, tmp_path)  # type: ignore[arg-type]

    completion = complete_boundary_adjudication(historical, adapter)

    assert completion.completed is False
    assert completion.analysis.boundary_adjudication_json == serialized
    assert runner.calls == []
