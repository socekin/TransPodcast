from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from transpodcast.adapters.codex import (
    _CONTENT_EDIT_SKELETON,
    _PROJECT_AGENT_GUIDANCE,
    _PROJECT_AUTHOR_INSTRUCTIONS,
    _PROJECT_REVIEW_INSTRUCTIONS,
    _PROJECT_VALIDATOR,
    _PUBLICATION_METADATA_SKELETON,
    _REVIEW_VERDICT_REPAIR_INSTRUCTIONS,
    _VERDICT_SKELETON,
    PROJECT_TRANSLATION_POLICY_V3,
    CodexExecution,
    CodexTranslationAdapter,
    ProjectExecution,
    parse_review_verdict,
    translation_policy_for_pipeline,
)
from transpodcast.domain import SourceCandidate, TranslationResult
from transpodcast.listening_translation import (
    LISTENING_AGENT_GUIDANCE,
    LISTENING_AUTHOR_INSTRUCTIONS,
    LISTENING_REVIEW_INSTRUCTIONS,
    LISTENING_SEGMENT_RULES,
    LISTENING_TRANSLATION_POLICY,
    description_sponsors,
    listening_validator_source,
    parse_listening_review_verdict,
    source_warnings_from_audit,
    structured_source_warning,
    term_candidates,
)
from transpodcast.pipeline_versions import (
    SPEECH_PIPELINE_V15,
    SPEECH_PIPELINE_V16,
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V18,
    SPEECH_PIPELINE_V19,
    SPEECH_PIPELINE_V20,
    SPEECH_PIPELINE_V21,
    SPEECH_PIPELINE_V22,
)
from transpodcast.translation_projects import mark_translation_project_reprocess

