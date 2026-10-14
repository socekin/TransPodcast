"""Voice Reference fidelity: selection, full-band cut, Emotion hygiene.

Selection and Emotion Reference rules are pure functions over recorded
evidence. The cut tests use real FFmpeg on short synthetic signals, as the
assembly tests do; nothing calls Modal.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import wave
from array import array
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from transpodcast import modal_pipeline
from transpodcast.decision_records import DecisionRecord
from transpodcast.domain import (
    AnalyzedSpeaker,
    CanonicalSegment,
    SourceAnalysisResult,
    SourceArchive,
)
from transpodcast.modal_pipeline import rhythm_unit_tts_identities
from transpodcast.pipeline_versions import (
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V18,
    SPEECH_PIPELINE_V19,
    SPEECH_PIPELINE_V20,
)
from transpodcast.service import _serialize_analysis
from transpodcast.voice_references import (
    OWNED_REFERENCES_KEY,
    VOICE_REFERENCE_POLICY_VERSION,
    ContinuityWindow,
    EpisodeEvidence,
    OwnedWord,
    VoiceReferencePolicy,
    apply_emotion_references,
    apply_owned_voice_references,
    crosstalk_regions,
    cut_voice_reference,
    plan_emotion_references,
    reselect_for_content_edits,
    select_owned_references,
)

DURATION = 600.0


def _run(
    speaker: str,
    start: float,
    seconds: float,
    *,
    zero: int = 0,
) -> list[OwnedWord]:
    """Words every 0.5 s lasting 0.4 s; ``zero`` interior words collapse."""
    words = [
        OwnedWord(speaker, f"w{index}", start + index * 0.5, start + index * 0.5 + 0.4)
        for index in range(int(round(seconds / 0.5)))
    ]
    for index in range(1, zero + 1):
        word = words[index]
        words[index] = replace(word, end_seconds=word.start_seconds)
    return words


def _filler(speaker: str = "a") -> list[OwnedWord]:
    # 68 s of owned speech inside the intro edge zone: owned, never chosen.
    return _run(speaker, 0.0, 85.0)


def _evidence(
    words: list[OwnedWord],
    *,
    windows: tuple[ContinuityWindow, ...] = (),
    source_events: tuple[tuple[float, float], ...] = (),
    crosstalk: tuple[tuple[float, float], ...] = (),
) -> EpisodeEvidence:
    return EpisodeEvidence(
        words=tuple(sorted(words, key=lambda word: word.start_seconds)),
        windows=windows,
        source_events=source_events,
        crosstalk=crosstalk,
        duration_seconds=DURATION,
    )


def _window(start: float, end: float, margin: float) -> ContinuityWindow:
    return ContinuityWindow(start, end, {"a": 0.5 + margin, "b": 0.5})


@pytest.mark.parametrize(
    ("words", "exclusions", "events", "reason"),
    [
        (_run("a", 86.5, 6.0), [], (), "inside-edge-zone"),
        (_run("a", 506.0, 6.0), [], (), "inside-edge-zone"),
        (
            [*_run("a", 200.0, 6.0), OwnedWord("b", "yes", 206.1, 206.3)],
            [],
            (),
            "near-another-speaker",
        ),
        (_run("a", 200.0, 6.0, zero=2), [], (), "zero-duration-words"),
        (_run("a", 200.0, 6.0), [(206.1, 230.0)], (), "inside-content-edit"),
        (_run("a", 200.0, 6.0), [], ((202.0, 203.0),), "overlaps-source-event"),
        (_run("a", 200.0, 2.5), [], (), "shorter-than-minimum"),
    ],
    ids=[
        "intro-edge",
        "outro-edge",
        "neighbour",
        "zero-duration",
        "content-edit",
        "source-event",
        "short",
    ],
)
def test_each_rule_rejects_an_unusable_run(
    words: list[OwnedWord],
    exclusions: list[tuple[float, float]],
    events: tuple[tuple[float, float], ...],
    reason: str,
) -> None:
    evidence = _evidence([*_filler(), *words], source_events=events)

    selection = select_owned_references(evidence, ["a"], exclusions=exclusions)["a"]

    # The filler run always counts as one intro-edge rejection.
    expected = 2 if reason == "inside-edge-zone" else 1
    assert selection.candidate_count == 0
    assert selection.rejections[reason] == expected
    assert selection.fallback_reason == "no-eligible-candidate"


@pytest.mark.parametrize(
    ("words", "exclusions"),
    [
        ([*_run("a", 200.0, 6.0), OwnedWord("b", "yes", 206.3, 206.5)], []),
        (_run("a", 200.0, 6.0, zero=1), []),
        (_run("a", 200.0, 6.0), [(206.3, 230.0)]),
    ],
    ids=["neighbour-beyond-0.3s", "one-zero-word-in-twelve", "removal-beyond-0.3s"],
)
def test_runs_just_inside_the_rules_remain_candidates(
    words: list[OwnedWord],
    exclusions: list[tuple[float, float]],
) -> None:
    evidence = _evidence([*_filler(), *words])

    selection = select_owned_references(evidence, ["a"], exclusions=exclusions)["a"]

    assert selection.candidate_count == 1


def test_a_long_run_is_trimmed_to_a_word_boundary_within_eight_seconds() -> None:
    evidence = _evidence([*_filler(), *_run("a", 200.0, 12.0)])

    selection = select_owned_references(
        evidence, ["a"], policy=VoiceReferencePolicy(minimum_total_seconds=0.0)
    )["a"]

    (clip,) = selection.clips
    assert clip.start_seconds == 200.0
    assert clip.end_seconds == pytest.approx(207.9)
    assert clip.duration_seconds <= 8.0


def test_clips_are_ranked_by_margin_and_spaced_sixty_seconds_apart() -> None:
    starts = (100.0, 130.0, 200.0, 270.0, 340.0)
    margins = (0.8, 0.9, 0.7, 0.6, 0.5)
    words = [word for start in starts for word in _run("a", start, 6.0)]
    windows = tuple(
        _window(start + 1.0, start + 3.0, margin)
        for start, margin in zip(starts, margins, strict=True)
    )
    evidence = _evidence([*_filler(), *words], windows=windows)

    selection = select_owned_references(evidence, ["a"])["a"]

    # 130 s wins; 100 s is too close to it; 200 s reaches the 10 s target.
    assert [clip.start_seconds for clip in selection.clips] == [130.0, 200.0]
    assert [clip.mean_margin for clip in selection.clips] == pytest.approx(
        [0.9, 0.7]
    )
    assert 10.0 <= selection.total_seconds <= 15.0
    assert selection.fallback_reason is None


def test_selection_caps_clip_count_total_and_ranks_unmeasured_runs_last() -> None:
    starts = (100.0, 170.0, 240.0, 310.0, 380.0)
    words = [word for start in starts for word in _run("a", start, 4.0)]
    windows = tuple(
        _window(start + 0.5, start + 2.5, margin)
        for start, margin in zip(starts[1:], (0.4, 0.3, 0.2, 0.1), strict=True)
    )
    evidence = _evidence([*_filler(), *words], windows=windows)

    capped = select_owned_references(
        evidence,
        ["a"],
        policy=VoiceReferencePolicy(
            target_total_seconds=100.0, maximum_total_seconds=100.0
        ),
    )["a"]
    bounded = select_owned_references(
        evidence,
        ["a"],
        policy=VoiceReferencePolicy(
            target_total_seconds=100.0, maximum_total_seconds=8.0
        ),
    )["a"]

    # The run at 100 s has no measured window, so it ranks below all others.
    assert [clip.start_seconds for clip in capped.clips] == [170.0, 240.0, 310.0]
    assert [clip.start_seconds for clip in bounded.clips] == [170.0, 240.0]
    assert capped.clips[0].window_count == 1


def test_fallbacks_are_explicit() -> None:
    sparse = select_owned_references(_evidence(_run("a", 200.0, 30.0)), ["a"])
    no_candidate = select_owned_references(_evidence(_filler()), ["a"])
    too_little = select_owned_references(
        _evidence([*_filler(), *_run("a", 200.0, 6.0)]), ["a"]
    )
    absent = select_owned_references(_evidence(_filler()), ["ghost"])

    assert sparse["a"].fallback_reason == "insufficient-owned-speech"
    assert no_candidate["a"].fallback_reason == "no-eligible-candidate"
    assert too_little["a"].fallback_reason == "selected-total-below-minimum"
    assert absent["ghost"].fallback_reason == "insufficient-owned-speech"


def test_crosstalk_regions_join_only_different_speakers() -> None:
    regions = crosstalk_regions(
        [
            ("a", 0.0, 10.0),
            ("b", 9.5, 12.0),
            ("a", 11.0, 13.0),
            ("a", 12.5, 14.0),
            ("b", 20.0, 21.0),
        ]
    )

    assert regions == ((9.5, 10.0), (11.0, 12.0))


def _tone_source(
    path: Path,
    *,
    seconds: float,
    rate: int = 44_100,
    envelope: str = "1",
) -> Path:
    """1 kHz plus a 10 kHz tone 12 dB lower, scaled by ``envelope(t)``."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=({envelope})*(0.2*sin(2*PI*1000*t)+0.05*sin(2*PI*10000*t))"
            f":s={rate}:d={seconds}",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )
    return path


