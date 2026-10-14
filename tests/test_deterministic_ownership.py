from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from transpodcast.deterministic_ownership import (
    DETERMINISTIC_OWNERSHIP_POLICY,
    DETERMINISTIC_OWNERSHIP_POLICY_V1,
    DETERMINISTIC_OWNERSHIP_POLICY_VERSION,
    UNKNOWN_SPEAKER,
    DeterministicOwnershipPolicy,
    SpeakerTurn,
    TimedWord,
    apply_deterministic_ownership,
    assign_word_owners,
    retime_zero_duration_words,
    shift_speaker_change_onsets,
)
from transpodcast.modal_pipeline import (
    SOURCE_ANALYSIS_VERSIONS,
    align_transcript,
    align_transcript_deterministically,
)
from transpodcast.pipeline_versions import (
    CONTINUITY_PIPELINES,
    CURRENT_SPEECH_PIPELINE_VERSION,
    DETERMINISTIC_OWNERSHIP_PIPELINES,
    INDEXTTS25_PIPELINES,
    RHYTHM_PIPELINES,
    SPEECH_PIPELINE_V15,
    SPEECH_PIPELINE_V16,
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V18,
    SPEECH_PIPELINE_V19,
    SPEECH_PIPELINE_V20,
    SPEECH_PIPELINE_V21,
    SPEECH_PIPELINE_V22,
    WORD_OWNERSHIP_PIPELINES,
    analysis_version_for_pipeline,
    pipeline_version_for_speech_model,
    speech_model_for_pipeline_version,
)

NO_BIAS = DeterministicOwnershipPolicy(onset_bias_seconds=0.0)


def _words(*items: tuple[str, float, float]) -> list[TimedWord]:
    return [TimedWord(text, start, end) for text, start, end in items]


def _spans(words: tuple[TimedWord, ...]) -> list[tuple[float, float]]:
    return [
        (round(word.start_seconds, 6), round(word.end_seconds, 6)) for word in words
    ]


# Zero-duration retiming


def test_policy_is_versioned_with_the_gold_set_calibration() -> None:
    policy = DETERMINISTIC_OWNERSHIP_POLICY
    assert policy.version == DETERMINISTIC_OWNERSHIP_POLICY_VERSION
    assert policy.version == "deterministic-ownership-v2"
    assert policy.onset_bias_seconds == 0.20
    assert policy.short_turn_guard_seconds == 0.40
    assert DETERMINISTIC_OWNERSHIP_POLICY_V1.version == "deterministic-ownership-v1"
    assert DETERMINISTIC_OWNERSHIP_POLICY_V1.short_turn_guard_seconds == 0.0
    assert policy.borrow_seconds_per_word == 0.04
    assert policy.minimum_room_seconds_per_word == 0.06
    assert policy.to_payload()["onset_bias_seconds"] == 0.20


def test_middle_run_with_room_is_spread_by_character_length() -> None:
    words = _words(
        ("well", 0.0, 0.5),
        ("I", 0.5, 0.5),
        ("something", 0.5, 0.5),
        ("else", 0.8, 1.0),
    )

    result = retime_zero_duration_words(words)

    assert _spans(result.words) == [
        (0.0, 0.5),
        (0.5, 0.53),
        (0.53, 0.8),
        (0.8, 1.0),
    ]
    assert result.retimed_word_indexes == (1, 2)
    assert result.borrowed_word_indexes == ()


def test_middle_run_without_room_borrows_from_both_neighbours() -> None:
    words = _words(("right", 0.0, 0.5), ("so", 0.5, 0.5), ("what", 0.5, 1.0))

    result = retime_zero_duration_words(words)

    assert _spans(result.words) == [(0.0, 0.46), (0.46, 0.54), (0.54, 1.0)]
    assert result.retimed_word_indexes == (1,)
    assert result.borrowed_word_indexes == (0, 2)


def test_borrowing_leaves_each_neighbour_half_its_duration() -> None:
    words = _words(("a", 0.40, 0.46), ("b", 0.46, 0.46), ("c", 0.46, 0.50))

    result = retime_zero_duration_words(words)

    assert _spans(result.words) == [(0.40, 0.43), (0.43, 0.48), (0.48, 0.50)]


