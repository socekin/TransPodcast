from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import (
    AnalyzedSpeaker,
    AudioQualityReport,
    CanonicalSegment,
    SourceAnalysisResult,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.models import ProcessingRunRecord
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    FakeSpeechAdapter,
    FakeTranslationAdapter,
)


class LegacySponsorFlagAnalysisAdapter(FakeAnalysisAdapter):
    def analyze(self, candidate, archive):
        self.invocation_count += 1
        return SourceAnalysisResult(
            segments=(
                CanonicalSegment(
                    segment_id="seg-0001",
                    speaker_label="speaker-01",
                    start_seconds=0.0,
                    end_seconds=5.0,
                    text=(
                        "This episode is brought to you by Acme. "
                        "Visit acme.example and use code SHOW."
                    ),
                    words=(
                        (
                            "This episode is brought to you by Acme.",
                            0.0,
                            2.0,
                        ),
                        (
                            "Visit acme.example and use code SHOW.",
                            2.1,
                            5.0,
                        ),
                    ),
                    omitted_as_sponsor=True,
                ),
                CanonicalSegment(
                    segment_id="seg-0002",
                    speaker_label="speaker-01",
                    start_seconds=5.1,
                    end_seconds=10.0,
                    text="Welcome to the conversation.",
                    words=(("Welcome to the conversation.", 5.1, 10.0),),
                ),
            ),
            pipeline_version="fixture-analysis-v1",
            diarization_model="fixture-sortformer",
            transcription_model="fixture-qwen3-asr",
            alignment_model="fixture-qwen3-aligner",
            speakers=(
                AnalyzedSpeaker(
                    local_label="speaker-01",
                    reference_audio=b"RIFFfixture-speaker-reference",
                    embedding=(1.0, 0.0, 0.0),
                    confidence=0.98,
                ),
            ),
            checksum="legacy-checksum",
        )


class CapturingTranslationAdapter(FakeTranslationAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.omission_flags: list[bool] = []

    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        analysis = json.loads(candidate.analysis_json or "{}")
        segments = list(analysis.get("segments", []))
        self.omission_flags = [
            bool(segment.get("omitted_as_sponsor", False))
            for segment in segments
        ]
        base = super().translate(candidate)
        return replace(
            base,
            segments=tuple(
                f"Retained translation {index}."
                for index in range(1, len(segments) + 1)
            ),
        )


class MatchingSpeechAdapter(FakeSpeechAdapter):
    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        result = super().render(candidate, translation)
        return replace(
            result,
            quality=AudioQualityReport(
                rendered_segments=len(translation.segments)
            ),
        )


class FirstRenderQualityFailureAdapter(MatchingSpeechAdapter):
    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        result = super().render(candidate, translation)
        if self.invocation_count != 1:
            return result
        return replace(
            result,
            quality=replace(
                result.quality,
                loudness_lufs=-13.0,
                true_peak_dbtp=-0.5,
                clipping=True,
            ),
        )


def test_every_spoken_segment_is_retained_through_publication(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    analysis = LegacySponsorFlagAnalysisAdapter()
    translation = CapturingTranslationAdapter()
    dependencies = replace(
        harness.dependencies,
        analysis=analysis,
        translation=translation,
        speech=MatchingSpeechAdapter(),
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")

    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["processing_state"] == "completed"
    assert translation.omission_flags == [False, False]
    detail = client.get(
        f"/api/v1/episodes/{completed['episode_id']}"
    ).json()
    assert [segment["source_text"] for segment in detail["segments"]] == [
        (
            "This episode is brought to you by Acme. "
            "Visit acme.example and use code SHOW."
        ),
        "Welcome to the conversation.",
    ]


def test_legacy_incomplete_translation_is_rebuilt_before_render(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    analysis = LegacySponsorFlagAnalysisAdapter()
    translation = CapturingTranslationAdapter()
    speech = FirstRenderQualityFailureAdapter()
    dependencies = replace(
        harness.dependencies,
        analysis=analysis,
        translation=translation,
        speech=speech,
    )
    client = TestClient(create_app(dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    failed = client.post("/api/v1/processing-runs:advance").json()

    assert failed["error_kind"] == "quality_gate"
    with dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            int(failed["processing_run_id"]),
        )
        assert run is not None
        analysis_payload = json.loads(str(run.analysis_json))
        analysis_payload["segments"][0]["omitted_as_sponsor"] = True
        run.analysis_json = json.dumps(analysis_payload)
        translation_payload = json.loads(str(run.translation_json))
        translation_payload["segments"] = translation_payload["segments"][1:]
        run.translation_json = json.dumps(translation_payload)

    client.post(
        f"/api/v1/processing-runs/{failed['processing_run_id']}:retry"
    )
    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["processing_state"] == "completed"
    assert analysis.invocation_count == 1
    assert translation.invocation_count == 2
    assert speech.invocation_count == 2
    with dependencies.database.session() as session:
        run = session.get(
            ProcessingRunRecord,
            int(failed["processing_run_id"]),
        )
        assert run is not None
        final_translation = json.loads(str(run.translation_json))
        final_analysis = json.loads(str(run.analysis_json))
    assert len(final_translation["segments"]) == 2
    assert all(
        not item["omitted_as_sponsor"]
        for item in final_analysis["segments"]
    )
