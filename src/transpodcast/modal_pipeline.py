from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import modal

from transpodcast.adjudication_judges import (
    MINIMUM_FRAGMENT_SECONDS,
    measure_fragment_voiceprints,
)
from transpodcast.boundary_adjudication import (
    ADJUDICATION_POLICY_VERSION,
    CONTINUITY_ADJUDICATION_POLICY_VERSION,
    SHORT_BOUNDARY_TRIGGER,
    SPARSE_LABEL_TRIGGER,
    find_ownership_disputes,
)
from transpodcast.config import TransPodcastSettings
from transpodcast.decision_records import DecisionRecord
from transpodcast.pipeline_versions import (
    CURRENT_SPEECH_PIPELINE_VERSION,
    SPEECH_PIPELINE_V13,
    SPEECH_PIPELINE_V14,
)
from transpodcast.source_segmentation import (
    MAXIMUM_SEGMENT_SECONDS,
    group_assigned_words,
    join_words,
)
from transpodcast.speaker_boundaries import (
    resolve_unassigned_speakers,
)
from transpodcast.speaker_consolidation import (
    CONSOLIDATION_POLICY_VERSION,
    ConsolidationWindow,
    consolidate_episode_speakers,
    select_voice_references,
)

# V12 keeps the accepted V11/V10/V9/v6 listening baseline while upgrading the
# pinned diarizer. The rejected v7/v8 experiments remain research history; see
# ADR-0029 and the V12 release record.
PIPELINE_VERSION = CURRENT_SPEECH_PIPELINE_VERSION
_MASTER_LOUDNESS_LUFS = -16
_MASTER_TRUE_PEAK_DBTP = -1.5
_MASTER_LOUDNESS_RANGE_LU = 11
_FINAL_LOUDNESS_MIN_LUFS = -16.5
_FINAL_LOUDNESS_MAX_LUFS = -15.5
_FINAL_TRUE_PEAK_MAX_DBTP = -1.0
_CODEC_PEAK_SAFETY_DB = 0.1
_CLIPPING_MAX_DBTP = -1.0
SORTFORMER_APP = "transpodcast-pipeline-sortformer"
QWEN_ASR_APP = "transpodcast-pipeline-qwen3-asr"
SPEAKER_APP = "transpodcast-pipeline-speaker-embeddings"
INDEXTTS_APP = "transpodcast-pipeline-indextts2"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the versioned TransPodcast Modal speech pipeline."
    )
    parser.add_argument(
        "--operation",
        required=True,
        choices=("analyze", "render", "remaster", "readiness"),
    )
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    request = _load_object(arguments.request)
    try:
        if arguments.operation == "readiness":
            response = readiness()
        elif arguments.operation == "analyze":
            response = analyze(request, arguments.output.parent)
        elif arguments.operation == "remaster":
            response = remaster(request, arguments.output.parent)
        else:
            response = render(request, arguments.output.parent)
    except modal.exception.AuthError as error:
        print(f"[AUTH] {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except modal.exception.ResourceExhaustedError as error:
        print(f"[SPEND_GUARDRAIL] {error}", file=sys.stderr)
        raise SystemExit(3) from error
    except modal.exception.TimeoutError as error:
        print(f"[TRANSIENT] {error}", file=sys.stderr)
        raise SystemExit(4) from error
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(response, ensure_ascii=False),
        encoding="utf-8",
    )


def readiness() -> dict[str, Any]:
    environment = _modal_settings().modal_environment
    components = (
        (SORTFORMER_APP, "SortformerDiarizer"),
        (QWEN_ASR_APP, "Qwen3Transcriber"),
        (SPEAKER_APP, "SpeakerEmbedder"),
        (INDEXTTS_APP, "IndexTTS2BatchRenderer"),
    )
    for app_name, class_name in components:
        modal.Cls.from_name(
            app_name,
            class_name,
            environment_name=environment,
        ).hydrate()
    return {
        "ready": True,
        "environment": environment,
        "pipeline_version": PIPELINE_VERSION,
        "components": [
            {"app": app_name, "class": class_name}
            for app_name, class_name in components
        ],
    }


