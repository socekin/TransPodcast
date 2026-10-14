from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import time
import wave
from array import array
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
from transpodcast.content_editing import (
    MAXIMUM_RENDER_UNIT_CHARACTERS,
    RENDER_PLAN_SCHEMA_VERSION,
    RenderPlanUnit,
)
from transpodcast.decision_records import DecisionRecord
from transpodcast.deterministic_ownership import (
    DETERMINISTIC_OWNERSHIP_POLICY,
    DeterministicOwnershipPolicy,
    apply_deterministic_ownership,
)
from transpodcast.indextts25_text import (
    INDEXTTS25_PRONUNCIATION_POLICY_VERSION,
)
from transpodcast.listening_audit import LISTENING_AUDIT_POLICY_VERSION
from transpodcast.listening_audit_runner import (
    AUDIT_LANGUAGE,
    AuditUnit,
    ListeningAuditSession,
)
from transpodcast.pipeline_versions import (
    CURRENT_SPEECH_PIPELINE_VERSION,
    RHYTHM_PIPELINES,
    SPEECH_MODEL_INDEXTTS25,
    SPEECH_PIPELINE_V13,
    SPEECH_PIPELINE_V14,
    SPEECH_PIPELINE_V15,
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V21,
    WORD_OWNERSHIP_PIPELINES,
    speech_model_for_pipeline_version,
)
from transpodcast.rhythm import (
    RHYTHM_POLICY,
    RhythmPolicy,
    apply_fades,
    cap_internal_silences,
    classify_boundary,
    inserted_silence_seconds,
    trim_window,
)
from transpodcast.similarity import cosine_similarity
from transpodcast.source_segmentation import (
    MAXIMUM_SEGMENT_SECONDS,
    group_assigned_words,
    join_words,
)
from transpodcast.span_identity import (
    DENSE_VOICEPRINT_POLICY,
    SPAN_IDENTITY_POLICY,
    MeasuredWindow,
    PlannedWindow,
    apply_speaker_identity,
    plan_speaker_identity,
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
from transpodcast.unit_clip_store import (
    UnitClipEntry,
    UnitClipManifestReference,
    UnitClipStore,
)
from transpodcast.voice_references import (
    EMOTION_REFERENCE_POLICY_VERSION,
    VOICE_REFERENCE_POLICY_VERSION,
    apply_emotion_references,
    reselect_for_content_edits,
)

# V12 keeps the accepted V11/V10/V9/v6 listening baseline while upgrading the
# pinned diarizer. The rejected v7/v8 experiments remain research history; see
# ADR-0029 and the V12 release record.
PIPELINE_VERSION = SPEECH_PIPELINE_V15
# Source Analysis versions this command accepts. V17 and V21 add continuity
# evidence; only V21 owns words with the deterministic ownership policy.
SOURCE_ANALYSIS_VERSIONS = frozenset(
    {SPEECH_PIPELINE_V15, SPEECH_PIPELINE_V17, SPEECH_PIPELINE_V21}
)
_CONTINUITY_ANALYSIS_VERSIONS = frozenset(
    {SPEECH_PIPELINE_V17, SPEECH_PIPELINE_V21}
)
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
INDEXTTS2_CLASS = "IndexTTS2BatchRenderer"
INDEXTTS25_CLASS = "IndexTTS25BatchRenderer"
# V19/V20 hash only the inputs that shape a rendered unit; Rhythm Policy and
# Render Plan pause fields are assembly-only (Design Principle 5).
RHYTHM_RENDER_IDENTITY_VERSION = "tts-inputs-v1"
_RENDER_SEED = 42
# D3: a unit with a hard Listening Audit finding is re-rendered once with
# this seed. The seed is fixed so a reprocessed run reproduces the retry.
LISTENING_AUDIT_RETRY_SEED = 43
QWEN_ASR_CLASS = "Qwen3Transcriber"
SPEAKER_CLASS = "SpeakerEmbedder"
LISTENING_AUDIT_METHODS = (
    (QWEN_ASR_APP, QWEN_ASR_CLASS, "transcribe_clips"),
    (SPEAKER_APP, SPEAKER_CLASS, "compare"),
)
# The encoded file can end slightly after the PCM timeline (AAC priming).
_TIMELINE_END_TOLERANCE_SECONDS = 0.5
_ASSEMBLY_SAMPLE_RATE = 48_000
_TEMPO_PADDING_SECONDS = 0.1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the versioned TransPodcast Modal speech pipeline."
    )
    parser.add_argument(
        "--operation",
        required=True,
        choices=("analyze", "ownership-evidence", "render", "remaster", "readiness"),
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
        elif arguments.operation == "ownership-evidence":
            response = measure_ownership_evidence(request, arguments.output.parent)
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
        (QWEN_ASR_APP, QWEN_ASR_CLASS),
        (SPEAKER_APP, SPEAKER_CLASS),
        (INDEXTTS_APP, INDEXTTS2_CLASS),
        (INDEXTTS_APP, INDEXTTS25_CLASS),
    )
    classes: dict[tuple[str, str], Any] = {}
    for app_name, class_name in components:
        remote_class = modal.Cls.from_name(
            app_name,
            class_name,
            environment_name=environment,
        )
        remote_class.hydrate()
        classes[(app_name, class_name)] = remote_class
    return {
        "ready": True,
        "environment": environment,
        "pipeline_version": CURRENT_SPEECH_PIPELINE_VERSION,
        "components": [
            {"app": app_name, "class": class_name}
            for app_name, class_name in components
        ],
        "listening_audit": listening_audit_readiness(classes),
    }


def listening_audit_readiness(
    classes: Mapping[tuple[str, str], Any],
) -> dict[str, Any]:
    """Report whether the deployed apps expose the audit methods.

    The hydrated class metadata lists the deployed methods, so this needs no
    container. A missing method only disables the Listening Audit: renders
    then record ``audit_unavailable`` and pipeline readiness is unaffected.
    """
    missing: list[str] = []
    for app_name, class_name, method_name in LISTENING_AUDIT_METHODS:
        remote_class = classes.get((app_name, class_name))
        try:
            if remote_class is None:
                raise LookupError(class_name)
            getattr(remote_class(), method_name)
        except (LookupError, modal.exception.NotFoundError):
            missing.append(f"{class_name}.{method_name}")
    return {
        "available": not missing,
        "policy_version": LISTENING_AUDIT_POLICY_VERSION,
        "missing_methods": missing,
    }


def analyze(request: dict[str, Any], output_root: Path) -> dict[str, Any]:
    analysis_version = str(
        request.get("analysis", {}).get("pipeline_version", PIPELINE_VERSION)
    )
    if analysis_version not in SOURCE_ANALYSIS_VERSIONS:
        raise ValueError("Unsupported Source Analysis version")
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
        deterministic_ownership: dict[str, Any] | None = None
        if analysis_version == SPEECH_PIPELINE_V21:
            segments, deterministic_ownership = (
                align_transcript_deterministically(
                    list(transcription["words"]),
                    turns,
                )
            )
        else:
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
        identity: SpeakerIdentityCalibration | None = None
        if analysis_version == SPEECH_PIPELINE_V21:
            # Sparse-label merge and the Span Identity Check correct owners
            # before any risk region is built, so the Agent layer sees them.
            identity = _calibrate_speaker_identity(
                source_wav=source_wav,
                segments=segments,
                speakers=speakers,
                turns=turns,
                consolidation=speaker_consolidation,
                source_sha256=str(source["checksum"]),
            )
            segments = identity.segments
            speakers = identity.speakers
            speaker_consolidation = identity.consolidation
        boundary_adjudication = _detect_boundary_disputes(
            source_wav=source_wav,
            scratch_root=root,
            segments=segments,
            speakers=speakers,
        )
        if deterministic_ownership is not None:
            boundary_adjudication["deterministic_ownership"] = (
                deterministic_ownership
            )
        identity_seconds = 0.0
        if identity is not None:
            boundary_adjudication["dense_voiceprints"] = identity.dense_evidence
            boundary_adjudication["span_identity"] = identity.span_identity
            identity_seconds = float(identity.dense_evidence["inference_seconds"])
        source_events = detect_source_events(segments)
        segments = [
            segment
            for segment in segments
            if not bool(segment.pop("_event_only", False))
        ]
        continuity_seconds = 0.0
        if analysis_version in _CONTINUITY_ANALYSIS_VERSIONS:
            evidence = _measure_continuity_coverage(
                source_wav, root, segments, speakers, turns,
                speaker_consolidation, str(transcription.get("text", "")),
                boundary_adjudication, str(source["checksum"]),
            )
            boundary_adjudication["continuity_evidence"] = evidence
            continuity_seconds = float(evidence["inference_seconds"])
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
        "pipeline_version": analysis_version,
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
            ) + continuity_seconds + identity_seconds,
        },
        "checksum": _content_hash(canonical),
        "estimated_cost_usd": _estimate_cost(
            float(diarization.get("inference_seconds", 0))
            + float(transcription.get("inference_seconds", 0))
            + float(speaker_result.get("inference_seconds", 0))
            + continuity_seconds
            + identity_seconds
        ),
        "elapsed_seconds": time.perf_counter() - started,
    }


def measure_ownership_evidence(
    request: dict[str, Any], output_root: Path
) -> dict[str, Any]:
    """Measure only a requested follow-up scope; never run ASR or diarization."""
    source = _object(request, "source")
    source_path = Path(str(source["audio_path"]))
    _verify_source(source_path, str(source["checksum"]))
    data = _object(request, "ownership")
    with TemporaryDirectory(prefix="transpodcast-ownership-evidence-") as temporary:
        root = Path(temporary)
        wave = root / "source.wav"
        _transcode(source_path, wave, sample_rate=16_000)
        result = _measure_continuity_coverage(
            wave,
            root,
            data["segments"],
            data["speakers"],
            data["turns"],
            data["consolidation"],
            data["full_asr_text"],
            data["boundary"],
            str(source["checksum"]),
            [(int(s), int(e)) for s, e in data["ranges"]],
        )
    result["estimated_cost_usd"] = _estimate_cost(result["inference_seconds"])
    return result


