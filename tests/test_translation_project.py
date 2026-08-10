from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from transpodcast.adapters.codex import (
    CodexExecution,
    CodexProjectInvocationError,
    CodexSessionUnavailableError,
    CodexTranslationAdapter,
    ProjectExecution,
    TranslationStalledError,
    _normalize_project_usage_accounting,
)
from transpodcast.domain import SourceCandidate, TranslationResult
from transpodcast.errors import TranslationProviderError
from transpodcast.translation_projects import mark_translation_project_reprocess


class ProgressiveProjectRunner:
    def __init__(self) -> None:
        self.invocations: list[tuple[str, str | None]] = []
        self.author_turn = 0

    def readiness(self) -> bool:
        return True

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        del instructions
        role = root.name
        self.invocations.append((role, session_id))
        if role == "author":
            self.author_turn += 1
            limit = 2 if self.author_turn == 1 else 99
            self._complete_pending_targets(root, limit)
            resolved_session = session_id or "author-session"
        else:
            (root / "verdict.md").write_text(
                "APPROVED: yes\n"
                "TITLE: 有用节目 · 项目式翻译\n"
                "OVERVIEW: 一次完整且经过独立校对的对话。\n"
                "UNRESOLVED:\n"
                "- (none)\n"
                "SOURCE_WARNINGS:\n"
                "- (none)\n",
                encoding="utf-8",
            )
            (root / "content-edit.json").write_text(
                json.dumps(
                    {
                        "policy_version": "semantic-content-edit-v1",
                        "status": "accepted",
                        "decisions": [],
                        "retention_notes": [
                            "The fixture contains no removable content."
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (root / "publication-metadata.json").write_text(
                json.dumps(
                    {
                        "policy_version": "publication-metadata-v1",
                        "status": "accepted",
                        "description": (
                            f"{chr(0x4E00) * 180}\n\n"
                            f"{chr(0x4E01) * 180}"
                        ),
                        "participant_line": "",
                        "chapters": [
                            {
                                "anchor_segment_id": "seg-0001",
                                "title": chr(0x4E10) * 4,
                                "rationale": "Opening topic",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            resolved_session = session_id or "reviewer-session"
        return ProjectExecution(
            session_id=resolved_session,
            model=f"{role}-fixture",
            input_tokens=100,
            output_tokens=20,
            cli_version="codex-fixture",
            events_jsonl="",
        )

    @staticmethod
    def _complete_pending_targets(root: Path, limit: int) -> None:
        completed = 0
        for worksheet in sorted((root / "translation").glob("*.md")):
            lines: list[str] = []
            for line in worksheet.read_text(encoding="utf-8").splitlines():
                if line == "TARGET: (pending)" and completed < limit:
                    completed += 1
                    line = f"TARGET: 译文第{completed}段。"
                lines.append(line)
            worksheet.write_text("\n".join(lines), encoding="utf-8")


def _candidate(segment_count: int = 5) -> SourceCandidate:
    segments = [
        {
            "segment_id": f"seg-{index:04d}",
            "speaker_label": "speaker_0",
            "start_seconds": float(index),
            "end_seconds": float(index + 1),
            "text": f"Source segment {index}.",
        }
        for index in range(1, segment_count + 1)
    ]
    return SourceCandidate(
        youtube_video_id="project-translation",
        title="Project Translation",
        description="A complete episode used to verify resumable translation.",
        duration_seconds=300,
        language="en",
        published_at=datetime(2026, 7, 31, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=project-translation",
        captions=None,
        analysis_json=json.dumps({"segments": segments}),
        processing_run_id=88,
    )


def test_translation_project_resumes_author_and_starts_independent_review(
    tmp_path: Path,
) -> None:
    runner = ProgressiveProjectRunner()
    progress: list[tuple[int, str]] = []
    adapter = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
        progress_reporter=lambda run_id, step, _recorded_at: progress.append(
            (run_id, step)
        ),
    )

    result = adapter.translate(_candidate())

    assert runner.invocations == [
        ("author", None),
        ("author", "author-session"),
        ("review", None),
    ]
    assert result.reviewed is True
    assert result.review_model == "review-fixture"
    assert len(result.segments) == 5
    audit = json.loads(result.audit_json)
    assert audit["author_sessions"] == ["author-session"]
    assert audit["reviewer_sessions"] == ["reviewer-session"]
    assert audit["author_turns"] == 2
    assert audit["reviewer_turns"] == 1
    assert result.input_tokens == 300
    assert result.output_tokens == 60
    assert progress == [
        (88, "translation_authoring"),
        (88, "translation_review"),
    ]
    content_edit = json.loads(result.content_edit_json)
    assert content_edit["status"] == "accepted"
    assert content_edit["decisions"] == []
    publication_metadata = json.loads(result.publication_metadata_json)
    assert publication_metadata["status"] == "accepted"
    assert publication_metadata["chapters"][0]["anchor_segment_id"] == (
        "seg-0001"
    )


class PublicationMetadataRunner:
    def __init__(self) -> None:
        self.source_data: dict[str, object] = {}

    def run(self, *, instructions, source_data, schema):  # type: ignore[no-untyped-def]
        assert "Never invent timestamps" in instructions
        assert schema["properties"]["status"]["const"] == "accepted"
        self.source_data = source_data
        return CodexExecution(
            output={
                "policy_version": "publication-metadata-v1",
                "status": "accepted",
                "description": (
                    f"{chr(0x4E00) * 180}\n\n{chr(0x4E01) * 180}"
                ),
                "participant_line": "",
                "chapters": [
                    {
                        "anchor_segment_id": "seg-0001",
                        "title": chr(0x4E10) * 4,
                        "rationale": "Opening topic",
                    }
                ],
            },
            model="metadata-fixture",
            input_tokens=33,
            output_tokens=22,
            cli_version="codex-fixture",
            session_id="metadata-session",
        )


def test_historical_metadata_uses_a_separate_structured_session(
    tmp_path: Path,
) -> None:
    runner = PublicationMetadataRunner()
    adapter = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    current = TranslationResult(
        title="Translated episode",
        overview="Short overview",
        target_locale="zh-CN",
        segments=tuple("Target segment" for _ in range(5)),
        provider="fixture",
        model="fixture",
        input_tokens=0,
        output_tokens=0,
    )

    result = adapter.publication_metadata(_candidate(), current)

    assert result.provider == "codex-cli"
    assert result.input_tokens == 33
    assert result.session_id == "metadata-session"
    assert len(runner.source_data["segments"]) == 5  # type: ignore[arg-type]
    assert json.loads(result.artifact_json)["status"] == "accepted"


class MissingContentEditRunner(ProgressiveProjectRunner):
    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        execution = super().run_project(
            root=root,
            instructions=instructions,
            session_id=session_id,
        )
        if root.name == "review":
            root.joinpath("content-edit.json").unlink()
        return execution


def test_missing_content_edit_does_not_block_an_approved_translation(
    tmp_path: Path,
) -> None:
    result = CodexTranslationAdapter(
        MissingContentEditRunner(),  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate())

    assert result.reviewed is True
    assert result.content_edit_json == "{}"


def test_reprocess_reuses_author_targets_but_starts_a_fresh_review(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "translation-projects"
    first_runner = ProgressiveProjectRunner()
    CodexTranslationAdapter(
        first_runner,  # type: ignore[arg-type]
        project_root=project_root,
    ).translate(_candidate())
    mark_translation_project_reprocess(tmp_path, 88)

    second_runner = ProgressiveProjectRunner()
    result = CodexTranslationAdapter(
        second_runner,  # type: ignore[arg-type]
        project_root=project_root,
    ).translate(_candidate())

    assert second_runner.invocations == [("review", None)]
    assert result.input_tokens == 300
    assert result.output_tokens == 60


class CumulativeUsageProjectRunner(ProgressiveProjectRunner):
    def __init__(self) -> None:
        super().__init__()
        self.session_turns: dict[str, int] = {}

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        execution = super().run_project(
            root=root,
            instructions=instructions,
            session_id=session_id,
        )
        session_turn = self.session_turns.get(execution.session_id, 0) + 1
        self.session_turns[execution.session_id] = session_turn
        return ProjectExecution(
            session_id=execution.session_id,
            model=execution.model,
            input_tokens=100 * session_turn,
            output_tokens=20 * session_turn,
            cli_version=execution.cli_version,
            usage_is_cumulative=True,
        )


def test_translation_project_counts_cumulative_session_usage_once(
    tmp_path: Path,
) -> None:
    result = CodexTranslationAdapter(
        CumulativeUsageProjectRunner(),  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate())

    assert result.input_tokens == 300
    assert result.output_tokens == 60
    state_path = next(tmp_path.glob("run-88/*/project.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [turn["input_tokens"] for turn in state["turns"]] == [
        100,
        100,
        100,
    ]
    assert [turn["provider_input_tokens"] for turn in state["turns"]] == [
        100,
        200,
        100,
    ]


def test_legacy_project_usage_is_normalized_from_cumulative_turns() -> None:
    state = {
        "input_tokens": 4_064_458,
        "output_tokens": 46_363,
        "turns": [
            {
                "role": "author",
                "session_id": "author-session",
                "input_tokens": 1_285_879,
                "output_tokens": 15_338,
            },
            {
                "role": "author",
                "session_id": "author-session",
                "input_tokens": 1_354_741,
                "output_tokens": 15_476,
            },
            {
                "role": "author",
                "session_id": "author-session",
                "input_tokens": 1_423_838,
                "output_tokens": 15_549,
            },
        ],
    }

    assert _normalize_project_usage_accounting(state) is True
    assert state["input_tokens"] == 1_423_838
    assert state["output_tokens"] == 15_549
    assert [turn["input_tokens"] for turn in state["turns"]] == [
        1_285_879,
        68_862,
        69_097,
    ]
    assert [turn["output_tokens"] for turn in state["turns"]] == [
        15_338,
        138,
        73,
    ]


class InterruptedLongProjectRunner(ProgressiveProjectRunner):
    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        if root.name == "author" and session_id is None:
            self.invocations.append(("author", None))
            self._complete_pending_targets(root, 120)
            return ProjectExecution(
                session_id="long-author-session",
                model="author-fixture",
                input_tokens=100,
                output_tokens=20,
                cli_version="codex-fixture",
            )
        raise RuntimeError("TransPodcast host process stopped")


class RestartedLongProjectRunner(ProgressiveProjectRunner):
    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        if root.name == "author":
            self.author_turn = 99
        return super().run_project(
            root=root,
            instructions=instructions,
            session_id=session_id,
        )


def test_long_project_recovers_120_targets_after_process_restart(
    tmp_path: Path,
) -> None:
    candidate = _candidate(segment_count=411)
    first_runner = InterruptedLongProjectRunner()
    first_adapter = CodexTranslationAdapter(
        first_runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="TransPodcast host process stopped"):
        first_adapter.translate(candidate)

    state_path = next(tmp_path.glob("run-88/*/project.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["completed_targets"] == 120
    first_target_before_restart = next(
        tmp_path.glob("run-88/*/author/translation/part-001.md")
    ).read_text(encoding="utf-8")

    restarted_runner = RestartedLongProjectRunner()
    result = CodexTranslationAdapter(
        restarted_runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(candidate)

    assert len(result.segments) == 411
    assert restarted_runner.invocations[0] == (
        "author",
        "long-author-session",
    )
    assert result.segments[0] == "译文第1段。"
    assert "TARGET: 译文第1段。" in first_target_before_restart


class ReplacementProjectRunner(ProgressiveProjectRunner):
    def __init__(self) -> None:
        super().__init__()
        self.resume_failed = False
        self.pending_before_replacement: int | None = None

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        if (
            root.name == "author"
            and session_id == "author-session"
            and not self.resume_failed
        ):
            self.invocations.append(("author", session_id))
            self.resume_failed = True
            raise CodexSessionUnavailableError(
                "Recorded Codex session is unavailable",
                execution=ProjectExecution(
                    session_id="author-session",
                    model="author-fixture",
                    input_tokens=13,
                    output_tokens=3,
                    cli_version="codex-fixture",
                    events_jsonl='{"type":"turn.failed"}\n',
                ),
            )
        if root.name == "author" and self.resume_failed:
            self.pending_before_replacement = sum(
                path.read_text(encoding="utf-8").count(
                    "TARGET: (pending)"
                )
                for path in (root / "translation").glob("*.md")
            )
            self.author_turn = 99
            execution = super().run_project(
                root=root,
                instructions=instructions,
                session_id=session_id,
            )
            return ProjectExecution(
                session_id=session_id or "replacement-author-session",
                model=execution.model,
                input_tokens=execution.input_tokens,
                output_tokens=execution.output_tokens,
                cli_version=execution.cli_version,
                events_jsonl=execution.events_jsonl,
            )
        return super().run_project(
            root=root,
            instructions=instructions,
            session_id=session_id,
        )


def test_unavailable_author_session_is_replaced_without_losing_progress(
    tmp_path: Path,
) -> None:
    runner = ReplacementProjectRunner()
    adapter = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    result = adapter.translate(_candidate())

    assert runner.pending_before_replacement == 3
    assert runner.invocations[:3] == [
        ("author", None),
        ("author", "author-session"),
        ("author", None),
    ]
    audit = json.loads(result.audit_json)
    assert audit["author_sessions"] == [
        "author-session",
        "replacement-author-session",
    ]
    assert audit["author_turns"] == 3
    assert audit["input_tokens"] == 313
    assert audit["output_tokens"] == 63
    assert audit["reviewer_sessions"] == ["reviewer-session"]


class StalledProjectRunner:
    def __init__(self) -> None:
        self.invocations: list[str | None] = []

    def readiness(self) -> bool:
        return True

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        del root, instructions
        self.invocations.append(session_id)
        return ProjectExecution(
            session_id=session_id or "stalled-author-session",
            model="stalled-fixture",
            input_tokens=100,
            output_tokens=20,
            cli_version="codex-fixture",
            events_jsonl="",
        )


def test_translation_project_stops_after_two_no_progress_turns(
    tmp_path: Path,
) -> None:
    runner = StalledProjectRunner()
    adapter = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    try:
        adapter.translate(_candidate())
    except TranslationStalledError as error:
        assert error.input_tokens == 200
        assert error.output_tokens == 40
    else:
        raise AssertionError("Translation Project should have stalled")

    state_path = next(tmp_path.glob("run-88/*/project.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "stalled"
    assert state["pending_targets"] == 5
    assert state["author_turns"] == 2
    assert runner.invocations == [None, "stalled-author-session"]


class ProgressiveReviewerRunner(ProgressiveProjectRunner):
    def __init__(self) -> None:
        super().__init__()
        self.review_turn = 0

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        if root.name == "author":
            self.author_turn = 99
            return super().run_project(
                root=root,
                instructions=instructions,
                session_id=session_id,
            )
        self.review_turn += 1
        self.invocations.append(("review", session_id))
        if self.review_turn == 2:
            (root / "verdict.md").write_text(
                "APPROVED: yes\n"
                "TITLE: 独立校对\n"
                "OVERVIEW: 校对在独立会话中完成。\n"
                "UNRESOLVED:\n"
                "- (none)\n"
                "SOURCE_WARNINGS:\n"
                "- (none)\n",
                encoding="utf-8",
            )
        return ProjectExecution(
            session_id=session_id or "reviewer-session",
            model="review-fixture",
            input_tokens=100,
            output_tokens=20,
            cli_version="codex-fixture",
            events_jsonl="",
        )


def test_incomplete_review_resumes_only_the_reviewer_session(
    tmp_path: Path,
) -> None:
    runner = ProgressiveReviewerRunner()
    adapter = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    result = adapter.translate(_candidate())

    assert runner.invocations == [
        ("author", None),
        ("review", None),
        ("review", "reviewer-session"),
    ]
    audit = json.loads(result.audit_json)
    assert audit["author_sessions"] == ["author-session"]
    assert audit["reviewer_sessions"] == ["reviewer-session"]
    assert audit["reviewer_turns"] == 2


class FailedAuthorTurnRunner(ProgressiveProjectRunner):
    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        if root.name == "author" and session_id is not None:
            raise CodexProjectInvocationError(
                "Codex CLI failed during an Author turn",
                execution=ProjectExecution(
                    session_id=session_id,
                    model="author-fixture",
                    input_tokens=70,
                    output_tokens=15,
                    cli_version="codex-fixture",
                    events_jsonl='{"type":"turn.failed"}\n',
                ),
            )
        return super().run_project(
            root=root,
            instructions=instructions,
            session_id=session_id,
        )


def test_failed_provider_turn_persists_usage_before_run_failure(
    tmp_path: Path,
) -> None:
    adapter = CodexTranslationAdapter(
        FailedAuthorTurnRunner(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    try:
        adapter.translate(_candidate())
    except TranslationProviderError as error:
        assert error.input_tokens == 170
        assert error.output_tokens == 35
    else:
        raise AssertionError("The failed provider turn should be reported")

    state_path = next(tmp_path.glob("run-88/*/project.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "failed"
    assert state["failed_role"] == "author"
    assert state["author_turns"] == 2
    assert state["input_tokens"] == 170
    assert state["output_tokens"] == 35
    assert next(tmp_path.glob("run-88/*/events/author-turn-002.jsonl"))


class StructurallyInvalidTurnRunner:
    def readiness(self) -> bool:
        return True

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        del instructions, session_id
        worksheet = next((root / "translation").glob("*.md"))
        worksheet.write_text(
            worksheet.read_text(encoding="utf-8").replace(
                "SOURCE: ",
                "SOURCE: tampered ",
                1,
            ),
            encoding="utf-8",
        )
        return ProjectExecution(
            session_id="invalid-author-session",
            model="author-fixture",
            input_tokens=71,
            output_tokens=16,
            cli_version="codex-fixture",
            events_jsonl='{"type":"turn.completed"}\n',
        )


def test_invalid_workspace_still_persists_turn_usage(
    tmp_path: Path,
) -> None:
    adapter = CodexTranslationAdapter(
        StructurallyInvalidTurnRunner(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    with pytest.raises(ValueError, match="anchors were modified"):
        adapter.translate(_candidate())

    state_path = next(tmp_path.glob("run-88/*/project.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["author_turns"] == 1
    assert state["input_tokens"] == 71
    assert state["output_tokens"] == 16
    assert state["author_sessions"] == ["invalid-author-session"]


class FailedReviewerTurnRunner(ProgressiveProjectRunner):
    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        if root.name == "author":
            self.author_turn = 99
            return super().run_project(
                root=root,
                instructions=instructions,
                session_id=session_id,
            )
        raise CodexProjectInvocationError(
            "Codex CLI failed during a Reviewer turn",
            execution=ProjectExecution(
                session_id=session_id or "reviewer-session",
                model="review-fixture",
                input_tokens=55,
                output_tokens=8,
                cli_version="codex-fixture",
            ),
        )


def test_failed_reviewer_turn_records_the_blocking_phase(
    tmp_path: Path,
) -> None:
    adapter = CodexTranslationAdapter(
        FailedReviewerTurnRunner(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    with pytest.raises(TranslationProviderError):
        adapter.translate(_candidate())

    state_path = next(tmp_path.glob("run-88/*/project.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "failed"
    assert state["failed_role"] == "reviewer"


class ReplacementReviewerRunner(ProgressiveProjectRunner):
    def __init__(self) -> None:
        super().__init__()
        self.review_turn = 0
        self.replacement_received_clean_snapshot = False

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        if root.name == "author":
            self.author_turn = 99
            return super().run_project(
                root=root,
                instructions=instructions,
                session_id=session_id,
            )
        self.invocations.append(("review", session_id))
        self.review_turn += 1
        worksheet = next((root / "translation").glob("*.md"))
        if self.review_turn == 1:
            text = worksheet.read_text(encoding="utf-8")
            worksheet.write_text(
                text.replace("TARGET: 译文第1段。", "TARGET: 校对临时改动。"),
                encoding="utf-8",
            )
            return ProjectExecution(
                session_id="reviewer-session",
                model="review-fixture",
                input_tokens=100,
                output_tokens=20,
                cli_version="codex-fixture",
            )
        if self.review_turn == 2:
            raise CodexSessionUnavailableError(
                "Recorded reviewer session is unavailable",
                execution=ProjectExecution(
                    session_id="reviewer-session",
                    model="review-fixture",
                    input_tokens=17,
                    output_tokens=4,
                    cli_version="codex-fixture",
                    events_jsonl='{"type":"turn.failed"}\n',
                ),
            )
        self.replacement_received_clean_snapshot = (
            "校对临时改动"
            not in worksheet.read_text(encoding="utf-8")
        )
        (root / "verdict.md").write_text(
            "APPROVED: yes\n"
            "TITLE: 全新独立校对\n"
            "OVERVIEW: 会话失效后从作者快照重新开始。\n"
            "UNRESOLVED:\n"
            "- (none)\n"
            "SOURCE_WARNINGS:\n"
            "- (none)\n",
            encoding="utf-8",
        )
        return ProjectExecution(
            session_id="replacement-reviewer-session",
            model="review-fixture",
            input_tokens=100,
            output_tokens=20,
            cli_version="codex-fixture",
        )


def test_unavailable_reviewer_session_restarts_from_author_snapshot(
    tmp_path: Path,
) -> None:
    runner = ReplacementReviewerRunner()
    adapter = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    result = adapter.translate(_candidate())

    assert runner.replacement_received_clean_snapshot is True
    assert runner.invocations == [
        ("author", None),
        ("review", None),
        ("review", "reviewer-session"),
        ("review", None),
    ]
    audit = json.loads(result.audit_json)
    assert audit["reviewer_sessions"] == [
        "reviewer-session",
        "replacement-reviewer-session",
    ]
    assert audit["reviewer_turns"] == 3
    assert next(
        tmp_path.glob("run-88/*/events/reviewer-turn-002.jsonl")
    )
