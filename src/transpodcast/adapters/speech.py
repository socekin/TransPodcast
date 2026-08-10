from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from transpodcast.domain import (
    AnalyzedSpeaker,
    AudioQualityReport,
    CanonicalSegment,
    SourceAnalysisResult,
    SourceArchive,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.errors import (
    ProviderExecutionError,
    SpendGuardrailReached,
    TransientProviderError,
)

_READINESS_CACHE: dict[tuple[str, ...], tuple[float, bool]] = {}


def _audio_quality_report(quality: dict[str, Any]) -> AudioQualityReport:
    return AudioQualityReport(
        codec=str(quality.get("codec", "")),
        sample_rate_hz=int(quality.get("sample_rate_hz", 0)),
        channels=int(quality.get("channels", 0)),
        bitrate_kbps=int(quality.get("bitrate_kbps", 0)),
        loudness_lufs=float(quality.get("loudness_lufs", 0)),
        true_peak_dbtp=float(quality.get("true_peak_dbtp", 0)),
        pre_encode_loudness_lufs=(
            float(quality["pre_encode_loudness_lufs"])
            if quality.get("pre_encode_loudness_lufs") is not None
            else None
        ),
        pre_encode_true_peak_dbtp=(
            float(quality["pre_encode_true_peak_dbtp"])
            if quality.get("pre_encode_true_peak_dbtp") is not None
            else None
        ),
        clipping=bool(quality.get("clipping", True)),
        rendered_segments=int(quality.get("rendered_segments", 0)),
        timeline_continuous=bool(quality.get("timeline_continuous", False)),
        boundary_artifacts=bool(quality.get("boundary_artifacts", True)),
        source_language_leakage=bool(quality.get("source_language_leakage", True)),
    )


def _routing_json(response: dict[str, Any]) -> str:
    raw = response.get("routing")
    if not isinstance(raw, dict):
        return "{}"
    attempts = raw.get("attempts")
    safe_attempts = []
    if isinstance(attempts, list):
        for item in attempts:
            if not isinstance(item, dict):
                continue
            safe_attempts.append(
                {
                    key: item[key]
                    for key in (
                        "profile",
                        "outcome",
                        "reason",
                        "remaining_usd",
                    )
                    if isinstance(item.get(key), (str, int, float))
                }
            )
    safe = {
        key: raw[key]
        for key in (
            "operation",
            "selected_profile",
            "correlation_id",
            "stage",
        )
        if isinstance(raw.get(key), str)
    }
    for key in ("processing_run_id", "processing_attempt_id"):
        if isinstance(raw.get(key), int):
            safe[key] = raw[key]
    safe["attempts"] = safe_attempts
    return json.dumps(safe, ensure_ascii=False, sort_keys=True)


class ModalCommandAnalysisAdapter:
    def __init__(self, command: str) -> None:
        self.command = shlex.split(command)

    def readiness(self) -> bool:
        return _pipeline_readiness(self.command)

    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        if not self.readiness():
            raise RuntimeError("Modal analysis pipeline command is not configured")
        with TemporaryDirectory(prefix="transpodcast-analysis-") as temp:
            root = Path(temp)
            source_path = root / f"source.{archive.extension}"
            source_path.write_bytes(archive.content)
            request_path = root / "request.json"
            response_path = root / "response.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source": {
                            "youtube_video_id": candidate.youtube_video_id,
                            "title": candidate.title,
                            "description": candidate.description,
                            "language": candidate.language,
                            "audio_path": str(source_path),
                            "checksum": archive.checksum,
                        },
                        "analysis": {
                            "detect_speakers": True,
                            "detect_crosstalk": True,
                            "detect_source_events": True,
                            "align_words": True,
                        },
                        "routing_context": {
                            "processing_run_id": candidate.processing_run_id,
                            "processing_attempt_id": (candidate.processing_attempt_id),
                            "correlation_id": (candidate.processing_correlation_id),
                            "stage": "source_analysis",
                        },
                    }
                ),
                encoding="utf-8",
            )
            response = _run_pipeline_command(
                self.command,
                operation="analyze",
                request_path=request_path,
                response_path=response_path,
            )
            segments = tuple(
                CanonicalSegment(
                    segment_id=str(item["segment_id"]),
                    speaker_label=str(item["speaker_label"]),
                    start_seconds=float(item["start_seconds"]),
                    end_seconds=float(item["end_seconds"]),
                    text=str(item["text"]),
                    words=tuple(
                        (
                            str(word["text"]),
                            float(word["start_seconds"]),
                            float(word["end_seconds"]),
                        )
                        for word in item.get("words", [])
                    ),
                    overlap=bool(item.get("overlap", False)),
                    events=tuple(str(value) for value in item.get("events", [])),
                    confidence=float(item.get("confidence", 1.0)),
                    review_state=str(item.get("review_state", "accepted")),
                    # Legacy Modal responses may still carry the retired
                    # sponsor flag. Content Editing uses a separate semantic
                    # artifact, so the compatibility field is normalized.
                    omitted_as_sponsor=False,
                )
                for item in response["segments"]
            )
            if not segments:
                raise ValueError("Canonical Transcript has no speech segments")
            return SourceAnalysisResult(
                segments=segments,
                pipeline_version=str(response["pipeline_version"]),
                diarization_model=str(response["diarization_model"]),
                diarization_model_revision=str(
                    response.get("diarization_model_revision", "")
                ),
                transcription_model=str(response["transcription_model"]),
                alignment_model=str(response["alignment_model"]),
                source_events=tuple(
                    (
                        str(item["kind"]),
                        float(item["start_seconds"]),
                        float(item["end_seconds"]),
                    )
                    for item in response.get("source_events", [])
                ),
                background_regions=tuple(
                    (
                        float(item["start_seconds"]),
                        float(item["end_seconds"]),
                    )
                    for item in response.get("background_regions", [])
                ),
                speakers=tuple(
                    AnalyzedSpeaker(
                        local_label=str(item["local_label"]),
                        reference_audio=Path(
                            str(item["reference_audio_path"])
                        ).read_bytes(),
                        embedding=tuple(
                            float(value) for value in item.get("embedding", [])
                        ),
                        confidence=float(item.get("confidence", 0.0)),
                    )
                    for item in response.get("speakers", [])
                ),
                raw_diarization_turns=tuple(
                    (
                        str(item["speaker"]),
                        float(item["start_seconds"]),
                        float(item["end_seconds"]),
                    )
                    for item in response.get(
                        "raw_diarization_turns",
                        [],
                    )
                ),
                full_asr_text=str(response.get("full_asr_text", "")),
                aligned_words=tuple(
                    (
                        str(item["text"]),
                        float(item["start_seconds"]),
                        float(item["end_seconds"]),
                    )
                    for item in response.get("aligned_words", [])
                ),
                boundary_stabilization_json=json.dumps(
                    response.get("boundary_stabilization", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                source_canonicalization_json=json.dumps(
                    response.get("source_canonicalization", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                speaker_consolidation_json=json.dumps(
                    response.get("speaker_consolidation", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                boundary_adjudication_json=json.dumps(
                    response.get("boundary_adjudication", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                checksum=str(response["checksum"]),
                estimated_cost_usd=float(response.get("estimated_cost_usd", 0.0)),
                call_seconds=tuple(
                    sorted(
                        (str(name), float(seconds))
                        for name, seconds in response.get("call_seconds", {}).items()
                    )
                ),
                routing_json=_routing_json(response),
            )


class ModalCommandSpeechAdapter:
    def __init__(self, command: str) -> None:
        self.command = shlex.split(command)

    def readiness(self) -> bool:
        return _pipeline_readiness(self.command)

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        return self._render(
            candidate,
            translation,
            segment_ordinal=None,
            prior_preview_key=None,
        )

    def render_segment(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
        segment_ordinal: int,
        prior_preview_key: str,
    ) -> SpeechResult:
        return self._render(
            candidate,
            translation,
            segment_ordinal=segment_ordinal,
            prior_preview_key=prior_preview_key,
        )

    def remaster(self, speech: SpeechResult) -> SpeechResult:
        with TemporaryDirectory(prefix="transpodcast-remaster-") as temp:
            root = Path(temp)
            source_path = root / "source-publication.m4a"
            source_path.write_bytes(speech.publication_audio)
            request_path = root / "request.json"
            response_path = root / "response.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "publication_audio": {
                            "audio_path": str(source_path),
                            "rendered_segments": (speech.quality.rendered_segments),
                            "pipeline_version": speech.pipeline_version,
                        },
                    }
                ),
                encoding="utf-8",
            )
            response = _run_pipeline_command(
                self.command,
                operation="remaster",
                request_path=request_path,
                response_path=response_path,
            )
            media_path = Path(str(response["publication_audio_path"]))
            return replace(
                speech,
                publication_audio=media_path.read_bytes(),
                duration_seconds=int(response["duration_seconds"]),
                pipeline_version=str(response["pipeline_version"]),
                quality=_audio_quality_report(response.get("quality") or {}),
            )

    def _render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
        *,
        segment_ordinal: int | None,
        prior_preview_key: str | None,
    ) -> SpeechResult:
        if not self.readiness():
            raise RuntimeError("Modal speech pipeline command is not configured")
        with TemporaryDirectory(prefix="transpodcast-modal-") as temp:
            root = Path(temp)
            source_path: Path | None = None
            if candidate.source_archive is not None:
                source_path = root / f"source.{candidate.source_archive.extension}"
                source_path.write_bytes(candidate.source_archive.content)
            prior_preview_path: Path | None = None
            if candidate.prior_preview_audio is not None:
                prior_preview_path = root / "prior-preview.m4a"
                prior_preview_path.write_bytes(candidate.prior_preview_audio)
            request_path = root / "request.json"
            response_path = root / "response.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source": {
                            "youtube_video_id": candidate.youtube_video_id,
                            "url": candidate.source_url,
                            "title": candidate.title,
                            "language": candidate.language,
                            "audio_path": (
                                str(source_path) if source_path is not None else None
                            ),
                            "analysis": (
                                json.loads(candidate.analysis_json)
                                if candidate.analysis_json
                                else None
                            ),
                        },
                        "translation": {
                            "target_locale": translation.target_locale,
                            "segments": translation.segments,
                        },
                        "rendering": {
                            "renderer": "IndexTTS2",
                            "emotion_alpha": 0.8,
                            "random_sampling": False,
                            "scope": (
                                "segment"
                                if segment_ordinal is not None
                                else "complete_episode"
                            ),
                            "segment_ordinal": segment_ordinal,
                            "prior_preview_key": prior_preview_key,
                            "prior_preview_audio_path": (
                                str(prior_preview_path)
                                if prior_preview_path is not None
                                else None
                            ),
                            "prior_render": (
                                json.loads(candidate.prior_render_json)
                                if candidate.prior_render_json
                                else None
                            ),
                        },
                        "publication_audio": {
                            "sample_rate_hz": 48000,
                            "channels": 2,
                            "codec": "AAC-LC",
                            "bitrate_kbps": 128,
                            "loudness_lufs": -16,
                            "true_peak_dbtp": -1.5,
                        },
                        "routing_context": {
                            "processing_run_id": candidate.processing_run_id,
                            "processing_attempt_id": (candidate.processing_attempt_id),
                            "correlation_id": (candidate.processing_correlation_id),
                            "stage": "render",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            response = _run_pipeline_command(
                self.command,
                operation="render",
                request_path=request_path,
                response_path=response_path,
            )
            media_path = Path(str(response["publication_audio_path"]))
            media = media_path.read_bytes()
            chapters = tuple(
                (int(item["start_seconds"]), str(item["title"]))
                for item in response.get("chapters", [])
            )
            return SpeechResult(
                publication_audio=media,
                duration_seconds=int(response["duration_seconds"]),
                chapters=chapters,
                pipeline_version=str(response["pipeline_version"]),
                quality=_audio_quality_report(response.get("quality") or {}),
                estimated_cost_usd=float(response.get("estimated_cost_usd", 0.0)),
                segment_timings=tuple(
                    (
                        float(item["start_seconds"]),
                        float(item["end_seconds"]),
                    )
                    for item in response.get("segment_timings", [])
                ),
                routing_json=_routing_json(response),
            )


def _run_pipeline_command(
    command: list[str],
    *,
    operation: str,
    request_path: Path,
    response_path: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            *command,
            "--operation",
            operation,
            "--request",
            str(request_path),
            "--output",
            str(response_path),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=24 * 60 * 60,
    )
    if completed.returncode != 0:
        error_tail = completed.stderr.strip()[-1000:]
        failure_response: dict[str, Any] = {}
        if response_path.exists():
            try:
                parsed = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                parsed = {}
            if isinstance(parsed, dict):
                failure_response = parsed
        routing_json = _routing_json(failure_response)
        if "[SPEND_GUARDRAIL]" in error_tail:
            raise SpendGuardrailReached(
                "TransPodcast Modal Environment limit reached",
                routing_json=routing_json,
            )
        if "[TRANSIENT]" in error_tail:
            raise TransientProviderError(
                "Modal speech capacity is temporarily unavailable",
                routing_json=routing_json,
            )
        raise ProviderExecutionError(
            "Modal speech pipeline failed",
            routing_json=routing_json,
        )
    value = json.loads(response_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Modal speech pipeline response must be an object")
    return value


def _pipeline_readiness(command: list[str]) -> bool:
    if not command or shutil.which(command[0]) is None:
        return False
    key = tuple(command)
    cached = _READINESS_CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < 300:
        return cached[1]
    with TemporaryDirectory(prefix="transpodcast-modal-readiness-") as temp:
        root = Path(temp)
        request_path = root / "request.json"
        response_path = root / "response.json"
        request_path.write_text("{}", encoding="utf-8")
        try:
            response = _run_pipeline_command(
                command,
                operation="readiness",
                request_path=request_path,
                response_path=response_path,
            )
            ready = response.get("ready") is True
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            ready = False
    _READINESS_CACHE[key] = (now, ready)
    return ready
