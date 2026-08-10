from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.pipeline.baseline_manifest import load_render_plan


def _write_manifest_fixture(root: Path) -> tuple[Path, Path, Path]:
    transcript_path = root / "canonical.en.json"
    translation_path = root / "baseline.zh-CN.json"
    speaker_dir = root / "speakers"
    reference = speaker_dir / "speaker-a" / "reference-neutral.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"synthetic-waveform-placeholder")
    transcript_path.write_text(
        json.dumps(
            {
                "video_id": "synthetic-example",
                "source_excerpt_start_seconds": 10.0,
                "source_excerpt_end_seconds": 18.0,
                "language": "en",
                "segments": [
                    {
                        "segment_id": "turn-001",
                        "kind": "speech",
                        "speaker_id": "speaker-a",
                        "source_start_seconds": 0.0,
                        "source_end_seconds": 3.0,
                        "source_text": "Welcome to the fictional show.",
                    },
                    {
                        "segment_id": "event-001",
                        "kind": "source_audio_event",
                        "event_type": "music",
                        "source_start_seconds": 3.0,
                        "source_end_seconds": 4.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    translation_path.write_text(
        json.dumps(
            {
                "video_id": "synthetic-example",
                "locale": "zh-CN",
                "translation_method": "synthetic-fixture",
                "translation_version": "1",
                "segments": {
                    "turn-001": (
                        "\u6b22\u8fce\u6536\u542c\u8fd9\u4e2a"
                        "\u865a\u6784\u8282\u76ee\u3002"
                    )
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return transcript_path, translation_path, speaker_dir


def test_loads_synthetic_baseline(tmp_path: Path) -> None:
    transcript, translation, speakers = _write_manifest_fixture(tmp_path)

    plan = load_render_plan(transcript, translation, speakers)

    assert len(plan["segments"]) == 2
    assert set(plan["speaker_references"]) == {"speaker-a"}
    assert plan["segments"][0]["target_text"] == (
        "\u6b22\u8fce\u6536\u542c\u8fd9\u4e2a"
        "\u865a\u6784\u8282\u76ee\u3002"
    )


def test_rejects_missing_translation(tmp_path: Path) -> None:
    transcript, translation, speakers = _write_manifest_fixture(tmp_path)
    payload = json.loads(translation.read_text(encoding="utf-8"))
    payload["segments"].pop("turn-001")
    translation.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Missing translations: turn-001"):
        load_render_plan(transcript, translation, speakers)


def test_rejects_segment_outside_excerpt(tmp_path: Path) -> None:
    transcript, translation, speakers = _write_manifest_fixture(tmp_path)
    payload = json.loads(transcript.read_text(encoding="utf-8"))
    payload["segments"][0]["source_start_seconds"] = -0.1
    transcript.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="outside the source excerpt"):
        load_render_plan(transcript, translation, speakers)
