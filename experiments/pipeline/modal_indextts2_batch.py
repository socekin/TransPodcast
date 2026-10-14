from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import modal

from transpodcast.indextts25_runtime import (
    INDEXTTS25_BASE_IMAGE,
    INDEXTTS25_COMMIT,
    INDEXTTS25_DEBIAN_SNAPSHOT,
    INDEXTTS25_INFERENCE_SETTINGS,
    INDEXTTS25_LOAD_SETTINGS,
    INDEXTTS25_MODEL_ID,
    INDEXTTS25_MODEL_REVISION,
    INDEXTTS25_NATIVE_HEADROOM_TARGET,
    INDEXTTS25_RUNTIME_REVISION,
    NLTK_DATA_ARTIFACTS,
    NLTK_DATA_REVISION,
)
from transpodcast.indextts25_text import (
    INDEXTTS25_PRONUNCIATION_POLICY_VERSION,
    adapt_indextts25_speech_text,
)

APP_NAME = "transpodcast-pipeline-indextts2"
MODEL_ID = "IndexTeam/IndexTTS-2"
MODEL_REVISION = "740dcaff396282ffb241903d150ac011cd4b1ede"  # pragma: allowlist secret
MODEL_DIR = "/models/index-tts-2"
INDEXTTS_COMMIT = "13495845e3028f0bb6ca1462ad22aa0e76349e40"  # pragma: allowlist secret
INDEXTTS_DIR = "/opt/index-tts"
INDEXTTS25_MODEL_DIR = f"/models/index-tts-2.5/{INDEXTTS25_MODEL_REVISION}"
INDEXTTS25_DIR = "/opt/index-tts-2.5"
INDEXTTS25_REQUIRED_MODEL_FILES = {
    "codec.pth",
    "config.yaml",
    "gpt.pth",
    "s2mel.pth",
}
DEFAULT_ROOT = Path(
    "artifacts/pipeline-baselines/sample-baseline"
)
DEFAULT_SOURCE_PATH = DEFAULT_ROOT / "source/excerpt-24k.wav"
DEFAULT_TRANSCRIPT_PATH = DEFAULT_ROOT / "transcript/canonical.en.json"
DEFAULT_TRANSLATION_PATH = DEFAULT_ROOT / "i18n/baseline.zh-CN.json"
DEFAULT_SPEAKER_DIR = DEFAULT_ROOT / "speakers"
DEFAULT_OUTPUT_DIR = DEFAULT_ROOT / "rendered/baseline-human-v1"

model_volume = modal.Volume.from_name(
    "transpodcast-indextts2-models",
    create_if_missing=True,
)
model25_volume = modal.Volume.from_name(
    "transpodcast-indextts25-models",
    create_if_missing=True,
)
transfer_volume = modal.Volume.from_name(
    "transpodcast-pipeline-transfer",
    create_if_missing=True,
)
TRANSFER_DIR = "/transfer"


def _pinned_nltk_data_commands() -> tuple[str, ...]:
    commands: list[str] = []
    for category, filename, expected_sha256 in NLTK_DATA_ARTIFACTS:
        url = (
            "https://raw.githubusercontent.com/nltk/nltk_data/"
            f"{NLTK_DATA_REVISION}/packages/{category}/{filename}"
        )
        archive_path = f"/tmp/{filename}"
        destination = f"/opt/nltk_data/{category}"
        commands.extend(
            (
                f"mkdir -p {destination}",
                (
                    "python -c \"from hashlib import sha256; "
                    "from pathlib import Path; "
                    "from urllib.request import urlretrieve; "
                    f"path=Path('{archive_path}'); "
                    f"urlretrieve('{url}', path); "
                    "actual=sha256(path.read_bytes()).hexdigest(); "
                    f"assert actual == '{expected_sha256}', actual\""
                ),
                (
                    "python -m zipfile -e "
                    f"{archive_path} {destination}"
                ),
            )
        )
    return tuple(commands)

image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install(
        "ffmpeg",
        "git",
        "git-lfs",
        "libsndfile1",
    )
    .pip_install("uv==0.9.17")
    .run_commands(
        f"git clone https://github.com/index-tts/index-tts.git {INDEXTTS_DIR}",
        f"cd {INDEXTTS_DIR} && git checkout {INDEXTTS_COMMIT}",
        f"cd {INDEXTTS_DIR} && uv sync --frozen --no-dev",
    )
    .env(
        {
            "HF_HOME": "/models/huggingface",
            "HF_HUB_CACHE": "/models/huggingface/hub",
            "PATH": f"{INDEXTTS_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": (
                f"{INDEXTTS_DIR}:"
                f"{INDEXTTS_DIR}/.venv/lib/python3.11/site-packages"
            ),
        }
    )
    .add_local_python_source("transpodcast")
)

