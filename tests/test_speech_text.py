"""The voice reads 零 where a target writes 〇, and the clip cache follows."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from transpodcast import modal_pipeline
from transpodcast.modal_pipeline import rhythm_unit_tts_identities
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V23
from transpodcast.speech_text import speech_text, speech_units

_REFERENCES = {"speaker-01": b"speaker-one-reference"}


def _unit(segment_id: str, text: str) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "speaker_id": "speaker-01",
        "source_segment_id": segment_id,
        "source_start_seconds": 0.0,
        "source_end_seconds": 2.0,
        "target_text": text,
        "parent_segment_id": segment_id,
        "pause_before_seconds": 0.0,
        "pause_after_seconds": 0.4,
        "internal_split": False,
        "approved_boundary_kind": None,
    }


def test_speech_text_reads_circle_zero_as_ling() -> None:
    assert speech_text("你看二〇二五年夏天") == "你看二零二五年夏天"
    assert speech_text("二〇〇六年创办") == "二零零六年创办"
    assert speech_text("过去二十五年") == "过去二十五年"


def test_speech_units_copy_the_plan_without_changing_it() -> None:
    plan = [_unit("seg-0001", "二〇二八年")]

    assert speech_units(plan)[0]["target_text"] == "二零二八年"
    assert plan[0]["target_text"] == "二〇二八年"


def test_clip_identity_hashes_the_speech_text() -> None:
    identity = {
        "source_checksum": "fixture-source",
        "pipeline_version": SPEECH_PIPELINE_V23,
        "renderer_class": "IndexTTS2BatchRenderer",
        "emotion_alpha": 0.8,
    }
    written = rhythm_unit_tts_identities(
        [_unit("seg-0001", "二〇二五年"), _unit("seg-0002", "第二句。")],
        _REFERENCES,
        **identity,
    )
    spoken = rhythm_unit_tts_identities(
        [_unit("seg-0001", "二零二五年"), _unit("seg-0002", "第二句。")],
        _REFERENCES,
        **identity,
    )

    assert written == spoken


def test_sharded_render_sends_the_speech_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = [_unit("seg-0001", "二〇二五年夏天"), _unit("seg-0002", "第二句。")]
    content = b"rendered-unit"
    digest = hashlib.sha256(content).hexdigest()
    sent: list[str] = []

    def fake_remote(
        _app: str,
        class_name: str,
        method: str,
        *arguments: Any,
        **_keywords: Any,
    ) -> dict[str, Any]:
        if class_name == "TransferStore" and method == "manifest":
            if str(arguments[0]).startswith("staged/"):
                return {"files": {}}
            return {
                "files": {
                    f"{item['segment_id']}.wav": {"sha256": digest}
                    for item in plan
                }
            }
        if class_name == "TransferStore" and method == "put":
            return {}
        if class_name == "TransferStore" and method == "get":
            return {name: content for name in arguments[1]}
        sent.extend(str(item["target_text"]) for item in arguments[2])
        return {
            "segments": [
                {"segment_id": item["segment_id"], "duration_seconds": 1.0}
                for item in arguments[2]
            ],
            "model_id": "fixture",
            "model_revision": "fixture",
            "seed": 42,
            "emotion_alpha": 0.8,
        }

    monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
    monkeypatch.setattr(
        modal_pipeline,
        "_probe",
        lambda _path: {"format": {"duration": "1.0"}},
    )
    source_wav = tmp_path / "source-24k.wav"
    source_wav.write_bytes(b"source")
    rendered_root = tmp_path / "rendered"
    rendered_root.mkdir()

    modal_pipeline._render_sharded(
        source_wav,
        "fixture-checksum",
        _REFERENCES,
        plan,
        rendered_root,
        emotion_alpha=0.8,
        pipeline_version=SPEECH_PIPELINE_V23,
        renderer_class="IndexTTS2BatchRenderer",
    )

    assert sorted(sent) == ["二零二五年夏天", "第二句。"]
    assert plan[0]["target_text"] == "二〇二五年夏天"
