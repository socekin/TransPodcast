from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from transpodcast.adjudication_completion import (
    complete_boundary_adjudication,
)
from transpodcast.boundary_adjudication import (
    LEGACY_ADJUDICATION_POLICY_VERSION,
    DiscourseAdjudicationResult,
    DiscourseVerdict,
)
from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.modal_pipeline import analyze
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V13


def _turns() -> list[dict]:
    # speaker-1 keeps talking; a 0.4s speaker-2 sliver steals 'and'.
    return [
        {"speaker": "speaker-1", "start_seconds": 100.0, "end_seconds": 110.8},
        {"speaker": "speaker-2", "start_seconds": 110.9, "end_seconds": 111.3},
        {"speaker": "speaker-1", "start_seconds": 111.4, "end_seconds": 122.0},
        {"speaker": "speaker-1", "start_seconds": 200.0, "end_seconds": 212.0},
        {"speaker": "speaker-1", "start_seconds": 300.0, "end_seconds": 312.0},
        {"speaker": "speaker-2", "start_seconds": 150.0, "end_seconds": 162.0},
        {"speaker": "speaker-2", "start_seconds": 250.0, "end_seconds": 262.0},
        {"speaker": "speaker-2", "start_seconds": 350.0, "end_seconds": 362.0},
    ]


def _words() -> list[dict]:
    return [
        {"text": "we", "start_seconds": 100.5, "end_seconds": 103.0},
        {"text": "and", "start_seconds": 110.95, "end_seconds": 111.25},
        {"text": "continue", "start_seconds": 111.5, "end_seconds": 114.0},
        {"text": "other", "start_seconds": 150.5, "end_seconds": 153.0},
        {"text": "steady", "start_seconds": 200.5, "end_seconds": 203.0},
        {"text": "stable", "start_seconds": 250.5, "end_seconds": 253.0},
        {"text": "speaker", "start_seconds": 300.5, "end_seconds": 303.0},
        {"text": "turn", "start_seconds": 350.5, "end_seconds": 353.0},
    ]


def _vector_for(key: str) -> list[float]:
    if key.startswith("dispute-"):
        # The disputed fragment sounds like speaker-1.
        return [1.0, 0.0]
    if "speaker-2" in key:
        return [0.0, 1.0]
    return [1.0, 0.0]


@pytest.fixture()
def analyzed(tmp_path, monkeypatch):
    from transpodcast import modal_pipeline

    source = tmp_path / "source.webm"
    source.write_bytes(b"source-audio")
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    turns = _turns()
    words = _words()

    def fake_transcode(source_path, destination, **kwargs):
        del source_path, kwargs
        destination.write_bytes(b"wav")

    def fake_remote(app_name, class_name, method_name, payload):
        del class_name, method_name
        if app_name == modal_pipeline.SORTFORMER_APP:
            return {
                "segments": turns,
                "model_id": "sortformer",
                "inference_seconds": 1.0,
            }
        if app_name == modal_pipeline.QWEN_ASR_APP:
            return {
                "text": "We and continue other steady stable speaker turn.",
                "words": words,
                "asr_model_id": "qwen3-asr",
                "aligner_model_id": "qwen3-forced-aligner",
                "inference_seconds": 1.0,
            }
        return {
            "embeddings": {
                key: _vector_for(key) for key in payload
            },
            "inference_seconds": 1.0,
        }

    def fake_reference_audio(
        source_wav,
        destination,
        *,
        start_seconds,
        duration_seconds,
    ):
        del source_wav, start_seconds, duration_seconds
        destination.write_bytes(b"clip")

    def fake_references(source_wav, diarization, reference_root):
        del source_wav, diarization
        references = {}
        for label in ("speaker-1", "speaker-2"):
            path = reference_root / f"{label}.wav"
            path.write_bytes(label.encode())
            references[label] = path
        return references

    monkeypatch.setattr(modal_pipeline, "_transcode", fake_transcode)
    monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_reference_audio",
        fake_reference_audio,
    )
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_speaker_references",
        fake_references,
    )
    return analyze(
        {
            "source": {
                "audio_path": str(source),
                "checksum": checksum,
            }
        },
        tmp_path / "output",
    )


class ScriptedAdapter:
    """A deterministic Translation Adapter for the discourse method."""

    def __init__(self, answer: str | None):
        self.answer = answer
        self.asked: list[str] = []

    def adjudicate_discourse(self, disputes):
        self.asked.extend(
            dispute.dispute_id for dispute in disputes
        )
        if self.answer is None:
            return DiscourseAdjudicationResult(
                verdicts={},
                error="RuntimeError: provider unavailable",
            )
        return DiscourseAdjudicationResult(
            verdicts={
                dispute.dispute_id: DiscourseVerdict(
                    owner=(
                        dispute.counterpart
                        if self.answer == "continuation"
                        else dispute.current_owner
                    ),
                    rationale="Scripted verdict.",
                )
                for dispute in disputes
            },
            input_tokens=120,
            output_tokens=30,
        )


