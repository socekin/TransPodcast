from __future__ import annotations

import json

import pytest

from transpodcast.publication_metadata import (
    parse_publication_metadata,
    resolve_episode_chapters,
    validate_publication_copy,
)


def _metadata(chapter_ids: list[str]) -> str:
    return json.dumps(
        {
            "policy_version": "publication-metadata-v1",
            "status": "accepted",
            "description": (
                f"{chr(0x4E00) * 180}\n\n{chr(0x4E01) * 180}"
            ),
            "participant_line": "",
            "chapters": [
                {
                    "anchor_segment_id": segment_id,
                    "title": chr(0x4E10 + index) * 4,
                    "rationale": f"Topic {index}",
                }
                for index, segment_id in enumerate(chapter_ids)
            ],
        }
    )


def test_chapters_use_final_retained_timings_and_drop_ambiguous_anchors() -> None:
    metadata = parse_publication_metadata(
        _metadata(["seg-0001", "seg-0002", "seg-0003", "seg-0004", "seg-0005"])
    )

    chapters = resolve_episode_chapters(
        metadata,
        (
            "seg-0001",
            "seg-0003-retained-1",
            "seg-0004-retained-1",
            "seg-0004-retained-2",
            "seg-0005",
        ),
        (
            (60.0, 120.0),
            (300.0, 360.0),
            (450.0, 470.0),
            (480.0, 500.0),
            (600.0, 660.0),
        ),
        1800,
    )

    assert [item[0] for item in chapters] == [0, 300, 600]


def test_fewer_than_three_valid_chapters_publishes_no_chapters() -> None:
    metadata = parse_publication_metadata(
        _metadata(["seg-0001", "seg-0002", "seg-0003"])
    )

    chapters = resolve_episode_chapters(
        metadata,
        ("seg-0001", "seg-0003"),
        ((0.0, 10.0), (300.0, 310.0)),
        1800,
    )

    assert chapters == ()


def test_short_episode_does_not_exceed_six_chapters_per_hour() -> None:
    metadata = parse_publication_metadata(
        _metadata(["seg-0001", "seg-0002", "seg-0003"])
    )

    chapters = resolve_episode_chapters(
        metadata,
        ("seg-0001", "seg-0002", "seg-0003"),
        ((0.0, 10.0), (120.0, 130.0), (240.0, 250.0)),
        1200,
    )

    assert chapters == ()


def test_rounded_chapter_time_cannot_equal_the_audio_duration() -> None:
    metadata = parse_publication_metadata(
        _metadata(["seg-0001", "seg-0002", "seg-0003"])
    )

    chapters = resolve_episode_chapters(
        metadata,
        ("seg-0001", "seg-0002", "seg-0003"),
        (
            (0.0, 10.0),
            (300.0, 310.0),
            (1799.6, 1799.9),
        ),
        1800,
    )

    assert chapters == ()


def test_publication_description_requires_two_or_three_substantial_paragraphs(
) -> None:
    payload = json.loads(_metadata([]))
    payload["description"] = chr(0x4E00) * 360

    with pytest.raises(ValueError, match="two or three paragraphs"):
        parse_publication_metadata(json.dumps(payload))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", f"{'一' * 180}\n\n{'二' * 170} TODO 请填写摘要"),
        ("participant_line", "[请填写主持人和嘉宾]"),
        ("participant_line", "主持人与嘉宾：" + "甲" * 120),
    ],
)
def test_publication_metadata_rejects_placeholder_or_bloated_copy(
    field: str,
    value: str,
) -> None:
    payload = json.loads(_metadata([]))
    payload[field] = value

    with pytest.raises(ValueError):
        parse_publication_metadata(json.dumps(payload))


def test_manual_publication_copy_accepts_three_paragraphs_and_participants(
) -> None:
    description = (
        f"{'一' * 180}\n\n{'二' * 180}\n\n{'三' * 180}"
    )
    participant_line = "主持人：甲；嘉宾：乙"

    validated = validate_publication_copy(
        f"{description}\n\n{participant_line}"
    )

    assert validated.endswith(participant_line)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("anchor_segment_id", 123),
        ("title", True),
        ("rationale", None),
        ("title", "TODO"),
        ("title", "Research outlook"),
    ],
)
def test_publication_chapters_require_typed_mandarin_copy(
    field: str,
    value: object,
) -> None:
    payload = json.loads(_metadata(["seg-0001"]))
    payload["chapters"][0][field] = value

    with pytest.raises(ValueError):
        parse_publication_metadata(json.dumps(payload))