def test_run_at_the_start_borrows_only_from_the_following_word() -> None:
    words = _words(("Oh", 1.0, 1.0), ("so", 1.0, 1.0), ("then", 1.0, 1.6))

    result = retime_zero_duration_words(words)

    assert _spans(result.words) == [(1.0, 1.04), (1.04, 1.08), (1.08, 1.6)]
    assert result.retimed_word_indexes == (0, 1)
    assert result.borrowed_word_indexes == (2,)


def test_run_at_the_end_borrows_only_from_the_previous_word() -> None:
    words = _words(("done", 2.0, 2.5), ("ok", 2.5, 2.5), ("go", 2.5, 2.5))

    result = retime_zero_duration_words(words)

    assert _spans(result.words) == [(2.0, 2.42), (2.42, 2.46), (2.46, 2.5)]
    assert result.borrowed_word_indexes == (0,)


def test_a_run_without_any_timed_neighbour_keeps_the_aligner_times() -> None:
    words = _words(("a", 3.0, 3.0), ("b", 3.0, 3.0))

    result = retime_zero_duration_words(words)

    assert result.words == tuple(words)
    assert result.retimed_word_indexes == ()


# Onset bias


def test_onset_bias_moves_only_speaker_change_turns() -> None:
    turns = [
        SpeakerTurn("b", 8.2, 10.0),
        SpeakerTurn("a", 0.0, 5.0),
        SpeakerTurn("a", 5.5, 8.0),
        SpeakerTurn("a", 10.1, 12.0),
    ]

    shifted = shift_speaker_change_onsets(turns)

    assert [(t.speaker, round(t.start_seconds, 6)) for t in shifted] == [
        ("a", 0.0),
        ("a", 5.5),
        ("b", 8.0),
        ("a", 9.9),
    ]
    assert [t.end_seconds for t in shifted] == [5.0, 8.0, 10.0, 12.0]


def test_onset_bias_never_starts_before_the_episode() -> None:
    shifted = shift_speaker_change_onsets(
        [SpeakerTurn("a", 0.0, 0.1), SpeakerTurn("b", 0.1, 2.0)],
        DETERMINISTIC_OWNERSHIP_POLICY_V1,
    )

    assert shifted[1].start_seconds == 0.0


# Short-turn guard (deterministic-ownership-v2)


def test_guard_never_moves_an_onset_into_a_short_preceding_turn() -> None:
    turns = [
        SpeakerTurn("a", 0.0, 5.0),
        SpeakerTurn("b", 5.0, 5.3),
        SpeakerTurn("a", 5.3, 9.0),
    ]

    guarded = shift_speaker_change_onsets(turns)
    unguarded = shift_speaker_change_onsets(turns, DETERMINISTIC_OWNERSHIP_POLICY_V1)

    # The long first turn still lends its tail to the reply's onset bias.
    assert [round(t.start_seconds, 6) for t in guarded] == [0.0, 4.8, 5.3]
    assert [round(t.start_seconds, 6) for t in unguarded] == [0.0, 4.8, 5.1]


def test_guard_lets_the_bias_cross_silence_up_to_the_short_turn() -> None:
    turns = [
        SpeakerTurn("a", 0.0, 5.0),
        SpeakerTurn("b", 5.0, 5.3),
        SpeakerTurn("a", 5.4, 9.0),
    ]

    shifted = shift_speaker_change_onsets(turns)

    assert round(shifted[2].start_seconds, 6) == 5.3


def test_guard_ignores_a_long_preceding_turn() -> None:
    turns = [SpeakerTurn("a", 0.0, 0.45), SpeakerTurn("b", 0.45, 2.0)]

    assert round(shift_speaker_change_onsets(turns)[1].start_seconds, 6) == 0.25