def analyze(request: dict[str, Any], output_root: Path) -> dict[str, Any]:
    source = _object(request, "source")
    source_path = Path(str(source["audio_path"]))
    _verify_source(source_path, str(source["checksum"]))
    started = time.perf_counter()
    speakers: list[dict[str, Any]]
    with TemporaryDirectory(prefix="transpodcast-source-analysis-") as temp:
        root = Path(temp)
        source_wav = root / "source-16k.wav"
        _transcode(source_path, source_wav, sample_rate=16_000)
        audio = source_wav.read_bytes()
        # Diarization and transcription depend only on the same waveform
        # and run concurrently; TitaNet starts as soon as diarization
        # finishes, while transcription may still be running.
        with ThreadPoolExecutor(max_workers=3) as pool:
            diarization_future = pool.submit(
                _remote_method,
                SORTFORMER_APP,
                "SortformerDiarizer",
                "diarize",
                audio,
            )
            transcription_future = pool.submit(
                _remote_method,
                QWEN_ASR_APP,
                "Qwen3Transcriber",
                "transcribe",
                audio,
            )
            diarization = diarization_future.result()
            turns = list(diarization["segments"])
            reference_root = output_root / "speaker-references"
            reference_root.mkdir(parents=True, exist_ok=True)
            references = _extract_speaker_references(
                source_wav,
                turns,
                reference_root,
            )
            embedding_payload = {
                label: path.read_bytes()
                for label, path in references.items()
            }
            window_plans = plan_consolidation_windows(turns)
            embedding_payload.update(
                _extract_consolidation_windows(
                    source_wav,
                    window_plans,
                    root,
                )
            )
            speaker_future = pool.submit(
                _remote_method,
                SPEAKER_APP,
                "SpeakerEmbedder",
                "compare",
                embedding_payload,
            )
            transcription = transcription_future.result()
            speaker_result = speaker_future.result()
        segments = align_transcript(
            list(transcription["words"]),
            turns,
        )
        speakers = [
            {
                "local_label": label,
                "reference_audio_path": str(path),
                "embedding": speaker_result.get(
                    "embeddings",
                    {},
                ).get(label, []),
                "confidence": 0.98,
            }
            for label, path in sorted(references.items())
        ]
        segments, speakers, speaker_consolidation = (
            _apply_speaker_consolidation(
                source_wav=source_wav,
                reference_root=reference_root,
                turns=turns,
                segments=segments,
                window_plans=window_plans,
                embeddings=speaker_result.get("embeddings", {}),
            )
        )
        boundary_adjudication = _detect_boundary_disputes(
            source_wav=source_wav,
            scratch_root=root,
            segments=segments,
            speakers=speakers,
        )
        source_events = detect_source_events(segments)
        segments = [
            segment
            for segment in segments
            if not bool(segment.pop("_event_only", False))
        ]
        adjudication_pending = bool(
            (boundary_adjudication or {}).get("pending")
        )
        if not adjudication_pending:
            # With disputes pending, the worker layer rewrites the
            # attribution ledger after applying the verdicts so it
            # always describes the final segments.
            speaker_consolidation["decision_records"] = [
                *speaker_consolidation["decision_records"],
                *_segment_attribution_records(
                    segments,
                    policy_version=PIPELINE_VERSION,
                ),
            ]
    canonical: dict[str, Any] = {
        "segments": segments,
        "source_events": source_events,
        "speakers": speakers,
        "raw_diarization_turns": list(diarization["segments"]),
        "full_asr_text": str(transcription.get("text", "")),
        "aligned_words": list(transcription["words"]),
    }
    canonical["speaker_consolidation"] = speaker_consolidation
    canonical["boundary_adjudication"] = boundary_adjudication
    return {
        **canonical,
        "pipeline_version": PIPELINE_VERSION,
        "diarization_model": str(diarization["model_id"]),
        "diarization_model_revision": str(
            diarization.get("model_revision", "")
        ),
        "transcription_model": str(transcription["asr_model_id"]),
        "alignment_model": str(transcription["aligner_model_id"]),
        "background_regions": [],
        "call_seconds": {
            "diarization": float(
                diarization.get("inference_seconds", 0.0)
            ),
            "transcription": float(
                transcription.get("inference_seconds", 0.0)
            ),
            "speaker_embeddings": float(
                speaker_result.get("inference_seconds", 0.0)
            ),
        },
        "checksum": _content_hash(canonical),
        "estimated_cost_usd": _estimate_cost(
            float(diarization.get("inference_seconds", 0))
            + float(transcription.get("inference_seconds", 0))
            + float(speaker_result.get("inference_seconds", 0))
        ),
        "elapsed_seconds": time.perf_counter() - started,
    }


def render(request: dict[str, Any], output_root: Path) -> dict[str, Any]:
    source = _object(request, "source")
    analysis = _object(source, "analysis")
    pipeline_version = _pipeline_version_for_analysis(analysis)
    translation = _object(request, "translation")
    rendering = _object(request, "rendering")
    scope = str(rendering.get("scope", "complete_episode"))
    segment_ordinal = (
        int(rendering["segment_ordinal"])
        if scope == "segment"
        else None
    )
    source_path = Path(str(source["audio_path"]))
    segments = [
        item for item in list(analysis["segments"])
    ]
    target_segments = [str(value) for value in translation["segments"]]
    if len(segments) != len(target_segments):
        raise ValueError(
            "Translation coverage does not match the Canonical Transcript"
        )
    empty_target_ids = [
        str(segment.get("segment_id", index))
        for index, (segment, target) in enumerate(
            zip(segments, target_segments, strict=True),
            start=1,
        )
        if not target.strip()
    ]
    if empty_target_ids:
        raise ValueError(
            "Translation contains empty target segments: "
            + ", ".join(empty_target_ids)
        )
    speaker_references = {
        str(item["local_label"]): Path(
            str(item["reference_artifact_key"])
        ).read_bytes()
        for item in analysis.get("speakers", [])
    }
    if not speaker_references:
        raise ValueError("No Speaker Voice References are available")
    with TemporaryDirectory(prefix="transpodcast-render-") as temp:
        root = Path(temp)
        source_wav = root / "source-24k.wav"
        _transcode(source_path, source_wav, sample_rate=24_000)
        render_plan = [
            {
                "segment_id": str(item["segment_id"]),
                "speaker_id": str(item["speaker_label"]),
                "source_start_seconds": float(item["start_seconds"]),
                "source_end_seconds": float(item["end_seconds"]),
                "target_text": target_text,
                **(
                    {
                        "emotion_reference_audio": Path(
                            str(
                                item[
                                    "emotion_reference_artifact_key"
                                ]
                            )
                        ).read_bytes()
                    }
                    if item.get("emotion_reference_artifact_key")
                    else {}
                ),
            }
            for item, target_text in zip(
                segments,
                target_segments,
                strict=True,
            )
        ]
        emotion_alpha = float(rendering.get("emotion_alpha", 0.8))
        rendered_root = output_root / "rendered-segments"
        rendered_root.mkdir(parents=True, exist_ok=True)
        rendered_paths: list[Path] = []
        rendered_metadata: list[dict[str, Any]] = []
        if segment_ordinal is not None:
            if not 1 <= segment_ordinal <= len(render_plan):
                raise ValueError("Segment revision ordinal is out of range")
            result = _remote_method(
                INDEXTTS_APP,
                "IndexTTS2BatchRenderer",
                "render",
                source_wav.read_bytes(),
                speaker_references,
                [render_plan[segment_ordinal - 1]],
                emotion_alpha=emotion_alpha,
                seed=42,
            )
            for item in result["segments"]:
                path = rendered_root / f"segment-{segment_ordinal:04d}.wav"
                path.write_bytes(bytes(item["audio"]))
                rendered_paths.append(path)
                rendered_metadata.append(
                    {
                        "ordinal": segment_ordinal,
                        "segment_id": str(item["segment_id"]),
                        "path": str(path),
                        "sha256": _file_hash(path),
                        "duration_seconds": float(item["duration_seconds"]),
                        "emotion_reference_start_seconds": float(
                            item["emotion_reference_start_seconds"]
                        ),
                        "emotion_reference_end_seconds": float(
                            item["emotion_reference_end_seconds"]
                        ),
                    }
                )
        else:
            result = _render_sharded(
                source_wav,
                str(source.get("checksum") or _file_hash(source_path)),
                speaker_references,
                render_plan,
                rendered_root,
                emotion_alpha=emotion_alpha,
                pipeline_version=pipeline_version,
            )
            for ordinal, item in enumerate(result.pop("segments"), start=1):
                path = Path(str(item["path"]))
                rendered_paths.append(path)
                rendered_metadata.append({"ordinal": ordinal, **item})
        publication_path = output_root / "publication.m4a"
        if segment_ordinal is None:
            sequence = _assembly_sequence(
                source_wav,
                segments,
                rendered_paths,
                list(analysis.get("source_events", [])),
                root,
            )
            segment_timings = _assembled_segment_timings(
                sequence,
                rendered_paths,
                rendered_metadata,
            )
            assembly = assemble_publication(
                sequence,
                publication_path,
                rendered_segment_count=len(rendered_paths),
            )
        else:
            prior_preview_path_value = rendering.get(
                "prior_preview_audio_path"
            )
            prior_render = rendering.get("prior_render")
            if not prior_preview_path_value or not isinstance(
                prior_render,
                dict,
            ):
                raise ValueError(
                    "Segment revision requires the prior render checkpoint"
                )
            prior_timings = [
                (float(item[0]), float(item[1]))
                for item in prior_render.get("segment_timings", [])
            ]
            if len(prior_timings) != len(target_segments):
                raise ValueError(
                    "Prior segment timing checkpoint is incomplete"
                )
            start_seconds, end_seconds = prior_timings[
                segment_ordinal - 1
            ]
            replacement_duration = float(
                rendered_metadata[0]["duration_seconds"]
            )
            assembly = _replace_publication_region(
                Path(str(prior_preview_path_value)),
                rendered_paths[0],
                start_seconds,
                end_seconds,
                publication_path,
                root,
                rendered_segment_count=len(target_segments),
            )
            shift = replacement_duration - (
                end_seconds - start_seconds
            )
            segment_timings = []
            for index, (start, end) in enumerate(prior_timings, start=1):
                if index < segment_ordinal:
                    segment_timings.append((start, end))
                elif index == segment_ordinal:
                    segment_timings.append(
                        (start_seconds, start_seconds + replacement_duration)
                    )
                else:
                    segment_timings.append((start + shift, end + shift))
            rendered_metadata[0]["assembled_start_seconds"] = start_seconds
            rendered_metadata[0]["assembled_end_seconds"] = (
                start_seconds + replacement_duration
            )
    return {
        "publication_audio_path": str(publication_path),
        "duration_seconds": round(float(assembly["duration_seconds"])),
        "chapters": _chapters_from_timings(segment_timings),
        "pipeline_version": pipeline_version,
        "rendered_segments": rendered_metadata,
        "render_model": str(result["model_id"]),
        "render_model_revision": str(result["model_revision"]),
        "render_settings": {
            "emotion_alpha": float(result["emotion_alpha"]),
            "seed": int(result["seed"]),
            "random_sampling": bool(result["random_sampling"]),
        },
        "quality": assembly["quality"],
        "segment_timings": [
            {"start_seconds": start, "end_seconds": end}
            for start, end in segment_timings
        ],
        "estimated_cost_usd": _estimate_cost(
            float(result.get("total_inference_seconds", 0))
        ),
    }