image25 = (
    modal.Image.from_registry(INDEXTTS25_BASE_IMAGE)
    .run_commands(
        "sed -i "
        "'s|http://deb.debian.org/debian-security|"
        "https://snapshot.debian.org/archive/debian-security/"
        f"{INDEXTTS25_DEBIAN_SNAPSHOT}|g; "
        "s|http://deb.debian.org/debian|"
        "https://snapshot.debian.org/archive/debian/"
        f"{INDEXTTS25_DEBIAN_SNAPSHOT}|g; "
        "/^Signed-By:/a Check-Valid-Until: no' "
        "/etc/apt/sources.list.d/debian.sources"
    )
    .apt_install(
        "ffmpeg",
        "git",
        "git-lfs",
        "libsndfile1",
    )
    .pip_install("uv==0.9.17")
    .run_commands(
        (
            "git clone https://github.com/index-tts/index-tts.git "
            f"{INDEXTTS25_DIR}"
        ),
        f"cd {INDEXTTS25_DIR} && git checkout {INDEXTTS25_COMMIT}",
        f"cd {INDEXTTS25_DIR} && uv sync --frozen --no-dev",
        *_pinned_nltk_data_commands(),
    )
    .env(
        {
            "HF_HOME": "/models/huggingface",
            "HF_HUB_CACHE": "/models/huggingface/hub",
            "NLTK_DATA": "/opt/nltk_data",
            "PATH": (
                f"{INDEXTTS25_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin"
            ),
            "PYTHONPATH": (
                f"{INDEXTTS25_DIR}:"
                f"{INDEXTTS25_DIR}/.venv/lib/python3.11/site-packages"
            ),
        }
    )
    .add_local_python_source("transpodcast")
)

app = modal.App(APP_NAME, image=image)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _renderer_implementation_sha256() -> str:
    """Identify the deployed wrapper code separately from its image layer."""

    return _sha256_path(Path(__file__))


def _model_file_manifest(model_path: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(model_path)),
            "bytes": path.stat().st_size,
            "sha256": _sha256_path(path),
        }
        for path in sorted(model_path.rglob("*"))
        if path.is_file()
        and ".cache" not in path.parts
        and path.name != ".transpodcast-pinned-snapshot.json"
    ]


