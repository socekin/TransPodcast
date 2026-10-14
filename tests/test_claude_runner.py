from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from transpodcast.adapters.agent import (
    AgentInvocationError,
    AgentProjectTurnError,
    AgentSessionUnavailableError,
)
from transpodcast.adapters.claude import (
    ISOLATION_FLAGS,
    PROJECT_ALLOWED_COMMANDS,
    PROJECT_TOOLS,
    REQUIRED_HELP_FLAGS,
    ClaudeRunner,
    classify_failure,
    envelope_model,
    envelope_output,
    envelope_usage,
    isolated_environment,
    parse_envelope,
)
from transpodcast.adapters.codex import (
    CodexProjectInvocationError,
    CodexSessionUnavailableError,
)

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply"],
    "properties": {"reply": {"type": "string"}},
}


def fixture(name: str) -> dict[str, Any]:
    loaded = json.loads(
        (FIXTURES / f"claude-envelope-{name}.json").read_text(encoding="utf-8")
    )
    assert isinstance(loaded, dict)
    return loaded


def ready_runner(model: str = "") -> ClaudeRunner:
    runner = ClaudeRunner("claude", model=model)
    runner._cli_version = "2.1.296 (Claude Code)"  # noqa: SLF001
    runner._ready = True  # noqa: SLF001
    runner._readiness_checked_at = float("inf")  # noqa: SLF001
    return runner