def _samples(path: Path) -> tuple[int, int, list[float]]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = array("h")
        raw.frombytes(handle.readframes(handle.getnframes()))
    return rate, channels, [value / 32768.0 for value in raw]


def _tone_power(samples: list[float], rate: int, frequency: float) -> float:
    coefficient = 2 * math.cos(2 * math.pi * frequency / rate)
    previous = before = 0.0
    for value in samples:
        previous, before = value + coefficient * previous - before, previous
    return previous * previous + before * before - coefficient * previous * before


def _integrated_loudness(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "loudnorm=print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(
        json.loads(re.findall(r"\{\s*\"input_i\"[\s\S]*?\}", completed.stderr)[-1])[
            "input_i"
        ]
    )


def test_full_band_cut_is_24k_mono_normalized_and_keeps_energy_above_8k(
    tmp_path: Path,
) -> None:
    source = _tone_source(tmp_path / "source.wav", seconds=20.0)
    destination = tmp_path / "reference.wav"

    cut_voice_reference(source, [(2.0, 6.0), (10.0, 15.0)], destination)

    rate, channels, samples = _samples(destination)
    assert (rate, channels) == (24_000, 1)
    assert len(samples) / rate == pytest.approx(4.0 + 0.25 + 5.0, abs=0.01)
    assert _integrated_loudness(destination) == pytest.approx(-20.0, abs=1.0)
    gap = samples[int(4.02 * rate) : int(4.23 * rate)]
    assert max(abs(value) for value in gap) < 1e-3
    high = _tone_power(samples, rate, 10_000.0)
    low = _tone_power(samples, rate, 1_000.0)
    # The source puts the 10 kHz tone 12 dB under the 1 kHz tone; a cut from
    # the 16 kHz analysis waveform would remove it entirely.
    assert 10 * math.log10(high / low) == pytest.approx(
        20 * math.log10(0.05 / 0.2), abs=1.5
    )