def _measure_continuity_coverage(
    source_wav: Path,
    root: Path,
    segments: list[dict[str, Any]],
    speakers: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    consolidation: dict[str, Any],
    full_asr_text: str,
    boundary: dict[str, Any],
    source_sha256: str,
    ranges: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    from transpodcast.continuity_preparation import (
        prepare_continuity_regions,
    )
    from transpodcast.domain import CanonicalSegment
    from transpodcast.ownership_acoustic_coverage import (
        plan_ownership_acoustic_windows,
        project_acoustic_word_evidence,
    )
    from transpodcast.similarity import cosine_similarity

    canonical = tuple(
        CanonicalSegment(
            str(s["segment_id"]),
            str(s["speaker_label"]),
            float(s["start_seconds"]),
            float(s["end_seconds"]),
            str(s["text"]),
            words=tuple(
                (str(w["text"]), float(w["start_seconds"]), float(w["end_seconds"]))
                for w in s["words"]
            ),
        )
        for s in segments
    )
    raw_turns = tuple(
        (str(t["speaker"]), float(t["start_seconds"]), float(t["end_seconds"]))
        for t in turns
    )
    mapping = dict(consolidation.get("speaker_mapping", {}))
    regions = prepare_continuity_regions(
        canonical,
        raw_turns,
        mapping,
        full_asr_text,
        boundary.get("pending", {}),
        ranges=ranges,
    )
    words = {w.word_index: w for r in regions for w in r.words}
    windows = plan_ownership_acoustic_windows(
        regions, raw_turns, mapping, source_sha256
    )
    anchors = {str(s["local_label"]): tuple(s["embedding"]) for s in speakers}
    prior = boundary.get("continuity_evidence", {})
    cached = {}
    if (
        prior.get("source_sha256") == source_sha256
        and prior.get("anchors_sha256") == _content_hash(anchors)
        and prior.get("model_id") == "nvidia/speakerverification_en_titanet_large"
        # pragma: allowlist nextline secret
        and prior.get("model_revision") == "0dc382f40121a5fbd34db10a2bb04d826c2be6a8"
    ):
        cached = {m["window_id"]: m for m in prior.get("measurements", [])}
    evidence: list[dict[str, Any]] = []
    measurements: list[dict[str, Any]] = []
    errors: list[str] = []
    inference_seconds = 0.0
    for offset in range(0, len(windows), 64):
        chunk = windows[offset : offset + 64]
        audio_files = {}
        try:
            for window in chunk:
                if window.name in cached:
                    continue
                path = root / f"{window.name}.wav"
                _extract_reference_audio(
                    source_wav,
                    path,
                    start_seconds=window.start_seconds,
                    duration_seconds=window.end_seconds - window.start_seconds,
                )
                audio_files[window.name] = path.read_bytes()
            measured: dict[str, Any] = {"embeddings": {}, "inference_seconds": 0.0}
            if audio_files:
                measured = _remote_method(
                    SPEAKER_APP, "SpeakerEmbedder", "compare", audio_files
                )
                if (
                    measured.get("model_id")
                    != "nvidia/speakerverification_en_titanet_large"
                    or measured.get("model_revision")
                    # pragma: allowlist nextline secret
                    != "0dc382f40121a5fbd34db10a2bb04d826c2be6a8"
                ):
                    raise ValueError("Continuity voiceprint model identity mismatch")
                inference_seconds += float(measured.get("inference_seconds", 0.0))
            for window in chunk:
                if window.name in cached:
                    scores = cached[window.name]["scores"]
                else:
                    vector = tuple(measured["embeddings"][window.name])
                    scores = {
                        s: cosine_similarity(vector, v) for s, v in anchors.items()
                    }
                projected = project_acoustic_word_evidence(
                    window,
                    scores,
                    tuple(
                        words[i]
                        for i in range(
                            window.first_word_index, window.last_word_index + 1
                        )
                    ),
                )
                evidence.extend(v.to_payload() for v in projected)
                measurements.append(
                    {
                        "window_id": window.name,
                        "start_seconds": window.start_seconds,
                        "end_seconds": window.end_seconds,
                        "scores": scores,
                        "overlap_fraction": window.overlap_fraction,
                        "accepted_spans": len(projected),
                    }
                )
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}"[:500])
            break
    return {
        "source_sha256": source_sha256,
        "anchors_sha256": _content_hash(anchors),
        "model_id": "nvidia/speakerverification_en_titanet_large",
        # pragma: allowlist nextline secret
        "model_revision": "0dc382f40121a5fbd34db10a2bb04d826c2be6a8",
        "window_count": len(windows),
        "measured_window_count": len(measurements),
        "inference_seconds": inference_seconds,
        "measurements": measurements,
        "voiceprints": evidence,
        "errors": errors,
    }


_TITANET_MODEL_ID = "nvidia/speakerverification_en_titanet_large"
# pragma: allowlist nextline secret
_TITANET_MODEL_REVISION = "0dc382f40121a5fbd34db10a2bb04d826c2be6a8"
_DENSE_CHUNK_SIZE = 64


@dataclass(frozen=True)
class SpeakerIdentityCalibration:
    """V21 owners after the sparse-label merge and Span Identity Check."""

    segments: list[dict[str, Any]]
    speakers: list[dict[str, Any]]
    consolidation: dict[str, Any]
    dense_evidence: dict[str, Any]
    span_identity: dict[str, Any]


def _calibrate_speaker_identity(
    *,
    source_wav: Path,
    segments: list[dict[str, Any]],
    speakers: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    consolidation: dict[str, Any],
    source_sha256: str,
) -> SpeakerIdentityCalibration:
    """Measure dense voiceprints, merge sparse labels, check span identity.

    One embedding pass measures every dense window plus the turns of each
    sparse label without an anchor embedding. Any measurement error leaves
    every owner, label, and Voice Reference unchanged.
    """
    mapping = {
        str(key): str(value)
        for key, value in consolidation.get("speaker_mapping", {}).items()
    }
    spans = [
        (float(word["start_seconds"]), float(word["end_seconds"]))
        for segment in segments
        for word in segment["words"]
    ]
    owners = [
        str(segment["speaker_label"])
        for segment in segments
        for _word in segment["words"]
    ]
    anchors = {
        str(speaker["local_label"]): tuple(
            float(value) for value in speaker.get("embedding", [])
        )
        for speaker in speakers
        if speaker.get("embedding")
    }
    plan = plan_speaker_identity(
        spans,
        owners,
        [
            (
                mapping.get(str(turn["speaker"]), str(turn["speaker"])),
                float(turn["start_seconds"]),
                float(turn["end_seconds"]),
            )
            for turn in turns
        ],
        set(anchors),
    )
    evidence = _measure_dense_voiceprints(
        source_wav,
        plan.windows,
        tuple(window for group in plan.fragments.values() for window in group),
        anchors,
        source_sha256,
    )
    if evidence["errors"]:
        return SpeakerIdentityCalibration(
            segments,
            speakers,
            consolidation,
            evidence,
            {
                "policy_version": SPAN_IDENTITY_POLICY.version,
                "policy": SPAN_IDENTITY_POLICY.to_payload(),
                "checked_run_count": 0,
                "relabelled_run_count": 0,
                "relabelled_word_count": 0,
                "skipped": "Dense voiceprint measurement failed",
            },
        )
    fragments = {str(item["window_id"]): item for item in evidence["fragments"]}
    outcome = apply_speaker_identity(
        plan,
        spans,
        owners,
        [_measured_window(item) for item in evidence["measurements"]],
        {
            label: [
                _measured_window(fragments[window.window_id])
                for window in group
                if window.window_id in fragments
            ]
            for label, group in plan.fragments.items()
        },
        {
            item.label: {
                speaker: cosine_similarity(anchors[item.label], anchors[speaker])
                for speaker in plan.established
            }
            for item in plan.sparse
            if item.label in anchors
        },
    )
    for raw in {str(turn["speaker"]) for turn in turns}:
        target = outcome.merges.get(mapping.get(raw, raw))
        if target is not None:
            mapping[raw] = target
    calibrated = dict(consolidation)
    calibrated["speaker_mapping"] = mapping
    calibrated["reference_selection"] = {
        label: span
        for label, span in dict(consolidation.get("reference_selection", {})).items()
        if label not in outcome.merges
    }
    calibrated["sparse_label_merge"] = outcome.sparse_summary()
    calibrated["decision_records"] = [
        *consolidation.get("decision_records", []),
        *outcome.decision_records(),
    ]
    return SpeakerIdentityCalibration(
        _relabel_segments(segments, list(outcome.owners), turns),
        [s for s in speakers if str(s["local_label"]) not in outcome.merges],
        calibrated,
        evidence,
        outcome.span_check.summary_payload(),
    )


def _measured_window(item: Mapping[str, Any]) -> MeasuredWindow:
    return MeasuredWindow(
        str(item["window_id"]),
        float(item["start_seconds"]),
        float(item["end_seconds"]),
        tuple(sorted((str(k), float(v)) for k, v in item["scores"].items())),
    )