class RecordedCli:
    """Replays one envelope per call and records every invocation."""

    def __init__(self, *responses: tuple[int, str, str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        cwd = Path(kwargs["cwd"])
        self.calls.append(
            {
                "command": command,
                "cwd": cwd,
                "cwd_entries": sorted(path.name for path in cwd.iterdir()),
                **kwargs,
            }
        )
        returncode, stdout, stderr = self.responses.pop(0)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def flag_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_single_shot_argv_is_isolated_toolless_and_ephemeral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = RecordedCli((0, json.dumps(fixture("single-shot")), ""))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    ready_runner(model="haiku").run(
        instructions="Reply.",
        source_data={"text": "Hello."},
        schema=SCHEMA,
    )

    call = cli.calls[0]
    command = call["command"]
    assert command[:4] == ["claude", "--print", "--output-format", "json"]
    assert json.loads(flag_value(command, "--json-schema")) == SCHEMA
    assert "--no-session-persistence" in command
    assert flag_value(command, "--permission-prompts") == "none"
    assert flag_value(command, "--tools") == ""
    assert flag_value(command, "--model") == "haiku"
    for flag in ISOLATION_FLAGS:
        assert flag in command
    assert "--session-id" not in command
    assert "--resume" not in command
    assert not any("bypassPermissions" in part for part in command)
    assert not any("dangerously" in part for part in command)
    assert call["cwd"] != Path.cwd()
    assert call["cwd"].name.startswith("transpodcast-claude-")
    assert call["cwd_entries"] == []
    assert call["timeout"] == 1800
    assert call["input"].startswith("Reply.\n\nTreat the JSON after SOURCE_DATA")
    assert call["input"].endswith('SOURCE_DATA\n{"text": "Hello."}')
    assert "CLAUDECODE" not in call["env"]


def test_single_shot_parses_recorded_envelope_usage_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = RecordedCli((0, json.dumps(fixture("single-shot")), ""))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    execution = ready_runner(model="haiku").run(
        instructions="Reply.",
        source_data={},
        schema=SCHEMA,
    )

    assert execution.output == {"reply": "OK"}
    assert execution.input_tokens == 2 + 41199 + 0
    assert execution.output_tokens == 53
    # The served model comes from modelUsage, not the requested alias.
    assert execution.model == "claude-sonnet-5"
    assert execution.cli_version == "2.1.296 (Claude Code)"
    assert execution.session_id == "00000000-0000-4000-8000-00000000c1a0"


def test_single_shot_falls_back_to_json_result_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = fixture("single-shot")
    envelope.pop("structured_output")
    envelope["result"] = '```json\n{"reply": "fenced"}\n```'
    cli = RecordedCli((0, json.dumps(envelope), ""))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    execution = ready_runner().run(
        instructions="Reply.",
        source_data={},
        schema=SCHEMA,
    )

    assert execution.output == {"reply": "fenced"}


def test_single_shot_without_schema_answer_is_a_schema_failure_with_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = RecordedCli((0, json.dumps(fixture("schema-unsatisfied")), ""))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    with pytest.raises(AgentInvocationError) as failure:
        ready_runner().run(instructions="Reply.", source_data={}, schema=SCHEMA)

    assert failure.value.kind == "schema"
    assert not failure.value.transient
    assert failure.value.execution is not None
    assert failure.value.execution.input_tokens == 10 + 4094 + 10700
    assert failure.value.execution.output_tokens == 762
    assert failure.value.execution.model == "claude-haiku-5-5"


def test_single_shot_not_logged_in_is_an_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = RecordedCli((1, json.dumps(fixture("not-logged-in")), ""))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    with pytest.raises(AgentInvocationError, match="Not logged in") as failure:
        ready_runner().run(instructions="Reply.", source_data={}, schema=SCHEMA)

    assert failure.value.kind == "auth"
    assert failure.value.execution is not None
    assert failure.value.execution.input_tokens == 0


def test_single_shot_without_envelope_is_a_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = RecordedCli((1, "", "unexpected crash"))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    with pytest.raises(AgentInvocationError, match="unexpected crash") as failure:
        ready_runner().run(instructions="Reply.", source_data={}, schema=SCHEMA)

    assert failure.value.kind == "provider"
    assert failure.value.execution is None


def test_single_shot_timeout_is_a_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 1800)

    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", timeout)

    with pytest.raises(AgentInvocationError) as failure:
        ready_runner().run(instructions="Reply.", source_data={}, schema=SCHEMA)

    assert failure.value.kind == "provider"


def test_unready_runner_refuses_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = ClaudeRunner("claude")
    monkeypatch.setattr(runner, "readiness", lambda: False)

    with pytest.raises(AgentInvocationError, match="not available"):
        runner.run(instructions="Reply.", source_data={}, schema=SCHEMA)


@pytest.mark.parametrize(
    ("changes", "stderr", "kind"),
    [
        ({"api_error_status": 429, "result": "API Error: 429"}, "", "rate_limit"),
        ({"api_error_status": 529, "result": "API Error: 529"}, "", "rate_limit"),
        (
            {"api_error_status": None, "result": "You've hit your limit"},
            "",
            "rate_limit",
        ),
        ({"api_error_status": 401, "result": "API Error: 401"}, "", "auth"),
        ({"api_error_status": "403", "result": "Forbidden"}, "", "auth"),
        (
            {"api_error_status": None, "result": "Not logged in · Please run /login"},
            "",
            "auth",
        ),
        ({"api_error_status": 500, "result": "API Error: 500"}, "", "provider"),
        (
            {"subtype": "error_max_structured_output_retries", "result": ""},
            "",
            "schema",
        ),
        (
            {"subtype": "error_during_execution", "errors": ["Overloaded"]},
            "",
            "rate_limit",
        ),
        ({"subtype": "error_max_turns", "result": ""}, "", "provider"),
    ],
)
def test_failure_classification(
    changes: dict[str, Any],
    stderr: str,
    kind: str,
) -> None:
    envelope = {**fixture("single-shot"), "is_error": True, **changes}

    assert classify_failure(envelope, stderr) == kind


def test_failure_classification_reads_stderr_without_envelope() -> None:
    assert classify_failure(None, "Error: Not logged in") == "auth"
    assert classify_failure(None, "rate limit exceeded") == "rate_limit"
    assert classify_failure(None, "segmentation fault") == "provider"


def test_transient_flag_marks_rate_limits_only() -> None:
    assert AgentInvocationError("x", kind="rate_limit").transient
    assert not AgentInvocationError("x", kind="provider").transient


def test_project_runner_starts_a_new_session_then_resumes_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    envelope = fixture("project-turn")
    cli = RecordedCli(
        (0, json.dumps({**envelope, "session_id": ""}), ""),
        (0, json.dumps({**envelope, "session_id": "resumed-session"}), ""),
    )
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)
    runner = ready_runner(model="haiku")

    started = runner.run_project(
        root=tmp_path,
        instructions="Author.",
        session_id=None,
    )
    resumed = runner.run_project(
        root=tmp_path,
        instructions="Continue.",
        session_id=started.session_id,
    )

    first, second = (call["command"] for call in cli.calls)
    new_session = flag_value(first, "--session-id")
    assert uuid.UUID(new_session).version == 4
    assert started.session_id == new_session
    assert "--resume" not in first
    assert flag_value(second, "--resume") == new_session
    assert "--session-id" not in second
    assert resumed.session_id == "resumed-session"
    for command in (first, second):
        assert command[:4] == ["claude", "--print", "--output-format", "json"]
        assert flag_value(command, "--permission-mode") == "acceptEdits"
        assert flag_value(command, "--permission-prompts") == "none"
        assert flag_value(command, "--tools") == PROJECT_TOOLS
        allowed_at = command.index("--allowedTools") + 1
        assert (
            tuple(command[allowed_at : allowed_at + len(PROJECT_ALLOWED_COMMANDS)])
            == PROJECT_ALLOWED_COMMANDS
        )
        assert "Bash(python validate.py)" in PROJECT_ALLOWED_COMMANDS
        assert "--append-system-prompt" in command
        assert flag_value(command, "--model") == "haiku"
        for flag in ISOLATION_FLAGS:
            assert flag in command
        assert "--no-session-persistence" not in command
        assert "--json-schema" not in command
        assert not any("bypassPermissions" in part for part in command)
        assert not any("dangerously" in part for part in command)
    assert all(call["cwd"] == tmp_path.resolve() for call in cli.calls)
    assert [call["input"] for call in cli.calls] == ["Author.", "Continue."]
    assert all(call["timeout"] == 3600 for call in cli.calls)


