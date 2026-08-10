from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from transpodcast.i18n import zh_cn

PUBLICATION_METADATA_POLICY_VERSION = "publication-metadata-v1"
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_PLACEHOLDER_OR_INSTRUCTION = re.compile(
    r"(?:"
    r"\b(?:todo|tbd|placeholder|fill\s+in|replace\s+this)\b"
    r"|\b(?:copy|follow)\s+(?:the\s+)?(?:worksheet|instructions?)\b"
    rf"|{zh_cn('publication_metadata.placeholder_pattern')}"
    r"|<[^>]{1,80}>|\[[^\]]*placeholder[^\]]*\]"
    r")",
    re.IGNORECASE,
)
_MAX_PARTICIPANT_LINE_LENGTH = 120


@dataclass(frozen=True)
class ChapterProposal:
    anchor_segment_id: str
    title: str
    rationale: str = ""


@dataclass(frozen=True)
class PublicationMetadata:
    description: str
    participant_line: str
    chapters: tuple[ChapterProposal, ...]
    status: str
    policy_version: str = PUBLICATION_METADATA_POLICY_VERSION
    error: str = ""

    def artifact_json(self) -> str:
        return json.dumps(
            {
                "policy_version": self.policy_version,
                "status": self.status,
                "description": self.description,
                "participant_line": self.participant_line,
                "chapters": [
                    {
                        "anchor_segment_id": chapter.anchor_segment_id,
                        "title": chapter.title,
                        "rationale": chapter.rationale,
                    }
                    for chapter in self.chapters
                ],
                "error": self.error,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def parse_publication_metadata(value: str) -> PublicationMetadata:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("Publication Metadata is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("Publication Metadata must be an object")
    policy = str(payload.get("policy_version") or "")
    if policy != PUBLICATION_METADATA_POLICY_VERSION:
        raise ValueError("Publication Metadata policy is unsupported")
    if payload.get("status") != "accepted":
        raise ValueError("Publication Metadata is unfinished")
    raw_description = payload.get("description")
    if not isinstance(raw_description, str):
        raise ValueError("Publication Description must be text")
    description = validate_publication_description(raw_description)

    raw_participant_line = payload.get("participant_line")
    if not isinstance(raw_participant_line, str):
        raise ValueError("Publication participant line must be text")
    participant_line = validate_participant_line(raw_participant_line)
    raw_chapters = payload.get("chapters")
    if not isinstance(raw_chapters, list):
        raise ValueError("Publication Metadata chapters must be a list")
    chapters: list[ChapterProposal] = []
    seen_anchors: set[str] = set()
    for item in raw_chapters:
        if not isinstance(item, dict):
            raise ValueError("Publication Chapter proposal must be an object")
        raw_anchor = item.get("anchor_segment_id")
        raw_title = item.get("title")
        raw_rationale = item.get("rationale")
        if (
            not isinstance(raw_anchor, str)
            or not isinstance(raw_title, str)
            or not isinstance(raw_rationale, str)
        ):
            raise ValueError(
                "Publication Chapter proposal fields must be text"
            )
        anchor = raw_anchor.strip()
        title = raw_title.strip()
        rationale = raw_rationale.strip()
        if (
            not anchor
            or not title
            or len(title) > 45
            or "\n" in title
            or _CJK.search(title) is None
            or _PLACEHOLDER_OR_INSTRUCTION.search(title)
        ):
            raise ValueError("Publication Chapter proposal is invalid")
        if anchor in seen_anchors:
            raise ValueError("Publication Chapter anchors must be unique")
        seen_anchors.add(anchor)
        chapters.append(
            ChapterProposal(
                anchor_segment_id=anchor,
                title=title,
                rationale=rationale,
            )
        )
    return PublicationMetadata(
        description=description,
        participant_line=participant_line,
        chapters=tuple(chapters),
        status="accepted",
        policy_version=policy,
    )


def validate_publication_description(value: str) -> str:
    description = value.strip()
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", description)
        if paragraph.strip()
    ]
    cjk_count = len(_CJK.findall(description))
    if not 350 <= cjk_count <= 600 or len(paragraphs) not in {2, 3}:
        raise ValueError(
            "Publication Description must contain 350 to 600 CJK "
            "characters in two or three paragraphs"
        )
    if _PLACEHOLDER_OR_INSTRUCTION.search(description):
        raise ValueError(
            "Publication Description contains placeholder or worksheet text"
        )
    return "\n\n".join(paragraphs)


def validate_participant_line(value: str) -> str:
    participant_line = value.strip()
    if not participant_line:
        return ""
    if (
        "\n" in participant_line
        or len(participant_line) > _MAX_PARTICIPANT_LINE_LENGTH
    ):
        raise ValueError("Publication participant line must be compact")
    if _PLACEHOLDER_OR_INSTRUCTION.search(participant_line):
        raise ValueError(
            "Publication participant line contains placeholder or worksheet text"
        )
    return participant_line


def validate_publication_copy(value: str) -> str:
    try:
        return validate_publication_description(value)
    except ValueError as original_error:
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", value.strip())
            if paragraph.strip()
        ]
        if len(paragraphs) not in {3, 4}:
            raise original_error
        participant_line = validate_participant_line(paragraphs[-1])
        description = validate_publication_description(
            "\n\n".join(paragraphs[:-1])
        )
        return f"{description}\n\n{participant_line}"


def degraded_publication_metadata(
    overview: str,
    error: str,
) -> PublicationMetadata:
    return PublicationMetadata(
        description=overview.strip(),
        participant_line="",
        chapters=(),
        status="degraded",
        error=error[:500],
    )


def resolve_episode_chapters(
    metadata: PublicationMetadata,
    render_segment_ids: tuple[str, ...],
    segment_timings: tuple[tuple[float, float], ...],
    duration_seconds: int,
) -> tuple[tuple[int, str], ...]:
    chapters, _diagnostics = resolve_episode_chapters_with_diagnostics(
        metadata,
        render_segment_ids,
        segment_timings,
        duration_seconds,
    )
    return chapters


def resolve_episode_chapters_with_diagnostics(
    metadata: PublicationMetadata,
    render_segment_ids: tuple[str, ...],
    segment_timings: tuple[tuple[float, float], ...],
    duration_seconds: int,
) -> tuple[tuple[tuple[int, str], ...], tuple[dict[str, str], ...]]:
    diagnostics = [
        {"status": "excluded", "reason": "not_evaluated"}
        for _proposal in metadata.chapters
    ]
    if metadata.status != "accepted":
        return (), tuple(
            {"status": "excluded", "reason": "metadata_degraded"}
            for _proposal in metadata.chapters
        )
    if len(render_segment_ids) != len(segment_timings):
        return (), tuple(
            {
                "status": "excluded",
                "reason": "timing_evidence_unavailable",
            }
            for _proposal in metadata.chapters
        )
    resolved: list[tuple[int, str, int]] = []
    for proposal_index, proposal in enumerate(metadata.chapters):
        matches = [
            index
            for index, segment_id in enumerate(render_segment_ids)
            if _canonical_lineage(segment_id) == proposal.anchor_segment_id
        ]
        if not matches:
            diagnostics[proposal_index] = {
                "status": "excluded",
                "reason": "anchor_missing",
            }
            continue
        if len(matches) != 1:
            diagnostics[proposal_index] = {
                "status": "excluded",
                "reason": "anchor_ambiguous",
            }
            continue
        start = segment_timings[matches[0]][0]
        if not math.isfinite(start) or not 0 <= start < duration_seconds:
            diagnostics[proposal_index] = {
                "status": "excluded",
                "reason": "timestamp_out_of_range",
            }
            continue
        rounded_start = round(start)
        if rounded_start >= duration_seconds:
            diagnostics[proposal_index] = {
                "status": "excluded",
                "reason": "timestamp_out_of_range",
            }
            continue
        resolved.append((rounded_start, proposal.title, proposal_index))
    resolved.sort(key=lambda item: item[0])
    normalized: list[tuple[int, str, int]] = []
    for start, title, proposal_index in resolved:
        start = 0 if not normalized else start
        if normalized and start - normalized[-1][0] < 120:
            diagnostics[proposal_index] = {
                "status": "excluded",
                "reason": "minimum_spacing",
            }
            continue
        if normalized and start <= normalized[-1][0]:
            diagnostics[proposal_index] = {
                "status": "excluded",
                "reason": "non_increasing_timestamp",
            }
            continue
        normalized.append((start, title, proposal_index))
    maximum = int(duration_seconds * 6 / 3600)
    if maximum < 3:
        for _start, _title, proposal_index in normalized:
            diagnostics[proposal_index] = {
                "status": "excluded",
                "reason": "episode_too_short_for_chapter_density",
            }
        return (), tuple(diagnostics)
    for _start, _title, proposal_index in normalized[maximum:]:
        diagnostics[proposal_index] = {
            "status": "excluded",
            "reason": "hourly_density_limit",
        }
    normalized = normalized[:maximum]
    if len(normalized) < 3:
        for _start, _title, proposal_index in normalized:
            diagnostics[proposal_index] = {
                "status": "excluded",
                "reason": "insufficient_valid_chapters",
            }
        return (), tuple(diagnostics)
    for _start, _title, proposal_index in normalized:
        diagnostics[proposal_index] = {
            "status": "included",
            "reason": "included",
        }
    return (
        tuple((start, title) for start, title, _index in normalized),
        tuple(diagnostics),
    )


def compose_publication_description(
    description: str,
    chapters: tuple[tuple[int, str], ...],
    source_url: str,
) -> str:
    sections = [description.strip()]
    if chapters:
        sections.append(
            "\n".join(
                f"{format_timestamp(start)} {title}"
                for start, title in chapters
            )
        )
    sections.append(f"Source: {source_url}")
    return "\n\n".join(section for section in sections if section)


def format_timestamp(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _canonical_lineage(segment_id: str) -> str:
    return segment_id.split("-retained-", maxsplit=1)[0]


def publication_metadata_schema() -> dict[str, Any]:
    return {
        "policy_version": PUBLICATION_METADATA_POLICY_VERSION,
        "status": "pending",
        "description": "",
        "participant_line": "",
        "chapters": [],
    }


def publication_metadata_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "policy_version",
            "status",
            "description",
            "participant_line",
            "chapters",
        ],
        "properties": {
            "policy_version": {
                "type": "string",
                "const": PUBLICATION_METADATA_POLICY_VERSION,
            },
            "status": {"type": "string", "const": "accepted"},
            "description": {"type": "string"},
            "participant_line": {"type": "string"},
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "anchor_segment_id",
                        "title",
                        "rationale",
                    ],
                    "properties": {
                        "anchor_segment_id": {"type": "string"},
                        "title": {"type": "string", "maxLength": 45},
                        "rationale": {"type": "string"},
                    },
                },
            },
        },
    }
