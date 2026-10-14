"""Rhythm Policy for Speech Pipelines V19 and later.

The Rhythm Policy restores source pacing during assembly without touching
speech rendering. It trims the edge silence of each raw unit clip, maps the
source gap between consecutive retained Render Plan units to an output pause,
turns each Content Edit removal into a fixed transition, and bounds how much a
short reply may be stretched. Every value here is assembly-only: none of it may
reach the render cache identity.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Sequence
from dataclasses import asdict, dataclass

RHYTHM_POLICY_VERSION = "rhythm-policy-v2"


@dataclass(frozen=True)
class RhythmBoundary:
    """The source relation between two consecutive retained units."""

    source_gap_seconds: float
    speaker_change: bool
    crosses_cut: bool


@dataclass(frozen=True)
class TrimWindow:
    """The kept sample range of one clip after edge trimming."""

    start: int
    end: int
    head_silence: int
    tail_silence: int
    voiced: bool


@dataclass(frozen=True)
class RhythmPolicy:
    version: str = RHYTHM_POLICY_VERSION
    head_keep_seconds: float = 0.04
    tail_keep_seconds: float = 0.06
    same_speaker_min_seconds: float = 0.15
    same_speaker_max_seconds: float = 1.0
    speaker_change_min_seconds: float = 0.25
    speaker_change_max_seconds: float = 1.2
    overlap_change_seconds: float = 0.08
    large_gap_seconds: float = 3.0
    cut_pause_seconds: float = 0.8
    cut_fade_seconds: float = 0.03
    edge_fade_seconds: float = 0.01
    short_reply_source_seconds: float = 1.5
    short_reply_ratio: float = 1.4
    maximum_tempo: float = 1.25
    minimum_tempo: float = 1.02
    silence_dbfs: float = -45.0
    frame_seconds: float = 0.01
    # Listening review judged 1.8-2.2 s silences inside rendered sentences
    # as defects; natural TTS pauses stay below this.
    internal_silence_cap_seconds: float = 0.8
    internal_cut_fade_seconds: float = 0.005

    def pause_seconds(self, boundary: RhythmBoundary) -> float:
        """Return the total output pause for one boundary."""
        gap = boundary.source_gap_seconds
        if boundary.crosses_cut:
            return self.cut_pause_seconds
        if boundary.speaker_change:
            if gap < 0:
                return self.overlap_change_seconds
            minimum = self.speaker_change_min_seconds
            maximum = self.speaker_change_max_seconds
        else:
            minimum = self.same_speaker_min_seconds
            maximum = self.same_speaker_max_seconds
        if gap > self.large_gap_seconds:
            return maximum
        return min(max(gap, minimum), maximum)

    def tempo_factor(
        self,
        source_seconds: float,
        rendered_seconds: float,
    ) -> float:
        """Return the bounded speed-up for a stretched short reply.

        ``1.0`` means the clip keeps its rendered tempo. Speech is never
        slowed down.
        """
        if not 0 < source_seconds < self.short_reply_source_seconds:
            return 1.0
        ratio = rendered_seconds / source_seconds
        if ratio <= self.short_reply_ratio:
            return 1.0
        factor = min(self.maximum_tempo, ratio / self.short_reply_ratio)
        return factor if factor > self.minimum_tempo else 1.0

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


RHYTHM_POLICY = RhythmPolicy()


def crosses_cut(
    previous_end_seconds: float,
    next_start_seconds: float,
    exclusions: Sequence[tuple[float, float]],
) -> bool:
    """Return whether a Content Edit removal starts between two units."""
    return any(
        previous_end_seconds <= start < next_start_seconds for start, _end in exclusions
    )


def classify_boundary(
    *,
    previous_end_seconds: float,
    previous_speaker: str,
    next_start_seconds: float,
    next_speaker: str,
    exclusions: Sequence[tuple[float, float]],
) -> RhythmBoundary:
    return RhythmBoundary(
        source_gap_seconds=next_start_seconds - previous_end_seconds,
        speaker_change=previous_speaker != next_speaker,
        crosses_cut=crosses_cut(
            previous_end_seconds,
            next_start_seconds,
            exclusions,
        ),
    )


def inserted_silence_seconds(
    pause_seconds: float,
    *,
    previous_tail_seconds: float,
    next_head_seconds: float,
) -> float:
    """Subtract the silence the trimmed clip edges already contribute."""
    return max(0.0, pause_seconds - previous_tail_seconds - next_head_seconds)


def trim_window(
    samples: Sequence[float],
    sample_rate: int,
    policy: RhythmPolicy = RHYTHM_POLICY,
) -> TrimWindow:
    """Find the voiced span on 10 ms frames and keep the policy margins.

    A frame is voiced when its RMS reaches the fixed silence threshold.
    Digital-silence padding, low noise floors, and partial trailing frames
    never count as speech. A clip with no voiced frame is kept unchanged so
    a quiet render is never dropped from the episode.
    """
    total = len(samples)
    frame = max(1, round(sample_rate * policy.frame_seconds))
    frames = total // frame
    threshold = 10 ** (policy.silence_dbfs / 10)

    def voiced(index: int) -> bool:
        start = index * frame
        energy = math.fsum(value * value for value in samples[start : start + frame])
        return energy / frame >= threshold

    first = next((index for index in range(frames) if voiced(index)), None)
    if first is None:
        return TrimWindow(
            start=0,
            end=total,
            head_silence=0,
            tail_silence=0,
            voiced=False,
        )
    last = next(index for index in range(frames - 1, first - 1, -1) if voiced(index))
    voice_start = first * frame
    voice_end = (last + 1) * frame
    start = max(0, voice_start - round(policy.head_keep_seconds * sample_rate))
    end = min(total, voice_end + round(policy.tail_keep_seconds * sample_rate))
    return TrimWindow(
        start=start,
        end=end,
        head_silence=voice_start - start,
        tail_silence=end - voice_end,
        voiced=True,
    )


def cap_internal_silences(
    samples: array[float],
    sample_rate: int,
    policy: RhythmPolicy = RHYTHM_POLICY,
) -> tuple[array[float], tuple[float, ...]]:
    """Shorten silences between voiced frames to the policy cap.

    Only silence strictly inside the voiced span is shortened; the middle of
    an over-long run is removed and short fades keep the joins click-free.
    Returns the new samples and the removed seconds per shortened run.
    """
    frame = max(1, round(sample_rate * policy.frame_seconds))
    frames = len(samples) // frame
    threshold = 10 ** (policy.silence_dbfs / 10)
    voiced = [
        math.fsum(value * value for value in samples[i * frame : (i + 1) * frame])
        / frame
        >= threshold
        for i in range(frames)
    ]
    voiced_indexes = [index for index, flag in enumerate(voiced) if flag]
    if len(voiced_indexes) < 2:
        return samples, ()
    keep = round(policy.internal_silence_cap_seconds * sample_rate)
    fade = round(policy.internal_cut_fade_seconds * sample_rate)
    cuts: list[tuple[int, int]] = []
    run_start: int | None = None
    for index in range(voiced_indexes[0], voiced_indexes[-1] + 1):
        if not voiced[index]:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            start, end = run_start * frame, index * frame
            if end - start > keep:
                cuts.append((start + keep // 2, end - (keep - keep // 2)))
            run_start = None
    if not cuts:
        return samples, ()
    result = array("f")
    cursor = 0
    for cut_start, cut_end in cuts:
        head = samples[cursor:cut_start]
        apply_fades(head, fade_in=0, fade_out=fade)
        result.extend(head)
        cursor = cut_end
        tail_start = len(result)
        result.extend(samples[cut_end : cut_end + fade])
        apply_fades_range(result, tail_start, len(result))
        cursor = cut_end + fade
    result.extend(samples[cursor:])
    removed = tuple(round((end - start) / sample_rate, 3) for start, end in cuts)
    return result, removed


def apply_fades_range(samples: array[float], start: int, end: int) -> None:
    """Fade in the samples in ``[start, end)`` in place."""
    count = end - start
    if count <= 1:
        return
    step = 1.0 / (count - 1)
    for offset in range(count):
        samples[start + offset] *= offset * step


def apply_fades(
    samples: array[float],
    *,
    fade_in: int,
    fade_out: int,
) -> None:
    """Apply linear fades in place, never longer than half the clip."""
    limit = len(samples) // 2
    for count, forward in ((min(fade_in, limit), True), (min(fade_out, limit), False)):
        if count <= 0:
            continue
        step = 1.0 / (count - 1) if count > 1 else 0.0
        for offset in range(count):
            gain = offset * step
            index = offset if forward else len(samples) - 1 - offset
            samples[index] *= gain