# SHA-256 of the translation-policy-v3-project texts as shipped before the
# listening policy. Existing V19/V20 projects carry the same hashes.
V3_TEXT_HASHES = {
    "agents_md": (
        _PROJECT_AGENT_GUIDANCE,
        # pragma: allowlist nextline secret
        "ae59558a7d5069082bcca7b59041176278b266ab974289dfb8c29faf24991f16",
    ),
    "validator": (
        _PROJECT_VALIDATOR,
        # pragma: allowlist nextline secret
        "02ed73b2b9330cd1ac93b6d8411bf06f3655799e8909cfe9f6896f846fea880c",
    ),
    "author": (
        _PROJECT_AUTHOR_INSTRUCTIONS,
        # pragma: allowlist nextline secret
        "aa7835f673e9ff0a10b10446daaac3454e935e38e86242b6ff3d18de3f08a447",
    ),
    "review": (
        _PROJECT_REVIEW_INSTRUCTIONS,
        # pragma: allowlist nextline secret
        "ff20c3713b22b3e5fface428c8c94b9a6c2964616a5ca409dd90476f64122f6e",
    ),
    "verdict_repair": (
        _REVIEW_VERDICT_REPAIR_INSTRUCTIONS,
        # pragma: allowlist nextline secret
        "575e6962c35e0c5654a6f46e8dfba3b76fe4f1260dd71153c3f0a84845ca844d",
    ),
    "verdict": (
        _VERDICT_SKELETON,
        # pragma: allowlist nextline secret
        "f77c8fc31706d3d24f0b51e673c6ae90c788efc0ba611d05ba927a95ed4c47c9",
    ),
    "content_edit": (
        _CONTENT_EDIT_SKELETON,
        # pragma: allowlist nextline secret
        "2bee8aee4af6b3b45965a47643697e06425305f093799e285ba1182450f5b1e7",
    ),
    "publication_metadata": (
        _PUBLICATION_METADATA_SKELETON,
        # pragma: allowlist nextline secret
        "452145e0f28b384461842f6df4ad12f528bdb0e9389901f13500da31889ff8ea",
    ),
}
DESCRIPTION = (
    "Jane Doe builds Dots at Initech.\n\n"
    "*Brought to you by:*\n"
    "Acme—Widgets for everyone: https://acme.example/podcast\n\n"
    "*In this episode, we cover:*\n"
    "(00:00) Introduction to Jane Doe\n"
    "(04:10) Building Dots with LeetCode practice\n"
)
SOURCE_SEGMENTS = [
    ("seg-0001", "speaker_0", 0.0, 4.0, "Welcome back to the show everyone."),
    (
        "seg-0002",
        "speaker_1",
        4.0,
        8.0,
        "This episode is brought to you by Acme. Acme makes great widgets.",
    ),
    (
        "seg-0003",
        "speaker_1",
        8.0,
        12.0,
        "Visit acme dot com today. So anyway what were you building?",
    ),
    (
        "seg-0004",
        "speaker_0",
        12.0,
        16.0,
        "We were building Dots for everyone at the company.",
    ),
    ("seg-0005", "speaker_1", 16.0, 17.5, "Right."),
    ("seg-0006", "speaker_0", 17.5, 22.0, "I tweet about it on Twitter every day."),
]
AUTHORED = {
    "seg-0001": "欢迎回来，大家好，今天我们接着聊下去。",
    "seg-0002": "本期节目由Acme赞助，Acme的小工具做得特别好。",
    "seg-0003": "今天就去Acme官网看看吧。那你们当时在做什么呢？",
    "seg-0004": "所以我们在给公司里每个人做Dots助手。",
    "seg-0005": "对。",
    "seg-0006": "我每天都会在推特上发这些内容。",
}
BRIDGED = "我们当时在给公司里每个人做Dots助手。"
GLOSSARY = {
    "policy_version": "listening-glossary-v1",
    "status": "accepted",
    "entries": [
        {"source_term": "Acme", "written_form": "Acme", "spoken_as_english": True},
        {
            "source_term": "Dots",
            "written_form": "Dots",
            "spoken_as_english": True,
            "asr_variants": ["docs"],
        },
        {"source_term": "Twitter", "written_form": "推特", "spoken_as_english": False},
    ],
}
LISTENING_VERDICT = """APPROVED: yes
TITLE: 做Dots的故事
OVERVIEW: 一次关于Dots的完整对话。
UNRESOLVED:
- (none)
SOURCE_WARNINGS:
- [asr_name] seg-0003: "acme dot com" is the Acme website.
- [speaker_label] seg-0005..seg-0006: the reply may belong to the host.
VALIDATOR_NOTES:
- (none)
"""
CONTENT_EDIT = {
    "policy_version": "semantic-content-edit-v1",
    "status": "accepted",
    "decisions": [
        {
            "decision_id": "edit-001",
            "category": "paid_ad",
            "confidence": 0.98,
            "start_segment_id": "seg-0002",
            "end_segment_id": "seg-0003",
            "start_anchor": "This episode is brought to you by Acme",
            "end_anchor": "Visit acme dot com today",
            "rationale": "A separate Acme sponsor read.",
            "evidence_segment_ids": ["seg-0002", "seg-0003"],
            "retained_start_target": "",
            "retained_end_target": "那你们当时在做什么呢？",
            "bridge_review": "Dropped the dangling 所以 opener after the cut.",
        }
    ],
    "bridge_edits": [
        {
            "decision_id": "edit-001",
            "segment_id": "seg-0004",
            "target": BRIDGED,
            "reason": "The 所以 opener no longer connects across the cut.",
        }
    ],
    "description_sponsors": [
        {"sponsor": "Acme", "status": "removed", "decision_ids": ["edit-001"]}
    ],
    "retention_notes": [],
}
PUBLICATION_METADATA = {
    "policy_version": "publication-metadata-v1",
    "status": "accepted",
    "description": f"{chr(0x4E00) * 180}\n\n{chr(0x4E01) * 180}",
    "participant_line": "",
    "chapters": [
        {
            "anchor_segment_id": "seg-0001",
            "title": chr(0x4E10) * 4,
            "rationale": "Opening topic",
        }
    ],
}


