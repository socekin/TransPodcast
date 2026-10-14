from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _renderer_module() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "experiments"
        / "pipeline"
        / "modal_indextts2_batch.py"
    )
    spec = importlib.util.spec_from_file_location(
        "modal_indextts2_batch_test",
        path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_indextts25_renderer_pins_the_validated_model_and_parameters() -> None:
    renderer = _renderer_module()

    assert renderer.INDEXTTS25_MODEL_ID == "IndexTeam/IndexTTS-2.5"
    assert renderer.INDEXTTS25_MODEL_REVISION == (
        # pragma: allowlist nextline secret
        "c39ce5ba981572cb187443877ff559dfb246ce63"
    )
    assert renderer.INDEXTTS25_COMMIT == (
        # pragma: allowlist nextline secret
        "ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c"
    )
    assert "@sha256:" in renderer.INDEXTTS25_BASE_IMAGE
    assert renderer.INDEXTTS25_DEBIAN_SNAPSHOT == "20260825T000000Z"
    assert renderer.INDEXTTS25_RUNTIME_REVISION == (
        "indextts25-runtime-2026-08-25-v1"
    )
    assert renderer.INDEXTTS25_LOAD_SETTINGS == {
        "use_bf16": True,
        "use_cuda_kernel": False,
        "use_deepspeed": False,
        "use_accel": False,
        "use_torch_compile": False,
        "use_qwen_emo": False,
    }
    assert renderer.INDEXTTS25_INFERENCE_SETTINGS == {
        "lang": "zh",
        "use_emo_text": False,
        "use_random": False,
        "interval_silence": 200,
        "verbose": False,
        "max_text_tokens_per_segment": 120,
        "stream_return": False,
        "duration_factor": 1.0,
        "text_normalization": True,
        "top_p": 0.8,
        "top_k": 30,
        "temperature": 0.8,
        "do_sample": True,
        "num_beams": 3,
        "repetition_penalty": 10.0,
        "max_mel_tokens": 1500,
    }
    assert renderer.NLTK_DATA_REVISION == (
        # pragma: allowlist nextline secret
        "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
    )
    assert len(renderer.NLTK_DATA_ARTIFACTS) == 3
    assert all(
        len(expected_sha256) == 64
        for _, _, expected_sha256 in renderer.NLTK_DATA_ARTIFACTS
    )
    assert {
        "codec.pth",
        "config.yaml",
        "gpt.pth",
        "s2mel.pth",
    } == renderer.INDEXTTS25_REQUIRED_MODEL_FILES
    assert hasattr(renderer, "IndexTTS25Preflight")
    assert renderer._renderer_implementation_sha256() == hashlib.sha256(
        Path(renderer.__file__).read_bytes()
    ).hexdigest()


def test_indextts25_snapshot_manifest_detects_file_corruption(
    tmp_path: Path,
) -> None:
    renderer = _renderer_module()
    items = []
    for filename in sorted(renderer.INDEXTTS25_REQUIRED_MODEL_FILES):
        path = tmp_path / filename
        content = filename.encode()
        path.write_bytes(content)
        items.append(
            {
                "path": filename,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    marker = {
        "model_id": renderer.INDEXTTS25_MODEL_ID,
        "revision": renderer.INDEXTTS25_MODEL_REVISION,
        "snapshot_files": items,
        "snapshot_manifest_sha256": renderer._manifest_sha256(items),
    }
    (tmp_path / ".transpodcast-pinned-snapshot.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )

    verified = renderer._validate_indextts25_model_snapshot(tmp_path)

    assert verified["snapshot_manifest_sha256"] == marker[
        "snapshot_manifest_sha256"
    ]
    assert verified["model_file_count"] == 4
    assert verified["snapshot_files"] == items

    (tmp_path / "gpt.pth").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="integrity failure"):
        renderer._validate_indextts25_model_snapshot(tmp_path)


def test_indextts25_native_headroom_guard_only_reduces_hot_waveforms() -> None:
    renderer = _renderer_module()

    assert renderer._indextts25_headroom_gain(0.97) == 1.0
    assert renderer._indextts25_headroom_gain(0.98) == 1.0
    assert renderer._indextts25_headroom_gain(1.0) == 0.98


def test_indextts25_cache_requires_matching_provenance_and_audio(
    tmp_path: Path,
) -> None:
    renderer = _renderer_module()
    output_path = tmp_path / "seg-0001.wav"
    metadata_path = tmp_path / "seg-0001.json"
    content = b"candidate audio"
    input_identity = {
        "modal_image_id": "im-pinned",
        "model_revision": renderer.INDEXTTS25_MODEL_REVISION,
        "speech_text_sha256": "adapted-text",
    }
    output_path.write_bytes(content)
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "input_identity": input_identity,
                "output": {
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )

    assert renderer._indextts25_cache_rejection_reasons(
        output_path,
        metadata_path,
        input_identity,
    ) == []

    stale_identity = {**input_identity, "modal_image_id": "im-stale"}
    assert "input-identity-mismatch" in (
        renderer._indextts25_cache_rejection_reasons(
            output_path,
            metadata_path,
            stale_identity,
        )
    )
    output_path.write_bytes(b"corrupted")
    assert "audio-checksum-mismatch" in (
        renderer._indextts25_cache_rejection_reasons(
            output_path,
            metadata_path,
            input_identity,
        )
    )