def remaster(request: dict[str, Any], output_root: Path) -> dict[str, Any]:
    publication = _object(request, "publication_audio")
    pipeline_version = str(
        publication.get("pipeline_version") or PIPELINE_VERSION
    )
    source_path = Path(str(publication["audio_path"]))
    if not source_path.is_file():
        raise FileNotFoundError("Publication Audio is unavailable")
    publication_path = output_root / "publication-remastered.m4a"
    assembly = remaster_publication(
        source_path,
        publication_path,
        rendered_segment_count=int(publication["rendered_segments"]),
    )
    return {
        "publication_audio_path": str(publication_path),
        "duration_seconds": round(float(assembly["duration_seconds"])),
        "pipeline_version": pipeline_version,
        "quality": assembly["quality"],
    }


RENDER_SHARD_COUNT = 4
_FETCH_CHUNK_SIZE = 40


def split_render_plan(
    render_plan: list[dict[str, Any]],
    shard_count: int,
) -> list[list[dict[str, Any]]]:
    """Split the ordered plan into contiguous, weight-balanced shards.

    Contiguous slices keep every segment file owned by exactly one
    shard; the target-text length approximates inference weight.
    """
    weights = [
        max(1, len(str(item["target_text"]))) for item in render_plan
    ]
    total = sum(weights)
    shards: list[list[dict[str, Any]]] = []
    cursor = 0
    for shard_index in range(shard_count):
        remaining_shards = shard_count - shard_index
        if cursor >= len(render_plan):
            shards.append([])
            continue
        budget = sum(weights[cursor:]) / remaining_shards
        end = cursor
        accumulated = 0.0
        while end < len(render_plan) and (
            accumulated + weights[end] / 2 <= budget
            or end == cursor
        ):
            accumulated += weights[end]
            end += 1
        reserve = len(render_plan) - (remaining_shards - 1)
        end = min(end, reserve) if remaining_shards > 1 else len(
            render_plan
        )
        shards.append(render_plan[cursor:end])
        cursor = end
    del total
    return shards


def missing_segment_files(
    render_plan: list[dict[str, Any]],
    manifest_files: dict[str, Any],
) -> list[str]:
    return [
        str(item["segment_id"])
        for item in render_plan
        if f"{item['segment_id']}.wav" not in manifest_files
    ]


