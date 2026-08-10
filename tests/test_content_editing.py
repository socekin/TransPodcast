from __future__ import annotations

import json

from transpodcast.content_editing import build_render_plan
from transpodcast.domain import (
    CanonicalSegment,
    SourceAnalysisResult,
    TranslationResult,
)


def _segment(
    segment_id: str,
    start: float,
    words: tuple[str, ...],
) -> CanonicalSegment:
    timed_words = tuple(
        (word, start + index, start + index + 0.8)
        for index, word in enumerate(words)
    )
    return CanonicalSegment(
        segment_id=segment_id,
        speaker_label="speaker-01",
        start_seconds=timed_words[0][1],
        end_seconds=timed_words[-1][2],
        text=" ".join(words),
        words=timed_words,
    )


def _timed_segment(
    segment_id: str,
    words: tuple[tuple[str, float, float], ...],
) -> CanonicalSegment:
    return CanonicalSegment(
        segment_id=segment_id,
        speaker_label="speaker-01",
        start_seconds=words[0][1],
        end_seconds=words[-1][2],
        text=" ".join(word for word, _, _ in words),
        words=words,
    )


def _analysis(*segments: CanonicalSegment) -> SourceAnalysisResult:
    return SourceAnalysisResult(
        segments=segments,
        pipeline_version="fixture-v9",
        diarization_model="fixture-sortformer",
        transcription_model="fixture-qwen3-asr",
        alignment_model="fixture-aligner",
    )


def _translation(
    segment_count: int,
    decisions: list[dict[str, object]],
) -> TranslationResult:
    return TranslationResult(
        title="Fixture title",
        overview="Fixture overview",
        target_locale="zh-CN",
        segments=tuple(
            f"Target fixture {index}" for index in range(segment_count)
        ),
        provider="fixture",
        model="fixture",
        input_tokens=0,
        output_tokens=0,
        content_edit_json=json.dumps(
            {
                "policy_version": "semantic-content-edit-v1",
                "status": "accepted",
                "decisions": decisions,
                "retention_notes": [],
            }
        ),
    )


def _decision(
    decision_id: str,
    segment_id: str,
    start_anchor: str,
    end_anchor: str,
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "category": "paid_ad",
        "confidence": 0.98,
        "start_segment_id": segment_id,
        "end_segment_id": segment_id,
        "start_anchor": start_anchor,
        "end_anchor": end_anchor,
        "rationale": "A separate third-party paid advertisement.",
        "evidence_segment_ids": [segment_id],
        "retained_start_target": "",
        "retained_end_target": "",
    }


def test_one_ambiguous_decision_does_not_disable_a_valid_exclusion() -> None:
    ambiguous = _segment("seg-0001", 0.0, ("ad", "copy", "ad", "copy"))
    removable = _segment("seg-0002", 4.0, ("paid", "message"))
    retained = _segment("seg-0003", 6.0, ("editorial", "answer"))
    analysis = _analysis(ambiguous, removable, retained)
    translation = _translation(
        3,
        [
            _decision("bad", "seg-0001", "ad", "copy"),
            _decision("good", "seg-0002", "paid", "message"),
        ],
    )

    plan = build_render_plan(analysis, translation)

    assert plan.status == "degraded"
    assert [item.segment_id for item in plan.analysis.segments] == [
        "seg-0001",
        "seg-0003",
    ]
    assert plan.translation.segments == (
        "Target fixture 0",
        "Target fixture 2",
    )
    assert translation.segments == (
        "Target fixture 0",
        "Target fixture 1",
        "Target fixture 2",
    )
    assert plan.diagnostics == (
        "decision_1:content_edit_anchor_is_ambiguous",
    )


def test_adjacent_paid_ad_decisions_become_one_stable_interval() -> None:
    first = _segment("seg-0001", 0.0, ("first", "advertisement"))
    second = _segment("seg-0002", 2.0, ("second", "advertisement"))
    retained = _segment("seg-0003", 4.0, ("editorial", "answer"))
    analysis = _analysis(first, second, retained)
    translation = _translation(
        3,
        [
            _decision("ad-one", "seg-0001", "first", "advertisement"),
            _decision("ad-two", "seg-0002", "second", "advertisement"),
        ],
    )

    plan = build_render_plan(analysis, translation)

    assert plan.status == "accepted"
    assert len(plan.exclusions) == 1
    assert plan.exclusions[0].decision_id == "ad-one+ad-two"
    assert [item.segment_id for item in plan.analysis.segments] == [
        "seg-0003"
    ]


def test_two_intervals_in_one_segment_fail_open_together() -> None:
    segment = _segment(
        "seg-0001",
        0.0,
        ("first", "editorial", "second", "editorial"),
    )
    first = _decision("first", "seg-0001", "first", "first")
    first["retained_end_target"] = "Retained suffix target fixture."
    second = _decision("second", "seg-0001", "second", "second")
    second["retained_start_target"] = "Retained prefix target fixture."
    second["retained_end_target"] = "Retained suffix target fixture."
    translation = _translation(1, [first, second])

    plan = build_render_plan(_analysis(segment), translation)

    assert plan.status == "degraded"
    assert plan.exclusions == ()
    assert plan.analysis.segments == (segment,)
    assert plan.translation.segments == translation.segments
    assert plan.diagnostics == (
        "decision_1:content_edit_multiple_intervals_per_segment",
        "decision_2:content_edit_multiple_intervals_per_segment",
    )


