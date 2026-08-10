from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

MAX_EMOTION_REFERENCE_SECONDS = 15.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return value


def load_render_plan(
    transcript_path: Path,
    translation_path: Path,
    speaker_dir: Path,
) -> dict[str, Any]:
    transcript = _load_json(transcript_path)
    translation = _load_json(translation_path)

    if transcript.get("video_id") != translation.get("video_id"):
        raise ValueError("Transcript and translation video IDs do not match")

    excerpt_duration = (
        float(transcript["source_excerpt_end_seconds"])
        - float(transcript["source_excerpt_start_seconds"])
    )
    source_segments = transcript.get("segments")
    target_segments = translation.get("segments")
    if not isinstance(source_segments, list):
        raise TypeError("Transcript segments must be a list")
    if not isinstance(target_segments, dict):
        raise TypeError("Translation segments must be an object")

    joined_segments: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    speech_ids: set[str] = set()
    speaker_ids: set[str] = set()

    for source_segment in source_segments:
        segment = dict(source_segment)
        segment_id = str(segment["segment_id"])
        if segment_id in seen_ids:
            raise ValueError(f"Duplicate segment ID: {segment_id}")
        seen_ids.add(segment_id)

        start_seconds = float(segment["source_start_seconds"])
        end_seconds = float(segment["source_end_seconds"])
        if (
            start_seconds < 0
            or end_seconds <= start_seconds
            or end_seconds > excerpt_duration + 0.001
        ):
            raise ValueError(
                f"Segment {segment_id} is outside the source excerpt"
            )

        kind = segment.get("kind")
        if kind == "speech":
            duration = end_seconds - start_seconds
            if duration > MAX_EMOTION_REFERENCE_SECONDS:
                raise ValueError(
                    f"Segment {segment_id} exceeds the "
                    f"{MAX_EMOTION_REFERENCE_SECONDS:.0f}-second "
                    "emotion-reference limit"
                )
            speaker_id = str(segment["speaker_id"])
            speaker_ids.add(speaker_id)
            speech_ids.add(segment_id)
            if segment_id in target_segments:
                segment["target_text"] = str(
                    target_segments[segment_id]
                ).strip()
        elif kind == "source_audio_event":
            if not segment.get("event_type"):
                raise ValueError(
                    f"Source event {segment_id} has no event type"
                )
        else:
            raise ValueError(
                f"Segment {segment_id} has unsupported kind: {kind}"
            )

        joined_segments.append(segment)

    missing_translations = sorted(speech_ids - set(target_segments))
    if missing_translations:
        raise ValueError(
            "Missing translations: " + ", ".join(missing_translations)
        )
    unused_translations = sorted(set(target_segments) - speech_ids)
    if unused_translations:
        raise ValueError(
            "Translations without speech segments: "
            + ", ".join(unused_translations)
        )

    speaker_references: dict[str, dict[str, str]] = {}
    for speaker_id in sorted(speaker_ids):
        reference_path = speaker_dir / speaker_id / "reference-neutral.wav"
        if not reference_path.is_file():
            raise ValueError(
                f"Missing speaker reference for {speaker_id}: "
                f"{reference_path}"
            )
        speaker_references[speaker_id] = {
            "path": str(reference_path),
            "sha256": sha256(reference_path),
        }

    return {
        "schema_version": 1,
        "video_id": transcript["video_id"],
        "source_excerpt_start_seconds": transcript[
            "source_excerpt_start_seconds"
        ],
        "source_excerpt_end_seconds": transcript[
            "source_excerpt_end_seconds"
        ],
        "source_language": transcript["language"],
        "target_locale": translation["locale"],
        "translation_method": translation["translation_method"],
        "translation_version": translation["translation_version"],
        "speaker_references": speaker_references,
        "segments": joined_segments,
    }
