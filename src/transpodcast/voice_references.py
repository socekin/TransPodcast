"""Voice Reference fidelity for the Rhythm pipelines (V19 and later).

Three deterministic steps run on the TransPodcast host; none of them calls a model:

- **B1 full band.** Voice References are cut from the original Source Archive
  to 24 kHz mono instead of the 16 kHz analysis waveform. Clips are joined
  with 250 ms of silence and normalized to -20 LUFS with two-pass loudnorm.
- **B2 post-ownership selection.** After Speaker ownership is final, each
  Speaker's reference is chosen from its own word runs: no other Speaker's
  word within ±0.3 s, at most 10% zero-duration words, no Source Event,
  outside the 90 s edge zones and every Content Edit removal, and 3-8 s
  long. Candidates are ranked by the mean margin of the recorded TitaNet
  continuity-evidence windows, so no GPU work is repeated. Up to three clips
  whose starts are at least 60 s apart are taken until 10-15 s are covered.
  A Speaker with under 60 s of owned speech, no candidate, or less than 8 s
  selected keeps the existing selection, re-cut at full band.
- **B3 Emotion Reference hygiene.** A Render Plan unit keeps its own source
  span as the emotion prompt unless that span (widened to the renderer's
  1 s minimum) holds another Speaker's words, Crosstalk, or a Source Event.
  Crosstalk is at least 0.1 s where diarization turns of two consolidated
  Speakers overlap; shorter overlaps are frame jitter. The unit then
  receives explicit audio from the nearest clean span of the same Speaker
  (1.5-8 s, within ±60 s) or, failing that, the Speaker's Voice Reference.
  The bytes ride on the Render Plan item, so the remote renderer is
  unchanged and the TTS cache identity hashes them.

Content Edit removals are only known after translation review, which is
later than the ownership seam that feeds the Speaker Registry. Selection
therefore runs at the ownership seam without removals and is re-checked at
render-plan time: only when a chosen clip touches a removal is that Speaker
re-selected with the removals applied. Every choice and fallback is returned
as a Decision Record.
"""

from __future__ import annotations

import bisect
import hashlib
import io
import json
import math
import re
import subprocess
import wave
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from transpodcast.decision_records import DecisionRecord
from transpodcast.domain import SourceAnalysisResult, SourceArchive

VOICE_REFERENCE_POLICY_VERSION = "voice-reference-selection-v2"
EMOTION_REFERENCE_POLICY_VERSION = "emotion-reference-hygiene-v1"
OWNED_REFERENCES_KEY = "owned_voice_references"

_ZERO_DURATION_SECONDS = 1e-3
_OVERLAP_EPSILON_SECONDS = 1e-3


@dataclass(frozen=True)
class VoiceReferencePolicy:
    edge_seconds: float = 90.0
    neighbour_seconds: float = 0.3
    run_gap_seconds: float = 0.5
    maximum_zero_duration_share: float = 0.1
    minimum_clip_seconds: float = 3.0
    maximum_clip_seconds: float = 8.0
    target_total_seconds: float = 10.0
    maximum_total_seconds: float = 15.0
    minimum_total_seconds: float = 8.0
    maximum_clips: int = 3
    minimum_spacing_seconds: float = 60.0
    minimum_owned_speech_seconds: float = 60.0
    minimum_window_coverage: float = 0.5
    join_silence_seconds: float = 0.25
    fade_seconds: float = 0.02
    sample_rate_hz: int = 24_000
    loudness_lufs: float = -20.0
    true_peak_dbtp: float = -2.0
    loudness_range_lu: float = 20.0


@dataclass(frozen=True)
class EmotionReferencePolicy:
    # Mirrors the renderer's own-span widening (``_emotion_window``).
    minimum_window_seconds: float = 1.0
    minimum_clip_seconds: float = 1.5
    maximum_clip_seconds: float = 8.0
    search_radius_seconds: float = 60.0
    crosstalk_tolerance_seconds: float = 0.1


VOICE_REFERENCE_POLICY = VoiceReferencePolicy()
EMOTION_REFERENCE_POLICY = EmotionReferencePolicy()


@dataclass(frozen=True)
class OwnedWord:
    speaker: str
    text: str
    start_seconds: float
    end_seconds: float

    @property
    def zero_duration(self) -> bool:
        return self.end_seconds - self.start_seconds <= _ZERO_DURATION_SECONDS


@dataclass(frozen=True)
class ContinuityWindow:
    start_seconds: float
    end_seconds: float
    scores: Mapping[str, float]