def test_guard_measures_the_time_a_turn_is_heard_alone() -> None:
    # A recorded calibration case: both short turns are long in raw extent
    # (0.96 s and 0.88 s) but heard alone for only 0.32 s and 0.16 s.
    words = [
        {"text": "a", "start_seconds": 709.966, "end_seconds": 710.046},
        {"text": "budget", "start_seconds": 710.046, "end_seconds": 710.446},
        {"text": "Yeah", "start_seconds": 710.446, "end_seconds": 710.446},
        {"text": "Yeah", "start_seconds": 710.606, "end_seconds": 711.006},
        {"text": "Practical", "start_seconds": 711.166, "end_seconds": 711.406},
        {"text": "I", "start_seconds": 711.406, "end_seconds": 711.486},
        {"text": "think", "start_seconds": 711.486, "end_seconds": 711.566},
    ]
    turns = [
        {"speaker": "host", "start_seconds": 701.28, "end_seconds": 710.32},
        {"speaker": "guest", "start_seconds": 710.24, "end_seconds": 711.2},
        {"speaker": "host", "start_seconds": 710.64, "end_seconds": 711.52},
        {"speaker": "guest", "start_seconds": 711.36, "end_seconds": 735.2},
    ]

    guarded = apply_deterministic_ownership(words, turns)
    unguarded = apply_deterministic_ownership(
        words, turns, DETERMINISTIC_OWNERSHIP_POLICY_V1
    )

    owners = [word["speaker"] for word in guarded.assigned_words]
    assert owners == ["host", "guest", "guest", "guest", "host", "guest", "guest"]
    # Without the guard the guest's "Yeah" and the host's "Practical" vanish.
    assert [word["speaker"] for word in unguarded.assigned_words][3:5] == [
        "host",
        "guest",
    ]
    assert guarded.guarded_turn_count == 2
    assert guarded.evidence_payload()["guarded_turn_count"] == 2


# Tie rule


def test_exact_overlap_tie_goes_to_the_following_speaker() -> None:
    words = _words(("yes", 1.6, 1.8))
    turns = [SpeakerTurn("a", 0.0, 2.0), SpeakerTurn("b", 1.5, 3.0)]

    assignment = assign_word_owners(words, turns, NO_BIAS)

    assert assignment.owners == ("b",)
    assert assignment.tie_word_indexes == (0,)
    # V17 keeps the first, previous turn on the same tie.
    v17 = align_transcript(
        [{"text": "yes", "start_seconds": 1.6, "end_seconds": 1.8}],
        [
            {"speaker": "a", "start_seconds": 0.0, "end_seconds": 2.0},
            {"speaker": "b", "start_seconds": 1.5, "end_seconds": 3.0},
        ],
    )
    assert v17[0]["speaker_label"] == "a"


def test_largest_total_overlap_still_wins_before_the_tie_rule() -> None:
    words = _words(("word", 1.0, 1.4))
    turns = [SpeakerTurn("a", 0.0, 1.3), SpeakerTurn("b", 1.2, 3.0)]

    assert assign_word_owners(words, turns, NO_BIAS).owners == ("a",)


def test_uncovered_word_at_equal_distance_goes_to_the_following_speaker() -> None:
    words = _words(("one", 0.0, 0.5), ("gap", 0.75, 1.0), ("two", 1.25, 1.5))
    turns = [SpeakerTurn("a", 0.0, 0.5), SpeakerTurn("b", 1.25, 1.5)]

    assignment = assign_word_owners(words, turns, NO_BIAS)

    assert assignment.owners == ("a", "b", "b")
    assert assignment.nearest_word_indexes == (1,)


def test_uncovered_word_nearer_the_previous_word_stays_previous() -> None:
    words = _words(("one", 0.0, 0.5), ("gap", 0.625, 1.0), ("two", 1.25, 1.5))
    turns = [SpeakerTurn("a", 0.0, 0.5), SpeakerTurn("b", 1.25, 1.5)]

    assert assign_word_owners(words, turns, NO_BIAS).owners == ("a", "a", "b")


def test_edge_words_take_their_only_neighbour_and_no_turns_stay_unknown() -> None:
    words = _words(("lead", 0.0, 0.2), ("body", 0.5, 1.0), ("tail", 1.5, 1.7))
    turns = [SpeakerTurn("a", 0.4, 1.1)]

    assert assign_word_owners(words, turns, NO_BIAS).owners == ("a", "a", "a")
    assert assign_word_owners(words, [], NO_BIAS).owners == (UNKNOWN_SPEAKER,) * 3


