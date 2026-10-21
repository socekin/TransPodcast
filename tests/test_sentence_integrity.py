from __future__ import annotations

import json

from transpodcast.domain import CanonicalSegment, SourceAnalysisResult
from transpodcast.natural_segmentation import segment_naturally
from transpodcast.ownership_release import (
    complete_sentence_integrity,
    has_sentence_integrity,
)
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V21
from transpodcast.sentence_integrity import (
    SENTENCE_INTEGRITY_RECORD_KIND,
    integrate_sentences,
)


def _timeline(spec: list[tuple[str, str]], step: float = 0.3) -> tuple[
    list[tuple[str, float, float]], list[str]
]:
    """Words at a fixed pace from ``(text, owner)`` pairs."""
    words = [
        (text, round(index * step, 3), round(index * step + step * 0.8, 3))
        for index, (text, _owner) in enumerate(spec)
    ]
    return words, [owner for _text, owner in spec]


def _transcript(spec: list[tuple[str, str]], ends: set[int]) -> str:
    return " ".join(
        text + ("." if index in ends else "") for index, (text, _) in enumerate(spec)
    )


def test_a_sentence_fragment_returns_to_the_sentence_owner() -> None:
    # A sign-off whose name and next sentence onset went to the other speaker.
    spec = [
        ("Bye", "a"),
        ("everyone", "a"),
        ("Captain", "a"),
        ("Example", "b"),
        ("Host", "b"),
        ("We", "b"),
        ("open", "a"),
        ("sourced", "a"),
        ("it", "a"),
        ("to", "a"),
        ("the", "a"),
        ("fans", "a"),
    ]
    words, owners = _timeline(spec)

    result = integrate_sentences(words, owners, _transcript(spec, {4, 11}))

    assert result.owners == ("a",) * 12
    assert [(m.text, m.from_speaker, m.to_speaker) for m in result.moves] == [
        ("Example Host", "b", "a"),
        ("We", "b", "a"),
    ]
    record = result.records()[0]
    assert record.kind == SENTENCE_INTEGRITY_RECORD_KIND
    assert record.verdict == "moved-to-sentence-owner"


def test_backchannels_keep_their_speaker() -> None:
    spec = [
        ("and", "a"),
        ("the", "a"),
        ("point", "a"),
        ("right", "b"),
        ("is", "a"),
        ("that", "a"),
        ("we", "a"),
        ("ship", "a"),
    ]
    words, owners = _timeline(spec)

    result = integrate_sentences(words, owners, _transcript(spec, {7}))

    assert result.moves == ()
    assert result.owners == tuple(owners)


def test_a_leading_backchannel_stays_while_the_onset_moves() -> None:
    # "Yeah" is the host's reply, "I" opens the guest's sentence.
    spec = [
        ("Yeah", "host"),
        ("I", "host"),
        ("think", "guest"),
        ("we", "guest"),
        ("should", "guest"),
        ("ship", "guest"),
        ("it", "guest"),
    ]
    words, owners = _timeline(spec)

    result = integrate_sentences(words, owners, "Yeah. I think we should ship it.")

    assert result.owners == ("host",) + ("guest",) * 6


def test_a_run_the_main_speaker_answers_is_kept() -> None:
    # A host question the guest answers at once: "Is that" / "yeah, yeah, ..."
    spec = [
        ("Is", "host"),
        ("that", "host"),
        ("yeah", "guest"),
        ("yeah", "guest"),
        ("shipping", "guest"),
        ("some", "guest"),
        ("code", "guest"),
        ("every", "guest"),
        ("week", "guest"),
    ]
    words, owners = _timeline(spec)

    result = integrate_sentences(
        words, owners, "Is that yeah, yeah, shipping some code every week."
    )

    assert result.moves == ()


def test_long_or_balanced_runs_are_left_alone() -> None:
    long_run = [("we", "a"), ("would", "a"), ("have", "b"), ("been", "b")]
    long_run += [("number", "b"), ("one", "b"), ("again", "a"), ("today", "a")]
    long_run += [("for", "a"), ("sure", "a"), ("now", "a")]
    words, owners = _timeline(long_run)
    assert integrate_sentences(words, owners, _transcript(long_run, {10})).moves == ()

    balanced = [("we", "a"), ("would", "a"), ("have", "b"), ("won", "b")]
    words, owners = _timeline(balanced)
    assert integrate_sentences(words, owners, _transcript(balanced, {3})).moves == ()


