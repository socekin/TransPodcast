from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from transpodcast.domain import (
    AnalyzedSpeaker,
    CanonicalSegment,
    SourceAnalysisResult,
    TranslationResult,
)
from transpodcast.evaluation import (
    CANONICAL_SEGMENTATION,
    CROSSTALK,
    CROSSTALK_SERIALIZATION,
    INTERLEAVE_SUSPECT,
    MIDSPLIT_SUSPECT,
    EpisodeInfo,
    build_report,
)


def _segment(
    segment_id: str,
    speaker: str,
    start: float,
    end: float,
    text: str,
    **overrides: Any,
) -> CanonicalSegment:
    return CanonicalSegment(
        segment_id=segment_id,
        speaker_label=speaker,
        start_seconds=start,
        end_seconds=end,
        text=text,
        **overrides,
    )


def _analysis(
    segments: list[CanonicalSegment],
    speakers: tuple[AnalyzedSpeaker, ...] = (),
) -> SourceAnalysisResult:
    return SourceAnalysisResult(
        segments=tuple(segments),
        pipeline_version="transpodcast-modal-pipeline-v6",
        diarization_model="test-diarization",
        diarization_model_revision="test-diarization-revision",
        transcription_model="test-asr",
        alignment_model="test-aligner",
        speakers=speakers,
    )


def _translation(segments: list[str]) -> TranslationResult:
    return TranslationResult(
        title="Translated title",
        overview="Translated overview",
        target_locale="zh-CN",
        segments=tuple(segments),
        provider="codex",
        model="codex-default",
        input_tokens=10,
        output_tokens=10,
    )


def _episode() -> EpisodeInfo:
    return EpisodeInfo(
        guid="ep-guid-1",
        title="Translated title",
        source_show="Useful Show",
        source_title="A source episode",
        run_id=12,
        generated_at="2026-07-30T12:00:00",
        publication_duration_seconds=228,
    )


def test_report_maps_publication_timings_onto_translated_segments() -> None:
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 8.0, "Welcome to the show."),
            _segment(
                "seg-0002",
                "speaker_0",
                8.0,
                20.0,
                "This episode is brought to you by Foo. Go to foo.com.",
                omitted_as_sponsor=True,
            ),
            _segment("seg-0003", "speaker_1", 20.0, 27.5, "Thanks for having me."),
        ]
    )
    translation = _translation(["zh-welcome", "zh-ad", "zh-thanks"])

    report = build_report(
        analysis=analysis,
        translation=translation,
        segment_timings=((0.0, 7.2), (7.2, 14.1), (14.1, 21.0)),
        episode=_episode(),
        speaker_names={},
    )

    assert [item.segment_id for item in report.segments] == [
        "seg-0001",
        "seg-0002",
        "seg-0003",
    ]
    first, sponsor, last = report.segments
    assert first.target_text == "zh-welcome"
    assert (first.publication_start, first.publication_end) == (0.0, 7.2)
    assert sponsor.omitted_as_sponsor is False
    assert sponsor.target_text == "zh-ad"
    assert (sponsor.publication_start, sponsor.publication_end) == (7.2, 14.1)
    assert last.target_text == "zh-thanks"
    assert (last.publication_start, last.publication_end) == (14.1, 21.0)
    assert report.seek_available is True


def test_report_preserves_alignment_for_legacy_sponsor_omission() -> None:
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 8.0, "Welcome."),
            _segment(
                "seg-0002",
                "speaker_0",
                8.0,
                20.0,
                "Legacy omitted advertisement.",
                omitted_as_sponsor=True,
            ),
            _segment("seg-0003", "speaker_1", 20.0, 27.5, "Thanks."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["zh-welcome", "zh-thanks"]),
        segment_timings=((0.0, 7.2), (7.2, 14.1)),
        episode=_episode(),
        speaker_names={},
    )

    first, omitted, last = report.segments
    assert first.target_text == "zh-welcome"
    assert omitted.target_text is None
    assert omitted.omitted_as_sponsor is True
    assert omitted.publication_start is None
    assert last.target_text == "zh-thanks"
    assert (last.publication_start, last.publication_end) == (7.2, 14.1)
    assert report.summary.translated_segment_count == 2
    assert report.summary.omitted_sponsor_count == 1