def _segments(words: list[OwnedWord]) -> tuple[CanonicalSegment, ...]:
    segments: list[CanonicalSegment] = []
    for word in sorted(words, key=lambda item: item.start_seconds):
        if segments and segments[-1].speaker_label == word.speaker:
            last = segments[-1]
            segments[-1] = replace(
                last,
                end_seconds=word.end_seconds,
                text=f"{last.text} {word.text}",
                words=(
                    *last.words,
                    (word.text, word.start_seconds, word.end_seconds),
                ),
            )
        else:
            segments.append(
                CanonicalSegment(
                    segment_id=f"seg-{len(segments) + 1:04d}",
                    speaker_label=word.speaker,
                    start_seconds=word.start_seconds,
                    end_seconds=word.end_seconds,
                    text=word.text,
                    words=((word.text, word.start_seconds, word.end_seconds),),
                )
            )
    return tuple(segments)


_SMALL_POLICY = VoiceReferencePolicy(
    edge_seconds=5.0,
    minimum_owned_speech_seconds=10.0,
    minimum_spacing_seconds=10.0,
)


def _small_analysis(*, with_prior: bool = True) -> SourceAnalysisResult:
    words = [
        *_run("a", 6.0, 6.0),
        *_run("b", 13.0, 2.0),
        *_run("a", 16.0, 6.0),
        *_run("b", 23.0, 1.0),
        *_run("a", 26.0, 6.0),
        *_run("b", 33.0, 6.0),
        *_run("a", 40.0, 2.0),
    ]
    consolidation: dict[str, Any] = {
        "decision_records": [
            {
                "kind": "reference-selection",
                "policy_version": "voice-reference-selection-v1",
                "subject": {"speaker": "a"},
                "evidence": {},
                "verdict": "selected",
                "rationale": "v1",
            }
        ],
    }
    if with_prior:
        consolidation["reference_selection"] = {
            "a": {"start_seconds": 6.0, "duration_seconds": 5.0},
            "b": {"start_seconds": 33.0, "duration_seconds": 5.0},
        }
    return SourceAnalysisResult(
        segments=_segments(words),
        pipeline_version=SPEECH_PIPELINE_V17,
        diarization_model="fixture",
        transcription_model="fixture",
        alignment_model="fixture",
        speakers=(
            AnalyzedSpeaker("a", b"RIFF-analysis-a", (1.0, 0.0), 0.98),
            AnalyzedSpeaker("b", b"RIFF-analysis-b", (0.0, 1.0), 0.98),
        ),
        raw_diarization_turns=(("a", 0.0, 50.0),),
        speaker_consolidation_json=json.dumps(consolidation),
    )


