"""Validator for listening-policy Translation Projects.

The Processing Service copies this file verbatim into every Translation
Project created under the listening translation policy as ``validate.py``,
and imports the same functions to re-check a completed review in-process.
It must therefore depend only on the Python standard library.

ERROR findings make ``python validate.py`` exit non-zero. WARNING findings
are listening risks that never fail on their own; in a Reviewer project
(one with ``verdict.md``) each warned segment must be fixed or justified
under ``VALIDATOR_NOTES`` in ``verdict.md``.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALIDATOR_VERSION = "listening-validator-v1"
GLOSSARY_POLICY_VERSION = "listening-glossary-v1"
CONTENT_EDIT_POLICY_VERSION = "semantic-content-edit-v1"
TARGET_PLACEHOLDER = "(pending)"
BUDGET_CHARACTERS_PER_SECOND = 5.0
BUDGET_MINIMUM_SECONDS = 2.0
BUDGET_LOW_RATIO = 0.5
BUDGET_HIGH_RATIO = 1.4
BRIDGE_WINDOW_SEGMENTS = 2
MINIMUM_CONTENT_EDIT_CONFIDENCE = 0.90
CONTENT_EDIT_CATEGORIES = frozenset({"opening_highlight", "paid_ad"})
SPONSOR_STATUSES = frozenset({"removed", "not_read_in_audio"})
SOURCE_WARNING_KINDS = (
    "asr_name",
    "asr_number",
    "asr_damaged",
    "speaker_label",
    "other",
)

_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_PUNCTUATION = "\u3000-\u303f\uff00-\uffef"
_CJK_CHARACTER = re.compile(f"[{_CJK}]")
_DIGIT = re.compile(r"\d")
_LATIN_WORD = r"[A-Za-z][A-Za-z0-9]*(?:[.'\u2019+#&-][A-Za-z0-9]+)*(?:\+\+|#)?"
_LATIN_SPAN = re.compile(rf"{_LATIN_WORD}(?: {_LATIN_WORD})*")
_LATIN_PART = re.compile(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z]+|[A-Z]")
_ACRONYM = re.compile(r"(?=.*[A-Z])[A-Z0-9][A-Z0-9.+&#-]*")
# Identical to the Render Plan token rule in ``content_editing.py``.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['\u2019][A-Za-z0-9]+)?")
_PARENTHESES = re.compile(r"[()\uff08\uff09]")
_TRAIL_OFF = re.compile(r"\u2014|\u2026|\.\.\.")
_LATIN_SPACING = re.compile(
    rf"[{_CJK}{_CJK_PUNCTUATION}][ \t]+[A-Za-z0-9]"
    rf"|[A-Za-z0-9][ \t]+[{_CJK}{_CJK_PUNCTUATION}]"
)
_NUMBER = re.compile(r"\d+(?:[.,:/]\d+)*")
_SEGMENT_ID = r"seg-\d{4,}"
_SEGMENT_REFERENCE = re.compile(
    rf"({_SEGMENT_ID})(?:\s*(?:\.\.|\u2013|\u2014|~|-|to)\s*({_SEGMENT_ID}))?"
)
_SOURCE_WARNING_LINE = re.compile(
    r"^\[(?P<kind>[A-Za-z_]+)\]\s*(?P<body>.*)$",
    re.DOTALL,
)


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str
    segment_ids: tuple[str, ...] = ()

    def line(self) -> str:
        return f"{self.severity.upper()} {self.code}: {self.message}"

    def to_payload(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "segment_ids": list(self.segment_ids),
        }


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "warning"]

    def error(
        self,
        code: str,
        message: str,
        segment_ids: Iterable[str] = (),
    ) -> None:
        self.findings.append(Finding("error", code, message, tuple(segment_ids)))

    def warning(
        self,
        code: str,
        message: str,
        segment_ids: Iterable[str] = (),
    ) -> None:
        self.findings.append(
            Finding("warning", code, message, tuple(segment_ids))
        )


@dataclass(frozen=True)
class GlossaryEntry:
    source_term: str
    written_form: str
    spoken_as_english: bool
    asr_variants: tuple[str, ...] = ()


@dataclass(frozen=True)
class StructuredSourceWarning:
    kind: str
    segment_ids: tuple[str, ...]
    detail: str

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "segment_ids": list(self.segment_ids),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class VerdictForm:
    fields: dict[str, str]
    sections: dict[str, list[str]]


def spoken_length(text: str) -> int:
    """Estimate spoken syllables: Mandarin characters, digits, Latin parts.

    A capitalized run (``GPT``) is spelled letter by letter; any other Latin
    part counts its vowel groups, at least one.
    """

    count = len(_CJK_CHARACTER.findall(text)) + len(_DIGIT.findall(text))
    for word in re.findall(r"[A-Za-z]+", text):
        for part in _LATIN_PART.findall(word):
            if part.isupper():
                count += len(part)
            else:
                count += max(1, len(re.findall(r"[aeiouy]+", part.lower())))
    return count


def budget_characters(seconds: float) -> int:
    return round(seconds * BUDGET_CHARACTERS_PER_SECOND)


def source_tokens(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text)


def expand_segment_ids(text: str, known_ids: Sequence[str]) -> list[str]:
    """Return the segment ids a text references, expanding ``a..b`` ranges."""

    positions = {segment_id: index for index, segment_id in enumerate(known_ids)}
    found: list[str] = []
    for match in _SEGMENT_REFERENCE.finditer(text):
        first, last = match.group(1), match.group(2)
        if (
            last is not None
            and first in positions
            and last in positions
            and positions[first] <= positions[last]
        ):
            found.extend(known_ids[positions[first] : positions[last] + 1])
            continue
        found.append(first)
        if last is not None:
            found.append(last)
    return list(dict.fromkeys(found))


def parse_source_warning(
    bullet: str,
    known_ids: Sequence[str],
) -> tuple[StructuredSourceWarning | None, str | None]:
    """Parse ``[kind] seg-0001, seg-0002: detail`` into a structured warning.

    Returns the warning, or ``None`` and the reason it is malformed.
    """

    match = _SOURCE_WARNING_LINE.match(bullet.strip())
    if match is None:
        return None, "must start with [kind]"
    kind = match.group("kind").lower()
    if kind not in SOURCE_WARNING_KINDS:
        return None, f"kind must be one of {', '.join(SOURCE_WARNING_KINDS)}"
    body = match.group("body").strip()
    if ":" not in body:
        return None, "must be '[kind] segment ids: detail'"
    references, detail = body.split(":", 1)
    detail = detail.strip()
    if not detail:
        return None, "detail is empty"
    segment_ids = expand_segment_ids(references, known_ids)
    leftover = _SEGMENT_REFERENCE.sub("", references)
    if re.sub(r"[\s,\uff0c;&]|and", "", leftover):
        return None, "put only segment ids before the colon"
    unknown = [item for item in segment_ids if item not in set(known_ids)]
    if known_ids and unknown:
        return None, f"unknown segment ids {', '.join(unknown[:5])}"
    if not segment_ids and kind != "other":
        return None, "list the affected segment ids before the colon"
    return StructuredSourceWarning(kind, tuple(segment_ids), detail), None


def parse_verdict(text: str) -> VerdictForm:
    fields: dict[str, str] = {}
    sections: dict[str, list[str]] = {
        "UNRESOLVED": [],
        "SOURCE_WARNINGS": [],
        "VALIDATOR_NOTES": [],
    }
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        key = line.split(":", 1)[0]
        if key in ("APPROVED", "TITLE", "OVERVIEW") and ":" in line:
            fields[key] = line.split(":", 1)[1].strip()
            section = ""
        elif line.endswith(":") and line[:-1] in sections:
            section = line[:-1]
        elif line.startswith("- ") and section:
            value = line[2:].strip()
            if value and value != "(none)":
                sections[section].append(value)
    return VerdictForm(fields, sections)


def validate_project(root: Path) -> ValidationReport:
    report = ValidationReport()
    segments = _load_segments(root, report)
    known_ids = [str(item.get("id", "")) for item in segments]
    order, targets, pending = _read_worksheets(root, report)
    if pending:
        report.error(
            "pending_targets",
            f"{len(pending)} TARGET lines remain pending",
            pending[:20],
        )
    for segment_id in order:
        text = targets.get(segment_id)
        if text:
            _check_target_text(report, segment_id, text)
    _check_budget(report, segments, targets)
    entries = _check_glossary(report, root)
    if entries is not None:
        _check_glossary_usage(report, entries, order, targets)
    if (root / "verdict.md").is_file():
        _check_content_edit(report, root, segments, targets)
        _check_verdict(report, root, known_ids)
    return report


def _load_segments(root: Path, report: ValidationReport) -> list[dict[str, Any]]:
    path = root / "source" / "segments.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report.error("source_missing", "source/segments.json is unreadable")
        return []
    if not isinstance(value, list):
        report.error("source_missing", "source/segments.json is malformed")
        return []
    return [item for item in value if isinstance(item, dict)]


def _read_worksheets(
    root: Path,
    report: ValidationReport,
) -> tuple[list[str], dict[str, str], list[str]]:
    order: list[str] = []
    targets: dict[str, str] = {}
    pending: list[str] = []
    paths = sorted((root / "translation").glob("part-*.md"))
    if not paths:
        report.error("worksheets_missing", "translation/part-*.md is missing")
    for path in paths:
        current = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                current = line[3:].split(" \u00b7 ")[0].strip()
            elif line.startswith("TARGET:") and current:
                value = line[len("TARGET:") :].strip()
                order.append(current)
                if not value or value == TARGET_PLACEHOLDER:
                    pending.append(current)
                else:
                    targets[current] = value
    return order, targets, pending


def _check_target_text(
    report: ValidationReport,
    segment_id: str,
    text: str,
    *,
    label: str = "",
) -> None:
    where = label or segment_id
    if _PARENTHESES.search(text):
        report.error(
            "parenthetical_gloss",
            f"{where}: remove brackets; say one spoken form instead of a gloss",
            (segment_id,),
        )
    if _TRAIL_OFF.search(text):
        report.warning(
            "trail_off",
            f"{where}: a dash or ellipsis is only for a speaker who truly "
            "trails off; otherwise end at a natural pause",
            (segment_id,),
        )
    if _LATIN_SPACING.search(text):
        report.warning(
            "latin_spacing",
            f"{where}: remove spaces between Chinese text and English words",
            (segment_id,),
        )
    numerals = [
        match.group(0)
        for match in _NUMBER.finditer(text)
        if not _is_label_number(text, match.start(), match.end())
    ]
    if numerals:
        report.warning(
            "numerals",
            f"{where}: write {', '.join(numerals[:4])} as spoken Mandarin "
            "numerals unless it is part of a name or version label",
            (segment_id,),
        )


def _is_label_number(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 2) : start]
    after = text[end : end + 2]
    if before[-1:] and re.match(r"[A-Za-z-]", before[-1:]):
        return True
    if after[:1] and re.match(r"[A-Za-z-]", after[:1]):
        return True
    return bool(
        re.fullmatch(r" [A-Za-z]", after) or re.fullmatch(r"[A-Za-z] ", before)
    )


def _speech_seconds(segment: Mapping[str, Any]) -> float | None:
    value = segment.get("source_speech_seconds")
    if isinstance(value, int | float):
        return float(value)
    start = segment.get("source_start_seconds")
    end = segment.get("source_end_seconds")
    if isinstance(start, int | float) and isinstance(end, int | float):
        return float(end) - float(start)
    return None


def _check_budget(
    report: ValidationReport,
    segments: Sequence[Mapping[str, Any]],
    targets: Mapping[str, str],
) -> None:
    for segment in segments:
        segment_id = str(segment.get("id", ""))
        target = targets.get(segment_id)
        seconds = _speech_seconds(segment)
        if not target or seconds is None or seconds <= BUDGET_MINIMUM_SECONDS:
            continue
        budget = segment.get("budget_chars")
        if not isinstance(budget, int) or budget <= 0:
            budget = budget_characters(seconds)
        if budget <= 0:
            continue
        length = spoken_length(target)
        ratio = length / budget
        if BUDGET_LOW_RATIO <= ratio <= BUDGET_HIGH_RATIO:
            continue
        direction = "long" if ratio > BUDGET_HIGH_RATIO else "short"
        report.warning(
            "length_budget",
            f"{segment_id}: {length} spoken characters against a budget of "
            f"{budget} ({ratio:.2f}x, {seconds:.1f} s) is too {direction}",
            (segment_id,),
        )


def _check_glossary(
    report: ValidationReport,
    root: Path,
) -> list[GlossaryEntry] | None:
    path = root / "glossary.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        report.error("glossary_missing", "glossary.json is missing")
        return None
    except ValueError:
        report.error("glossary_malformed", "glossary.json is not valid JSON")
        return None
    if not isinstance(payload, dict):
        report.error("glossary_malformed", "glossary.json must be an object")
        return None
    if payload.get("policy_version") != GLOSSARY_POLICY_VERSION:
        report.error(
            "glossary_malformed",
            f"glossary.json policy_version must be {GLOSSARY_POLICY_VERSION}",
        )
    if payload.get("status") != "accepted":
        report.error(
            "glossary_pending",
            "complete glossary.json and set status to accepted",
        )
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        report.error("glossary_malformed", "glossary.json entries must be a list")
        return None
    entries: list[GlossaryEntry] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_entries, start=1):
        label = f"glossary entry {index}"
        if not isinstance(raw, dict):
            report.error("glossary_malformed", f"{label} must be an object")
            continue
        source_term = raw.get("source_term")
        written_form = raw.get("written_form")
        spoken_as_english = raw.get("spoken_as_english")
        variants = raw.get("asr_variants", [])
        if not isinstance(source_term, str) or not source_term.strip():
            report.error("glossary_malformed", f"{label} needs source_term")
            continue
        label = f"glossary entry '{source_term.strip()}'"
        if not isinstance(written_form, str) or not written_form.strip():
            report.error("glossary_malformed", f"{label} needs written_form")
            continue
        if not isinstance(spoken_as_english, bool):
            report.error(
                "glossary_malformed",
                f"{label} needs spoken_as_english true or false",
            )
            continue
        if not isinstance(variants, list) or not all(
            isinstance(item, str) for item in variants
        ):
            report.error(
                "glossary_malformed",
                f"{label} asr_variants must be a list of strings",
            )
            continue
        written = written_form.strip()
        has_latin = bool(re.search(r"[A-Za-z]", written))
        if spoken_as_english != has_latin:
            report.error(
                "glossary_malformed",
                f"{label}: spoken_as_english must be true exactly when "
                "written_form uses Latin letters",
            )
        if _PARENTHESES.search(written) or _LATIN_SPACING.search(written):
            report.error(
                "glossary_malformed",
                f"{label}: written_form must be one spoken form without "
                "brackets or spaces next to Chinese characters",
            )
        key = source_term.strip().casefold()
        if key in seen:
            report.error("glossary_malformed", f"{label} is duplicated")
            continue
        seen.add(key)
        entries.append(
            GlossaryEntry(
                source_term=source_term.strip(),
                written_form=written,
                spoken_as_english=spoken_as_english,
                asr_variants=tuple(
                    item.strip() for item in variants if item.strip()
                ),
            )
        )
    return entries


def _compact(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", value.casefold())


def _same_name(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    shorter = min(len(left), len(right))
    return shorter >= 2 and (left + "s" == right or right + "s" == left)


def _contains_term(text: str, term: str) -> bool:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return pattern.search(text) is not None


def _check_glossary_usage(
    report: ValidationReport,
    entries: Sequence[GlossaryEntry],
    order: Sequence[str],
    targets: Mapping[str, str],
) -> None:
    written_forms = {entry.written_form for entry in entries}
    latin_forms = sorted(
        (
            entry.written_form.split(" ")
            for entry in entries
            if entry.spoken_as_english
        ),
        key=len,
        reverse=True,
    )
    form_pattern = (
        re.compile(
            "|".join(
                rf"(?<![A-Za-z0-9]){re.escape(' '.join(form))}(?![A-Za-z0-9])"
                for form in latin_forms
            )
        )
        if latin_forms
        else None
    )
    for segment_id in order:
        text = targets.get(segment_id)
        if not text:
            continue
        spans = [match.group(0) for match in _LATIN_SPAN.finditer(text)]
        # Source terms and ASR variants are searched outside the registered
        # Latin names, so "Apple" inside "Apple Podcasts" is not a variant.
        remainder = form_pattern.sub("\u0000", text) if form_pattern else text
        variants: list[str] = []
        for entry in entries:
            if entry.spoken_as_english:
                key = _compact(entry.written_form)
                for span in spans:
                    words = span.split(" ")
                    for start in range(len(words)):
                        for end in range(start + 1, len(words) + 1):
                            gram = " ".join(words[start:end])
                            if (
                                gram != entry.written_form
                                and gram not in written_forms
                                and _same_name(_compact(gram), key)
                            ):
                                variants.append(
                                    f"'{gram}' should be '{entry.written_form}'"
                                )
            elif _contains_term(remainder, entry.source_term):
                variants.append(
                    f"'{entry.source_term}' should be '{entry.written_form}'"
                )
            for variant in entry.asr_variants:
                if _compact(variant) == _compact(entry.written_form):
                    continue
                if _contains_term(remainder, variant):
                    variants.append(f"'{variant}' should be '{entry.written_form}'")
        if variants:
            report.warning(
                "glossary_variant",
                f"{segment_id}: {'; '.join(dict.fromkeys(variants))}",
                (segment_id,),
            )
        unregistered = _unregistered_latin(spans, latin_forms)
        if unregistered:
            report.warning(
                "latin_unregistered",
                f"{segment_id}: add {', '.join(unregistered[:5])} to "
                "glossary.json or write it in Chinese",
                (segment_id,),
            )


def _unregistered_latin(
    spans: Sequence[str],
    latin_forms: Sequence[list[str]],
) -> list[str]:
    uncovered: list[str] = []
    for span in spans:
        words = span.split(" ")
        index = 0
        while index < len(words):
            matched = next(
                (
                    form
                    for form in latin_forms
                    if words[index : index + len(form)] == form
                ),
                None,
            )
            if matched is not None:
                index += len(matched)
                continue
            word = words[index]
            if len(word) >= 2 and _ACRONYM.fullmatch(word):
                index += 1
                continue
            uncovered.append(word)
            index += 1
    return list(dict.fromkeys(uncovered))


def _check_content_edit(
    report: ValidationReport,
    root: Path,
    segments: Sequence[Mapping[str, Any]],
    targets: Mapping[str, str],
) -> None:
    path = root / "content-edit.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        report.error("content_edit_missing", "content-edit.json is missing")
        return
    except ValueError:
        report.error("content_edit_malformed", "content-edit.json is not JSON")
        return
    if not isinstance(payload, dict):
        report.error("content_edit_malformed", "content-edit.json must be an object")
        return
    if payload.get("policy_version") != CONTENT_EDIT_POLICY_VERSION:
        report.error(
            "content_edit_malformed",
            f"content-edit.json policy_version must be {CONTENT_EDIT_POLICY_VERSION}",
        )
    if payload.get("status") != "accepted":
        report.error(
            "content_edit_pending",
            "complete content-edit.json and set status to accepted",
        )
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        report.error("content_edit_malformed", "decisions must be a list")
        return
    positions = {
        str(segment.get("id", "")): index for index, segment in enumerate(segments)
    }
    tokens = [
        source_tokens(str(segment.get("source_text", ""))) for segment in segments
    ]
    ranges: dict[str, tuple[int, int, str]] = {}
    decision_ids = [
        str(item.get("decision_id", "")).strip()
        for item in decisions
        if isinstance(item, dict)
    ]
    for number, decision in enumerate(decisions, start=1):
        resolved = _check_decision(
            report,
            number,
            decision,
            decision_ids,
            positions,
            tokens,
        )
        if resolved is not None:
            decision_id, start_index, end_index, category = resolved
            ranges[decision_id] = (start_index, end_index, category)
    _check_overlaps(report, ranges, segments)
    if segments and ranges:
        removed = {
            index
            for start_index, end_index, _ in ranges.values()
            for index in range(start_index, end_index + 1)
        }
        if len(removed) >= len(segments):
            report.error(
                "content_edit_removes_everything",
                "content edits would remove all spoken content",
            )
    _check_bridge_edits(report, payload, ranges, segments, targets)
    _check_sponsors(report, root, payload, ranges)


def _check_decision(
    report: ValidationReport,
    number: int,
    decision: object,
    decision_ids: Sequence[str],
    positions: Mapping[str, int],
    tokens: Sequence[Sequence[str]],
) -> tuple[str, int, int, str] | None:
    label = f"decision {number}"
    if not isinstance(decision, dict):
        report.error("content_edit_malformed", f"{label} must be an object")
        return None
    decision_id = str(decision.get("decision_id", "")).strip()
    if not decision_id:
        report.error("content_edit_malformed", f"{label} needs decision_id")
        return None
    label = f"decision {decision_id}"
    if decision_ids.count(decision_id) > 1:
        report.error("content_edit_malformed", f"{label} is duplicated")
        return None
    valid = True
    category = decision.get("category")
    if category not in CONTENT_EDIT_CATEGORIES:
        report.error(
            "content_edit_malformed",
            f"{label}: category must be paid_ad or opening_highlight",
        )
        valid = False
    confidence = decision.get("confidence")
    if (
        not isinstance(confidence, int | float)
        or isinstance(confidence, bool)
        or not MINIMUM_CONTENT_EDIT_CONFIDENCE <= float(confidence) <= 1.0
    ):
        report.error(
            "content_edit_confidence",
            f"{label}: confidence must be 0.90 to 1; a lower decision is "
            "discarded at render time, so raise it only when certain or "
            "remove the decision",
        )
        valid = False
    for key in ("rationale", "bridge_review"):
        value = decision.get(key)
        if not isinstance(value, str) or not value.strip():
            report.error("content_edit_malformed", f"{label} needs {key}")
            valid = False
    evidence = decision.get("evidence_segment_ids")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(str(item).strip() not in positions for item in evidence)
    ):
        report.error(
            "content_edit_malformed",
            f"{label}: evidence_segment_ids must list known segment ids",
        )
        valid = False
    start_id = str(decision.get("start_segment_id", "")).strip()
    end_id = str(decision.get("end_segment_id", "")).strip()
    if start_id not in positions or end_id not in positions:
        report.error(
            "content_edit_anchor",
            f"{label}: start_segment_id and end_segment_id must be known ids",
        )
        return None
    start_index = positions[start_id]
    end_index = positions[end_id]
    if start_index > end_index:
        report.error("content_edit_anchor", f"{label}: segment range is reversed")
        return None
    flat = [
        (segment_index, token_index, token.lower())
        for segment_index in range(start_index, end_index + 1)
        for token_index, token in enumerate(tokens[segment_index])
    ]
    start_match = _anchor_match(
        report, label, "start_anchor", decision.get("start_anchor"), flat
    )
    end_match = _anchor_match(
        report, label, "end_anchor", decision.get("end_anchor"), flat
    )
    if start_match is None or end_match is None:
        return None
    first = flat[start_match[0]]
    last = flat[end_match[1]]
    anchors_placed = True
    if first[0] != start_index:
        report.error(
            "content_edit_anchor",
            f"{label}: start_anchor must begin in {start_id}",
        )
        anchors_placed = False
    if last[0] != end_index:
        report.error(
            "content_edit_anchor",
            f"{label}: end_anchor must end in {end_id}",
        )
        anchors_placed = False
    if start_match[0] > end_match[1]:
        report.error(
            "content_edit_anchor",
            f"{label}: end_anchor comes before start_anchor",
        )
        anchors_placed = False
    if not anchors_placed:
        return None
    fragments = (
        ("retained_start_target", first[1] > 0, start_id),
        ("retained_end_target", last[1] < len(tokens[end_index]) - 1, end_id),
    )
    for key, required, segment_id in fragments:
        value = decision.get(key, "")
        if not isinstance(value, str):
            report.error("content_edit_malformed", f"{label}: {key} must be text")
            valid = False
            continue
        if required and not _CJK_CHARACTER.search(value):
            report.error(
                "content_edit_fragment",
                f"{label}: the cut leaves part of {segment_id} in the episode; "
                f"write its Mandarin in {key}",
                (segment_id,),
            )
            valid = False
        elif value.strip():
            _check_target_text(
                report,
                segment_id,
                value.strip(),
                label=f"{label} {key}",
            )
    if not valid:
        return None
    return decision_id, start_index, end_index, str(category)


def _anchor_match(
    report: ValidationReport,
    label: str,
    key: str,
    anchor: object,
    flat: Sequence[tuple[int, int, str]],
) -> tuple[int, int] | None:
    if not isinstance(anchor, str) or not source_tokens(anchor):
        report.error("content_edit_anchor", f"{label}: {key} is empty")
        return None
    expected = [token.lower() for token in source_tokens(anchor)]
    actual = [token for _, _, token in flat]
    starts = [
        index
        for index in range(0, len(actual) - len(expected) + 1)
        if actual[index : index + len(expected)] == expected
    ]
    if not starts:
        report.error(
            "content_edit_anchor",
            f"{label}: {key} does not occur in the source text of its "
            "segment range; copy the words literally",
        )
        return None
    if len(starts) > 1:
        report.error(
            "content_edit_anchor",
            f"{label}: {key} occurs {len(starts)} times in its segment "
            "range; extend it until it is unique",
        )
        return None
    return starts[0], starts[0] + len(expected) - 1


def _check_overlaps(
    report: ValidationReport,
    ranges: Mapping[str, tuple[int, int, str]],
    segments: Sequence[Mapping[str, Any]],
) -> None:
    items = sorted(ranges.items(), key=lambda item: item[1][0])
    for (left_id, left), (right_id, right) in zip(items, items[1:], strict=False):
        if right[0] <= left[1]:
            shared = str(segments[right[0]].get("id", ""))
            report.error(
                "content_edit_overlap",
                f"decisions {left_id} and {right_id} share {shared}; both "
                "would be discarded at render time. Merge them or keep one",
                (shared,),
            )
            continue
        if right[0] != left[1] + 1:
            continue
        # Crosstalk segments can overlap in time; adjacent removals across
        # such a boundary overlap at render time and are both discarded.
        left_end = segments[left[1]].get("source_end_seconds")
        right_start = segments[right[0]].get("source_start_seconds")
        if (
            isinstance(left_end, int | float)
            and isinstance(right_start, int | float)
            and right_start < left_end
        ):
            report.error(
                "content_edit_overlap",
                f"decisions {left_id} and {right_id} overlap in time across "
                "crosstalk; merge them into one decision",
            )


def bridge_window(
    decision_range: tuple[int, int],
    ranges: Iterable[tuple[int, int]],
    count: int,
) -> list[int]:
    """Retained segment indices next to one removal, nearest first."""

    removed = {
        index for start, end in ranges for index in range(start, end + 1)
    }
    before: list[int] = []
    index = decision_range[0] - 1
    while index >= 0 and len(before) < BRIDGE_WINDOW_SEGMENTS:
        if index not in removed:
            before.append(index)
        index -= 1
    after: list[int] = []
    index = decision_range[1] + 1
    while index < count and len(after) < BRIDGE_WINDOW_SEGMENTS:
        if index not in removed:
            after.append(index)
        index += 1
    return before + after


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _check_bridge_edits(
    report: ValidationReport,
    payload: Mapping[str, Any],
    ranges: Mapping[str, tuple[int, int, str]],
    segments: Sequence[Mapping[str, Any]],
    targets: Mapping[str, str],
) -> None:
    edits = payload.get("bridge_edits", [])
    if not isinstance(edits, list):
        report.error("bridge_edit", "bridge_edits must be a list")
        return
    spans = [(start, end) for start, end, _ in ranges.values()]
    seen: set[str] = set()
    for number, edit in enumerate(edits, start=1):
        label = f"bridge edit {number}"
        if not isinstance(edit, dict):
            report.error("bridge_edit", f"{label} must be an object")
            continue
        decision_id = str(edit.get("decision_id", "")).strip()
        segment_id = str(edit.get("segment_id", "")).strip()
        target = edit.get("target")
        reason = edit.get("reason")
        if decision_id not in ranges:
            report.error(
                "bridge_edit",
                f"{label}: decision_id must name a valid content edit decision",
            )
            continue
        start, end, _ = ranges[decision_id]
        window = [
            str(segments[index].get("id", ""))
            for index in bridge_window((start, end), spans, len(segments))
        ]
        if segment_id not in window:
            report.error(
                "bridge_edit",
                f"{label}: {decision_id} may only bridge {', '.join(window)}",
                (segment_id,) if segment_id else (),
            )
            continue
        if segment_id in seen:
            report.error("bridge_edit", f"{label}: {segment_id} is duplicated")
            continue
        seen.add(segment_id)
        if not isinstance(reason, str) or not reason.strip():
            report.error("bridge_edit", f"{label} needs a reason")
        current = targets.get(segment_id, "")
        if not isinstance(target, str) or _normalized(target) != _normalized(
            current
        ):
            report.error(
                "bridge_edit",
                f"{label}: target must equal the edited TARGET of {segment_id}",
                (segment_id,),
            )


def _check_sponsors(
    report: ValidationReport,
    root: Path,
    payload: Mapping[str, Any],
    ranges: Mapping[str, tuple[int, int, str]],
) -> None:
    required: list[str] = []
    path = root / "source" / "description-sponsors.json"
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            value = {}
        listed = value.get("sponsors", []) if isinstance(value, dict) else []
        required = [str(item) for item in listed if str(item).strip()]
    entries = payload.get("description_sponsors", [])
    if not isinstance(entries, list):
        report.error("sponsor_check", "description_sponsors must be a list")
        return
    covered: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        label = f"description sponsor {number}"
        if not isinstance(entry, dict):
            report.error("sponsor_check", f"{label} must be an object")
            continue
        sponsor = entry.get("sponsor")
        status = entry.get("status")
        if not isinstance(sponsor, str) or not sponsor.strip():
            report.error("sponsor_check", f"{label} needs sponsor")
            continue
        label = f"sponsor {sponsor.strip()}"
        if status not in SPONSOR_STATUSES:
            report.error(
                "sponsor_check",
                f"{label}: status must be removed or not_read_in_audio",
            )
            continue
        if status == "removed":
            decisions = entry.get("decision_ids")
            if (
                not isinstance(decisions, list)
                or not decisions
                or any(
                    str(item) not in ranges or ranges[str(item)][2] != "paid_ad"
                    for item in decisions
                )
            ):
                report.error(
                    "sponsor_check",
                    f"{label}: decision_ids must name valid paid_ad decisions",
                )
                continue
        else:
            note = entry.get("note")
            if not isinstance(note, str) or not note.strip():
                report.error(
                    "sponsor_check",
                    f"{label}: explain in note why no read occurs in the audio",
                )
                continue
        covered.add(sponsor.strip().casefold())
    for name in required:
        if name.strip().casefold() not in covered:
            report.error(
                "sponsor_check",
                f"sponsor {name} is named in the description; record a "
                "removal decision or a not_read_in_audio confirmation",
            )


def _check_verdict(
    report: ValidationReport,
    root: Path,
    known_ids: Sequence[str],
) -> None:
    verdict = parse_verdict((root / "verdict.md").read_text(encoding="utf-8"))
    approved = verdict.fields.get("APPROVED", "").lower()
    if approved not in ("yes", "no"):
        report.error("verdict", "set APPROVED to yes or no in verdict.md")
    if any(
        verdict.fields.get(key, "") in ("", TARGET_PLACEHOLDER)
        for key in ("TITLE", "OVERVIEW")
    ):
        report.error("verdict", "complete TITLE and OVERVIEW in verdict.md")
    if approved == "yes" and verdict.sections["UNRESOLVED"]:
        report.error(
            "verdict",
            "APPROVED: yes conflicts with non-empty UNRESOLVED. Use '- (none)' "
            "when no target defect remains; retain source concerns in "
            "SOURCE_WARNINGS. Otherwise preserve the issue and set APPROVED "
            "to no.",
        )
    for bullet in verdict.sections["SOURCE_WARNINGS"]:
        _, problem = parse_source_warning(bullet, known_ids)
        if problem is not None:
            report.error(
                "source_warning_format",
                f"SOURCE_WARNINGS bullet {problem}: write "
                "'[kind] seg-0001, seg-0002: detail'",
            )
    justified: set[str] = set()
    for bullet in verdict.sections["VALIDATOR_NOTES"]:
        references, _, detail = bullet.partition(":")
        segment_ids = expand_segment_ids(references, known_ids)
        if not segment_ids or not detail.strip():
            report.error(
                "validator_note_format",
                "VALIDATOR_NOTES bullets must be 'seg-0001: justification'",
            )
            continue
        justified.update(segment_ids)
    unjustified = [
        segment_id
        for segment_id in dict.fromkeys(
            item
            for warning in report.warnings
            for item in warning.segment_ids
        )
        if segment_id not in justified
    ]
    if unjustified:
        report.error(
            "unjustified_warnings",
            "fix or justify the WARNING lines for "
            f"{', '.join(unjustified[:20])}"
            f"{' and more' if len(unjustified) > 20 else ''} under "
            "VALIDATOR_NOTES in verdict.md",
            unjustified,
        )


def main() -> int:
    root = Path(__file__).resolve().parent
    report = validate_project(root)
    for finding in report.errors + report.warnings:
        print(finding.line())
    if report.errors:
        print(
            f"{len(report.errors)} errors and {len(report.warnings)} "
            "warnings; fix every ERROR"
        )
        return 1
    print(
        "Translation project targets are complete"
        + (f" with {len(report.warnings)} warnings" if report.warnings else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