def _manifest_sha256(items: list[dict[str, Any]]) -> str:
    content = json.dumps(
        items,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _indextts25_cache_rejection_reasons(
    output_path: Path,
    metadata_path: Path,
    expected_input_identity: dict[str, Any],
) -> list[str]:
    """Explain why a persisted 2.5 render is unsafe to reuse."""

    reasons: list[str] = []
    if not output_path.is_file():
        reasons.append("audio-missing")
    if not metadata_path.is_file():
        reasons.append("metadata-missing")
        return reasons
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reasons.append("metadata-invalid")
        return reasons
    if metadata.get("schema_version") != 1:
        reasons.append("metadata-schema-mismatch")
    if metadata.get("input_identity") != expected_input_identity:
        reasons.append("input-identity-mismatch")
    output = metadata.get("output")
    if not isinstance(output, dict):
        reasons.append("output-audit-missing")
        return reasons
    if output_path.is_file():
        content = output_path.read_bytes()
        if int(output.get("bytes", -1)) != len(content):
            reasons.append("audio-size-mismatch")
        if str(output.get("sha256", "")) != hashlib.sha256(content).hexdigest():
            reasons.append("audio-checksum-mismatch")
    return reasons


def _validate_indextts25_model_snapshot(
    model_path: Path,
    *,
    verify_content: bool = True,
) -> dict[str, Any]:
    marker_path = model_path / ".transpodcast-pinned-snapshot.json"
    if not marker_path.is_file():
        raise FileNotFoundError("Pinned IndexTTS 2.5 snapshot marker is missing")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if (
        marker.get("model_id") != INDEXTTS25_MODEL_ID
        or marker.get("revision") != INDEXTTS25_MODEL_REVISION
    ):
        raise ValueError("Pinned IndexTTS 2.5 snapshot identity mismatch")
    items = marker.get("snapshot_files")
    if not isinstance(items, list) or not items:
        raise ValueError("Pinned IndexTTS 2.5 snapshot manifest is incomplete")
    available = {str(item.get("path", "")) for item in items}
    missing = sorted(INDEXTTS25_REQUIRED_MODEL_FILES - available)
    if missing:
        raise FileNotFoundError(
            "Pinned IndexTTS 2.5 snapshot is incomplete: " + ", ".join(missing)
        )
    resolved_root = model_path.resolve()
    for item in items:
        path = (model_path / str(item["path"])).resolve()
        if resolved_root not in path.parents:
            raise ValueError("Pinned model manifest contains an unsafe path")
        if (
            not path.is_file()
            or path.stat().st_size != int(item["bytes"])
            or (
                verify_content
                and _sha256_path(path) != str(item["sha256"])
            )
        ):
            raise ValueError(f"Pinned model file integrity failure: {path}")
    actual_manifest_sha256 = _manifest_sha256(items)
    if actual_manifest_sha256 != str(marker.get("snapshot_manifest_sha256", "")):
        raise ValueError("Pinned model manifest checksum mismatch")
    return {
        "model_id": INDEXTTS25_MODEL_ID,
        "model_revision": INDEXTTS25_MODEL_REVISION,
        "model_file_count": len(items),
        "snapshot_manifest_sha256": actual_manifest_sha256,
        "snapshot_files": items,
    }


def _ensure_indextts25_model_snapshot(
    model_path: Path,
    snapshot_download: Any,
    *,
    verify_content: bool = True,
) -> dict[str, Any]:
    marker_path = model_path / ".transpodcast-pinned-snapshot.json"
    if marker_path.is_file():
        return _validate_indextts25_model_snapshot(
            model_path,
            verify_content=verify_content,
        )
    snapshot_download(
        repo_id=INDEXTTS25_MODEL_ID,
        revision=INDEXTTS25_MODEL_REVISION,
        local_dir=model_path,
    )
    items = _model_file_manifest(model_path)
    available = {str(item["path"]) for item in items}
    missing = sorted(INDEXTTS25_REQUIRED_MODEL_FILES - available)
    if missing:
        raise FileNotFoundError(
            "Pinned IndexTTS 2.5 snapshot is incomplete: " + ", ".join(missing)
        )
    marker_path.write_text(
        json.dumps(
            {
                "model_id": INDEXTTS25_MODEL_ID,
                "revision": INDEXTTS25_MODEL_REVISION,
                "snapshot_files": items,
                "snapshot_manifest_sha256": _manifest_sha256(items),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return _validate_indextts25_model_snapshot(
        model_path,
        verify_content=verify_content,
    )


def _emotion_window(
    start_seconds: float,
    end_seconds: float,
    source_duration_seconds: float,
    minimum_seconds: float = 1.0,
) -> tuple[float, float]:
    current_duration = end_seconds - start_seconds
    if current_duration >= minimum_seconds:
        return start_seconds, end_seconds

    missing_seconds = minimum_seconds - current_duration
    resolved_start = max(0.0, start_seconds - (missing_seconds / 2))
    resolved_end = min(
        source_duration_seconds,
        end_seconds + (missing_seconds / 2),
    )
    if resolved_end - resolved_start < minimum_seconds:
        resolved_start = max(0.0, resolved_end - minimum_seconds)
    return resolved_start, resolved_end


@app.cls(
    timeout=1800,
    scaledown_window=60,
    volumes={TRANSFER_DIR: transfer_volume},
)
class TransferStore:
    """CPU-side access to the shared transfer Volume.

    Staged waveforms and shard outputs are rebuildable intermediates;
    the TransPodcast host uses this class so no GPU container is billed for
    pure data movement.
    """

    @modal.method()
    def put(self, path: str, content: bytes) -> dict:
        import hashlib as _hashlib

        destination = Path(TRANSFER_DIR) / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        transfer_volume.commit()
        return {
            "path": path,
            "bytes": len(content),
            "sha256": _hashlib.sha256(content).hexdigest(),
        }

    @modal.method()
    def manifest(self, prefix: str) -> dict:
        import hashlib as _hashlib

        transfer_volume.reload()
        root = Path(TRANSFER_DIR) / prefix
        entries = {}
        if root.is_dir():
            for item in sorted(root.rglob("*")):
                if item.is_file():
                    content = item.read_bytes()
                    entries[str(item.relative_to(root))] = {
                        "bytes": len(content),
                        "sha256": _hashlib.sha256(content).hexdigest(),
                    }
        return {"prefix": prefix, "files": entries}

    @modal.method()
    def get(self, prefix: str, names: list[str]) -> dict:
        transfer_volume.reload()
        root = Path(TRANSFER_DIR) / prefix
        return {
            name: (root / name).read_bytes()
            for name in names
        }

    @modal.method()
    def delete(self, prefix: str) -> dict:
        import shutil

        root = Path(TRANSFER_DIR) / prefix
        removed = root.is_dir()
        if removed:
            shutil.rmtree(root)
            transfer_volume.commit()
        return {"prefix": prefix, "removed": removed}


@app.cls(
    image=image25,
    timeout=3600,
    scaledown_window=60,
    volumes={"/models": model25_volume},
)
class IndexTTS25Preflight:
    """Verify and freeze the 2.5 model snapshot without allocating a GPU."""

    @modal.method()
    def verify(self) -> dict[str, Any]:
        import os

        from huggingface_hub import snapshot_download

        snapshot = _ensure_indextts25_model_snapshot(
            Path(INDEXTTS25_MODEL_DIR),
            snapshot_download,
        )
        model25_volume.commit()
        return {
            **snapshot,
            "code_commit": INDEXTTS25_COMMIT,
            "runtime_revision": INDEXTTS25_RUNTIME_REVISION,
            "runtime_base_image": INDEXTTS25_BASE_IMAGE,
            "native_headroom_target": INDEXTTS25_NATIVE_HEADROOM_TARGET,
            "pronunciation_policy_version": (
                INDEXTTS25_PRONUNCIATION_POLICY_VERSION
            ),
            "load_settings": INDEXTTS25_LOAD_SETTINGS,
            "inference_settings": INDEXTTS25_INFERENCE_SETTINGS,
            "nltk_data_revision": NLTK_DATA_REVISION,
            "nltk_data_artifacts": [
                {
                    "category": category,
                    "filename": filename,
                    "sha256": expected_sha256,
                }
                for category, filename, expected_sha256 in NLTK_DATA_ARTIFACTS
            ],
            "modal_image_id": os.environ.get("MODAL_IMAGE_ID"),
            "renderer_implementation_sha256": (
                _renderer_implementation_sha256()
            ),
        }


@app.cls(
    gpu="L40S",
    timeout=3600,
    scaledown_window=60,
    volumes={"/models": model_volume, TRANSFER_DIR: transfer_volume},
)
class IndexTTS2BatchRenderer:
    @modal.enter()
    def load_model(self) -> None:
        import torch
        from huggingface_hub import snapshot_download
        from indextts.infer_v2 import IndexTTS2

        model_path = Path(MODEL_DIR)
        if not (model_path / "gpt.pth").exists():
            snapshot_download(
                repo_id=MODEL_ID,
                revision=MODEL_REVISION,
                local_dir=model_path,
            )
            model_volume.commit()

        torch.cuda.reset_peak_memory_stats()
        load_started = time.perf_counter()
        self.tts = IndexTTS2(
            cfg_path=str(model_path / "config.yaml"),
            model_dir=str(model_path),
            use_fp16=True,
            use_cuda_kernel=False,
            use_deepspeed=False,
            use_accel=False,
            use_torch_compile=False,
        )
        self.model_load_seconds = time.perf_counter() - load_started

    @modal.method()
    def render_shard(
        self,
        staged_source_path: str,
        speaker_reference_wavs: dict[str, bytes],
        speech_segments: list[dict],
        output_prefix: str,
        emotion_alpha: float = 0.8,
        seed: int = 42,
    ) -> dict:
        """Render one shard of the plan against the staged waveform.

        Outputs are written per segment to the transfer Volume, so a
        completed segment survives any later failure; re-running the
        shard skips segments that already exist (idempotent resume).
        One writer per file: shard plans never overlap.
        """
        import hashlib as _hashlib

        import soundfile
        import torch

        transfer_volume.reload()
        source_path = Path(TRANSFER_DIR) / staged_source_path
        output_root = Path(TRANSFER_DIR) / output_prefix
        output_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path("/tmp/transpodcast-indextts2-shard")
        work_dir.mkdir(parents=True, exist_ok=True)
        speaker_paths: dict[str, Path] = {}
        for speaker_id, reference_bytes in speaker_reference_wavs.items():
            speaker_path = work_dir / f"speaker-{speaker_id}.wav"
            speaker_path.write_bytes(reference_bytes)
            speaker_paths[speaker_id] = speaker_path
        source_audio, source_sample_rate = soundfile.read(
            source_path,
            always_2d=True,
        )
        source_duration_seconds = len(source_audio) / source_sample_rate
        results: list[dict] = []
        skipped = 0
        total_started_at = time.perf_counter()
        for segment in speech_segments:
            segment_id = segment["segment_id"]
            output_path = output_root / f"{segment_id}.wav"
            if output_path.exists():
                skipped += 1
                continue
            emotion_path = work_dir / f"{segment_id}-emotion.wav"
            emotion_audio = segment.get("emotion_reference_audio")
            if isinstance(emotion_audio, bytes):
                emotion_path.write_bytes(emotion_audio)
                start_seconds = -1.0
                end_seconds = -1.0
            else:
                start_seconds, end_seconds = _emotion_window(
                    float(segment["source_start_seconds"]),
                    float(segment["source_end_seconds"]),
                    source_duration_seconds,
                )
                start_frame = round(start_seconds * source_sample_rate)
                end_frame = round(end_seconds * source_sample_rate)
                soundfile.write(
                    emotion_path,
                    source_audio[start_frame:end_frame],
                    source_sample_rate,
                )
            scratch_path = work_dir / f"{segment_id}.wav"
            torch.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
            started_at = time.perf_counter()
            resolved_path = self.tts.infer(
                spk_audio_prompt=str(
                    speaker_paths[segment["speaker_id"]]
                ),
                text=segment["target_text"],
                output_path=str(scratch_path),
                emo_audio_prompt=str(emotion_path),
                emo_alpha=emotion_alpha,
                use_random=False,
                verbose=False,
            )
            if resolved_path is None or not scratch_path.exists():
                raise RuntimeError(
                    f"IndexTTS2 produced no output for {segment_id}"
                )
            content = scratch_path.read_bytes()
            output_path.write_bytes(content)
            output_audio, output_sample_rate = soundfile.read(scratch_path)
            results.append(
                {
                    "segment_id": segment_id,
                    "file": output_path.name,
                    "sha256": _hashlib.sha256(content).hexdigest(),
                    "sample_rate_hz": output_sample_rate,
                    "duration_seconds": (
                        len(output_audio) / output_sample_rate
                    ),
                    "inference_seconds": time.perf_counter() - started_at,
                    "emotion_reference_start_seconds": start_seconds,
                    "emotion_reference_end_seconds": end_seconds,
                }
            )
            if len(results) % 25 == 0:
                transfer_volume.commit()
        transfer_volume.commit()
        return {
            "segments": results,
            "skipped_existing": skipped,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "code_commit": INDEXTTS_COMMIT,
            "seed": seed,
            "emotion_alpha": emotion_alpha,
            "model_load_seconds": self.model_load_seconds,
            "total_inference_seconds": time.perf_counter()
            - total_started_at,
            "gpu_name": torch.cuda.get_device_name(),
        }

    @modal.method()
    def render(
        self,
        source_audio_wav: bytes,
        speaker_reference_wavs: dict[str, bytes],
        speech_segments: list[dict],
        emotion_alpha: float = 0.8,
        seed: int = 42,
    ) -> dict:
        import soundfile
        import torch

        work_dir = Path("/tmp/transpodcast-indextts2-batch")
        work_dir.mkdir(parents=True, exist_ok=True)
        source_path = work_dir / "source.wav"
        source_path.write_bytes(source_audio_wav)

        speaker_paths: dict[str, Path] = {}
        for speaker_id, reference_bytes in speaker_reference_wavs.items():
            speaker_path = work_dir / f"speaker-{speaker_id}.wav"
            speaker_path.write_bytes(reference_bytes)
            speaker_paths[speaker_id] = speaker_path

        source_audio, source_sample_rate = soundfile.read(
            source_path,
            always_2d=True,
        )
        source_duration_seconds = len(source_audio) / source_sample_rate
        results: list[dict] = []
        torch.cuda.reset_peak_memory_stats()
        total_started_at = time.perf_counter()

        for index, segment in enumerate(speech_segments, start=1):
            segment_id = segment["segment_id"]
            emotion_path = work_dir / f"{segment_id}-emotion.wav"
            emotion_audio = segment.get("emotion_reference_audio")
            if isinstance(emotion_audio, bytes):
                emotion_path.write_bytes(emotion_audio)
                start_seconds = -1.0
                end_seconds = -1.0
            else:
                start_seconds, end_seconds = _emotion_window(
                    float(segment["source_start_seconds"]),
                    float(segment["source_end_seconds"]),
                    source_duration_seconds,
                )
                start_frame = round(start_seconds * source_sample_rate)
                end_frame = round(end_seconds * source_sample_rate)
                soundfile.write(
                    emotion_path,
                    source_audio[start_frame:end_frame],
                    source_sample_rate,
                )

            output_path = work_dir / f"{segment_id}.wav"
            torch.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
            started_at = time.perf_counter()
            resolved_path = self.tts.infer(
                spk_audio_prompt=str(
                    speaker_paths[segment["speaker_id"]]
                ),
                text=segment["target_text"],
                output_path=str(output_path),
                emo_audio_prompt=str(emotion_path),
                emo_alpha=emotion_alpha,
                use_random=False,
                verbose=False,
            )
            if resolved_path is None or not output_path.exists():
                raise RuntimeError(
                    f"IndexTTS2 produced no output for {segment_id}"
                )

            output_audio, output_sample_rate = soundfile.read(output_path)
            results.append(
                {
                    "segment_id": segment_id,
                    "audio": output_path.read_bytes(),
                    "sample_rate_hz": output_sample_rate,
                    "duration_seconds": (
                        len(output_audio) / output_sample_rate
                    ),
                    "inference_seconds": time.perf_counter() - started_at,
                    "emotion_reference_start_seconds": start_seconds,
                    "emotion_reference_end_seconds": end_seconds,
                }
            )
            print(
                f"Rendered {index}/{len(speech_segments)}: {segment_id}",
                flush=True,
            )

        return {
            "segments": results,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "code_commit": INDEXTTS_COMMIT,
            "seed": seed,
            "emotion_alpha": emotion_alpha,
            "random_sampling": False,
            "total_inference_seconds": time.perf_counter()
            - total_started_at,
            "peak_gpu_memory_gib": (
                torch.cuda.max_memory_allocated() / (1024**3)
            ),
            "gpu_name": torch.cuda.get_device_name(),
        }


def _indextts25_headroom_gain(peak_amplitude: float) -> float:
    if peak_amplitude <= INDEXTTS25_NATIVE_HEADROOM_TARGET:
        return 1.0
    return INDEXTTS25_NATIVE_HEADROOM_TARGET / peak_amplitude


def _run_indextts25_inference(
    renderer: Any,
    *,
    speaker_path: Path,
    target_text: str,
    output_path: Path,
    emotion_path: Path,
    emotion_alpha: float,
    seed: int,
) -> tuple[Any, float]:
    import random

    import numpy as np
    import torch

    adapted = adapt_indextts25_speech_text(
        target_text,
        renderer.g2p,
    )
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    renderer.native_headroom_events = []
    output_path.unlink(missing_ok=True)
    started_at = time.perf_counter()
    resolved_path = renderer.tts.infer(
        spk_audio_prompt=str(speaker_path),
        text=adapted.text,
        output_path=str(output_path),
        emo_audio_prompt=str(emotion_path),
        emo_alpha=emotion_alpha,
        **INDEXTTS25_INFERENCE_SETTINGS,
    )
    inference_seconds = time.perf_counter() - started_at
    if resolved_path is None or not output_path.exists():
        raise RuntimeError("IndexTTS 2.5 produced no output")
    return adapted, inference_seconds


@app.cls(
    image=image25,
    gpu="L40S",
    timeout=3600,
    scaledown_window=60,
    volumes={"/models": model25_volume, TRANSFER_DIR: transfer_volume},
)
class IndexTTS25BatchRenderer:
    @modal.enter()
    def load_model(self) -> None:
        import os

        import torch
        from g2p_en import G2p
        from huggingface_hub import snapshot_download
        from indextts.infer_v2_5 import IndexTTS2

        model_path = Path(INDEXTTS25_MODEL_DIR)
        self.model_snapshot = _ensure_indextts25_model_snapshot(
            model_path,
            snapshot_download,
            verify_content=False,
        )
        self.modal_image_id = os.environ.get("MODAL_IMAGE_ID")
        if not self.modal_image_id:
            raise RuntimeError("Modal image identity is unavailable")
        self.renderer_implementation_sha256 = (
            _renderer_implementation_sha256()
        )
        model25_volume.commit()

        torch.cuda.reset_peak_memory_stats()
        load_started = time.perf_counter()
        self.tts = IndexTTS2(
            cfg_path=str(model_path / "config.yaml"),
            model_dir=str(model_path),
            **INDEXTTS25_LOAD_SETTINGS,
        )
        self.g2p = G2p()
        official_bigvgan_forward = self.tts.bigvgan.forward
        self.native_headroom_events: list[dict[str, float]] = []

        def forward_with_native_headroom(*args: Any, **kwargs: Any) -> Any:
            waveform = official_bigvgan_forward(*args, **kwargs)
            peak_amplitude = float(waveform.detach().abs().max().item())
            gain = _indextts25_headroom_gain(peak_amplitude)
            self.native_headroom_events.append(
                {
                    "pre_gain_peak_amplitude": peak_amplitude,
                    "gain": gain,
                }
            )
            if gain < 1.0:
                waveform = waveform * gain
            return waveform

        self.tts.bigvgan.forward = forward_with_native_headroom
        self.model_load_seconds = time.perf_counter() - load_started

    @modal.method()
    def render_shard(
        self,
        staged_source_path: str,
        speaker_reference_wavs: dict[str, bytes],
        speech_segments: list[dict],
        output_prefix: str,
        emotion_alpha: float = 0.8,
        seed: int = 42,
    ) -> dict:
        import hashlib as _hashlib

        import soundfile
        import torch

        transfer_volume.reload()
        source_path = Path(TRANSFER_DIR) / staged_source_path
        output_root = Path(TRANSFER_DIR) / output_prefix
        output_root.mkdir(parents=True, exist_ok=True)
        metadata_root = output_root / ".transpodcast-cache"
        metadata_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path("/tmp/transpodcast-indextts25-shard")
        work_dir.mkdir(parents=True, exist_ok=True)
        speaker_paths: dict[str, Path] = {}
        for speaker_id, reference_bytes in speaker_reference_wavs.items():
            speaker_path = work_dir / f"speaker-{speaker_id}.wav"
            speaker_path.write_bytes(reference_bytes)
            speaker_paths[speaker_id] = speaker_path
        source_audio, source_sample_rate = soundfile.read(
            source_path,
            always_2d=True,
        )
        source_duration_seconds = len(source_audio) / source_sample_rate
        results: list[dict] = []
        skipped = 0
        rejected_cache_reasons: list[dict[str, Any]] = []
        source_sha256 = _hashlib.sha256(source_path.read_bytes()).hexdigest()
        renderer_identity = {
            "model_id": INDEXTTS25_MODEL_ID,
            "model_revision": INDEXTTS25_MODEL_REVISION,
            "code_commit": INDEXTTS25_COMMIT,
            "runtime_revision": INDEXTTS25_RUNTIME_REVISION,
            "runtime_base_image": INDEXTTS25_BASE_IMAGE,
            "modal_image_id": self.modal_image_id,
            "renderer_implementation_sha256": (
                self.renderer_implementation_sha256
            ),
            "pronunciation_policy_version": (
                INDEXTTS25_PRONUNCIATION_POLICY_VERSION
            ),
            "load_settings": INDEXTTS25_LOAD_SETTINGS,
            "inference_settings": INDEXTTS25_INFERENCE_SETTINGS,
            "native_headroom_target": INDEXTTS25_NATIVE_HEADROOM_TARGET,
            "model_manifest_sha256": self.model_snapshot[
                "snapshot_manifest_sha256"
            ],
            "seed": seed,
            "emotion_alpha": emotion_alpha,
        }
        total_started_at = time.perf_counter()
        for segment in speech_segments:
            segment_id = str(segment["segment_id"])
            output_path = output_root / f"{segment_id}.wav"
            metadata_path = metadata_root / f"{segment_id}.json"
            adapted = adapt_indextts25_speech_text(
                str(segment["target_text"]),
                self.g2p,
            )
            emotion_audio = segment.get("emotion_reference_audio")
            if isinstance(emotion_audio, bytes):
                start_seconds = -1.0
                end_seconds = -1.0
                emotion_identity = {
                    "kind": "provided-audio",
                    "sha256": _hashlib.sha256(emotion_audio).hexdigest(),
                }
            else:
                start_seconds, end_seconds = _emotion_window(
                    float(segment["source_start_seconds"]),
                    float(segment["source_end_seconds"]),
                    source_duration_seconds,
                )
                emotion_identity = {
                    "kind": "source-window",
                    "source_sha256": source_sha256,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                }
            speaker_id = str(segment["speaker_id"])
            input_identity = {
                **renderer_identity,
                "segment_id": segment_id,
                "target_text_sha256": _hashlib.sha256(
                    str(segment["target_text"]).encode("utf-8")
                ).hexdigest(),
                "speech_text_sha256": adapted.sha256,
                "pronunciation_annotation_count": adapted.annotation_count,
                "chinese_replacement_count": adapted.chinese_replacement_count,
                "pronunciation_fallback_count": (
                    adapted.pronunciation_fallback_count
                ),
                "pronunciation_annotation_surfaces": list(
                    adapted.pronunciation_annotation_surfaces
                ),
                "speaker_id": speaker_id,
                "speaker_reference_sha256": _hashlib.sha256(
                    speaker_reference_wavs[speaker_id]
                ).hexdigest(),
                "emotion_reference": emotion_identity,
            }
            cache_rejections = _indextts25_cache_rejection_reasons(
                output_path,
                metadata_path,
                input_identity,
            )
            if not cache_rejections:
                output_audio, output_sample_rate = soundfile.read(output_path)
                content = output_path.read_bytes()
                results.append(
                    {
                        "segment_id": segment_id,
                        "file": output_path.name,
                        "sha256": _hashlib.sha256(content).hexdigest(),
                        "sample_rate_hz": output_sample_rate,
                        "duration_seconds": (
                            len(output_audio) / output_sample_rate
                        ),
                        "inference_seconds": 0.0,
                        "emotion_reference_start_seconds": start_seconds,
                        "emotion_reference_end_seconds": end_seconds,
                        "speech_text_sha256": adapted.sha256,
                        "pronunciation_annotation_count": (
                            adapted.annotation_count
                        ),
                        "chinese_replacement_count": (
                            adapted.chinese_replacement_count
                        ),
                        "pronunciation_fallback_count": (
                            adapted.pronunciation_fallback_count
                        ),
                        "pronunciation_annotation_surfaces": list(
                            adapted.pronunciation_annotation_surfaces
                        ),
                    }
                )
                skipped += 1
                continue
            if output_path.exists() or metadata_path.exists():
                rejected_cache_reasons.append(
                    {"segment_id": segment_id, "reasons": cache_rejections}
                )
            emotion_path = work_dir / f"{segment_id}-emotion.wav"
            if isinstance(emotion_audio, bytes):
                emotion_path.write_bytes(emotion_audio)
            else:
                start_frame = round(start_seconds * source_sample_rate)
                end_frame = round(end_seconds * source_sample_rate)
                soundfile.write(
                    emotion_path,
                    source_audio[start_frame:end_frame],
                    source_sample_rate,
                )
            scratch_path = work_dir / f"{segment_id}.wav"
            adapted, inference_seconds = _run_indextts25_inference(
                self,
                speaker_path=speaker_paths[str(segment["speaker_id"])],
                target_text=str(segment["target_text"]),
                output_path=scratch_path,
                emotion_path=emotion_path,
                emotion_alpha=emotion_alpha,
                seed=seed,
            )
            content = scratch_path.read_bytes()
            output_path.write_bytes(content)
            metadata_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "input_identity": input_identity,
                        "output": {
                            "bytes": len(content),
                            "sha256": _hashlib.sha256(content).hexdigest(),
                        },
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            output_audio, output_sample_rate = soundfile.read(scratch_path)
            results.append(
                {
                    "segment_id": segment_id,
                    "file": output_path.name,
                    "sha256": _hashlib.sha256(content).hexdigest(),
                    "sample_rate_hz": output_sample_rate,
                    "duration_seconds": len(output_audio) / output_sample_rate,
                    "inference_seconds": inference_seconds,
                    "emotion_reference_start_seconds": start_seconds,
                    "emotion_reference_end_seconds": end_seconds,
                    "speech_text_sha256": adapted.sha256,
                    "pronunciation_annotation_count": (
                        adapted.annotation_count
                    ),
                    "chinese_replacement_count": (
                        adapted.chinese_replacement_count
                    ),
                    "pronunciation_fallback_count": (
                        adapted.pronunciation_fallback_count
                    ),
                    "pronunciation_annotation_surfaces": list(
                        adapted.pronunciation_annotation_surfaces
                    ),
                }
            )
            if len(results) % 25 == 0:
                transfer_volume.commit()
        transfer_volume.commit()
        return {
            "segments": results,
            "skipped_existing": skipped,
            "rejected_cache_reasons": rejected_cache_reasons,
            "model_id": INDEXTTS25_MODEL_ID,
            "model_revision": INDEXTTS25_MODEL_REVISION,
            "code_commit": INDEXTTS25_COMMIT,
            "pronunciation_policy_version": (
                INDEXTTS25_PRONUNCIATION_POLICY_VERSION
            ),
            "lang": "zh",
            "load_settings": INDEXTTS25_LOAD_SETTINGS,
            "inference_settings": INDEXTTS25_INFERENCE_SETTINGS,
            "native_headroom_target": INDEXTTS25_NATIVE_HEADROOM_TARGET,
            "runtime_revision": INDEXTTS25_RUNTIME_REVISION,
            "runtime_base_image": INDEXTTS25_BASE_IMAGE,
            "modal_image_id": self.modal_image_id,
            "renderer_implementation_sha256": (
                self.renderer_implementation_sha256
            ),
            "model_manifest_sha256": self.model_snapshot[
                "snapshot_manifest_sha256"
            ],
            "model_file_count": self.model_snapshot["model_file_count"],
            "seed": seed,
            "emotion_alpha": emotion_alpha,
            "model_load_seconds": self.model_load_seconds,
            "total_inference_seconds": (
                time.perf_counter() - total_started_at
            ),
            "gpu_name": torch.cuda.get_device_name(),
        }

    @modal.method()
    def render(
        self,
        source_audio_wav: bytes,
        speaker_reference_wavs: dict[str, bytes],
        speech_segments: list[dict],
        emotion_alpha: float = 0.8,
        seed: int = 42,
    ) -> dict:
        import soundfile
        import torch

        work_dir = Path("/tmp/transpodcast-indextts25-batch")
        work_dir.mkdir(parents=True, exist_ok=True)
        source_path = work_dir / "source.wav"
        source_path.write_bytes(source_audio_wav)
        speaker_paths: dict[str, Path] = {}
        for speaker_id, reference_bytes in speaker_reference_wavs.items():
            speaker_path = work_dir / f"speaker-{speaker_id}.wav"
            speaker_path.write_bytes(reference_bytes)
            speaker_paths[speaker_id] = speaker_path
        source_audio, source_sample_rate = soundfile.read(
            source_path,
            always_2d=True,
        )
        source_duration_seconds = len(source_audio) / source_sample_rate
        results: list[dict] = []
        torch.cuda.reset_peak_memory_stats()
        total_started_at = time.perf_counter()
        for index, segment in enumerate(speech_segments, start=1):
            segment_id = str(segment["segment_id"])
            emotion_path = work_dir / f"{segment_id}-emotion.wav"
            emotion_audio = segment.get("emotion_reference_audio")
            if isinstance(emotion_audio, bytes):
                emotion_path.write_bytes(emotion_audio)
                start_seconds = -1.0
                end_seconds = -1.0
            else:
                start_seconds, end_seconds = _emotion_window(
                    float(segment["source_start_seconds"]),
                    float(segment["source_end_seconds"]),
                    source_duration_seconds,
                )
                start_frame = round(start_seconds * source_sample_rate)
                end_frame = round(end_seconds * source_sample_rate)
                soundfile.write(
                    emotion_path,
                    source_audio[start_frame:end_frame],
                    source_sample_rate,
                )
            output_path = work_dir / f"{segment_id}.wav"
            adapted, inference_seconds = _run_indextts25_inference(
                self,
                speaker_path=speaker_paths[str(segment["speaker_id"])],
                target_text=str(segment["target_text"]),
                output_path=output_path,
                emotion_path=emotion_path,
                emotion_alpha=emotion_alpha,
                seed=seed,
            )
            output_audio, output_sample_rate = soundfile.read(output_path)
            results.append(
                {
                    "segment_id": segment_id,
                    "audio": output_path.read_bytes(),
                    "sample_rate_hz": output_sample_rate,
                    "duration_seconds": len(output_audio) / output_sample_rate,
                    "inference_seconds": inference_seconds,
                    "emotion_reference_start_seconds": start_seconds,
                    "emotion_reference_end_seconds": end_seconds,
                    "speech_text_sha256": adapted.sha256,
                    "pronunciation_annotation_count": (
                        adapted.annotation_count
                    ),
                    "chinese_replacement_count": (
                        adapted.chinese_replacement_count
                    ),
                    "pronunciation_fallback_count": (
                        adapted.pronunciation_fallback_count
                    ),
                    "pronunciation_annotation_surfaces": list(
                        adapted.pronunciation_annotation_surfaces
                    ),
                }
            )
            print(
                f"Rendered {index}/{len(speech_segments)}: {segment_id}",
                flush=True,
            )
        return {
            "segments": results,
            "model_id": INDEXTTS25_MODEL_ID,
            "model_revision": INDEXTTS25_MODEL_REVISION,
            "code_commit": INDEXTTS25_COMMIT,
            "pronunciation_policy_version": (
                INDEXTTS25_PRONUNCIATION_POLICY_VERSION
            ),
            "lang": "zh",
            "load_settings": INDEXTTS25_LOAD_SETTINGS,
            "inference_settings": INDEXTTS25_INFERENCE_SETTINGS,
            "native_headroom_target": INDEXTTS25_NATIVE_HEADROOM_TARGET,
            "runtime_revision": INDEXTTS25_RUNTIME_REVISION,
            "runtime_base_image": INDEXTTS25_BASE_IMAGE,
            "modal_image_id": self.modal_image_id,
            "renderer_implementation_sha256": (
                self.renderer_implementation_sha256
            ),
            "model_manifest_sha256": self.model_snapshot[
                "snapshot_manifest_sha256"
            ],
            "model_file_count": self.model_snapshot["model_file_count"],
            "seed": seed,
            "emotion_alpha": emotion_alpha,
            "random_sampling": False,
            "total_inference_seconds": (
                time.perf_counter() - total_started_at
            ),
            "peak_gpu_memory_gib": (
                torch.cuda.max_memory_allocated() / (1024**3)
            ),
            "gpu_name": torch.cuda.get_device_name(),
        }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@app.local_entrypoint()
def main(
    source_path: str = str(DEFAULT_SOURCE_PATH),
    transcript_path: str = str(DEFAULT_TRANSCRIPT_PATH),
    translation_path: str = str(DEFAULT_TRANSLATION_PATH),
    speaker_dir: str = str(DEFAULT_SPEAKER_DIR),
    output_dir: str = str(DEFAULT_OUTPUT_DIR),
    emotion_alpha: float = 0.8,
    seed: int = 42,
) -> None:
    from baseline_manifest import load_render_plan

    if not 0.0 <= emotion_alpha <= 1.0:
        raise ValueError("Emotion alpha must be between 0 and 1")

    source = Path(source_path)
    transcript = Path(transcript_path)
    translation = Path(translation_path)
    speakers = Path(speaker_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    plan = load_render_plan(transcript, translation, speakers)
    speech_segments = [
        segment
        for segment in plan["segments"]
        if segment["kind"] == "speech"
    ]
    speaker_reference_paths = {
        speaker_id: Path(reference["path"])
        for speaker_id, reference in plan["speaker_references"].items()
    }
    speaker_reference_wavs = {
        speaker_id: path.read_bytes()
        for speaker_id, path in speaker_reference_paths.items()
    }

    result = IndexTTS2BatchRenderer().render.remote(
        source.read_bytes(),
        speaker_reference_wavs,
        speech_segments,
        emotion_alpha=emotion_alpha,
        seed=seed,
    )

    segment_metadata = []
    for segment in result.pop("segments"):
        segment_id = segment["segment_id"]
        if not re.fullmatch(r"turn-[0-9]{3}", segment_id):
            raise ValueError(f"Unsafe segment ID: {segment_id}")
        audio_path = destination / f"{segment_id}.wav"
        audio_path.write_bytes(segment.pop("audio"))
        segment["file"] = audio_path.name
        segment["sha256"] = sha256(audio_path)
        segment_metadata.append(segment)
        print(f"Audio: {audio_path}")

    result.update(
        {
            "video_id": plan["video_id"],
            "source_file": source.name,
            "source_sha256": sha256(source),
            "transcript_file": transcript.name,
            "transcript_sha256": sha256(transcript),
            "translation_file": translation.name,
            "translation_sha256": sha256(translation),
            "translation_method": plan["translation_method"],
            "translation_version": plan["translation_version"],
            "speaker_references": plan["speaker_references"],
            "segments": segment_metadata,
        }
    )
    metadata_path = destination / "render-metadata.json"
    metadata_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Metadata: {metadata_path}")