def _archive(path: Path) -> SourceArchive:
    content = path.read_bytes()
    return SourceArchive(
        content=content,
        extension="wav",
        media_type="audio/wav",
        checksum=hashlib.sha256(content).hexdigest(),
    )


def test_ownership_seam_replaces_references_and_records_decisions(
    tmp_path: Path,
) -> None:
    archive = _archive(_tone_source(tmp_path / "source.wav", seconds=50.0))
    analysis = _small_analysis()

    applied = apply_owned_voice_references(analysis, archive, policy=_SMALL_POLICY)
    again = apply_owned_voice_references(applied, archive, policy=_SMALL_POLICY)

    references = {speaker.local_label: speaker for speaker in applied.speakers}
    assert references["a"].reference_audio.startswith(b"RIFF")
    assert references["a"].reference_audio != b"RIFF-analysis-a"
    consolidation = json.loads(applied.speaker_consolidation_json)
    summary = consolidation[OWNED_REFERENCES_KEY]["speakers"]
    assert summary["a"]["status"] == "selected"
    assert summary["a"]["spans"] == [[6.0, 11.9], [16.0, 21.9]]
    assert summary["a"]["reference_sha256"] == hashlib.sha256(
        references["a"].reference_audio
    ).hexdigest()
    # Speaker b owns about 7 s: it keeps its v1 span, re-cut at full band.
    assert summary["b"] == {
        "status": "fallback",
        "fallback_reason": "insufficient-owned-speech",
        "spans": [[33.0, 38.0]],
        "reference_sha256": hashlib.sha256(
            references["b"].reference_audio
        ).hexdigest(),
    }
    records = [
        DecisionRecord.from_payload(record)
        for record in consolidation["decision_records"]
    ]
    assert [(record.kind, record.policy_version) for record in records] == [
        ("reference-selection", "voice-reference-selection-v1"),
        ("reference-selection", VOICE_REFERENCE_POLICY_VERSION),
        ("reference-selection", VOICE_REFERENCE_POLICY_VERSION),
        ("fallback", VOICE_REFERENCE_POLICY_VERSION),
    ]
    selected = records[1]
    assert selected.subject == {"speaker": "a"}
    assert selected.verdict == "selected"
    assert selected.evidence["selection_stage"] == "post-ownership"
    assert selected.evidence["source"] == "source-archive"
    assert selected.evidence["sample_rate_hz"] == 24_000
    assert selected.evidence["loudness_lufs"] == -20.0
    assert selected.evidence["join_silence_seconds"] == 0.25
    assert [clip["start_seconds"] for clip in selected.evidence["clips"]] == [
        6.0,
        16.0,
    ]
    assert records[3].evidence["fallback_reason"] == "insufficient-owned-speech"
    # Re-running the seam replaces its own records instead of duplicating.
    assert json.loads(again.speaker_consolidation_json)["decision_records"] == (
        consolidation["decision_records"]
    )


