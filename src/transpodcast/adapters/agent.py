"""Provider-neutral boundary for Agent CLI runners.

Every Agent call in TransPodcast goes through an ``AgentRunner``: single-shot
structured questions use ``run()``, and durable Translation Project turns
use ``run_project()``. The execution and error types live here; the Codex
names in ``transpodcast.adapters.codex`` are aliases of these classes.

Agent providers and recorded identities
---------------------------------------

The Owner selects an Agent provider (``agent_provider`` setting): ``codex``
or ``claude``. Recorded artifacts (Translation Artifact ``provider``,
ownership plan ``provider``, stage rows) carry the provider's *identity*,
which names the CLI that served the call:

- ``codex`` records ``codex-cli`` (unchanged, so historical rows stay
  comparable);
- ``claude`` records ``claude-code``.

The recorded model is always the one the CLI reported serving the call.
When the CLI reports none, the runner falls back to its configured model or
to ``codex-default`` / ``claude-default``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast, runtime_checkable

AgentProvider = Literal["codex", "claude"]
AGENT_PROVIDERS: tuple[AgentProvider, ...] = ("codex", "claude")
DEFAULT_AGENT_PROVIDER: AgentProvider = "codex"
AGENT_PROVIDER_IDENTITIES: dict[str, str] = {
    "codex": "codex-cli",
    "claude": "claude-code",
}

AgentFailureKind = Literal["auth", "rate_limit", "provider", "schema"]
AGENT_FAILURE_KINDS: tuple[AgentFailureKind, ...] = (
    "auth",
    "rate_limit",
    "provider",
    "schema",
)


def provider_identity(provider: str | None) -> str:
    """The recorded identity of an Agent provider; NULL means Codex."""
    resolved = provider or DEFAULT_AGENT_PROVIDER
    return AGENT_PROVIDER_IDENTITIES.get(resolved, resolved)


def runner_provider(runner: object) -> str:
    """The provider of a runner; test doubles without one act as Codex."""
    provider = getattr(runner, "provider", None)
    return str(provider) if provider else DEFAULT_AGENT_PROVIDER


@dataclass(frozen=True)
class AgentExecution:
    output: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    cli_version: str = ""
    session_id: str = ""


@dataclass(frozen=True)
class AgentProjectExecution:
    session_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cli_version: str = ""
    events_jsonl: str = ""
    # Codex reports session-cumulative usage on every resumed turn; Claude
    # Code reports usage per invocation. Turn accounting depends on it.
    usage_is_cumulative: bool = False


class AgentSessionUnavailableError(RuntimeError):
    """A recorded non-interactive Agent session cannot be resumed."""

    def __init__(
        self,
        message: str,
        *,
        execution: AgentProjectExecution | None = None,
    ) -> None:
        super().__init__(message)
        self.execution = execution


class AgentProjectInvocationError(RuntimeError):
    """A project turn failed after the Agent emitted auditable usage."""

    def __init__(
        self,
        message: str,
        *,
        execution: AgentProjectExecution,
    ) -> None:
        super().__init__(message)
        self.execution = execution


class AgentInvocationError(RuntimeError):
    """A single-shot Agent call failed with a classified cause.

    ``execution`` is present when the provider answered and therefore
    reported real usage, for example when its answer violated the schema.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: AgentFailureKind,
        execution: AgentExecution | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: AgentFailureKind = kind
        self.execution = execution

    @property
    def transient(self) -> bool:
        return self.kind == "rate_limit"


class AgentProjectTurnError(AgentProjectInvocationError):
    """A Translation Project turn failed with a classified cause.

    It subclasses the project invocation error so the Translation Project
    loop records its usage and fails the project the same way.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: AgentFailureKind,
        execution: AgentProjectExecution,
    ) -> None:
        super().__init__(message, execution=execution)
        self.kind: AgentFailureKind = kind

    @property
    def transient(self) -> bool:
        return self.kind == "rate_limit"


def agent_failure_kind(error: BaseException) -> AgentFailureKind | None:
    """The classified Agent failure kind carried by an error, if any."""
    kind = getattr(error, "kind", None)
    if kind in AGENT_FAILURE_KINDS:
        return cast(AgentFailureKind, kind)
    return None


@runtime_checkable
class AgentRunner(Protocol):
    """One local Agent CLI that TransPodcast can invoke non-interactively."""

    @property
    def provider(self) -> str: ...

    @property
    def cli_version(self) -> str: ...

    def readiness(self) -> bool: ...

    def run(
        self,
        *,
        instructions: str,
        source_data: dict[str, Any],
        schema: dict[str, Any],
    ) -> AgentExecution: ...

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> AgentProjectExecution: ...