def test_report_distinguishes_semantic_exclusion_from_legacy_sponsor_flag() -> None:
    analysis = _analysis(
        [
            _segment(
                "seg-0001",
                "speaker_0",
                0.0,
                2.0,
                "Paid message",
                words=(("Paid", 0.0, 0.8), ("message", 0.8, 2.0)),
            ),
            _segment(
                "seg-0002",
                "speaker_1",
                2.2,
                4.0,
                "Editorial answer",
                words=(
                    ("Editorial", 2.2, 3.0),
                    ("answer", 3.0, 4.0),
                ),
            ),
        ]
    )
    translation = replace(
        _translation(["ad target", "editorial target"]),
        content_edit_json=json.dumps(
            {
                "policy_version": "semantic-content-edit-v1",
                "status": "accepted",
                "decisions": [
                    {
                        "decision_id": "paid-ad-1",
                        "category": "paid_ad",
                        "confidence": 0.98,
                        "start_segment_id": "seg-0001",
                        "end_segment_id": "seg-0001",
                        "start_anchor": "Paid",
                        "end_anchor": "message",
                        "rationale": "Separate third-party advertisement.",
                        "evidence_segment_ids": ["seg-0001"],
                        "retained_start_target": "",
                        "retained_end_target": "",
                    }
                ],
                "retention_notes": [],
            }
        ),
    )

    report = build_report(
        analysis=analysis,
        translation=translation,
        segment_timings=((0.0, 1.6),),
        episode=_episode(),
        speaker_names={},
    )

    removed, retained = report.segments
    assert removed.omitted_as_sponsor is False
    assert removed.content_edit_state == "excluded"
    assert removed.content_edit_category == "paid_ad"
    assert removed.content_edit_evidence_segment_ids == ("seg-0001",)
    assert removed.target_text == "ad target"
    assert removed.publication_start is None
    assert (retained.publication_start, retained.publication_end) == (0.0, 1.6)
    assert report.summary.omitted_sponsor_count == 0
    assert report.summary.semantic_exclusion_count == 1
    assert report.summary.semantic_excluded_source_seconds == 2.0


def test_short_sandwiched_turn_is_reported_as_an_interleave_suspect() -> None:
    # A long sentence of speaker_0 is broken by two words credited to
    # speaker_1, then continues. This is the attribution flicker the
    # Owner hears as a wrong-voice interruption.
    analysis = _analysis(
        [
            _segment(
                "seg-0040",
                "speaker_0",
                0.0,
                7.3,
                "Look, the thing about this economy is",
            ),
            _segment(
                "seg-0041",
                "speaker_1",
                7.3,
                7.9,
                "No way",
                overlap=True,
                words=(("No", 7.31, 7.53), ("way", 7.53, 7.88)),
            ),
            _segment(
                "seg-0042",
                "speaker_0",
                7.9,
                14.0,
                "that people keep being surprised by it.",
            ),
        ]
    )
    translation = _translation(["zh-a", "zh-b", "zh-c"])

    report = build_report(
        analysis=analysis,
        translation=translation,
        segment_timings=((0.0, 7.0), (7.0, 7.6), (7.6, 13.4)),
        episode=_episode(),
        speaker_names={},
    )

    suspects = [
        finding for finding in report.findings if finding.kind == INTERLEAVE_SUSPECT
    ]
    assert len(suspects) == 1
    suspect = suspects[0]
    assert suspect.segment_ids == ("seg-0040", "seg-0041", "seg-0042")
    assert suspect.stage == CANONICAL_SEGMENTATION
    assert suspect.neighbour_speaker == "speaker_0"


def test_a_full_sentence_between_two_turns_is_not_an_interleave_suspect() -> None:
    # Same A-B-A shape, but B is a complete contribution rather than a
    # flicker, so flagging it would be noise.
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 7.0, "What do you make of it?"),
            _segment(
                "seg-0002",
                "speaker_1",
                7.0,
                18.0,
                "I think the whole framing misses the point about wages.",
            ),
            _segment("seg-0003", "speaker_0", 18.0, 24.0, "That is fair enough."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["zh-a", "zh-b", "zh-c"]),
        segment_timings=((0.0, 6.5), (6.5, 17.0), (17.0, 23.0)),
        episode=_episode(),
        speaker_names={},
    )

    assert [
        finding for finding in report.findings if finding.kind == INTERLEAVE_SUSPECT
    ] == []


