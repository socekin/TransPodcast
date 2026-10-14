"""Deterministic word ownership for V21 Source Analysis.

V15/V17 Source Analysis gives each aligned word to the raw diarization turn
with the largest overlap, then attaches uncovered and zero-duration words to
the nearest owned word, with ties going to the previous Speaker. A
listener-confirmed transition set showed two systematic errors in that
rule: the diarizer switches late, and zero-duration words default to the
previous Speaker.

This policy decides the initial word owners for V21 Source Analysis before
Speaker Ownership Risk Regions are built. It applies three versioned rules:

1. **Zero-duration retiming.** A run of zero-duration words is spread over
   the gap between its timed neighbours in proportion to character length.
   When the gap is too small, each timed neighbour lends up to
   ``borrow_seconds_per_word`` per word in the run, but always keeps at least
   ``neighbour_keep_fraction`` of its own aligned duration.
2. **Onset bias.** Each diarization turn that starts a Speaker change starts
   ``onset_bias_seconds`` earlier when words are assigned. Since
   ``deterministic-ownership-v2`` a short-turn guard never moves that start
   into the preceding turn when the preceding turn is heard alone for less
   than ``short_turn_guard_seconds``: the bias may still cross silence, but
   it stops at the end of such a turn so a short reply is not erased.
3. **Tie rule.** A word belongs to the Speaker with the largest total overlap.
   An exact tie goes to the Speaker whose overlapping turn starts last, the
   following Speaker. A word without any overlap takes the owner of its
   nearest covered neighbour; equal distance goes to the following word.

The raw diarization turns and the aligner's word timestamps stay unchanged in
the analysis payload. The policy changes only who owns a word and the
timestamps that Canonical Transcript segments carry for retimed words. Its
evidence lists every adjusted word by its ``aligned_words`` index so the
original aligner timestamps remain auditable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

DETERMINISTIC_OWNERSHIP_POLICY_VERSION = "deterministic-ownership-v2"
UNKNOWN_SPEAKER = "speaker-unknown"


@dataclass(frozen=True)
class DeterministicOwnershipPolicy:
    version: str = DETERMINISTIC_OWNERSHIP_POLICY_VERSION
    # Calibration optimum on a listener-confirmed transition set: boundary
    # word error 9.6% at 0.20 s, 10.2% at both 0.16 s and 0.24 s, against
    # 14.7% for the V17 initial owners.
    onset_bias_seconds: float = 0.20
    # v2 short-turn guard. A turn counts as short when the time it is the
    # only active Speaker, after removing every overlap with another
    # Speaker's turn, is below this length. A calibration case held two such
    # turns (0.32 s and 0.16 s heard alone, 0.96 s and 0.88 s raw) whose
    # replies the 0.20 s bias erased; the calibration score is unchanged.
    # Zero disables the guard (``deterministic-ownership-v1``).
    short_turn_guard_seconds: float = 0.40
    # Aligned words at or below this duration carry no usable timing.
    zero_duration_seconds: float = 0.001
    # A zero-duration run needs at least this much room per word before it
    # borrows time from its timed neighbours.
    minimum_room_seconds_per_word: float = 0.06
    borrow_seconds_per_word: float = 0.04
    neighbour_keep_fraction: float = 0.5
    tie_tolerance_seconds: float = 1e-6

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


DETERMINISTIC_OWNERSHIP_POLICY = DeterministicOwnershipPolicy()
# The first policy without the short-turn guard, kept so its recorded
# calibration stays reproducible. No Pipeline Version selects it.
DETERMINISTIC_OWNERSHIP_POLICY_V1 = DeterministicOwnershipPolicy(
    version="deterministic-ownership-v1", short_turn_guard_seconds=0.0
)


@dataclass(frozen=True)
class TimedWord:
    text: str
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class SpeakerTurn:
    speaker: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class RetimedWords:
    words: tuple[TimedWord, ...]
    retimed_word_indexes: tuple[int, ...]
    borrowed_word_indexes: tuple[int, ...]


@dataclass(frozen=True)
class OwnerAssignment:
    owners: tuple[str, ...]
    tie_word_indexes: tuple[int, ...]
    nearest_word_indexes: tuple[int, ...]


@dataclass(frozen=True)
class DeterministicOwnership:
    """Assigned words ready for v6 word grouping, plus audit evidence."""

    assigned_words: tuple[dict[str, Any], ...]
    retimed: RetimedWords
    assignment: OwnerAssignment
    shifted_turn_count: int
    policy: DeterministicOwnershipPolicy
    guarded_turn_count: int = 0

    def evidence_payload(self) -> dict[str, Any]:
        adjusted = sorted(
            {
                *self.retimed.retimed_word_indexes,
                *self.retimed.borrowed_word_indexes,
            }
        )
        return {
            "policy_version": self.policy.version,
            "policy": self.policy.to_payload(),
            "word_index_basis": "aligned_words",
            "word_count": len(self.assigned_words),
            "shifted_turn_count": self.shifted_turn_count,
            "guarded_turn_count": self.guarded_turn_count,
            "retimed_word_indexes": list(self.retimed.retimed_word_indexes),
            "borrowed_word_indexes": list(self.retimed.borrowed_word_indexes),
            "adjusted_words": [
                {
                    "word_index": index,
                    "start_seconds": self.retimed.words[index].start_seconds,
                    "end_seconds": self.retimed.words[index].end_seconds,
                }
                for index in adjusted
            ],
            "tie_word_indexes": list(self.assignment.tie_word_indexes),
            "nearest_word_indexes": list(self.assignment.nearest_word_indexes),
            "unowned_word_count": sum(
                owner == UNKNOWN_SPEAKER for owner in self.assignment.owners
            ),
        }


def retime_zero_duration_words(
    words: Sequence[TimedWord],
    policy: DeterministicOwnershipPolicy = DETERMINISTIC_OWNERSHIP_POLICY,
) -> RetimedWords:
    """Spread each run of zero-duration words between its timed neighbours."""
    result = list(words)
    retimed: list[int] = []
    borrowed: list[int] = []
    index = 0
    while index < len(result):
        if not _is_zero_duration(result[index], policy):
            index += 1
            continue
        first = index
        while index < len(result) and _is_zero_duration(result[index], policy):
            index += 1
        run = range(first, index)
        previous = first - 1 if first > 0 else None
        following = index if index < len(result) else None
        left = (
            result[previous].end_seconds
            if previous is not None
            else result[first].start_seconds
        )
        right = (
            result[following].start_seconds
            if following is not None
            else result[index - 1].end_seconds
        )
        if right - left < policy.minimum_room_seconds_per_word * len(run):
            borrow = policy.borrow_seconds_per_word * len(run)
            keep = policy.neighbour_keep_fraction
            if previous is not None:
                lender = result[previous]
                left = max(
                    lender.start_seconds + keep * lender.duration_seconds,
                    lender.end_seconds - borrow,
                )
            if following is not None:
                lender = result[following]
                right = min(
                    lender.end_seconds - keep * lender.duration_seconds,
                    lender.start_seconds + borrow,
                )
        if right <= left:
            # Even borrowed time cannot hold the run: keep the aligner's
            # timestamps and let the nearest-neighbour rule own the words.
            continue
        if previous is not None and left < result[previous].end_seconds:
            result[previous] = replace(result[previous], end_seconds=left)
            borrowed.append(previous)
        if following is not None and right > result[following].start_seconds:
            result[following] = replace(result[following], start_seconds=right)
            borrowed.append(following)
        weights = [max(1, len(result[k].text)) for k in run]
        total = sum(weights)
        cursor = left
        consumed = 0
        for k, weight in zip(run, weights, strict=True):
            consumed += weight
            end = right if consumed == total else left + (right - left) * (
                consumed / total
            )
            result[k] = replace(result[k], start_seconds=cursor, end_seconds=end)
            retimed.append(k)
            cursor = end
    return RetimedWords(
        words=tuple(result),
        retimed_word_indexes=tuple(retimed),
        borrowed_word_indexes=tuple(sorted(set(borrowed))),
    )


def shift_speaker_change_onsets(
    turns: Sequence[SpeakerTurn],
    policy: DeterministicOwnershipPolicy = DETERMINISTIC_OWNERSHIP_POLICY,
) -> tuple[SpeakerTurn, ...]:
    """Start every turn that changes Speaker earlier by the onset bias."""
    return _shift_onsets(turns, policy)[0]


def exclusive_seconds(
    turn: SpeakerTurn, turns: Sequence[SpeakerTurn]
) -> float:
    """Seconds of ``turn`` during which no other Speaker's turn is active."""
    blockers = sorted(
        (
            max(turn.start_seconds, other.start_seconds),
            min(turn.end_seconds, other.end_seconds),
        )
        for other in turns
        if other.speaker != turn.speaker
        and min(turn.end_seconds, other.end_seconds)
        > max(turn.start_seconds, other.start_seconds)
    )
    covered = 0.0
    cursor = turn.start_seconds
    for start, end in blockers:
        start = max(start, cursor)
        if end > start:
            covered += end - start
            cursor = end
    return max(0.0, turn.end_seconds - turn.start_seconds - covered)


