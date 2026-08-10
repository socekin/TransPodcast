from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from math import sqrt
from typing import Any

from transpodcast.similarity import cosine_similarity
from transpodcast.speaker_boundaries import stabilize_speaker_boundaries


@dataclass(frozen=True)
class SourceCanonicalizationPolicy:
    version: str = "source-analysis-canonicalization-v1"
    minimum_window_seconds: float = 1.0
    maximum_window_seconds: float = 12.0
    maximum_windows_per_label: int = 5
    minimum_dominant_duration_seconds: float = 30.0
    minimum_dominant_duration_ratio: float = 0.05
    minimum_dominant_windows: int = 3
    minimum_candidate_windows: int = 2
    minimum_window_agreement: float = 0.75
    minimum_window_similarity: float = 0.65
    minimum_winning_margin: float = 0.15


DEFAULT_SOURCE_CANONICALIZATION_POLICY = SourceCanonicalizationPolicy()


@dataclass(frozen=True)
class AlignedWordEvidence:
    text: str
    start_seconds: float
    end_seconds: float

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiarizationTurnEvidence:
    speaker: str
    start_seconds: float
    end_seconds: float

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpeakerWindowEvidence:
    window_id: str
    speaker_label: str
    start_seconds: float
    end_seconds: float
    content_hash: str
    embedding: tuple[float, ...]
    clean: bool = True
    quality_score: float | None = None
    embedding_model_id: str = ""
    embedding_model_revision: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "embedding": list(self.embedding),
        }


@dataclass(frozen=True)
class SourceCanonicalizationRequest:
    words: tuple[AlignedWordEvidence, ...]
    diarization: tuple[DiarizationTurnEvidence, ...]
    transcript_text: str
    speaker_windows: tuple[SpeakerWindowEvidence, ...]

    @classmethod
    def from_payloads(
        cls,
        *,
        words: list[dict[str, Any]],
        diarization: list[dict[str, Any]],
        transcript_text: str,
        speaker_windows: list[dict[str, Any]],
    ) -> SourceCanonicalizationRequest:
        return cls(
            words=tuple(
                AlignedWordEvidence(
                    text=str(word["text"]),
                    start_seconds=float(word["start_seconds"]),
                    end_seconds=float(word["end_seconds"]),
                )
                for word in words
            ),
            diarization=tuple(
                DiarizationTurnEvidence(
                    speaker=str(turn["speaker"]),
                    start_seconds=float(turn["start_seconds"]),
                    end_seconds=float(turn["end_seconds"]),
                )
                for turn in diarization
            ),
            transcript_text=transcript_text,
            speaker_windows=tuple(
                SpeakerWindowEvidence(
                    window_id=str(window["window_id"]),
                    speaker_label=str(window["speaker_label"]),
                    start_seconds=float(window["start_seconds"]),
                    end_seconds=float(window["end_seconds"]),
                    content_hash=str(window["content_hash"]),
                    embedding=tuple(
                        float(value)
                        for value in window.get("embedding", [])
                    ),
                    clean=bool(window.get("clean", True)),
                    quality_score=(
                        float(window["quality_score"])
                        if window.get("quality_score") is not None
                        else None
                    ),
                    embedding_model_id=str(
                        window.get("embedding_model_id", "")
                    ),
                    embedding_model_revision=str(
                        window.get("embedding_model_revision", "")
                    ),
                )
                for window in speaker_windows
            ),
        )