def _candidate(pipeline_version: str, run_id: int = 88) -> SourceCandidate:
    return SourceCandidate(
        youtube_video_id="listening-translation",
        title="Building Dots | Jane Doe",
        description=DESCRIPTION,
        duration_seconds=22,
        language="en",
        published_at=datetime(2026, 10, 11, tzinfo=UTC),
        source_url="https://www.youtube.com/watch?v=listening-translation",
        captions=None,
        analysis_json=json.dumps(
            {
                "segments": [
                    {
                        "segment_id": segment_id,
                        "speaker_label": speaker,
                        "start_seconds": start,
                        "end_seconds": end,
                        "text": text,
                    }
                    for segment_id, speaker, start, end, text in SOURCE_SEGMENTS
                ]
            }
        ),
        processing_run_id=run_id,
        speech_pipeline_version=pipeline_version,
    )


def _fill_targets(root: Path, targets: dict[str, str]) -> None:
    for worksheet in sorted((root / "translation").glob("part-*.md")):
        lines: list[str] = []
        current = ""
        for line in worksheet.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                current = line[3:].split(" · ")[0]
            if line.startswith("TARGET: ") and current in targets:
                line = f"TARGET: {targets[current]}"
            lines.append(line)
        worksheet.write_text("\n".join(lines), encoding="utf-8")


class ListeningProjectRunner:
    """A fake Agent that follows the listening policy files."""

    def __init__(self, *, first_review_gloss: bool = False, fix: bool = True) -> None:
        self.first_review_gloss = first_review_gloss
        self.fix = fix
        self.invocations: list[tuple[str, str | None, str]] = []
        self.review_turn = 0

    def readiness(self) -> bool:
        return True

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        role = root.name
        self.invocations.append((role, session_id, instructions))
        if role == "author":
            root.joinpath("glossary.json").write_text(
                json.dumps(GLOSSARY, ensure_ascii=False),
                encoding="utf-8",
            )
            _fill_targets(root, AUTHORED)
        else:
            self.review_turn += 1
            gloss = self.first_review_gloss and (
                self.review_turn == 1 or not self.fix
            )
            _fill_targets(
                root,
                {
                    "seg-0004": "我们在给公司里每个人做答案引擎（Dots）助手。"
                    if gloss
                    else BRIDGED
                },
            )
            root.joinpath("verdict.md").write_text(
                LISTENING_VERDICT,
                encoding="utf-8",
            )
            root.joinpath("content-edit.json").write_text(
                json.dumps(CONTENT_EDIT, ensure_ascii=False),
                encoding="utf-8",
            )
            root.joinpath("publication-metadata.json").write_text(
                json.dumps(PUBLICATION_METADATA, ensure_ascii=False),
                encoding="utf-8",
            )
        return ProjectExecution(
            session_id=session_id or f"{role}-session",
            model=f"{role}-fixture",
            input_tokens=100,
            output_tokens=20,
            cli_version="fixture",
            events_jsonl="",
        )


