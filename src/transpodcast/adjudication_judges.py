"""Production judges for Boundary Ownership Adjudication.

The voiceprint judge scores a disputed fragment's TitaNet embedding
against the permissible Speakers' consolidated centroids and abstains
when the fragment is too short to measure reliably. The discourse judge
asks the Translation Agent one text-only continuity question per episode
(batched) through the same schema-constrained execution used for
translation; any provider failure becomes an abstention, never a changed
assignment.

Both judges are exposed as pure measurement functions plus dict-backed
adapters for the verdict engine, so tests and Modal calibration replay
recorded evidence without providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from transpodcast.boundary_adjudication import (
    CONTINUITY_ADJUDICATION_POLICY_VERSION,
    DiscourseVerdict,
    OwnershipDispute,
    VoiceprintVerdict,
)
from transpodcast.similarity import cosine_similarity

MINIMUM_FRAGMENT_SECONDS = 0.3
# Sub-second fragments can embed as noise; a measurement whose best
# score is below this floor is no evidence at all, so the judge
# abstains instead of casting a noise vote (historical calibration: the
# 0.32s "right And" fragment scored 0.04 vs 0.13).
MINIMUM_CONFIDENCE_SCORE = 0.35

# Structured output rejects any object schema without an explicit
# additionalProperties: false (discovered during calibration).
DISCOURSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "group_id": {"type": "string"},
                    "verdicts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "dispute_id": {"type": "string"},
                                "owner": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                            "required": [
                                "dispute_id",
                                "owner",
                                "rationale",
                            ],
                        },
                    },
                },
                "required": ["group_id", "verdicts"],
            },
        }
    },
    "required": ["groups"],
}

DISCOURSE_INSTRUCTIONS = (
    "You are judging source-transcript Speaker ownership for a dubbed "
    "podcast. For each dispute, select exactly one permissible owner "
    "label from candidate_owners, or answer owner: unsure when the text "
    "alone cannot decide. A standalone acknowledgement like 'yeah' can "
    "be either, so do not guess. Judge only from the source text. Do not "
    "translate or rewrite it. Answer every dispute_id."
    " Return one complete verdict group for every input group_id."
)

CONTINUITY_DISCOURSE_INSTRUCTIONS = (
    "You are judging source-transcript Speaker ownership for a dubbed "
    "podcast. The supplied current_owner is an untrusted acoustic and "
    "segmentation hypothesis, not a default answer. For every dispute, "
    "compare the current split with each supplied owner reconstruction "
    "before selecting exactly one label from candidate_owners, or answer "
    "owner: unsure when the source text alone cannot decide. Prefer clear "
    "grammatical and semantic sentence continuity, especially when the "
    "disputed words supply the missing beginning of an incomplete adjacent "
    "sentence. Retain an interrupted or abandoned thought only when the "
    "supplied source text affirmatively supports it. A Speaker-label change "
    "alone is not evidence of an interruption. Never invent a backchannel, "
    "interruption, false start, or restarted answer to preserve the current "
    "label. Judge only from the source text. Do not translate, rewrite, add, "
    "remove, or reorder words. Answer every dispute_id and return one "
    "complete verdict group for every input group_id."
)


def measure_fragment_voiceprints(
    disputes: list[OwnershipDispute],
    fragment_embeddings: dict[str, tuple[float, ...]],
    centroids: dict[str, tuple[float, ...]],
    *,
    minimum_fragment_seconds: float = MINIMUM_FRAGMENT_SECONDS,
    minimum_confidence_score: float = MINIMUM_CONFIDENCE_SCORE,
) -> dict[str, VoiceprintVerdict | None]:
    verdicts: dict[str, VoiceprintVerdict | None] = {}
    for dispute in disputes:
        verdicts[dispute.dispute_id] = _measure_one(
            dispute,
            fragment_embeddings.get(dispute.dispute_id, ()),
            centroids,
            minimum_fragment_seconds=minimum_fragment_seconds,
            minimum_confidence_score=minimum_confidence_score,
        )
    return verdicts


def _measure_one(
    dispute: OwnershipDispute,
    embedding: tuple[float, ...],
    centroids: dict[str, tuple[float, ...]],
    *,
    minimum_fragment_seconds: float,
    minimum_confidence_score: float,
) -> VoiceprintVerdict | None:
    span = dispute.end_seconds - dispute.start_seconds
    if span < minimum_fragment_seconds - 1e-9:
        return None
    if not embedding:
        return None
    scores: dict[str, float] = {}
    for label in dispute.permissible_owners:
        centroid = centroids.get(label)
        if not centroid:
            continue
        scores[label] = round(
            cosine_similarity(list(embedding), list(centroid)),
            6,
        )
    if len(scores) < 2:
        return None
    if max(scores.values()) < minimum_confidence_score:
        return None
    owner = max(scores, key=lambda label: scores[label])
    return VoiceprintVerdict(owner=owner, scores=scores)


def discourse_question(
    disputes: list[OwnershipDispute],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """One batched, schema-constrained question for the episode."""
    grouped = _group_disputes(disputes)
    source_data = {
        "groups": [
            {
                "group_id": group_id,
                "context_before_speaker": (
                    items[0].context_before_owner
                ),
                "context_before": " ".join(items[0].context_before),
                "candidates": [
                    {
                        "dispute_id": dispute.dispute_id,
                        "current_owner": dispute.current_owner,
                        "candidate_owners": list(
                            dispute.permissible_owners
                        ),
                        "trigger_reasons": list(
                            dispute.trigger_reasons
                        ),
                        "context_before_speaker": (
                            dispute.context_before_owner
                        ),
                        "context_before": " ".join(
                            dispute.context_before
                        ),
                        "disputed_words": " ".join(
                            str(word["text"])
                            for word in dispute.words
                        ),
                        "context_after_speaker": (
                            dispute.context_after_owner
                        ),
                        "context_after": " ".join(
                            dispute.context_after
                        ),
                        **(
                            _continuity_comparison(dispute)
                            if dispute.question_policy_version
                            == CONTINUITY_ADJUDICATION_POLICY_VERSION
                            else {}
                        ),
                    }
                    for dispute in items
                ],
                "context_after_speaker": items[-1].context_after_owner,
                "context_after": " ".join(items[-1].context_after),
            }
            for group_id, items in grouped.items()
        ]
    }
    instructions = (
        CONTINUITY_DISCOURSE_INSTRUCTIONS
        if any(
            dispute.question_policy_version
            == CONTINUITY_ADJUDICATION_POLICY_VERSION
            for dispute in disputes
        )
        else DISCOURSE_INSTRUCTIONS
    )
    return instructions, source_data, DISCOURSE_SCHEMA


def _continuity_comparison(dispute: OwnershipDispute) -> dict[str, Any]:
    disputed_text = " ".join(str(word["text"]) for word in dispute.words)
    before_text = " ".join(dispute.context_before)
    after_text = " ".join(dispute.context_after)
    reconstructions: dict[str, str] = {}
    for owner in dispute.permissible_owners:
        parts: list[str] = []
        if owner == dispute.context_before_owner and before_text:
            parts.append(before_text)
        parts.append(disputed_text)
        if owner == dispute.context_after_owner and after_text:
            parts.append(after_text)
        reconstructions[owner] = " ".join(parts)
    return {
        "current_owner_is_untrusted": True,
        "current_split": {
            "before_speaker": dispute.context_before_owner,
            "before_text": before_text,
            "current_speaker": dispute.current_owner,
            "current_text": disputed_text,
            "after_speaker": dispute.context_after_owner,
            "after_text": after_text,
        },
        "owner_reconstructions": reconstructions,
    }


def parse_discourse_verdicts(
    payload: dict[str, Any] | None,
    disputes: list[OwnershipDispute],
) -> dict[str, DiscourseVerdict | None]:
    """Map provider verdicts back to Speakers; anything missing or
    malformed is an abstention for that dispute."""
    by_id = {dispute.dispute_id: dispute for dispute in disputes}
    verdicts: dict[str, DiscourseVerdict | None] = {
        dispute.dispute_id: None for dispute in disputes
    }
    if not payload:
        return verdicts
    if "groups" in payload:
        return _parse_grouped_discourse_verdicts(payload, disputes)
    raw_verdicts = payload.get("verdicts", [])
    if not isinstance(raw_verdicts, list):
        raise ValueError("Discourse answer verdicts must be a list")
    return _parse_verdict_items(raw_verdicts, by_id)


def _parse_grouped_discourse_verdicts(
    payload: dict[str, Any],
    disputes: list[OwnershipDispute],
) -> dict[str, DiscourseVerdict | None]:
    expected_groups = _group_disputes(disputes)
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        raise ValueError("Discourse answer groups must be a list")
    verdicts: dict[str, DiscourseVerdict | None] = {}
    seen_groups: set[str] = set()
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            raise ValueError("Discourse answer group must be an object")
        group_id = str(raw_group.get("group_id", ""))
        expected = expected_groups.get(group_id)
        if expected is None:
            raise ValueError(
                f"Discourse answer contains unknown group_id: {group_id}"
            )
        if group_id in seen_groups:
            verdicts.update(
                _group_abstention(expected, "Duplicate verdict group.")
            )
            continue
        seen_groups.add(group_id)
        by_id = {dispute.dispute_id: dispute for dispute in expected}
        raw_verdicts = raw_group.get("verdicts")
        try:
            if not isinstance(raw_verdicts, list):
                raise ValueError("Group verdicts must be a list")
            verdicts.update(_parse_verdict_items(raw_verdicts, by_id))
        except (AttributeError, TypeError, ValueError) as error:
            verdicts.update(
                _group_abstention(
                    expected,
                    f"Invalid or incomplete verdict group: {error}",
                )
            )
    for group_id, expected in expected_groups.items():
        if group_id not in seen_groups:
            verdicts.update(
                _group_abstention(expected, "Missing verdict group.")
            )
    return verdicts


def _parse_verdict_items(
    raw_verdicts: list[Any],
    by_id: dict[str, OwnershipDispute],
) -> dict[str, DiscourseVerdict | None]:
    verdicts: dict[str, DiscourseVerdict | None] = {}
    seen: set[str] = set()
    for item in raw_verdicts:
        if not isinstance(item, dict):
            raise ValueError("Discourse verdict must be an object")
        dispute_id = str(item.get("dispute_id", ""))
        dispute = by_id.get(dispute_id)
        if dispute is None:
            raise ValueError(
                f"Discourse answer contains unknown dispute_id: {dispute_id}"
            )
        if dispute_id in seen:
            raise ValueError(
                f"Discourse answer contains duplicate dispute_id: {dispute_id}"
            )
        seen.add(dispute_id)
        verdicts[dispute_id] = _parse_verdict_item(item, dispute)
    missing = sorted(set(by_id) - seen)
    if missing:
        raise ValueError(
            "Discourse answer is missing verdicts for: "
            + ", ".join(missing)
        )
    return verdicts


def _parse_verdict_item(
    item: dict[str, Any],
    dispute: OwnershipDispute,
) -> DiscourseVerdict:
    if "owner" in item:
        selected = str(item.get("owner", ""))
        if selected == "unsure":
            owner = None
        elif selected in dispute.permissible_owners:
            owner = selected
        else:
            raise ValueError(
                "Discourse answer contains an impermissible owner: "
                f"{selected}"
            )
    else:
        verdict = str(item.get("verdict", ""))
        if verdict == "continuation":
            owner = dispute.counterpart
        elif verdict == "interjection":
            owner = dispute.current_owner
        elif verdict == "unsure":
            owner = None
        else:
            raise ValueError(
                f"Discourse answer contains invalid verdict: {verdict}"
            )
    return DiscourseVerdict(
        owner=owner,
        rationale=str(item.get("rationale", "")).strip()
        or "No rationale supplied.",
    )


def _group_abstention(
    disputes: list[OwnershipDispute],
    rationale: str,
) -> dict[str, DiscourseVerdict]:
    return {
        dispute.dispute_id: DiscourseVerdict(
            owner=None,
            rationale=rationale,
        )
        for dispute in disputes
    }


def _group_disputes(
    disputes: list[OwnershipDispute],
) -> dict[str, list[OwnershipDispute]]:
    grouped: dict[str, list[OwnershipDispute]] = {}
    for dispute in disputes:
        grouped.setdefault(
            dispute.group_id or dispute.dispute_id, []
        ).append(dispute)
    return grouped


@dataclass(frozen=True)
class MappedVoiceprintJudge:
    verdicts: dict[str, VoiceprintVerdict | None]

    def measure(
        self, dispute: OwnershipDispute
    ) -> VoiceprintVerdict | None:
        return self.verdicts.get(dispute.dispute_id)


@dataclass(frozen=True)
class MappedDiscourseJudge:
    verdicts: dict[str, DiscourseVerdict | None]

    def judge(
        self, dispute: OwnershipDispute
    ) -> DiscourseVerdict | None:
        return self.verdicts.get(dispute.dispute_id)