def test_ownership_seam_keeps_the_analysis_reference_when_cutting_fails() -> None:
    archive = SourceArchive(
        content=b"not audio",
        extension="m4a",
        media_type="audio/mp4",
        checksum="fixture",
    )

    applied = apply_owned_voice_references(
        _small_analysis(with_prior=False), archive, policy=_SMALL_POLICY
    )

    references = {speaker.local_label: speaker for speaker in applied.speakers}
    assert references["a"].reference_audio == b"RIFF-analysis-a"
    assert references["b"].reference_audio == b"RIFF-analysis-b"
    summary = json.loads(applied.speaker_consolidation_json)[OWNED_REFERENCES_KEY]
    assert summary["speakers"]["a"]["fallback_reason"] == "reference-cut-failed"
    assert summary["speakers"]["b"]["fallback_reason"] == (
        "insufficient-owned-speech"
    )


def test_render_plan_recheck_reselects_only_owned_references_touching_removals(
    tmp_path: Path,
) -> None:
    # A time-varying envelope makes different spans produce different bytes.
    source = _tone_source(
        tmp_path / "source.wav", seconds=50.0, envelope="0.5+t/100"
    )
    applied = apply_owned_voice_references(
        _small_analysis(), _archive(source), policy=_SMALL_POLICY
    )
    payload = json.loads(_serialize_analysis(applied))
    owned = {
        speaker.local_label: speaker.reference_audio for speaker in applied.speakers
    }

    unchanged, no_records = reselect_for_content_edits(
        payload, owned, source, [(44.0, 46.0)], tmp_path, policy=_SMALL_POLICY
    )
    reselected, records = reselect_for_content_edits(
        payload, owned, source, [(17.0, 19.0)], tmp_path, policy=_SMALL_POLICY
    )
    canonical, untouched = reselect_for_content_edits(
        payload,
        {**owned, "a": b"RIFF-approved-canonical"},
        source,
        [(17.0, 19.0)],
        tmp_path,
        policy=_SMALL_POLICY,
    )

    assert unchanged == owned and no_records == []
    assert reselected["a"] != owned["a"]
    assert reselected["b"] == owned["b"]
    (record,) = records
    assert record.kind == "reference-selection"
    assert record.evidence["selection_stage"] == "render-plan"
    assert record.evidence["spans"] == [[6.0, 11.9], [26.0, 31.9]]
    assert record.evidence["content_edit_exclusions"] == [[17.0, 19.0]]
    assert canonical["a"] == b"RIFF-approved-canonical" and untouched == []


def _unit(
    unit_id: str,
    speaker: str,
    start: float,
    end: float,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "segment_id": unit_id,
        "speaker_id": speaker,
        "source_start_seconds": start,
        "source_end_seconds": end,
        **extra,
    }