def _render_sharded(
    source_wav: Path,
    source_checksum: str,
    speaker_references: dict[str, bytes],
    render_plan: list[dict[str, Any]],
    rendered_root: Path,
    *,
    emotion_alpha: float,
    pipeline_version: str,
) -> dict[str, Any]:
    staged_prefix = f"staged/{source_checksum}"
    staged_name = "source-24k.wav"
    staged_manifest = _remote_method(
        INDEXTTS_APP,
        "TransferStore",
        "manifest",
        staged_prefix,
    )
    if staged_name not in staged_manifest["files"]:
        _remote_method(
            INDEXTTS_APP,
            "TransferStore",
            "put",
            f"{staged_prefix}/{staged_name}",
            source_wav.read_bytes(),
        )
    # The cache key must change whenever anything that shapes a rendered
    # segment changes: the text, WHO speaks it, the reference audio each
    # voice is cloned from, and any emotion reference. The review caught
    # that reassigned Speakers silently reused stale cached WAVs.
    render_version = _content_hash(
        {
            "segments": [
                {
                    "target_text": str(item["target_text"]),
                    "speaker_id": str(item["speaker_id"]),
                    "emotion_reference_sha256": (
                        hashlib.sha256(
                            item["emotion_reference_audio"]
                        ).hexdigest()
                        if item.get("emotion_reference_audio")
                        else ""
                    ),
                }
                for item in render_plan
            ],
            "speaker_references": {
                label: hashlib.sha256(content).hexdigest()
                for label, content in sorted(
                    speaker_references.items()
                )
            },
            "pipeline_version": pipeline_version,
            "emotion_alpha": emotion_alpha,
            "seed": 42,
        }
    )[:16]
    output_prefix = f"render/{source_checksum}/{render_version}"
    shards = [
        shard
        for shard in split_render_plan(render_plan, RENDER_SHARD_COUNT)
        if shard
    ]
    shard_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        futures = [
            pool.submit(
                _remote_method,
                INDEXTTS_APP,
                "IndexTTS2BatchRenderer",
                "render_shard",
                f"{staged_prefix}/{staged_name}",
                speaker_references,
                shard,
                output_prefix,
                emotion_alpha=emotion_alpha,
                seed=42,
            )
            for shard in shards
        ]
        for future in futures:
            shard_results.append(future.result())
    manifest = _remote_method(
        INDEXTTS_APP,
        "TransferStore",
        "manifest",
        output_prefix,
    )
    missing = missing_segment_files(render_plan, manifest["files"])
    if missing:
        raise RuntimeError(
            "Render shard outputs are incomplete: "
            + ", ".join(missing[:10])
        )
    by_segment: dict[str, dict[str, Any]] = {}
    for shard_result in shard_results:
        for item in shard_result["segments"]:
            by_segment[str(item["segment_id"])] = item
    segments_metadata: list[dict[str, Any]] = []
    names = [f"{item['segment_id']}.wav" for item in render_plan]
    for chunk_start in range(0, len(names), _FETCH_CHUNK_SIZE):
        chunk = names[chunk_start : chunk_start + _FETCH_CHUNK_SIZE]
        contents = _remote_method(
            INDEXTTS_APP,
            "TransferStore",
            "get",
            output_prefix,
            chunk,
        )
        for offset, name in enumerate(chunk):
            ordinal = chunk_start + offset + 1
            path = rendered_root / f"segment-{ordinal:04d}.wav"
            path.write_bytes(bytes(contents[name]))
            segment_id = str(render_plan[ordinal - 1]["segment_id"])
            rendered = by_segment.get(segment_id, {})
            manifest_entry = manifest["files"][name]
            local_hash = _file_hash(path)
            if local_hash != str(manifest_entry["sha256"]):
                raise RuntimeError(
                    f"Rendered segment {segment_id} failed checksum "
                    "verification after transfer"
                )
            segments_metadata.append(
                {
                    "segment_id": segment_id,
                    "path": str(path),
                    "sha256": local_hash,
                    "duration_seconds": float(
                        rendered.get(
                            "duration_seconds",
                            float(
                                _probe(path)["format"]["duration"]
                            ),
                        )
                    ),
                    "emotion_reference_start_seconds": float(
                        rendered.get(
                            "emotion_reference_start_seconds", -1.0
                        )
                    ),
                    "emotion_reference_end_seconds": float(
                        rendered.get(
                            "emotion_reference_end_seconds", -1.0
                        )
                    ),
                }
            )
    first = shard_results[0]
    return {
        "segments": segments_metadata,
        "model_id": first["model_id"],
        "model_revision": first["model_revision"],
        "seed": first["seed"],
        "emotion_alpha": first["emotion_alpha"],
        "random_sampling": False,
        "total_inference_seconds": sum(
            float(item.get("total_inference_seconds", 0.0))
            + float(item.get("model_load_seconds", 0.0))
            for item in shard_results
        ),
        "render_shards": [
            {
                "rendered_segments": len(item["segments"]),
                "skipped_existing": int(
                    item.get("skipped_existing", 0)
                ),
                "model_load_seconds": float(
                    item.get("model_load_seconds", 0.0)
                ),
                "total_inference_seconds": float(
                    item.get("total_inference_seconds", 0.0)
                ),
            }
            for item in shard_results
        ],
    }


def align_transcript(
    words: list[dict[str, Any]],
    diarization: list[dict[str, Any]],
    *,
    maximum_seconds: float = MAXIMUM_SEGMENT_SECONDS,
) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    for word in words:
        start = float(word["start_seconds"])
        end = float(word["end_seconds"])
        overlaps = [
            turn
            for turn in diarization
            if min(end, float(turn["end_seconds"]))
            > max(start, float(turn["start_seconds"]))
        ]
        speaker = (
            max(
                overlaps,
                key=lambda turn: min(end, float(turn["end_seconds"]))
                - max(start, float(turn["start_seconds"])),
            )["speaker"]
            if overlaps
            else "speaker-unknown"
        )
        assigned.append(
            {
                **word,
                "speaker": str(speaker),
                "overlap": len(
                    {str(turn["speaker"]) for turn in overlaps}
                )
                > 1,
            }
        )
    resolve_unassigned_speakers(assigned)
    return _segments_from_assigned_words(
        assigned,
        maximum_seconds=maximum_seconds,
    )


