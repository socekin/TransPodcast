from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from transpodcast.adapters.codex import render_translation_worksheet
from transpodcast.content_editing import build_render_plan
from transpodcast.domain import (
    CanonicalSegment,
    SourceAnalysisResult,
    TranslationResult,
)
from transpodcast.listening_translation import (
    GLOSSARY_SKELETON,
    LISTENING_VERDICT_SKELETON,
    listening_source_data,
    listening_validator_source,
)
from transpodcast.translation_project_validator import (
    ValidationReport,
    bridge_window,
    expand_segment_ids,
    parse_source_warning,
    spoken_length,
    validate_project,
)

SEGMENTS: list[dict[str, Any]] = [
    {
        "id": "seg-0001",
        "speaker": "speaker_0",
        "source_start_seconds": 0.0,
        "source_end_seconds": 4.0,
        "source_text": "Welcome back to the show everyone.",
    },
    {
        "id": "seg-0002",
        "speaker": "speaker_1",
        "source_start_seconds": 4.0,
        "source_end_seconds": 8.0,
        "source_text": (
            "This episode is brought to you by Acme. Acme makes great widgets."
        ),
    },
    {
        "id": "seg-0003",
        "speaker": "speaker_1",
        "source_start_seconds": 8.0,
        "source_end_seconds": 12.0,
        "source_text": "Visit acme dot com today. So anyway what were you building?",
    },
    {
        "id": "seg-0004",
        "speaker": "speaker_0",
        "source_start_seconds": 12.0,
        "source_end_seconds": 16.0,
        "source_text": "We were building Dots for everyone at the company.",
    },
    {
        "id": "seg-0005",
        "speaker": "speaker_1",
        "source_start_seconds": 16.0,
        "source_end_seconds": 17.5,
        "source_text": "Right.",
    },
    {
        "id": "seg-0006",
        "speaker": "speaker_0",
        "source_start_seconds": 17.5,
        "source_end_seconds": 22.0,
        "source_text": "I tweet about it on Twitter every day.",
    },
]
TARGETS = {
    "seg-0001": "欢迎回来，大家好，今天我们接着聊下去。",
    "seg-0002": "本期节目由Acme赞助，Acme的小工具做得特别好。",
    "seg-0003": "今天就去Acme官网看看吧。那你们当时在做什么呢？",
    "seg-0004": "我们在给公司里每个人做Dots助手。",
    "seg-0005": "对。",
    "seg-0006": "我每天都会在推特上发这些内容。",
}
GLOSSARY = {
    "policy_version": "listening-glossary-v1",
    "status": "accepted",
    "entries": [
        {
            "source_term": "Acme",
            "written_form": "Acme",
            "spoken_as_english": True,
            "asr_variants": [],
        },
        {
            "source_term": "Dots",
            "written_form": "Dots",
            "spoken_as_english": True,
            "asr_variants": ["docs"],
        },
        {
            "source_term": "Twitter",
            "written_form": "推特",
            "spoken_as_english": False,
            "asr_variants": [],
        },
    ],
}
DECISION: dict[str, Any] = {
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
    "bridge_review": "The host question after the read joins seg-0001 cleanly.",
}
VERDICT = """APPROVED: yes
TITLE: 做Dots的故事
OVERVIEW: 一次关于Dots的完整对话。
UNRESOLVED:
- (none)
SOURCE_WARNINGS:
- [asr_name] seg-0003: "acme dot com" is the Acme website.
VALIDATOR_NOTES:
- (none)
"""


def _content_edit(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "policy_version": "semantic-content-edit-v1",
        "status": "accepted",
        "decisions": [dict(DECISION)],
        "bridge_edits": [],
        "description_sponsors": [
            {"sponsor": "Acme", "status": "removed", "decision_ids": ["edit-001"]}
        ],
        "retention_notes": [],
    }
    payload.update(overrides)
    return payload