def test_overlap_flag_describes_raw_turns_and_inputs_stay_unchanged() -> None:
    words = [
        {"text": "and", "start_seconds": 0.85, "end_seconds": 0.95, "score": 0.9}
    ]
    turns = [
        {"speaker": "a", "start_seconds": 0.0, "end_seconds": 1.0},
        {"speaker": "b", "start_seconds": 1.0, "end_seconds": 2.0},
    ]
    original = json.dumps([words, turns])

    ownership = apply_deterministic_ownership(words, turns)

    # The 0.2 s onset bias ties the word between both Speakers; the tie goes
    # to the following Speaker, yet no Crosstalk is invented.
    assert ownership.assigned_words[0]["speaker"] == "b"
    assert ownership.assigned_words[0]["overlap"] is False
    assert ownership.assigned_words[0]["score"] == 0.9
    assert json.dumps([words, turns]) == original


def test_evidence_lists_adjusted_words_by_aligned_word_index() -> None:
    words = [
        {"text": "right", "start_seconds": 0.0, "end_seconds": 0.5},
        {"text": "so", "start_seconds": 0.5, "end_seconds": 0.5},
        {"text": "what", "start_seconds": 0.5, "end_seconds": 1.0},
    ]
    turns = [
        {"speaker": "a", "start_seconds": 0.0, "end_seconds": 0.5},
        {"speaker": "b", "start_seconds": 0.5, "end_seconds": 1.0},
    ]

    evidence = apply_deterministic_ownership(words, turns).evidence_payload()

    assert evidence["policy_version"] == "deterministic-ownership-v2"
    assert evidence["policy"]["onset_bias_seconds"] == 0.20
    assert evidence["policy"]["short_turn_guard_seconds"] == 0.40
    assert evidence["guarded_turn_count"] == 0
    assert evidence["word_index_basis"] == "aligned_words"
    assert evidence["retimed_word_indexes"] == [1]
    assert evidence["borrowed_word_indexes"] == [0, 2]
    assert [item["word_index"] for item in evidence["adjusted_words"]] == [0, 1, 2]
    assert evidence["shifted_turn_count"] == 1
    assert evidence["unowned_word_count"] == 0


# Version gating


def test_v21_and_v22_extend_v19_and_v20_with_v21_source_analysis() -> None:
    assert SPEECH_PIPELINE_V21 == "transpodcast-modal-pipeline-v21"
    assert SPEECH_PIPELINE_V22 == "transpodcast-modal-pipeline-v22"
    assert {SPEECH_PIPELINE_V21, SPEECH_PIPELINE_V22} == (
        DETERMINISTIC_OWNERSHIP_PIPELINES
    )
    assert DETERMINISTIC_OWNERSHIP_PIPELINES <= RHYTHM_PIPELINES
    assert RHYTHM_PIPELINES <= CONTINUITY_PIPELINES <= WORD_OWNERSHIP_PIPELINES
    assert speech_model_for_pipeline_version(SPEECH_PIPELINE_V21) == "indextts2"
    assert speech_model_for_pipeline_version(SPEECH_PIPELINE_V22) == "indextts25"
    assert SPEECH_PIPELINE_V22 in INDEXTTS25_PIPELINES
    assert analysis_version_for_pipeline(SPEECH_PIPELINE_V21) == SPEECH_PIPELINE_V21
    assert analysis_version_for_pipeline(SPEECH_PIPELINE_V22) == SPEECH_PIPELINE_V21
    assert SPEECH_PIPELINE_V21 in SOURCE_ANALYSIS_VERSIONS
    for version in (
        SPEECH_PIPELINE_V17,
        SPEECH_PIPELINE_V18,
        SPEECH_PIPELINE_V19,
        SPEECH_PIPELINE_V20,
    ):
        assert analysis_version_for_pipeline(version) == SPEECH_PIPELINE_V17
    for version in (SPEECH_PIPELINE_V15, SPEECH_PIPELINE_V16):
        assert analysis_version_for_pipeline(version) == SPEECH_PIPELINE_V15


