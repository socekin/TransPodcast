from __future__ import annotations

import inspect

import pytest

from transpodcast.adapters.agent import (
    AgentExecution,
    AgentProjectExecution,
    AgentProjectInvocationError,
    AgentProjectTurnError,
    AgentRunner,
    AgentSessionUnavailableError,
)
from transpodcast.adapters.claude import ClaudeRunner
from transpodcast.adapters.codex import (
    CodexExecution,
    CodexProjectInvocationError,
    CodexRunner,
    CodexSessionUnavailableError,
    ProjectExecution,
)

# Static conformance: mypy rejects this tuple if a runner drifts from the
# protocol; the test below checks the same contract at runtime.
RUNNERS: tuple[AgentRunner, ...] = (CodexRunner("codex"), ClaudeRunner("claude"))


@pytest.mark.parametrize(
    ("runner", "provider"),
    list(zip(RUNNERS, ("codex", "claude"), strict=True)),
)
def test_runners_conform_to_the_agent_runner_protocol(
    runner: AgentRunner,
    provider: str,
) -> None:
    assert isinstance(runner, AgentRunner)
    assert runner.provider == provider
    assert runner.cli_version == ""
    for name in ("run", "run_project"):
        expected = inspect.signature(getattr(AgentRunner, name))
        actual = inspect.signature(getattr(type(runner), name))
        assert list(actual.parameters) == list(expected.parameters)
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in list(actual.parameters.values())[1:]
        )


def test_cli_version_reflects_the_last_readiness_probe() -> None:
    runner = CodexRunner("codex")
    runner._cli_version = "codex-cli 0.160.0"  # noqa: SLF001

    assert runner.cli_version == "codex-cli 0.160.0"


def test_neutral_names_alias_the_codex_types() -> None:
    assert AgentExecution is CodexExecution
    assert AgentProjectExecution is ProjectExecution
    assert AgentSessionUnavailableError is CodexSessionUnavailableError
    assert AgentProjectInvocationError is CodexProjectInvocationError
    assert issubclass(AgentProjectTurnError, CodexProjectInvocationError)