@dataclass(frozen=True)
class SourceCanonicalizationResult:
    payload_json: str

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
    ) -> SourceCanonicalizationResult:
        return cls(
            payload_json=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    def to_payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise TypeError("Canonicalization result must be an object")
        return value


def canonicalize_source_analysis(
    request: SourceCanonicalizationRequest,
    *,
    policy: SourceCanonicalizationPolicy = (
        DEFAULT_SOURCE_CANONICALIZATION_POLICY
    ),
) -> SourceCanonicalizationResult:
    """Convert provider evidence into one deterministic episode model."""

    words = [word.to_payload() for word in request.words]
    diarization = [turn.to_payload() for turn in request.diarization]
    transcript_text = request.transcript_text
    speaker_windows = [
        window.to_payload()
        for window in request.speaker_windows
    ]
    raw_labels = sorted(
        {
            str(turn["speaker"])
            for turn in diarization
        }
        | {
            str(window["speaker_label"])
            for window in speaker_windows
        }
    )
    windows_by_label = _valid_windows_by_label(speaker_windows)
    durations = _speech_durations(diarization, raw_labels)
    dominant_labels = _dominant_labels(
        raw_labels,
        durations,
        windows_by_label,
        policy,
    )
    dominant_centroids = {
        label: _centroid(
            [
                _embedding(window)
                for window in windows_by_label.get(label, [])
            ]
        )
        for label in dominant_labels
    }

    mapping: dict[str, str] = {}
    decisions: list[dict[str, Any]] = []
    for label in raw_labels:
        if label in dominant_centroids:
            mapping[label] = label
            decisions.append(
                {
                    "source_label": label,
                    "resolved_speaker": label,
                    "outcome": "preserved",
                    "reason": "dominant_label",
                    "window_count": len(windows_by_label.get(label, [])),
                    "duration_seconds": round(durations[label], 6),
                    "votes": [],
                }
            )
            continue
        decision = _candidate_decision(
            label,
            windows_by_label.get(label, []),
            dominant_centroids,
            durations[label],
            policy,
        )
        mapping[label] = str(decision["resolved_speaker"])
        decisions.append(decision)

    mapped_turns = [
        {
            **turn,
            "speaker": mapping.get(
                str(turn["speaker"]),
                str(turn["speaker"]),
            ),
        }
        for turn in diarization
    ]
    boundary = stabilize_speaker_boundaries(
        words,
        mapped_turns,
        transcript_text=transcript_text,
    )
    speakers = _episode_speakers(speaker_windows, mapping)
    validate_canonical_word_invariants(words, boundary["words"])
    missing_references = set(mapping.values()) - {
        str(speaker["local_label"])
        for speaker in speakers
    }
    if missing_references:
        raise ValueError(
            "No usable Voice Reference for episode Speaker(s): "
            + ", ".join(sorted(missing_references))
        )
    unresolved = [
        decision
        for decision in decisions
        if decision["reason"] not in {"dominant_label", "evidence_converged"}
    ]
    consolidation = {
        "summary": {
            "policy_version": policy.version,
            "raw_label_count": len(raw_labels),
            "episode_speaker_count": len(set(mapping.values())),
            "consolidated_label_count": sum(
                source != resolved
                for source, resolved in mapping.items()
            ),
            "unresolved_label_count": len(unresolved),
        },
        "policy": asdict(policy),
        "dominant_speakers": [
            {
                "speaker_label": label,
                "duration_seconds": round(durations[label], 6),
                "window_count": len(
                    windows_by_label.get(label, [])
                ),
                "centroid": list(dominant_centroids[label]),
            }
            for label in dominant_labels
        ],
        "decisions": decisions,
        "findings": [
            {
                "kind": "speaker_consolidation_unresolved",
                "speaker_label": str(decision["source_label"]),
                "reason": str(decision["reason"]),
            }
            for decision in unresolved
        ],
    }
    return SourceCanonicalizationResult.from_payload(
        {
            "segments": boundary["segments"],
            "words": boundary["words"],
            "speakers": speakers,
            "speaker_mapping": mapping,
            "mapped_diarization_turns": mapped_turns,
            "consolidation": consolidation,
            "boundary_stabilization": {
                "summary": boundary["summary"],
                "decisions": boundary["decisions"],
            },
            "policy_version": policy.version,
        }
    )


def _valid_windows_by_label(
    windows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    expected_dimension: int | None = None
    for window in windows:
        embedding = _embedding(window)
        if not bool(window.get("clean", True)) or not embedding:
            continue
        if expected_dimension is None:
            expected_dimension = len(embedding)
        if len(embedding) != expected_dimension:
            continue
        grouped.setdefault(str(window["speaker_label"]), []).append(window)
    for values in grouped.values():
        values.sort(
            key=lambda item: (
                float(item["start_seconds"]),
                str(item["window_id"]),
            )
        )
    return grouped


def _speech_durations(
    turns: list[dict[str, Any]],
    labels: list[str],
) -> dict[str, float]:
    durations = dict.fromkeys(labels, 0.0)
    for turn in turns:
        label = str(turn["speaker"])
        durations[label] = durations.get(label, 0.0) + max(
            0.0,
            float(turn["end_seconds"])
            - float(turn["start_seconds"]),
        )
    return durations


def _dominant_labels(
    labels: list[str],
    durations: dict[str, float],
    windows: dict[str, list[dict[str, Any]]],
    policy: SourceCanonicalizationPolicy,
) -> list[str]:
    longest = max(durations.values(), default=0.0)
    minimum_duration = max(
        policy.minimum_dominant_duration_seconds,
        longest * policy.minimum_dominant_duration_ratio,
    )
    return [
        label
        for label in labels
        if durations[label] >= minimum_duration
        and len(windows.get(label, [])) >= policy.minimum_dominant_windows
    ]


def _candidate_decision(
    label: str,
    windows: list[dict[str, Any]],
    dominant_centroids: dict[str, tuple[float, ...]],
    duration_seconds: float,
    policy: SourceCanonicalizationPolicy,
) -> dict[str, Any]:
    votes: list[dict[str, Any]] = []
    for window in windows:
        scores = sorted(
            (
                (cosine_similarity(_embedding(window), centroid), candidate)
                for candidate, centroid in dominant_centroids.items()
            ),
            reverse=True,
        )
        if not scores:
            continue
        winner_score, winner = scores[0]
        runner_up_score = scores[1][0] if len(scores) > 1 else -1.0
        votes.append(
            {
                "window_id": str(window["window_id"]),
                "winner": winner,
                "similarity": round(winner_score, 6),
                "winning_margin": round(
                    winner_score - runner_up_score,
                    6,
                ),
                "scores": {
                    candidate: round(score, 6)
                    for score, candidate in sorted(
                        scores,
                        key=lambda item: item[1],
                    )
                },
            }
        )

    winner_counts: dict[str, int] = {}
    for vote in votes:
        winner = str(vote["winner"])
        winner_counts[winner] = winner_counts.get(winner, 0) + 1
    winner = (
        max(
            winner_counts,
            key=lambda candidate: (
                winner_counts[candidate],
                candidate,
            ),
        )
        if winner_counts
        else label
    )
    winner_votes = [
        vote for vote in votes if str(vote["winner"]) == winner
    ]
    agreement = (
        len(winner_votes) / len(votes)
        if votes
        else 0.0
    )
    enough_windows = len(votes) >= policy.minimum_candidate_windows
    enough_candidates = len(dominant_centroids) >= 2
    consistent = agreement >= policy.minimum_window_agreement
    enough_similarity = bool(winner_votes) and all(
        float(vote["similarity"]) >= policy.minimum_window_similarity
        for vote in winner_votes
    )
    enough_margin = bool(winner_votes) and all(
        float(vote["winning_margin"]) >= policy.minimum_winning_margin
        for vote in winner_votes
    )
    if (
        enough_candidates
        and enough_windows
        and consistent
        and enough_similarity
        and enough_margin
    ):
        outcome = "consolidated"
        reason = "evidence_converged"
        resolved = winner
    else:
        outcome = "preserved"
        resolved = label
        if not enough_candidates:
            reason = "insufficient_dominant_comparison"
        elif not enough_windows:
            reason = "insufficient_window_evidence"
        elif not consistent:
            reason = "inconsistent_window_votes"
        elif not enough_similarity:
            reason = "insufficient_similarity"
        else:
            reason = "insufficient_winning_margin"
    return {
        "source_label": label,
        "resolved_speaker": resolved,
        "outcome": outcome,
        "reason": reason,
        "window_count": len(windows),
        "duration_seconds": round(duration_seconds, 6),
        "agreement": round(agreement, 6),
        "votes": votes,
    }


def _episode_speakers(
    windows: list[dict[str, Any]],
    mapping: dict[str, str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        if not _embedding(window):
            continue
        label = mapping.get(
            str(window["speaker_label"]),
            str(window["speaker_label"]),
        )
        grouped.setdefault(label, []).append(window)
    speakers: list[dict[str, Any]] = []
    for label, values in sorted(grouped.items()):
        clean_values = [
            value
            for value in values
            if bool(value.get("clean", True))
        ]
        centroid_values = clean_values or values
        centroid = _centroid(
            [_embedding(value) for value in centroid_values]
        )
        representative_values = clean_values or values
        ranked: list[dict[str, Any]] = sorted(
            (
                {
                    "window_id": str(value["window_id"]),
                    "source_label": str(value["speaker_label"]),
                    "quality_score": round(
                        _window_quality_score(value),
                        6,
                    ),
                    "centroid_similarity": round(
                        cosine_similarity(
                            _embedding(value),
                            centroid,
                        ),
                        6,
                    ),
                    "duration_seconds": round(
                        float(value["end_seconds"])
                        - float(value["start_seconds"]),
                        6,
                    ),
                    "clean": bool(value.get("clean", True)),
                }
                for value in representative_values
            ),
            key=lambda item: (
                float(item["quality_score"]),
                float(item["centroid_similarity"]),
                float(item["duration_seconds"]),
                str(item["window_id"]),
            ),
            reverse=True,
        )
        selected_window_id = str(ranked[0]["window_id"])
        speakers.append(
            {
                "local_label": label,
                "embedding": list(centroid),
                "selected_reference_window_id": selected_window_id,
                "source_labels": sorted(
                    {
                        str(value["speaker_label"])
                        for value in values
                    }
                ),
                "window_count": len(values),
                "reference_selection": {
                    "selected_window_id": selected_window_id,
                    "candidates": ranked,
                },
            }
        )
    return speakers


def _window_quality_score(window: dict[str, Any]) -> float:
    if window.get("quality_score") is not None:
        return float(window["quality_score"])
    duration = max(
        0.0,
        float(window["end_seconds"])
        - float(window["start_seconds"]),
    )
    duration_score = min(duration / 6.0, 1.0)
    return duration_score if bool(window.get("clean", True)) else 0.0


def _embedding(window: dict[str, Any]) -> tuple[float, ...]:
    raw = window.get("embedding", [])
    return tuple(float(value) for value in raw)


def _centroid(values: list[tuple[float, ...]]) -> tuple[float, ...]:
    if not values:
        return ()
    dimension = len(values[0])
    mean = tuple(
        sum(value[index] for value in values) / len(values)
        for index in range(dimension)
    )
    magnitude = sqrt(sum(value * value for value in mean))
    if magnitude == 0:
        return mean
    return tuple(value / magnitude for value in mean)


def _word_signature(
    words: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    return [
        (
            str(word["text"]),
            float(word["start_seconds"]),
            float(word["end_seconds"]),
        )
        for word in words
    ]


def validate_canonical_word_invariants(
    source_words: list[dict[str, Any]],
    canonical_words: list[dict[str, Any]],
) -> None:
    """Reject any canonical result that mutates aligned lexical evidence."""

    if _word_signature(source_words) != _word_signature(canonical_words):
        raise ValueError(
            "Source Analysis Canonicalization changed aligned lexical data"
        )