def test_r2_selects_v21_and_v22_for_new_runs() -> None:
    assert pipeline_version_for_speech_model("indextts2") == SPEECH_PIPELINE_V21
    assert pipeline_version_for_speech_model("indextts25") == SPEECH_PIPELINE_V22
    assert CURRENT_SPEECH_PIPELINE_VERSION == SPEECH_PIPELINE_V21


def _fake_analysis_models(
    monkeypatch: pytest.MonkeyPatch,
    words: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> None:
    from transpodcast import modal_pipeline

    def fake_remote(
        app_name: str, class_name: str, method_name: str, payload: Any
    ) -> dict[str, Any]:
        del class_name, method_name, payload
        if app_name == modal_pipeline.SORTFORMER_APP:
            return {"segments": turns, "model_id": "sortformer"}
        if app_name == modal_pipeline.QWEN_ASR_APP:
            return {
                "text": "We agree. So what now?",
                "words": words,
                "asr_model_id": "qwen3-asr",
                "aligner_model_id": "qwen3-forced-aligner",
            }
        return {"embeddings": {"a": [1.0, 0.0], "b": [0.0, 1.0]}}

    def fake_references(
        source_wav: Path, diarization: Any, reference_root: Path
    ) -> dict[str, Path]:
        del source_wav, diarization
        references = {}
        for label in ("a", "b"):
            path = reference_root / f"{label}.wav"
            path.write_bytes(label.encode())
            references[label] = path
        return references

    monkeypatch.setattr(
        modal_pipeline,
        "_transcode",
        lambda source, destination, **kwargs: destination.write_bytes(b"wav"),
    )
    monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
    monkeypatch.setattr(
        modal_pipeline, "_extract_speaker_references", fake_references
    )
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_reference_audio",
        lambda source_wav, destination, **kwargs: destination.write_bytes(b"clip"),
    )


@pytest.mark.parametrize(
    ("version", "owners"),
    [
        (SPEECH_PIPELINE_V17, [["a", "a"], ["a"], ["b", "b"]]),
        (SPEECH_PIPELINE_V21, [["a", "a"], ["b", "b", "b"]]),
    ],
)
def test_only_v21_source_analysis_applies_the_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    owners: list[list[str]],
) -> None:
    from transpodcast.modal_pipeline import analyze

    words = [
        {"text": "We", "start_seconds": 0.0, "end_seconds": 0.4},
        {"text": "agree.", "start_seconds": 0.4, "end_seconds": 0.9},
        {"text": "So", "start_seconds": 0.9, "end_seconds": 0.9},
        {"text": "what", "start_seconds": 0.9, "end_seconds": 1.3},
        {"text": "now?", "start_seconds": 1.3, "end_seconds": 1.7},
    ]
    turns = [
        {"speaker": "a", "start_seconds": 0.0, "end_seconds": 0.98},
        {"speaker": "b", "start_seconds": 1.0, "end_seconds": 1.8},
    ]
    _fake_analysis_models(monkeypatch, words, turns)
    source = tmp_path / "source.webm"
    source.write_bytes(b"source-audio")

    result = analyze(
        {
            "analysis": {"pipeline_version": version},
            "source": {
                "audio_path": str(source),
                "checksum": hashlib.sha256(source.read_bytes()).hexdigest(),
            },
        },
        tmp_path / "output",
    )

    assert result["pipeline_version"] == version
    assert [
        [segment["speaker_label"]] * len(segment["words"])
        for segment in result["segments"]
    ] == owners
    assert "continuity_evidence" in result["boundary_adjudication"]
    # The models' original timestamps stay available for audit.
    assert result["aligned_words"] == words
    assert result["raw_diarization_turns"] == turns
    evidence = result["boundary_adjudication"].get("deterministic_ownership")
    if version == SPEECH_PIPELINE_V17:
        assert evidence is None
        return
    assert evidence["policy_version"] == "deterministic-ownership-v2"
    assert evidence["retimed_word_indexes"] == [2]
    assert evidence["tie_word_indexes"] == [2]
    retimed = result["segments"][1]["words"][0]
    assert retimed["text"] == "So"
    assert retimed["end_seconds"] - retimed["start_seconds"] == pytest.approx(0.08)


