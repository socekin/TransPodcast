"""Listening Audit orchestration for Speech Pipelines V19 and later.

The audit runs on the TransPodcast host inside the render operation. Each pass reads
the raw unit clips of the Unit Clip Store, measures speech and the longest
internal silence locally from frame energy (no model runs locally), and sends
every clip that still lacks evidence to the deployed Qwen3-ASR
``transcribe_clips`` and TitaNet ``compare`` methods in exactly one call each.
Scoring is ``transpodcast.listening_audit``.

Evidence is cached per raw clip hash next to the store, so a D3 re-render or a
Segment Revision sends only the clips that changed, and the per-Speaker voice
centroids still cover every unit. A remote failure or a missing clip becomes
an explicit ``audit_unavailable`` finding; the local energy checks still run.
"""

from __future__ import annotations

import contextlib
import io
import json
import operator
import os
import re
import subprocess
import sys
import wave
from array import array
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from transpodcast.listening_audit import (
    DEFAULT_LISTENING_AUDIT_POLICY,
    AuditFinding,
    ListeningAuditPolicy,
    ListeningAuditResult,
    UnitObservation,
    audit_units,
)

LISTENING_AUDIT_EVIDENCE_VERSION = "listening-audit-evidence-v1"
AUDIT_LANGUAGE = "Chinese"
AUDIT_SAMPLE_RATE = 16_000
_FRAME_SECONDS = 0.01
_SILENCE_DBFS = -45.0
_ERROR_CHARACTERS = 300
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class AuditUnit:
    """One Render Plan unit and its raw clip, in plan order."""

    unit_id: str
    parent_segment_id: str
    speaker_id: str
    target_text: str
    clip_path: Path | None
    clip_sha256: str


@dataclass(frozen=True)
class ClipMeasurement:
    wav_bytes: bytes
    speech_seconds: float
    longest_internal_silence_seconds: float


