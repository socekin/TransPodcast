from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from experiments.pipeline.baseline_manifest import load_render_plan

DEFAULT_ROOT = Path("artifacts/pipeline-baselines/sample-baseline")
DEFAULT_SOURCE_PATH = DEFAULT_ROOT / "source/excerpt-master.wav"
DEFAULT_TRANSCRIPT_PATH = DEFAULT_ROOT / "transcript/canonical.en.json"
DEFAULT_TRANSLATION_PATH = DEFAULT_ROOT / "i18n/baseline.zh-CN.json"
DEFAULT_SPEAKER_DIR = DEFAULT_ROOT / "speakers"
DEFAULT_RENDERED_DIR = DEFAULT_ROOT / "rendered/baseline-human-v1"
DEFAULT_OUTPUT_DIR = DEFAULT_ROOT / "final/baseline-human-v1"
SAMPLE_RATE_HZ = 48000
LOUDNESS_TARGET_LUFS = -16.0
TRUE_PEAK_TARGET_DBTP = -1.0
LOUDNESS_RANGE_TARGET_LU = 11.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
    )


def parse_loudnorm_json(stderr: str) -> dict[str, str]:
    matches = re.findall(r"\{\s*\"input_i\"[\s\S]*?\}", stderr)
    if not matches:
        raise ValueError("Could not find FFmpeg loudness measurement")
    value = json.loads(matches[-1])
    if not isinstance(value, dict):
        raise TypeError("FFmpeg loudness measurement must be an object")
    return {str(key): str(item) for key, item in value.items()}


def probe_duration(path: Path) -> float:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(result.stdout.strip())