def test_project_usage_is_per_invocation_and_auditable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stdout = json.dumps(fixture("project-turn"))
    cli = RecordedCli((0, stdout, ""))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    execution = ready_runner().run_project(
        root=tmp_path,
        instructions="Author.",
        session_id=None,
    )

    assert execution.usage_is_cumulative is False
    assert execution.input_tokens == 8 + 2823 + 30932
    assert execution.output_tokens == 1079
    assert execution.model == "claude-haiku-5-5"
    assert execution.session_id == "00000000-0000-4000-8000-00000000c1a0"
    assert execution.cli_version == "2.1.296 (Claude Code)"
    assert execution.events_jsonl == stdout


def test_project_runner_resolves_relative_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    cli = RecordedCli((0, json.dumps(fixture("project-turn")), ""))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    ready_runner().run_project(
        root=Path("relative-project"),
        instructions="Author.",
        session_id=None,
    )

    assert cli.calls[0]["cwd"] == (tmp_path / "relative-project").resolve()


def test_missing_resumed_session_raises_the_unavailable_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cli = RecordedCli((1, "", "No conversation found with session ID: lost-session"))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    with pytest.raises(CodexSessionUnavailableError) as failure:
        ready_runner().run_project(
            root=tmp_path,
            instructions="Continue.",
            session_id="lost-session",
        )

    assert AgentSessionUnavailableError is CodexSessionUnavailableError
    assert failure.value.execution is not None
    assert failure.value.execution.session_id == "lost-session"
    assert failure.value.execution.input_tokens == 0


def test_new_session_failure_is_never_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cli = RecordedCli((1, "", "No conversation found with session ID: x"))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    with pytest.raises(AgentProjectTurnError) as failure:
        ready_runner().run_project(
            root=tmp_path,
            instructions="Author.",
            session_id=None,
        )

    assert not isinstance(failure.value, CodexSessionUnavailableError)


def test_failed_project_turn_keeps_usage_for_the_translation_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    envelope = {
        **fixture("project-turn"),
        "is_error": True,
        "api_error_status": 429,
        "result": "API Error: 429 rate limited",
    }
    cli = RecordedCli((1, json.dumps(envelope), ""))
    monkeypatch.setattr("transpodcast.adapters.claude.subprocess.run", cli)

    with pytest.raises(CodexProjectInvocationError, match="429") as failure:
        ready_runner().run_project(
            root=tmp_path,
            instructions="Author.",
            session_id="session-1",
        )

    assert isinstance(failure.value, AgentProjectTurnError)
    assert failure.value.kind == "rate_limit"
    assert failure.value.transient
    assert failure.value.execution.input_tokens == 8 + 2823 + 30932
    assert failure.value.execution.output_tokens == 1079


def test_envelope_helpers_tolerate_missing_fields() -> None:
    assert parse_envelope("") is None
    assert parse_envelope("not json") is None
    assert parse_envelope('noise\n{"type": "result", "subtype": "success"}') == {
        "type": "result",
        "subtype": "success",
    }
    assert envelope_usage({}) == (0, 0)
    assert envelope_model({}) == ""
    assert envelope_output({"result": "plain prose"}) is None
    assert envelope_output({"result": "[1, 2]"}) is None


def test_usage_falls_back_to_model_usage_and_picks_the_dominant_model() -> None:
    envelope = {
        "modelUsage": {
            "claude-haiku-5-5": {"inputTokens": 5, "outputTokens": 1},
            "claude-opus-5-5": {
                "inputTokens": 2,
                "outputTokens": 40,
                "cacheReadInputTokens": 100,
                "cacheCreationInputTokens": 10,
            },
        }
    }

    assert envelope_usage(envelope) == (5 + 2 + 100 + 10, 41)
    assert envelope_model(envelope) == "claude-opus-5-5"


