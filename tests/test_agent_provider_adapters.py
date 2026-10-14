"""Provider-neutral adapters: identities, project binding, usage, errors."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.test_speaker_ownership_continuity import example
from tests.test_translation_project import ProgressiveProjectRunner, _candidate
from transpodcast.adapters.agent import (
    AgentExecution,
    AgentInvocationError,
    AgentProjectExecution,
    AgentProjectTurnError,
    provider_identity,
    runner_provider,
)
from transpodcast.adapters.claude import (
    REQUIRED_HELP_FLAGS,
    ClaudeRunner,
    isolated_environment,
)
from transpodcast.adapters.codex import (
    AgentTranslationAdapter,
    AgentTriageAdapter,
    CodexRunner,
    CodexTranslationAdapter,
    CodexTriageAdapter,
    _normalize_project_usage_accounting,
    classify_codex_failure,
)
from transpodcast.adapters.codex_ownership_continuity import (
    AgentContinuityJudge,
    CodexContinuityJudge,
)
from transpodcast.adjudication_completion import complete_boundary_adjudication
from transpodcast.dependencies import AgentProviderNotConfiguredError
from transpodcast.domain import SourceAnalysisResult, TranslationResult
from transpodcast.errors import (
    TranslationProviderError,
    TranslationProviderMismatchError,
)
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V17
from transpodcast.speaker_ownership_v15 import SpeakerOwnershipPlanningResult
from transpodcast.testing import AcceptanceHarness, FakeTranslationAdapter
from transpodcast.translation_projects import (
    allow_translation_project_provider_rebind,
    mark_translation_project_reprocess,
)


class ClaudeProjectRunner(ProgressiveProjectRunner):
    provider = "claude"

    def __init__(self, *, per_turn_tokens: tuple[int, ...] = ()) -> None:
        super().__init__()
        self.per_turn_tokens = per_turn_tokens
        self.turns = 0

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> AgentProjectExecution:
        execution = super().run_project(
            root=root,
            instructions=instructions,
            session_id=session_id,
        )
        self.turns += 1
        tokens = (
            self.per_turn_tokens[self.turns - 1]
            if self.turns <= len(self.per_turn_tokens)
            else execution.input_tokens
        )
        return AgentProjectExecution(
            session_id=f"claude-{execution.session_id}",
            model=f"claude-{execution.model}",
            input_tokens=tokens,
            output_tokens=tokens // 5,
            cli_version="2.1.296 (Claude Code)",
            events_jsonl="",
            usage_is_cumulative=False,
        )


class InterruptedCodexRunner(ProgressiveProjectRunner):
    """Authors two targets, then fails the next Author turn."""

    provider = "codex"

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> AgentProjectExecution:
        if root.name == "author" and session_id is not None:
            raise AgentProjectTurnError(
                "Codex CLI failed: usage limit reached",
                kind="rate_limit",
                execution=AgentProjectExecution(
                    session_id=session_id,
                    model="author-fixture",
                    input_tokens=10,
                    output_tokens=2,
                    usage_is_cumulative=True,
                ),
            )
        return super().run_project(
            root=root,
            instructions=instructions,
            session_id=session_id,
        )


def _project_state(root: Path) -> dict[str, Any]:
    state_path = next(root.glob("run-88/*/project.json"))
    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_provider_identities_keep_codex_history_and_name_claude_code() -> None:
    assert provider_identity("codex") == "codex-cli"
    assert provider_identity(None) == "codex-cli"
    assert provider_identity("claude") == "claude-code"
    assert runner_provider(CodexRunner("codex")) == "codex"
    assert runner_provider(ClaudeRunner("claude")) == "claude"
    assert runner_provider(object()) == "codex"
    assert CodexTranslationAdapter is AgentTranslationAdapter
    assert CodexTriageAdapter is AgentTriageAdapter
    assert CodexContinuityJudge is AgentContinuityJudge


def test_claude_translation_project_records_provider_and_identity(
    tmp_path: Path,
) -> None:
    result = AgentTranslationAdapter(
        ClaudeProjectRunner(),  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate())

    assert result.provider == "claude-code"
    assert result.cli_version == "2.1.296 (Claude Code)"
    audit = json.loads(result.audit_json)
    assert audit["provider"] == "claude-code"
    assert audit["cli_version"] == "2.1.296 (Claude Code)"
    assert "codex_cli_version" not in audit
    state = _project_state(tmp_path)
    assert state["provider"] == "claude"
    assert {turn["provider"] for turn in state["turns"]} == {"claude"}


def test_codex_translation_project_keeps_the_codex_cli_identity(
    tmp_path: Path,
) -> None:
    result = AgentTranslationAdapter(
        ProgressiveProjectRunner(),  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate())

    assert result.provider == "codex-cli"
    assert _project_state(tmp_path)["provider"] == "codex"


def test_claude_usage_is_summed_per_invocation(tmp_path: Path) -> None:
    # Two Author turns in one session, then one Reviewer turn. Claude Code
    # reports usage per invocation, so the second Author turn's 250 tokens
    # are not a cumulative total to subtract from.
    result = AgentTranslationAdapter(
        ClaudeProjectRunner(per_turn_tokens=(100, 250, 50)),  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate())

    assert result.input_tokens == 400
    assert result.output_tokens == 20 + 50 + 10
    turns = _project_state(tmp_path)["turns"]
    assert [turn["input_tokens"] for turn in turns] == [100, 250, 50]
    assert [turn["usage_is_cumulative"] for turn in turns] == [False] * 3


def test_usage_normalizer_sums_explicit_per_invocation_turns() -> None:
    state: dict[str, Any] = {
        "turns": [
            {
                "role": "author",
                "session_id": "claude-session",
                "input_tokens": 100,
                "output_tokens": 10,
                "usage_is_cumulative": False,
            },
            {
                "role": "author",
                "session_id": "claude-session",
                "input_tokens": 250,
                "output_tokens": 30,
                "usage_is_cumulative": False,
            },
        ],
    }

    assert _normalize_project_usage_accounting(state) is True
    assert state["input_tokens"] == 350
    assert state["output_tokens"] == 40


def test_project_never_resumes_another_providers_session(tmp_path: Path) -> None:
    project_root = tmp_path / "translation-projects"
    with pytest.raises(TranslationProviderError) as interrupted:
        AgentTranslationAdapter(
            InterruptedCodexRunner(),  # type: ignore[arg-type]
            project_root=project_root,
        ).translate(_candidate())
    assert interrupted.value.kind == "rate_limit"
    assert _project_state(project_root)["author_sessions"] == ["author-session"]

    claude = ClaudeProjectRunner()
    with pytest.raises(TranslationProviderMismatchError, match="reprocess"):
        AgentTranslationAdapter(
            claude,  # type: ignore[arg-type]
            project_root=project_root,
        ).translate(_candidate())
    assert claude.invocations == []
    assert _project_state(project_root)["provider"] == "codex"


def test_explicit_reprocess_starts_fresh_sessions_for_a_new_provider(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "translation-projects"
    with pytest.raises(TranslationProviderError):
        AgentTranslationAdapter(
            InterruptedCodexRunner(),  # type: ignore[arg-type]
            project_root=project_root,
        ).translate(_candidate())

    allow_translation_project_provider_rebind(tmp_path, 88)
    claude = ClaudeProjectRunner()
    result = AgentTranslationAdapter(
        claude,  # type: ignore[arg-type]
        project_root=project_root,
    ).translate(_candidate())

    assert claude.invocations[0] == ("author", None)
    assert result.provider == "claude-code"
    state = _project_state(project_root)
    assert state["provider"] == "claude"
    assert "provider_rebind_allowed" not in state
    assert state["retired_sessions"][0]["session_id"] == "author-session"
    assert state["retired_sessions"][0]["provider"] == "codex"
    assert "author-session" not in state["author_sessions"]
    # The two Codex-authored targets were kept; Claude authored the rest.
    assert result.segments[:2] == ("译文第1段。", "译文第2段。")


def test_reprocess_with_the_same_provider_keeps_author_targets(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "translation-projects"
    first = AgentTranslationAdapter(
        ClaudeProjectRunner(),  # type: ignore[arg-type]
        project_root=project_root,
    ).translate(_candidate())

    allow_translation_project_provider_rebind(tmp_path, 88)
    mark_translation_project_reprocess(tmp_path, 88, first.segments)
    second_runner = ClaudeProjectRunner()
    AgentTranslationAdapter(
        second_runner,  # type: ignore[arg-type]
        project_root=project_root,
    ).translate(_candidate())

    assert second_runner.invocations == [("review", None)]
    assert "retired_sessions" not in _project_state(project_root)


class FixedOwnershipRunner:
    def __init__(self, provider: str, error: Exception | None = None) -> None:
        self.provider = provider
        self.error = error
        self.calls = 0

    def run(
        self,
        *,
        instructions: str,
        source_data: dict[str, Any],
        schema: dict[str, Any],
    ) -> AgentExecution:
        del instructions, schema
        self.calls += 1
        if self.error is not None:
            raise self.error
        return AgentExecution(
            output={
                "plans": [
                    {
                        "region_id": region["region_id"],
                        "action": "keep",
                        "assignments": [],
                        "rationale": "Keep the recorded turns.",
                    }
                    for region in source_data["regions"]
                ]
            },
            model="claude-opus-5-5",
            input_tokens=900,
            output_tokens=40,
            cli_version="2.1.296 (Claude Code)",
        )


def test_continuity_judge_records_the_runner_identity() -> None:
    _, region = example()

    claude = AgentContinuityJudge(FixedOwnershipRunner("claude")).plan([region])
    codex = AgentContinuityJudge(FixedOwnershipRunner("codex")).plan([region])

    assert claude.provider == "claude-code"
    assert claude.model == "claude-opus-5-5"
    assert codex.provider == "codex-cli"


def _v17_analysis() -> SourceAnalysisResult:
    segments, _ = example()
    return SourceAnalysisResult(
        segments=segments,
        pipeline_version=SPEECH_PIPELINE_V17,
        diarization_model="fixture",
        transcription_model="fixture",
        alignment_model="fixture",
    )


@pytest.mark.parametrize("kind", ["rate_limit", "auth"])
def test_ownership_stops_on_rate_limit_and_auth_failures(kind: str) -> None:
    runner = FixedOwnershipRunner(
        "claude",
        AgentInvocationError(f"Claude Code CLI failed ({kind})", kind=kind),  # type: ignore[arg-type]
    )
    adapter = AgentTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=Path("unused"),
    )

    with pytest.raises(AgentInvocationError) as raised:
        complete_boundary_adjudication(_v17_analysis(), adapter)
    assert raised.value.kind == kind


def test_ownership_degrades_a_batch_on_other_provider_failures() -> None:
    runner = FixedOwnershipRunner(
        "claude",
        AgentInvocationError("Claude Code CLI failed (provider)", kind="provider"),
    )
    adapter = AgentTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=Path("unused"),
    )

    completion = complete_boundary_adjudication(_v17_analysis(), adapter)

    assert completion.completed
    assert runner.calls >= 1


def test_codex_failures_are_classified() -> None:
    assert classify_codex_failure("ERROR: You've hit your usage limit") == (
        "rate_limit"
    )
    assert classify_codex_failure("stream error: 429 Too Many Requests") == (
        "rate_limit"
    )
    assert classify_codex_failure("Not logged in. Please run codex login") == (
        "auth"
    )
    assert classify_codex_failure("provider timeout") == "provider"


def test_codex_runner_raises_classified_agent_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = CodexRunner("codex")
    monkeypatch.setattr(runner, "readiness", lambda: True)
    monkeypatch.setattr(
        "transpodcast.adapters.codex.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout='{"type":"thread.started","thread_id":"session-9"}\n',
            stderr="ERROR: rate limit exceeded, try again later",
        ),
    )

    with pytest.raises(AgentInvocationError) as single:
        runner.run(instructions="Q", source_data={}, schema={"type": "object"})
    assert single.value.kind == "rate_limit"
    assert single.value.transient is True

    with pytest.raises(AgentProjectTurnError) as project:
        runner.run_project(root=tmp_path, instructions="A", session_id=None)
    assert project.value.kind == "rate_limit"
    assert project.value.execution.session_id == "session-9"


def test_codex_runner_records_the_observed_model_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CodexRunner("codex", model="configured-model")
    monkeypatch.setattr(runner, "readiness", lambda: True)

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"type":"turn.completed","model":"gpt-observed"}\n',
            stderr="",
        )

    monkeypatch.setattr("transpodcast.adapters.codex.subprocess.run", fake_run)

    execution = runner.run(instructions="Q", source_data={}, schema={})

    assert execution.model == "gpt-observed"


def test_claude_environment_uses_the_subscription_and_pins_the_cli() -> None:
    environment = isolated_environment(
        # pragma: allowlist nextline secret
        {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-test"}
    )

    assert "ANTHROPIC_API_KEY" not in environment
    assert environment["DISABLE_AUTOUPDATER"] == "1"


def test_claude_readiness_reports_the_auth_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = ClaudeRunner("claude")
    monkeypatch.setattr(
        "transpodcast.adapters.claude.shutil.which", lambda _: "/bin/claude"
    )
    help_text = " ".join(REQUIRED_HELP_FLAGS)

    def probe(arguments: list[str], environment: dict[str, str]) -> Any:
        del environment
        outputs = {
            "--version": "2.1.296 (Claude Code)",
            "auth": json.dumps({"loggedIn": True, "authMethod": "claude.ai"}),
            "--help": help_text,
        }
        return subprocess.CompletedProcess(
            arguments, 0, stdout=outputs[arguments[0]], stderr=""
        )

    monkeypatch.setattr(runner, "_probe", probe)

    assert runner.readiness() is True
    assert runner.auth_method == "claude.ai"
    status = AgentTranslationAdapter(runner, project_root=Path("unused"))
    assert status.provider_status() == {
        "ready": True,
        "identity": "claude-code",
        "cli_version": "2.1.296 (Claude Code)",
        "auth_method": "claude.ai",
    }


def test_dependencies_resolve_adapters_by_provider(tmp_path: Path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    dependencies = harness.dependencies

    assert dependencies.translation_for(None) is harness.codex
    assert dependencies.translation_for("codex") is harness.codex
    assert dependencies.translation_for("claude") is harness.claude
    assert dependencies.configured_agent_providers() == ("codex", "claude")
    with pytest.raises(AgentProviderNotConfiguredError):
        dependencies.translation_for("gemini")


def test_fake_adapter_records_its_provider() -> None:
    fake = FakeTranslationAdapter(provider="fake-claude")
    result = fake.translate(_candidate())

    assert isinstance(result, TranslationResult)
    assert result.provider == "fake-claude"
    assert isinstance(
        fake.plan_ownership_continuity([example()[1]]),
        SpeakerOwnershipPlanningResult,
    )
    assert fake.ownership_invocation_count == 1