def test_a_stretched_word_counts_as_at_most_0_6_seconds() -> None:
    # The aligner stretched "You" over 1.28 s of crosstalk.
    words = [
        ("You", 0.0, 1.28),
        ("would", 1.3, 1.6),
        ("be", 1.6, 1.8),
        ("where", 1.8, 2.1),
        ("every", 2.1, 2.4),
        ("other", 2.4, 2.7),
        ("show", 2.7, 3.2),
        ("is", 3.2, 3.4),
    ]
    owners = ["b", "a", "a", "a", "a", "a", "a", "a"]

    result = integrate_sentences(
        words, owners, "You would be where every other show is."
    )

    assert result.owners == ("a",) * 8


def test_unconfident_punctuation_never_merges_sentences() -> None:
    spec = [(word, "a") for word in ["we", "said", "it", "would", "work"]]
    spec += [("and", "a"), ("then", "a"), ("x", "b"), ("went", "a"), ("home", "a")]
    words, owners = _timeline(spec)
    transcript = "we said it would work. something entirely different was said."

    result = integrate_sentences(words, owners, transcript)

    assert result.moves == ()
    assert result.skipped_unconfident_sentences >= 1


def _analysis(spec: list[tuple[str, str]], transcript: str) -> SourceAnalysisResult:
    words, owners = _timeline(spec, step=0.4)
    per_word = tuple(
        CanonicalSegment(
            segment_id=f"word-{index}",
            speaker_label=owner,
            start_seconds=word[1],
            end_seconds=word[2],
            text=word[0],
            words=(word,),
        )
        for index, (word, owner) in enumerate(zip(words, owners, strict=True))
    )
    natural = segment_naturally(per_word, full_asr_text=transcript)
    boundaries = {
        "speaker_ownership_continuity": {
            "natural_segmentation": natural.to_summary(),
            "decision_records": [],
        },
        "decision_records": [],
    }
    return SourceAnalysisResult(
        segments=natural.segments,
        pipeline_version=SPEECH_PIPELINE_V21,
        diarization_model="fixture",
        transcription_model="fixture",
        alignment_model="fixture",
        full_asr_text=transcript,
        boundary_adjudication_json=json.dumps(boundaries),
        speaker_consolidation_json=json.dumps(
            {"speaker_mapping": {}, "decision_records": []}
        ),
    )


def test_completion_resegments_only_around_moved_words() -> None:
    first = [
        (w, "a")
        for w in ["this", "opening", "sentence", "stays", "exactly", "as", "it", "was"]
    ]
    pause = [(w, "c") for w in ["and", "a", "third", "voice", "answers", "here"]]
    broken = [("we", "a"), ("would", "a"), ("have", "b"), ("been", "a")]
    broken += [(w, "a") for w in ["number", "one", "by", "now", "for", "sure"]]
    last = [(w, "c") for w in ["closing", "words", "from", "the", "third", "voice"]]
    spec = first + pause + broken + last
    transcript = (
        "This opening sentence stays exactly as it was. And a third voice "
        "answers here. We would have been number one by now for sure. "
        "Closing words from the third voice."
    )
    analysis = _analysis(spec, transcript)

    completed = complete_sentence_integrity(analysis)

    assert has_sentence_integrity(completed)
    assert [s.text.capitalize() for s in completed.segments] == [
        "This opening sentence stays exactly as it was.",
        "And a third voice answers here.",
        "We would have been number one by now for sure.",
        "Closing words from the third voice.",
    ]
    assert [s.segment_id for s in completed.segments] == [
        "seg-0001",
        "seg-0002",
        "seg-0003",
        "seg-0004",
    ]
    assert completed.segments[0] == analysis.segments[0]
    payload = json.loads(completed.boundary_adjudication_json)
    assert payload["sentence_integrity"]["moved_word_count"] == 1
    kinds = [record["kind"] for record in payload["decision_records"]]
    assert kinds.count("sentence-integrity") == 1
    assert kinds.count("natural-segmentation-boundary") == 3
    natural = payload["speaker_ownership_continuity"]["natural_segmentation"]
    assert natural["segment_count"] == 4
    attribution = json.loads(completed.speaker_consolidation_json)["decision_records"]
    assert [r["subject"]["speaker"] for r in attribution] == ["a", "c", "a", "c"]
    assert complete_sentence_integrity(completed) is completed


def test_completion_without_moves_only_records_the_summary() -> None:
    spec = [(w, "a") for w in ["nothing", "to", "fix", "in", "this", "sentence"]]
    analysis = _analysis(spec, "Nothing to fix in this sentence.")

    completed = complete_sentence_integrity(analysis)

    assert completed.segments == analysis.segments
    payload = json.loads(completed.boundary_adjudication_json)
    assert payload["sentence_integrity"]["moved_run_count"] == 0
