"""V19/V20 rhythm assembly, Unit Clip Store, and cache-identity split.

These tests use real FFmpeg on small synthetic clips, as the existing
assembly tests do, and replace only the remote TTS calls.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

import pytest

from tests.test_legacy_assembly_regression import (
    _SPEAKER_REFERENCES,
    _captured_output_prefix,
    _fixture_render_plan,
)
from transpodcast import modal_pipeline
from transpodcast.adapters import speech
from transpodcast.maintenance import MaintenanceManager
from transpodcast.modal_pipeline import (
    RhythmAssemblyUnit,
    assemble_rhythm_publication,
    rhythm_render_cache_identity,
)
from transpodcast.pipeline_versions import (
    SPEECH_PIPELINE_V17,
    SPEECH_PIPELINE_V19,
    SPEECH_PIPELINE_V20,
)
from transpodcast.rhythm import RHYTHM_POLICY
from transpodcast.unit_clip_store import (
    UnitClipManifestReference,
    UnitClipStore,
    unit_clip_store_root,
)

TTS_RATE = 22_050
FRAME = 1 / 48_000
TOLERANCE = 0.003


def _write_clip(
    path: Path,
    *,
    lead: float = 0.25,
    speech_seconds: float = 0.6,
    tail: float = 0.3,
    frequency: float = 220.0,
    rate: int = TTS_RATE,
) -> Path:
    frames = bytearray()
    for index in range(round((lead + speech_seconds + tail) * rate)):
        moment = index / rate
        value = (
            0.3 * math.sin(2 * math.pi * frequency * moment)
            if lead <= moment < lead + speech_seconds
            else 0.0
        )
        frames += int(value * 32767).to_bytes(2, "little", signed=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


def _gaps(timings: list[Any]) -> list[float]:
    return [
        float(following[0]) - float(current[1])
        for current, following in zip(timings, timings[1:], strict=False)
    ]


def _timing_pairs(result: dict[str, Any]) -> list[tuple[float, float]]:
    return [
        (item["start_seconds"], item["end_seconds"])
        for item in result["segment_timings"]
    ]


# --- Render cache identity --------------------------------------------------


def _identity(plan: list[dict[str, Any]], **overrides: Any) -> str:
    settings: dict[str, Any] = {
        "source_checksum": "fixture-source",
        "pipeline_version": SPEECH_PIPELINE_V19,
        "renderer_class": "IndexTTS2BatchRenderer",
        "emotion_alpha": 0.8,
    }
    settings.update(overrides)
    references = settings.pop("speaker_references", _SPEAKER_REFERENCES)
    return rhythm_render_cache_identity(plan, references, **settings)


@pytest.mark.parametrize(
    "assembly_change",
    [
        {"pause_before_seconds": 0.9},
        {"pause_after_seconds": 1.4},
        {"internal_split": False},
        {"approved_boundary_kind": "sentence"},
    ],
)
def test_rhythm_identity_ignores_assembly_only_fields(
    assembly_change: dict[str, Any],
) -> None:
    plan = _fixture_render_plan()
    changed = [dict(plan[0], **assembly_change), *plan[1:]]

    assert _identity(changed) == _identity(plan)


@pytest.mark.parametrize(
    "tts_change",
    [
        {"target_text": "Different words, "},
        {"speaker_id": "speaker-02"},
        {"source_end_seconds": 2.2},
        {"segment_id": "seg-0001--render-09"},
        {"emotion_reference_audio": b"new-emotion"},
    ],
)
def test_rhythm_identity_changes_with_tts_shaping_inputs(
    tts_change: dict[str, Any],
) -> None:
    plan = _fixture_render_plan()
    changed = [dict(plan[0], **tts_change), *plan[1:]]

    assert _identity(changed) != _identity(plan)


def test_rhythm_identity_changes_with_references_and_renderer_settings() -> None:
    plan = _fixture_render_plan()
    baseline = _identity(plan)

    assert (
        _identity(
            plan,
            speaker_references={
                **_SPEAKER_REFERENCES,
                "speaker-01": b"new-voice",
            },
        )
        != baseline
    )
    assert _identity(plan, emotion_alpha=0.6) != baseline
    assert (
        _identity(
            plan,
            pipeline_version=SPEECH_PIPELINE_V20,
            renderer_class="IndexTTS25BatchRenderer",
        )
        != baseline
    )
    # An unrelated Speaker reference does not shape this plan's units.
    assert (
        _identity(
            plan,
            speaker_references={**_SPEAKER_REFERENCES, "speaker-09": b"other"},
        )
        == baseline
    )


def test_explicit_emotion_reference_replaces_the_source_span() -> None:
    plan = _fixture_render_plan()
    moved = [*plan[:2], dict(plan[2], source_start_seconds=4.9)]

    assert _identity(moved) == _identity(plan)


@pytest.mark.parametrize(
    ("pipeline_version", "renderer_class"),
    [
        (SPEECH_PIPELINE_V19, "IndexTTS2BatchRenderer"),
        (SPEECH_PIPELINE_V20, "IndexTTS25BatchRenderer"),
    ],
)
def test_rhythm_render_cache_survives_pause_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pipeline_version: str,
    renderer_class: str,
) -> None:
    first = _fixture_render_plan()
    second = [
        dict(
            item,
            pause_before_seconds=0.9,
            pause_after_seconds=1.1,
            internal_split=False,
            approved_boundary_kind=None,
        )
        for item in first
    ]

    prefixes = {
        _captured_output_prefix(
            monkeypatch,
            tmp_path / name,
            plan,
            pipeline_version=pipeline_version,
            renderer_class=renderer_class,
        )
        for name, plan in (("first", first), ("second", second))
    }

    assert len(prefixes) == 1


def test_v17_render_cache_still_changes_with_pause_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _fixture_render_plan()
    second = [dict(item, pause_after_seconds=1.1) for item in first]

    prefixes = {
        _captured_output_prefix(
            monkeypatch,
            tmp_path / name,
            plan,
            pipeline_version=SPEECH_PIPELINE_V17,
            renderer_class="IndexTTS2BatchRenderer",
        )
        for name, plan in (("first", first), ("second", second))
    }

    assert len(prefixes) == 2


# --- Assembly -----------------------------------------------------------------


def test_assembly_inserts_policy_pauses_and_cut_transitions(
    tmp_path: Path,
) -> None:
    units = [
        RhythmAssemblyUnit(
            "a", _write_clip(tmp_path / "a.wav"), "speaker-01", 0.0, 1.0
        ),
        RhythmAssemblyUnit(
            "b", _write_clip(tmp_path / "b.wav"), "speaker-01", 1.3, 2.0
        ),
        RhythmAssemblyUnit(
            "c", _write_clip(tmp_path / "c.wav"), "speaker-02", 1.9, 2.6
        ),
        RhythmAssemblyUnit(
            "d", _write_clip(tmp_path / "d.wav"), "speaker-02", 40.2, 41.0
        ),
        RhythmAssemblyUnit(
            "e", _write_clip(tmp_path / "e.wav"), "speaker-01", 41.5, 42.3
        ),
    ]
    publication = tmp_path / "publication.m4a"

    result = assemble_rhythm_publication(
        units,
        publication,
        exclusions=[(3.0, 40.0)],
    )

    keep = RHYTHM_POLICY.head_keep_seconds + RHYTHM_POLICY.tail_keep_seconds
    assert _gaps(result["unit_timings"]) == pytest.approx(
        [
            0.3 - keep,  # same Speaker, source gap 0.3 s
            0.0,  # overlap at a change: 0.08 s is below the kept edges
            RHYTHM_POLICY.cut_pause_seconds - keep,  # Content Edit cut
            0.5 - keep,  # Speaker change, source gap 0.5 s
        ],
        abs=TOLERANCE,
    )
    durations = [end - start for start, end in result["unit_timings"]]
    assert durations == pytest.approx([0.7] * 5, abs=0.02)
    assert result["rhythm"]["cut_transition_count"] == 1
    assert result["rhythm"]["speaker_change_count"] == 2
    assert result["rhythm"]["tempo_adjustments"] == []
    assert publication.read_bytes()[4:8] == b"ftyp"
    assert result["quality"]["rendered_segments"] == 5
    assert -16.5 <= result["quality"]["loudness_lufs"] <= -15.5


def test_assembly_compresses_only_stretched_short_replies(tmp_path: Path) -> None:
    units = [
        RhythmAssemblyUnit(
            "question",
            _write_clip(tmp_path / "question.wav", speech_seconds=1.0),
            "speaker-01",
            0.0,
            2.0,
        ),
        RhythmAssemblyUnit(
            "reply",
            _write_clip(tmp_path / "reply.wav", speech_seconds=1.0),
            "speaker-02",
            2.3,
            2.8,
        ),
    ]

    result = assemble_rhythm_publication(units, tmp_path / "publication.m4a")

    assert result["rhythm"]["tempo_adjustments"] == [
        {"render_unit_id": "reply", "factor": 1.25}
    ]
    question, reply = (end - start for start, end in result["unit_timings"])
    assert question == pytest.approx(1.1, abs=0.02)
    # 1.0 s of speech at 1.25x plus the kept 40 ms head and 60 ms tail.
    assert reply == pytest.approx(0.8 + 0.1, abs=0.04)
    assert _gaps(result["unit_timings"]) == pytest.approx(
        [0.3 - 0.1],
        abs=TOLERANCE,
    )


def test_assembly_keeps_source_events_in_source_order(tmp_path: Path) -> None:
    source = _write_clip(
        tmp_path / "source.wav",
        lead=0.0,
        speech_seconds=6.0,
        tail=0.0,
        rate=24_000,
    )
    units = [
        RhythmAssemblyUnit(
            "a", _write_clip(tmp_path / "a.wav"), "speaker-01", 1.0, 2.0
        ),
        RhythmAssemblyUnit(
            "b", _write_clip(tmp_path / "b.wav"), "speaker-02", 3.0, 4.0
        ),
    ]
    events = [
        {"start_seconds": 2.2, "end_seconds": 2.7, "safe_to_preserve": True},
        {"start_seconds": 0.2, "end_seconds": 0.5, "safe_to_preserve": True},
        {"start_seconds": 2.0, "end_seconds": 3.0, "safe_to_preserve": False},
    ]

    result = assemble_rhythm_publication(
        units,
        tmp_path / "publication.m4a",
        source_events=events,
        source_wav=source,
    )

    first, second = result["unit_timings"]
    # The leading event precedes the first unit; the 0.5 s event follows it
    # directly, and the Speaker-change pause then precedes the next unit.
    assert first[0] == pytest.approx(0.3, abs=TOLERANCE)
    assert second[0] - first[1] == pytest.approx(0.5 + 1.0 - 0.1, abs=TOLERANCE)


# --- Unit Clip Store ------------------------------------------------------------


def test_store_root_prefers_a_pinned_copy(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"

    assert unit_clip_store_root(root, 42) == (
        root / "intermediates" / "processing-runs" / "42" / "unit-clips"
    )
    pinned = root / "pinned" / "processing-runs" / "42" / "unit-clips"
    pinned.mkdir(parents=True)
    assert unit_clip_store_root(root, 42) == pinned


def test_store_keeps_lossless_clips_and_an_immutable_manifest(
    tmp_path: Path,
) -> None:
    clip = _write_clip(tmp_path / "raw" / "unit.wav")
    store = UnitClipStore(tmp_path / "store")
    unit = {
        "segment_id": "seg-0007--render-02",
        "parent_segment_id": "seg-0007",
        "source_segment_id": "seg-0007",
        "speaker_id": "speaker-02",
        "source_start_seconds": 12.5,
        "source_end_seconds": 13.4,
    }

    entry = store.put_clip(
        clip,
        unit=unit,
        tts_identity="identity",
        duration_seconds=1.15,
    )
    reference = store.write_manifest([entry], pipeline_version=SPEECH_PIPELINE_V19)

    flac = store.verified_clip(entry)
    assert flac is not None and flac.suffix == ".flac"
    decoded = [
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-"],
            check=True,
            capture_output=True,
        ).stdout
        for path in (clip, flac)
    ]
    assert decoded[0] == decoded[1]
    manifest = json.loads((store.root / reference.manifest).read_text())
    assert manifest["schema_version"] == "unit-clip-store-v1"
    assert manifest["pipeline_version"] == SPEECH_PIPELINE_V19
    assert manifest["units"] == [
        {
            "unit_id": "seg-0007--render-02",
            "parent_segment_id": "seg-0007",
            "source_segment_id": "seg-0007",
            "speaker_id": "speaker-02",
            "source_start_seconds": 12.5,
            "source_end_seconds": 13.4,
            "file": entry.file,
            "sha256": entry.sha256,
            "source_wav_sha256": entry.source_wav_sha256,
            "duration_seconds": 1.15,
            "tts_identity": "identity",
        }
    ]
    assert store.load_manifest(reference) == [entry]
    assert UnitClipManifestReference.from_payload(reference.to_payload()) == (
        reference
    )


def test_store_rejects_tampered_or_missing_artifacts(tmp_path: Path) -> None:
    store = UnitClipStore(tmp_path / "store")
    entry = store.put_clip(
        _write_clip(tmp_path / "unit.wav"),
        unit={
            "segment_id": "seg-0001",
            "parent_segment_id": "seg-0001",
            "source_segment_id": "seg-0001",
            "speaker_id": "speaker-01",
            "source_start_seconds": 0.0,
            "source_end_seconds": 1.0,
        },
        tts_identity="identity",
        duration_seconds=1.15,
    )
    reference = store.write_manifest([entry], pipeline_version=SPEECH_PIPELINE_V19)

    (store.root / entry.file).write_bytes(b"corrupt")
    assert store.verified_clip(entry) is None
    (store.root / reference.manifest).write_text("{}")
    assert store.load_manifest(reference) is None
    shutil.rmtree(store.root)
    assert store.load_manifest(reference) is None
    assert UnitClipManifestReference.from_payload(None) is None


def test_local_cleanup_expires_the_store_but_never_a_pinned_copy(
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    artifact_root = tmp_path / "artifacts"
    expiring = unit_clip_store_root(artifact_root, 7)
    stale = _write_clip(expiring / "clips" / "unit.flac")
    pinned_root = artifact_root / "pinned" / "processing-runs" / "8" / "unit-clips"
    pinned = _write_clip(pinned_root / "clips" / "unit.flac")
    old = datetime(2026, 1, 1).timestamp()
    for path in (stale, pinned):
        os.utime(path, (old, old))
    dependencies = SimpleNamespace(
        artifact_root=artifact_root,
        clock=SimpleNamespace(now=lambda: datetime(2026, 2, 1)),
    )

    deleted = MaintenanceManager(dependencies)._cleanup_intermediates(
        retention_days=7
    )

    assert deleted == 1
    assert not stale.exists()
    assert pinned.exists()


def test_adapter_passes_the_run_store_only_to_rhythm_pipelines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from transpodcast.domain import SourceCandidate, TranslationResult

    captured: list[dict[str, Any]] = []

    def fake_pipeline(command, *, operation, request_path, response_path):
        del command, operation, response_path
        captured.append(json.loads(request_path.read_text(encoding="utf-8")))
        media_path = tmp_path / "publication.m4a"
        media_path.write_bytes(b"audio")
        return {
            "publication_audio_path": str(media_path),
            "duration_seconds": 1,
            "pipeline_version": "fixture",
            "quality": {},
        }

    monkeypatch.setattr(speech, "_pipeline_readiness", lambda command: True)
    monkeypatch.setattr(speech, "_run_pipeline_command", fake_pipeline)
    translation = TranslationResult(
        title="Title",
        overview="Overview",
        target_locale="zh-CN",
        segments=("你好。",),
        provider="fixture",
        model="fixture",
        input_tokens=0,
        output_tokens=0,
    )
    adapter = speech.ModalCommandSpeechAdapter(
        "python",
        artifact_root=tmp_path / "artifacts",
    )
    for version in (SPEECH_PIPELINE_V17, SPEECH_PIPELINE_V19):
        adapter.render(
            SourceCandidate(
                youtube_video_id="video",
                title="Fixture",
                description="",
                duration_seconds=1,
                language="en",
                published_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_url="https://example.test/video",
                captions=None,
                analysis_json=json.dumps({"segments": []}),
                processing_run_id=31,
                speech_pipeline_version=version,
            ),
            translation,
        )

    assert "unit_clip_store_root" not in captured[0]["rendering"]
    assert captured[1]["rendering"]["unit_clip_store_root"] == str(
        (
            tmp_path
            / "artifacts"
            / "intermediates"
            / "processing-runs"
            / "31"
            / "unit-clips"
        ).resolve()
    )


# --- render() with the Unit Clip Store ------------------------------------------


_SEGMENTS = [
    ("seg-0001", "speaker-01", 0.0, 1.0, "第一句。"),
    ("seg-0002", "speaker-01", 1.3, 2.0, "第二句。"),
    ("seg-0003", "speaker-02", 2.0, 3.8, "回答。"),
    ("seg-0004", "speaker-02", 40.2, 41.0, "广告之后。"),
]


class _RenderFixture:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        pipeline_version: str,
    ) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.pipeline_version = pipeline_version
        self.store_root = tmp_path / "artifacts" / "unit-clips"
        self.source = _write_clip(
            tmp_path / "source.wav",
            lead=0.0,
            speech_seconds=42.0,
            tail=0.0,
            rate=24_000,
        )
        self.references = {}
        for label in ("speaker-01", "speaker-02"):
            path = tmp_path / f"{label}.wav"
            path.write_bytes(label.encode())
            self.references[label] = path
        self.master_calls = 0
        original_master = modal_pipeline._master_stereo_pcm

        def counting_master(*arguments: Any, **keywords: Any) -> dict[str, Any]:
            self.master_calls += 1
            return original_master(*arguments, **keywords)

        monkeypatch.setattr(modal_pipeline, "_master_stereo_pcm", counting_master)

    def request(
        self,
        targets: list[str],
        rendering: dict[str, Any],
        *,
        speaker_references: dict[str, Path] | None = None,
    ) -> dict[str, Any]:
        references = speaker_references or self.references
        return {
            "source": {
                "audio_path": str(self.source),
                "checksum": "fixture-source",
                "analysis": {
                    "pipeline_version": SPEECH_PIPELINE_V17,
                    "segments": [
                        {
                            "segment_id": segment_id,
                            "speaker_label": speaker,
                            "start_seconds": start,
                            "end_seconds": end,
                        }
                        for segment_id, speaker, start, end, _text in _SEGMENTS
                    ],
                    "speakers": [
                        {"local_label": label, "reference_artifact_key": str(path)}
                        for label, path in references.items()
                    ],
                    "source_events": [],
                    "render_plan": {
                        "schema_version": "render-plan-v1",
                        "units": [
                            {
                                "unit_id": segment_id,
                                "parent_segment_id": segment_id,
                                "source_segment_id": segment_id,
                                "source_start_seconds": start,
                                "source_end_seconds": end,
                                "pause_before_seconds": 0.0,
                                "pause_after_seconds": 0.0,
                                "target_text": target,
                                "internal_split": False,
                                "approved_boundary_kind": None,
                            }
                            for (segment_id, _, start, end, _), target in zip(
                                _SEGMENTS, targets, strict=True
                            )
                        ],
                        "exclusions": [
                            {"start_seconds": 5.0, "end_seconds": 40.0}
                        ],
                    },
                },
            },
            "translation": {"segments": targets},
            "rendering": {
                "pipeline_version": self.pipeline_version,
                "unit_clip_store_root": str(self.store_root),
                **rendering,
            },
        }

    def render_complete(self) -> dict[str, Any]:
        def fake_render_sharded(
            _source_wav: Path,
            _checksum: str,
            _references: dict[str, bytes],
            plan: list[dict[str, Any]],
            rendered_root: Path,
            **keywords: Any,
        ) -> dict[str, Any]:
            assert keywords["pipeline_version"] == self.pipeline_version
            metadata = []
            for index, unit in enumerate(plan, start=1):
                path = _write_clip(rendered_root / f"segment-{index:04d}.wav")
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

        self.monkeypatch.setattr(
            modal_pipeline, "_render_sharded", fake_render_sharded
        )
        return modal_pipeline.render(
            self.request([item[4] for item in _SEGMENTS], {}),
            self.tmp_path / "complete",
        )

    def revise(
        self,
        prior: dict[str, Any],
        *,
        speaker_references: dict[str, Path] | None = None,
    ) -> dict[str, Any]:
        revised_audio = _write_clip(
            self.tmp_path / "revised.wav",
            speech_seconds=1.2,
            frequency=330.0,
        ).read_bytes()

        def fake_remote(
            _app: str,
            _class_name: str,
            method: str,
            *arguments: Any,
            **_keywords: Any,
        ) -> dict[str, Any]:
            assert method == "render"
            return {
                "segments": [
                    {
                        "segment_id": unit["segment_id"],
                        "audio": revised_audio,
                        "duration_seconds": 1.75,
                        "emotion_reference_start_seconds": 2.0,
                        "emotion_reference_end_seconds": 3.0,
                        "speech_text_sha256": "revised-text",
                    }
                    for unit in arguments[2]
                ],
                "model_id": "fixture-indextts",
                "model_revision": "fixture-revision",
                "seed": 42,
                "emotion_alpha": 0.8,
                "random_sampling": False,
                "total_inference_seconds": 0.0,
            }

        self.monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
        prior_preview = self.tmp_path / "prior-preview.m4a"
        shutil.copyfile(prior["publication_audio_path"], prior_preview)
        targets = [item[4] for item in _SEGMENTS]
        targets[2] = "改过的回答。"
        return modal_pipeline.render(
            self.request(
                targets,
                {
                    "scope": "segment",
                    "segment_ordinal": 3,
                    "prior_preview_audio_path": str(prior_preview),
                    "prior_render": {
                        "segment_timings": [
                            [item["start_seconds"], item["end_seconds"]]
                            for item in prior["segment_timings"]
                        ],
                        "render_settings_json": json.dumps(
                            prior["render_settings"]
                        ),
                    },
                },
                speaker_references=speaker_references,
            ),
            self.tmp_path / "revision",
        )


@pytest.mark.parametrize("pipeline_version", [SPEECH_PIPELINE_V19, SPEECH_PIPELINE_V20])
def test_complete_render_stores_clips_and_records_the_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_version: str,
) -> None:
    fixture = _RenderFixture(tmp_path, monkeypatch, pipeline_version)

    result = fixture.render_complete()

    settings = result["render_settings"]
    assert result["pipeline_version"] == pipeline_version
    assert settings["rhythm_policy_version"] == "rhythm-policy-v2"
    assert settings["assembly_source"] == "unit_clip_store"
    assert settings["rhythm"]["cut_transition_count"] == 1
    reference = UnitClipManifestReference.from_payload(settings["unit_clip_store"])
    assert reference is not None
    assert settings["unit_clip_store"]["root"] == str(fixture.store_root)
    entries = UnitClipStore(fixture.store_root).load_manifest(reference)
    assert entries is not None
    assert [entry.unit_id for entry in entries] == [item[0] for item in _SEGMENTS]
    assert [entry.speaker_id for entry in entries] == [item[1] for item in _SEGMENTS]
    assert all(entry.file.endswith(".flac") for entry in entries)
    assert fixture.master_calls == 1
    keep = RHYTHM_POLICY.head_keep_seconds + RHYTHM_POLICY.tail_keep_seconds
    assert _gaps(_timing_pairs(result)) == pytest.approx(
        [0.3 - keep, 0.25 - keep, 0.8 - keep],
        abs=TOLERANCE,
    )


def test_segment_revision_reassembles_from_the_store_and_masters_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _RenderFixture(tmp_path, monkeypatch, SPEECH_PIPELINE_V19)
    prior = fixture.render_complete()
    prior_reference = UnitClipManifestReference.from_payload(
        prior["render_settings"]["unit_clip_store"]
    )
    assert prior_reference is not None
    prior_entries = UnitClipStore(fixture.store_root).load_manifest(prior_reference)
    assert prior_entries is not None
    fixture.master_calls = 0
    decoded: list[str] = []
    commands: list[list[str]] = []
    original_decode = modal_pipeline._decode_mono_f32
    original_run = modal_pipeline._run

    def spy_decode(path: Path, **keywords: Any) -> Any:
        decoded.append(str(path))
        return original_decode(path, **keywords)

    def spy_run(arguments: list[str]) -> Any:
        commands.append(arguments)
        return original_run(arguments)

    def no_splice(*_arguments: Any, **_keywords: Any) -> dict[str, Any]:
        raise AssertionError("A V19 revision must not splice mastered audio")

    monkeypatch.setattr(modal_pipeline, "_decode_mono_f32", spy_decode)
    monkeypatch.setattr(modal_pipeline, "_run", spy_run)
    monkeypatch.setattr(modal_pipeline, "_replace_publication_region", no_splice)

    result = fixture.revise(prior)

    settings = result["render_settings"]
    assert settings["assembly_source"] == "unit_clip_store"
    assert fixture.master_calls == 1
    prior_preview = str(tmp_path / "prior-preview.m4a")
    assert prior_preview not in decoded
    assert not any(prior_preview in command for command in commands)
    reference = UnitClipManifestReference.from_payload(settings["unit_clip_store"])
    assert reference is not None and reference != prior_reference
    entries = UnitClipStore(fixture.store_root).load_manifest(reference)
    assert entries is not None
    assert [entry.file for entry in entries[:2]] == [
        entry.file for entry in prior_entries[:2]
    ]
    assert entries[3].file == prior_entries[3].file
    assert entries[2].file != prior_entries[2].file
    # The prior manifest version remains intact for audit.
    assert UnitClipStore(fixture.store_root).load_manifest(prior_reference) == (
        prior_entries
    )
    prior_timings = _timing_pairs(prior)
    timings = _timing_pairs(result)
    assert timings[:2] == pytest.approx(prior_timings[:2], abs=1e-6)
    revised_growth = (timings[2][1] - timings[2][0]) - (
        prior_timings[2][1] - prior_timings[2][0]
    )
    assert revised_growth == pytest.approx(0.6, abs=0.02)
    assert timings[3][0] - prior_timings[3][0] == pytest.approx(
        revised_growth,
        abs=1e-6,
    )
    assert result["rendered_segments"][0]["assembled_start_seconds"] == (
        pytest.approx(timings[2][0])
    )


@pytest.mark.parametrize("staleness", ["expired_store", "changed_voice"])
def test_segment_revision_falls_back_to_a_splice_without_a_usable_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    staleness: str,
) -> None:
    fixture = _RenderFixture(tmp_path, monkeypatch, SPEECH_PIPELINE_V19)
    prior = fixture.render_complete()
    references = None
    if staleness == "expired_store":
        shutil.rmtree(fixture.store_root)
    else:
        changed = tmp_path / "changed-voice.wav"
        changed.write_bytes(b"another voice")
        references = {**fixture.references, "speaker-01": changed}
    spliced: list[float] = []
    original_splice = modal_pipeline._replace_publication_region

    def spy_splice(*arguments: Any, **keywords: Any) -> dict[str, Any]:
        spliced.append(float(arguments[2]))
        return original_splice(*arguments, **keywords)

    monkeypatch.setattr(modal_pipeline, "_replace_publication_region", spy_splice)

    result = fixture.revise(prior, speaker_references=references)

    settings = result["render_settings"]
    assert settings["assembly_source"] == "publication_splice"
    assert settings["unit_clip_store"] is None
    assert settings["rhythm_policy_version"] == "rhythm-policy-v2"
    assert spliced == [prior["segment_timings"][2]["start_seconds"]]
    assert len(result["segment_timings"]) == len(_SEGMENTS)