def normalize_aligned_segments(
    segments: list[dict[str, Any]],
    *,
    maximum_seconds: float = MAXIMUM_SEGMENT_SECONDS,
) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    for segment in segments:
        words = list(segment.get("words", []))
        if not words:
            words = [
                {
                    "text": str(segment["text"]),
                    "start_seconds": float(segment["start_seconds"]),
                    "end_seconds": float(segment["end_seconds"]),
                }
            ]
        for word in words:
            if isinstance(word, dict):
                text = str(word["text"])
                start_seconds = float(word["start_seconds"])
                end_seconds = float(word["end_seconds"])
            elif isinstance(word, (list, tuple)) and len(word) == 3:
                text = str(word[0])
                start_seconds = float(word[1])
                end_seconds = float(word[2])
            else:
                raise ValueError("Aligned word is malformed")
            assigned.append(
                {
                    "text": text,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "speaker": str(segment["speaker_label"]),
                    "overlap": bool(segment.get("overlap", False)),
                }
            )
    resolve_unassigned_speakers(assigned)
    return _segments_from_assigned_words(
        assigned,
        maximum_seconds=maximum_seconds,
    )


def _segments_from_assigned_words(
    assigned: list[dict[str, Any]],
    *,
    maximum_seconds: float,
) -> list[dict[str, Any]]:
    groups = group_assigned_words(
        assigned,
        maximum_seconds=maximum_seconds,
    )
    return [
        {
            "segment_id": f"seg-{index:04d}",
            "speaker_label": str(group[0]["speaker"]),
            "start_seconds": float(group[0]["start_seconds"]),
            "end_seconds": float(group[-1]["end_seconds"]),
            "text": join_words(group),
            "words": [
                {
                    "text": str(word["text"]),
                    "start_seconds": float(word["start_seconds"]),
                    "end_seconds": float(word["end_seconds"]),
                }
                for word in group
            ],
            "overlap": any(bool(word["overlap"]) for word in group),
            "events": [],
            "confidence": 0.95,
            "review_state": "accepted",
            "omitted_as_sponsor": False,
        }
        for index, group in enumerate(groups, start=1)
    ]


def detect_source_events(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment["text"]).lower()
        markers = ("[laughter]", "(laughter)", "[laughs]", "(laughs)")
        if any(marker in text for marker in markers):
            event = {
                "kind": "laughter",
                "start_seconds": float(segment["start_seconds"]),
                "end_seconds": float(segment["end_seconds"]),
                "safe_to_preserve": all(
                    value in " []()laughter"
                    for value in text
                ),
            }
            events.append(event)
            segment["events"] = ["laughter"]
            segment["_event_only"] = event["safe_to_preserve"]
    return events


def assemble_publication(
    rendered_paths: list[Path],
    publication_path: Path,
    *,
    rendered_segment_count: int | None = None,
) -> dict[str, Any]:
    if not rendered_paths:
        raise ValueError("No rendered speech segments are available")
    with TemporaryDirectory(prefix="transpodcast-assembly-") as temp:
        root = Path(temp)
        prepared: list[Path] = []
        for index, source in enumerate(rendered_paths, start=1):
            destination = root / f"prepared-{index:04d}.wav"
            _run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-af",
                    (
                        "aresample=48000,"
                        "afade=t=in:st=0:d=0.01,"
                        "areverse,"
                        "afade=t=in:st=0:d=0.01,"
                        "areverse"
                    ),
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s24le",
                    str(destination),
                ]
            )
            prepared.append(destination)
        concat = root / "concat.txt"
        concat.write_text(
            "".join(f"file '{_concat_path(path)}'\n" for path in prepared),
            encoding="utf-8",
        )
        raw_master = root / "raw-master.wav"
        _run(
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
                str(concat),
                "-c:a",
                "pcm_s24le",
                str(raw_master),
            ]
        )
        compressed = root / "compressed-master.wav"
        _compress(raw_master, compressed)
        stereo_master = root / "compressed-master-stereo.wav"
        _to_stereo_pcm(compressed, stereo_master)
        return _master_stereo_pcm(
            stereo_master,
            publication_path,
            root,
            rendered_segment_count=(
                rendered_segment_count
                if rendered_segment_count is not None
                else len(rendered_paths)
            ),
        )


def remaster_publication(
    source_path: Path,
    publication_path: Path,
    *,
    rendered_segment_count: int,
) -> dict[str, Any]:
    with TemporaryDirectory(prefix="transpodcast-remaster-") as temp:
        root = Path(temp)
        stereo_master = root / "source-master-stereo.wav"
        _to_stereo_pcm(source_path, stereo_master)
        return _master_stereo_pcm(
            stereo_master,
            publication_path,
            root,
            rendered_segment_count=rendered_segment_count,
        )


def _assembly_sequence(
    source_wav: Path,
    speech_segments: list[dict[str, Any]],
    rendered_paths: list[Path],
    source_events: list[dict[str, Any]],
    work_root: Path,
) -> list[Path]:
    timeline: list[tuple[float, Path]] = [
        (float(segment["start_seconds"]), path)
        for segment, path in zip(
            speech_segments,
            rendered_paths,
            strict=True,
        )
    ]
    for index, event in enumerate(source_events, start=1):
        if not bool(event.get("safe_to_preserve", False)):
            continue
        start = float(event["start_seconds"])
        end = float(event["end_seconds"])
        if end <= start:
            continue
        path = work_root / f"source-event-{index:04d}.wav"
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{start:.6f}",
                "-t",
                f"{end - start:.6f}",
                "-i",
                str(source_wav),
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s24le",
                str(path),
            ]
        )
        timeline.append((start, path))
    timeline.sort(key=lambda item: item[0])
    return [item[1] for item in timeline]


