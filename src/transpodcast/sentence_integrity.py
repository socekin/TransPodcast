"""Sentence Integrity for V23/V24 Speaker ownership (crosstalk WS-A).

Crosstalk splits one spoken sentence into sub-second pieces owned by
different Speakers ("would have been number" / "one You"), and every piece
becomes its own Translation unit and TTS voice. A sentence (split at the
ASR transcript's sentence-final punctuation) that is mostly one Speaker's
gets its short foreign runs back:

- the sentence's main Speaker owns at least ``dominance`` of its speech;
- a foreign run of at most ``maximum_run_words`` words and
  ``maximum_run_seconds`` of speech moves to the main Speaker (each word
  counts as at most ``maximum_word_seconds``: aligners stretch words over
  crosstalk and silence);
- leading backchannel words ("Yeah I") keep their Speaker, and a run made
  only of backchannels never moves: backchannels are never folded
  (Round-3 Problem 4);
- a run is kept when the main Speaker's next words start with a
  backchannel ("Is that" / "yeah, yeah, merging ..."): the main Speaker is
  answering it.

A sentence with more than ``maximum_unconfident_words`` words whose
punctuation was not confidently recovered is left alone: a wide transcript
mismatch can hide a period, while one or two unmatched words are spelling
drift ("scifi" / "sci-fi").

Calibration: a replay bench of recorded episodes with Owner-confirmed
speaker-transition cases.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from transpodcast.decision_records import DecisionRecord
from transpodcast.speaker_boundaries import recover_sentence_punctuation

SENTENCE_INTEGRITY_POLICY_VERSION = "sentence-integrity-v1"
SENTENCE_INTEGRITY_RECORD_KIND = "sentence-integrity"
# Key of the summary in ``boundary_adjudication_json``; its presence marks a
# completed analysis as already integrated.
SENTENCE_INTEGRITY_SUMMARY_KEY = "sentence_integrity"
# Short replies a listener hears as their own turn; never moved or folded.
BACKCHANNEL_WORDS = frozenset(
    {
        "absolutely",
        "agreed",
        "ah",
        "cool",
        "correct",
        "definitely",
        "exactly",
        "great",
        "hmm",
        "huh",
        "indeed",
        "mhm",
        "mm",
        "nice",
        "no",
        "nope",
        "oh",
        "ok",
        "okay",
        "right",
        "sure",
        "totally",
        "true",
        "uh",
        "uhhuh",
        "um",
        "wow",
        "ya",
        "yah",
        "yeah",
        "yep",
        "yes",
        "yup",
    }
)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SentenceIntegrityPolicy:
    version: str = SENTENCE_INTEGRITY_POLICY_VERSION
    dominance: float = 0.6
    maximum_run_words: int = 3
    maximum_run_seconds: float = 1.2
    maximum_word_seconds: float = 0.6
    maximum_unconfident_words: int = 2
    # Zero-duration words still count a little toward the main Speaker.
    minimum_word_seconds: float = 0.05

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_SENTENCE_INTEGRITY_POLICY = SentenceIntegrityPolicy()


@dataclass(frozen=True)
class SentenceIntegrityMove:
    first_word_index: int
    last_word_index: int
    text: str
    from_speaker: str
    to_speaker: str
    start_seconds: float
    end_seconds: float
    sentence_first_word_index: int
    sentence_last_word_index: int
    main_speaker_share: float

    def record(self, policy: SentenceIntegrityPolicy) -> DecisionRecord:
        return DecisionRecord(
            kind=SENTENCE_INTEGRITY_RECORD_KIND,
            policy_version=policy.version,
            subject={
                "first_word_index": self.first_word_index,
                "last_word_index": self.last_word_index,
                "start_seconds": round(self.start_seconds, 3),
                "end_seconds": round(self.end_seconds, 3),
                "text": self.text,
            },
            evidence={
                "sentence_first_word_index": self.sentence_first_word_index,
                "sentence_last_word_index": self.sentence_last_word_index,
                "main_speaker_share": round(self.main_speaker_share, 3),
            },
            verdict="moved-to-sentence-owner",
            rationale=(
                f"The words belong to a sentence {self.to_speaker} mostly "
                f"speaks; moved from {self.from_speaker}."
            ),
        )


@dataclass(frozen=True)
class SentenceIntegrityResult:
    owners: tuple[str, ...]
    moves: tuple[SentenceIntegrityMove, ...]
    sentence_count: int
    skipped_unconfident_sentences: int
    punctuation_alignment: dict[str, Any]

    def records(
        self,
        policy: SentenceIntegrityPolicy = DEFAULT_SENTENCE_INTEGRITY_POLICY,
    ) -> tuple[DecisionRecord, ...]:
        return tuple(move.record(policy) for move in self.moves)

    def summary(
        self,
        policy: SentenceIntegrityPolicy = DEFAULT_SENTENCE_INTEGRITY_POLICY,
    ) -> dict[str, Any]:
        return {
            "policy_version": policy.version,
            "policy": policy.to_payload(),
            "sentence_count": self.sentence_count,
            "skipped_unconfident_sentences": self.skipped_unconfident_sentences,
            "moved_run_count": len(self.moves),
            "moved_word_count": sum(
                move.last_word_index - move.first_word_index + 1
                for move in self.moves
            ),
            "punctuation_alignment": self.punctuation_alignment,
        }


def _key(text: str) -> str:
    return "".join(_TOKEN.findall(text.lower()))


def is_backchannel(text: str) -> bool:
    return _key(text) in BACKCHANNEL_WORDS


def integrate_sentences(
    words: Sequence[tuple[str, float, float]],
    owners: Sequence[str],
    full_asr_text: str,
    policy: SentenceIntegrityPolicy = DEFAULT_SENTENCE_INTEGRITY_POLICY,
) -> SentenceIntegrityResult:
    """Return owners with short foreign runs moved to their sentence owner."""
    if len(words) != len(owners):
        raise ValueError("Sentence Integrity needs one owner per word")
    annotated: list[dict[str, Any]] = [
        {"text": text, "start_seconds": start, "end_seconds": end}
        for text, start, end in words
    ]
    alignment = recover_sentence_punctuation(annotated, full_asr_text)
    result = list(owners)
    moves: list[SentenceIntegrityMove] = []
    sentence_count = 0
    skipped = 0
    start = 0
    for index in range(len(words)):
        if not annotated[index].get("sentence_end") and index != len(words) - 1:
            continue
        first, last = start, index
        start = index + 1
        sentence_count += 1
        unconfident = sum(
            not annotated[i].get("punctuation_confident")
            for i in range(first, last + 1)
        )
        if unconfident > policy.maximum_unconfident_words:
            skipped += 1
            continue
        moves.extend(
            _sentence_moves(words, owners, first, last, policy)
        )
    for move in moves:
        for i in range(move.first_word_index, move.last_word_index + 1):
            result[i] = move.to_speaker
    return SentenceIntegrityResult(
        owners=tuple(result),
        moves=tuple(moves),
        sentence_count=sentence_count,
        skipped_unconfident_sentences=skipped,
        punctuation_alignment=alignment,
    )


def _sentence_moves(
    words: Sequence[tuple[str, float, float]],
    owners: Sequence[str],
    first: int,
    last: int,
    policy: SentenceIntegrityPolicy,
) -> list[SentenceIntegrityMove]:
    seconds: dict[str, float] = {}
    for i in range(first, last + 1):
        duration = max(float(words[i][2]) - float(words[i][1]), 0.0)
        seconds[owners[i]] = seconds.get(owners[i], 0.0) + max(
            duration, policy.minimum_word_seconds
        )
    if len(seconds) < 2:
        return []
    main, main_seconds = max(seconds.items(), key=lambda item: item[1])
    share = main_seconds / sum(seconds.values())
    if share < policy.dominance:
        return []
    runs: list[list[int]] = []
    for i in range(first, last + 1):
        if runs and owners[runs[-1][-1]] == owners[i]:
            runs[-1].append(i)
        else:
            runs.append([i])
    moves: list[SentenceIntegrityMove] = []
    for position, run in enumerate(runs):
        owner = owners[run[0]]
        if owner == main:
            continue
        while run and is_backchannel(words[run[0]][0]):
            run = run[1:]
        if not run:
            continue
        speech = sum(
            min(
                policy.maximum_word_seconds,
                max(float(words[i][2]) - float(words[i][1]), 0.0),
            )
            for i in run
        )
        if len(run) > policy.maximum_run_words or speech > policy.maximum_run_seconds:
            continue
        following = runs[position + 1] if position + 1 < len(runs) else []
        if (
            following
            and owners[following[0]] == main
            and is_backchannel(words[following[0]][0])
        ):
            continue
        moves.append(
            SentenceIntegrityMove(
                first_word_index=run[0],
                last_word_index=run[-1],
                text=" ".join(str(words[i][0]) for i in run),
                from_speaker=owner,
                to_speaker=main,
                start_seconds=float(words[run[0]][1]),
                end_seconds=float(words[run[-1]][2]),
                sentence_first_word_index=first,
                sentence_last_word_index=last,
                main_speaker_share=share,
            )
        )
    return moves
