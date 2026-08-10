from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import modal

APP_NAME = "transpodcast-pipeline-sortformer"
MODEL_ID = "nvidia/diar_streaming_sortformer_4spk-v2.1"
MODEL_REVISION = "fafaab5faa1617a0ca52d38dd3dc4bd636800d3d"  # pragma: allowlist secret
MODEL_FILENAME = "diar_streaming_sortformer_4spk-v2.1.nemo"
MODEL_DIR = "/models/diar_streaming_sortformer_4spk-v2.1"
NEMO_VERSION = "2.7.3"
GPU_TYPE = "L40S"
STREAMING_CHUNK_FRAMES = 340
STREAMING_RIGHT_CONTEXT_FRAMES = 40
STREAMING_FIFO_FRAMES = 40
STREAMING_CACHE_UPDATE_FRAMES = 300
STREAMING_SPEAKER_CACHE_FRAMES = 188
DEFAULT_AUDIO_PATH = (
    "artifacts/pipeline-baselines/sample-baseline/source/excerpt-16k.wav"
)
DEFAULT_OUTPUT_PATH = (
    "artifacts/pipeline-baselines/sample-baseline/analysis/diarization.json"
)

model_volume = modal.Volume.from_name(
    "transpodcast-pipeline-models",
    create_if_missing=True,
)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "ffmpeg",
        "libsndfile1",
        "sox",
    )
    .pip_install(
        "torch==2.7.1",
        "torchaudio==2.7.1",
        index_url="https://download.pytorch.org/whl/cu128",
    )
    .pip_install(
        "Cython==3.1.2",
        "huggingface_hub==0.34.4",
        f"nemo_toolkit[asr]=={NEMO_VERSION}",
    )
    .env(
        {
            "HF_HOME": "/models/huggingface",
            "HF_HUB_CACHE": "/models/huggingface/hub",
        }
    )
)

app = modal.App(APP_NAME, image=image)


def _configure_streaming_model(model: Any) -> None:
    modules = model.sortformer_modules
    modules.chunk_len = STREAMING_CHUNK_FRAMES
    modules.chunk_right_context = STREAMING_RIGHT_CONTEXT_FRAMES
    modules.fifo_len = STREAMING_FIFO_FRAMES
    modules.spkcache_update_period = STREAMING_CACHE_UPDATE_FRAMES
    modules.spkcache_len = STREAMING_SPEAKER_CACHE_FRAMES
    modules._check_streaming_parameters()


def _parse_segment(value: object) -> dict:
    if isinstance(value, str):
        parts = value.split()
        if len(parts) < 3:
            raise ValueError(f"Unexpected Sortformer segment: {value!r}")
        return {
            "start_seconds": float(parts[0]),
            "end_seconds": float(parts[1]),
            "speaker": parts[2],
        }

    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return {
            "start_seconds": float(value[0]),
            "end_seconds": float(value[1]),
            "speaker": str(value[2]),
        }

    raise TypeError(f"Unsupported Sortformer segment: {value!r}")


@app.cls(
    gpu=GPU_TYPE,
    timeout=1800,
    scaledown_window=180,
    volumes={"/models": model_volume},
)
class SortformerDiarizer:
    @modal.enter()
    def load_model(self) -> None:
        import torch
        from huggingface_hub import hf_hub_download
        from nemo.collections.asr.models import SortformerEncLabelModel

        model_dir = Path(MODEL_DIR)
        model_path = model_dir / MODEL_FILENAME
        if not model_path.exists():
            model_dir.mkdir(parents=True, exist_ok=True)
            hf_hub_download(
                repo_id=MODEL_ID,
                filename=model_path.name,
                revision=MODEL_REVISION,
                local_dir=model_dir,
            )
            model_volume.commit()

        self.model = SortformerEncLabelModel.restore_from(
            restore_path=str(model_path),
            map_location="cuda",
            strict=False,
        )
        self.model.eval()
        _configure_streaming_model(self.model)
        torch.cuda.reset_peak_memory_stats()

    @modal.method()
    def diarize(self, audio_bytes: bytes) -> dict:
        import torch

        audio_path = Path("/tmp/source-16k.wav")
        audio_path.write_bytes(audio_bytes)
        torch.cuda.reset_peak_memory_stats()
        started_at = time.perf_counter()

        raw_results = self.model.diarize(
            audio=[str(audio_path)],
            batch_size=1,
        )
        if not raw_results:
            raise RuntimeError("Sortformer returned no diarization result")

        first_result = raw_results[0]
        segments = [_parse_segment(value) for value in first_result]
        segments.sort(
            key=lambda segment: (
                segment["start_seconds"],
                segment["end_seconds"],
                segment["speaker"],
            )
        )

        return {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "gpu_name": torch.cuda.get_device_name(),
            "inference_seconds": time.perf_counter() - started_at,
            "peak_gpu_memory_gib": (
                torch.cuda.max_memory_allocated() / (1024**3)
            ),
            "segments": segments,
        }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@app.local_entrypoint()
def main(
    audio_path: str = DEFAULT_AUDIO_PATH,
    output_path: str = DEFAULT_OUTPUT_PATH,
) -> None:
    source_path = Path(audio_path)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    result = SortformerDiarizer().diarize.remote(source_path.read_bytes())
    result.update(
        {
            "source_file": source_path.name,
            "source_sha256": sha256(source_path),
        }
    )
    destination.write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )
    print(f"Diarization: {destination}")