def _project(
    root: Path,
    *,
    targets: dict[str, str] | None = None,
    segments: list[dict[str, Any]] | None = None,
    glossary: dict[str, Any] | str | None = None,
    review: bool = True,
    verdict: str = VERDICT,
    content_edit: dict[str, Any] | None = None,
    sponsors: list[str] | None = None,
) -> Path:
    source_data = listening_source_data(
        {
            "source_title": "Building Dots",
            "source_description": "",
            "segments": segments if segments is not None else SEGMENTS,
        }
    )
    values = dict(TARGETS if targets is None else targets)
    root.joinpath("source").mkdir(parents=True, exist_ok=True)
    root.joinpath("translation").mkdir(parents=True, exist_ok=True)
    root.joinpath("source", "segments.json").write_text(
        json.dumps(source_data["segments"], ensure_ascii=False),
        encoding="utf-8",
    )
    lines: list[str] = []
    current = ""
    worksheet = render_translation_worksheet(
        source_data,
        source_data["segments"],
        budget_headings=True,
    )
    for line in worksheet.splitlines():
        if line.startswith("## "):
            current = line[3:].split(" · ")[0]
        if line == "TARGET: (pending)" and current in values:
            line = f"TARGET: {values[current]}"
        lines.append(line)
    root.joinpath("translation", "part-001.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    glossary_value = GLOSSARY if glossary is None else glossary
    root.joinpath("glossary.json").write_text(
        glossary_value
        if isinstance(glossary_value, str)
        else json.dumps(glossary_value, ensure_ascii=False),
        encoding="utf-8",
    )
    if review:
        root.joinpath("verdict.md").write_text(verdict, encoding="utf-8")
        root.joinpath("content-edit.json").write_text(
            json.dumps(
                content_edit if content_edit is not None else _content_edit(),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        root.joinpath("source", "description-sponsors.json").write_text(
            json.dumps({"sponsors": ["Acme"] if sponsors is None else sponsors}),
            encoding="utf-8",
        )
    return root


def _codes(report: ValidationReport, severity: str) -> list[str]:
    return [item.code for item in report.findings if item.severity == severity]


def _with(segment_id: str, text: str) -> dict[str, str]:
    return {**TARGETS, segment_id: text}


def test_a_clean_listening_review_has_no_findings(tmp_path: Path) -> None:
    report = validate_project(_project(tmp_path))

    assert report.findings == []


def test_parenthetical_gloss_is_an_error(tmp_path: Path) -> None:
    report = validate_project(
        _project(
            tmp_path,
            review=False,
            targets=_with("seg-0004", "我们在做答案引擎优化（AEO）这件事。"),
        )
    )

    assert "parenthetical_gloss" in _codes(report, "error")


def test_glossary_variants_are_warnings(tmp_path: Path) -> None:
    targets = {
        **TARGETS,
        "seg-0001": "欢迎回来，今天聊聊Dot这个产品，大家好。",
        "seg-0004": "我们在给公司里每个人做docs助手。",
        "seg-0006": "我每天都会在Twitter上发这些内容。",
    }
    report = validate_project(_project(tmp_path, review=False, targets=targets))

    variants = [
        item.message for item in report.warnings if item.code == "glossary_variant"
    ]
    assert any("'Dot' should be 'Dots'" in item for item in variants)
    assert any("'docs' should be 'Dots'" in item for item in variants)
    assert any("'Twitter' should be '推特'" in item for item in variants)
    assert report.errors == []


def test_registered_multiword_names_are_not_variants(tmp_path: Path) -> None:
    glossary = json.loads(json.dumps(GLOSSARY))
    glossary["entries"].extend(
        [
            {
                "source_term": "Apple",
                "written_form": "苹果",
                "spoken_as_english": False,
            },
            {
                "source_term": "Apple Podcasts",
                "written_form": "Apple Podcasts",
                "spoken_as_english": True,
            },
        ]
    )
    report = validate_project(
        _project(
            tmp_path,
            review=False,
            glossary=glossary,
            targets=_with("seg-0006", "我每天都会在推特和Apple Podcasts上发内容。"),
        )
    )

    assert report.warnings == []


def test_unregistered_latin_words_warn_but_spoken_acronyms_do_not(
    tmp_path: Path,
) -> None:
    report = validate_project(
        _project(
            tmp_path,
            review=False,
            targets=_with("seg-0004", "我们给每个PM和CEO做AI助手，还接入了Figma。"),
        )
    )

    messages = [
        item.message
        for item in report.warnings
        if item.code == "latin_unregistered"
    ]
    assert messages == [
        "seg-0004: add Figma to glossary.json or write it in Chinese"
    ]


def test_length_budget_warns_only_for_segments_over_two_seconds(
    tmp_path: Path,
) -> None:
    targets = {
        **TARGETS,
        "seg-0001": "好。",
        "seg-0004": "我们在给公司里每个人做Dots助手，" * 4,
        "seg-0005": "对对对，没错没错，就是这样，完全同意你说的。",
    }
    report = validate_project(_project(tmp_path, review=False, targets=targets))

    budget = [item for item in report.warnings if item.code == "length_budget"]
    assert [item.segment_ids for item in budget] == [("seg-0001",), ("seg-0004",)]
    assert "too short" in budget[0].message
    assert "too long" in budget[1].message
    assert report.errors == []


def test_numerals_spacing_and_trail_off_warn(tmp_path: Path) -> None:
    targets = {
        **TARGETS,
        "seg-0001": "我们签了16家合作伙伴，用GPT-5和6.1 Acme做的。",
        "seg-0004": "我们在给公司里每个人做 Dots 助手——",
    }
    report = validate_project(_project(tmp_path, review=False, targets=targets))

    by_code = {item.code: item for item in report.warnings}
    assert "16" in by_code["numerals"].message
    assert "GPT" not in by_code["numerals"].message
    assert "6.1" not in by_code["numerals"].message
    assert by_code["latin_spacing"].segment_ids == ("seg-0004",)
    assert by_code["trail_off"].segment_ids == ("seg-0004",)


def test_glossary_must_be_complete_and_consistent(tmp_path: Path) -> None:
    pending = validate_project(
        _project(tmp_path / "pending", review=False, glossary=GLOSSARY_SKELETON)
    )
    assert "glossary_pending" in _codes(pending, "error")

    glossary = json.loads(json.dumps(GLOSSARY))
    glossary["entries"][2]["spoken_as_english"] = True
    glossary["entries"].append(dict(glossary["entries"][0]))
    inconsistent = validate_project(
        _project(tmp_path / "inconsistent", review=False, glossary=glossary)
    )
    messages = [item.message for item in inconsistent.errors]
    assert any("spoken_as_english must be true exactly" in item for item in messages)
    assert any("is duplicated" in item for item in messages)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"start_anchor": "brought to you by Globex"}, "does not occur"),
        ({"start_anchor": "Acme"}, "occurs 3 times"),
        ({"end_anchor": "Welcome back"}, "does not occur"),
        ({"retained_end_target": ""}, "write its Mandarin in retained_end_target"),
        ({"confidence": 0.8}, "confidence must be 0.90 to 1"),
        ({"bridge_review": ""}, "needs bridge_review"),
        ({"category": "self_promotion"}, "category must be paid_ad"),
    ],
)
def test_content_edit_decisions_are_validated_in_session(
    tmp_path: Path,
    change: dict[str, Any],
    message: str,
) -> None:
    decision = {**DECISION, **change}
    report = validate_project(
        _project(tmp_path, content_edit=_content_edit(decisions=[decision]))
    )

    assert any(message in item.message for item in report.errors), report.findings