def test_speaker_change_mid_sentence_is_reported_as_a_midsplit_suspect() -> None:
    # The prior turn has no sentence-final punctuation, so the sentence
    # was cut by the speaker-change rule rather than by its own ending.
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 6.0, "and then the numbers came in"),
            _segment("seg-0002", "speaker_1", 6.0, 12.0, "Right, they did."),
            _segment("seg-0003", "speaker_1", 12.0, 18.0, "Everyone saw it."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["zh-a", "zh-b", "zh-c"]),
        segment_timings=((0.0, 5.5), (5.5, 11.0), (11.0, 17.0)),
        episode=_episode(),
        speaker_names={},
    )

    midsplits = [
        finding for finding in report.findings if finding.kind == MIDSPLIT_SUSPECT
    ]
    assert len(midsplits) == 1
    assert midsplits[0].segment_ids == ("seg-0001", "seg-0002")
    assert midsplits[0].stage == CANONICAL_SEGMENTATION


def test_crosstalk_segments_are_reported_against_the_assembly_stage() -> None:
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 9.0, "The polling surprises."),
            _segment(
                "seg-0002",
                "speaker_1",
                7.7,
                14.0,
                "Well, but the polling was wrong.",
                overlap=True,
            ),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["zh-a", "zh-b"]),
        segment_timings=((0.0, 8.4), (8.46, 14.2)),
        episode=_episode(),
        speaker_names={},
    )

    crosstalk = [finding for finding in report.findings if finding.kind == CROSSTALK]
    assert len(crosstalk) == 1
    assert crosstalk[0].segment_ids == ("seg-0002",)
    assert crosstalk[0].stage == CROSSTALK_SERIALIZATION


def test_missing_publication_timings_disable_seeking_without_losing_evidence() -> None:
    # Older Processing Runs predate durable segment timings. The report
    # must stay reviewable and say so instead of inventing timestamps.
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 6.0, "and the numbers came in"),
            _segment("seg-0002", "speaker_1", 6.0, 12.0, "Right, they did."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["zh-a", "zh-b"]),
        segment_timings=(),
        episode=_episode(),
        speaker_names={},
    )

    assert report.seek_available is False
    assert [item.publication_start for item in report.segments] == [None, None]
    assert [item.target_text for item in report.segments] == ["zh-a", "zh-b"]
    assert [finding.kind for finding in report.findings] == [MIDSPLIT_SUSPECT]


def test_registry_speaker_names_replace_diarization_labels() -> None:
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 6.0, "Welcome back."),
            _segment("seg-0002", "speaker_9", 6.0, 12.0, "Good to be here."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["zh-a", "zh-b"]),
        segment_timings=((0.0, 5.5), (5.5, 11.0)),
        episode=_episode(),
        speaker_names={"speaker_0": "Jordan"},
    )

    assert [item.speaker_name for item in report.segments] == [
        "Jordan",
        "speaker_9",
    ]
    assert report.speakers == (
        ("speaker_0", "Jordan"),
        ("speaker_9", "speaker_9"),
    )


def test_summary_counts_describe_the_reviewable_surface() -> None:
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 7.3, "the thing about this is"),
            _segment("seg-0002", "speaker_1", 7.3, 7.9, "No way", overlap=True),
            _segment("seg-0003", "speaker_0", 7.9, 14.0, "that it keeps happening."),
            _segment(
                "seg-0004",
                "speaker_0",
                14.0,
                22.0,
                "This episode is brought to you by Foo. Visit foo.com.",
                omitted_as_sponsor=True,
            ),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["zh-a", "zh-b", "zh-c", "zh-ad"]),
        segment_timings=(
            (0.0, 7.0),
            (7.0, 7.6),
            (7.6, 13.4),
            (13.4, 20.0),
        ),
        episode=_episode(),
        speaker_names={"speaker_0": "Jordan", "speaker_1": "Casey"},
    )

    summary = report.summary
    assert summary.segment_count == 4
    assert summary.translated_segment_count == 4
    assert summary.omitted_sponsor_count == 0
    assert summary.speaker_count == 2
    assert summary.crosstalk_count == 1
    assert summary.finding_counts[INTERLEAVE_SUSPECT] == 1