def _shift_onsets(
    turns: Sequence[SpeakerTurn],
    policy: DeterministicOwnershipPolicy,
) -> tuple[tuple[SpeakerTurn, ...], int]:
    """Shifted turns in start order and the number of guarded shifts."""
    ordered = sorted(turns, key=lambda item: item.start_seconds)
    shifted: list[SpeakerTurn] = []
    guarded = 0
    previous: SpeakerTurn | None = None
    for turn in ordered:
        if previous is not None and turn.speaker != previous.speaker:
            start = max(0.0, turn.start_seconds - policy.onset_bias_seconds)
            if (
                start < previous.end_seconds
                and exclusive_seconds(previous, ordered)
                < policy.short_turn_guard_seconds
            ):
                # Cross silence, but never enter a short preceding turn.
                start = max(start, min(turn.start_seconds, previous.end_seconds))
                guarded += 1
            shifted.append(replace(turn, start_seconds=start))
        else:
            shifted.append(turn)
        previous = turn
    return tuple(shifted), guarded


def assign_word_owners(
    words: Sequence[TimedWord],
    turns: Sequence[SpeakerTurn],
    policy: DeterministicOwnershipPolicy = DETERMINISTIC_OWNERSHIP_POLICY,
) -> OwnerAssignment:
    """Own each word by total overlap; ties and gaps go to the following side."""
    covered: list[str | None] = []
    ties: list[int] = []
    for index, word in enumerate(words):
        totals: dict[str, float] = {}
        latest_start: dict[str, float] = {}
        for turn in turns:
            overlap = min(word.end_seconds, turn.end_seconds) - max(
                word.start_seconds, turn.start_seconds
            )
            if overlap <= 0:
                continue
            totals[turn.speaker] = totals.get(turn.speaker, 0.0) + overlap
            latest_start[turn.speaker] = max(
                latest_start.get(turn.speaker, turn.start_seconds),
                turn.start_seconds,
            )
        if not totals:
            covered.append(None)
            continue
        best = max(totals.values())
        tied = [
            speaker
            for speaker, seconds in totals.items()
            if best - seconds <= policy.tie_tolerance_seconds
        ]
        if len(tied) > 1:
            ties.append(index)
        covered.append(
            max(tied, key=lambda speaker: (latest_start[speaker], speaker))
        )
    owned = {
        index: owner for index, owner in enumerate(covered) if owner is not None
    }
    previous: list[int | None] = []
    last: int | None = None
    for index in range(len(covered)):
        if index in owned:
            last = index
        previous.append(last)
    following: list[int | None] = [None] * len(covered)
    last = None
    for index in range(len(covered) - 1, -1, -1):
        if index in owned:
            last = index
        following[index] = last
    owners: list[str] = []
    nearest: list[int] = []
    for index in range(len(covered)):
        if index in owned:
            owners.append(owned[index])
            continue
        before, after = previous[index], following[index]
        if before is None and after is None:
            owners.append(UNKNOWN_SPEAKER)
            continue
        nearest.append(index)
        if before is None or after is None:
            anchor = before if before is not None else after
            assert anchor is not None
            owners.append(owned[anchor])
            continue
        gap_before = abs(words[index].start_seconds - words[before].end_seconds)
        gap_after = abs(words[after].start_seconds - words[index].end_seconds)
        # Equal distance goes to the following word, never by default to
        # the previous Speaker.
        owners.append(owned[before] if gap_before < gap_after else owned[after])
    return OwnerAssignment(
        owners=tuple(owners),
        tie_word_indexes=tuple(ties),
        nearest_word_indexes=tuple(nearest),
    )