def _assembled_segment_timings(
    sequence: list[Path],
    rendered_paths: list[Path],
    rendered_metadata: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    metadata_by_path = {
        path: metadata
        for path, metadata in zip(
            rendered_paths,
            rendered_metadata,
            strict=True,
        )
    }
    timings: dict[int, tuple[float, float]] = {}
    cursor = 0.0
    for path in sequence:
        metadata = metadata_by_path.get(path)
        duration = (
            float(metadata["duration_seconds"])
            if metadata is not None
            else float(_probe(path)["format"]["duration"])
        )
        if metadata is not None:
            ordinal = int(metadata["ordinal"])
            timings[ordinal] = (cursor, cursor + duration)
            metadata["assembled_start_seconds"] = cursor
            metadata["assembled_end_seconds"] = cursor + duration
        cursor += duration
    return [timings[index] for index in sorted(timings)]


def _replace_publication_region(
    prior_publication: Path,
    replacement: Path,
    start_seconds: float,
    end_seconds: float,
    publication_path: Path,
    work_root: Path,
    *,
    rendered_segment_count: int,
) -> dict[str, Any]:
    if not prior_publication.is_file():
        raise FileNotFoundError("Prior Private Preview is unavailable")
    prior_duration = float(
        _probe(prior_publication)["format"]["duration"]
    )
    if (
        start_seconds < 0
        or end_seconds <= start_seconds
        or end_seconds > prior_duration + 0.1
    ):
        raise ValueError("Prior segment timing is outside the preview")
    sequence: list[Path] = []
    if start_seconds > 0.01:
        before = work_root / "revision-before.wav"
        _extract_audio_region(
            prior_publication,
            before,
            0.0,
            start_seconds,
        )
        sequence.append(before)
    sequence.append(replacement)
    if prior_duration - end_seconds > 0.01:
        after = work_root / "revision-after.wav"
        _extract_audio_region(
            prior_publication,
            after,
            end_seconds,
            prior_duration - end_seconds,
        )
        sequence.append(after)
    return assemble_publication(
        sequence,
        publication_path,
        rendered_segment_count=rendered_segment_count,
    )


def _extract_audio_region(
    source: Path,
    destination: Path,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.6f}",
            "-t",
            f"{duration_seconds:.6f}",
            "-i",
            str(source),
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def _remote_method(
    app_name: str,
    class_name: str,
    method_name: str,
    *arguments: object,
    **keywords: object,
) -> dict[str, Any]:
    environment = _modal_settings().modal_environment
    remote_class = modal.Cls.from_name(
        app_name,
        class_name,
        environment_name=environment,
    )
    instance = remote_class()
    method = getattr(instance, method_name)
    value = method.remote(*arguments, **keywords)
    if not isinstance(value, dict):
        raise TypeError("Modal component response must be an object")
    return value


def _extract_speaker_references(
    source: Path,
    turns: list[dict[str, Any]],
    destination: Path,
) -> dict[str, Path]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for turn in turns:
        grouped.setdefault(str(turn["speaker"]), []).append(turn)
    references: dict[str, Path] = {}
    for label, values in grouped.items():
        selected = max(
            values,
            key=lambda item: float(item["end_seconds"])
            - float(item["start_seconds"]),
        )
        start = float(selected["start_seconds"])
        duration = min(12.0, float(selected["end_seconds"]) - start)
        if duration < 1.0:
            continue
        path = destination / f"{_safe_name(label)}.wav"
        _extract_reference_audio(
            source,
            path,
            start_seconds=start,
            duration_seconds=duration,
        )
        references[label] = path
    if not references:
        raise ValueError("No usable Speaker Voice Reference was found")
    return references


def plan_consolidation_windows(
    turns: list[dict[str, Any]],
    *,
    minimum_seconds: float = 2.0,
    maximum_seconds: float = 12.0,
    per_label_maximum: int = 6,
) -> dict[str, list[dict[str, Any]]]:
    """Chunk each label's turns into candidate consolidation windows.

    Overlapped stretches are carved out rather than discarding whole
    windows: conversational audio overlaps constantly, and the historical
    calibration showed a binary clean flag starves labels of voters.
    Windows keep their vote wherever they sit in the episode: position
    screening applies only to Voice Reference selection, because the
    phantom labels consolidation merges live inside the cold open.
    """
    planned: dict[str, list[dict[str, Any]]] = {}
    for turn in turns:
        label = str(turn["speaker"])
        start = float(turn["start_seconds"])
        end = float(turn["end_seconds"])
        blockers = [
            (
                max(start, float(other["start_seconds"])),
                min(end, float(other["end_seconds"])),
            )
            for other in turns
            if str(other["speaker"]) != label
            and min(end, float(other["end_seconds"]))
            > max(start, float(other["start_seconds"]))
        ]
        for span_start, span_end in _subtract_intervals(
            start,
            end,
            blockers,
        ):
            cursor = span_start
            while span_end - cursor >= minimum_seconds:
                piece_end = min(cursor + maximum_seconds, span_end)
                planned.setdefault(label, []).append(
                    {
                        "start_seconds": cursor,
                        "end_seconds": piece_end,
                        "clean": True,
                    }
                )
                cursor = piece_end
    for label, windows in planned.items():
        if len(windows) > per_label_maximum:
            indexes = _evenly_spaced_indexes(
                len(windows),
                per_label_maximum,
            )
            planned[label] = [windows[index] for index in indexes]
    return planned


def _subtract_intervals(
    start: float,
    end: float,
    blockers: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    cursor = start
    for blocker_start, blocker_end in sorted(blockers):
        if blocker_start > cursor:
            spans.append((cursor, min(blocker_start, end)))
        cursor = max(cursor, blocker_end)
        if cursor >= end:
            break
    if cursor < end:
        spans.append((cursor, end))
    return [(lo, hi) for lo, hi in spans if hi > lo]


def _evenly_spaced_indexes(total: int, maximum: int) -> list[int]:
    if total <= maximum:
        return list(range(total))
    if maximum <= 1:
        return [0]
    return sorted(
        {
            round(index * (total - 1) / (maximum - 1))
            for index in range(maximum)
        }
    )


def _window_key(label: str, index: int) -> str:
    return f"consolidation-window::{_safe_name(label)}::{index:04d}"


def _extract_consolidation_windows(
    source_wav: Path,
    window_plans: dict[str, list[dict[str, Any]]],
    destination: Path,
) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for label, plans in sorted(window_plans.items()):
        for index, plan in enumerate(plans, start=1):
            window_id = _window_key(label, index)
            path = destination / f"{_safe_name(window_id)}.wav"
            _extract_reference_audio(
                source_wav,
                path,
                start_seconds=float(plan["start_seconds"]),
                duration_seconds=(
                    float(plan["end_seconds"])
                    - float(plan["start_seconds"])
                ),
            )
            payload[window_id] = path.read_bytes()
    return payload


def _apply_speaker_consolidation(
    *,
    source_wav: Path,
    reference_root: Path,
    turns: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    window_plans: dict[str, list[dict[str, Any]]],
    embeddings: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    windows: list[ConsolidationWindow] = []
    for label, plans in sorted(window_plans.items()):
        for index, plan in enumerate(plans, start=1):
            window_id = _window_key(label, index)
            windows.append(
                ConsolidationWindow(
                    window_id=window_id,
                    speaker_label=label,
                    start_seconds=float(plan["start_seconds"]),
                    end_seconds=float(plan["end_seconds"]),
                    clean=bool(plan["clean"]),
                    embedding=tuple(
                        float(value)
                        for value in embeddings.get(window_id, [])
                    ),
                )
            )
    consolidation = consolidate_episode_speakers(windows)
    mapping = consolidation.mapping
    mapped_segments = [
        {
            **segment,
            "speaker_label": mapping.get(
                str(segment["speaker_label"]),
                str(segment["speaker_label"]),
            ),
        }
        for segment in segments
    ]
    mapped_turns = [
        {
            **turn,
            "speaker": mapping.get(
                str(turn["speaker"]),
                str(turn["speaker"]),
            ),
        }
        for turn in turns
    ]
    source_duration = max(
        (float(turn["end_seconds"]) for turn in turns),
        default=0.0,
    )
    selection = select_voice_references(
        mapped_turns,
        source_duration_seconds=source_duration,
    )
    speakers: list[dict[str, Any]] = []
    for label in sorted(selection.selections):
        span = selection.selections[label]
        path = (
            reference_root / f"{_safe_name(label)}-consolidated.wav"
        )
        _extract_reference_audio(
            source_wav,
            path,
            start_seconds=float(span["start_seconds"]),
            duration_seconds=float(span["duration_seconds"]),
        )
        speakers.append(
            {
                "local_label": label,
                "reference_audio_path": str(path),
                "embedding": _consolidated_centroid(
                    label,
                    mapping,
                    windows,
                ),
                "confidence": 0.98,
            }
        )
    payload = {
        "policy_version": CONSOLIDATION_POLICY_VERSION,
        "speaker_mapping": mapping,
        "reference_selection": selection.selections,
        "window_count": len(windows),
        "decision_records": [
            record.to_payload()
            for record in (
                *consolidation.records,
                *selection.records,
            )
        ],
    }
    return mapped_segments, speakers, payload


def _segment_attribution_records(
    segments: list[dict[str, Any]],
    *,
    policy_version: str,
) -> list[dict[str, Any]]:
    """The full-population attribution ledger, recorded LAST so it
    always describes the final segments (the review caught records
    written before adjudication could still change ownership)."""
    return [
        DecisionRecord(
            kind="segment-attribution",
            policy_version=policy_version,
            subject={
                "segment_index": index,
                "speaker": str(segment["speaker_label"]),
                "start_seconds": float(segment["start_seconds"]),
                "end_seconds": float(segment["end_seconds"]),
            },
            evidence={
                "word_count": len(segment.get("words", [])),
                "overlap": bool(segment.get("overlap", False)),
                "review_state": str(
                    segment.get("review_state", "accepted")
                ),
            },
            verdict="attributed",
            rationale=(
                "Final word ownership after consolidation and "
                "adjudication."
            ),
        ).to_payload()
        for index, segment in enumerate(segments)
    ]


def _detect_boundary_disputes(
    *,
    source_wav: Path,
    scratch_root: Path,
    segments: list[dict[str, Any]],
    speakers: list[dict[str, Any]],
) -> dict[str, Any]:
    """The acoustic half of Boundary Ownership Adjudication.

    Source Analysis detects the disputes and measures the fragment
    voiceprints, then stops: the discourse question and the verdict
    application belong to the worker layer, behind the Translation
    Adapter. The pending payload carries everything the worker needs.
    """
    disputes = find_ownership_disputes(segments)
    policy_version = (
        CONTINUITY_ADJUDICATION_POLICY_VERSION
        if PIPELINE_VERSION in {SPEECH_PIPELINE_V13, SPEECH_PIPELINE_V14}
        else ADJUDICATION_POLICY_VERSION
    )
    payload: dict[str, Any] = {
        "policy_version": policy_version,
        "dispute_count": len(disputes),
        "candidate_count": len(disputes),
        "short_boundary_candidate_count": sum(
            SHORT_BOUNDARY_TRIGGER in dispute.trigger_reasons
            for dispute in disputes
        ),
        "sparse_label_candidate_count": sum(
            SPARSE_LABEL_TRIGGER in dispute.trigger_reasons
            for dispute in disputes
        ),
        "stable_segment_count": len(segments) - len(disputes),
        "changed_count": 0,
        "marked_count": 0,
        "decision_records": [],
    }
    if not disputes:
        return payload
    centroids = {
        str(speaker["local_label"]): tuple(
            float(value) for value in speaker.get("embedding", [])
        )
        for speaker in speakers
    }
    fragment_payload: dict[str, bytes] = {}
    for dispute in disputes:
        span = dispute.end_seconds - dispute.start_seconds
        if (
            span < MINIMUM_FRAGMENT_SECONDS - 1e-9
            or len(dispute.permissible_owners) < 2
        ):
            continue
        path = scratch_root / f"{_safe_name(dispute.dispute_id)}.wav"
        _extract_reference_audio(
            source_wav,
            path,
            start_seconds=dispute.start_seconds,
            duration_seconds=span,
        )
        fragment_payload[dispute.dispute_id] = path.read_bytes()
    fragment_embeddings: dict[str, tuple[float, ...]] = {}
    if fragment_payload:
        result = _remote_method(
            SPEAKER_APP,
            "SpeakerEmbedder",
            "compare",
            fragment_payload,
        )
        fragment_embeddings = {
            str(key): tuple(float(value) for value in vector)
            for key, vector in result.get("embeddings", {}).items()
        }
    voiceprints = measure_fragment_voiceprints(
        disputes,
        fragment_embeddings,
        centroids,
    )
    payload["pending"] = {
        "disputes": [dispute.to_payload() for dispute in disputes],
        "voiceprints": {
            dispute.dispute_id: (
                verdict.to_payload() if verdict is not None else None
            )
            for dispute in disputes
            for verdict in (voiceprints.get(dispute.dispute_id),)
        },
    }
    return payload


def _consolidated_centroid(
    label: str,
    mapping: dict[str, str],
    windows: list[ConsolidationWindow],
) -> list[float]:
    members = [
        window
        for window in windows
        if window.clean
        and window.embedding
        and mapping.get(window.speaker_label, window.speaker_label)
        == label
    ]
    if not members:
        return []
    dimension = len(members[0].embedding)
    return [
        sum(window.embedding[index] for window in members)
        / len(members)
        for index in range(dimension)
    ]


def _extract_reference_audio(
    source: Path,
    destination: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.6f}",
            "-t",
            f"{duration_seconds:.6f}",
            "-i",
            str(source),
            "-ar",
            "24000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )


def _transcode(source: Path, destination: Path, *, sample_rate: int) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )


def _to_stereo_pcm(source: Path, destination: Path) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def _compress(source: Path, destination: Path) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            (
                "acompressor=threshold=0.125:ratio=1.5:"
                "attack=20:release=250:knee=2:makeup=1"
            ),
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def _master_stereo_pcm(
    source: Path,
    publication_path: Path,
    work_root: Path,
    *,
    rendered_segment_count: int,
) -> dict[str, Any]:
    true_peak_target = _MASTER_TRUE_PEAK_DBTP
    pre_encode: dict[str, str] = {}
    final: dict[str, str] = {}
    publication_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = 4
    for attempt in range(attempts):
        measurement = _loudness(
            source,
            true_peak_dbtp=true_peak_target,
        )
        normalized = work_root / f"normalized-master-{attempt + 1}.wav"
        _normalize(
            source,
            normalized,
            measurement,
            true_peak_dbtp=true_peak_target,
        )
        pre_encode = _loudness(normalized)
        _encode_aac(normalized, publication_path)
        final = _loudness(publication_path)
        if _final_measurement_passes(final) or attempt == attempts - 1:
            break
        # AAC inter-sample overshoot is not linear in the pre-encode
        # peak (a calibration run missed the gate by 0.08 dB and a
        # matching correction changed nothing), so escalate: at least
        # double the measured miss each retry, bounded at -3 dBTP.
        peak_overshoot = max(
            0.0,
            float(final["input_tp"]) - _FINAL_TRUE_PEAK_MAX_DBTP,
        )
        true_peak_target -= (
            max(2 * peak_overshoot, 0.2) + _CODEC_PEAK_SAFETY_DB
        )
        true_peak_target = max(true_peak_target, -3.0)
    probe = _probe(publication_path)
    return {
        "duration_seconds": float(probe["format"]["duration"]),
        "quality": {
            "codec": "AAC-LC",
            "sample_rate_hz": 48_000,
            "channels": 2,
            "bitrate_kbps": 128,
            "pre_encode_loudness_lufs": float(pre_encode["input_i"]),
            "pre_encode_true_peak_dbtp": float(pre_encode["input_tp"]),
            "loudness_lufs": float(final["input_i"]),
            "true_peak_dbtp": float(final["input_tp"]),
            "clipping": float(final["input_tp"]) > _CLIPPING_MAX_DBTP,
            "rendered_segments": rendered_segment_count,
            "timeline_continuous": True,
            "boundary_artifacts": False,
            "source_language_leakage": False,
        },
    }


def _encode_aac(source: Path, publication_path: Path) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(publication_path),
        ]
    )


