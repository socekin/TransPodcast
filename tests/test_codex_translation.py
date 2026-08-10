from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from transpodcast.adapters.codex import (
    CodexExecution,
    CodexProjectInvocationError,
    CodexRunner,
    _translation_schema,
)


class RecordedRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def readiness(self) -> bool:
        return True

    def run(
        self,
        *,
        instructions: str,
        source_data: dict[str, Any],
        schema: dict[str, Any],
    ) -> CodexExecution:
        self.calls.append(
            {
                "instructions": instructions,
                "source_data": source_data,
                "schema": schema,
            }
        )
        if len(self.calls) == 1:
            return CodexExecution(
                output={
                    "segments": {
                        "seg-0001": "First translation",
                        "seg-0002": "Second translation",
                    },
                },
                model="author-model",
                input_tokens=100,
                output_tokens=50,
            )
        return CodexExecution(
            output={
                "title": "Mandarin title",
                "overview": "Mandarin overview",
                "approved": True,
                "corrections": [
                    {
                        "id": "seg-0002",
                        "target_text": "Corrected second translation",
                        "reason": "Preserve the number.",
                    }
                ],
                "unresolved": [],
                "source_warnings": [],
            },
            model="review-model",
            input_tokens=80,
            output_tokens=20,
        )


def test_runner_allows_the_ephemeral_non_repository_workdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CodexRunner("codex")
    monkeypatch.setattr(runner, "readiness", lambda: True)

    def fake_run(
        command: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        assert "--skip-git-repo-check" in command
        output_path = Path(
            command[command.index("--output-last-message") + 1]
        )
        output_path.write_text('{"translated": true}', encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"type":"turn.completed","usage":{"input_tokens":12,'
                '"output_tokens":4}}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(
        "transpodcast.adapters.codex.subprocess.run",
        fake_run,
    )

    result = runner.run(
        instructions="Translate.",
        source_data={"text": "Hello."},
        schema={"type": "object"},
    )

    assert result.output == {"translated": True}
    assert result.input_tokens == 12
    assert result.output_tokens == 4


def test_translation_schema_requires_every_stable_segment_id() -> None:
    schema = _translation_schema(("seg-0001", "seg-0002"))

    segment_schema = schema["properties"]["segments"]
    assert segment_schema["type"] == "object"
    assert segment_schema["required"] == ["seg-0001", "seg-0002"]
    assert segment_schema["additionalProperties"] is False
    assert list(segment_schema["properties"]) == ["seg-0001", "seg-0002"]


def test_project_runner_starts_durable_session_then_resumes_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = CodexRunner("codex", model="gpt-project")
    runner._cli_version = "codex-fixture"  # noqa: SLF001
    monkeypatch.setattr(runner, "readiness", lambda: True)
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"type":"thread.started","thread_id":"session-1"}\n'
                '{"type":"turn.completed","usage":{"input_tokens":12,'
                '"output_tokens":4}}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(
        "transpodcast.adapters.codex.subprocess.run",
        fake_run,
    )

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

    assert "--ephemeral" not in commands[0]
    assert "workspace-write" in commands[0]
    assert commands[1][0:5] == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "resume",
    ]
    assert commands[1][-2:] == ["session-1", "-"]
    assert resumed.session_id == "session-1"


def test_project_runner_resolves_relative_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CodexRunner("codex")
    runner._cli_version = "codex-fixture"  # noqa: SLF001
    monkeypatch.setattr(runner, "readiness", lambda: True)
    expected_root = (tmp_path / "relative-project").resolve()

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert kwargs["cwd"] == expected_root
        assert Path(command[command.index("--cd") + 1]) == expected_root
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"type":"thread.started","thread_id":"session-relative"}\n'
                '{"type":"turn.completed","usage":{"input_tokens":1,'
                '"output_tokens":1}}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(
        "transpodcast.adapters.codex.subprocess.run",
        fake_run,
    )

    execution = runner.run_project(
        root=Path("relative-project"),
        instructions="Author.",
        session_id=None,
    )

    assert execution.session_id == "session-relative"


def test_project_runner_retains_usage_from_failed_provider_turn(
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
            stdout=(
                '{"type":"thread.started","thread_id":"session-2"}\n'
                '{"type":"turn.completed","usage":{"input_tokens":31,'
                '"output_tokens":7}}\n'
            ),
            stderr="provider timeout",
        ),
    )

    with pytest.raises(
        CodexProjectInvocationError,
        match="provider timeout",
    ) as failure:
        runner.run_project(
            root=tmp_path,
            instructions="Author.",
            session_id=None,
        )

    assert failure.value.execution.session_id == "session-2"
    assert failure.value.execution.input_tokens == 31
    assert failure.value.execution.output_tokens == 7
