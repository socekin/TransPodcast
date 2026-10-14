"""Listening Audit scoring for rendered Mandarin units.

The audit compares a back-transcription of each rendered Render Plan unit with
its target text, checks speech rate and internal silence, and compares unit
voiceprints with per-Speaker rendered centroids. Model inference happens
elsewhere; this module is deterministic scoring only.

A unit without a transcript (the back-transcription was unavailable) is still
scored on its local energy measurements, so a duration anomaly is caught even
when the remote audit models cannot run. ``audit_unavailable`` findings make
every missing measurement explicit; they are listed and never block.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

LISTENING_AUDIT_POLICY_VERSION = "listening-audit-v1"

Severity = Literal["hard", "soft"]
FindingKind = Literal[
    "high_cer",
    "truncation",
    "insertion",
    "english_leakage",
    "speech_rate",
    "internal_silence",
    "duration_anomaly",
    "voice_drift",
    "audit_unavailable",
]


@dataclass(frozen=True)
class ListeningAuditPolicy:
    version: str = LISTENING_AUDIT_POLICY_VERSION
    hard_cer: float = 0.5
    soft_cer: float = 0.25
    truncated_tail_share: float = 0.3
    insertion_ratio: float = 1.5
    minimum_rate_characters: int = 6
    minimum_hanzi_per_second: float = 2.5
    maximum_hanzi_per_second: float = 8.0
    expected_hanzi_per_second: float = 5.0
    hard_duration_ratio: float = 3.0
    maximum_internal_silence_seconds: float = 1.2
    voice_drift_margin: float = 0.05


DEFAULT_LISTENING_AUDIT_POLICY = ListeningAuditPolicy()


@dataclass(frozen=True)
class UnitObservation:
    unit_id: str
    speaker_label: str
    target_text: str
    transcript: str | None
    speech_seconds: float
    longest_internal_silence_seconds: float = 0.0
    voiceprint: tuple[float, ...] | None = None


@dataclass(frozen=True)
class AuditFinding:
    unit_id: str
    kind: FindingKind
    severity: Severity
    detail: str

    def to_payload(self) -> dict[str, str]:
        return {
            "unit_id": self.unit_id,
            "kind": self.kind,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class UnitAudit:
    unit_id: str
    cer: float | None
    findings: tuple[AuditFinding, ...] = ()

    @property
    def hard(self) -> bool:
        return any(finding.severity == "hard" for finding in self.findings)


@dataclass(frozen=True)
class ListeningAuditResult:
    policy_version: str
    units: tuple[UnitAudit, ...]
    voice_findings: tuple[AuditFinding, ...] = field(default_factory=tuple)

    @property
    def findings(self) -> tuple[AuditFinding, ...]:
        return (
            tuple(finding for unit in self.units for finding in unit.findings)
            + self.voice_findings
        )

    @property
    def hard_unit_ids(self) -> tuple[str, ...]:
        hard = {f.unit_id for f in self.findings if f.severity == "hard"}
        return tuple(unit.unit_id for unit in self.units if unit.unit_id in hard)

    def to_payload(self) -> dict[str, object]:
        findings = self.findings
        scored = [unit.cer for unit in self.units if unit.cer is not None]
        return {
            "policy_version": self.policy_version,
            "unit_count": len(self.units),
            "transcribed_unit_count": len(scored),
            "flagged_unit_count": len({f.unit_id for f in findings}),
            "hard_unit_ids": list(self.hard_unit_ids),
            "mean_cer": (round(sum(scored) / len(scored), 4) if scored else None),
            "findings": [finding.to_payload() for finding in findings],
        }


_DIGITS = "零一二三四五六七八九"
_SMALL_UNITS = ("", "十", "百", "千")
_LARGE_UNITS = ("", "万", "亿", "万亿")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_LATIN_TOKEN = re.compile(r"[a-z][a-z0-9'.+-]*")
_LATIN_RUN = re.compile(r"[a-z][a-z0-9'.+-]*(?:[\s,]+[a-z][a-z0-9'.+-]*)*")
_MINIMUM_LEAKED_TOKENS = 3
_SPOKEN_CHARACTER = re.compile(r"[一-鿿]|[a-z0-9]")


def _integer_reading(value: int) -> str:
    if value == 0:
        return _DIGITS[0]
    groups: list[int] = []
    remaining = value
    while remaining:
        groups.append(remaining % 10_000)
        remaining //= 10_000
    if len(groups) > len(_LARGE_UNITS):
        return "".join(_DIGITS[int(d)] for d in str(value))
    parts: list[str] = []
    zero_pending = False
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if group == 0:
            zero_pending = bool(parts)
            continue
        if zero_pending or (parts and group < 1000):
            parts.append(_DIGITS[0])
        parts.append(_group_reading(group) + _LARGE_UNITS[index])
        zero_pending = False
    reading = "".join(parts)
    return reading[1:] if reading.startswith("一十") else reading


def _group_reading(group: int) -> str:
    digits = f"{group:04d}"
    parts: list[str] = []
    zero_pending = False
    for position, digit in enumerate(digits):
        number = int(digit)
        unit = _SMALL_UNITS[3 - position]
        if number == 0:
            zero_pending = bool(parts)
            continue
        if zero_pending:
            parts.append(_DIGITS[0])
            zero_pending = False
        parts.append(_DIGITS[number] + unit)
    return "".join(parts)


def _number_reading(match: re.Match[str]) -> str:
    return _decimal_reading(match.group(0))


def _decimal_reading(text: str) -> str:
    whole, _, fraction = text.partition(".")
    reading = _integer_reading(int(whole))
    if fraction:
        reading += "点" + "".join(_DIGITS[int(d)] for d in fraction)
    return reading


def normalize_spoken_text(value: str) -> str:
    """Return comparable spoken characters: hanzi, Latin letters, digits read."""
    text = unicodedata.normalize("NFKC", value).lower()
    text = _PERCENT.sub(lambda m: "百分之" + _decimal_reading(m.group(1)), text)
    text = _NUMBER.sub(_number_reading, text)
    return "".join(_SPOKEN_CHARACTER.findall(text))


def _alignment(reference: str, hypothesis: str) -> tuple[int, list[bool]]:
    """Return edit distance and, per reference character, whether it was kept."""
    rows = len(reference) + 1
    columns = len(hypothesis) + 1
    distance = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        distance[row][0] = row
    for column in range(columns):
        distance[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            cost = 0 if reference[row - 1] == hypothesis[column - 1] else 1
            distance[row][column] = min(
                distance[row - 1][column] + 1,
                distance[row][column - 1] + 1,
                distance[row - 1][column - 1] + cost,
            )
    kept = [False] * len(reference)
    row, column = len(reference), len(hypothesis)
    while row > 0 and column > 0:
        cost = 0 if reference[row - 1] == hypothesis[column - 1] else 1
        if distance[row][column] == distance[row - 1][column - 1] + cost:
            kept[row - 1] = cost == 0
            row -= 1
            column -= 1
        elif distance[row][column] == distance[row - 1][column] + 1:
            row -= 1
        else:
            column -= 1
    return distance[-1][-1], kept


def character_error_rate(reference: str, hypothesis: str) -> float:
    normalized_reference = normalize_spoken_text(reference)
    normalized_hypothesis = normalize_spoken_text(hypothesis)
    if not normalized_reference:
        return 0.0 if not normalized_hypothesis else 1.0
    distance, _ = _alignment(normalized_reference, normalized_hypothesis)
    return distance / len(normalized_reference)


def spoken_length(value: str) -> int:
    """Approximate spoken syllables: hanzi after reading numbers aloud, plus
    two syllables per Latin token."""
    normalized = normalize_spoken_text(value)
    hanzi = sum(1 for character in normalized if "\u4e00" <= character <= "\u9fff")
    latin = len(_LATIN_TOKEN.findall(unicodedata.normalize("NFKC", value).lower()))
    return hanzi + 2 * latin


def audit_unit(
    observation: UnitObservation,
    policy: ListeningAuditPolicy = DEFAULT_LISTENING_AUDIT_POLICY,
) -> UnitAudit:
    findings: list[AuditFinding] = []

    def flag(kind: FindingKind, severity: Severity, detail: str) -> None:
        findings.append(AuditFinding(observation.unit_id, kind, severity, detail))

    cer = (
        None
        if observation.transcript is None
        else _score_transcript(observation, policy, flag)
    )
    _score_energy(observation, policy, flag)
    return UnitAudit(
        observation.unit_id,
        round(cer, 4) if cer is not None else None,
        tuple(findings),
    )


def _score_transcript(
    observation: UnitObservation,
    policy: ListeningAuditPolicy,
    flag: Callable[[FindingKind, Severity, str], None],
) -> float:
    transcript = observation.transcript or ""
    reference = normalize_spoken_text(observation.target_text)
    hypothesis = normalize_spoken_text(transcript)
    if reference:
        distance, kept = _alignment(reference, hypothesis)
        cer = distance / len(reference)
    else:
        cer, kept = (0.0 if not hypothesis else 1.0), []
    if cer >= policy.hard_cer:
        flag("high_cer", "hard", f"CER {cer:.2f}")
    elif cer >= policy.soft_cer:
        flag("high_cer", "soft", f"CER {cer:.2f}")

    if kept:
        tail = max(1, math.ceil(len(kept) * policy.truncated_tail_share))
        if not any(kept[-tail:]):
            flag("truncation", "hard", f"last {tail} target characters missing")
    if reference and len(hypothesis) >= policy.insertion_ratio * len(reference) + 2:
        flag(
            "insertion",
            "hard",
            f"transcript {len(hypothesis)} vs target {len(reference)} characters",
        )

    target_tokens = set(
        _LATIN_TOKEN.findall(
            unicodedata.normalize("NFKC", observation.target_text).lower()
        )
    )
    # Leakage means untranslated English speech: a run of at least three
    # consecutive Latin tokens absent from the target. A single English name
    # transcribed differently (for example Dots -> Docs) is a
    # pronunciation variant, not leakage.
    leaked: list[str] = []
    for run in _LATIN_RUN.findall(unicodedata.normalize("NFKC", transcript).lower()):
        unmatched = [
            token for token in _LATIN_TOKEN.findall(run) if token not in target_tokens
        ]
        if len(unmatched) >= _MINIMUM_LEAKED_TOKENS:
            leaked.append(" ".join(unmatched))
    if leaked:
        flag(
            "english_leakage", "soft", "untranslated English: " + "; ".join(leaked[:3])
        )
    return cer


def _score_energy(
    observation: UnitObservation,
    policy: ListeningAuditPolicy,
    flag: Callable[[FindingKind, Severity, str], None],
) -> None:
    hanzi = spoken_length(observation.target_text)
    if observation.speech_seconds > 0 and hanzi >= policy.minimum_rate_characters:
        rate = hanzi / observation.speech_seconds
        expected = hanzi / policy.expected_hanzi_per_second
        if observation.speech_seconds >= policy.hard_duration_ratio * expected:
            flag(
                "duration_anomaly",
                "hard",
                f"{observation.speech_seconds:.1f}s for {hanzi} hanzi",
            )
        elif not (
            policy.minimum_hanzi_per_second <= rate <= policy.maximum_hanzi_per_second
        ):
            flag("speech_rate", "soft", f"{rate:.1f} hanzi/s")

    if (
        observation.longest_internal_silence_seconds
        > policy.maximum_internal_silence_seconds
    ):
        flag(
            "internal_silence",
            "soft",
            f"{observation.longest_internal_silence_seconds:.1f}s silence",
        )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _centroids(
    observations: Sequence[UnitObservation],
) -> dict[str, tuple[float, ...]]:
    grouped: dict[str, list[tuple[float, ...]]] = {}
    for observation in observations:
        if observation.voiceprint is not None:
            grouped.setdefault(observation.speaker_label, []).append(
                observation.voiceprint
            )
    return {
        speaker: tuple(
            sum(values) / len(vectors) for values in zip(*vectors, strict=True)
        )
        for speaker, vectors in grouped.items()
    }


def voice_drift_findings(
    observations: Sequence[UnitObservation],
    policy: ListeningAuditPolicy = DEFAULT_LISTENING_AUDIT_POLICY,
) -> tuple[AuditFinding, ...]:
    centroids = _centroids(observations)
    if len(centroids) < 2:
        return ()
    findings = []
    for observation in observations:
        if observation.voiceprint is None:
            continue
        own = centroids.get(observation.speaker_label)
        if own is None:
            continue
        own_score = _cosine(observation.voiceprint, own)
        rivals: Mapping[str, float] = {
            speaker: _cosine(observation.voiceprint, centroid)
            for speaker, centroid in centroids.items()
            if speaker != observation.speaker_label
        }
        rival, rival_score = max(rivals.items(), key=lambda item: item[1])
        if rival_score - own_score >= policy.voice_drift_margin:
            findings.append(
                AuditFinding(
                    observation.unit_id,
                    "voice_drift",
                    "soft",
                    f"closer to {rival} ({rival_score:.2f}) than "
                    f"{observation.speaker_label} ({own_score:.2f})",
                )
            )
    return tuple(findings)


def audit_units(
    observations: Sequence[UnitObservation],
    policy: ListeningAuditPolicy = DEFAULT_LISTENING_AUDIT_POLICY,
) -> ListeningAuditResult:
    return ListeningAuditResult(
        policy_version=policy.version,
        units=tuple(audit_unit(observation, policy) for observation in observations),
        voice_findings=voice_drift_findings(observations, policy),
    )