def apply_deterministic_ownership(
    words: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    policy: DeterministicOwnershipPolicy = DETERMINISTIC_OWNERSHIP_POLICY,
) -> DeterministicOwnership:
    """Retime, then own aligned words against onset-biased diarization turns.

    The returned word dictionaries keep every aligner field, carry the
    retimed timestamps, and add ``speaker`` and ``overlap``. ``overlap``
    still describes the raw diarization turns, so the onset bias never
    invents Crosstalk.
    """
    timed = tuple(
        TimedWord(
            str(word["text"]),
            float(word["start_seconds"]),
            float(word["end_seconds"]),
        )
        for word in words
    )
    raw_turns = tuple(
        SpeakerTurn(
            str(turn["speaker"]),
            float(turn["start_seconds"]),
            float(turn["end_seconds"]),
        )
        for turn in turns
    )
    retimed = retime_zero_duration_words(timed, policy)
    shifted, guarded = _shift_onsets(raw_turns, policy)
    assignment = assign_word_owners(retimed.words, shifted, policy)
    assigned = tuple(
        {
            **source,
            "start_seconds": word.start_seconds,
            "end_seconds": word.end_seconds,
            "speaker": owner,
            "overlap": len(_overlapping_speakers(word, raw_turns)) > 1,
        }
        for source, word, owner in zip(
            words, retimed.words, assignment.owners, strict=True
        )
    )
    return DeterministicOwnership(
        assigned_words=assigned,
        retimed=retimed,
        assignment=assignment,
        shifted_turn_count=sum(
            a.start_seconds != b.start_seconds
            for a, b in zip(
                sorted(raw_turns, key=lambda item: item.start_seconds),
                shifted,
                strict=True,
            )
        ),
        policy=policy,
        guarded_turn_count=guarded,
    )


def _is_zero_duration(
    word: TimedWord, policy: DeterministicOwnershipPolicy
) -> bool:
    return word.duration_seconds <= policy.zero_duration_seconds


def _overlapping_speakers(
    word: TimedWord, turns: Sequence[SpeakerTurn]
) -> set[str]:
    return {
        turn.speaker
        for turn in turns
        if min(word.end_seconds, turn.end_seconds)
        > max(word.start_seconds, turn.start_seconds)
    }
