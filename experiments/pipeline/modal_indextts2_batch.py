from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import modal

APP_NAME = "transpodcast-pipeline-indextts2"
MODEL_ID = "IndexTeam/IndexTTS-2"
MODEL_REVISION = "740dcaff396282ffb241903d150ac011cd4b1ede"  # pragma: allowlist secret
MODEL_DIR = "/models/index-tts-2"
INDEXTTS_COMMIT = "13495845e3028f0bb6ca1462ad22aa0e76349e40"  # pragma: allowlist secret
INDEXTTS_DIR = "/opt/index-tts"
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
transfer_volume = modal.Volume.from_name(
    "transpodcast-pipeline-transfer",
    create_if_missing=True,
)
TRANSFER_DIR = "/transfer"

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
)

app = modal.App(APP_NAME, image=image)


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
