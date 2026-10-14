"""Pins the V15-V18 render cache identity and assembly behavior.

Speech Pipelines V19/V20 add the Rhythm Policy and the Unit Clip Store. The
historical pipelines must keep their exact cache identity, assembly sequence,
and FFmpeg commands so ordinary retries and Segment Revisions of V15-V18 runs
behave exactly as before.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from transpodcast import modal_pipeline
from transpodcast.pipeline_versions import (
    SPEECH_PIPELINE_V15,
    SPEECH_PIPELINE_V16,
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V18,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_render_plan(pause_after: float = 0.4) -> list[dict[str, Any]]:
    return [
        {
            "segment_id": "seg-0001--render-01",
            "speaker_id": "speaker-01",
            "source_segment_id": "seg-0001",
            "source_start_seconds": 0.0,
            "source_end_seconds": 2.0,
            "target_text": "First half, ",
            "parent_segment_id": "seg-0001",
            "pause_before_seconds": 0.0,
            "pause_after_seconds": pause_after,
            "internal_split": True,
            "approved_boundary_kind": "pause",
        },
        {
            "segment_id": "seg-0001--render-02",
            "speaker_id": "speaker-01",
            "source_segment_id": "seg-0001",
            "source_start_seconds": 2.0 + pause_after,
            "source_end_seconds": 4.0,
            "target_text": "second half.",
            "parent_segment_id": "seg-0001",
            "pause_before_seconds": pause_after,
            "pause_after_seconds": 0.7,
            "internal_split": True,
            "approved_boundary_kind": None,
        },
        {
            "segment_id": "seg-0002",
            "speaker_id": "speaker-02",
            "source_segment_id": "seg-0002",
            "source_start_seconds": 4.7,
            "source_end_seconds": 6.0,
            "target_text": "Reply.",
            "parent_segment_id": "seg-0002",
            "pause_before_seconds": 0.7,
            "pause_after_seconds": 0.0,
            "internal_split": False,
            "approved_boundary_kind": None,
            "emotion_reference_audio": b"emotion-reference",
        },
    ]


_SPEAKER_REFERENCES = {
    "speaker-01": b"speaker-one-reference",
    "speaker-02": b"speaker-two-reference",
}


def _captured_output_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    render_plan: list[dict[str, Any]],
    *,
    pipeline_version: str,
    renderer_class: str,
) -> str:
    content = b"rendered-unit"
    digest = hashlib.sha256(content).hexdigest()
    prefixes: list[str] = []

    def fake_remote(
        _app: str,
        class_name: str,
        method: str,
        *arguments: Any,
        **_keywords: Any,
    ) -> dict[str, Any]:
        if class_name == "TransferStore" and method == "manifest":
            prefix = str(arguments[0])
            if prefix.startswith("staged/"):
                return {"files": {}}
            return {
                "files": {
                    f"{item['segment_id']}.wav": {"sha256": digest}
                    for item in render_plan
                }
            }
        if class_name == "TransferStore" and method == "put":
            return {}
        if class_name == "TransferStore" and method == "get":
            return {name: content for name in arguments[1]}
        prefixes.append(str(arguments[3]))
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
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_wav = tmp_path / "source-24k.wav"
    source_wav.write_bytes(b"source")
    rendered_root = tmp_path / f"rendered-{len(prefixes)}-{pipeline_version}"
    rendered_root.mkdir(parents=True, exist_ok=True)
    modal_pipeline._render_sharded(
        source_wav,
        "fixture-checksum",
        _SPEAKER_REFERENCES,
        render_plan,
        rendered_root,
        emotion_alpha=0.8,
        pipeline_version=pipeline_version,
        renderer_class=renderer_class,
    )
    assert len(set(prefixes)) == 1
    return prefixes[0]


@pytest.mark.parametrize(
    ("pipeline_version", "renderer_class", "expected_prefix"),
    [
        (
            SPEECH_PIPELINE_V15,
            "IndexTTS2BatchRenderer",
            "render/fixture-checksum/6772998c99248d9c",
        ),
        (
            SPEECH_PIPELINE_V16,
            "IndexTTS25BatchRenderer",
            "render/fixture-checksum/c78c3926f04bc466",
        ),
        (
            SPEECH_PIPELINE_V17,
            "IndexTTS2BatchRenderer",
            "render/fixture-checksum/fce9b3ed58ca77bb",
        ),
        (
            SPEECH_PIPELINE_V18,
            "IndexTTS25BatchRenderer",
            "render/fixture-checksum/4f84ee3b3b585fb7",
        ),
    ],
)
def test_legacy_render_cache_identity_is_pinned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pipeline_version: str,
    renderer_class: str,
    expected_prefix: str,
) -> None:
    prefix = _captured_output_prefix(
        monkeypatch,
        tmp_path,
        _fixture_render_plan(),
        pipeline_version=pipeline_version,
        renderer_class=renderer_class,
    )

    assert prefix == expected_prefix


def _normalize(value: object, tmp_path: Path) -> object:
    import re

    if isinstance(value, list | tuple):
        return [_normalize(item, tmp_path) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item, tmp_path) for key, item in value.items()}
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        text = value.replace(str(tmp_path.resolve()), "<tmp>").replace(
            str(tmp_path), "<tmp>"
        )
        return re.sub(r"[^ ']*/(transpodcast-[a-z]+)-[^/ ']+", r"<\1>", text)
    return value


def _legacy_render_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pipeline_version: str,
) -> dict[str, Any]:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"source")
    references = {}
    for label, content in _SPEAKER_REFERENCES.items():
        path = tmp_path / f"{label}.wav"
        path.write_bytes(content)
        references[label] = path
    commands: list[list[str]] = []
    silences: list[tuple[str, float]] = []
    mastered: list[dict[str, Any]] = []

    def fake_transcode(_source: Path, destination: Path, **_kwargs: Any) -> None:
        destination.write_bytes(b"wav")

    def fake_render_sharded(
        _source_wav: Path,
        _checksum: str,
        _references: dict[str, bytes],
        plan: list[dict[str, Any]],
        rendered_root: Path,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        metadata = []
        for index, unit in enumerate(plan, start=1):
            path = rendered_root / f"segment-{index:04d}.wav"
            path.write_bytes(f"unit-{index}".encode())
            metadata.append(
                {
                    "segment_id": unit["segment_id"],
                    "path": str(path),
                    "sha256": "fixture",
                    "duration_seconds": 1.0 + index / 10,
                    "emotion_reference_start_seconds": 0.0,
                    "emotion_reference_end_seconds": 1.0,
                    "speech_text_sha256": f"text-{index}",
                }
            )
        return {
            "segments": metadata,
            "model_id": "fixture-indextts",
            "model_revision": "fixture-revision",
            "seed": 42,
            "emotion_alpha": 0.8,
            "random_sampling": False,
            "total_inference_seconds": 0.0,
        }

    def fake_run(arguments: list[str]) -> Any:
        commands.append(list(arguments))
        return None

    def fake_write_silence(path: Path, duration: float) -> None:
        silences.append((path.name, duration))

    def fake_master(
        source_path: Path,
        publication_path: Path,
        _work_root: Path,
        *,
        rendered_segment_count: int,
    ) -> dict[str, Any]:
        mastered.append(
            {
                "source": source_path.name,
                "publication": publication_path.name,
                "rendered_segment_count": rendered_segment_count,
            }
        )
        return {"duration_seconds": 9.0, "quality": {"rendered_segments": 3}}

    monkeypatch.setattr(modal_pipeline, "_transcode", fake_transcode)
    monkeypatch.setattr(modal_pipeline, "_render_sharded", fake_render_sharded)
    monkeypatch.setattr(modal_pipeline, "_run", fake_run)
    monkeypatch.setattr(modal_pipeline, "_write_silence", fake_write_silence)
    monkeypatch.setattr(modal_pipeline, "_master_stereo_pcm", fake_master)
    monkeypatch.setattr(
        modal_pipeline,
        "_probe",
        lambda path: {
            "format": {"duration": "0.4" if "pause" in path.name else "0.9"}
        },
    )
    units = [
        {
            key: value
            for key, value in item.items()
            if key not in {"speaker_id", "segment_id", "emotion_reference_audio"}
        }
        | {"unit_id": item["segment_id"]}
        for item in _fixture_render_plan()
    ]
    result = modal_pipeline.render(
        {
            "source": {
                "audio_path": str(source),
                "checksum": "fixture-source",
                "analysis": {
                    "pipeline_version": pipeline_version,
                    "segments": [
                        {
                            "segment_id": "seg-0001",
                            "speaker_label": "speaker-01",
                            "start_seconds": 0.0,
                            "end_seconds": 4.0,
                        },
                        {
                            "segment_id": "seg-0002",
                            "speaker_label": "speaker-02",
                            "start_seconds": 4.7,
                            "end_seconds": 6.0,
                        },
                    ],
                    "speakers": [
                        {
                            "local_label": label,
                            "reference_artifact_key": str(path),
                        }
                        for label, path in references.items()
                    ],
                    "source_events": [
                        {
                            "kind": "laughter",
                            "start_seconds": 4.1,
                            "end_seconds": 4.6,
                            "safe_to_preserve": True,
                        },
                        {
                            "kind": "laughter",
                            "start_seconds": 1.0,
                            "end_seconds": 1.5,
                            "safe_to_preserve": False,
                        },
                    ],
                    "render_plan": {
                        "schema_version": "render-plan-v1",
                        "units": units,
                    },
                },
            },
            "translation": {
                "segments": ["First half, second half.", "Reply."]
            },
            "rendering": {"pipeline_version": pipeline_version},
        },
        tmp_path / "output",
    )
    return {
        "commands": _normalize(commands, tmp_path),
        "silences": [list(item) for item in silences],
        "mastered": mastered,
        "segment_timings": result["segment_timings"],
        "render_settings_keys": sorted(result["render_settings"]),
    }


@pytest.mark.parametrize(
    "pipeline_version",
    [
        SPEECH_PIPELINE_V15,
        SPEECH_PIPELINE_V16,
        SPEECH_PIPELINE_V17,
        SPEECH_PIPELINE_V18,
    ],
)
def test_legacy_complete_episode_assembly_is_pinned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pipeline_version: str,
) -> None:
    trace = _legacy_render_trace(monkeypatch, tmp_path, pipeline_version)

    assert trace == json.loads(
        (_FIXTURES / "legacy-assembly-trace-complete-episode.json").read_text()
    )


def _legacy_revision_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pipeline_version: str,
) -> dict[str, Any]:
    source = tmp_path / "source.m4a"
    source.write_bytes(b"source")
    prior_preview = tmp_path / "prior-preview.m4a"
    prior_preview.write_bytes(b"prior")
    references = {}
    for label, content in _SPEAKER_REFERENCES.items():
        path = tmp_path / f"{label}.wav"
        path.write_bytes(content)
        references[label] = path
    commands: list[list[str]] = []
    remote_calls: list[dict[str, Any]] = []
    mastered: list[dict[str, Any]] = []

    def fake_transcode(_source: Path, destination: Path, **_kwargs: Any) -> None:
        destination.write_bytes(b"wav")

    def fake_remote(
        app: str,
        class_name: str,
        method: str,
        *arguments: Any,
        **keywords: Any,
    ) -> dict[str, Any]:
        plan = arguments[2]
        remote_calls.append(
            {
                "app": app,
                "class": class_name,
                "method": method,
                "units": [
                    {
                        key: value
                        for key, value in unit.items()
                        if key != "emotion_reference_audio"
                    }
                    for unit in plan
                ],
                "keywords": keywords,
            }
        )
        return {
            "segments": [
                {
                    "segment_id": unit["segment_id"],
                    "audio": b"revised-audio",
                    "duration_seconds": 1.25,
                    "emotion_reference_start_seconds": 4.2,
                    "emotion_reference_end_seconds": 6.0,
                    "speech_text_sha256": "revised-text",
                }
                for unit in plan
            ],
            "model_id": "fixture-indextts",
            "model_revision": "fixture-revision",
            "seed": 42,
            "emotion_alpha": 0.8,
            "random_sampling": False,
            "total_inference_seconds": 0.0,
        }

    def fake_run(arguments: list[str]) -> Any:
        commands.append(list(arguments))
        return None

    def fake_master(
        source_path: Path,
        publication_path: Path,
        _work_root: Path,
        *,
        rendered_segment_count: int,
    ) -> dict[str, Any]:
        mastered.append(
            {
                "source": source_path.name,
                "publication": publication_path.name,
                "rendered_segment_count": rendered_segment_count,
            }
        )
        return {"duration_seconds": 9.0, "quality": {"rendered_segments": 3}}

    def fake_probe(path: Path) -> dict[str, Any]:
        if path.name == "prior-preview.m4a":
            return {"format": {"duration": "9.0"}}
        return {"format": {"duration": "1.25"}}

    monkeypatch.setattr(modal_pipeline, "_transcode", fake_transcode)
    monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
    monkeypatch.setattr(modal_pipeline, "_run", fake_run)
    monkeypatch.setattr(modal_pipeline, "_master_stereo_pcm", fake_master)
    monkeypatch.setattr(modal_pipeline, "_probe", fake_probe)
    units = [
        {
            key: value
            for key, value in item.items()
            if key not in {"speaker_id", "segment_id", "emotion_reference_audio"}
        }
        | {"unit_id": item["segment_id"]}
        for item in _fixture_render_plan()
    ]
    result = modal_pipeline.render(
        {
            "source": {
                "audio_path": str(source),
                "checksum": "fixture-source",
                "analysis": {
                    "pipeline_version": pipeline_version,
                    "segments": [
                        {
                            "segment_id": "seg-0001",
                            "speaker_label": "speaker-01",
                            "start_seconds": 0.0,
                            "end_seconds": 4.0,
                        },
                        {
                            "segment_id": "seg-0002",
                            "speaker_label": "speaker-02",
                            "start_seconds": 4.7,
                            "end_seconds": 6.0,
                        },
                    ],
                    "speakers": [
                        {
                            "local_label": label,
                            "reference_artifact_key": str(path),
                        }
                        for label, path in references.items()
                    ],
                    "source_events": [],
                    "render_plan": {
                        "schema_version": "render-plan-v1",
                        "units": units,
                    },
                },
            },
            "translation": {
                "segments": ["First half, second half.", "Reply."]
            },
            "rendering": {
                "pipeline_version": pipeline_version,
                "scope": "segment",
                "segment_ordinal": 2,
                "prior_preview_audio_path": str(prior_preview),
                "prior_render": {
                    "segment_timings": [[0.0, 2.7], [3.6, 4.9]],
                    "render_settings_json": json.dumps(
                        {
                            "speech_text_units": [
                                {
                                    "render_unit_id": item["segment_id"],
                                    "sha256": f"prior-{index}",
                                }
                                for index, item in enumerate(
                                    _fixture_render_plan(), start=1
                                )
                            ]
                        }
                    ),
                },
            },
        },
        tmp_path / "output",
    )
    return {
        "remote_calls": remote_calls,
        "commands": _normalize(commands, tmp_path),
        "mastered": mastered,
        "segment_timings": result["segment_timings"],
        "rendered_segments": _normalize(result["rendered_segments"], tmp_path),
        "render_settings_keys": sorted(result["render_settings"]),
    }


@pytest.mark.parametrize(
    ("pipeline_version", "renderer_class"),
    [
        (SPEECH_PIPELINE_V15, "IndexTTS2BatchRenderer"),
        (SPEECH_PIPELINE_V16, "IndexTTS25BatchRenderer"),
        (SPEECH_PIPELINE_V17, "IndexTTS2BatchRenderer"),
        (SPEECH_PIPELINE_V18, "IndexTTS25BatchRenderer"),
    ],
)
def test_legacy_segment_revision_splice_is_pinned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pipeline_version: str,
    renderer_class: str,
) -> None:
    trace = _legacy_revision_trace(monkeypatch, tmp_path, pipeline_version)
    renderer_classes = [call.pop("class") for call in trace["remote_calls"]]

    assert renderer_classes == [renderer_class]
    for call in trace["remote_calls"]:
        call["class"] = "<renderer-class>"
    assert trace == json.loads(
        (_FIXTURES / "legacy-assembly-trace-segment-revision.json").read_text()
    )