def prepare_audio(
    source: Path,
    destination: Path,
    duration_seconds: float,
) -> None:
    fade_seconds = min(0.01, duration_seconds / 4)
    fade_out_start = max(0.0, duration_seconds - fade_seconds)
    audio_filter = (
        f"aresample={SAMPLE_RATE_HZ},"
        f"afade=t=in:st=0:d={fade_seconds:.6f},"
        f"afade=t=out:st={fade_out_start:.6f}:d={fade_seconds:.6f}"
    )
    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            audio_filter,
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def extract_event(
    source: Path,
    destination: Path,
    start_seconds: float,
    end_seconds: float,
) -> None:
    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.6f}",
            "-t",
            f"{end_seconds - start_seconds:.6f}",
            "-i",
            str(source),
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def create_silence(destination: Path, duration_ms: int) -> None:
    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={SAMPLE_RATE_HZ}:cl=mono",
            "-t",
            f"{duration_ms / 1000:.6f}",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def _concat_quote(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def concatenate(sequence: list[Path], destination: Path, work_dir: Path) -> None:
    concat_path = work_dir / "concat.txt"
    concat_path.write_text(
        "".join(f"file '{_concat_quote(path)}'\n" for path in sequence),
        encoding="utf-8",
    )
    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def measure_loudness(path: Path) -> dict[str, str]:
    audio_filter = (
        f"loudnorm=I={LOUDNESS_TARGET_LUFS}:"
        f"TP={TRUE_PEAK_TARGET_DBTP}:"
        f"LRA={LOUDNESS_RANGE_TARGET_LU}:print_format=json"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            audio_filter,
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_loudnorm_json(result.stderr)


def normalize_master(
    source: Path,
    destination: Path,
    measurement: dict[str, str],
) -> None:
    audio_filter = (
        f"loudnorm=I={LOUDNESS_TARGET_LUFS}:"
        f"TP={TRUE_PEAK_TARGET_DBTP}:"
        f"LRA={LOUDNESS_RANGE_TARGET_LU}:"
        f"measured_I={measurement['input_i']}:"
        f"measured_TP={measurement['input_tp']}:"
        f"measured_LRA={measurement['input_lra']}:"
        f"measured_thresh={measurement['input_thresh']}:"
        f"offset={measurement['target_offset']}:"
        "linear=true:print_format=summary"
    )
    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            audio_filter,
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def encode_aac(source: Path, destination: Path) -> None:
    run_command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )


def assemble(
    source_path: Path,
    transcript_path: Path,
    translation_path: Path,
    speaker_dir: Path,
    rendered_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = load_render_plan(
        transcript_path,
        translation_path,
        speaker_dir,
    )
    render_metadata_path = rendered_dir / "render-metadata.json"
    render_metadata = json.loads(
        render_metadata_path.read_text(encoding="utf-8")
    )
    rendered_by_id = {
        item["segment_id"]: item for item in render_metadata["segments"]
    }

    master_path = output_dir / "transpodcast-baseline-master.wav"
    aac_path = output_dir / "transpodcast-baseline-apple-podcasts.m4a"
    timeline: list[dict[str, Any]] = []

    with TemporaryDirectory(prefix="transpodcast-assembly-") as temp_dir:
        work_dir = Path(temp_dir)
        sequence: list[Path] = []
        silence_paths: dict[int, Path] = {}
        timeline_cursor = 0.0

        for index, segment in enumerate(plan["segments"], start=1):
            segment_id = segment["segment_id"]
            prepared_path = work_dir / f"{index:03d}-{segment_id}.wav"

            if segment["kind"] == "speech":
                rendered = rendered_by_id.get(segment_id)
                if rendered is None:
                    raise ValueError(
                        f"Missing rendered audio for {segment_id}"
                    )
                raw_path = rendered_dir / rendered["file"]
                raw_duration = probe_duration(raw_path)
                prepare_audio(raw_path, prepared_path, raw_duration)
            else:
                raw_path = work_dir / f"{segment_id}-source.wav"
                extract_event(
                    source_path,
                    raw_path,
                    float(segment["source_start_seconds"]),
                    float(segment["source_end_seconds"]),
                )
                raw_duration = probe_duration(raw_path)
                prepare_audio(raw_path, prepared_path, raw_duration)

            prepared_duration = probe_duration(prepared_path)
            sequence.append(prepared_path)
            timeline.append(
                {
                    "segment_id": segment_id,
                    "kind": segment["kind"],
                    "output_start_seconds": timeline_cursor,
                    "output_end_seconds": (
                        timeline_cursor + prepared_duration
                    ),
                }
            )
            timeline_cursor += prepared_duration

            pause_after_ms = int(segment.get("pause_after_ms", 0))
            if pause_after_ms > 0:
                silence_path = silence_paths.get(pause_after_ms)
                if silence_path is None:
                    silence_path = (
                        work_dir / f"silence-{pause_after_ms}ms.wav"
                    )
                    create_silence(silence_path, pause_after_ms)
                    silence_paths[pause_after_ms] = silence_path
                sequence.append(silence_path)
                timeline_cursor += pause_after_ms / 1000

        raw_master_path = work_dir / "assembled-raw.wav"
        concatenate(sequence, raw_master_path, work_dir)
        first_pass = measure_loudness(raw_master_path)
        normalize_master(raw_master_path, master_path, first_pass)

    encode_aac(master_path, aac_path)
    final_measurement = measure_loudness(master_path)
    metadata = {
        "schema_version": 1,
        "video_id": plan["video_id"],
        "translation_version": plan["translation_version"],
        "render_model_id": render_metadata["model_id"],
        "render_model_revision": render_metadata["model_revision"],
        "render_code_commit": render_metadata["code_commit"],
        "emotion_alpha": render_metadata["emotion_alpha"],
        "seed": render_metadata["seed"],
        "source_file": source_path.name,
        "source_sha256": sha256(source_path),
        "transcript_sha256": sha256(transcript_path),
        "translation_sha256": sha256(translation_path),
        "render_metadata_sha256": sha256(render_metadata_path),
        "loudness_target_lufs": LOUDNESS_TARGET_LUFS,
        "true_peak_target_dbtp": TRUE_PEAK_TARGET_DBTP,
        "loudness_first_pass": first_pass,
        "loudness_final_measurement": final_measurement,
        "duration_seconds": probe_duration(master_path),
        "timeline": timeline,
        "outputs": [
            {
                "file": master_path.name,
                "format": "WAV PCM 24-bit mono 48 kHz",
                "sha256": sha256(master_path),
            },
            {
                "file": aac_path.name,
                "format": "AAC LC mono 48 kHz 128 kbps",
                "sha256": sha256(aac_path),
            },
        ],
    }
    metadata_path = output_dir / "assembly-metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument(
        "--transcript",
        type=Path,
        default=DEFAULT_TRANSCRIPT_PATH,
    )
    parser.add_argument(
        "--translation",
        type=Path,
        default=DEFAULT_TRANSLATION_PATH,
    )
    parser.add_argument(
        "--speaker-dir",
        type=Path,
        default=DEFAULT_SPEAKER_DIR,
    )
    parser.add_argument(
        "--rendered-dir",
        type=Path,
        default=DEFAULT_RENDERED_DIR,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    args = parser.parse_args()

    metadata = assemble(
        args.source,
        args.transcript,
        args.translation,
        args.speaker_dir,
        args.rendered_dir,
        args.output_dir,
    )
    print(
        f"Master duration: {metadata['duration_seconds']:.2f} seconds"
    )
    for output in metadata["outputs"]:
        print(f"Audio: {args.output_dir / output['file']}")


if __name__ == "__main__":
    main()