def test_a_retained_start_fragment_is_required_inside_a_segment(
    tmp_path: Path,
) -> None:
    decision = {
        **DECISION,
        "start_segment_id": "seg-0002",
        "start_anchor": "Acme makes great widgets",
    }
    report = validate_project(
        _project(tmp_path, content_edit=_content_edit(decisions=[decision]))
    )

    assert any(
        "retained_start_target" in item.message for item in report.errors
    )


def test_overlapping_decisions_are_errors(tmp_path: Path) -> None:
    second = {
        **DECISION,
        "decision_id": "edit-002",
        "start_segment_id": "seg-0003",
        "end_segment_id": "seg-0004",
        "start_anchor": "So anyway what were you building",
        "end_anchor": "everyone at the company",
        "retained_start_target": "今天就去Acme官网看看吧。",
        "retained_end_target": "",
    }
    report = validate_project(
        _project(
            tmp_path,
            content_edit=_content_edit(decisions=[dict(DECISION), second]),
        )
    )

    assert "content_edit_overlap" in _codes(report, "error")


def test_adjacent_decisions_across_crosstalk_are_errors(tmp_path: Path) -> None:
    segments = json.loads(json.dumps(SEGMENTS))
    segments[3]["source_start_seconds"] = 11.5
    first = {
        **DECISION,
        "end_anchor": "what were you building",
        "retained_end_target": "",
    }
    second = {
        **DECISION,
        "decision_id": "edit-002",
        "start_segment_id": "seg-0004",
        "end_segment_id": "seg-0004",
        "start_anchor": "We were building Dots",
        "end_anchor": "everyone at the company",
        "retained_end_target": "",
    }
    report = validate_project(
        _project(
            tmp_path,
            segments=segments,
            content_edit=_content_edit(decisions=[first, second]),
        )
    )

    assert any("overlap in time" in item.message for item in report.errors)