class V3ProjectRunner(ListeningProjectRunner):
    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        self.invocations.append((root.name, session_id, instructions))
        if root.name == "author":
            _fill_targets(root, AUTHORED)
        else:
            root.joinpath("verdict.md").write_text(
                "APPROVED: yes\nTITLE: 标题\nOVERVIEW: 概述。\nUNRESOLVED:\n"
                "- (none)\nSOURCE_WARNINGS:\n- seg-0003 misspells Acme.\n",
                encoding="utf-8",
            )
        return ProjectExecution(
            session_id=session_id or f"{root.name}-session",
            model="fixture",
            input_tokens=100,
            output_tokens=20,
            cli_version="fixture",
            events_jsonl="",
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_v3_policy_texts_are_pinned() -> None:
    for name, (value, expected) in V3_TEXT_HASHES.items():
        assert _sha256(value) == expected, name
    assert PROJECT_TRANSLATION_POLICY_V3.version == "translation-policy-v3-project"
    assert PROJECT_TRANSLATION_POLICY_V3.listening is False


@pytest.mark.parametrize(
    "pipeline_version",
    [
        "",
        "transpodcast-modal-pipeline-v11",
        SPEECH_PIPELINE_V15,
        SPEECH_PIPELINE_V16,
        SPEECH_PIPELINE_V17,
        SPEECH_PIPELINE_V18,
        SPEECH_PIPELINE_V19,
        SPEECH_PIPELINE_V20,
    ],
)
def test_pipelines_before_r2_keep_the_v3_policy(pipeline_version: str) -> None:
    assert translation_policy_for_pipeline(pipeline_version) is (
        PROJECT_TRANSLATION_POLICY_V3
    )


@pytest.mark.parametrize("pipeline_version", [SPEECH_PIPELINE_V21, SPEECH_PIPELINE_V22])
def test_r2_pipelines_use_the_listening_policy(pipeline_version: str) -> None:
    policy = translation_policy_for_pipeline(pipeline_version)

    assert policy.version == LISTENING_TRANSLATION_POLICY
    assert policy.listening is True
    assert policy.agents_md == LISTENING_AGENT_GUIDANCE
    assert policy.validator == listening_validator_source()
    assert policy.author_instructions == LISTENING_AUTHOR_INSTRUCTIONS
    assert policy.review_instructions == LISTENING_REVIEW_INSTRUCTIONS
    assert policy.verdict_repair_instructions == _REVIEW_VERDICT_REPAIR_INSTRUCTIONS


def test_listening_instructions_carry_the_f1_rules() -> None:
    guidance = LISTENING_AGENT_GUIDANCE
    for required in (
        "Write for the ear",
        "Spoken numerals",
        "No parenthetical glosses",
        "only for a real trail-off",
        "Twitter → 推特",
        "Google → 谷歌",
        "Microsoft → 微软",
        "Anthropic",
        "ONE written form",
        "Never write a word-name in all capitals",
        "AI, PM, CEO",
        "Never put spaces between Chinese characters and an English word",
        "glossary.json",
        "spoken_as_english",
        "budget",
        "bridge_edits",
        "bridge_review",
        "description_sponsors",
        "not_read_in_audio",
        "VALIDATOR_NOTES",
        "asr_name",
        "asr_number",
        "asr_damaged",
        "speaker_label",
    ):
        assert required in guidance, required
    assert "glossary.json" in LISTENING_AUTHOR_INSTRUCTIONS
    assert "glossary.json" in LISTENING_REVIEW_INSTRUCTIONS
    assert "glossary" not in _PROJECT_AGENT_GUIDANCE
    assert "glossary" not in _PROJECT_AUTHOR_INSTRUCTIONS
    assert "glossary" not in _PROJECT_REVIEW_INSTRUCTIONS


def test_v20_runs_keep_the_v3_project_byte_for_byte(tmp_path: Path) -> None:
    runner = V3ProjectRunner()
    result = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate(SPEECH_PIPELINE_V20))

    (project,) = (tmp_path / "run-88").iterdir()
    assert project.name.endswith("-translation-policy-v3-project")
    for role in ("author", "review"):
        root = project / role
        assert _sha256(root.joinpath("AGENTS.md").read_text()) == (
            V3_TEXT_HASHES["agents_md"][1]
        )
        assert _sha256(root.joinpath("validate.py").read_text()) == (
            V3_TEXT_HASHES["validator"][1]
        )
        assert not root.joinpath("glossary.json").exists()
        assert not root.joinpath("source", "term-candidates.json").exists()
        segments = json.loads(root.joinpath("source", "segments.json").read_text())
        assert "budget_chars" not in segments[0]
        worksheet = root.joinpath("translation", "part-001.md").read_text()
        assert "## seg-0001 · speaker_0\n" in worksheet
    assert [instructions for _, _, instructions in runner.invocations] == [
        _PROJECT_AUTHOR_INSTRUCTIONS,
        _PROJECT_REVIEW_INSTRUCTIONS,
    ]
    assert result.policy_version == "translation-policy-v3-project"
    audit = json.loads(result.audit_json)
    assert audit["source_warnings"] == ["seg-0003 misspells Acme."]
    assert "listening" not in audit
    assert "source_warnings_schema" not in audit