def test_findings_carry_stable_keys_so_verdicts_survive_regeneration() -> None:
    analysis = _analysis(
        [
            _segment("seg-0040", "speaker_0", 0.0, 7.3, "the thing about this is"),
            _segment("seg-0041", "speaker_1", 7.3, 7.9, "No way", overlap=True),
            _segment("seg-0042", "speaker_0", 7.9, 14.0, "that it keeps happening."),
        ]
    )
    arguments = {
        "analysis": analysis,
        "translation": _translation(["zh-a", "zh-b", "zh-c"]),
        "segment_timings": ((0.0, 7.0), (7.0, 7.6), (7.6, 13.4)),
        "episode": _episode(),
        "speaker_names": {},
    }

    first = build_report(**arguments)
    interleave = next(
        finding for finding in first.findings if finding.kind == INTERLEAVE_SUSPECT
    )
    assert interleave.key == "interleave_suspect:seg-0040+seg-0041+seg-0042"

    # A later regeneration with recorded verdicts re-displays them.
    second = build_report(
        **arguments,
        verdicts={interleave.key: "segmentation_error"},
    )
    replayed = next(
        finding for finding in second.findings if finding.kind == INTERLEAVE_SUSPECT
    )
    assert replayed.verdict == "segmentation_error"
    assert second.summary.reviewed_count == 1
    assert all(
        finding.verdict is None
        for finding in second.findings
        if finding.key != interleave.key
    )


def test_verdicts_only_replay_onto_the_episode_they_were_recorded_for() -> None:
    # Segment ids restart at seg-0001 in every episode, so verdicts must
    # be scoped by episode or one review leaks onto every other episode.
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 7.3, "the thing about this is"),
            _segment("seg-0002", "speaker_1", 7.3, 7.9, "No way"),
            _segment("seg-0003", "speaker_0", 7.9, 14.0, "that it keeps happening."),
        ]
    )
    arguments = {
        "analysis": analysis,
        "translation": _translation(["zh-a", "zh-b", "zh-c"]),
        "segment_timings": ((0.0, 7.0), (7.0, 7.6), (7.6, 13.4)),
        "speaker_names": {},
    }
    reviewed = build_report(episode=_episode(), **arguments)
    key = next(
        finding.key
        for finding in reviewed.findings
        if finding.kind == INTERLEAVE_SUSPECT
    )
    recorded = {"episode_guid": "ep-guid-1", "verdicts": {key: "segmentation_error"}}

    same = build_report(episode=_episode(), verdicts=recorded, **arguments)
    other = build_report(
        episode=replace(_episode(), guid="ep-guid-2"),
        verdicts=recorded,
        **arguments,
    )

    assert same.summary.reviewed_count == 1
    assert other.summary.reviewed_count == 0


def test_report_names_the_pipeline_version_and_models_behind_the_run() -> None:
    report = build_report(
        analysis=_analysis(
            [_segment("seg-0001", "speaker_0", 0.0, 8.0, "Welcome to the show.")]
        ),
        translation=_translation(["zh-welcome"]),
        segment_timings=((0.0, 7.2),),
        episode=_episode(),
        speaker_names={},
    )

    provenance = report.provenance
    assert provenance.pipeline_version == "transpodcast-modal-pipeline-v6"
    assert provenance.diarization_model == "test-diarization"
    assert provenance.diarization_model_revision == (
        "test-diarization-revision"
    )
    assert provenance.transcription_model == "test-asr"
    assert provenance.translation_model == "codex-default"