def test_unknown_source_analysis_versions_are_rejected(tmp_path: Path) -> None:
    from transpodcast.modal_pipeline import analyze

    with pytest.raises(ValueError, match="Unsupported Source Analysis version"):
        analyze(
            {"analysis": {"pipeline_version": SPEECH_PIPELINE_V22}, "source": {}},
            tmp_path,
        )


# Released ownership completion


_SYNTHETIC_CONVERSATION = (
    ("speaker_0", "So what changed after the launch of the new plan"),
    ("speaker_1", "Well we rebuilt the onboarding flow and the whole team"),
    ("speaker_0", "Right"),
    ("speaker_1", "learned a lot from the support tickets every week"),
    ("speaker_0", "And what about pricing for the larger customers"),
    ("speaker_1", "Pricing came later because we wanted clean data first"),
    ("speaker_0", "Yeah"),
    ("speaker_1", "so we tested three plans with a small group"),
    ("speaker_0", "That makes sense to me"),
)


def _synthetic_window() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A fictional exchange with short replies and one zero-duration word."""

    words: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    cursor = 0.0
    zero_duration_assigned = False
    for speaker, sentence in _SYNTHETIC_CONVERSATION:
        start = cursor
        for token in sentence.split():
            if token == "And" and not zero_duration_assigned:
                words.append(
                    {
                        "text": token,
                        "start_seconds": round(cursor, 3),
                        "end_seconds": round(cursor, 3),
                    }
                )
                zero_duration_assigned = True
                continue
            words.append(
                {
                    "text": token,
                    "start_seconds": round(cursor, 3),
                    "end_seconds": round(cursor + 0.3, 3),
                }
            )
            cursor += 0.32
        turns.append(
            {
                "speaker": speaker,
                "start_seconds": round(start, 3),
                "end_seconds": round(cursor, 3),
            }
        )
        cursor += 0.2
    return words, turns


def test_v21_retimed_owners_complete_through_the_released_ownership_policy() -> None:
    from transpodcast.adjudication_completion import complete_boundary_adjudication
    from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
    from transpodcast.testing import FakeTranslationAdapter

    words, turns = _synthetic_window()
    speaker_mapping = {"speaker_0": "speaker_0", "speaker_1": "speaker_1"}
    segments, evidence = align_transcript_deterministically(words, turns)
    assert evidence["retimed_word_indexes"]
    canonical = tuple(
        CanonicalSegment(
            str(segment["segment_id"]),
            str(segment["speaker_label"]),
            float(segment["start_seconds"]),
            float(segment["end_seconds"]),
            str(segment["text"]),
            words=tuple(
                (str(w["text"]), float(w["start_seconds"]), float(w["end_seconds"]))
                for w in segment["words"]
            ),
            overlap=bool(segment["overlap"]),
        )
        for segment in segments
    )
    analysis = SourceAnalysisResult(
        segments=canonical,
        pipeline_version=SPEECH_PIPELINE_V21,
        diarization_model="fixture",
        transcription_model="fixture",
        alignment_model="fixture",
        raw_diarization_turns=tuple(
            (t["speaker"], t["start_seconds"], t["end_seconds"]) for t in turns
        ),
        full_asr_text=" ".join(w["text"] for w in words),
        aligned_words=tuple(
            (w["text"], w["start_seconds"], w["end_seconds"]) for w in words
        ),
        speaker_consolidation_json=json.dumps(
            {"speaker_mapping": speaker_mapping}
        ),
        boundary_adjudication_json=json.dumps(
            {"deterministic_ownership": evidence, "continuity_evidence": {}}
        ),
    )
    judge = FakeTranslationAdapter()

    result = complete_boundary_adjudication(analysis, judge)

    assert result.completed
    assert judge.planned_speaker_ownership_regions
    assert [w for s in result.analysis.segments for w in s.words] == [
        w for s in canonical for w in s.words
    ]
    # Keep plans preserve every deterministic owner.
    assert [s.speaker_label for s in result.analysis.segments for _ in s.words] == [
        s.speaker_label for s in canonical for _ in s.words
    ]
    audit = json.loads(result.analysis.boundary_adjudication_json)
    assert audit["deterministic_ownership"] == evidence
    assert audit["speaker_ownership_continuity"]["region_count"] > 0