def test_v21_runs_use_a_listening_project(tmp_path: Path) -> None:
    runner = ListeningProjectRunner()
    result = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate(SPEECH_PIPELINE_V21))

    (project,) = (tmp_path / "run-88").iterdir()
    assert project.name.endswith("-translation-policy-v4-listening")
    state = json.loads(project.joinpath("project.json").read_text())
    assert state["policy_version"] == LISTENING_TRANSLATION_POLICY
    author = project / "author"
    assert author.joinpath("AGENTS.md").read_text() == LISTENING_AGENT_GUIDANCE
    assert author.joinpath("validate.py").read_text() == listening_validator_source()
    segments = json.loads(author.joinpath("source", "segments.json").read_text())
    assert segments[0]["source_speech_seconds"] == 4.0
    assert segments[0]["budget_chars"] == 20
    worksheet = author.joinpath("translation", "part-001.md").read_text()
    assert "## seg-0001 · speaker_0 · 4.0 s · budget 20\n" in worksheet
    candidates = json.loads(
        author.joinpath("source", "term-candidates.json").read_text()
    )
    terms = {item["term"]: item for item in candidates["terms"]}
    assert terms["Dots"]["transcript_segment_ids"] == ["seg-0004"]
    assert terms["Jane Doe"]["transcript_mentions"] == 0
    review = project / "review"
    assert json.loads(
        review.joinpath("source", "description-sponsors.json").read_text()
    ) == {"sponsors": ["Acme"]}
    assert json.loads(review.joinpath("glossary.json").read_text()) == GLOSSARY
    assert [instructions for _, _, instructions in runner.invocations] == [
        LISTENING_AUTHOR_INSTRUCTIONS,
        LISTENING_REVIEW_INSTRUCTIONS,
    ]

    assert result.policy_version == LISTENING_TRANSLATION_POLICY
    assert result.segments[3] == BRIDGED
    audit = json.loads(result.audit_json)
    assert audit["source_warnings_schema"] == "structured-source-warnings-v1"
    assert audit["source_warnings"] == [
        {
            "kind": "asr_name",
            "segment_ids": ["seg-0003"],
            "detail": '"acme dot com" is the Acme website.',
        },
        {
            "kind": "speaker_label",
            "segment_ids": ["seg-0005", "seg-0006"],
            "detail": "the reply may belong to the host.",
        },
    ]
    listening = audit["listening"]
    assert listening["policy_version"] == LISTENING_TRANSLATION_POLICY
    assert listening["glossary"] == GLOSSARY
    assert listening["bridge_edits"] == CONTENT_EDIT["bridge_edits"]
    assert listening["errors"] == []
    assert listening["warnings"] == []
    assert listening["listening_repair_turns"] == 0
    assert audit["corrections"] == {"seg-0004": BRIDGED}
    assert json.loads(result.content_edit_json)["bridge_edits"][0][
        "segment_id"
    ] == "seg-0004"


def test_failing_validation_resumes_the_reviewer_once(tmp_path: Path) -> None:
    runner = ListeningProjectRunner(first_review_gloss=True)
    result = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate(SPEECH_PIPELINE_V22))

    roles = [(role, session) for role, session, _ in runner.invocations]
    assert roles == [
        ("author", None),
        ("review", None),
        ("review", "review-session"),
    ]
    repair = runner.invocations[2][2]
    assert repair.startswith("The completed review still fails")
    assert "ERROR parenthetical_gloss: seg-0004" in repair
    audit = json.loads(result.audit_json)
    assert audit["listening"]["listening_repair_turns"] == 1
    assert audit["listening"]["errors"] == []


def test_validation_errors_never_block_after_the_repair_turn(tmp_path: Path) -> None:
    runner = ListeningProjectRunner(first_review_gloss=True, fix=False)
    result = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=tmp_path,
    ).translate(_candidate(SPEECH_PIPELINE_V21))

    assert len(runner.invocations) == 3
    listening = json.loads(result.audit_json)["listening"]
    assert [item["code"] for item in listening["errors"]] == [
        "parenthetical_gloss",
        "bridge_edit",
    ]
    assert result.reviewed is True


class UnresponsiveReviewerRunner(ListeningProjectRunner):
    """Writes a conflicting verdict once, then ignores every repair."""

    def run_project(
        self,
        *,
        root: Path,
        instructions: str,
        session_id: str | None,
    ) -> ProjectExecution:
        if root.name == "review" and self.review_turn >= 1:
            self.review_turn += 1
            self.invocations.append((root.name, session_id, instructions))
            return ProjectExecution(
                session_id=session_id or "review-session",
                model="review-fixture",
                input_tokens=1,
                output_tokens=1,
                cli_version="fixture",
                events_jsonl="",
            )
        execution = super().run_project(
            root=root,
            instructions=instructions,
            session_id=session_id,
        )
        if root.name == "review":
            verdict = root / "verdict.md"
            verdict.write_text(
                verdict.read_text().replace(
                    "UNRESOLVED:\n- (none)",
                    "UNRESOLVED:\n- seg-0004 still needs a decision.",
                ),
                encoding="utf-8",
            )
        return execution