def test_missing_per_word_evidence_marks_the_report_partial() -> None:
    # Older checkpoints carry no per-word timestamps. The report must say
    # so rather than silently showing weaker evidence as if it were full.
    report = build_report(
        analysis=_analysis(
            [
                _segment("seg-0001", "speaker_0", 0.0, 7.3, "the thing about this is"),
                _segment("seg-0002", "speaker_1", 7.3, 7.9, "No way"),
                _segment(
                    "seg-0003", "speaker_0", 7.9, 14.0, "that it keeps happening."
                ),
            ]
        ),
        translation=_translation(["zh-a", "zh-b", "zh-c"]),
        segment_timings=(),
        episode=_episode(),
        speaker_names={},
    )

    assert report.partial is True
    assert "publication_timings" in report.partial_reasons
    assert "word_timestamps" in report.partial_reasons


def _unpunctuated_analysis() -> SourceAnalysisResult:
    # Production ASR word tokens carry no punctuation, so segment text
    # joined from them carries none either.
    return _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 9.0, "and then the numbers came in"),
            _segment("seg-0002", "speaker_1", 9.0, 15.0, "right they did"),
            _segment("seg-0003", "speaker_0", 15.0, 22.0, "so the framing is wrong"),
            _segment("seg-0004", "speaker_1", 22.0, 30.0, "i would put it differently"),
        ]
    )


def test_midsplit_is_withheld_when_the_transcript_has_no_sentence_punctuation() -> None:
    # Without punctuation the pipeline can never break on a sentence, so
    # every Speaker change would flag. A signal that fires on everything
    # measures nothing, so the report withholds it and says why.
    report = build_report(
        analysis=_unpunctuated_analysis(),
        translation=_translation(["a", "b", "c", "d"]),
        segment_timings=((0.0, 8.0), (8.0, 14.0), (14.0, 21.0), (21.0, 29.0)),
        episode=_episode(),
        speaker_names={"speaker_0": "A", "speaker_1": "B"},
    )

    assert [f for f in report.findings if f.kind == MIDSPLIT_SUSPECT] == []
    assert "sentence_punctuation" in report.partial_reasons


def test_midsplit_still_fires_when_punctuation_is_available() -> None:
    report = build_report(
        analysis=_analysis(
            [
                _segment("seg-0001", "speaker_0", 0.0, 6.0, "and the numbers came in"),
                _segment("seg-0002", "speaker_1", 6.0, 12.0, "Right, they did."),
                _segment("seg-0003", "speaker_0", 12.0, 18.0, "That is fair enough."),
            ]
        ),
        translation=_translation(["a", "b", "c"]),
        segment_timings=((0.0, 5.5), (5.5, 11.0), (11.0, 17.0)),
        episode=_episode(),
        speaker_names={"speaker_0": "A", "speaker_1": "B"},
    )

    assert [f.segment_ids for f in report.findings if f.kind == MIDSPLIT_SUSPECT] == [
        ("seg-0001", "seg-0002")
    ]
    assert "sentence_punctuation" not in report.partial_reasons


def test_overlapping_short_turns_outrank_clean_interjections() -> None:
    # A short turn with a clean source gap is most likely a real
    # backchannel. One that overlaps its neighbours is where attribution
    # is genuinely uncertain, so it must be reviewed first.
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 7.0, "First point here."),
            _segment("seg-0002", "speaker_1", 7.5, 8.0, "Yeah."),
            _segment("seg-0003", "speaker_0", 8.5, 15.0, "Second point here."),
            _segment("seg-0004", "speaker_0", 20.0, 27.0, "Third point here."),
            _segment("seg-0005", "speaker_1", 26.8, 27.3, "No way.", overlap=True),
            _segment("seg-0006", "speaker_0", 27.3, 34.0, "Fourth point here."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["a", "b", "c", "d", "e", "f"]),
        segment_timings=tuple((float(i), float(i) + 0.9) for i in range(6)),
        episode=_episode(),
        speaker_names={"speaker_0": "A", "speaker_1": "B"},
    )

    interleaves = [f for f in report.findings if f.kind == INTERLEAVE_SUSPECT]
    assert len(interleaves) == 2
    # Findings are ordered worst-first, so the overlapping one leads.
    assert interleaves[0].segment_ids[1] == "seg-0005"
    assert interleaves[0].severity > interleaves[1].severity