def test_emotion_references_replace_only_contaminated_spans() -> None:
    evidence = _evidence(
        [
            *_run("a", 100.0, 10.0),
            *_run("a", 130.0, 3.0),
            OwnedWord("b", "yes", 131.0, 131.3),
            *_run("a", 139.5, 1.5),
            *_run("a", 149.5, 1.5),
            *_run("a", 159.5, 2.5),
            *_run("a", 170.0, 5.0),
            *_run("a", 200.0, 0.5),
            OwnedWord("b", "mm", 200.6, 200.7),
            *_run("a", 300.0, 3.0),
            OwnedWord("b", "no", 301.0, 301.2),
        ],
        crosstalk=((140.0, 140.5), (150.0, 150.05)),
        source_events=((160.0, 161.0),),
    )
    units = [
        _unit("clean", "a", 100.0, 109.9),
        _unit("words", "a", 130.0, 132.9),
        _unit("crosstalk", "a", 139.5, 140.9),
        _unit("jitter", "a", 149.5, 150.9),
        _unit("event", "a", 159.5, 161.9),
        _unit("short", "a", 200.0, 200.4),
        _unit("far", "a", 300.0, 302.9),
        _unit("override", "a", 130.0, 132.9, emotion_reference_audio=b"owner"),
    ]

    choices = {
        choice.unit_id: choice for choice in plan_emotion_references(units, evidence)
    }

    assert choices["clean"].source == "own-span"
    assert choices["jitter"].source == "own-span"
    assert choices["override"].source == "owner-override"
    assert choices["words"].reasons == ("another-speaker-words",)
    assert choices["crosstalk"].reasons == ("crosstalk",)
    assert choices["event"].reasons == ("source-event",)
    # A 0.4 s unit is widened to the renderer's 1 s window first.
    assert choices["short"].reasons == ("another-speaker-words",)
    # The nearest clean same-Speaker chunk, never longer than 8 s.
    assert (choices["words"].start_seconds, choices["words"].end_seconds) == (
        108.0,
        pytest.approx(109.9),
    )
    assert choices["short"].source == "nearby-clean-span"
    assert choices["short"].start_seconds == 170.0
    assert choices["far"].source == "voice-reference"
    for choice in choices.values():
        if choice.source == "nearby-clean-span":
            assert choice.start_seconds is not None
            assert choice.end_seconds is not None
            assert 1.5 <= choice.end_seconds - choice.start_seconds <= 8.0


def _wav(path: Path, seconds: float, rate: int = 24_000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            array(
                "h",
                (
                    int(8000 * math.sin(2 * math.pi * 220 * index / rate))
                    for index in range(int(seconds * rate))
                ),
            ).tobytes()
        )
    return path


def _render_analysis() -> dict[str, Any]:
    words = [*_run("speaker-01", 0.0, 6.0), *_run("speaker-02", 7.0, 2.0)]
    words += _run("speaker-01", 10.0, 4.0)
    segments = _segments(words)
    return {
        "pipeline_version": SPEECH_PIPELINE_V17,
        "segments": [
            {
                "segment_id": segment.segment_id,
                "speaker_label": segment.speaker_label,
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "words": [list(word) for word in segment.words],
            }
            for segment in segments
        ],
        "raw_diarization_turns": [
            {"speaker": "speaker-01", "start_seconds": 10.0, "end_seconds": 14.0},
            {"speaker": "speaker-02", "start_seconds": 11.0, "end_seconds": 11.6},
        ],
        "source_events": [],
    }


def test_attached_emotion_audio_is_a_slice_or_the_voice_reference(
    tmp_path: Path,
) -> None:
    source_wav = _wav(tmp_path / "source-24k.wav", 20.0)
    analysis = _render_analysis()
    analysis["raw_diarization_turns"] += [
        {"speaker": "speaker-01", "start_seconds": 0.0, "end_seconds": 6.0},
        {"speaker": "speaker-02", "start_seconds": 1.0, "end_seconds": 1.5},
    ]
    plan = [
        _unit("seg-0001", "speaker-01", 0.0, 5.9),
        _unit("seg-0002", "speaker-02", 7.0, 8.9),
        _unit("seg-0003", "speaker-01", 10.0, 13.9),
    ]

    report = apply_emotion_references(
        plan, analysis, {"speaker-01": b"RIFF-voice"}, source_wav
    )

    assert "emotion_reference_audio" not in plan[1]
    # Every same-Speaker chunk now touches Crosstalk: the Voice Reference.
    assert plan[0]["emotion_reference_audio"] == b"RIFF-voice"
    assert plan[2]["emotion_reference_audio"] == b"RIFF-voice"
    assert report["voice_reference_count"] == 2
    assert report["own_span_count"] == 1

    clean = _render_analysis()
    plan = [_unit("seg-0003", "speaker-01", 10.0, 13.9)]
    report = apply_emotion_references(plan, clean, {}, source_wav)
    attached = tmp_path / "attached.wav"
    attached.write_bytes(plan[0]["emotion_reference_audio"])
    rate, channels, samples = _samples(attached)
    assert (rate, channels) == (24_000, 1)
    assert len(samples) / rate == pytest.approx(5.9, abs=1e-3)
    assert report["replacements"] == [
        {
            "render_unit_id": "seg-0003",
            "speaker_id": "speaker-01",
            "source": "nearby-clean-span",
            "reasons": ["crosstalk"],
            "start_seconds": 0.0,
            "end_seconds": 5.9,
        }
    ]


