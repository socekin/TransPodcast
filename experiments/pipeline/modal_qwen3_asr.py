from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import modal

APP_NAME = "transpodcast-pipeline-qwen3-asr"
ASR_MODEL_ID = "Qwen/Qwen3-ASR-1.7B"
ASR_MODEL_REVISION = (
    "7278e1e70fe206f11671096ffdd38061171dd6e5"  # pragma: allowlist secret
)
ALIGNER_MODEL_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
ALIGNER_MODEL_REVISION = (
    "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"  # pragma: allowlist secret
)
ASR_MODEL_DIR = "/models/qwen3-asr-1.7b"
ALIGNER_MODEL_DIR = "/models/qwen3-forced-aligner-0.6b"
DEFAULT_AUDIO_PATH = (
    "artifacts/pipeline-baselines/sample-baseline/source/excerpt-16k.wav"
)
DEFAULT_OUTPUT_PATH = (
    "artifacts/pipeline-baselines/sample-baseline/analysis/transcription.json"
)

model_volume = modal.Volume.from_name(
    "transpodcast-pipeline-models",
    create_if_missing=True,
)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "ffmpeg",
        "libsndfile1",
    )
    .pip_install(
        "qwen-asr==0.0.6",
        "huggingface_hub==0.34.4",
        "soundfile==0.13.1",
    )
    .env(
        {
            "HF_HOME": "/models/huggingface",
            "HF_HUB_CACHE": "/models/huggingface/hub",
        }
    )
)

app = modal.App(APP_NAME, image=image)


@app.cls(
    gpu="L40S",
    timeout=1800,
    scaledown_window=180,
    volumes={"/models": model_volume},
)
class Qwen3Transcriber:
    @modal.enter()
    def load_models(self) -> None:
        import torch
        from huggingface_hub import snapshot_download
        from qwen_asr import Qwen3ASRModel

        asr_path = Path(ASR_MODEL_DIR)
        aligner_path = Path(ALIGNER_MODEL_DIR)
        if not (asr_path / "config.json").exists():
            snapshot_download(
                repo_id=ASR_MODEL_ID,
                revision=ASR_MODEL_REVISION,
                local_dir=asr_path,
            )
            model_volume.commit()
        if not (aligner_path / "config.json").exists():
            snapshot_download(
                repo_id=ALIGNER_MODEL_ID,
                revision=ALIGNER_MODEL_REVISION,
                local_dir=aligner_path,
            )
            model_volume.commit()

        self.model = Qwen3ASRModel.from_pretrained(
            str(asr_path),
            dtype=torch.bfloat16,
            device_map="cuda:0",
            max_inference_batch_size=1,
            max_new_tokens=4096,
            forced_aligner=str(aligner_path),
            forced_aligner_kwargs={
                "dtype": torch.bfloat16,
                "device_map": "cuda:0",
            },
        )
        torch.cuda.reset_peak_memory_stats()

    @modal.method()
    def transcribe(self, audio_bytes: bytes) -> dict:
        import torch

        audio_path = Path("/tmp/source-16k.wav")
        audio_path.write_bytes(audio_bytes)
        torch.cuda.reset_peak_memory_stats()
        started_at = time.perf_counter()

        results = self.model.transcribe(
            audio=str(audio_path),
            language="English",
            return_time_stamps=True,
        )
        if not results:
            raise RuntimeError("Qwen3-ASR returned no transcription")

        result = results[0]
        if result.time_stamps is None:
            raise RuntimeError("Qwen3-ASR returned no forced alignment")

        words = [
            {
                "text": item.text,
                "start_seconds": item.start_time,
                "end_seconds": item.end_time,
            }
            for item in result.time_stamps.items
        ]

        return {
            "asr_model_id": ASR_MODEL_ID,
            "asr_model_revision": ASR_MODEL_REVISION,
            "aligner_model_id": ALIGNER_MODEL_ID,
            "aligner_model_revision": ALIGNER_MODEL_REVISION,
            "language": result.language,
            "text": result.text,
            "words": words,
            "gpu_name": torch.cuda.get_device_name(),
            "inference_seconds": time.perf_counter() - started_at,
            "peak_gpu_memory_gib": (
                torch.cuda.max_memory_allocated() / (1024**3)
            ),
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

    result = Qwen3Transcriber().transcribe.remote(source_path.read_bytes())
    result.update(
        {
            "source_file": source_path.name,
            "source_sha256": sha256(source_path),
        }
    )
    destination.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Transcription: {destination}")