def test_boundary_jitter_does_not_count_as_genuine_overlap() -> None:
    # Diarization turn edges routinely miss by tens of milliseconds. That
    # is not someone talking over someone, so it must not promote a
    # finding to high severity.
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 7.00, "First point here."),
            _segment("seg-0002", "speaker_1", 7.00, 7.60, "Yeah."),
            # 80 ms earlier than the middle turn's end: boundary noise.
            _segment("seg-0003", "speaker_0", 7.52, 14.0, "Second point here."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["a", "b", "c"]),
        segment_timings=((0.0, 6.5), (6.5, 7.1), (7.1, 13.5)),
        episode=_episode(),
        speaker_names={"speaker_0": "A", "speaker_1": "B"},
    )

    suspect = next(f for f in report.findings if f.kind == INTERLEAVE_SUSPECT)
    assert suspect.overlapping is False
    assert suspect.severity == 1
    assert report.summary.high_severity_count == 0


def test_substantial_overlap_still_counts_as_genuine() -> None:
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 7.0, "First point here."),
            _segment("seg-0002", "speaker_1", 6.2, 7.4, "No way."),
            _segment("seg-0003", "speaker_0", 7.4, 14.0, "Second point here."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["a", "b", "c"]),
        segment_timings=((0.0, 6.5), (6.5, 7.3), (7.3, 13.5)),
        episode=_episode(),
        speaker_names={"speaker_0": "A", "speaker_1": "B"},
    )

    suspect = next(f for f in report.findings if f.kind == INTERLEAVE_SUSPECT)
    assert suspect.overlapping is True
    assert suspect.severity == 2


def test_the_pipeline_overlap_flag_alone_marks_a_finding_genuine() -> None:
    # The flag means words fell inside more than one Speaker's turn, which
    # is exactly the attribution ambiguity worth reviewing first.
    analysis = _analysis(
        [
            _segment("seg-0001", "speaker_0", 0.0, 7.0, "First point here."),
            _segment("seg-0002", "speaker_1", 7.2, 7.8, "No way.", overlap=True),
            _segment("seg-0003", "speaker_0", 8.0, 14.0, "Second point here."),
        ]
    )

    report = build_report(
        analysis=analysis,
        translation=_translation(["a", "b", "c"]),
        segment_timings=((0.0, 6.5), (6.5, 7.2), (7.2, 13.5)),
        episode=_episode(),
        speaker_names={"speaker_0": "A", "speaker_1": "B"},
    )

    suspect = next(f for f in report.findings if f.kind == INTERLEAVE_SUSPECT)
    assert suspect.overlapping is True
    assert suspect.severity == 2


def test_auto_generated_provisional_names_collapse_to_their_label() -> None:
    # The Speaker Registry names a Provisional Speaker "<label> · <episode
    # title>" so the Owner can tell candidates apart in the Channel Voice
    # Library. Rendered as a chip on every segment it is unreadable, and
    # the provisional marker already carries that meaning.
    long_title = "The Orbital Systems IPO, Fable 5, AI Capex Update & Market Check"
    report = build_report(
        analysis=_analysis(
            [_segment("seg-0001", "speaker_2", 0.0, 8.0, "Welcome back.")]
        ),
        translation=_translation(["zh"]),
        segment_timings=((0.0, 7.2),),
        episode=_episode(),
        speaker_names={"speaker_2": f"speaker_2 · {long_title}"},
    )

    segment = report.segments[0]
    assert segment.speaker_name == "speaker_2"
    assert segment.speaker_provisional is True
    assert report.speakers == (("speaker_2", "speaker_2"),)


def test_a_registry_name_the_owner_chose_is_kept_verbatim() -> None:
    report = build_report(
        analysis=_analysis(
            [_segment("seg-0001", "speaker_2", 0.0, 8.0, "Welcome back.")]
        ),
        translation=_translation(["zh"]),
        segment_timings=((0.0, 7.2),),
        episode=_episode(),
        speaker_names={"speaker_2": "Gavin Baker"},
    )

    assert report.segments[0].speaker_name == "Gavin Baker"
    assert report.segments[0].speaker_provisional is False