def test_overlapping_decisions_fail_open_as_one_conflict_group() -> None:
    first = _segment("seg-0001", 0.0, ("paid", "message"))
    second = _segment("seg-0002", 2.0, ("sponsor", "ending"))
    retained = _segment("seg-0003", 4.0, ("editorial", "answer"))
    first_decision = _decision(
        "first",
        "seg-0001",
        "paid",
        "sponsor",
    )
    first_decision["end_segment_id"] = "seg-0002"
    first_decision["retained_end_target"] = (
        "Retained ending target fixture."
    )
    second_decision = _decision(
        "second",
        "seg-0002",
        "sponsor",
        "ending",
    )
    translation = _translation(3, [first_decision, second_decision])

    plan = build_render_plan(
        _analysis(first, second, retained),
        translation,
    )

    assert plan.status == "degraded"
    assert plan.exclusions == ()
    assert plan.translation.segments == translation.segments
    assert plan.diagnostics == (
        "decision_1:content_edit_overlap",
        "decision_2:content_edit_overlap",
    )


def test_removing_every_spoken_segment_fails_open() -> None:
    segment = _segment("seg-0001", 0.0, ("paid", "message"))
    translation = _translation(
        1,
        [_decision("all", "seg-0001", "paid", "message")],
    )

    plan = build_render_plan(_analysis(segment), translation)

    assert plan.status == "degraded"
    assert plan.exclusions == ()
    assert plan.analysis.segments == (segment,)
    assert plan.translation.segments == translation.segments
    assert plan.diagnostics == (
        "content_edit_would_remove_all_spoken_content",
    )


def test_null_partial_target_fails_open_instead_of_rendering_none() -> None:
    segment = _segment("seg-0001", 0.0, ("paid", "editorial"))
    decision = _decision("partial", "seg-0001", "paid", "paid")
    decision["retained_end_target"] = None
    translation = _translation(1, [decision])

    plan = build_render_plan(_analysis(segment), translation)

    assert plan.exclusions == ()
    assert plan.translation.segments == translation.segments
    assert plan.diagnostics == (
        "decision_1:content_edit_retained_end_target_is_malformed",
    )


def test_zero_duration_start_anchor_does_not_create_empty_prefix() -> None:
    segment = _timed_segment(
        "seg-0001",
        (
            ("Crusoe", 10.0, 10.0),
            ("sponsors", 10.0, 10.4),
            ("this", 10.4, 10.7),
            ("Editorial", 10.7, 11.2),
        ),
    )
    decision = _decision(
        "zero-start",
        "seg-0001",
        "Crusoe",
        "this",
    )
    decision["retained_end_target"] = "Retained editorial target."

    plan = build_render_plan(_analysis(segment), _translation(1, [decision]))

    assert [item.segment_id for item in plan.analysis.segments] == [
        "seg-0001-retained-after"
    ]
    assert [item.text for item in plan.analysis.segments] == ["Editorial"]
    assert plan.translation.segments == ("Retained editorial target.",)


def test_zero_duration_end_anchor_does_not_create_empty_suffix() -> None:
    segment = _timed_segment(
        "seg-0001",
        (
            ("Editorial", 20.0, 20.4),
            ("paid", 20.4, 20.8),
            ("A", 20.8, 20.8),
        ),
    )
    decision = _decision(
        "zero-end",
        "seg-0001",
        "paid",
        "A",
    )
    decision["retained_start_target"] = "Retained editorial target."

    plan = build_render_plan(_analysis(segment), _translation(1, [decision]))

    assert [item.segment_id for item in plan.analysis.segments] == [
        "seg-0001-retained-before"
    ]
    assert [item.text for item in plan.analysis.segments] == ["Editorial"]
    assert plan.translation.segments == ("Retained editorial target.",)


def test_every_occurrence_of_a_duplicate_decision_id_fails_open() -> None:
    malformed = _decision("duplicate", "seg-0001", "paid", "message")
    malformed["confidence"] = 0.1
    valid = _decision("duplicate", "seg-0002", "sponsor", "message")
    translation = _translation(2, [malformed, valid])
    analysis = _analysis(
        _segment("seg-0001", 0.0, ("paid", "message")),
        _segment("seg-0002", 2.0, ("sponsor", "message")),
    )

    plan = build_render_plan(analysis, translation)

    assert plan.exclusions == ()
    assert plan.translation.segments == translation.segments
    assert plan.diagnostics == (
        "decision_1:content_edit_decision_id_is_duplicated",
        "decision_2:content_edit_decision_id_is_duplicated",
    )