def _measure_dense_voiceprints(
    source_wav: Path,
    windows: Sequence[PlannedWindow],
    fragments: Sequence[PlannedWindow],
    anchors: Mapping[str, tuple[float, ...]],
    source_sha256: str,
    *,
    prior: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score dense windows and sparse-label fragments against the anchors.

    Uses the deployed ``SpeakerEmbedder.compare`` in 64-clip chunks, like the
    continuity coverage. Measurements are bound to the source, model, anchor,
    and window policy identity; a matching ``prior`` payload is reused by
    window ID, so a resumed measurement never re-bills finished windows.
    Clips are cut from the 16 kHz analysis waveform, the rate of the
    calibration evidence.
    """
    anchors_sha256 = _content_hash({k: list(v) for k, v in anchors.items()})
    cached: dict[str, dict[str, Any]] = {}
    if (
        prior
        and prior.get("source_sha256") == source_sha256
        and prior.get("anchors_sha256") == anchors_sha256
        and prior.get("model_id") == _TITANET_MODEL_ID
        and prior.get("model_revision") == _TITANET_MODEL_REVISION
        and prior.get("policy_version") == DENSE_VOICEPRINT_POLICY.version
    ):
        cached = {
            str(item["window_id"]): dict(item)
            for item in (*prior.get("measurements", []), *prior.get("fragments", []))
        }
    planned = [*windows, *fragments]
    fragment_ids = {window.window_id for window in fragments}
    results: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    inference_seconds = 0.0
    pending = [window for window in planned if window.window_id not in cached]
    try:
        for offset in range(0, len(pending), _DENSE_CHUNK_SIZE):
            chunk = pending[offset : offset + _DENSE_CHUNK_SIZE]
            measured = _remote_method(
                SPEAKER_APP,
                "SpeakerEmbedder",
                "compare",
                _dense_clip_audio(source_wav, chunk),
            )
            if (
                measured.get("model_id") != _TITANET_MODEL_ID
                or measured.get("model_revision") != _TITANET_MODEL_REVISION
            ):
                raise ValueError("Dense voiceprint model identity mismatch")
            inference_seconds += float(measured.get("inference_seconds", 0.0))
            for window in chunk:
                vector = tuple(
                    float(value) for value in measured["embeddings"][window.window_id]
                )
                results[window.window_id] = {
                    "window_id": window.window_id,
                    "start_seconds": window.start_seconds,
                    "end_seconds": window.end_seconds,
                    "scores": {
                        label: round(cosine_similarity(vector, anchor), 6)
                        for label, anchor in sorted(anchors.items())
                    },
                }
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}"[:500])
    rows = [
        cached.get(window.window_id) or results[window.window_id]
        for window in planned
        if window.window_id in cached or window.window_id in results
    ]
    return {
        "policy_version": DENSE_VOICEPRINT_POLICY.version,
        "policy": DENSE_VOICEPRINT_POLICY.to_payload(),
        "source_sha256": source_sha256,
        "anchors_sha256": anchors_sha256,
        "model_id": _TITANET_MODEL_ID,
        "model_revision": _TITANET_MODEL_REVISION,
        "window_count": len(windows),
        "fragment_count": len(fragments),
        "measured_window_count": len(rows),
        "resumed_window_count": sum(w.window_id in cached for w in planned),
        "inference_seconds": inference_seconds,
        "measurements": [r for r in rows if r["window_id"] not in fragment_ids],
        "fragments": [r for r in rows if r["window_id"] in fragment_ids],
        "errors": errors,
    }


def _dense_clip_audio(
    source_wav: Path, windows: Sequence[PlannedWindow]
) -> dict[str, bytes]:
    """WAV clips of the analysis waveform, keyed by window ID."""
    with wave.open(str(source_wav), "rb") as handle:
        rate = handle.getframerate()
        width = handle.getsampwidth() * handle.getnchannels()
        parameters = handle.getparams()
        frames = handle.readframes(handle.getnframes())
    clips: dict[str, bytes] = {}
    for window in windows:
        first = max(0, int(window.start_seconds * rate)) * width
        last = max(0, int(window.end_seconds * rate)) * width
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as clip:
            clip.setparams(parameters)
            clip.writeframes(frames[first:last])
        clips[window.window_id] = buffer.getvalue()
    return clips


def _relabel_segments(
    segments: list[dict[str, Any]],
    owners: list[str],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply per-word owners; split a segment only where its owner changes."""
    flat = [str(s["speaker_label"]) for s in segments for _ in s["words"]]
    if flat == owners:
        return segments
    raw = [
        (str(t["speaker"]), float(t["start_seconds"]), float(t["end_seconds"]))
        for t in turns
    ]
    result: list[dict[str, Any]] = []
    cursor = 0
    for segment in segments:
        words = list(segment["words"])
        labels = owners[cursor : cursor + len(words)]
        cursor += len(words)
        pieces: list[tuple[str, list[dict[str, Any]]]] = []
        for word, label in zip(words, labels, strict=True):
            if pieces and pieces[-1][0] == label:
                pieces[-1][1].append(word)
            else:
                pieces.append((label, [word]))
        if len(pieces) <= 1:
            label = pieces[0][0] if pieces else str(segment["speaker_label"])
            result.append({**segment, "speaker_label": label})
            continue
        for label, group in pieces:
            result.append(
                {
                    **segment,
                    "speaker_label": label,
                    "start_seconds": float(group[0]["start_seconds"]),
                    "end_seconds": float(group[-1]["end_seconds"]),
                    "text": join_words(group),
                    "words": group,
                    "overlap": any(
                        len(
                            {
                                speaker
                                for speaker, start, end in raw
                                if min(float(w["end_seconds"]), end)
                                > max(float(w["start_seconds"]), start)
                            }
                        )
                        > 1
                        for w in group
                    ),
                }
            )
    if len(result) != len(segments):
        result = [
            {**segment, "segment_id": f"seg-{index:04d}"}
            for index, segment in enumerate(result, start=1)
        ]
    return result


def _render_units_by_segment(
    analysis: dict[str, Any],
    segments: list[dict[str, Any]],
    target_segments: list[str],
) -> list[dict[str, Any]]:
    payload = analysis.get("render_plan")
    if not payload:
        return [
            RenderPlanUnit(
                unit_id=str(segment["segment_id"]),
                parent_segment_id=str(segment["segment_id"]),
                source_segment_id=str(segment["segment_id"]),
                source_start_seconds=float(segment["start_seconds"]),
                source_end_seconds=float(segment["end_seconds"]),
                pause_before_seconds=0.0,
                pause_after_seconds=0.0,
                target_text=target,
            ).to_payload()
            for segment, target in zip(
                segments,
                target_segments,
                strict=True,
            )
        ]
    if not isinstance(payload, dict):
        raise ValueError("Render Plan payload is malformed")
    if payload.get("schema_version") != RENDER_PLAN_SCHEMA_VERSION:
        raise ValueError("Render Plan schema version is unsupported")
    raw_units = payload.get("units")
    if not isinstance(raw_units, list):
        raise ValueError("Render Plan units are malformed")
    segments_by_id = {
        str(segment["segment_id"]): segment for segment in segments
    }
    targets_by_id = {
        str(segment["segment_id"]): target
        for segment, target in zip(segments, target_segments, strict=True)
    }
    units: list[dict[str, Any]] = []
    unit_ids: set[str] = set()
    for raw in raw_units:
        if not isinstance(raw, dict):
            raise ValueError("Render Plan unit is malformed")
        parsed = RenderPlanUnit.from_payload(raw)
        if parsed.unit_id in unit_ids:
            raise ValueError("Render Plan unit identity is invalid")
        segment = segments_by_id.get(parsed.source_segment_id)
        if segment is None:
            raise ValueError("Render Plan unit parent is unavailable")
        expected_parent_id = parsed.source_segment_id.split(
            "-retained-",
            maxsplit=1,
        )[0]
        if parsed.parent_segment_id != expected_parent_id:
            raise ValueError("Render Plan unit lineage is invalid")
        if (
            parsed.source_start_seconds
            < float(segment["start_seconds"]) - 1e-6
            or parsed.source_end_seconds
            > float(segment["end_seconds"]) + 1e-6
        ):
            raise ValueError("Render Plan unit is outside its source segment")
        unit_ids.add(parsed.unit_id)
        units.append(parsed.to_payload())
    grouped = {
        segment_id: [
            unit
            for unit in units
            if unit["source_segment_id"] == segment_id
        ]
        for segment_id in segments_by_id
    }
    flattened: list[dict[str, Any]] = []
    for segment in segments:
        segment_id = str(segment["segment_id"])
        group = grouped[segment_id]
        if not group:
            raise ValueError("Render Plan does not cover every Canonical segment")
        if "".join(str(unit["target_text"]) for unit in group) != targets_by_id[
            segment_id
        ]:
            raise ValueError("Render Plan units changed the Translation target")
        if abs(
            float(group[0]["source_start_seconds"])
            - float(segment["start_seconds"])
        ) > 1e-6 or abs(
            float(group[-1]["source_end_seconds"])
            - float(segment["end_seconds"])
        ) > 1e-6:
            raise ValueError("Render Plan units do not cover their source segment")
        if len(group) == 1:
            if bool(group[0]["internal_split"]):
                raise ValueError(
                    "A single Render Plan unit cannot be an internal split"
                )
        else:
            if not all(bool(unit["internal_split"]) for unit in group):
                raise ValueError("Internal Render Plan unit flags are incomplete")
            for left, right in zip(group, group[1:], strict=False):
                if left["approved_boundary_kind"] not in {"sentence", "pause"}:
                    raise ValueError(
                        "Internal Render Plan splits require an approved natural "
                        "boundary"
                    )
                if float(left["source_end_seconds"]) > float(
                    right["source_start_seconds"]
                ) + 1e-6:
                    raise ValueError("Internal Render Plan units overlap")
                if abs(
                    float(left["pause_after_seconds"])
                    - float(right["pause_before_seconds"])
                ) > 1e-6:
                    raise ValueError(
                        "Internal Render Plan pause metadata is inconsistent"
                    )
        flattened.extend(group)
    if flattened != units:
        raise ValueError("Render Plan units are not in Canonical segment order")
    return flattened


def _speech_text_audit_units(
    render_plan: list[dict[str, Any]],
    rendered_metadata: list[dict[str, Any]],
    prior_render: object,
) -> list[dict[str, Any]]:
    by_unit: dict[str, dict[str, Any]] = {}
    if isinstance(prior_render, dict):
        raw_settings = prior_render.get("render_settings_json", "{}")
        try:
            prior_settings = (
                json.loads(raw_settings)
                if isinstance(raw_settings, str)
                else raw_settings
            )
        except json.JSONDecodeError:
            prior_settings = {}
        if isinstance(prior_settings, dict):
            prior_units = prior_settings.get("speech_text_units") or []
            if isinstance(prior_units, list):
                for item in prior_units:
                    if not isinstance(item, dict):
                        continue
                    unit_id = str(item.get("render_unit_id", ""))
                    sha256 = str(item.get("sha256", ""))
                    if unit_id and sha256:
                        by_unit[unit_id] = {
                            "render_unit_id": unit_id,
                            "sha256": sha256,
                            "pronunciation_annotation_count": int(
                                item.get(
                                    "pronunciation_annotation_count",
                                    0,
                                )
                            ),
                            "chinese_replacement_count": int(
                                item.get("chinese_replacement_count", 0)
                            ),
                            "pronunciation_fallback_count": int(
                                item.get("pronunciation_fallback_count", 0)
                            ),
                        }
    for item in rendered_metadata:
        unit_id = str(item.get("segment_id", ""))
        sha256 = str(item.get("speech_text_sha256", ""))
        if unit_id and sha256:
            by_unit[unit_id] = {
                "render_unit_id": unit_id,
                "sha256": sha256,
                "pronunciation_annotation_count": int(
                    item.get("pronunciation_annotation_count", 0)
                ),
                "chinese_replacement_count": int(
                    item.get("chinese_replacement_count", 0)
                ),
                "pronunciation_fallback_count": int(
                    item.get("pronunciation_fallback_count", 0)
                ),
            }
    return [
        by_unit[str(unit["segment_id"])]
        for unit in render_plan
        if str(unit["segment_id"]) in by_unit
    ]


def render(request: dict[str, Any], output_root: Path) -> dict[str, Any]:
    source = _object(request, "source")
    analysis = _object(source, "analysis")
    translation = _object(request, "translation")
    rendering = _object(request, "rendering")
    pipeline_version = str(
        rendering.get("pipeline_version")
        or _pipeline_version_for_analysis(analysis)
    )
    renderer = str(
        rendering.get("renderer")
        or speech_model_for_pipeline_version(pipeline_version)
    )
    if renderer != speech_model_for_pipeline_version(pipeline_version):
        raise ValueError(
            "Render pipeline version and speech model do not match"
        )
    renderer_class = (
        INDEXTTS25_CLASS
        if renderer == SPEECH_MODEL_INDEXTTS25
        else INDEXTTS2_CLASS
    )
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
        render_units = _render_units_by_segment(
            analysis,
            segments,
            target_segments,
        )
        if pipeline_version in WORD_OWNERSHIP_PIPELINES and any(
            len(str(unit["target_text"]))
            > MAXIMUM_RENDER_UNIT_CHARACTERS
            for unit in render_units
        ):
            raise ValueError(
                "A V15 Translation target exceeds the renderer safety "
                "envelope without an approved natural split"
            )
        segments_by_id = {
            str(segment["segment_id"]): segment for segment in segments
        }
        render_plan: list[dict[str, Any]] = []
        for unit in render_units:
            item = segments_by_id[str(unit["source_segment_id"])]
            render_plan.append(
                {
                    "segment_id": str(unit["unit_id"]),
                    "speaker_id": str(item["speaker_label"]),
                    "source_segment_id": str(unit["source_segment_id"]),
                    "source_start_seconds": float(
                        unit["source_start_seconds"]
                    ),
                    "source_end_seconds": float(unit["source_end_seconds"]),
                    "target_text": str(unit["target_text"]),
                    "parent_segment_id": str(unit["parent_segment_id"]),
                    "pause_before_seconds": float(
                        unit["pause_before_seconds"]
                    ),
                    "pause_after_seconds": float(unit["pause_after_seconds"]),
                    "internal_split": bool(unit["internal_split"]),
                    "approved_boundary_kind": unit.get(
                        "approved_boundary_kind"
                    ),
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
            )
        voice_settings: dict[str, Any] = {}
        if pipeline_version in RHYTHM_PIPELINES:
            speaker_references, voice_settings = _apply_voice_fidelity(
                analysis,
                render_plan,
                speaker_references,
                source_path=source_path,
                source_wav=source_wav,
                work_root=root,
            )
        emotion_alpha = float(rendering.get("emotion_alpha", 0.8))
        rendered_root = output_root / "rendered-segments"
        rendered_root.mkdir(parents=True, exist_ok=True)
        rendered_paths: list[Path] = []
        rendered_metadata: list[dict[str, Any]] = []
        if segment_ordinal is not None:
            if not 1 <= segment_ordinal <= len(segments):
                raise ValueError("Segment revision ordinal is out of range")
            selected_segment_id = str(
                segments[segment_ordinal - 1]["segment_id"]
            )
            selected_render_plan = [
                unit
                for unit in render_plan
                if unit["source_segment_id"] == selected_segment_id
            ]
            if not selected_render_plan:
                raise ValueError("Segment revision Render Plan is incomplete")
            result = _remote_method(
                INDEXTTS_APP,
                renderer_class,
                "render",
                source_wav.read_bytes(),
                speaker_references,
                selected_render_plan,
                emotion_alpha=emotion_alpha,
                seed=42,
            )
            for item in result["segments"]:
                unit_id = str(item["segment_id"])
                render_ordinal = next(
                    index
                    for index, unit in enumerate(render_plan, start=1)
                    if unit["segment_id"] == unit_id
                )
                path = rendered_root / f"segment-{render_ordinal:04d}.wav"
                path.write_bytes(bytes(item["audio"]))
                rendered_paths.append(path)
                rendered_metadata.append(
                    {
                        "ordinal": render_ordinal,
                        "segment_id": unit_id,
                        "path": str(path),
                        "sha256": _file_hash(path),
                        "duration_seconds": float(item["duration_seconds"]),
                        "emotion_reference_start_seconds": float(
                            item["emotion_reference_start_seconds"]
                        ),
                        "emotion_reference_end_seconds": float(
                            item["emotion_reference_end_seconds"]
                        ),
                        **{
                            key: item[key]
                            for key in (
                                "speech_text_sha256",
                                "pronunciation_annotation_count",
                                "chinese_replacement_count",
                                "pronunciation_fallback_count",
                            )
                            if key in item
                        },
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
                renderer_class=renderer_class,
            )
            for ordinal, item in enumerate(result.pop("segments"), start=1):
                path = Path(str(item["path"]))
                rendered_paths.append(path)
                rendered_metadata.append({"ordinal": ordinal, **item})
        publication_path = output_root / "publication.m4a"
        rhythm_settings: dict[str, Any] = {}
        if pipeline_version in RHYTHM_PIPELINES:
            source_checksum = str(
                source.get("checksum") or _file_hash(source_path)
            )
            rendered_unit_ids = {
                str(item["segment_id"]) for item in rendered_metadata
            }
            unit_seeds = (
                {
                    unit_id: seed
                    for unit_id, seed in _prior_unit_seeds(
                        rendering.get("prior_render")
                    ).items()
                    if unit_id not in rendered_unit_ids
                }
                if segment_ordinal is not None
                else {}
            )

            def unit_identities(seeds: Mapping[str, int]) -> dict[str, str]:
                return rhythm_unit_tts_identities(
                    render_plan,
                    speaker_references,
                    source_checksum=source_checksum,
                    pipeline_version=pipeline_version,
                    renderer_class=renderer_class,
                    emotion_alpha=emotion_alpha,
                    unit_seeds=seeds,
                )

            def rerender(
                units: list[dict[str, Any]],
                seed: int,
            ) -> dict[str, Any]:
                return _remote_method(
                    INDEXTTS_APP,
                    renderer_class,
                    "render",
                    source_wav.read_bytes(),
                    speaker_references,
                    units,
                    emotion_alpha=emotion_alpha,
                    seed=seed,
                )

            assembly, segment_timings, rhythm_settings = _assemble_rhythm_render(
                rendering=rendering,
                analysis=analysis,
                render_plan=render_plan,
                rendered_paths=rendered_paths,
                rendered_metadata=rendered_metadata,
                unit_identities=unit_identities(unit_seeds),
                unit_seeds=unit_seeds,
                listening_audit=(
                    ListeningAuditRequest(
                        rerender=rerender,
                        identities=unit_identities,
                        rendered_root=rendered_root,
                    )
                    if rendering.get("listening_audit")
                    else None
                ),
                store_root=Path(
                    str(
                        rendering.get("unit_clip_store_root")
                        or output_root / "unit-clips"
                    )
                ),
                segment_ids=[str(segment["segment_id"]) for segment in segments],
                segment_ordinal=segment_ordinal,
                source_wav=source_wav,
                publication_path=publication_path,
                pipeline_version=pipeline_version,
                work_root=root,
            )
        elif segment_ordinal is None:
            sequence = _assembly_sequence(
                source_wav,
                render_plan,
                rendered_paths,
                list(analysis.get("source_events", [])),
                root,
            )
            unit_timings = _assembled_segment_timings(
                sequence,
                rendered_paths,
                rendered_metadata,
            )
            segment_timings = _parent_segment_timings(
                render_plan,
                unit_timings,
                [str(segment["segment_id"]) for segment in segments],
            )
            assembly = assemble_publication(
                sequence,
                publication_path,
                rendered_segment_count=len(render_plan),
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
            replacement_sequence = _assembly_sequence(
                source_wav,
                selected_render_plan,
                rendered_paths,
                [],
                root,
            )
            replacement_unit_timings = _assembled_segment_timings(
                replacement_sequence,
                rendered_paths,
                rendered_metadata,
            )
            replacement_path = root / "revision-replacement.wav"
            _concatenate_audio_sequence(
                replacement_sequence,
                replacement_path,
                root,
            )
            replacement_duration = float(
                _probe(replacement_path)["format"]["duration"]
            )
            assembly = _replace_publication_region(
                Path(str(prior_preview_path_value)),
                replacement_path,
                start_seconds,
                end_seconds,
                publication_path,
                root,
                rendered_segment_count=len(render_plan),
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
            for metadata, (unit_start, unit_end) in zip(
                rendered_metadata,
                replacement_unit_timings,
                strict=True,
            ):
                metadata["assembled_start_seconds"] = (
                    start_seconds + unit_start
                )
                metadata["assembled_end_seconds"] = start_seconds + unit_end
    quality = {
        **assembly["quality"],
        "timeline_continuous": timeline_is_continuous(
            segment_timings,
            float(assembly["duration_seconds"]),
        ),
    }
    audit_payload = rhythm_settings.get("listening_audit")
    audit_gpu_seconds = 0.0
    if isinstance(audit_payload, dict):
        leakage = source_language_leakage(audit_payload)
        if leakage is not None:
            quality["source_language_leakage"] = leakage
        audit_gpu_seconds = float(
            (audit_payload.get("usage") or {}).get("gpu_seconds", 0.0)
        )
    speech_text_units = _speech_text_audit_units(
        render_plan,
        rendered_metadata,
        rendering.get("prior_render"),
    )
    if (
        renderer == SPEECH_MODEL_INDEXTTS25
        and len(speech_text_units) != len(render_plan)
    ):
        raise ValueError("V16 speech text audit evidence is incomplete")
    speech_text_hashes = [
        {
            "render_unit_id": str(item["render_unit_id"]),
            "sha256": str(item["sha256"]),
        }
        for item in speech_text_units
    ]
    return {
        "publication_audio_path": str(publication_path),
        "duration_seconds": round(float(assembly["duration_seconds"])),
        "chapters": _chapters_from_timings(segment_timings),
        "pipeline_version": pipeline_version,
        "rendered_segments": rendered_metadata,
        "render_model": str(result["model_id"]),
        "render_model_revision": str(result["model_revision"]),
        "render_code_commit": str(result.get("code_commit", "")),
        "render_settings": {
            "renderer": renderer,
            "pronunciation_policy_version": (
                INDEXTTS25_PRONUNCIATION_POLICY_VERSION
                if renderer == SPEECH_MODEL_INDEXTTS25
                else None
            ),
            "emotion_alpha": float(result["emotion_alpha"]),
            "seed": int(result["seed"]),
            "random_sampling": bool(result["random_sampling"]),
            "lang": result.get("lang"),
            "model_load": result.get("load_settings") or {},
            "inference": result.get("inference_settings") or {},
            "native_headroom_target": result.get(
                "native_headroom_target"
            ),
            "runtime_revision": result.get("runtime_revision"),
            "pronunciation_annotation_count": sum(
                int(item["pronunciation_annotation_count"])
                for item in speech_text_units
            ),
            "chinese_replacement_count": sum(
                int(item["chinese_replacement_count"])
                for item in speech_text_units
            ),
            "pronunciation_fallback_count": sum(
                int(item["pronunciation_fallback_count"])
                for item in speech_text_units
            ),
            "speech_text_units": speech_text_units,
            "speech_text_hashes": speech_text_hashes,
            "speech_text_manifest_sha256": (
                _content_hash(speech_text_hashes)
                if speech_text_hashes
                else None
            ),
            **rhythm_settings,
            **voice_settings,
        },
        "quality": quality,
        "segment_timings": [
            {"start_seconds": start, "end_seconds": end}
            for start, end in segment_timings
        ],
        "estimated_cost_usd": _estimate_cost(
            float(result.get("total_inference_seconds", 0)) + audit_gpu_seconds
        ),
    }


def timeline_is_continuous(
    segment_timings: Sequence[tuple[float, float]],
    duration_seconds: float,
) -> bool:
    """Measure whether segment timings form one ordered, gap-safe timeline.

    Every segment must be finite and non-empty, start no earlier than the
    previous segment ends, and end inside the encoded file.
    """
    if not segment_timings:
        return False
    previous_end = 0.0
    for start, end in segment_timings:
        if (
            not (math.isfinite(start) and math.isfinite(end))
            or start < 0
            or end <= start
            or start < previous_end - 0.001
        ):
            return False
        previous_end = end
    return previous_end <= duration_seconds + _TIMELINE_END_TOLERANCE_SECONDS


def source_language_leakage(audit_payload: Mapping[str, Any]) -> bool | None:
    """Derive leaked English from the Listening Audit findings.

    ``None`` means not measured: some unit has no back-transcription.
    """
    findings = audit_payload.get("findings")
    if isinstance(findings, list) and any(
        isinstance(item, Mapping) and item.get("kind") == "english_leakage"
        for item in findings
    ):
        return True
    units = audit_payload.get("audited_unit_count")
    transcribed = audit_payload.get("transcribed_unit_count")
    if isinstance(units, int) and units > 0 and transcribed == units:
        return False
    return None


def _apply_voice_fidelity(
    analysis: dict[str, Any],
    render_plan: list[dict[str, Any]],
    speaker_references: dict[str, bytes],
    *,
    source_path: Path,
    source_wav: Path,
    work_root: Path,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Apply the render-plan half of Voice Reference fidelity (B2, B3).

    Content Edit removals are known only here, so an owned Voice Reference
    whose clip touches a removal is re-selected first; every Render Plan
    item whose own span is unusable as an emotion prompt then carries
    explicit emotion audio. Both are deterministic, so a Segment Revision
    reproduces the bytes and the TTS identities of unchanged units.
    """
    exclusions = _render_plan_exclusions(analysis)
    settings: dict[str, Any] = {
        "voice_reference_policy_version": VOICE_REFERENCE_POLICY_VERSION,
        "voice_reference_records": [],
    }
    # Malformed evidence degrades to today's behavior with an explicit
    # error instead of blocking the render; the outcome is deterministic,
    # so a Segment Revision degrades identically.
    references = dict(speaker_references)
    try:
        references, records = reselect_for_content_edits(
            analysis,
            speaker_references,
            source_path,
            exclusions,
            work_root,
        )
        settings["voice_reference_records"] = [
            record.to_payload() for record in records
        ]
    except (OSError, KeyError, TypeError, ValueError) as error:
        settings["voice_reference_error"] = f"{type(error).__name__}: {error}"[
            :300
        ]
    try:
        settings["emotion_references"] = apply_emotion_references(
            render_plan,
            analysis,
            references,
            source_wav,
            exclusions=exclusions,
        )
    except (OSError, KeyError, TypeError, ValueError) as error:
        settings["emotion_references"] = {
            "policy_version": EMOTION_REFERENCE_POLICY_VERSION,
            "error": f"{type(error).__name__}: {error}"[:300],
        }
    return references, settings


@dataclass(frozen=True)
class ListeningAuditRequest:
    """What the Listening Audit and its D3 retry need from the render."""

    rerender: Callable[[list[dict[str, Any]], int], dict[str, Any]]
    identities: Callable[[Mapping[str, int]], dict[str, str]]
    rendered_root: Path


class _ModalListeningAuditRemote:
    """The deployed audit methods, called under the routed Modal profile."""

    def transcribe(self, clips: dict[str, bytes]) -> dict[str, Any]:
        return _remote_method(
            QWEN_ASR_APP,
            QWEN_ASR_CLASS,
            "transcribe_clips",
            clips,
            language=AUDIT_LANGUAGE,
        )

    def embed(self, clips: dict[str, bytes]) -> dict[str, Any]:
        return _remote_method(SPEAKER_APP, SPEAKER_CLASS, "compare", clips)


def _assemble_rhythm_render(
    *,
    rendering: dict[str, Any],
    analysis: dict[str, Any],
    render_plan: list[dict[str, Any]],
    rendered_paths: list[Path],
    rendered_metadata: list[dict[str, Any]],
    unit_identities: dict[str, str],
    store_root: Path,
    segment_ids: list[str],
    segment_ordinal: int | None,
    source_wav: Path,
    publication_path: Path,
    pipeline_version: str,
    work_root: Path,
    unit_seeds: Mapping[str, int] | None = None,
    listening_audit: ListeningAuditRequest | None = None,
    policy: RhythmPolicy = RHYTHM_POLICY,
) -> tuple[dict[str, Any], list[tuple[float, float]], dict[str, Any]]:
    """Assemble V19/V20 audio from the Unit Clip Store and master it once.

    A complete render stores every raw clip. A Segment Revision stores only
    the revised units' clips, reuses every other clip whose TTS identity is
    unchanged, and re-assembles the whole episode from PCM. When the store
    has expired or no longer matches the Render Plan, the revision splices
    the rhythm-prepared units into the prior preview instead.

    When requested, the Listening Audit runs on the raw clips before
    assembly, so a D3 re-render replaces its clips and the episode is still
    mastered once.
    """
    store = UnitClipStore(store_root)
    plan_by_unit = {str(item["segment_id"]): item for item in render_plan}
    rendered_entries: dict[str, UnitClipEntry] = {}
    for path, metadata in zip(rendered_paths, rendered_metadata, strict=True):
        unit_id = str(metadata["segment_id"])
        entry = store.put_clip(
            path,
            unit=plan_by_unit[unit_id],
            tts_identity=unit_identities[unit_id],
            duration_seconds=float(metadata["duration_seconds"]),
        )
        rendered_entries[unit_id] = entry
        metadata["unit_clip_file"] = entry.file
    exclusions = _render_plan_exclusions(analysis)
    entries = (
        [rendered_entries[str(item["segment_id"])] for item in render_plan]
        if segment_ordinal is None
        else _revision_clip_entries(
            store,
            rendering.get("prior_render"),
            render_plan,
            rendered_entries,
            unit_identities,
        )
    )
    seeds = dict(unit_seeds or {})
    audit_payload: dict[str, Any] | None = None
    if listening_audit is not None:
        audit_payload, retried = _run_listening_audit(
            store=store,
            request=listening_audit,
            plan_by_unit=plan_by_unit,
            entries=(
                entries
                if entries is not None
                else [
                    rendered_entries[str(item["segment_id"])]
                    for item in render_plan
                    if str(item["segment_id"]) in rendered_entries
                ]
            ),
            rendered_entries=rendered_entries,
            rendered_paths=rendered_paths,
            rendered_metadata=rendered_metadata,
            seeds=seeds,
            scope="episode" if entries is not None else "revised_units",
        )
        if entries is not None:
            entries = [retried.get(entry.unit_id, entry) for entry in entries]
    audit_settings: dict[str, Any] = {
        "unit_seed_overrides": dict(sorted(seeds.items())),
    }
    clip_paths = [
        path
        for path in (
            store.verified_clip(entry) for entry in entries or []
        )
        if path is not None
    ]
    if entries is not None and len(clip_paths) == len(entries):
        reference = store.write_manifest(
            entries,
            pipeline_version=pipeline_version,
        )
        assembly = assemble_rhythm_publication(
            [
                RhythmAssemblyUnit(
                    unit_id=str(item["segment_id"]),
                    clip_path=path,
                    speaker_id=str(item["speaker_id"]),
                    source_start_seconds=float(item["source_start_seconds"]),
                    source_end_seconds=float(item["source_end_seconds"]),
                )
                for item, path in zip(render_plan, clip_paths, strict=True)
            ],
            publication_path,
            exclusions=exclusions,
            source_events=list(analysis.get("source_events", [])),
            source_wav=source_wav,
            rendered_segment_count=len(render_plan),
            policy=policy,
        )
        store.touch(entries, reference)
        unit_timings: list[tuple[float, float]] = assembly.pop("unit_timings")
        timings_by_unit = {
            str(item["segment_id"]): timing
            for item, timing in zip(render_plan, unit_timings, strict=True)
        }
        for metadata in rendered_metadata:
            start, end = timings_by_unit[str(metadata["segment_id"])]
            metadata["assembled_start_seconds"] = start
            metadata["assembled_end_seconds"] = end
        assembly.pop("timeline_seconds")
        rhythm = assembly.pop("rhythm")
        if audit_payload is not None:
            audit_settings["listening_audit"] = _with_output_timings(
                audit_payload,
                timings_by_unit,
            )
        return (
            assembly,
            _parent_segment_timings(render_plan, unit_timings, segment_ids),
            {
                "rhythm_policy_version": policy.version,
                "assembly_source": "unit_clip_store",
                "unit_clip_store": {
                    **reference.to_payload(),
                    "root": str(store.root),
                },
                "rhythm": rhythm,
                **audit_settings,
            },
        )
    if segment_ordinal is None:
        raise RuntimeError("Unit Clip Store failed to verify the stored clips")
    assembly, segment_timings, settings = _splice_rhythm_revision(
        rendering=rendering,
        render_plan=render_plan,
        rendered_paths=rendered_paths,
        rendered_metadata=rendered_metadata,
        segment_ids=segment_ids,
        segment_ordinal=segment_ordinal,
        exclusions=exclusions,
        publication_path=publication_path,
        work_root=work_root,
        policy=policy,
    )
    if audit_payload is not None:
        audit_settings["listening_audit"] = _with_output_timings(
            audit_payload,
            {
                str(item["segment_id"]): (
                    float(item["assembled_start_seconds"]),
                    float(item["assembled_end_seconds"]),
                )
                for item in rendered_metadata
            },
        )
    return assembly, segment_timings, {**settings, **audit_settings}


def _run_listening_audit(
    *,
    store: UnitClipStore,
    request: ListeningAuditRequest,
    plan_by_unit: Mapping[str, dict[str, Any]],
    entries: Sequence[UnitClipEntry],
    rendered_entries: dict[str, UnitClipEntry],
    rendered_paths: list[Path],
    rendered_metadata: list[dict[str, Any]],
    seeds: dict[str, int],
    scope: str,
) -> tuple[dict[str, Any], dict[str, UnitClipEntry]]:
    """Audit the raw clips and apply the D3 policy once.

    Units rendered by this call with a hard finding are re-rendered once
    with ``LISTENING_AUDIT_RETRY_SEED`` in one remote call, stored, and
    audited again. Their clips replace the originals in place; a failed
    retry keeps the originals and is reported. Hard findings that remain on
    units rendered by this call are ``blocking_unit_ids``: the Quality Gate
    sends the run to the Review Queue. Hard findings on reused units were
    already decided by an earlier render.
    """
    session = ListeningAuditSession(
        evidence_root=store.root / "audit-evidence",
        remote=_ModalListeningAuditRemote(),
    )
    first = session.run(_audit_units(store, entries, plan_by_unit))
    final = first
    retry_ids = [
        unit_id for unit_id in first.hard_unit_ids if unit_id in rendered_entries
    ]
    retried: dict[str, UnitClipEntry] = {}
    retry: dict[str, Any] | None = None
    retry_seconds = 0.0
    if retry_ids:
        first_payload = first.to_payload()
        retry = {
            "seed": LISTENING_AUDIT_RETRY_SEED,
            "unit_ids": retry_ids,
            "initial_findings": [
                item
                for item in first_payload["findings"]
                if item["unit_id"] in retry_ids
            ],
        }
        try:
            outputs = _render_audit_retry(
                store=store,
                request=request,
                plan_by_unit=plan_by_unit,
                rendered_metadata=rendered_metadata,
                retry_ids=retry_ids,
                seeds=seeds,
            )
        except Exception as error:
            # A failed retry keeps the original clips and their findings.
            retry["status"] = "failed"
            retry["error"] = f"{type(error).__name__}: {error}"[:300]
        else:
            retry_seconds = float(outputs.pop("inference_seconds"))
            index_by_unit = {
                str(item["segment_id"]): index
                for index, item in enumerate(rendered_metadata)
            }
            for unit_id in retry_ids:
                path, entry, metadata = outputs[unit_id]
                index = index_by_unit[unit_id]
                rendered_paths[index] = path
                rendered_metadata[index].update(metadata)
                rendered_entries[unit_id] = entry
                retried[unit_id] = entry
                seeds[unit_id] = LISTENING_AUDIT_RETRY_SEED
            final = session.run(
                _audit_units(
                    store,
                    [retried.get(entry.unit_id, entry) for entry in entries],
                    plan_by_unit,
                )
            )
            retry["status"] = "rendered"
            retry["resolved_unit_ids"] = [
                unit_id
                for unit_id in retry_ids
                if unit_id not in final.hard_unit_ids
            ]
    usage = session.usage_payload()
    usage["retry_render_inference_seconds"] = round(retry_seconds, 3)
    usage["gpu_seconds"] = round(float(usage["gpu_seconds"]) + retry_seconds, 3)
    return (
        {
            **final.to_payload(),
            "scope": scope,
            "language": AUDIT_LANGUAGE,
            "blocking_unit_ids": [
                unit_id
                for unit_id in final.hard_unit_ids
                if unit_id in rendered_entries
            ],
            "retry": retry,
            "usage": usage,
        },
        retried,
    )


def _render_audit_retry(
    *,
    store: UnitClipStore,
    request: ListeningAuditRequest,
    plan_by_unit: Mapping[str, dict[str, Any]],
    rendered_metadata: list[dict[str, Any]],
    retry_ids: list[str],
    seeds: Mapping[str, int],
) -> dict[str, Any]:
    """Render the retry units in one call and store their clips.

    Nothing outside the store changes until every retry clip is stored.
    """
    result = request.rerender(
        [plan_by_unit[unit_id] for unit_id in retry_ids],
        LISTENING_AUDIT_RETRY_SEED,
    )
    items = {str(item["segment_id"]): item for item in result["segments"]}
    if set(items) != set(retry_ids):
        raise ValueError("Listening Audit retry render is incomplete")
    identities = request.identities(
        {**seeds, **dict.fromkeys(retry_ids, LISTENING_AUDIT_RETRY_SEED)}
    )
    ordinals = {
        str(item["segment_id"]): int(item["ordinal"]) for item in rendered_metadata
    }
    outputs: dict[str, Any] = {
        "inference_seconds": float(result.get("total_inference_seconds", 0.0))
    }
    for unit_id in retry_ids:
        item = items[unit_id]
        path = request.rendered_root / f"segment-{ordinals[unit_id]:04d}-retry.wav"
        path.write_bytes(bytes(item["audio"]))
        duration = float(item["duration_seconds"])
        entry = store.put_clip(
            path,
            unit=plan_by_unit[unit_id],
            tts_identity=identities[unit_id],
            duration_seconds=duration,
        )
        outputs[unit_id] = (
            path,
            entry,
            {
                "path": str(path),
                "sha256": _file_hash(path),
                "duration_seconds": duration,
                "emotion_reference_start_seconds": float(
                    item["emotion_reference_start_seconds"]
                ),
                "emotion_reference_end_seconds": float(
                    item["emotion_reference_end_seconds"]
                ),
                "unit_clip_file": entry.file,
                "seed": LISTENING_AUDIT_RETRY_SEED,
                "listening_audit_retry": True,
                **{
                    key: item[key]
                    for key in (
                        "speech_text_sha256",
                        "pronunciation_annotation_count",
                        "chinese_replacement_count",
                        "pronunciation_fallback_count",
                    )
                    if key in item
                },
            },
        )
    return outputs


def _audit_units(
    store: UnitClipStore,
    entries: Sequence[UnitClipEntry],
    plan_by_unit: Mapping[str, dict[str, Any]],
) -> list[AuditUnit]:
    return [
        AuditUnit(
            unit_id=entry.unit_id,
            parent_segment_id=str(plan_by_unit[entry.unit_id]["parent_segment_id"]),
            speaker_id=str(plan_by_unit[entry.unit_id]["speaker_id"]),
            target_text=str(plan_by_unit[entry.unit_id]["target_text"]),
            clip_path=store.verified_clip(entry),
            clip_sha256=entry.sha256,
        )
        for entry in entries
    ]


def _with_output_timings(
    payload: Mapping[str, Any],
    timings: Mapping[str, tuple[float, float]],
) -> dict[str, Any]:
    """Attach each finding's span in Publication Audio for the listen link."""
    findings = []
    for item in payload.get("findings", []):
        timing = timings.get(str(item.get("unit_id")))
        findings.append(
            {
                **item,
                "output_start_seconds": round(timing[0], 3) if timing else None,
                "output_end_seconds": round(timing[1], 3) if timing else None,
            }
        )
    return {**payload, "findings": findings}


def _prior_unit_seeds(prior_render: object) -> dict[str, int]:
    """Read the retry seeds a prior V19/V20 render recorded per unit."""
    if not isinstance(prior_render, dict):
        return {}
    raw_settings = prior_render.get("render_settings_json", "{}")
    try:
        settings = (
            json.loads(raw_settings)
            if isinstance(raw_settings, str)
            else raw_settings
        )
    except json.JSONDecodeError:
        return {}
    overrides = (
        settings.get("unit_seed_overrides") if isinstance(settings, dict) else None
    )
    if not isinstance(overrides, dict):
        return {}
    return {
        str(unit_id): int(seed)
        for unit_id, seed in overrides.items()
        if isinstance(seed, int) and not isinstance(seed, bool)
    }


def _revision_clip_entries(
    store: UnitClipStore,
    prior_render: object,
    render_plan: list[dict[str, Any]],
    rendered_entries: dict[str, UnitClipEntry],
    unit_identities: dict[str, str],
) -> list[UnitClipEntry] | None:
    """Combine revised clips with every reusable prior clip, in plan order."""
    if not isinstance(prior_render, dict):
        return None
    raw_settings = prior_render.get("render_settings_json", "{}")
    try:
        settings = (
            json.loads(raw_settings)
            if isinstance(raw_settings, str)
            else raw_settings
        )
    except json.JSONDecodeError:
        return None
    if not isinstance(settings, dict):
        return None
    reference = UnitClipManifestReference.from_payload(
        settings.get("unit_clip_store")
    )
    if reference is None:
        return None
    prior_entries = store.load_manifest(reference)
    if prior_entries is None:
        return None
    prior_by_unit = {entry.unit_id: entry for entry in prior_entries}
    entries: list[UnitClipEntry] = []
    for item in render_plan:
        unit_id = str(item["segment_id"])
        revised = rendered_entries.get(unit_id)
        if revised is not None:
            entries.append(revised)
            continue
        prior = prior_by_unit.get(unit_id)
        if prior is None or prior.tts_identity != unit_identities[unit_id]:
            return None
        entries.append(prior)
    return entries


def _splice_rhythm_revision(
    *,
    rendering: dict[str, Any],
    render_plan: list[dict[str, Any]],
    rendered_paths: list[Path],
    rendered_metadata: list[dict[str, Any]],
    segment_ids: list[str],
    segment_ordinal: int,
    exclusions: Sequence[tuple[float, float]],
    publication_path: Path,
    work_root: Path,
    policy: RhythmPolicy,
) -> tuple[dict[str, Any], list[tuple[float, float]], dict[str, Any]]:
    prior_preview_path_value = rendering.get("prior_preview_audio_path")
    prior_render = rendering.get("prior_render")
    if not prior_preview_path_value or not isinstance(prior_render, dict):
        raise ValueError("Segment revision requires the prior render checkpoint")
    prior_timings = [
        (float(item[0]), float(item[1]))
        for item in prior_render.get("segment_timings", [])
    ]
    if len(prior_timings) != len(segment_ids):
        raise ValueError("Prior segment timing checkpoint is incomplete")
    start_seconds, end_seconds = prior_timings[segment_ordinal - 1]
    plan_by_unit = {str(item["segment_id"]): item for item in render_plan}
    replacement_path = work_root / "revision-replacement.wav"
    timeline = _write_rhythm_timeline(
        [
            RhythmAssemblyUnit(
                unit_id=str(metadata["segment_id"]),
                clip_path=path,
                speaker_id=str(
                    plan_by_unit[str(metadata["segment_id"])]["speaker_id"]
                ),
                source_start_seconds=float(
                    plan_by_unit[str(metadata["segment_id"])][
                        "source_start_seconds"
                    ]
                ),
                source_end_seconds=float(
                    plan_by_unit[str(metadata["segment_id"])][
                        "source_end_seconds"
                    ]
                ),
            )
            for path, metadata in zip(
                rendered_paths,
                rendered_metadata,
                strict=True,
            )
        ],
        replacement_path,
        work_root,
        exclusions=exclusions,
        source_events=(),
        source_wav=None,
        policy=policy,
    )
    replacement_duration = float(timeline["timeline_seconds"])
    assembly = _replace_publication_region(
        Path(str(prior_preview_path_value)),
        replacement_path,
        start_seconds,
        end_seconds,
        publication_path,
        work_root,
        rendered_segment_count=len(render_plan),
    )
    shift = replacement_duration - (end_seconds - start_seconds)
    segment_timings: list[tuple[float, float]] = []
    for index, (start, end) in enumerate(prior_timings, start=1):
        if index < segment_ordinal:
            segment_timings.append((start, end))
        elif index == segment_ordinal:
            segment_timings.append(
                (start_seconds, start_seconds + replacement_duration)
            )
        else:
            segment_timings.append((start + shift, end + shift))
    for metadata, (unit_start, unit_end) in zip(
        rendered_metadata,
        timeline["unit_timings"],
        strict=True,
    ):
        metadata["assembled_start_seconds"] = start_seconds + unit_start
        metadata["assembled_end_seconds"] = start_seconds + unit_end
    return (
        assembly,
        segment_timings,
        {
            "rhythm_policy_version": policy.version,
            "assembly_source": "publication_splice",
            "unit_clip_store": None,
            "rhythm": timeline["rhythm"],
        },
    )


def _render_plan_exclusions(
    analysis: dict[str, Any],
) -> list[tuple[float, float]]:
    """Read the Content Edit removals that the Render Plan applied."""
    payload = analysis.get("render_plan")
    if not isinstance(payload, dict):
        return []
    raw = payload.get("exclusions", [])
    if not isinstance(raw, list):
        raise ValueError("Render Plan exclusions are malformed")
    intervals: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Render Plan exclusions are malformed")
        try:
            start = float(item["start_seconds"])
            end = float(item["end_seconds"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Render Plan exclusions are malformed") from error
        if not (math.isfinite(start) and math.isfinite(end)) or end < start:
            raise ValueError("Render Plan exclusions are malformed")
        intervals.append((start, end))
    return sorted(intervals)


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
    if shard_count < 1:
        raise ValueError("Render shard count must be positive")
    if not render_plan:
        return []
    resolved_shard_count = min(shard_count, len(render_plan))
    weights = [
        max(1, len(str(item["target_text"]))) for item in render_plan
    ]
    shards: list[list[dict[str, Any]]] = []
    cursor = 0
    for shard_index in range(resolved_shard_count):
        remaining_shards = resolved_shard_count - shard_index
        if remaining_shards == 1:
            shards.append(render_plan[cursor:])
            break
        budget = sum(weights[cursor:]) / remaining_shards
        end = cursor + 1
        accumulated = float(weights[cursor])
        maximum_end = len(render_plan) - (remaining_shards - 1)
        while (
            end < maximum_end
            and accumulated + weights[end] / 2 <= budget
        ):
            accumulated += weights[end]
            end += 1
        shards.append(render_plan[cursor:end])
        cursor = end
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
    renderer_class: str = INDEXTTS2_CLASS,
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
    if pipeline_version in RHYTHM_PIPELINES:
        render_version = rhythm_render_cache_identity(
            render_plan,
            speaker_references,
            source_checksum=source_checksum,
            pipeline_version=pipeline_version,
            renderer_class=renderer_class,
            emotion_alpha=emotion_alpha,
        )
    else:
        render_version = _legacy_render_cache_identity(
            render_plan,
            speaker_references,
            pipeline_version=pipeline_version,
            renderer_class=renderer_class,
            emotion_alpha=emotion_alpha,
        )
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
                renderer_class,
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
                    **{
                        key: rendered[key]
                        for key in (
                            "speech_text_sha256",
                            "pronunciation_annotation_count",
                            "chinese_replacement_count",
                            "pronunciation_fallback_count",
                        )
                        if key in rendered
                    },
                }
            )
    first = shard_results[0]
    return {
        "segments": segments_metadata,
        "model_id": first["model_id"],
        "model_revision": first["model_revision"],
        "code_commit": first.get("code_commit", ""),
        "lang": first.get("lang"),
        "load_settings": first.get("load_settings") or {},
        "inference_settings": first.get("inference_settings") or {},
        "native_headroom_target": first.get("native_headroom_target"),
        "runtime_revision": first.get("runtime_revision"),
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


def _legacy_render_cache_identity(
    render_plan: list[dict[str, Any]],
    speaker_references: dict[str, bytes],
    *,
    pipeline_version: str,
    renderer_class: str,
    emotion_alpha: float,
) -> str:
    """V15-V18 identity; it also hashes assembly-only pause metadata."""
    return _content_hash(
        {
            "segments": [
                {
                    "target_text": str(item["target_text"]),
                    "speaker_id": str(item["speaker_id"]),
                    "parent_segment_id": str(item["parent_segment_id"]),
                    "source_segment_id": str(item["source_segment_id"]),
                    "source_start_seconds": float(
                        item["source_start_seconds"]
                    ),
                    "source_end_seconds": float(item["source_end_seconds"]),
                    "pause_before_seconds": float(
                        item["pause_before_seconds"]
                    ),
                    "pause_after_seconds": float(item["pause_after_seconds"]),
                    "internal_split": bool(item["internal_split"]),
                    "approved_boundary_kind": item[
                        "approved_boundary_kind"
                    ],
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
            "pronunciation_policy_version": (
                INDEXTTS25_PRONUNCIATION_POLICY_VERSION
                if renderer_class == INDEXTTS25_CLASS
                else None
            ),
            "emotion_alpha": emotion_alpha,
            "seed": _RENDER_SEED,
        }
    )[:16]


def rhythm_unit_tts_identities(
    render_plan: list[dict[str, Any]],
    speaker_references: dict[str, bytes],
    *,
    source_checksum: str,
    pipeline_version: str,
    renderer_class: str,
    emotion_alpha: float,
    unit_seeds: Mapping[str, int] | None = None,
) -> dict[str, str]:
    """Hash, per render unit, only the inputs that shape its TTS output.

    The emotion reference is either explicit audio or the source span the
    renderer cuts from the staged source, so the span counts only when no
    explicit reference exists. Pause fields, internal-split flags, and every
    Rhythm Policy value are excluded so assembly changes reuse the clips.
    ``unit_seeds`` names the units that a Listening Audit retry re-rendered
    with another seed; every other unit hashes the default seed.
    """
    seeds = unit_seeds or {}
    speaker_hashes = {
        label: hashlib.sha256(content).hexdigest()
        for label, content in speaker_references.items()
    }
    identities: dict[str, str] = {}
    for item in render_plan:
        emotion_audio = item.get("emotion_reference_audio")
        explicit_emotion = isinstance(emotion_audio, bytes) and bool(
            emotion_audio
        )
        speaker_id = str(item["speaker_id"])
        identities[str(item["segment_id"])] = _content_hash(
            {
                "cache_identity": RHYTHM_RENDER_IDENTITY_VERSION,
                "render_unit_id": str(item["segment_id"]),
                "parent_segment_id": str(item["parent_segment_id"]),
                "source_segment_id": str(item["source_segment_id"]),
                "target_text": str(item["target_text"]),
                "speaker_id": speaker_id,
                "speaker_reference_sha256": speaker_hashes.get(speaker_id, ""),
                "emotion_reference_sha256": (
                    hashlib.sha256(emotion_audio).hexdigest()
                    if explicit_emotion and isinstance(emotion_audio, bytes)
                    else ""
                ),
                "emotion_reference_span": (
                    None
                    if explicit_emotion
                    else [
                        float(item["source_start_seconds"]),
                        float(item["source_end_seconds"]),
                    ]
                ),
                "source_checksum": source_checksum,
                "pipeline_version": pipeline_version,
                "renderer_class": renderer_class,
                "pronunciation_policy_version": (
                    INDEXTTS25_PRONUNCIATION_POLICY_VERSION
                    if renderer_class == INDEXTTS25_CLASS
                    else None
                ),
                "emotion_alpha": emotion_alpha,
                "seed": int(seeds.get(str(item["segment_id"]), _RENDER_SEED)),
            }
        )
    return identities


def rhythm_render_cache_identity(
    render_plan: list[dict[str, Any]],
    speaker_references: dict[str, bytes],
    *,
    source_checksum: str,
    pipeline_version: str,
    renderer_class: str,
    emotion_alpha: float,
) -> str:
    identities = rhythm_unit_tts_identities(
        render_plan,
        speaker_references,
        source_checksum=source_checksum,
        pipeline_version=pipeline_version,
        renderer_class=renderer_class,
        emotion_alpha=emotion_alpha,
    )
    return _content_hash(
        {
            "cache_identity": RHYTHM_RENDER_IDENTITY_VERSION,
            "units": [
                identities[str(item["segment_id"])] for item in render_plan
            ],
        }
    )[:16]


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


def align_transcript_deterministically(
    words: list[dict[str, Any]],
    diarization: list[dict[str, Any]],
    *,
    maximum_seconds: float = MAXIMUM_SEGMENT_SECONDS,
    policy: DeterministicOwnershipPolicy = DETERMINISTIC_OWNERSHIP_POLICY,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """V21 initial ownership: retime, onset-bias, and tie-to-following.

    Returns the v6-grouped segments and the policy evidence. The input words
    and turns are not modified, so ``aligned_words`` and
    ``raw_diarization_turns`` keep the models' original timestamps.
    """
    ownership = apply_deterministic_ownership(words, diarization, policy)
    segments = _segments_from_assigned_words(
        [dict(word) for word in ownership.assigned_words],
        maximum_seconds=maximum_seconds,
    )
    return segments, ownership.evidence_payload()


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


@dataclass(frozen=True)
class RhythmAssemblyUnit:
    """One retained Render Plan unit and its raw TTS clip."""

    unit_id: str
    clip_path: Path
    speaker_id: str
    source_start_seconds: float
    source_end_seconds: float


def assemble_rhythm_publication(
    units: Sequence[RhythmAssemblyUnit],
    publication_path: Path,
    *,
    exclusions: Sequence[tuple[float, float]] = (),
    source_events: Sequence[dict[str, Any]] = (),
    source_wav: Path | None = None,
    rendered_segment_count: int | None = None,
    policy: RhythmPolicy = RHYTHM_POLICY,
) -> dict[str, Any]:
    """Assemble V19/V20 Publication Audio from raw clips and master it once.

    Each clip is edge-trimmed, short replies get bounded tempo, and the
    Rhythm Policy inserts source-derived pauses and Content Edit cut
    transitions before the shared compression and mastering chain.
    """
    if not units:
        raise ValueError("No rendered speech segments are available")
    with TemporaryDirectory(prefix="transpodcast-rhythm-") as temp:
        root = Path(temp)
        raw_master = root / "raw-master.wav"
        timeline = _write_rhythm_timeline(
            units,
            raw_master,
            root,
            exclusions=exclusions,
            source_events=source_events,
            source_wav=source_wav,
            policy=policy,
        )
        compressed = root / "compressed-master.wav"
        _compress(raw_master, compressed)
        stereo_master = root / "compressed-master-stereo.wav"
        _to_stereo_pcm(compressed, stereo_master)
        mastered = _master_stereo_pcm(
            stereo_master,
            publication_path,
            root,
            rendered_segment_count=(
                rendered_segment_count
                if rendered_segment_count is not None
                else len(units)
            ),
        )
    return {**mastered, **timeline}


def _write_rhythm_timeline(
    units: Sequence[RhythmAssemblyUnit],
    destination: Path,
    work_root: Path,
    *,
    exclusions: Sequence[tuple[float, float]],
    source_events: Sequence[dict[str, Any]],
    source_wav: Path | None,
    policy: RhythmPolicy,
) -> dict[str, Any]:
    """Write the unmastered mono timeline and return its unit timings."""
    sample_rate = _ASSEMBLY_SAMPLE_RATE
    boundaries = [
        classify_boundary(
            previous_end_seconds=previous.source_end_seconds,
            previous_speaker=previous.speaker_id,
            next_start_seconds=unit.source_start_seconds,
            next_speaker=unit.speaker_id,
            exclusions=exclusions,
        )
        for previous, unit in zip(units, units[1:], strict=False)
    ]
    events = sorted(
        (
            (float(event["start_seconds"]), float(event["end_seconds"]))
            for event in source_events
            if bool(event.get("safe_to_preserve", False))
            and float(event["end_seconds"]) > float(event["start_seconds"])
        ),
        key=lambda item: item[0],
    )
    edge_fade = round(policy.edge_fade_seconds * sample_rate)
    cut_fade = round(policy.cut_fade_seconds * sample_rate)
    raw_path = work_root / "rhythm-timeline.f32"
    cursor = 0
    event_index = 0
    previous_tail_seconds = 0.0
    unit_timings: list[tuple[float, float]] = []
    tempo_adjustments: list[dict[str, Any]] = []
    silence_caps: list[dict[str, Any]] = []
    pauses: list[float] = []
    with raw_path.open("wb") as output:

        def write(samples: array[float]) -> None:
            nonlocal cursor
            output.write(_f32le_bytes(samples))
            cursor += len(samples)

        def write_events_before(start_seconds: float | None) -> None:
            # Preserved Source Events keep their source order and follow the
            # preceding unit directly; the policy pause precedes the next unit.
            nonlocal event_index
            while event_index < len(events) and (
                start_seconds is None or events[event_index][0] < start_seconds
            ):
                event_start, event_end = events[event_index]
                event = _decode_mono_f32(
                    _required_path(source_wav),
                    start_seconds=event_start,
                    duration_seconds=event_end - event_start,
                )
                apply_fades(event, fade_in=edge_fade, fade_out=edge_fade)
                write(event)
                event_index += 1

        for index, unit in enumerate(units):
            kept, head_seconds, tail_seconds, factor, removed = (
                _prepare_rhythm_clip(unit, policy)
            )
            if removed:
                silence_caps.append(
                    {"render_unit_id": unit.unit_id, "removed_seconds": list(removed)}
                )
            if factor != 1.0:
                tempo_adjustments.append(
                    {"render_unit_id": unit.unit_id, "factor": round(factor, 4)}
                )
            before = boundaries[index - 1] if index > 0 else None
            after = boundaries[index] if index < len(boundaries) else None
            apply_fades(
                kept,
                fade_in=cut_fade if before and before.crosses_cut else edge_fade,
                fade_out=cut_fade if after and after.crosses_cut else edge_fade,
            )
            write_events_before(unit.source_start_seconds)
            if before is not None:
                pause = policy.pause_seconds(before)
                pauses.append(pause)
                silence = inserted_silence_seconds(
                    pause,
                    previous_tail_seconds=previous_tail_seconds,
                    next_head_seconds=head_seconds,
                )
                write(array("f", bytes(4 * round(silence * sample_rate))))
            start = cursor
            write(kept)
            unit_timings.append((start / sample_rate, cursor / sample_rate))
            previous_tail_seconds = tail_seconds
        write_events_before(None)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-i",
            str(raw_path),
            "-c:a",
            "pcm_s24le",
            str(destination),
        ]
    )
    return {
        "unit_timings": unit_timings,
        "timeline_seconds": cursor / sample_rate,
        "rhythm": {
            "policy_version": policy.version,
            "boundary_count": len(boundaries),
            "cut_transition_count": sum(
                boundary.crosses_cut for boundary in boundaries
            ),
            "speaker_change_count": sum(
                boundary.speaker_change for boundary in boundaries
            ),
            "pause_seconds_total": round(sum(pauses), 3),
            "tempo_adjustments": tempo_adjustments,
            "internal_silence_caps": silence_caps,
        },
    }


def _prepare_rhythm_clip(
    unit: RhythmAssemblyUnit,
    policy: RhythmPolicy,
) -> tuple[array[float], float, float, float, tuple[float, ...]]:
    """Trim one raw clip, cap internal silences, and apply short-reply tempo.

    Returns the kept samples, the kept head and tail silence in seconds, the
    applied tempo factor, and the seconds removed from over-long internal
    silences. FFmpeg ``atempo`` drops part of its first analysis window, so
    the trimmed clip is padded with silence before the tempo change and
    trimmed again afterwards; speech onsets stay intact.
    """
    sample_rate = _ASSEMBLY_SAMPLE_RATE
    clip = _decode_mono_f32(unit.clip_path)
    window = trim_window(clip, sample_rate, policy)
    kept = clip[window.start : window.end]
    removed: tuple[float, ...] = ()
    if window.voiced:
        kept, removed = cap_internal_silences(kept, sample_rate, policy)
    factor = policy.tempo_factor(
        unit.source_end_seconds - unit.source_start_seconds,
        len(kept) / sample_rate,
    )
    if factor == 1.0 or not window.voiced:
        return (
            kept,
            window.head_silence / sample_rate,
            window.tail_silence / sample_rate,
            1.0,
            removed,
        )
    padding = array("f", bytes(4 * round(_TEMPO_PADDING_SECONDS * sample_rate)))
    stretched = _apply_tempo(padding + kept + padding, factor)
    retrimmed = trim_window(stretched, sample_rate, policy)
    return (
        stretched[retrimmed.start : retrimmed.end],
        retrimmed.head_silence / sample_rate,
        retrimmed.tail_silence / sample_rate,
        factor,
        removed,
    )


def _required_path(path: Path | None) -> Path:
    if path is None:
        raise ValueError("Source Events require the source waveform")
    return path


def _decode_mono_f32(
    path: Path,
    *,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
) -> array[float]:
    arguments = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if start_seconds is not None and duration_seconds is not None:
        arguments.extend(
            ["-ss", f"{start_seconds:.6f}", "-t", f"{duration_seconds:.6f}"]
        )
    arguments.extend(
        [
            "-i",
            str(path),
            "-ar",
            str(_ASSEMBLY_SAMPLE_RATE),
            "-ac",
            "1",
            "-f",
            "f32le",
            "-",
        ]
    )
    return _f32le_samples(_run_binary(arguments))


def _apply_tempo(samples: array[float], factor: float) -> array[float]:
    """Speed speech up with FFmpeg ``atempo`` without changing its pitch."""
    return _f32le_samples(
        _run_binary(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "f32le",
                "-ar",
                str(_ASSEMBLY_SAMPLE_RATE),
                "-ac",
                "1",
                "-i",
                "-",
                "-filter:a",
                f"atempo={factor:.4f}",
                "-f",
                "f32le",
                "-",
            ],
            input_bytes=_f32le_bytes(samples),
        )
    )


def _f32le_samples(raw: bytes) -> array[float]:
    samples = array("f")
    samples.frombytes(raw[: len(raw) - len(raw) % 4])
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _f32le_bytes(samples: array[float]) -> bytes:
    if sys.byteorder == "little":
        return samples.tobytes()
    swapped = array("f", samples)
    swapped.byteswap()
    return swapped.tobytes()


def _assembly_sequence(
    source_wav: Path,
    speech_segments: list[dict[str, Any]],
    rendered_paths: list[Path],
    source_events: list[dict[str, Any]],
    work_root: Path,
) -> list[Path]:
    timeline: list[tuple[float, Path]] = []
    for index, (segment, path) in enumerate(
        zip(speech_segments, rendered_paths, strict=True),
        start=1,
    ):
        timeline.append((float(segment["source_start_seconds"]), path))
        pause_after = float(segment.get("pause_after_seconds", 0.0))
        if (
            not bool(segment.get("internal_split"))
            or segment.get("approved_boundary_kind")
            not in {"sentence", "pause"}
            or pause_after <= 0
        ):
            continue
        pause_path = work_root / f"internal-pause-{index:04d}.wav"
        _write_silence(pause_path, pause_after)
        timeline.append(
            (float(segment["source_end_seconds"]) + 1e-7, pause_path)
        )
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


def _write_silence(path: Path, duration_seconds: float) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            f"{duration_seconds:.6f}",
            "-c:a",
            "pcm_s24le",
            str(path),
        ]
    )


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


def _parent_segment_timings(
    render_plan: list[dict[str, Any]],
    unit_timings: list[tuple[float, float]],
    source_segment_ids: list[str],
) -> list[tuple[float, float]]:
    if len(render_plan) != len(unit_timings):
        raise ValueError("Render Plan timing coverage is incomplete")
    grouped: dict[str, list[tuple[float, float]]] = {
        segment_id: [] for segment_id in source_segment_ids
    }
    for unit, timing in zip(render_plan, unit_timings, strict=True):
        source_segment_id = str(unit["source_segment_id"])
        if source_segment_id not in grouped:
            raise ValueError("Render Plan timing parent is unavailable")
        grouped[source_segment_id].append(timing)
    if any(not timings for timings in grouped.values()):
        raise ValueError("Render Plan timing does not cover every segment")
    return [
        (grouped[segment_id][0][0], grouped[segment_id][-1][1])
        for segment_id in source_segment_ids
    ]


def _concatenate_audio_sequence(
    sources: list[Path],
    destination: Path,
    work_root: Path,
) -> None:
    if not sources:
        raise ValueError("No replacement render units are available")
    prepared: list[Path] = []
    for index, source in enumerate(sources, start=1):
        path = work_root / f"revision-unit-{index:04d}.wav"
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
                "1",
                "-c:a",
                "pcm_s24le",
                str(path),
            ]
        )
        prepared.append(path)
    concat = work_root / "revision-units.txt"
    concat.write_text(
        "".join(f"file '{_concat_path(path)}'\n" for path in prepared),
        encoding="utf-8",
    )
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
            str(destination),
        ]
    )


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
    # Only measured values are reported. Timeline continuity is measured by
    # the render that owns the timings; Source Language Leakage comes from
    # the Listening Audit; nothing measures boundary artifacts, so the field
    # is no longer reported.
    return {
        "duration_seconds": float(probe["format"]["duration"]),
        "quality": {
            **encoded_audio_fields(probe),
            "pre_encode_loudness_lufs": float(pre_encode["input_i"]),
            "pre_encode_true_peak_dbtp": float(pre_encode["input_tp"]),
            "loudness_lufs": float(final["input_i"]),
            "true_peak_dbtp": float(final["input_tp"]),
            "clipping": float(final["input_tp"]) > _CLIPPING_MAX_DBTP,
            "rendered_segments": rendered_segment_count,
        },
    }


def encoded_audio_fields(probe: Mapping[str, Any]) -> dict[str, Any]:
    """Read codec, sample rate, channels, and bitrate from an ffprobe result.

    ``bitrate_kbps`` is the measured average audio bitrate. The AAC encoder
    targets 128 kbps but spends about 2 kbps on digital silence, so speech
    with pauses measures below the target (about 117 kbps).
    """
    streams = probe.get("streams")
    stream: Mapping[str, Any] = next(
        (
            item
            for item in (streams if isinstance(streams, list) else [])
            if isinstance(item, Mapping) and item.get("codec_type") == "audio"
        ),
        {},
    )
    codec_name = str(stream.get("codec_name") or "")
    profile = str(stream.get("profile") or "")
    format_info = probe.get("format")
    bit_rate = stream.get("bit_rate") or (
        format_info.get("bit_rate") if isinstance(format_info, Mapping) else None
    )
    return {
        "codec": (
            f"AAC-{profile}"
            if codec_name == "aac" and profile
            else codec_name.upper()
        ),
        "sample_rate_hz": _probe_integer(stream.get("sample_rate")),
        "channels": _probe_integer(stream.get("channels")),
        "bitrate_kbps": round(_probe_integer(bit_rate) / 1000),
    }


def _probe_integer(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


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


def _run_binary(
    arguments: list[str],
    *,
    input_bytes: bytes | None = None,
) -> bytes:
    return subprocess.run(
        arguments,
        input=input_bytes,
        check=True,
        capture_output=True,
    ).stdout


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