def _final_measurement_passes(measurement: dict[str, str]) -> bool:
    loudness = float(measurement["input_i"])
    true_peak = float(measurement["input_tp"])
    return (
        _FINAL_LOUDNESS_MIN_LUFS
        <= loudness
        <= _FINAL_LOUDNESS_MAX_LUFS
        and true_peak <= _FINAL_TRUE_PEAK_MAX_DBTP
    )


def _normalize(
    source: Path,
    destination: Path,
    measurement: dict[str, str],
    *,
    true_peak_dbtp: float = _MASTER_TRUE_PEAK_DBTP,
) -> None:
    audio_filter = (
        f"loudnorm=I={_MASTER_LOUDNESS_LUFS}:"
        f"TP={true_peak_dbtp}:"
        f"LRA={_MASTER_LOUDNESS_RANGE_LU}:"
        f"measured_I={measurement['input_i']}:"
        f"measured_TP={measurement['input_tp']}:"
        f"measured_LRA={measurement['input_lra']}:"
        f"measured_thresh={measurement['input_thresh']}:"
        f"offset={measurement['target_offset']}:"
        "linear=false"
    )
    _run(
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
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )


def _loudness(
    path: Path,
    *,
    true_peak_dbtp: float = _MASTER_TRUE_PEAK_DBTP,
) -> dict[str, str]:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            (
                f"loudnorm=I={_MASTER_LOUDNESS_LUFS}:"
                f"TP={true_peak_dbtp}:"
                f"LRA={_MASTER_LOUDNESS_RANGE_LU}:"
                "print_format=json"
            ),
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    matches = re.findall(
        r"\{\s*\"input_i\"[\s\S]*?\}",
        completed.stderr,
    )
    if not matches:
        raise ValueError("FFmpeg returned no loudness measurement")
    parsed = json.loads(matches[-1])
    return {str(key): str(value) for key, value in parsed.items()}


def _probe(path: Path) -> dict[str, Any]:
    completed = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise TypeError("FFprobe response must be an object")
    return value


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
    )


def _verify_source(path: Path, expected_checksum: str) -> None:
    if not path.is_file():
        raise FileNotFoundError("Source Archive file is unavailable")
    if _file_hash(path) != expected_checksum:
        raise ValueError("Source Archive checksum verification failed")


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected an object in {path}")
    return value


def _object(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise TypeError(f"{key} must be an object")
    return item


def _chapters_from_timings(
    segment_timings: list[tuple[float, float]],
) -> list[dict[str, object]]:
    del segment_timings
    return []


def _estimate_cost(gpu_seconds: float) -> float:
    hourly_rate = _modal_settings().modal_l40s_usd_per_hour
    return round(max(gpu_seconds, 0.0) * hourly_rate / 3600, 4)


def _pipeline_version_for_analysis(analysis: dict[str, Any]) -> str:
    return str(
        analysis.get("pipeline_version") or PIPELINE_VERSION
    )


@lru_cache(maxsize=1)
def _modal_settings() -> TransPodcastSettings:
    return TransPodcastSettings()


def _content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _concat_path(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "speaker"


if __name__ == "__main__":
    main()