def test_bridge_edits_are_limited_to_the_retained_neighbours(
    tmp_path: Path,
) -> None:
    targets = _with("seg-0004", "我们当时在给公司里每个人做Dots助手。")
    valid = _content_edit(
        bridge_edits=[
            {
                "decision_id": "edit-001",
                "segment_id": "seg-0004",
                "target": "我们当时在给公司里每个人做Dots助手。",
                "reason": "Answer the question retained across the cut.",
            }
        ]
    )
    assert validate_project(
        _project(tmp_path / "valid", targets=targets, content_edit=valid)
    ).errors == []

    distant = _content_edit(
        bridge_edits=[
            {
                "decision_id": "edit-001",
                "segment_id": "seg-0006",
                "target": TARGETS["seg-0006"],
                "reason": "Too far from the cut.",
            }
        ]
    )
    report = validate_project(_project(tmp_path / "distant", content_edit=distant))
    assert any(
        "may only bridge seg-0001, seg-0004, seg-0005" in item.message
        for item in report.errors
    )

    stale = _content_edit(
        bridge_edits=[
            {
                "decision_id": "edit-001",
                "segment_id": "seg-0004",
                "target": "一个没有写进TARGET的版本。",
                "reason": "The TARGET line was not edited.",
            }
        ]
    )
    report = validate_project(_project(tmp_path / "stale", content_edit=stale))
    assert any("must equal the edited TARGET" in item.message for item in report.errors)


def test_bridge_window_skips_other_removals() -> None:
    assert bridge_window((3, 4), [(3, 4), (6, 6)], 10) == [2, 1, 5, 7]
    assert bridge_window((0, 1), [(0, 1)], 3) == [2]


@pytest.mark.parametrize(
    ("sponsors", "entries", "message"),
    [
        (["Acme", "Globex"], None, "sponsor Globex is named in the description"),
        (
            ["Acme"],
            [{"sponsor": "Acme", "status": "not_read_in_audio", "note": ""}],
            "explain in note",
        ),
        (
            ["Acme"],
            [{"sponsor": "Acme", "status": "removed", "decision_ids": ["edit-9"]}],
            "decision_ids must name valid paid_ad decisions",
        ),
        (
            ["Acme"],
            [{"sponsor": "Acme", "status": "retained"}],
            "status must be removed or not_read_in_audio",
        ),
    ],
)
def test_every_description_sponsor_needs_a_decision_or_confirmation(
    tmp_path: Path,
    sponsors: list[str],
    entries: list[dict[str, Any]] | None,
    message: str,
) -> None:
    content_edit = (
        _content_edit()
        if entries is None
        else _content_edit(description_sponsors=entries)
    )
    report = validate_project(
        _project(tmp_path, sponsors=sponsors, content_edit=content_edit)
    )

    assert any(message in item.message for item in report.errors), report.findings


def test_not_read_in_audio_confirms_a_description_sponsor(tmp_path: Path) -> None:
    content_edit = _content_edit(
        description_sponsors=[
            {"sponsor": "Acme", "status": "removed", "decision_ids": ["edit-001"]},
            {
                "sponsor": "Globex",
                "status": "not_read_in_audio",
                "note": "No Globex read occurs in the transcript.",
            },
        ]
    )
    report = validate_project(
        _project(tmp_path, sponsors=["Acme", "Globex"], content_edit=content_edit)
    )

    assert report.errors == []


def test_review_warnings_must_be_fixed_or_justified(tmp_path: Path) -> None:
    targets = _with("seg-0001", "好。")
    unjustified = validate_project(_project(tmp_path / "open", targets=targets))
    assert "unjustified_warnings" in _codes(unjustified, "error")

    justified = validate_project(
        _project(
            tmp_path / "justified",
            targets=targets,
            verdict=VERDICT.replace(
                "VALIDATOR_NOTES:\n- (none)",
                "VALIDATOR_NOTES:\n- seg-0001..seg-0002: the greeting is short.",
            ),
        )
    )
    assert justified.errors == []
    assert _codes(justified, "warning") == ["length_budget"]


def test_author_warnings_never_fail(tmp_path: Path) -> None:
    report = validate_project(
        _project(tmp_path, review=False, targets=_with("seg-0001", "好。"))
    )

    assert report.errors == []
    assert _codes(report, "warning") == ["length_budget"]


def test_malformed_source_warnings_fail_the_review(tmp_path: Path) -> None:
    verdict = VERDICT.replace(
        '- [asr_name] seg-0003: "acme dot com" is the Acme website.',
        "- The transcript misspells Acme in seg-0003.",
    )
    report = validate_project(_project(tmp_path, verdict=verdict))

    assert "source_warning_format" in _codes(report, "error")


