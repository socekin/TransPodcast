"""Listening Audit orchestration and the D3 retry inside V19/V20 renders.

Real FFmpeg assembles small synthetic clips. Every remote call (TTS, ASR,
and voiceprints) is a fake; no test reaches Modal or runs a model.
"""

from __future__ import annotations

import io
import json
import shutil
import wave
from pathlib import Path
from typing import Any

import pytest

from tests.test_rhythm_assembly import _SEGMENTS, _RenderFixture, _write_clip
from transpodcast import modal_pipeline
from transpodcast.listening_audit_runner import measure_clip
from transpodcast.pipeline_versions import (
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V19,
    SPEECH_PIPELINE_V20,
)
from transpodcast.unit_clip_store import UnitClipManifestReference, UnitClipStore

_SPEAKER_VOICE = {
    "speaker-01": [1.0, 0.0, 0.0, 0.0],
    "speaker-02": [0.0, 1.0, 0.0, 0.0],
}
_TARGETS = {segment_id: text for segment_id, _, _, _, text in _SEGMENTS}
_SPEAKERS = {segment_id: speaker for segment_id, speaker, _, _, _ in _SEGMENTS}


class _AuditFixture(_RenderFixture):
    """Fake TTS and audit models keyed by the clip each unit produced."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        pipeline_version: str = SPEECH_PIPELINE_V19,
    ) -> None:
        super().__init__(tmp_path, monkeypatch, pipeline_version)
        self.calls: list[dict[str, Any]] = []
        self.targets = dict(_TARGETS)
        self.clip_owner: dict[bytes, tuple[str, str]] = {}
        self.transcripts: dict[tuple[str, str], str] = {}
        self.speech_seconds: dict[tuple[str, str], float] = {}
        self.fail_methods: set[str] = set()
        monkeypatch.setattr(modal_pipeline, "_remote_method", self.fake_remote)

    def clip(self, path: Path, unit_id: str, variant: str, index: int) -> Path:
        _write_clip(
            path,
            speech_seconds=self.speech_seconds.get((unit_id, variant), 0.6),
            frequency=200.0 + 40 * index + (15 if variant != "first" else 0),
        )
        self.clip_owner[measure_clip(path).wav_bytes] = (unit_id, variant)
        return path

    def fake_render_sharded(
        self,
        _source_wav: Path,
        _checksum: str,
        _references: dict[str, bytes],
        plan: list[dict[str, Any]],
        rendered_root: Path,
        **_keywords: Any,
    ) -> dict[str, Any]:
        self.calls.append({"method": "render_shard", "units": len(plan)})
        metadata = []
        for index, unit in enumerate(plan, start=1):
            path = self.clip(
                rendered_root / f"segment-{index:04d}.wav",
                unit["segment_id"],
                "first",
                index,
            )
            metadata.append(
                {
                    "segment_id": unit["segment_id"],
                    "path": str(path),
                    "sha256": "fixture",
                    "duration_seconds": 1.15,
                    "emotion_reference_start_seconds": 0.0,
                    "emotion_reference_end_seconds": 1.0,
                    "speech_text_sha256": f"text-{index}",
                }
            )
        return {
            "segments": metadata,
            "model_id": "fixture-indextts",
            "model_revision": "fixture-revision",
            "seed": 42,
            "emotion_alpha": 0.8,
            "random_sampling": False,
            "total_inference_seconds": 0.0,
        }

    def fake_remote(
        self,
        app: str,
        class_name: str,
        method: str,
        *arguments: Any,
        **keywords: Any,
    ) -> dict[str, Any]:
        if method in self.fail_methods:
            self.calls.append({"method": method, "failed": True})
            raise RuntimeError(f"{class_name}.{method} is not deployed")
        if method == "transcribe_clips":
            clips: dict[str, bytes] = arguments[0]
            self.calls.append(
                {
                    "method": method,
                    "app": app,
                    "class": class_name,
                    "language": keywords.get("language"),
                    "clips": clips,
                }
            )
            transcripts = {}
            for name, content in clips.items():
                owner = self.clip_owner[content]
                transcripts[name] = self.transcripts.get(owner, self.targets[owner[0]])
            return {
                "asr_model_id": "Qwen/Qwen3-ASR-1.7B",
                "asr_model_revision": "fixture",
                "transcripts": transcripts,
                "gpu_name": "fixture-L40S",
                "inference_seconds": 2.0,
            }
        if method == "compare":
            clips = arguments[0]
            self.calls.append(
                {"method": method, "app": app, "class": class_name, "clips": clips}
            )
            return {
                "model_id": "nvidia/speakerverification_en_titanet_large",
                "model_revision": "fixture",
                "embeddings": {
                    name: _SPEAKER_VOICE[_SPEAKERS[self.clip_owner[content][0]]]
                    for name, content in clips.items()
                },
                "gpu_name": "fixture-L40S",
                "inference_seconds": 1.0,
            }
        assert method == "render"
        plan = arguments[2]
        seed = int(keywords["seed"])
        variant = (
            "retry" if seed == modal_pipeline.LISTENING_AUDIT_RETRY_SEED else "revision"
        )
        self.calls.append(
            {
                "method": method,
                "units": [unit["segment_id"] for unit in plan],
                "seed": seed,
            }
        )
        segments = []
        for unit in plan:
            index = [item[0] for item in _SEGMENTS].index(unit["segment_id"]) + 1
            path = self.clip(
                self.tmp_path / f"remote-{variant}-{unit['segment_id']}.wav",
                unit["segment_id"],
                variant,
                index,
            )
            segments.append(
                {
                    "segment_id": unit["segment_id"],
                    "audio": path.read_bytes(),
                    "duration_seconds": 1.15,
                    "emotion_reference_start_seconds": 2.0,
                    "emotion_reference_end_seconds": 3.0,
                    "speech_text_sha256": f"{variant}-text",
                }
            )
        return {
            "segments": segments,
            "model_id": "fixture-indextts",
            "model_revision": "fixture-revision",
            "seed": seed,
            "emotion_alpha": 0.8,
            "random_sampling": False,
            "total_inference_seconds": 3.0,
        }

    def methods(self) -> list[str]:
        return [call["method"] for call in self.calls]

    def render_audited(self, targets: list[str] | None = None) -> dict[str, Any]:
        targets = targets or [item[4] for item in _SEGMENTS]
        self.targets = {
            item[0]: text for item, text in zip(_SEGMENTS, targets, strict=True)
        }
        self.monkeypatch.setattr(
            modal_pipeline, "_render_sharded", self.fake_render_sharded
        )
        return modal_pipeline.render(
            self.request(targets, {"listening_audit": True}),
            self.tmp_path / "complete",
        )

    def revise_audited(self, prior: dict[str, Any]) -> dict[str, Any]:
        prior_preview = self.tmp_path / "prior-preview.m4a"
        shutil.copyfile(prior["publication_audio_path"], prior_preview)
        targets = [self.targets[item[0]] for item in _SEGMENTS]
        targets[2] = "改过的回答。"
        self.targets["seg-0003"] = targets[2]
        return modal_pipeline.render(
            self.request(
                targets,
                {
                    "listening_audit": True,
                    "scope": "segment",
                    "segment_ordinal": 3,
                    "prior_preview_audio_path": str(prior_preview),
                    "prior_render": {
                        "segment_timings": [
                            [item["start_seconds"], item["end_seconds"]]
                            for item in prior["segment_timings"]
                        ],
                        "render_settings_json": json.dumps(prior["render_settings"]),
                    },
                },
            ),
            self.tmp_path / "revision",
        )


def _audit(result: dict[str, Any]) -> dict[str, Any]:
    payload = result["render_settings"]["listening_audit"]
    assert isinstance(payload, dict)
    return payload


def _wav_format(content: bytes) -> tuple[int, int, int]:
    with wave.open(io.BytesIO(content), "rb") as handle:
        return handle.getframerate(), handle.getnchannels(), handle.getsampwidth()


@pytest.mark.parametrize("pipeline_version", [SPEECH_PIPELINE_V19, SPEECH_PIPELINE_V20])
def test_complete_render_audits_every_clip_in_one_call_per_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_version: str,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch, pipeline_version)

    result = fixture.render_audited()

    assert fixture.methods() == ["render_shard", "transcribe_clips", "compare"]
    asr, voice = fixture.calls[1], fixture.calls[2]
    assert (asr["app"], asr["class"], asr["language"]) == (
        "transpodcast-pipeline-qwen3-asr",
        "Qwen3Transcriber",
        "Chinese",
    )
    assert (voice["app"], voice["class"]) == (
        "transpodcast-pipeline-speaker-embeddings",
        "SpeakerEmbedder",
    )
    assert len(asr["clips"]) == len(voice["clips"]) == len(_SEGMENTS)
    assert asr["clips"] == voice["clips"]
    assert {_wav_format(content) for content in asr["clips"].values()} == {
        (16_000, 1, 2)
    }
    payload = _audit(result)
    assert payload["policy_version"] == "listening-audit-v1"
    assert payload["status"] == "complete"
    assert payload["scope"] == "episode"
    assert payload["unit_count"] == len(_SEGMENTS)
    assert payload["findings"] == []
    assert payload["hard_unit_ids"] == payload["blocking_unit_ids"] == []
    assert payload["retry"] is None
    usage = payload["usage"]
    assert usage["gpu_seconds"] == pytest.approx(3.0)
    assert usage["asr_gpu"] == usage["embedding_gpu"] == "fixture-L40S"
    assert [call["clip_count"] for call in usage["remote_calls"]] == [4, 4]
    assert usage["remote_calls"][0]["payload_bytes"] == sum(
        len(content) for content in asr["clips"].values()
    )
    assert result["quality"]["source_language_leakage"] is False
    assert result["quality"]["timeline_continuous"] is True
    assert "boundary_artifacts" not in result["quality"]
    assert result["render_settings"]["unit_seed_overrides"] == {}
    assert result["estimated_cost_usd"] > 0
    assert fixture.master_calls == 1


def test_soft_findings_are_listed_without_a_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch)
    targets = [item[4] for item in _SEGMENTS]
    targets[1] = "我们今天讨论的是一个很长的话题。"
    fixture.speech_seconds[("seg-0002", "first")] = 3.0
    fixture.transcripts[("seg-0002", "first")] = (
        "我们今天讨论的是一个很长的话题 so it is"
    )

    result = fixture.render_audited(targets)

    payload = _audit(result)
    assert fixture.methods() == ["render_shard", "transcribe_clips", "compare"]
    # Untranslated English also raises CER; both findings stay soft.
    assert sorted(
        (f["unit_id"], f["kind"], f["severity"]) for f in payload["findings"]
    ) == [
        ("seg-0002", "english_leakage", "soft"),
        ("seg-0002", "high_cer", "soft"),
    ]
    finding = next(f for f in payload["findings"] if f["kind"] == "english_leakage")
    assert finding["parent_segment_id"] == "seg-0002"
    assert finding["output_start_seconds"] == pytest.approx(
        result["rendered_segments"][1]["assembled_start_seconds"], abs=1e-3
    )
    assert payload["blocking_unit_ids"] == []
    assert result["quality"]["source_language_leakage"] is True


def test_hard_finding_rerenders_only_that_unit_once_with_a_new_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch)
    fixture.transcripts[("seg-0003", "first")] = "完全无关的内容"

    result = fixture.render_audited()

    assert fixture.methods() == [
        "render_shard",
        "transcribe_clips",
        "compare",
        "render",
        "transcribe_clips",
        "compare",
    ]
    retry_call = fixture.calls[3]
    assert retry_call["units"] == ["seg-0003"]
    assert retry_call["seed"] == 43
    # The second pass sends only the re-rendered clip.
    assert len(fixture.calls[4]["clips"]) == len(fixture.calls[5]["clips"]) == 1
    payload = _audit(result)
    assert payload["hard_unit_ids"] == payload["blocking_unit_ids"] == []
    assert payload["retry"]["status"] == "rendered"
    assert payload["retry"]["resolved_unit_ids"] == ["seg-0003"]
    assert {f["kind"] for f in payload["retry"]["initial_findings"]} >= {"high_cer"}
    assert payload["usage"]["retry_render_inference_seconds"] == pytest.approx(3.0)
    assert payload["usage"]["gpu_seconds"] == pytest.approx(9.0)
    settings = result["render_settings"]
    assert settings["unit_seed_overrides"] == {"seg-0003": 43}
    retried = result["rendered_segments"][2]
    assert retried["seed"] == 43 and retried["listening_audit_retry"] is True
    reference = UnitClipManifestReference.from_payload(settings["unit_clip_store"])
    assert reference is not None
    entries = UnitClipStore(fixture.store_root).load_manifest(reference)
    assert entries is not None
    assert entries[2].file == retried["unit_clip_file"]
    assert fixture.master_calls == 1


def test_hard_finding_that_survives_the_retry_blocks_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch)
    fixture.transcripts[("seg-0003", "first")] = "完全无关的内容"
    fixture.transcripts[("seg-0003", "retry")] = "还是不对的东西"

    payload = _audit(fixture.render_audited())

    assert fixture.methods().count("render") == 1
    assert payload["hard_unit_ids"] == payload["blocking_unit_ids"] == ["seg-0003"]
    assert payload["retry"]["resolved_unit_ids"] == []


def test_failed_retry_keeps_the_original_clip_and_reports_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch)
    fixture.transcripts[("seg-0003", "first")] = "完全无关的内容"
    fixture.fail_methods.add("render")

    result = fixture.render_audited()

    payload = _audit(result)
    assert payload["retry"]["status"] == "failed"
    assert "not deployed" in payload["retry"]["error"]
    assert payload["blocking_unit_ids"] == ["seg-0003"]
    assert result["render_settings"]["unit_seed_overrides"] == {}
    assert "listening_audit_retry" not in result["rendered_segments"][2]


def test_remote_failure_is_an_explicit_audit_unavailable_finding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch)
    fixture.fail_methods.update({"transcribe_clips", "compare"})

    result = fixture.render_audited()

    payload = _audit(result)
    assert payload["status"] == "unavailable"
    assert [(f["unit_id"], f["kind"], f["severity"]) for f in payload["findings"]] == [
        ("", "audit_unavailable", "soft"),
        ("", "audit_unavailable", "soft"),
    ]
    assert "not deployed" in payload["findings"][0]["detail"]
    assert payload["blocking_unit_ids"] == []
    assert payload["usage"]["remote_errors"].keys() == {"asr", "embedding"}
    assert "source_language_leakage" not in result["quality"]
    assert fixture.master_calls == 1


def test_local_energy_checks_still_trigger_the_retry_without_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch)
    fixture.fail_methods.add("transcribe_clips")
    fixture.speech_seconds[("seg-0003", "first")] = 9.0
    targets = [item[4] for item in _SEGMENTS]
    targets[2] = "这一段回答一共有十二个汉字。"

    result = fixture.render_audited(targets)

    payload = _audit(result)
    # One failed ASR attempt; the retry pass does not call it again.
    assert fixture.methods() == [
        "render_shard",
        "transcribe_clips",
        "compare",
        "render",
        "compare",
    ]
    assert payload["retry"]["initial_findings"][0]["kind"] == "duration_anomaly"
    assert payload["retry"]["resolved_unit_ids"] == ["seg-0003"]
    assert payload["status"] == "partial"
    assert payload["blocking_unit_ids"] == []


def test_segment_revision_audits_only_the_new_clip_and_keeps_retry_seeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch)
    fixture.transcripts[("seg-0002", "first")] = "完全无关的内容"
    prior = fixture.render_audited()
    assert prior["render_settings"]["unit_seed_overrides"] == {"seg-0002": 43}
    fixture.calls.clear()
    fixture.master_calls = 0

    result = fixture.revise_audited(prior)

    assert fixture.methods() == ["render", "transcribe_clips", "compare"]
    assert fixture.calls[0]["seed"] == 42
    assert len(fixture.calls[1]["clips"]) == len(fixture.calls[2]["clips"]) == 1
    settings = result["render_settings"]
    # The retried seg-0002 clip is reused, so the store path still applies.
    assert settings["assembly_source"] == "unit_clip_store"
    assert settings["unit_seed_overrides"] == {"seg-0002": 43}
    payload = _audit(result)
    assert payload["unit_count"] == len(_SEGMENTS)
    assert payload["status"] == "complete"
    assert payload["blocking_unit_ids"] == []
    assert fixture.master_calls == 1


def test_v17_render_ignores_the_audit_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _AuditFixture(tmp_path, monkeypatch, SPEECH_PIPELINE_V17)

    result = fixture.render_audited()

    assert fixture.methods() == ["render_shard"]
    settings = result["render_settings"]
    assert "listening_audit" not in settings
    assert "unit_seed_overrides" not in settings
    assert result["quality"]["timeline_continuous"] is True
    assert "source_language_leakage" not in result["quality"]
    assert "boundary_artifacts" not in result["quality"]


# --- C4: measured gate fields ------------------------------------------------


def test_mastering_reports_probed_fields_and_no_literal_flags(
    tmp_path: Path,
) -> None:
    import subprocess

    source = tmp_path / "master.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=6",
            "-af",
            "volume=4.0",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(source),
        ],
        check=True,
    )

    result = modal_pipeline._master_stereo_pcm(
        source,
        tmp_path / "publication.m4a",
        tmp_path,
        rendered_segment_count=1,
    )

    quality = result["quality"]
    assert quality["codec"] == "AAC-LC"
    assert quality["sample_rate_hz"] == 48_000
    assert quality["channels"] == 2
    assert 120 <= quality["bitrate_kbps"] <= 136
    for literal in (
        "timeline_continuous",
        "boundary_artifacts",
        "source_language_leakage",
    ):
        assert literal not in quality


def test_probed_fields_follow_the_encoded_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        modal_pipeline,
        "_loudness",
        lambda _path, **_keywords: {
            "input_i": "-16.0",
            "input_tp": "-1.2",
            "input_lra": "4.0",
            "input_thresh": "-26.0",
            "target_offset": "0.0",
        },
    )
    monkeypatch.setattr(modal_pipeline, "_normalize", lambda *_a, **_k: None)
    monkeypatch.setattr(modal_pipeline, "_encode_aac", lambda *_a: None)
    monkeypatch.setattr(
        modal_pipeline,
        "_probe",
        lambda _path: {
            "format": {"duration": "60.0", "bit_rate": "70000"},
            "streams": [
                {"codec_type": "video", "codec_name": "mjpeg"},
                {
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "sample_rate": "44100",
                    "channels": 1,
                },
            ],
        },
    )

    quality = modal_pipeline._master_stereo_pcm(
        tmp_path / "master.wav",
        tmp_path / "publication.m4a",
        tmp_path,
        rendered_segment_count=1,
    )["quality"]

    assert (quality["codec"], quality["sample_rate_hz"], quality["channels"]) == (
        "MP3",
        44_100,
        1,
    )
    assert quality["bitrate_kbps"] == 70


@pytest.mark.parametrize(
    ("timings", "duration", "expected"),
    [
        ([(0.0, 1.0), (1.2, 2.0)], 2.0, True),
        ([(0.0, 1.0), (0.5, 2.0)], 2.0, False),
        ([(0.0, 1.0), (1.0, 3.0)], 2.0, False),
        ([(0.0, 1.0), (1.0, 1.0)], 2.0, False),
        ([], 2.0, False),
    ],
)
def test_timeline_continuity_is_measured_from_segment_timings(
    timings: list[tuple[float, float]],
    duration: float,
    expected: bool,
) -> None:
    assert modal_pipeline.timeline_is_continuous(timings, duration) is expected


# --- Setup Readiness -----------------------------------------------------------


class _FakeRemoteClass:
    def __init__(self, methods: set[str]) -> None:
        self.methods = methods

    def hydrate(self) -> _FakeRemoteClass:
        return self

    def __call__(self) -> Any:
        methods = self.methods

        class _Instance:
            def __getattr__(self, name: str) -> Any:
                if name not in methods:
                    raise modal_pipeline.modal.exception.NotFoundError(name)
                return object()

        return _Instance()


def test_readiness_reports_missing_audit_methods_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployed = {
        "Qwen3Transcriber": {"transcribe"},
        "SpeakerEmbedder": {"compare", "measure_windows"},
    }

    def from_name(_app: str, class_name: str, **_keywords: Any) -> _FakeRemoteClass:
        return _FakeRemoteClass(deployed.get(class_name, set()))

    monkeypatch.setattr(modal_pipeline.modal.Cls, "from_name", from_name)

    response = modal_pipeline.readiness()

    assert response["ready"] is True
    assert response["listening_audit"] == {
        "available": False,
        "policy_version": "listening-audit-v1",
        "missing_methods": ["Qwen3Transcriber.transcribe_clips"],
    }
    deployed["Qwen3Transcriber"].add("transcribe_clips")
    assert modal_pipeline.readiness()["listening_audit"]["available"] is True