@dataclass(frozen=True)
class EpisodeEvidence:
    """The final-ownership evidence that every fidelity decision reads."""

    words: tuple[OwnedWord, ...]
    windows: tuple[ContinuityWindow, ...] = ()
    source_events: tuple[tuple[float, float], ...] = ()
    crosstalk: tuple[tuple[float, float], ...] = ()
    duration_seconds: float = 0.0
    prior_selection: Mapping[str, tuple[float, float]] = field(
        default_factory=dict
    )
    _ordered: tuple[OwnedWord, ...] = field(
        init=False, repr=False, compare=False
    )
    _starts: tuple[float, ...] = field(init=False, repr=False, compare=False)
    _longest_word: float = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.words, key=lambda word: word.start_seconds))
        object.__setattr__(self, "_ordered", ordered)
        object.__setattr__(
            self, "_starts", tuple(word.start_seconds for word in ordered)
        )
        object.__setattr__(
            self,
            "_longest_word",
            max(
                (word.end_seconds - word.start_seconds for word in ordered),
                default=0.0,
            ),
        )

    @classmethod
    def from_analysis(cls, analysis: SourceAnalysisResult) -> EpisodeEvidence:
        words = tuple(
            OwnedWord(segment.speaker_label, text, float(start), float(end))
            for segment in analysis.segments
            for text, start, end in segment.words
        )
        consolidation = _json_object(analysis.speaker_consolidation_json)
        adjudication = _json_object(analysis.boundary_adjudication_json)
        turns = [
            (speaker, float(start), float(end))
            for speaker, start, end in analysis.raw_diarization_turns
        ]
        return cls._build(
            words=words,
            consolidation=consolidation,
            adjudication=adjudication,
            turns=turns,
            source_events=[
                (float(start), float(end))
                for _kind, start, end in analysis.source_events
            ],
            extra_ends=[segment.end_seconds for segment in analysis.segments]
            + [end for _text, _start, end in analysis.aligned_words],
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> EpisodeEvidence:
        """Parse a serialized analysis or a Modal analysis response."""
        words: list[OwnedWord] = []
        segment_ends: list[float] = []
        for segment in _list(payload.get("segments")):
            if not isinstance(segment, Mapping):
                continue
            speaker = str(segment.get("speaker_label", ""))
            segment_ends.append(float(segment.get("end_seconds", 0.0)))
            for word in _list(segment.get("words")):
                parsed = _parse_timed(word)
                if parsed is not None:
                    words.append(OwnedWord(speaker, *parsed))
        turns = [
            (
                str(turn.get("speaker", "")),
                float(turn.get("start_seconds", 0.0)),
                float(turn.get("end_seconds", 0.0)),
            )
            for turn in _list(payload.get("raw_diarization_turns"))
            if isinstance(turn, Mapping)
        ]
        events: list[tuple[float, float]] = []
        for event in _list(payload.get("source_events")):
            if isinstance(event, Mapping):
                events.append(
                    (
                        float(event.get("start_seconds", 0.0)),
                        float(event.get("end_seconds", 0.0)),
                    )
                )
            elif isinstance(event, (list, tuple)) and len(event) == 3:
                events.append((float(event[1]), float(event[2])))
        aligned_ends = [
            parsed[2]
            for parsed in (
                _parse_timed(word) for word in _list(payload.get("aligned_words"))
            )
            if parsed is not None
        ]
        consolidation = payload.get("speaker_consolidation")
        adjudication = payload.get("boundary_adjudication")
        return cls._build(
            words=tuple(words),
            consolidation=(
                dict(consolidation) if isinstance(consolidation, Mapping) else {}
            ),
            adjudication=(
                dict(adjudication) if isinstance(adjudication, Mapping) else {}
            ),
            turns=turns,
            source_events=events,
            extra_ends=segment_ends + aligned_ends,
        )

    @classmethod
    def _build(
        cls,
        *,
        words: tuple[OwnedWord, ...],
        consolidation: Mapping[str, Any],
        adjudication: Mapping[str, Any],
        turns: list[tuple[str, float, float]],
        source_events: list[tuple[float, float]],
        extra_ends: list[float],
    ) -> EpisodeEvidence:
        raw_mapping = consolidation.get("speaker_mapping")
        mapping = (
            {str(key): str(value) for key, value in raw_mapping.items()}
            if isinstance(raw_mapping, Mapping)
            else {}
        )
        evidence = adjudication.get("continuity_evidence")
        windows: list[ContinuityWindow] = []
        for item in (
            _list(evidence.get("measurements"))
            if isinstance(evidence, Mapping)
            else []
        ):
            # A malformed window only loses its ranking vote.
            with suppress(KeyError, TypeError, ValueError, AttributeError):
                windows.append(
                    ContinuityWindow(
                        start_seconds=float(item["start_seconds"]),
                        end_seconds=float(item["end_seconds"]),
                        scores={
                            str(label): float(score)
                            for label, score in dict(item["scores"]).items()
                        },
                    )
                )
        prior: dict[str, tuple[float, float]] = {}
        raw_prior = consolidation.get("reference_selection")
        if isinstance(raw_prior, Mapping):
            for label, span in raw_prior.items():
                with suppress(TypeError, ValueError, AttributeError):
                    start = float(span.get("start_seconds", 0.0))
                    duration = float(span.get("duration_seconds", 0.0))
                    if duration > 0:
                        prior[str(label)] = (start, start + duration)
        mapped_turns = [
            (mapping.get(speaker, speaker), start, end)
            for speaker, start, end in turns
        ]
        return cls(
            words=words,
            windows=tuple(windows),
            source_events=tuple(source_events),
            crosstalk=crosstalk_regions(mapped_turns),
            duration_seconds=max(
                [
                    *(end for _speaker, _start, end in turns),
                    *extra_ends,
                    *(word.end_seconds for word in words),
                ],
                default=0.0,
            ),
            prior_selection=prior,
        )

    def owned_speech_seconds(self, speaker: str) -> float:
        return sum(
            word.end_seconds - word.start_seconds
            for word in self.words
            if word.speaker == speaker
        )

    def other_speaker_near(
        self,
        speaker: str,
        start_seconds: float,
        end_seconds: float,
    ) -> bool:
        """Whether another Speaker's word overlaps the open interval."""
        low = bisect.bisect_left(
            self._starts, start_seconds - self._longest_word - 1.0
        )
        high = bisect.bisect_left(self._starts, end_seconds)
        return any(
            word.speaker != speaker
            and word.end_seconds > start_seconds
            and word.start_seconds < end_seconds
            for word in self._ordered[low:high]
        )


@dataclass(frozen=True)
class ReferenceClip:
    start_seconds: float
    end_seconds: float
    mean_margin: float | None
    window_count: int
    text: str

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_payload(self) -> dict[str, Any]:
        return {
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "duration_seconds": round(self.duration_seconds, 3),
            "mean_margin": (
                None if self.mean_margin is None else round(self.mean_margin, 4)
            ),
            "window_count": self.window_count,
        }


@dataclass(frozen=True)
class SpeakerReferenceSelection:
    speaker: str
    clips: tuple[ReferenceClip, ...]
    owned_speech_seconds: float
    candidate_count: int
    rejections: Mapping[str, int]
    fallback_reason: str | None = None

    @property
    def total_seconds(self) -> float:
        return sum(clip.duration_seconds for clip in self.clips)

    @property
    def spans(self) -> tuple[tuple[float, float], ...]:
        return tuple((clip.start_seconds, clip.end_seconds) for clip in self.clips)


@dataclass(frozen=True)
class ReferenceOutcome:
    """The reference one Speaker actually uses and how it was produced."""

    speaker: str
    selection: SpeakerReferenceSelection
    spans: tuple[tuple[float, float], ...]
    reference: bytes
    cut: str
    stage: str
    exclusions: tuple[tuple[float, float], ...] = ()
    error: str | None = None

    @property
    def fallback_reason(self) -> str | None:
        if self.error is not None:
            return "reference-cut-failed"
        return self.selection.fallback_reason

    def summary(self) -> dict[str, Any]:
        return {
            "status": "fallback" if self.fallback_reason else "selected",
            "fallback_reason": self.fallback_reason,
            "spans": [[round(start, 3), round(end, 3)] for start, end in self.spans],
            "reference_sha256": hashlib.sha256(self.reference).hexdigest(),
        }

    def records(self, policy: VoiceReferencePolicy) -> tuple[DecisionRecord, ...]:
        reason = self.fallback_reason
        evidence: dict[str, Any] = {
            "selection_stage": self.stage,
            "source": (
                "source-archive" if self.cut == "full-band" else "existing-reference"
            ),
            "cut": self.cut,
            "owned_speech_seconds": round(self.selection.owned_speech_seconds, 3),
            "candidate_count": self.selection.candidate_count,
            "rejected_runs": dict(sorted(self.selection.rejections.items())),
            "clips": [clip.to_payload() for clip in self.selection.clips],
            "spans": [[round(start, 3), round(end, 3)] for start, end in self.spans],
            "total_seconds": round(
                sum(end - start for start, end in self.spans), 3
            ),
            "content_edit_exclusions": [
                [round(start, 3), round(end, 3)] for start, end in self.exclusions
            ],
            "sample_rate_hz": policy.sample_rate_hz,
            "loudness_lufs": policy.loudness_lufs,
            "join_silence_seconds": policy.join_silence_seconds,
            "reference_sha256": hashlib.sha256(self.reference).hexdigest(),
            "fallback": reason is not None,
            "fallback_reason": reason,
        }
        if self.error is not None:
            evidence["error"] = self.error[:300]
        selected = DecisionRecord(
            kind="reference-selection",
            policy_version=VOICE_REFERENCE_POLICY_VERSION,
            subject={"speaker": self.speaker},
            evidence=evidence,
            verdict="selected" if reason is None else "kept-current-selection",
            rationale=(
                "Clean final-owner word runs ranked by the mean TitaNet "
                "continuity margin, cut at full band from the Source Archive."
                if reason is None
                else "The post-ownership rules found no usable reference; the "
                "existing longest-turn selection was kept."
            ),
        )
        if reason is None:
            return (selected,)
        return (
            selected,
            DecisionRecord(
                kind="fallback",
                policy_version=VOICE_REFERENCE_POLICY_VERSION,
                subject={"speaker": self.speaker},
                evidence={
                    "selection_stage": self.stage,
                    "fallback_reason": reason,
                    "owned_speech_seconds": round(
                        self.selection.owned_speech_seconds, 3
                    ),
                    "candidate_count": self.selection.candidate_count,
                    "rejected_runs": dict(sorted(self.selection.rejections.items())),
                },
                verdict="existing-selection",
                rationale=_FALLBACK_RATIONALE.get(reason, reason),
            ),
        )


_FALLBACK_RATIONALE = {
    "insufficient-owned-speech": (
        "The Speaker owns less speech than the post-ownership rules require."
    ),
    "no-eligible-candidate": (
        "No final-owner word run passed the cleanliness, edge, and length rules."
    ),
    "selected-total-below-minimum": (
        "The eligible runs added up to less reference speech than required."
    ),
    "reference-cut-failed": (
        "The full-band reference could not be cut from the Source Archive."
    ),
}


def crosstalk_regions(
    turns: Iterable[tuple[str, float, float]],
) -> tuple[tuple[float, float], ...]:
    """Intervals where diarization turns of different Speakers overlap."""
    ordered = sorted(turns, key=lambda turn: (turn[1], turn[2]))
    regions: list[tuple[float, float]] = []
    for index, (speaker, start, end) in enumerate(ordered):
        for other, other_start, other_end in ordered[index + 1 :]:
            if other_start >= end:
                break
            if other != speaker:
                regions.append((max(start, other_start), min(end, other_end)))
    merged: list[tuple[float, float]] = []
    for start, end in sorted(regions):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def select_owned_references(
    evidence: EpisodeEvidence,
    speakers: Iterable[str],
    *,
    exclusions: Sequence[tuple[float, float]] = (),
    policy: VoiceReferencePolicy = VOICE_REFERENCE_POLICY,
) -> dict[str, SpeakerReferenceSelection]:
    """Apply the B2 rules to every requested Speaker."""
    runs = _owned_runs(evidence.words, policy.run_gap_seconds)
    selections: dict[str, SpeakerReferenceSelection] = {}
    for speaker in sorted(set(speakers)):
        rejections: Counter[str] = Counter()
        pool: list[ReferenceClip] = []
        for run_speaker, words in runs:
            if run_speaker != speaker:
                continue
            reason, clip = _reference_candidate(
                speaker, words, evidence, exclusions, policy
            )
            if clip is None:
                rejections[reason or "rejected"] += 1
            else:
                pool.append(clip)
        owned = evidence.owned_speech_seconds(speaker)
        base = SpeakerReferenceSelection(
            speaker=speaker,
            clips=(),
            owned_speech_seconds=owned,
            candidate_count=len(pool),
            rejections=dict(rejections),
        )
        if owned < policy.minimum_owned_speech_seconds:
            selections[speaker] = replace(
                base, fallback_reason="insufficient-owned-speech"
            )
            continue
        if not pool:
            selections[speaker] = replace(
                base, fallback_reason="no-eligible-candidate"
            )
            continue
        chosen = _choose_clips(pool, policy)
        selection = replace(base, clips=chosen)
        if selection.total_seconds < policy.minimum_total_seconds:
            selection = replace(
                selection, fallback_reason="selected-total-below-minimum"
            )
        selections[speaker] = selection
    return selections


def cut_voice_reference(
    source: Path,
    spans: Sequence[tuple[float, float]],
    destination: Path,
    *,
    policy: VoiceReferencePolicy = VOICE_REFERENCE_POLICY,
) -> None:
    """Cut, join, and loudness-normalize full-band clips from ``source``."""
    if not spans or any(end <= start for start, end in spans):
        raise ValueError("A Voice Reference needs at least one positive span")
    rate = policy.sample_rate_hz
    mono = f"aformat=sample_fmts=flt:sample_rates={rate}:channel_layouts=mono"
    arguments = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    filters: list[str] = []
    labels: list[str] = []
    for index, (start, end) in enumerate(spans):
        duration = end - start
        fade = min(policy.fade_seconds, duration / 2)
        arguments += [
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{duration:.6f}",
            "-i",
            str(source),
        ]
        if index:
            filters.append(
                f"anullsrc=r={rate}:cl=mono,"
                f"atrim=duration={policy.join_silence_seconds:.6f},"
                f"{mono}[gap{index}]"
            )
            labels.append(f"[gap{index}]")
        filters.append(
            f"[{index}:a]aresample={rate},{mono},"
            f"afade=t=in:d={fade:.6f},"
            f"afade=t=out:st={max(0.0, duration - fade):.6f}:d={fade:.6f}"
            f"[clip{index}]"
        )
        labels.append(f"[clip{index}]")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[joined]")
    with TemporaryDirectory(prefix="transpodcast-voice-reference-") as temp:
        joined = Path(temp) / "joined.wav"
        _ffmpeg(
            [
                *arguments,
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[joined]",
                "-c:a",
                "pcm_f32le",
                str(joined),
            ]
        )
        normalize_loudness(joined, destination, policy=policy)


def normalize_loudness(
    source: Path,
    destination: Path,
    *,
    policy: VoiceReferencePolicy = VOICE_REFERENCE_POLICY,
) -> None:
    """Two-pass linear loudnorm to the reference target, 24 kHz s16 mono."""
    target = (
        f"I={policy.loudness_lufs}:TP={policy.true_peak_dbtp}:"
        f"LRA={policy.loudness_range_lu}"
    )
    measured = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(source),
            "-af",
            f"loudnorm={target}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    matches = re.findall(r"\{\s*\"input_i\"[\s\S]*?\}", measured.stderr)
    values = json.loads(matches[-1]) if matches else {}
    audio_filter = "anull"
    if _finite(values.get("input_i")) and _finite(values.get("input_tp")):
        audio_filter = (
            f"loudnorm={target}:"
            f"measured_I={values['input_i']}:"
            f"measured_TP={values['input_tp']}:"
            f"measured_LRA={values['input_lra']}:"
            f"measured_thresh={values['input_thresh']}:"
            f"offset={values['target_offset']}:"
            "linear=true"
        )
    _ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            audio_filter,
            "-ar",
            str(policy.sample_rate_hz),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )


def apply_owned_voice_references(
    analysis: SourceAnalysisResult,
    archive: SourceArchive,
    *,
    policy: VoiceReferencePolicy = VOICE_REFERENCE_POLICY,
) -> SourceAnalysisResult:
    """Replace episode-local references with B1/B2 references (ownership seam).

    Runs after Speaker ownership completion and before Speaker Registry
    matching, so a provisional Speaker's registry candidate is the new
    reference while an approved Canonical Voice Reference still wins at
    matching time. Any failure keeps the analysis reference for that Speaker.
    """
    evidence = EpisodeEvidence.from_analysis(analysis)
    selections = select_owned_references(
        evidence,
        (speaker.local_label for speaker in analysis.speakers),
        policy=policy,
    )
    outcomes: dict[str, ReferenceOutcome] = {}
    with TemporaryDirectory(prefix="transpodcast-owned-references-") as temp:
        root = Path(temp)
        source = root / f"source.{archive.extension or 'audio'}"
        source.write_bytes(archive.content)
        for index, speaker in enumerate(analysis.speakers, start=1):
            if not speaker.reference_audio:
                continue
            outcomes[speaker.local_label] = _reference_outcome(
                selections[speaker.local_label],
                evidence,
                source,
                root / f"speaker-{index:04d}.wav",
                speaker.reference_audio,
                stage="post-ownership",
                policy=policy,
            )
    consolidation = _json_object(analysis.speaker_consolidation_json)
    records = [
        record
        for record in _list(consolidation.get("decision_records"))
        if not (
            isinstance(record, Mapping)
            and record.get("policy_version") == VOICE_REFERENCE_POLICY_VERSION
        )
    ]
    for outcome in outcomes.values():
        records.extend(record.to_payload() for record in outcome.records(policy))
    consolidation["decision_records"] = records
    consolidation[OWNED_REFERENCES_KEY] = {
        "policy_version": VOICE_REFERENCE_POLICY_VERSION,
        "speakers": {
            label: outcome.summary() for label, outcome in sorted(outcomes.items())
        },
    }
    return replace(
        analysis,
        speakers=tuple(
            replace(speaker, reference_audio=outcomes[speaker.local_label].reference)
            if speaker.local_label in outcomes
            else speaker
            for speaker in analysis.speakers
        ),
        speaker_consolidation_json=json.dumps(
            consolidation,
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def reselect_for_content_edits(
    analysis: Mapping[str, Any],
    speaker_references: Mapping[str, bytes],
    source: Path,
    exclusions: Sequence[tuple[float, float]],
    work_root: Path,
    *,
    policy: VoiceReferencePolicy = VOICE_REFERENCE_POLICY,
) -> tuple[dict[str, bytes], list[DecisionRecord]]:
    """Re-select owned references whose clips touch a Content Edit removal.

    Only a reference that is byte-identical to the one chosen at the
    ownership seam is eligible, so an approved Canonical Voice Reference or
    an Owner-selected Speaker is never replaced.
    """
    references = dict(speaker_references)
    consolidation = analysis.get("speaker_consolidation")
    owned = (
        consolidation.get(OWNED_REFERENCES_KEY)
        if isinstance(consolidation, Mapping)
        else None
    )
    owned_speakers = owned.get("speakers") if isinstance(owned, Mapping) else None
    if not exclusions or not isinstance(owned_speakers, Mapping):
        return references, []
    affected: list[str] = []
    for label, summary in sorted(owned_speakers.items()):
        if not isinstance(summary, Mapping) or summary.get("status") != "selected":
            continue
        current = references.get(str(label))
        if current is None or hashlib.sha256(current).hexdigest() != summary.get(
            "reference_sha256"
        ):
            continue
        spans = [
            (float(span[0]), float(span[1]))
            for span in _list(summary.get("spans"))
            if isinstance(span, (list, tuple)) and len(span) == 2
        ]
        if any(
            _touches(start, end, exclusions, policy.neighbour_seconds)
            for start, end in spans
        ):
            affected.append(str(label))
    if not affected:
        return references, []
    evidence = EpisodeEvidence.from_payload(analysis)
    selections = select_owned_references(
        evidence, affected, exclusions=exclusions, policy=policy
    )
    records: list[DecisionRecord] = []
    for index, label in enumerate(affected, start=1):
        selection = selections[label]
        if selection.fallback_reason is not None:
            outcome = ReferenceOutcome(
                speaker=label,
                selection=selection,
                spans=(),
                reference=references[label],
                cut="ownership-seam-reference",
                stage="render-plan",
                exclusions=tuple(exclusions),
            )
        else:
            outcome = _reference_outcome(
                selection,
                evidence,
                source,
                work_root / f"content-edit-reference-{index:04d}.wav",
                references[label],
                stage="render-plan",
                policy=policy,
                exclusions=tuple(exclusions),
            )
        references[label] = outcome.reference
        records.extend(outcome.records(policy))
    return references, records


@dataclass(frozen=True)
class EmotionChoice:
    unit_id: str
    speaker: str
    source: str
    reasons: tuple[str, ...] = ()
    start_seconds: float | None = None
    end_seconds: float | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "render_unit_id": self.unit_id,
            "speaker_id": self.speaker,
            "source": self.source,
            "reasons": list(self.reasons),
        }
        if self.start_seconds is not None and self.end_seconds is not None:
            payload["start_seconds"] = round(self.start_seconds, 3)
            payload["end_seconds"] = round(self.end_seconds, 3)
        return payload


def plan_emotion_references(
    units: Sequence[Mapping[str, Any]],
    evidence: EpisodeEvidence,
    *,
    exclusions: Sequence[tuple[float, float]] = (),
    policy: EmotionReferencePolicy = EMOTION_REFERENCE_POLICY,
    reference_policy: VoiceReferencePolicy = VOICE_REFERENCE_POLICY,
) -> list[EmotionChoice]:
    """Choose each Render Plan unit's emotion prompt under the B3 rules."""
    clean = _clean_emotion_spans(evidence, exclusions, policy, reference_policy)
    choices: list[EmotionChoice] = []
    for unit in units:
        unit_id = str(unit["segment_id"])
        speaker = str(unit["speaker_id"])
        if unit.get("emotion_reference_audio"):
            choices.append(EmotionChoice(unit_id, speaker, "owner-override"))
            continue
        start = float(unit["source_start_seconds"])
        end = float(unit["source_end_seconds"])
        window_start, window_end = _emotion_window(
            start, end, evidence.duration_seconds, policy.minimum_window_seconds
        )
        reasons = _contamination(
            speaker, window_start, window_end, evidence, policy
        )
        if not reasons:
            choices.append(EmotionChoice(unit_id, speaker, "own-span"))
            continue
        nearby = [
            span
            for span in clean.get(speaker, [])
            if span[0] >= start - policy.search_radius_seconds
            and span[1] <= end + policy.search_radius_seconds
        ]
        if not nearby:
            choices.append(
                EmotionChoice(unit_id, speaker, "voice-reference", reasons)
            )
            continue
        best = min(
            nearby,
            key=lambda span: (
                max(0.0, span[0] - end, start - span[1]),
                -(span[1] - span[0]),
                span[0],
            ),
        )
        choices.append(
            EmotionChoice(
                unit_id, speaker, "nearby-clean-span", reasons, best[0], best[1]
            )
        )
    return choices


def slice_wav(source: Path, start_seconds: float, end_seconds: float) -> bytes:
    """Return a PCM WAV slice of ``source``; deterministic for one input."""
    try:
        with wave.open(str(source), "rb") as handle:
            rate = handle.getframerate()
            frames = handle.getnframes()
            first = max(0, min(frames, round(start_seconds * rate)))
            last = max(first, min(frames, round(end_seconds * rate)))
            handle.setpos(first)
            content = handle.readframes(last - first)
            channels = handle.getnchannels()
            width = handle.getsampwidth()
    except wave.Error as error:
        raise ValueError(f"Emotion Reference source is unreadable: {error}") from error
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(content)
    return buffer.getvalue()


def apply_emotion_references(
    render_plan: list[dict[str, Any]],
    analysis: Mapping[str, Any],
    speaker_references: Mapping[str, bytes],
    source_wav: Path,
    *,
    exclusions: Sequence[tuple[float, float]] = (),
    policy: EmotionReferencePolicy = EMOTION_REFERENCE_POLICY,
) -> dict[str, Any]:
    """Attach B3 emotion audio to Render Plan items in place; return a report."""
    evidence = EpisodeEvidence.from_payload(analysis)
    choices = plan_emotion_references(
        render_plan, evidence, exclusions=exclusions, policy=policy
    )
    # Cut every clip before touching the plan so a failure leaves it intact.
    attachments: dict[int, bytes] = {}
    for index, choice in enumerate(choices):
        if (
            choice.source == "nearby-clean-span"
            and choice.start_seconds is not None
            and choice.end_seconds is not None
        ):
            attachments[index] = slice_wav(
                source_wav, choice.start_seconds, choice.end_seconds
            )
        elif choice.source == "voice-reference" and speaker_references.get(
            choice.speaker
        ):
            attachments[index] = speaker_references[choice.speaker]
    for index, audio in attachments.items():
        render_plan[index]["emotion_reference_audio"] = audio
    counts = Counter(choice.source for choice in choices)
    return {
        "policy_version": EMOTION_REFERENCE_POLICY_VERSION,
        "unit_count": len(choices),
        "own_span_count": counts["own-span"],
        "nearby_clean_span_count": counts["nearby-clean-span"],
        "voice_reference_count": counts["voice-reference"],
        "owner_override_count": counts["owner-override"],
        "replacements": [
            choice.to_payload()
            for choice in choices
            if choice.source in {"nearby-clean-span", "voice-reference"}
        ],
    }


def _reference_outcome(
    selection: SpeakerReferenceSelection,
    evidence: EpisodeEvidence,
    source: Path,
    destination: Path,
    current: bytes,
    *,
    stage: str,
    policy: VoiceReferencePolicy,
    exclusions: tuple[tuple[float, float], ...] = (),
) -> ReferenceOutcome:
    spans: tuple[tuple[float, float], ...] = selection.spans
    if selection.fallback_reason is not None:
        prior = evidence.prior_selection.get(selection.speaker)
        spans = (prior,) if prior is not None else ()
    if not spans:
        return ReferenceOutcome(
            speaker=selection.speaker,
            selection=selection,
            spans=(),
            reference=current,
            cut="analysis-reference",
            stage=stage,
            exclusions=exclusions,
        )
    try:
        cut_voice_reference(source, spans, destination, policy=policy)
        reference = destination.read_bytes()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return ReferenceOutcome(
            speaker=selection.speaker,
            selection=selection,
            spans=(),
            reference=current,
            cut="analysis-reference",
            stage=stage,
            exclusions=exclusions,
            error=f"{type(error).__name__}: {error}",
        )
    return ReferenceOutcome(
        speaker=selection.speaker,
        selection=selection,
        spans=spans,
        reference=reference,
        cut="full-band",
        stage=stage,
        exclusions=exclusions,
    )


def _owned_runs(
    words: Sequence[OwnedWord],
    gap_seconds: float,
) -> list[tuple[str, list[OwnedWord]]]:
    runs: list[tuple[str, list[OwnedWord]]] = []
    for word in words:
        if (
            runs
            and runs[-1][0] == word.speaker
            and word.start_seconds - runs[-1][1][-1].end_seconds <= gap_seconds
        ):
            runs[-1][1].append(word)
        else:
            runs.append((word.speaker, [word]))
    return runs


def _reference_candidate(
    speaker: str,
    words: Sequence[OwnedWord],
    evidence: EpisodeEvidence,
    exclusions: Sequence[tuple[float, float]],
    policy: VoiceReferencePolicy,
) -> tuple[str | None, ReferenceClip | None]:
    start = words[0].start_seconds
    end = words[-1].end_seconds
    if (
        start < policy.edge_seconds
        or end > evidence.duration_seconds - policy.edge_seconds
    ):
        return "inside-edge-zone", None
    if _touches(start, end, exclusions, policy.neighbour_seconds):
        return "inside-content-edit", None
    if evidence.other_speaker_near(
        speaker,
        start - policy.neighbour_seconds,
        end + policy.neighbour_seconds,
    ):
        return "near-another-speaker", None
    zero = sum(1 for word in words if word.zero_duration)
    if zero > policy.maximum_zero_duration_share * len(words):
        return "zero-duration-words", None
    if _touches(start, end, evidence.source_events, 0.0):
        return "overlaps-source-event", None
    kept = [
        word
        for word in words
        if word.end_seconds - start <= policy.maximum_clip_seconds
    ]
    if not kept or kept[-1].end_seconds - start < policy.minimum_clip_seconds:
        return "shorter-than-minimum", None
    clip_end = kept[-1].end_seconds
    margin, count = _mean_margin(evidence.windows, speaker, start, clip_end, policy)
    return None, ReferenceClip(
        start_seconds=start,
        end_seconds=clip_end,
        mean_margin=margin,
        window_count=count,
        text=" ".join(word.text for word in kept),
    )


def _mean_margin(
    windows: Sequence[ContinuityWindow],
    speaker: str,
    start: float,
    end: float,
    policy: VoiceReferencePolicy,
) -> tuple[float | None, int]:
    margins: list[float] = []
    for window in windows:
        length = window.end_seconds - window.start_seconds
        overlap = min(end, window.end_seconds) - max(start, window.start_seconds)
        if length <= 0 or overlap / length < policy.minimum_window_coverage:
            continue
        others = [
            score for label, score in window.scores.items() if label != speaker
        ]
        if not others:
            continue
        margins.append(window.scores.get(speaker, -1.0) - max(others))
    if not margins:
        return None, 0
    return sum(margins) / len(margins), len(margins)


def _choose_clips(
    pool: Sequence[ReferenceClip],
    policy: VoiceReferencePolicy,
) -> tuple[ReferenceClip, ...]:
    ranked = sorted(
        pool,
        key=lambda clip: (
            clip.mean_margin is not None,
            clip.mean_margin or 0.0,
            clip.duration_seconds,
        ),
        reverse=True,
    )
    chosen: list[ReferenceClip] = []
    for clip in ranked:
        total = sum(item.duration_seconds for item in chosen)
        if total >= policy.target_total_seconds or len(chosen) >= policy.maximum_clips:
            break
        if total + clip.duration_seconds > policy.maximum_total_seconds:
            continue
        if any(
            abs(clip.start_seconds - item.start_seconds)
            < policy.minimum_spacing_seconds
            for item in chosen
        ):
            continue
        chosen.append(clip)
    return tuple(sorted(chosen, key=lambda clip: clip.start_seconds))


def _clean_emotion_spans(
    evidence: EpisodeEvidence,
    exclusions: Sequence[tuple[float, float]],
    policy: EmotionReferencePolicy,
    reference_policy: VoiceReferencePolicy,
) -> dict[str, list[tuple[float, float]]]:
    spans: dict[str, list[tuple[float, float]]] = {}
    for speaker, words in _owned_runs(
        evidence.words, reference_policy.run_gap_seconds
    ):
        for chunk in _chunks(words, policy.maximum_clip_seconds):
            start = chunk[0].start_seconds
            end = chunk[-1].end_seconds
            if end - start < policy.minimum_clip_seconds:
                continue
            zero = sum(1 for word in chunk if word.zero_duration)
            if zero > reference_policy.maximum_zero_duration_share * len(chunk):
                continue
            if evidence.other_speaker_near(
                speaker,
                start - reference_policy.neighbour_seconds,
                end + reference_policy.neighbour_seconds,
            ):
                continue
            if _touches(start, end, exclusions, reference_policy.neighbour_seconds):
                continue
            if _contamination(speaker, start, end, evidence, policy):
                continue
            spans.setdefault(speaker, []).append((start, end))
    return spans


def _chunks(
    words: Sequence[OwnedWord],
    maximum_seconds: float,
) -> list[list[OwnedWord]]:
    chunks: list[list[OwnedWord]] = []
    for word in words:
        if chunks and word.end_seconds - chunks[-1][0].start_seconds <= maximum_seconds:
            chunks[-1].append(word)
        else:
            chunks.append([word])
    return chunks


def _contamination(
    speaker: str,
    start: float,
    end: float,
    evidence: EpisodeEvidence,
    policy: EmotionReferencePolicy,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if evidence.other_speaker_near(
        speaker,
        start + _OVERLAP_EPSILON_SECONDS,
        end - _OVERLAP_EPSILON_SECONDS,
    ):
        reasons.append("another-speaker-words")
    crosstalk = sum(
        max(0.0, min(end, region_end) - max(start, region_start))
        for region_start, region_end in evidence.crosstalk
    )
    if crosstalk >= policy.crosstalk_tolerance_seconds:
        reasons.append("crosstalk")
    if _touches(start, end, evidence.source_events, 0.0):
        reasons.append("source-event")
    return tuple(reasons)


def _emotion_window(
    start: float,
    end: float,
    duration: float,
    minimum: float,
) -> tuple[float, float]:
    if end - start >= minimum:
        return start, end
    missing = minimum - (end - start)
    resolved_start = max(0.0, start - missing / 2)
    resolved_end = min(max(duration, end), end + missing / 2)
    if resolved_end - resolved_start < minimum:
        resolved_start = max(0.0, resolved_end - minimum)
    return resolved_start, resolved_end


def _touches(
    start: float,
    end: float,
    intervals: Iterable[tuple[float, float]],
    padding: float,
) -> bool:
    return any(
        start < interval_end + padding and end > interval_start - padding
        for interval_start, interval_end in intervals
    )


def _parse_timed(value: object) -> tuple[str, float, float] | None:
    if isinstance(value, Mapping):
        if "start_seconds" not in value or "end_seconds" not in value:
            return None
        return (
            str(value.get("text", "")),
            float(value["start_seconds"]),
            float(value["end_seconds"]),
        )
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return str(value[0]), float(value[1]), float(value[2])
    return None


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(str(value)))
    except ValueError:
        return False


def _ffmpeg(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, capture_output=True, text=True)