def test_structured_source_warning_parsing() -> None:
    known = [f"seg-{index:04d}" for index in range(1, 10)]

    warning, problem = parse_source_warning(
        "[asr_damaged] seg-0002..seg-0004, seg-0008: garbled clause",
        known,
    )
    assert problem is None and warning is not None
    assert warning.kind == "asr_damaged"
    assert warning.segment_ids == ("seg-0002", "seg-0003", "seg-0004", "seg-0008")
    assert warning.detail == "garbled clause"

    other, problem = parse_source_warning("[other]: unverified claims", known)
    assert problem is None and other is not None and other.segment_ids == ()

    for bullet, reason in (
        ("seg-0001: no kind", "must start with [kind]"),
        ("[typo] seg-0001: wrong kind", "kind must be one of"),
        ("[asr_name]: no ids", "list the affected segment ids"),
        ("[asr_name] seg-0042: unknown", "unknown segment ids"),
        ("[asr_name] see seg-0001: words", "put only segment ids"),
    ):
        parsed, problem = parse_source_warning(bullet, known)
        assert parsed is None
        assert problem is not None and problem.startswith(reason), bullet


def test_segment_ranges_expand_in_order() -> None:
    known = ["seg-0001", "seg-0002", "seg-0003", "seg-0004"]

    assert expand_segment_ids("seg-0002–seg-0004", known) == [
        "seg-0002",
        "seg-0003",
        "seg-0004",
    ]
    assert expand_segment_ids("seg-0004, seg-0001", known) == [
        "seg-0004",
        "seg-0001",
    ]


def test_spoken_length_counts_syllables() -> None:
    assert spoken_length("我们用ChatGPT做的") == 5 + 4
    assert spoken_length("十二亿") == 3
    assert spoken_length("Dots和AI") == 1 + 1 + 2


def test_validate_py_runs_as_a_standalone_script(tmp_path: Path) -> None:
    root = _project(tmp_path / "clean")
    root.joinpath("validate.py").write_text(
        listening_validator_source(),
        encoding="utf-8",
    )
    clean = subprocess.run(
        [sys.executable, "-I", "validate.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stdout
    assert "Translation project targets are complete" in clean.stdout

    failing = _project(
        tmp_path / "failing",
        targets=_with("seg-0004", "我们在做答案引擎优化（AEO）。"),
    )
    failing.joinpath("validate.py").write_text(
        listening_validator_source(),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-I", "validate.py"],
        cwd=failing,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "ERROR parenthetical_gloss" in result.stdout


def test_pending_verdict_skeleton_fails_review(tmp_path: Path) -> None:
    report = validate_project(_project(tmp_path, verdict=LISTENING_VERDICT_SKELETON))

    messages = [item.message for item in report.errors]
    assert "set APPROVED to yes or no in verdict.md" in messages
    assert "complete TITLE and OVERVIEW in verdict.md" in messages


def test_a_validated_decision_is_applied_by_the_render_plan() -> None:
    """The validator and the Render Plan agree on anchors and fragments."""

    canonical = tuple(
        CanonicalSegment(
            segment_id=item["id"],
            speaker_label=item["speaker"],
            start_seconds=item["source_start_seconds"],
            end_seconds=item["source_end_seconds"],
            text=item["source_text"],
            words=_timed_words(item),
        )
        for item in SEGMENTS
    )
    analysis = SourceAnalysisResult(
        segments=canonical,
        pipeline_version="fixture-v21",
        diarization_model="fixture",
        transcription_model="fixture",
        alignment_model="fixture",
    )
    translation = TranslationResult(
        title="t",
        overview="o",
        target_locale="zh-CN",
        segments=tuple(TARGETS[item["id"]] for item in SEGMENTS),
        provider="fixture",
        model="fixture",
        input_tokens=0,
        output_tokens=0,
        content_edit_json=json.dumps(_content_edit(), ensure_ascii=False),
    )

    plan = build_render_plan(analysis, translation)

    assert plan.status == "accepted"
    assert plan.diagnostics == ()
    assert [unit.unit_id for unit in plan.units] == [
        "seg-0001",
        "seg-0003-retained-after",
        "seg-0004",
        "seg-0005",
        "seg-0006",
    ]
    assert plan.translation.segments[1] == DECISION["retained_end_target"]


def _timed_words(item: dict[str, Any]) -> tuple[tuple[str, float, float], ...]:
    words = item["source_text"].split()
    start = float(item["source_start_seconds"])
    step = (float(item["source_end_seconds"]) - start) / len(words)
    return tuple(
        (word, round(start + index * step, 3), round(start + (index + 1) * step, 3))
        for index, word in enumerate(words)
    )
