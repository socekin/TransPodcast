"""One bounded Agent execution per candidate Planner or final Reviewer role.

The judge is provider-neutral: the runner of the Processing Run's frozen
Agent provider answers, and the recorded provider identity comes from that
runner (``codex-cli`` or ``claude-code``).
"""

from collections.abc import Sequence
from typing import Any, Protocol

from transpodcast.adapters.agent import (
    AgentExecution,
    provider_identity,
    runner_provider,
)
from transpodcast.speaker_ownership_continuity import continuity_question
from transpodcast.speaker_ownership_v15 import (
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
    parse_ownership_plans,
)


class OwnershipRunner(Protocol):
    def run(
        self, *, instructions: str, source_data: dict[str, Any], schema: dict[str, Any]
    ) -> AgentExecution: ...


class AgentContinuityJudge:
    def __init__(self, runner: OwnershipRunner) -> None:
        self.runner = runner
        self.identity = provider_identity(runner_provider(runner))

    def plan(
        self, regions: Sequence[SpeakerOwnershipRegion]
    ) -> SpeakerOwnershipPlanningResult:
        return self._execute(regions)

    def review(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
        planning: SpeakerOwnershipPlanningResult,
    ) -> SpeakerOwnershipPlanningResult:
        return self._execute(regions, planning)

    def _execute(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
        planning: SpeakerOwnershipPlanningResult | None = None,
    ) -> SpeakerOwnershipPlanningResult:
        instructions, source_data, schema = continuity_question(regions, planning)
        execution = self.runner.run(
            instructions=instructions, source_data=source_data, schema=schema
        )
        try:
            plans, errors, raw = parse_ownership_plans(execution.output, regions)
        except (TypeError, ValueError) as error:
            return SpeakerOwnershipPlanningResult(
                plans={},
                error=f"{type(error).__name__}: {error}"[:500],
                input_tokens=execution.input_tokens,
                output_tokens=execution.output_tokens,
                provider=self.identity,
                model=execution.model,
                cli_version=execution.cli_version,
            )
        return SpeakerOwnershipPlanningResult(
            plans=plans,
            validation_errors=errors,
            raw_plans=raw,
            input_tokens=execution.input_tokens,
            output_tokens=execution.output_tokens,
            provider=self.identity,
            model=execution.model,
            cli_version=execution.cli_version,
        )


# Historical name of the provider-neutral judge.
CodexContinuityJudge = AgentContinuityJudge