def test_isolated_environment_drops_host_session_state() -> None:
    environment = isolated_environment(
        {
            "HOME": "/home/owner",
            "PATH": "/usr/bin",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "claude-desktop",
            "CLAUDE_CODE_SESSION_ID": "host-session",
            "CLAUDE_EFFORT": "xhigh",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-sonnet-5",
            "ANTHROPIC_MODEL": "claude-opus-5-5",
            "MCP_CONNECTION_NONBLOCKING": "true",
            "CLAUDE_CONFIG_DIR": "/home/owner/.claude",
            "CLAUDE_CODE_OAUTH_TOKEN": "token-value",
        }
    )

    assert environment["HOME"] == "/home/owner"
    assert environment["CLAUDE_CONFIG_DIR"] == "/home/owner/.claude"
    assert environment["CLAUDE_CODE_OAUTH_TOKEN"] == "token-value"
    for key in (
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_EFFORT",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_MODEL",
        "MCP_CONNECTION_NONBLOCKING",
    ):
        assert key not in environment
    assert environment["PATH"].split(os.pathsep) == [
        str(Path(sys.executable).parent),
        "/usr/bin",
    ]


def readiness_cli(
    *,
    auth: dict[str, Any] | None = None,
    help_text: str | None = None,
    calls: list[list[str]] | None = None,
) -> Any:
    def fake_run(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(command)
        assert "CLAUDECODE" not in kwargs["env"]
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(
                command, 0, "2.1.296 (Claude Code)\n", ""
            )
        if command[1:] == ["auth", "status"]:
            status = auth if auth is not None else {"loggedIn": True}
            code = 0 if status.get("loggedIn") else 1
            return subprocess.CompletedProcess(command, code, json.dumps(status), "")
        text = (
            help_text
            if help_text is not None
            else "\n".join(f"  {flag} <value>" for flag in REQUIRED_HELP_FLAGS)
        )
        return subprocess.CompletedProcess(command, 0, text, "")

    return fake_run


def test_readiness_checks_version_login_and_flags_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "transpodcast.adapters.claude.shutil.which", lambda _: "/bin/claude"
    )
    monkeypatch.setattr(
        "transpodcast.adapters.claude.subprocess.run", readiness_cli(calls=calls)
    )
    runner = ClaudeRunner("claude")

    assert runner.readiness()
    assert runner.readiness()
    assert runner.cli_version == "2.1.296 (Claude Code)"
    assert calls == [
        ["claude", "--version"],
        ["claude", "auth", "status"],
        ["claude", "--help"],
    ]


def test_readiness_fails_when_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "transpodcast.adapters.claude.shutil.which", lambda _: "/bin/claude"
    )
    monkeypatch.setattr(
        "transpodcast.adapters.claude.subprocess.run",
        readiness_cli(auth={"loggedIn": False, "authMethod": "none"}),
    )
    runner = ClaudeRunner("claude")

    assert not runner.readiness()
    with pytest.raises(AgentInvocationError) as failure:
        runner.run(instructions="Reply.", source_data={}, schema=SCHEMA)
    assert failure.value.kind == "auth"


def test_readiness_fails_when_a_required_flag_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "transpodcast.adapters.claude.shutil.which", lambda _: "/bin/claude"
    )
    monkeypatch.setattr(
        "transpodcast.adapters.claude.subprocess.run",
        readiness_cli(help_text="  --print\n  --output-format\n"),
    )

    assert not ClaudeRunner("claude").readiness()


def test_readiness_fails_without_the_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("transpodcast.adapters.claude.shutil.which", lambda _: None)

    assert not ClaudeRunner("claude").readiness()


def test_effort_is_passed_only_when_configured() -> None:
    default = ClaudeRunner(executable="claude")
    assert "--effort" not in default.single_shot_command({"type": "object"})
    assert "--effort" not in default.project_command("session", resume=False)

    pinned = ClaudeRunner(executable="claude", model="claude-opus-5-5", effort="high")
    for command in (
        pinned.single_shot_command({"type": "object"}),
        pinned.project_command("session", resume=True),
    ):
        assert command[command.index("--effort") + 1] == "high"
        assert command[command.index("--model") + 1] == "claude-opus-5-5"


def test_unknown_effort_level_is_rejected() -> None:
    with pytest.raises(ValueError):
        ClaudeRunner(executable="claude", effort="extreme")