class _Captured(Exception):
    pass


def _captured_render_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_version: str,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    source = _wav(tmp_path / "source.wav", 20.0)
    references = {}
    for label in ("speaker-01", "speaker-02"):
        path = tmp_path / f"{label}.wav"
        path.write_bytes(label.encode())
        references[label] = path
    analysis = _render_analysis()
    analysis["speakers"] = [
        {"local_label": label, "reference_artifact_key": str(path)}
        for label, path in references.items()
    ]
    captured: dict[str, Any] = {}

    def capture(
        _source_wav: Path,
        _checksum: str,
        speaker_references: dict[str, bytes],
        plan: list[dict[str, Any]],
        _rendered_root: Path,
        **_keywords: Any,
    ) -> dict[str, Any]:
        captured["plan"] = plan
        captured["references"] = speaker_references
        raise _Captured

    monkeypatch.setattr(modal_pipeline, "_render_sharded", capture)
    with pytest.raises(_Captured):
        modal_pipeline.render(
            {
                "source": {
                    "audio_path": str(source),
                    "checksum": "fixture-source",
                    "analysis": analysis,
                },
                "translation": {"segments": ["第一句。", "回答。", "第三句。"]},
                "rendering": {"pipeline_version": pipeline_version},
            },
            tmp_path / "output",
        )
    return captured["plan"], captured["references"]


@pytest.mark.parametrize("pipeline_version", [SPEECH_PIPELINE_V19, SPEECH_PIPELINE_V20])
def test_rhythm_render_attaches_emotion_audio_that_shapes_the_cache_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_version: str,
) -> None:
    plan, references = _captured_render_plan(tmp_path, monkeypatch, pipeline_version)

    assert [bool(item.get("emotion_reference_audio")) for item in plan] == [
        False,
        False,
        True,
    ]
    identity = {
        "source_checksum": "fixture-source",
        "pipeline_version": pipeline_version,
        "renderer_class": "IndexTTS2BatchRenderer",
        "emotion_alpha": 0.8,
    }
    with_emotion = rhythm_unit_tts_identities(plan, references, **identity)
    without = rhythm_unit_tts_identities(
        [
            {k: v for k, v in item.items() if k != "emotion_reference_audio"}
            for item in plan
        ],
        references,
        **identity,
    )
    assert with_emotion["seg-0001"] == without["seg-0001"]
    assert with_emotion["seg-0003"] != without["seg-0003"]


@pytest.mark.parametrize("pipeline_version", [SPEECH_PIPELINE_V17, SPEECH_PIPELINE_V18])
def test_v17_and_v18_render_plans_keep_their_own_emotion_spans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_version: str,
) -> None:
    plan, references = _captured_render_plan(tmp_path, monkeypatch, pipeline_version)

    assert not any("emotion_reference_audio" in item for item in plan)
    assert references == {
        "speaker-01": b"speaker-01",
        "speaker-02": b"speaker-02",
    }


def test_unreadable_emotion_source_degrades_to_own_spans(tmp_path: Path) -> None:
    broken = tmp_path / "source-24k.wav"
    broken.write_bytes(b"not a wave file")
    plan = [
        _unit("seg-0001", "speaker-01", 0.0, 5.9),
        _unit("seg-0003", "speaker-01", 10.0, 13.9),
    ]

    references, settings = modal_pipeline._apply_voice_fidelity(
        _render_analysis(),
        plan,
        {"speaker-01": b"RIFF-voice"},
        source_path=broken,
        source_wav=broken,
        work_root=tmp_path,
    )

    assert references == {"speaker-01": b"RIFF-voice"}
    assert not any("emotion_reference_audio" in item for item in plan)
    assert settings["emotion_references"]["error"].startswith("ValueError")
    assert settings["voice_reference_records"] == []
