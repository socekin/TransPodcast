from __future__ import annotations

from datetime import UTC, datetime

import pytest

from transpodcast.adapters.codex import (
    CodexTranslationAdapter,
    WorkspaceExecution,
    harvest_translation_worksheet,
    parse_review_verdict,
    render_translation_worksheet,
)
from transpodcast.domain import SourceCandidate

_SEGMENTS = [
    {
        "id": f"seg-{index:04d}",
        "speaker": "speaker_0" if index % 2 else "speaker_1",
        "source_text": text,
    }
    for index, text in enumerate(
        [
            "Let's talk about Stripe.",
            "They clearly want to become PayPal.",
            "And China is catching up fast.",
        ],
        start=1,
    )
]
_SOURCE_DATA = {
    "source_title": "Useful Show",
    "source_description": "A conversation about payments.",
    "target_locale": "zh-CN",
}


def _translate_targets(worksheet: str) -> str:
    lines = []
    counter = 0
    for line in worksheet.splitlines():
        if line.startswith("TARGET: "):
            counter += 1
            lines.append(f"TARGET: 中文译文第{counter}句。")
        else:
            lines.append(line)
    return "\n".join(lines)


def _verdict(approved: str = "yes", unresolved: str = "- (none)") -> str:
    return (
        f"APPROVED: {approved}\n"
        "TITLE: 有用节目 · 支付格局\n"
        "OVERVIEW: 关于支付行业的完整对话。\n"
        "UNRESOLVED:\n"
        f"{unresolved}\n"
        "SOURCE_WARNINGS:\n"
        "- seg-0002 source transcript is ambiguous\n"
    )


class FileEditingRunner:
    """Simulates the Agent completing workspace sessions."""

    def __init__(self) -> None:
        self.sessions: list[dict[str, str]] = []
        self.tamper: str | None = None

    def readiness(self) -> bool:
        return True

    def run_workspace(
        self,
        *,
        instructions: str,
        files: dict[str, str],
        collect: tuple[str, ...],
    ) -> WorkspaceExecution:
        self.sessions.append(dict(files))
        edited = dict(files)
        if "verdict.md" in files:
            edited["verdict.md"] = _verdict()
            reviewed = files["translation.md"].replace(
                "中文译文第2句。",
                "他们显然想成为 PayPal。",
            )
            edited["translation.md"] = reviewed
        else:
            authored = _translate_targets(files["translation.md"])
            if self.tamper == "source":
                authored = authored.replace(
                    "SOURCE: Let's talk about Stripe.",
                    "SOURCE: Something else entirely.",
                )
            if self.tamper == "empty":
                authored = authored.replace(
                    "TARGET: 中文译文第3句。",
                    "TARGET: (pending)",
                )
            if self.tamper == "delete-block":
                lines = authored.splitlines()
                authored = "\n".join(lines[:-4])
            edited["translation.md"] = authored
        return WorkspaceExecution(
            files={name: edited[name] for name in collect},
            model="codex-fixture",
            input_tokens=1000,
            output_tokens=200,
            cli_version="codex 9.9",
        )


def _candidate() -> SourceCandidate:
    import json

    return SourceCandidate(
        youtube_video_id="useful-001",
        title="Useful Show",
        description="A conversation about payments.",
        duration_seconds=600,
        language="en",
        published_at=datetime(2026, 7, 29, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=useful-001",
        captions=None,
        analysis_json=json.dumps(
            {
                "segments": [
                    {
                        "segment_id": item["id"],
                        "speaker_label": item["speaker"],
                        "start_seconds": float(index),
                        "end_seconds": float(index + 1),
                        "text": item["source_text"],
                    }
                    for index, item in enumerate(_SEGMENTS)
                ]
            }
        ),
    )


def test_worksheet_round_trip_preserves_alignment_by_id() -> None:
    worksheet = render_translation_worksheet(_SOURCE_DATA, _SEGMENTS)
    targets = harvest_translation_worksheet(
        worksheet,
        _translate_targets(worksheet),
    )
    assert list(targets) == ["seg-0001", "seg-0002", "seg-0003"]
    assert targets["seg-0002"] == "中文译文第2句。"


@pytest.mark.parametrize(
    "mutate,message",
    [
        (
            lambda text: text.replace(
                "SOURCE: And China is catching up fast.",
                "SOURCE: Rewritten source.",
            ),
            "anchors were modified",
        ),
        (
            lambda text: "\n".join(text.splitlines()[:-4]),
            "structure was modified",
        ),
        (
            lambda text: text.replace(
                "TARGET: 中文译文第1句。",
                "TARGET: (pending)",
            ),
            "unfinished: seg-0001",
        ),
        (
            lambda text: text + "\n## seg-9999 · speaker_0\n",
            "structure was modified",
        ),
    ],
)
def test_harvest_rejects_any_skeleton_violation(mutate, message) -> None:
    worksheet = render_translation_worksheet(_SOURCE_DATA, _SEGMENTS)
    edited = mutate(_translate_targets(worksheet))
    with pytest.raises(ValueError, match=message):
        harvest_translation_worksheet(worksheet, edited)


def test_reordered_blocks_are_rejected() -> None:
    worksheet = render_translation_worksheet(_SOURCE_DATA, _SEGMENTS)
    edited = _translate_targets(worksheet).replace(
        "## seg-0001", "## seg-0002", 1
    )
    with pytest.raises(ValueError, match="anchors were modified"):
        harvest_translation_worksheet(worksheet, edited)


def test_verdict_parsing_is_strict() -> None:
    verdict = parse_review_verdict(_verdict())
    assert verdict["approved"] is True
    assert verdict["unresolved"] == []
    assert verdict["source_warnings"] == [
        "seg-0002 source transcript is ambiguous"
    ]
    with pytest.raises(ValueError, match="malformed"):
        parse_review_verdict(_verdict(approved="(pending)"))


def test_translate_by_file_editing_reviews_and_applies_corrections(
    tmp_path,
) -> None:
    runner = FileEditingRunner()
    adapter = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    result = adapter.translate_legacy_file_editing(_candidate())

    assert len(runner.sessions) == 2
    assert result.segments == (
        "中文译文第1句。",
        "他们显然想成为 PayPal。",
        "中文译文第3句。",
    )
    assert result.title == "有用节目 · 支付格局"
    assert result.reviewed is True
    assert result.policy_version == "translation-policy-v2-file-editing"
    assert result.input_tokens == 2000
    assert "他们显然想成为 PayPal。" in result.audit_json


@pytest.mark.parametrize(
    "tamper", ["source", "empty", "delete-block"]
)
def test_translate_rejects_structural_violations(tamper, tmp_path) -> None:
    runner = FileEditingRunner()
    runner.tamper = tamper
    adapter = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    )

    with pytest.raises(ValueError):
        adapter.translate_legacy_file_editing(_candidate())
    # The review session never starts on a structurally broken file.
    assert len(runner.sessions) == 1
