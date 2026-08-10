from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import modal

APP_NAME = "transpodcast-pipeline-speaker-embeddings"
MODEL_ID = "nvidia/speakerverification_en_titanet_large"
MODEL_REVISION = "0dc382f40121a5fbd34db10a2bb04d826c2be6a8"  # pragma: allowlist secret
MODEL_FILENAME = "speakerverification_en_titanet_large.nemo"
MODEL_DIR = "/models/speakerverification_en_titanet_large"
DEFAULT_CANDIDATE_DIR = (
    "artifacts/pipeline-baselines/sample-baseline/speakers/candidates"
)
DEFAULT_OUTPUT_PATH = (
    "artifacts/pipeline-baselines/sample-baseline/analysis/"
    "speaker-matching.json"
)
DEFAULT_SPEAKER_A_REFERENCE = (
    "artifacts/model-evaluation/source/speaker-a-reference.wav"
)
DEFAULT_SPEAKER_B_REFERENCE = (
    "artifacts/model-evaluation/source/speaker-b-reference.wav"
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
        "nemo_toolkit[asr]==2.7.3",
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
class SpeakerEmbedder:
    @modal.enter()
    def load_model(self) -> None:
        import torch
        from huggingface_hub import hf_hub_download
        from nemo.collections.asr.models import EncDecSpeakerLabelModel

        model_dir = Path(MODEL_DIR)
        model_path = model_dir / MODEL_FILENAME
        if not model_path.exists():
            model_dir.mkdir(parents=True, exist_ok=True)
            hf_hub_download(
                repo_id=MODEL_ID,
                filename=MODEL_FILENAME,
                revision=MODEL_REVISION,
                local_dir=model_dir,
            )
            model_volume.commit()

        self.model = EncDecSpeakerLabelModel.restore_from(
            restore_path=str(model_path),
            map_location="cuda",
        )
        self.model.eval()
        torch.cuda.reset_peak_memory_stats()

    @modal.method()
    def compare(self, audio_files: dict[str, bytes]) -> dict:
        import torch
        from torch.nn import functional

        torch.cuda.reset_peak_memory_stats()
        started_at = time.perf_counter()
        embeddings: dict[str, torch.Tensor] = {}

        for name, audio_bytes in sorted(audio_files.items()):
            audio_path = Path("/tmp") / f"{name}.wav"
            audio_path.write_bytes(audio_bytes)
            embedding = self.model.get_embedding(str(audio_path))
            embeddings[name] = functional.normalize(
                embedding.flatten().float(),
                dim=0,
            )

        similarities: dict[str, dict[str, float]] = {}
        for left_name, left_embedding in embeddings.items():
            similarities[left_name] = {}
            for right_name, right_embedding in embeddings.items():
                score = torch.dot(left_embedding, right_embedding).item()
                similarities[left_name][right_name] = score

        return {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "gpu_name": torch.cuda.get_device_name(),
            "inference_seconds": time.perf_counter() - started_at,
            "peak_gpu_memory_gib": (
                torch.cuda.max_memory_allocated() / (1024**3)
            ),
            "similarities": similarities,
            "embeddings": {
                name: embedding.detach().cpu().tolist()
                for name, embedding in embeddings.items()
            },
        }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@app.local_entrypoint()
def main(
    candidate_dir: str = DEFAULT_CANDIDATE_DIR,
    output_path: str = DEFAULT_OUTPUT_PATH,
    speaker_a_reference: str = DEFAULT_SPEAKER_A_REFERENCE,
    speaker_b_reference: str = DEFAULT_SPEAKER_B_REFERENCE,
) -> None:
    candidate_path = Path(candidate_dir)
    source_paths = {
        path.stem: path for path in sorted(candidate_path.glob("*.wav"))
    }
    source_paths["reference_speaker_a"] = Path(speaker_a_reference)
    source_paths["reference_speaker_b"] = Path(speaker_b_reference)

    audio_files = {
        name: path.read_bytes() for name, path in source_paths.items()
    }
    result = SpeakerEmbedder().compare.remote(audio_files)
    result["sources"] = {
        name: {
            "path": str(path),
            "sha256": sha256(path),
        }
        for name, path in source_paths.items()
    }

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )
    print(f"Speaker matching: {destination}")