def _analysis_from(result: dict) -> SourceAnalysisResult:
    return SourceAnalysisResult(
        segments=tuple(
            CanonicalSegment(
                segment_id=str(item["segment_id"]),
                speaker_label=str(item["speaker_label"]),
                start_seconds=float(item["start_seconds"]),
                end_seconds=float(item["end_seconds"]),
                text=str(item["text"]),
                words=tuple(
                    (
                        str(word["text"]),
                        float(word["start_seconds"]),
                        float(word["end_seconds"]),
                    )
                    for word in item.get("words", [])
                ),
                overlap=bool(item.get("overlap", False)),
                review_state=str(
                    item.get("review_state", "accepted")
                ),
            )
            for item in result["segments"]
        ),
        # These completion cases preserve the historical V13 two-judge
        # contract. V14 has its own public-seam acceptance suite.
        pipeline_version=SPEECH_PIPELINE_V13,
        diarization_model="d",
        transcription_model="t",
        alignment_model="a",
        speaker_consolidation_json=json.dumps(
            result.get("speaker_consolidation", {})
        ),
        boundary_adjudication_json=json.dumps(
            result.get("boundary_adjudication", {})
        ),
    )


# --- Source Analysis stays acoustic: it emits, it never asks ---


def test_analysis_emits_pending_disputes_without_changing_ownership(
    analyzed,
) -> None:
    adjudication = analyzed["boundary_adjudication"]
    assert adjudication["dispute_count"] == 1
    assert adjudication["candidate_count"] == 1
    assert adjudication["short_boundary_candidate_count"] == 1
    assert adjudication["sparse_label_candidate_count"] == 0
    assert adjudication["stable_segment_count"] == 7
    assert adjudication["changed_count"] == 0
    assert adjudication["decision_records"] == []
    pending = adjudication["pending"]
    assert len(pending["disputes"]) == 1
    dispute = pending["disputes"][0]
    assert dispute["current_owner"] == "speaker-2"
    assert dispute["counterpart"] == "speaker-1"
    voiceprint = pending["voiceprints"][dispute["dispute_id"]]
    assert voiceprint["owner"] == "speaker-1"
    stolen = next(
        segment
        for segment in analyzed["segments"]
        if segment["text"] == "and"
    )
    assert stolen["speaker_label"] == "speaker-2"
    assert stolen.get("review_state", "accepted") == "accepted"


def test_analysis_defers_attribution_records_while_pending(
    analyzed,
) -> None:
    kinds = {
        record["kind"]
        for record in analyzed["speaker_consolidation"][
            "decision_records"
        ]
    }
    assert "segment-attribution" not in kinds


# --- The worker layer completes the verdicts through the adapter ---


def test_agreeing_judges_repair_the_stolen_word(analyzed) -> None:
    original = _analysis_from(analyzed)
    completion = complete_boundary_adjudication(
        original,
        ScriptedAdapter("continuation"),
    )

    assert len(completion.analysis.segments) == len(original.segments)
    for before, after in zip(
        original.segments,
        completion.analysis.segments,
        strict=True,
    ):
        assert after.segment_id == before.segment_id
        assert after.start_seconds == before.start_seconds
        assert after.end_seconds == before.end_seconds
        assert after.text == before.text
        assert after.words == before.words
    repaired = next(
        segment
        for segment in completion.analysis.segments
        if segment.text == "and"
    )
    assert repaired.speaker_label == "speaker-1"
    assert all(
        segment.review_state == "accepted"
        for segment in completion.analysis.segments
    )
    adjudication = json.loads(
        completion.analysis.boundary_adjudication_json
    )
    assert "pending" not in adjudication
    assert adjudication["dispute_count"] == 1
    assert adjudication["changed_count"] == 1
    assert adjudication["marked_count"] == 0
    record = adjudication["decision_records"][0]
    assert record["kind"] == "ownership-adjudication"
    assert record["verdict"] == "reassigned-by-agreement"
    assert adjudication["discourse_usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
    }


def test_disagreeing_judges_follow_discourse_and_mark(
    analyzed,
) -> None:
    completion = complete_boundary_adjudication(
        _analysis_from(analyzed),
        ScriptedAdapter("interjection"),
    )

    disputed = next(
        segment
        for segment in completion.analysis.segments
        if segment.text == "and"
    )
    assert disputed.speaker_label == "speaker-2"
    assert disputed.review_state == "disputed"
    adjudication = json.loads(
        completion.analysis.boundary_adjudication_json
    )
    assert adjudication["changed_count"] == 0
    assert adjudication["marked_count"] == 1
    assert adjudication["disagreement_count"] == 1
    assert adjudication["abstention_count"] == 0
    assert (
        adjudication["decision_records"][0]["verdict"]
        == "discourse-preferred"
    )