class AuditRemote(Protocol):
    """The two deployed audit methods; each call receives every clip."""

    def transcribe(self, clips: dict[str, bytes]) -> Mapping[str, Any]: ...

    def embed(self, clips: dict[str, bytes]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class AuditPass:
    """One scored pass.

    ``status`` is ``complete`` when every unit has every measurement,
    ``unavailable`` when no remote evidence or no clip could be used, and
    ``partial`` otherwise.
    """

    result: ListeningAuditResult
    unavailable: tuple[AuditFinding, ...]
    parent_segments: Mapping[str, str]
    status: str
    audited_unit_count: int

    @property
    def hard_unit_ids(self) -> tuple[str, ...]:
        return self.result.hard_unit_ids

    def to_payload(self) -> dict[str, Any]:
        payload = self.result.to_payload()
        findings = [*self.result.findings, *self.unavailable]
        return {
            **payload,
            "status": self.status,
            "audited_unit_count": self.audited_unit_count,
            "findings": [
                {
                    **finding.to_payload(),
                    "parent_segment_id": self.parent_segments.get(
                        finding.unit_id
                    ),
                }
                for finding in findings
            ],
        }


def measure_clip(path: Path) -> ClipMeasurement:
    """Decode one raw clip to 16 kHz mono and measure it on 10 ms frames.

    A frame is voiced when its RMS reaches -45 dBFS, as in the Rhythm Policy
    trim and the M3 calibration lab. Speech spans the first to the last voiced
    frame; the longest internal silence is the longest unvoiced run between
    voiced frames.
    """
    raw = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ar",
            str(AUDIT_SAMPLE_RATE),
            "-ac",
            "1",
            "-f",
            "s16le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    pcm = raw[: len(raw) - len(raw) % 2]
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    frame = round(AUDIT_SAMPLE_RATE * _FRAME_SECONDS)
    threshold = frame * 32768.0**2 * 10 ** (_SILENCE_DBFS / 10)
    voiced = [
        index
        for index in range(len(samples) // frame)
        if sum(
            map(
                operator.mul,
                samples[index * frame : (index + 1) * frame],
                samples[index * frame : (index + 1) * frame],
            )
        )
        >= threshold
    ]
    speech_seconds = (
        (voiced[-1] - voiced[0] + 1) * _FRAME_SECONDS if voiced else 0.0
    )
    longest_gap = max(
        (following - current - 1 for current, following in zip(
            voiced, voiced[1:], strict=False
        )),
        default=0,
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(AUDIT_SAMPLE_RATE)
        handle.writeframes(pcm)
    return ClipMeasurement(
        wav_bytes=buffer.getvalue(),
        speech_seconds=round(speech_seconds, 3),
        longest_internal_silence_seconds=round(longest_gap * _FRAME_SECONDS, 3),
    )


@dataclass
class _Evidence:
    transcript: str | None = None
    voiceprint: tuple[float, ...] | None = None


@dataclass
class ListeningAuditSession:
    """Runs the audit passes of one render and accumulates measured usage.

    A remote method that failed once is not called again in the same render;
    later passes reuse cached evidence and report the gap explicitly.
    """

    evidence_root: Path
    remote: AuditRemote
    policy: ListeningAuditPolicy = DEFAULT_LISTENING_AUDIT_POLICY
    measure: Callable[[Path], ClipMeasurement] = measure_clip
    remote_errors: dict[str, str] = field(default_factory=dict)
    usage: dict[str, Any] = field(
        default_factory=lambda: {
            "asr_inference_seconds": 0.0,
            "embedding_inference_seconds": 0.0,
            "asr_gpu": None,
            "embedding_gpu": None,
            "asr_model": None,
            "embedding_model": None,
            "remote_calls": [],
        }
    )

    def run(self, units: Sequence[AuditUnit]) -> AuditPass:
        measurements: dict[str, ClipMeasurement] = {}
        evidence: dict[str, _Evidence] = {}
        unavailable: list[AuditFinding] = []
        for unit in units:
            if unit.clip_path is None:
                unavailable.append(
                    AuditFinding(
                        unit.unit_id,
                        "audit_unavailable",
                        "soft",
                        "raw unit clip is unavailable",
                    )
                )
                continue
            try:
                measurements[unit.unit_id] = self.measure(unit.clip_path)
            except (OSError, subprocess.CalledProcessError) as error:
                unavailable.append(
                    AuditFinding(
                        unit.unit_id,
                        "audit_unavailable",
                        "soft",
                        "raw unit clip could not be decoded: "
                        + type(error).__name__,
                    )
                )
                continue
            evidence[unit.unit_id] = self._read_evidence(unit.clip_sha256)
        measured = [unit for unit in units if unit.unit_id in measurements]
        names = {
            unit.unit_id: f"clip-{index:05d}"
            for index, unit in enumerate(measured, start=1)
        }
        need_text = [u for u in measured if evidence[u.unit_id].transcript is None]
        need_voice = [u for u in measured if evidence[u.unit_id].voiceprint is None]
        calls: dict[str, Callable[[], Mapping[str, Any]]] = {}
        if need_text and "asr" not in self.remote_errors:
            text_clips = {
                names[u.unit_id]: measurements[u.unit_id].wav_bytes
                for u in need_text
            }
            calls["asr"] = lambda: self._call("asr", text_clips)
        if need_voice and "embedding" not in self.remote_errors:
            voice_clips = {
                names[u.unit_id]: measurements[u.unit_id].wav_bytes
                for u in need_voice
            }
            calls["embedding"] = lambda: self._call("embedding", voice_clips)
        responses: dict[str, Mapping[str, Any]] = {}
        if calls:
            with ThreadPoolExecutor(max_workers=len(calls)) as pool:
                futures = {name: pool.submit(call) for name, call in calls.items()}
                for name, future in futures.items():
                    try:
                        responses[name] = future.result()
                    except Exception as error:
                        # Any remote failure becomes audit_unavailable.
                        self.remote_errors[name] = (
                            f"{type(error).__name__}: {error}"[:_ERROR_CHARACTERS]
                        )
        transcripts = _mapping(responses.get("asr"), "transcripts")
        voiceprints = _mapping(responses.get("embedding"), "embeddings")
        for unit in measured:
            name = names[unit.unit_id]
            item = evidence[unit.unit_id]
            changed = False
            if item.transcript is None and isinstance(transcripts.get(name), str):
                item.transcript = str(transcripts[name])
                changed = True
            if item.voiceprint is None:
                vector = _vector(voiceprints.get(name))
                if vector is not None:
                    item.voiceprint = vector
                    changed = True
            if changed:
                self._write_evidence(unit.clip_sha256, item)
        unavailable.extend(self._missing_evidence(measured, evidence))
        observations = [
            UnitObservation(
                unit_id=unit.unit_id,
                speaker_label=unit.speaker_id,
                target_text=unit.target_text,
                transcript=evidence[unit.unit_id].transcript,
                speech_seconds=measurements[unit.unit_id].speech_seconds,
                longest_internal_silence_seconds=(
                    measurements[unit.unit_id].longest_internal_silence_seconds
                ),
                voiceprint=evidence[unit.unit_id].voiceprint,
            )
            for unit in measured
        ]
        has_remote_evidence = any(
            item.transcript is not None or item.voiceprint is not None
            for item in evidence.values()
        )
        status = (
            "complete"
            if not unavailable
            else "partial"
            if measured and has_remote_evidence
            else "unavailable"
        )
        return AuditPass(
            result=audit_units(observations, self.policy),
            unavailable=tuple(unavailable),
            parent_segments={u.unit_id: u.parent_segment_id for u in units},
            status=status,
            audited_unit_count=len(units),
        )

    def usage_payload(self) -> dict[str, Any]:
        gpu_seconds = float(self.usage["asr_inference_seconds"]) + float(
            self.usage["embedding_inference_seconds"]
        )
        return {
            **self.usage,
            "asr_inference_seconds": round(self.usage["asr_inference_seconds"], 3),
            "embedding_inference_seconds": round(
                self.usage["embedding_inference_seconds"], 3
            ),
            "gpu_seconds": round(gpu_seconds, 3),
            "remote_errors": dict(self.remote_errors),
        }

    def _call(self, kind: str, clips: dict[str, bytes]) -> Mapping[str, Any]:
        response = (
            self.remote.transcribe(clips)
            if kind == "asr"
            else self.remote.embed(clips)
        )
        if not isinstance(response, Mapping):
            raise TypeError("Listening Audit response must be an object")
        self.usage[f"{kind}_inference_seconds"] += float(
            response.get("inference_seconds", 0.0)
        )
        self.usage[f"{kind}_gpu"] = response.get("gpu_name")
        model = response.get("asr_model_id" if kind == "asr" else "model_id")
        revision = response.get(
            "asr_model_revision" if kind == "asr" else "model_revision"
        )
        if model:
            self.usage[f"{kind}_model"] = f"{model}@{revision}" if revision else model
        self.usage["remote_calls"].append(
            {
                "method": "transcribe_clips" if kind == "asr" else "compare",
                "clip_count": len(clips),
                "payload_bytes": sum(len(value) for value in clips.values()),
            }
        )
        return response

    def _missing_evidence(
        self,
        measured: Sequence[AuditUnit],
        evidence: Mapping[str, _Evidence],
    ) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        for kind, label, attribute in (
            ("asr", "back-transcription", "transcript"),
            ("embedding", "voice consistency", "voiceprint"),
        ):
            missing = [
                unit.unit_id
                for unit in measured
                if getattr(evidence[unit.unit_id], attribute) is None
            ]
            if not missing:
                continue
            reason = self.remote_errors.get(kind, "the response omitted the clip")
            if len(missing) == len(measured):
                findings.append(
                    AuditFinding(
                        "",
                        "audit_unavailable",
                        "soft",
                        f"{label} unavailable for every unit: {reason}",
                    )
                )
                continue
            findings.extend(
                AuditFinding(
                    unit_id,
                    "audit_unavailable",
                    "soft",
                    f"{label} unavailable: {reason}",
                )
                for unit_id in missing
            )
        return findings

    def _evidence_path(self, clip_sha256: str) -> Path | None:
        if not _SHA256.fullmatch(clip_sha256):
            return None
        return self.evidence_root / f"{clip_sha256}.json"

    def _read_evidence(self, clip_sha256: str) -> _Evidence:
        path = self._evidence_path(clip_sha256)
        if path is None or not path.is_file():
            return _Evidence()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _Evidence()
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != LISTENING_AUDIT_EVIDENCE_VERSION
            or payload.get("language") != AUDIT_LANGUAGE
        ):
            return _Evidence()
        transcript = payload.get("transcript")
        with contextlib.suppress(OSError):
            os.utime(path)
        return _Evidence(
            transcript=transcript if isinstance(transcript, str) else None,
            voiceprint=_vector(payload.get("voiceprint")),
        )

    def _write_evidence(self, clip_sha256: str, evidence: _Evidence) -> None:
        path = self._evidence_path(clip_sha256)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": LISTENING_AUDIT_EVIDENCE_VERSION,
                    "language": AUDIT_LANGUAGE,
                    "transcript": evidence.transcript,
                    "voiceprint": (
                        list(evidence.voiceprint)
                        if evidence.voiceprint is not None
                        else None
                    ),
                    "asr_model": self.usage.get("asr_model"),
                    "embedding_model": self.usage.get("embedding_model"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)


def _mapping(response: Mapping[str, Any] | None, key: str) -> Mapping[str, Any]:
    if response is None:
        return {}
    value = response.get(key)
    return value if isinstance(value, Mapping) else {}


def _vector(value: object) -> tuple[float, ...] | None:
    if not isinstance(value, list | tuple) or not value:
        return None
    try:
        return tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
