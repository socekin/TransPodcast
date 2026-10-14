from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from tests.test_speaker_ownership_continuity import draft, example
from transpodcast.ownership_continuity_completion import (
    complete_continuity_ownership,
    overlay_reviewed_ownership,
    question_fingerprint,
)
from transpodcast.speaker_ownership_continuity import expand_continuity_regions
from transpodcast.speaker_ownership_v15 import (
    SpeakerOwnershipPlan,
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
)


@pytest.mark.parametrize("review_state", ["missing", "failed", "invalid", "keep"])
def test_edge_review_cannot_revert_an_accepted_pass_without_a_new_assignment(
    review_state: str,
) -> None:
    segments, region = example()
    regions = expand_continuity_regions(segments, (region,), (), {})

    class EdgeJudge(Judge):
        def review(self, selected, planning):
            if review_state == "failed":
                raise RuntimeError("Reviewer unavailable")
            if review_state == "missing":
                return SpeakerOwnershipPlanningResult(plans={})
            if review_state == "invalid":
                return replace(draft(), validation_errors={region.region_id: "invalid"})
            return super().review(selected, planning)

    result = complete_continuity_ownership(
        segments, regions, EdgeJudge(), raw_turns=(), speaker_mapping={}
    )
    accepted = ("a",) * sum(len(s.words) for s in segments)
    assert overlay_reviewed_ownership(accepted, result.application) == accepted


class Judge:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_planner = False

    def plan(
        self, regions: Sequence[SpeakerOwnershipRegion]
    ) -> SpeakerOwnershipPlanningResult:
        self.calls.append("planner")
        if self.fail_planner:
            raise RuntimeError("Planner unavailable")
        return draft()

    def review(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
        planning: SpeakerOwnershipPlanningResult,
    ) -> SpeakerOwnershipPlanningResult:
        self.calls.append("reviewer")
        assert planning.plans or planning.error
        return SpeakerOwnershipPlanningResult(
            plans={
                r.region_id: SpeakerOwnershipPlan(
                    "keep", rationale="Retain genuine handoffs."
                )
                for r in regions
            }
        )


def test_resume_after_planner_calls_only_reviewer_and_keeps_rejected_draft() -> None:
    segments, region = example()
    regions = expand_continuity_regions(segments, (region,), (), {})
    saved: list[dict[str, Any]] = []

    def interrupt(checkpoint: dict[str, Any]) -> None:
        saved.append(checkpoint)
        raise InterruptedError("Worker interrupted after durable Planner save")

    judge = Judge()
    with pytest.raises(InterruptedError):
        complete_continuity_ownership(
            segments,
            regions,
            judge,
            raw_turns=(),
            speaker_mapping={},
            checkpoint_writer=interrupt,
        )
    assert judge.calls == ["planner"]

    resumed = Judge()
    result = complete_continuity_ownership(
        segments,
        regions,
        resumed,
        raw_turns=(),
        speaker_mapping={},
        checkpoint=saved[-1],
    )
    assert resumed.calls == ["reviewer"]
    assert result.application.changed_word_count == 0
    assert result.executed_call_count == 1
    assert result.resumed_call_count == 1

    cached = Judge()
    replay = complete_continuity_ownership(
        segments,
        regions,
        cached,
        raw_turns=(),
        speaker_mapping={},
        checkpoint=result.checkpoint,
    )
    assert cached.calls == []
    assert replay.application == result.application


def test_reviewer_checkpoint_is_bound_to_the_exact_planner_draft() -> None:
    segments, region = example()
    regions = expand_continuity_regions(segments, (region,), (), {})
    initial = complete_continuity_ownership(
        segments,
        regions,
        Judge(),
        raw_turns=(),
        speaker_mapping={},
    )
    changed = deepcopy(initial.checkpoint)
    entry = next(iter(changed["batches"].values()))["planner"]
    entry["result"]["plans"][0]["rationale"] = (
        "A revised interpretation of the same words."
    )
    entry["response_sha256"] = question_fingerprint(entry["result"])
    judge = Judge()
    result = complete_continuity_ownership(
        segments,
        regions,
        judge,
        raw_turns=(),
        speaker_mapping={},
        checkpoint=changed,
    )
    assert judge.calls == ["reviewer"]
    assert result.resumed_call_count == 1


def test_changed_immutable_evidence_invalidates_both_cached_calls() -> None:
    segments, region = example()
    regions = expand_continuity_regions(segments, (region,), (), {})
    initial = complete_continuity_ownership(
        segments,
        regions,
        Judge(),
        raw_turns=(),
        speaker_mapping={},
    )
    changed = (replace(regions[0], context_after=("New", "context")),)
    judge = Judge()
    complete_continuity_ownership(
        segments,
        changed,
        judge,
        raw_turns=(),
        speaker_mapping={},
        checkpoint=initial.checkpoint,
    )
    assert judge.calls == ["planner", "reviewer"]


def test_failed_planner_uses_the_budgeted_reviewer_without_an_extra_retry() -> None:
    segments, region = example()
    regions = expand_continuity_regions(segments, (region,), (), {})
    judge = Judge()
    judge.fail_planner = True
    result = complete_continuity_ownership(
        segments,
        regions,
        judge,
        raw_turns=(),
        speaker_mapping={},
    )
    assert judge.calls == ["planner", "reviewer"]
    assert result.application.changed_word_count == 0
    assert result.application.records[0].verdict == "planner-degraded"


def test_preserved_oversized_regions_do_not_consume_provider_calls() -> None:
    segments, region = example()
    regions = expand_continuity_regions(
        segments, (region,), (), {}, maximum_characters=50
    )
    judge = Judge()
    result = complete_continuity_ownership(
        segments,
        regions,
        judge,
        raw_turns=(),
        speaker_mapping={},
        maximum_characters=50,
    )
    assert judge.calls == []
    assert result.application.changed_word_count == 0
    assert result.application.records[0].verdict == "provider-degraded"