def test_v9_pending_payload_retains_v2_precedence(analyzed) -> None:
    analysis = _analysis_from(analyzed)
    payload = json.loads(analysis.boundary_adjudication_json)
    payload["policy_version"] = LEGACY_ADJUDICATION_POLICY_VERSION
    legacy = replace(
        analysis,
        pipeline_version="transpodcast-modal-pipeline-v9",
        boundary_adjudication_json=json.dumps(payload),
    )

    completion = complete_boundary_adjudication(
        legacy,
        ScriptedAdapter("interjection"),
    )

    adjudication = json.loads(
        completion.analysis.boundary_adjudication_json
    )
    assert adjudication["policy_version"] == (
        LEGACY_ADJUDICATION_POLICY_VERSION
    )
    assert adjudication["decision_records"][0]["verdict"] == (
        "voiceprint-preferred"
    )
    assert len(completion.analysis.segments) < len(legacy.segments)
    repaired = next(
        segment
        for segment in completion.analysis.segments
        if "and" in segment.text.split()
    )
    assert repaired.text == "we and continue"
    assert repaired.speaker_label == "speaker-1"


def test_voiceprint_abstention_is_counted_when_discourse_rules(analyzed) -> None:
    analysis = _analysis_from(analyzed)
    payload = json.loads(analysis.boundary_adjudication_json)
    payload["pending"]["voiceprints"] = {
        dispute_id: None
        for dispute_id in payload["pending"]["voiceprints"]
    }

    completion = complete_boundary_adjudication(
        replace(
            analysis,
            boundary_adjudication_json=json.dumps(payload),
        ),
        ScriptedAdapter("continuation"),
    )

    adjudication = json.loads(
        completion.analysis.boundary_adjudication_json
    )
    assert adjudication["abstention_count"] == 1


def test_provider_failure_keeps_ownership_and_marks(analyzed) -> None:
    completion = complete_boundary_adjudication(
        _analysis_from(analyzed),
        ScriptedAdapter(None),
    )

    stolen = next(
        segment
        for segment in completion.analysis.segments
        if segment.text == "and"
    )
    assert stolen.speaker_label == "speaker-2"
    assert stolen.review_state == "disputed"
    adjudication = json.loads(
        completion.analysis.boundary_adjudication_json
    )
    assert adjudication["changed_count"] == 0
    assert adjudication["marked_count"] == 1
    assert adjudication["abstention_count"] == 1
    assert (
        adjudication["decision_records"][0]["verdict"]
        == "kept-discourse-abstained"
    )
    assert "provider unavailable" in adjudication["discourse_error"]
    assert completion.discourse_error is not None


def test_adapter_contract_errors_stop_local_completion(analyzed) -> None:
    class BrokenAdapter:
        def adjudicate_discourse(self, disputes):
            del disputes
            raise TypeError("adapter returned an invalid internal state")

    with pytest.raises(TypeError, match="invalid internal state"):
        complete_boundary_adjudication(
            _analysis_from(analyzed),
            BrokenAdapter(),
        )


def test_explicit_unsure_is_a_healthy_marked_abstention(analyzed) -> None:
    class UnsureAdapter:
        def adjudicate_discourse(self, disputes):
            return DiscourseAdjudicationResult(
                verdicts={
                    dispute.dispute_id: DiscourseVerdict(
                        owner=None,
                        rationale="Text alone cannot identify the Speaker.",
                    )
                    for dispute in disputes
                },
                input_tokens=80,
                output_tokens=5,
            )

    completion = complete_boundary_adjudication(
        _analysis_from(analyzed),
        UnsureAdapter(),
    )

    assert completion.discourse_error is None
    adjudication = json.loads(
        completion.analysis.boundary_adjudication_json
    )
    assert adjudication["marked_count"] == 1
    record = adjudication["decision_records"][0]
    assert record["verdict"] == "kept-discourse-abstained"
    assert record["evidence"]["discourse_rationale"] == (
        "Text alone cannot identify the Speaker."
    )


def test_a_format_legal_empty_answer_is_surfaced_as_degraded(
    analyzed,
) -> None:
    class EmptyAnswerAdapter:
        def adjudicate_discourse(self, disputes):
            del disputes
            return DiscourseAdjudicationResult(
                verdicts={},
                input_tokens=80,
                output_tokens=5,
            )

    completion = complete_boundary_adjudication(
        _analysis_from(analyzed),
        EmptyAnswerAdapter(),
    )

    assert completion.discourse_error is not None
    assert "no verdicts" in completion.discourse_error
    adjudication = json.loads(
        completion.analysis.boundary_adjudication_json
    )
    assert "no verdicts" in adjudication["discourse_error"]
    assert (
        adjudication["decision_records"][0]["verdict"]
        == "kept-discourse-abstained"
    )


def test_completion_rewrites_the_attribution_ledger(analyzed) -> None:
    completion = complete_boundary_adjudication(
        _analysis_from(analyzed),
        ScriptedAdapter("continuation"),
    )

    attribution = [
        record
        for record in json.loads(
            completion.analysis.speaker_consolidation_json
        )["decision_records"]
        if record["kind"] == "segment-attribution"
    ]
    assert attribution
    by_speaker = {
        record["subject"]["speaker"]
        for record in attribution
    }
    assert by_speaker == {"speaker-1", "speaker-2"}
    repaired = next(
        record
        for record in attribution
        if record["subject"]["start_seconds"] == 110.95
    )
    assert repaired["subject"]["speaker"] == "speaker-1"
    assert repaired["evidence"]["word_count"] == 1