def test_an_ignored_listening_repair_never_stalls_the_run(tmp_path: Path) -> None:
    runner = UnresponsiveReviewerRunner(first_review_gloss=True, fix=False)

    with pytest.raises(ValueError, match="unresolved uncertainty"):
        CodexTranslationAdapter(
            runner,  # type: ignore[arg-type]
            project_root=tmp_path,
        ).translate(_candidate(SPEECH_PIPELINE_V21))

    instructions = [text for role, _, text in runner.invocations if role == "review"]
    assert len(instructions) == 3
    assert instructions[1] == _REVIEW_VERDICT_REPAIR_INSTRUCTIONS
    assert instructions[2].startswith("The completed review still fails")


def test_reprocess_resets_the_listening_repair_budget(tmp_path: Path) -> None:
    project_root = tmp_path / "translation-projects"
    CodexTranslationAdapter(
        ListeningProjectRunner(first_review_gloss=True),  # type: ignore[arg-type]
        project_root=project_root,
    ).translate(_candidate(SPEECH_PIPELINE_V21))
    mark_translation_project_reprocess(tmp_path, 88)

    runner = ListeningProjectRunner(first_review_gloss=True)
    result = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=project_root,
    ).translate(_candidate(SPEECH_PIPELINE_V21))

    assert [role for role, _, _ in runner.invocations] == ["review", "review"]
    assert json.loads(result.audit_json)["listening"]["listening_repair_turns"] == 1


def test_legacy_free_text_warnings_read_as_structured_other() -> None:
    legacy = {
        "source_warnings": [
            "seg-0010–seg-0012 contain an unclear opening question.",
            "Product names are not independently verified.",
        ]
    }

    warnings = source_warnings_from_audit(legacy)

    assert [item.kind for item in warnings] == ["other", "other"]
    assert warnings[0].segment_ids == ("seg-0010", "seg-0012")
    assert warnings[0].detail == legacy["source_warnings"][0]
    assert warnings[1].segment_ids == ()


def test_structured_audit_warnings_round_trip() -> None:
    structured = {
        "source_warnings": [
            {"kind": "asr_number", "segment_ids": ["seg-0045"], "detail": "1.2B"},
            {"kind": "invented", "segment_ids": [], "detail": "unknown kind"},
        ]
    }

    warnings = source_warnings_from_audit(structured)

    assert warnings[0].to_payload() == structured["source_warnings"][0]
    assert warnings[1].kind == "other"
    assert source_warnings_from_audit({"source_warnings": "broken"}) == ()
    assert structured_source_warning(
        "[asr_damaged] seg-0002: garbled", ["seg-0002"]
    ).kind == "asr_damaged"


def test_listening_verdict_keeps_malformed_warnings_as_other() -> None:
    verdict = LISTENING_VERDICT.replace(
        "- [speaker_label] seg-0005..seg-0006: the reply may belong to the host.",
        "- The reply in seg-0005 may belong to the host.",
    ).replace(
        "VALIDATOR_NOTES:\n- (none)",
        "VALIDATOR_NOTES:\n- seg-0004: the bridge keeps the answer.",
    )

    parsed = parse_listening_review_verdict(
        verdict, [segment[0] for segment in SOURCE_SEGMENTS]
    )

    assert parsed["source_warnings"][1] == {
        "kind": "other",
        "segment_ids": ["seg-0005"],
        "detail": "The reply in seg-0005 may belong to the host.",
    }
    assert parsed["validator_notes"] == ["seg-0004: the bridge keeps the answer."]
    assert parsed["unresolved"] == []


