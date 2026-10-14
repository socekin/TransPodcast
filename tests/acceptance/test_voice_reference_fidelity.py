"""The ownership-seam Voice Reference selection in the worker (B1/B2).

A V19 Run replaces the episode Voice Reference with a full-band reference
chosen from final word owners before Speaker Registry matching; V17 keeps
the analysis reference; an approved Canonical Voice Reference still wins.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import wave
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.acceptance.test_adjudication_completion import (
    MatchingSpeechAdapter,
    ThreeSegmentTranslationAdapter,
)
from transpodcast import service
from transpodcast.api import create_app
from transpodcast.domain import (
    CanonicalSegment,
    SourceAnalysisResult,
    SourceArchive,
    SourceCandidate,
)
from transpodcast.models import ProcessingRunRecord
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V17, SPEECH_PIPELINE_V19
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    FakeSourceAdapter,
)
from transpodcast.voice_references import (
    OWNED_REFERENCES_KEY,
    VOICE_REFERENCE_POLICY_VERSION,
)

SOURCE_SECONDS = 300.0
FIXTURE_REFERENCE = b"RIFFfixture-speaker-reference"


class AudioSourceAdapter(FakeSourceAdapter):
    def __init__(self, candidates: list[SourceCandidate], audio: bytes) -> None:
        super().__init__(candidates)
        self.audio = audio

    def archive(self, candidate: SourceCandidate) -> SourceArchive:
        self.archive_invocation_count += 1
        return SourceArchive(
            content=self.audio,
            extension="wav",
            media_type="audio/wav",
            checksum=hashlib.sha256(self.audio).hexdigest(),
        )


class OwnedRunsAnalysisAdapter(FakeAnalysisAdapter):
    """One Speaker speaking 7 s runs every 8.5 s across the episode."""

    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        base = super().analyze(candidate, archive)
        segments = []
        start = 0.0
        while start + 7.0 <= SOURCE_SECONDS:
            words = tuple(
                ("word", start + index * 0.5, start + index * 0.5 + 0.4)
                for index in range(14)
            )
            segments.append(
                CanonicalSegment(
                    segment_id=f"seg-{len(segments) + 1:04d}",
                    speaker_label="speaker-01",
                    start_seconds=words[0][1],
                    end_seconds=words[-1][2],
                    text=" ".join(word[0] for word in words),
                    words=words,
                )
            )
            start += 8.5
        return replace(
            base,
            segments=tuple(segments),
            raw_diarization_turns=(("speaker-01", 0.0, SOURCE_SECONDS),),
            aligned_words=tuple(
                word for segment in segments for word in segment.words
            ),
        )


def _source_audio(path: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=(0.5+t/600)*0.2*sin(2*PI*700*t):s=16000"
            f":d={SOURCE_SECONDS}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def _candidates() -> list[SourceCandidate]:
    return [
        SourceCandidate(
            youtube_video_id=f"episode-{index}",
            title=f"Episode {index}",
            description="Product strategy.",
            duration_seconds=int(SOURCE_SECONDS),
            language="en",
            published_at=datetime(2026, 7, 28, 11, index, tzinfo=UTC),
            source_url=f"https://youtube.test/watch?v=episode-{index}",
            captions="A durable product preserves intent.",
        )
        for index in (1, 2)
    ]


def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_version: str,
) -> tuple[TestClient, Any]:
    monkeypatch.setattr(
        service,
        "pipeline_version_for_speech_model",
        lambda _speech_model: pipeline_version,
    )
    harness = AcceptanceHarness.for_candidates(
        tmp_path, candidates=_candidates(), triage_results=None
    )
    dependencies = replace(
        harness.dependencies,
        source=AudioSourceAdapter(
            _candidates(), _source_audio(tmp_path / "source.wav")
        ),
        analysis=OwnedRunsAnalysisAdapter(),
        translation=ThreeSegmentTranslationAdapter(),
        speech=MatchingSpeechAdapter(),
    )
    client = TestClient(create_app(dependencies))
    channel = client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    ).json()
    client.post("/api/v1/discovery-runs")
    return client, (dependencies, channel)


def _stored_analysis(dependencies: Any, run_id: int) -> dict[str, Any]:
    with dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, run_id)
        assert run is not None
        assert run.error_kind is None
        return dict(json.loads(str(run.analysis_json)))


def _owned_records(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        record
        for record in analysis["speaker_consolidation"].get("decision_records", [])
        if record["policy_version"] == VOICE_REFERENCE_POLICY_VERSION
    ]


def test_v19_registry_candidate_is_the_full_band_owned_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, (dependencies, _channel) = _client(
        tmp_path, monkeypatch, SPEECH_PIPELINE_V19
    )

    advanced = client.post("/api/v1/processing-runs:advance").json()

    assert advanced["processing_state"] == "completed"
    analysis = _stored_analysis(dependencies, advanced["processing_run_id"])
    (speaker,) = analysis["speakers"]
    reference = Path(speaker["reference_artifact_key"])
    assert reference.name == "candidate-reference.wav"
    with wave.open(str(reference), "rb") as handle:
        assert handle.getframerate() == 24_000
        assert handle.getnchannels() == 1
        seconds = handle.getnframes() / handle.getframerate()
    # Two 6.9 s runs starting 93.5 s and 161.5 s, joined with 250 ms.
    assert seconds == pytest.approx(6.9 + 0.25 + 6.9, abs=0.02)
    summary = analysis["speaker_consolidation"][OWNED_REFERENCES_KEY]
    assert summary["speakers"]["speaker-01"]["spans"] == [
        [93.5, 100.4],
        [161.5, 168.4],
    ]
    assert summary["speakers"]["speaker-01"]["reference_sha256"] == (
        hashlib.sha256(reference.read_bytes()).hexdigest()
    )
    (record,) = _owned_records(analysis)
    assert record["kind"] == "reference-selection"
    assert record["verdict"] == "selected"
    assert record["evidence"]["selection_stage"] == "post-ownership"
    # The persisted Decision Ledger covers the new record.
    assert analysis["speaker_consolidation"]["decision_ledger_reference"]


def test_v17_keeps_the_analysis_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, (dependencies, _channel) = _client(
        tmp_path, monkeypatch, SPEECH_PIPELINE_V17
    )

    advanced = client.post("/api/v1/processing-runs:advance").json()

    analysis = _stored_analysis(dependencies, advanced["processing_run_id"])
    (speaker,) = analysis["speakers"]
    assert Path(speaker["reference_artifact_key"]).read_bytes() == (
        FIXTURE_REFERENCE
    )
    assert OWNED_REFERENCES_KEY not in analysis["speaker_consolidation"]
    assert _owned_records(analysis) == []


def test_v19_approved_canonical_reference_still_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, (dependencies, channel) = _client(
        tmp_path, monkeypatch, SPEECH_PIPELINE_V19
    )
    client.post("/api/v1/processing-runs:advance")
    provisional = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()["pending_candidates"][0]
    client.post(
        f"/api/v1/channel-voice-candidates/{provisional['id']}:save",
        json={"display_name": "Known speaker"},
    )
    approved_reference = client.get("/api/v1/speakers").json()["items"][0][
        "canonical_voice_reference"
    ]

    second = client.post("/api/v1/processing-runs:advance").json()

    analysis = _stored_analysis(dependencies, second["processing_run_id"])
    (speaker,) = analysis["speakers"]
    assert speaker["registry_speaker_id"] == provisional["speaker_id"]
    assert speaker["reference_artifact_key"] == approved_reference
    # The episode selection is still recorded, but only as evidence.
    assert len(_owned_records(analysis)) == 1