def test_v3_verdict_parsing_is_unchanged() -> None:
    parsed = parse_review_verdict(
        "APPROVED: yes\nTITLE: 标题\nOVERVIEW: 概述\nUNRESOLVED:\n- (none)\n"
        "SOURCE_WARNINGS:\n- seg-0003 misspells Acme.\n"
    )

    assert parsed == {
        "approved": True,
        "title": "标题",
        "overview": "概述",
        "unresolved": [],
        "source_warnings": ["seg-0003 misspells Acme."],
    }


def test_term_candidates_come_from_title_description_and_chapters() -> None:
    candidates = term_candidates(
        {
            "source_title": "Initech’s Head of Product | Jane Doe",
            "source_description": (
                "Jane leads Codex and Initech Work.\n"
                "(07:32) Bringing Codex, ChatGPT, and Dots together\n"
                "• The Rolling Labs incident: https://example.com\n"
                "• Sam Rivera on X: https://x.example/samrivera"
            ),
            "segments": [
                {"id": "seg-0001", "source_text": "I do a little lead code."},
                {"id": "seg-0002", "source_text": "We shipped dots today."},
            ],
        }
    )

    terms = {item["term"]: item for item in candidates["terms"]}
    assert {
        "Initech",
        "ChatGPT",
        "Jane Doe",
        "Codex",
        "Initech Work",
        "Dots",
        "Rolling Labs",
        "Sam Rivera",
    } <= set(terms)
    assert "Bringing Codex" not in terms
    assert terms["Jane Doe"]["metadata"] == ["title"]
    assert terms["Dots"]["metadata"] == ["chapters"]
    assert terms["Dots"]["transcript_segment_ids"] == ["seg-0002"]
    assert "https" not in " ".join(terms)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            "*Brought to you by:*\nAcme—Make your app enterprise-ready: "
            "https://acme.example/podcast\nDX—Engineering intelligence: "
            "https://dx.example\n\n*Episode transcript:* https://example.com\n"
            "For inquiries about sponsoring the podcast, email a@b.example.",
            ["Acme", "DX"],
        ),
        (
            "This episode is brought to you by Globex. Get $1000 off at "
            "globex.example/podcast",
            ["Globex"],
        ),
        ("Sponsors:\n• Initrode: https://initrode.example\n• Hooli (banking)", [
            "Initrode",
            "Hooli",
        ]),
        ("A conversation about agents with no sponsors listed.", []),
    ],
)
def test_description_sponsors(description: str, expected: list[str]) -> None:
    assert description_sponsors(description) == expected


class RetranslationRunner:
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
        self.calls.append({"instructions": instructions, "source_data": source_data})
        output: dict[str, Any] = (
            {"segments": {"seg-0004": "我们在给每个人做Dots助手。"}}
            if len(self.calls) == 1
            else {"approved": True, "corrections": [], "unresolved": []}
        )
        return CodexExecution(
            output=output,
            model="fixture",
            input_tokens=1,
            output_tokens=1,
        )


@pytest.mark.parametrize(
    ("policy_version", "listening"),
    [
        ("translation-policy-v3-project", False),
        (LISTENING_TRANSLATION_POLICY, True),
    ],
)
def test_segment_retranslation_keeps_the_episode_policy(
    policy_version: str,
    listening: bool,
) -> None:
    runner = RetranslationRunner()
    current = TranslationResult(
        title="标题",
        overview="概述",
        target_locale="zh-CN",
        segments=tuple(AUTHORED[segment[0]] for segment in SOURCE_SEGMENTS),
        provider="fixture",
        model="fixture",
        input_tokens=0,
        output_tokens=0,
        policy_version=policy_version,
        audit_json=json.dumps({"listening": {"glossary": GLOSSARY}}),
    )

    result = CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        project_root=Path("unused"),
    ).retranslate_segment(_candidate(SPEECH_PIPELINE_V21), current, 4)

    author_call = runner.calls[0]
    assert (LISTENING_SEGMENT_RULES in author_call["instructions"]) is listening
    assert ("glossary" in author_call["source_data"]) is listening
    if listening:
        assert author_call["source_data"]["glossary"][1] == {
            "source_term": "Dots",
            "written_form": "Dots",
        }
    assert result.policy_version == policy_version
