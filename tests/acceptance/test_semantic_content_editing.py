from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import select

from transpodcast.api import create_app
from transpodcast.artifacts import resolve_structured_artifact
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
from transpodcast.models import (
    EpisodeContentEditRevisionRecord,
    ProcessingRunRecord,
)
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    FakeSpeechAdapter,
    FakeTranslationAdapter,
)


class EpisodeTenHighlightAnalysis(FakeAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        self.invocation_count += 1
        assert archive.checksum
        return SourceAnalysisResult(
            segments=(
                CanonicalSegment(
                    segment_id="seg-0001",
                    speaker_label="speaker-01",
                    start_seconds=0.16,
                    end_seconds=44.4,
                    text=(
                        "The cost to replace an enterprise platform is "
                        "extraordinary. People never forgive software "
                        "for making a mistake."
                    ),
                    words=(
                        ("The", 0.16, 0.24),
                        ("cost", 0.24, 0.88),
                        ("to", 0.88, 1.04),
                        ("replace", 1.04, 1.84),
                        ("an", 1.84, 1.92),
                        ("enterprise", 1.92, 2.48),
                        ("platform", 2.48, 3.2),
                        ("is", 3.2, 3.44),
                        ("extraordinary", 3.44, 4.8),
                        ("People", 40.0, 40.4),
                        ("never", 40.4, 40.8),
                        ("forgive", 40.8, 41.4),
                        ("software", 41.4, 42.1),
                        ("for", 42.1, 42.3),
                        ("making", 42.3, 43.2),
                        ("a", 43.2, 43.84),
                        ("mistake", 43.84, 44.4),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0002",
                    speaker_label="speaker-02",
                    start_seconds=47.76,
                    end_seconds=65.6,
                    text=(
                        "Hi listeners. Welcome back to Research Signals. Today "
                        "I am here with Bill McDermott."
                    ),
                    words=(
                        ("Hi", 47.76, 47.92),
                        ("listeners", 51.28, 51.76),
                        ("Welcome", 51.76, 52.24),
                        ("back", 52.24, 52.64),
                        ("to", 52.64, 52.8),
                        ("No", 52.8, 53.04),
                        ("Priors", 53.04, 53.6),
                        ("Today", 53.6, 54.0),
                        ("I", 54.0, 54.08),
                        ("am", 54.08, 54.24),
                        ("here", 54.24, 54.56),
                        ("with", 54.56, 54.8),
                        ("Bill", 54.8, 55.04),
                        ("McDermott", 55.04, 55.68),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0003",
                    speaker_label="speaker-01",
                    start_seconds=1371.371,
                    end_seconds=1490.946,
                    text=(
                        "The cost to replace an enterprise platform is "
                        "extraordinary in the complete discussion."
                    ),
                    words=(
                        ("The", 1371.371, 1371.451),
                        ("cost", 1371.451, 1371.8),
                        ("to", 1371.8, 1371.96),
                        ("replace", 1371.96, 1372.56),
                        ("an", 1372.56, 1372.64),
                        ("enterprise", 1372.64, 1373.2),
                        ("platform", 1373.2, 1373.84),
                        ("is", 1373.84, 1374.0),
                        ("extraordinary", 1374.0, 1374.96),
                        ("in", 1374.96, 1375.04),
                        ("the", 1375.04, 1375.12),
                        ("complete", 1375.12, 1375.68),
                        ("discussion", 1375.68, 1376.48),
                    ),
                ),
            ),
            pipeline_version="fixture-analysis-v9",
            diarization_model="fixture-sortformer",
            transcription_model="fixture-qwen3-asr",
            alignment_model="fixture-qwen3-aligner",
            speakers=(
                AnalyzedSpeaker(
                    local_label="speaker-01",
                    reference_audio=b"RIFFspeaker-01",
                    embedding=(1.0, 0.0),
                    confidence=0.98,
                ),
                AnalyzedSpeaker(
                    local_label="speaker-02",
                    reference_audio=b"RIFFspeaker-02",
                    embedding=(0.0, 1.0),
                    confidence=0.98,
                ),
            ),
            checksum="episode-ten-highlight-fixture",
        )


class EpisodeTenHighlightTranslation(FakeTranslationAdapter):
    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        base = super().translate(candidate)
        return replace(
            base,
            segments=(
                "Opening highlight target fixture.",
                "Formal episode opening target fixture.",
                "Complete discussion target fixture.",
            ),
            content_edit_json=json.dumps(
                {
                    "policy_version": "semantic-content-edit-v1",
                    "status": "accepted",
                    "decisions": [
                        {
                            "decision_id": "opening-highlight-1",
                            "category": "opening_highlight",
                            "confidence": 0.99,
                            "start_segment_id": "seg-0001",
                            "end_segment_id": "seg-0001",
                            "start_anchor": "The cost",
                            "end_anchor": "a mistake",
                            "rationale": (
                                "The opening excerpt repeats in the later "
                                "complete discussion."
                            ),
                            "evidence_segment_ids": ["seg-0003"],
                            "retained_start_target": "",
                            "retained_end_target": "",
                        }
                    ],
                    "retention_notes": [],
                }
            ),
        )


class CapturingRenderPlanSpeech(FakeSpeechAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.rendered_targets: tuple[str, ...] = ()

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        self.invocation_count += 1
        self.last_analysis_json = candidate.analysis_json
        self.rendered_targets = translation.segments
        result = self._result(translation)
        return replace(
            result,
            quality=AudioQualityReport(
                rendered_segments=len(translation.segments)
            ),
        )

    def render_segment(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
        segment_ordinal: int,
        prior_preview_key: str,
    ) -> SpeechResult:
        result = super().render_segment(
            candidate,
            translation,
            segment_ordinal,
            prior_preview_key,
        )
        self.rendered_targets = translation.segments
        return replace(
            result,
            quality=replace(
                result.quality,
                rendered_segments=len(translation.segments),
            ),
        )


def test_opening_highlight_is_absent_from_render_but_preserved_in_detail(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = CapturingRenderPlanSpeech()
    dependencies = replace(
        harness.dependencies,
        analysis=EpisodeTenHighlightAnalysis(),
        translation=EpisodeTenHighlightTranslation(),
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

    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["processing_state"] == "completed"
    assert speech.rendered_targets == (
        "Formal episode opening target fixture.",
        "Complete discussion target fixture.",
    )
    rendered_analysis = json.loads(speech.last_analysis_json or "{}")
    assert [
        item["segment_id"] for item in rendered_analysis["segments"]
    ] == ["seg-0002", "seg-0003"]

    detail = client.get(
        f"/api/v1/episodes/{completed['episode_id']}/processing-detail"
    ).json()
    assert detail["summary"]["content_edit"] == {
        "status": "accepted",
        "excluded_count": 1,
        "excluded_source_seconds": 44.24,
    }
    first, opening, repeated = detail["segments"]
    assert first["source_text"].startswith(
        "The cost to replace an enterprise platform"
    )
    assert first["content_edit"]["state"] == "excluded"
    assert first["content_edit"]["category"] == "opening_highlight"
    assert first["content_edit"]["evidence_segment_ids"] == ["seg-0003"]
    assert opening["source_text"].startswith("Hi listeners")
    assert opening["content_edit"] is None
    assert repeated["content_edit"] is None
    with harness.dependencies.database.session() as session:
        revision = session.scalar(
            select(EpisodeContentEditRevisionRecord).where(
                EpisodeContentEditRevisionRecord.episode_id
                == completed["episode_id"]
            )
        )
        assert revision is not None
        preserved = json.loads(
            resolve_structured_artifact(
                session,
                revision.content_edit_json,
            )
        )
    assert preserved["decisions"][0]["decision_id"] == (
        "opening-highlight-1"
    )


def test_targeted_revision_maps_a_retained_canonical_segment_to_render_plan(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = CapturingRenderPlanSpeech()
    client = TestClient(
        create_app(
            replace(
                harness.dependencies,
                analysis=EpisodeTenHighlightAnalysis(),
                translation=EpisodeTenHighlightTranslation(),
                speech=speech,
            )
        )
    )
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    episode_id = client.post("/api/v1/processing-runs:advance").json()[
        "episode_id"
    ]

    excluded = client.patch(
        f"/api/v1/episodes/{episode_id}/segments/1",
        json={"target_text": "Unsafe excluded revision."},
    )
    transcript = client.patch(
        f"/api/v1/episodes/{episode_id}/transcript-segments/2",
        json={"source_text": "Unsafe canonical revision."},
    )
    retained = client.patch(
        f"/api/v1/episodes/{episode_id}/segments/2",
        json={"target_text": "Revised formal opening."},
    )
    completed = client.post("/api/v1/processing-runs:advance").json()

    assert excluded.status_code == 409
    assert "full episode reprocessing" in excluded.json()["detail"]
    assert transcript.status_code == 409
    assert "full episode reprocessing" in transcript.json()["detail"]
    assert retained.status_code == 202
    assert completed["revision_state"] == "completed"
    assert speech.last_render_scope == ("segment", 1)
    detail = client.get(f"/api/v1/episodes/{episode_id}").json()
    assert detail["segments"][1]["target_text"] == "Revised formal opening."


class MutableQualityContentEditSpeech(CapturingRenderPlanSpeech):
    def __init__(self) -> None:
        super().__init__()
        self.valid = False

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        result = super().render(candidate, translation)
        return replace(
            result,
            quality=replace(
                result.quality,
                loudness_lufs=-16.0 if self.valid else -13.0,
                clipping=not self.valid,
            ),
        )


def test_quality_retry_discards_audio_when_the_render_plan_changes(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = MutableQualityContentEditSpeech()
    client = TestClient(
        create_app(
            replace(
                harness.dependencies,
                analysis=EpisodeTenHighlightAnalysis(),
                translation=EpisodeTenHighlightTranslation(),
                speech=speech,
            )
        )
    )
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    failed = client.post("/api/v1/processing-runs:advance").json()
    assert failed["processing_state"] == "review_queue"
    assert speech.invocation_count == 1

    with harness.dependencies.database.session() as session:
        run = session.get(ProcessingRunRecord, failed["processing_run_id"])
        assert run is not None
        run.content_edit_json = "{}"
    speech.valid = True

    client.post(
        f"/api/v1/processing-runs/{failed['processing_run_id']}:retry"
    )
    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["processing_state"] == "completed"
    assert speech.invocation_count == 2
    assert speech.rendered_targets == (
        "Opening highlight target fixture.",
        "Formal episode opening target fixture.",
        "Complete discussion target fixture.",
    )


class EpisodeOnePaidAdAnalysis(FakeAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        self.invocation_count += 1
        assert archive.checksum
        return SourceAnalysisResult(
            segments=(
                CanonicalSegment(
                    segment_id="seg-0225",
                    speaker_label="speaker-01",
                    start_seconds=1948.0,
                    end_seconds=1951.8,
                    text="That completes the previous editorial answer.",
                    words=(
                        ("That", 1948.0, 1948.3),
                        ("completes", 1948.3, 1948.9),
                        ("the", 1948.9, 1949.1),
                        ("previous", 1949.1, 1949.7),
                        ("editorial", 1949.7, 1950.4),
                        ("answer", 1950.4, 1951.8),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0226",
                    speaker_label="speaker-03",
                    start_seconds=1952.226,
                    end_seconds=2144.589,
                    text=(
                        "This episode is brought to you by Gemini. Visit "
                        "the product site to learn more"
                    ),
                    words=(
                        ("This", 1952.226, 1952.45),
                        ("episode", 1952.45, 1952.9),
                        ("is", 1952.9, 1953.05),
                        ("brought", 1953.05, 1953.4),
                        ("to", 1953.4, 1953.55),
                        ("you", 1953.55, 1953.8),
                        ("by", 1953.8, 1954.0),
                        ("Gemini", 1954.0, 1954.6),
                        ("Visit", 2142.4, 2142.8),
                        ("more", 2144.249, 2144.589),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0227",
                    speaker_label="speaker-01",
                    start_seconds=2144.829,
                    end_seconds=2148.509,
                    text="Google I am curious if we return to a research era",
                    words=(
                        ("Google", 2144.829, 2145.149),
                        ("I", 2145.949, 2146.029),
                        ("am", 2146.029, 2146.109),
                        ("curious", 2146.109, 2146.349),
                        ("if", 2146.349, 2146.509),
                        ("we", 2146.509, 2146.669),
                        ("return", 2146.669, 2147.149),
                        ("to", 2147.149, 2147.309),
                        ("a", 2147.309, 2147.389),
                        ("research", 2147.389, 2148.029),
                        ("era", 2148.029, 2148.509),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0228",
                    speaker_label="speaker-02",
                    start_seconds=2149.0,
                    end_seconds=2152.0,
                    text="The answer continues with editorial discussion.",
                    words=(
                        ("The", 2149.0, 2149.2),
                        ("answer", 2149.2, 2149.7),
                        ("continues", 2149.7, 2150.3),
                        ("with", 2150.3, 2150.5),
                        ("editorial", 2150.5, 2151.2),
                        ("discussion", 2151.2, 2152.0),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0335",
                    speaker_label="speaker-03",
                    start_seconds=3234.159,
                    end_seconds=3301.5,
                    text="This separate sponsor message ends now",
                    words=(
                        ("This", 3234.159, 3234.4),
                        ("separate", 3234.4, 3234.9),
                        ("sponsor", 3234.9, 3235.4),
                        ("message", 3235.4, 3236.0),
                        ("ends", 3301.0, 3301.3),
                        ("now", 3301.3, 3301.5),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0336",
                    speaker_label="speaker-01",
                    start_seconds=3301.62,
                    end_seconds=3305.4,
                    text="Labelbox It seems to me that research has changed",
                    words=(
                        ("Labelbox", 3301.62, 3301.94),
                        ("It", 3302.2, 3302.3),
                        ("seems", 3302.3, 3302.58),
                        ("to", 3302.58, 3302.7),
                        ("me", 3302.7, 3302.9),
                        ("that", 3302.9, 3303.1),
                        ("research", 3303.1, 3303.8),
                        ("has", 3303.8, 3304.1),
                        ("changed", 3304.1, 3305.4),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0337",
                    speaker_label="speaker-02",
                    start_seconds=3305.6,
                    end_seconds=3308.0,
                    text="The editorial response continues.",
                    words=(
                        ("The", 3305.6, 3305.8),
                        ("editorial", 3305.8, 3306.5),
                        ("response", 3306.5, 3307.2),
                        ("continues", 3307.2, 3308.0),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0445",
                    speaker_label="speaker-03",
                    start_seconds=4614.249,
                    end_seconds=4691.4,
                    text="Another separate sponsor message ends now",
                    words=(
                        ("Another", 4614.249, 4614.7),
                        ("separate", 4614.7, 4615.2),
                        ("sponsor", 4615.2, 4615.7),
                        ("message", 4615.7, 4616.3),
                        ("ends", 4690.9, 4691.15),
                        ("now", 4691.15, 4691.4),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0446",
                    speaker_label="speaker-01",
                    start_seconds=4691.5,
                    end_seconds=4695.4,
                    text="Sardine What is SSI trying to accomplish",
                    words=(
                        ("Sardine", 4691.5, 4691.781),
                        ("What", 4692.1, 4692.34),
                        ("is", 4692.34, 4692.48),
                        ("SSI", 4692.48, 4692.82),
                        ("trying", 4692.82, 4693.22),
                        ("to", 4693.22, 4693.35),
                        ("accomplish", 4693.35, 4695.4),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0447",
                    speaker_label="speaker-02",
                    start_seconds=4695.6,
                    end_seconds=4698.0,
                    text="The final editorial response continues.",
                    words=(
                        ("The", 4695.6, 4695.8),
                        ("final", 4695.8, 4696.2),
                        ("editorial", 4696.2, 4696.9),
                        ("response", 4696.9, 4697.4),
                        ("continues", 4697.4, 4698.0),
                    ),
                ),
                CanonicalSegment(
                    segment_id="seg-0500",
                    speaker_label="speaker-02",
                    start_seconds=5000.0,
                    end_seconds=5004.0,
                    text="Please share rate and review our show.",
                    words=(
                        ("Please", 5000.0, 5000.4),
                        ("share", 5000.4, 5000.8),
                        ("rate", 5000.8, 5001.2),
                        ("and", 5001.2, 5001.4),
                        ("review", 5001.4, 5002.0),
                        ("our", 5002.0, 5002.3),
                        ("show", 5002.3, 5004.0),
                    ),
                ),
            ),
            pipeline_version="fixture-analysis-v9",
            diarization_model="fixture-sortformer",
            transcription_model="fixture-qwen3-asr",
            alignment_model="fixture-qwen3-aligner",
            speakers=(
                AnalyzedSpeaker(
                    local_label="speaker-01",
                    reference_audio=b"RIFFspeaker-01",
                    embedding=(1.0, 0.0),
                    confidence=0.98,
                ),
                AnalyzedSpeaker(
                    local_label="speaker-02",
                    reference_audio=b"RIFFspeaker-02",
                    embedding=(0.0, 1.0),
                    confidence=0.98,
                ),
                AnalyzedSpeaker(
                    local_label="speaker-03",
                    reference_audio=b"RIFFspeaker-03",
                    embedding=(0.0, 0.0, 1.0),
                    confidence=0.98,
                ),
            ),
            checksum="episode-one-paid-ad-fixture",
        )


class EpisodeOnePaidAdTranslation(FakeTranslationAdapter):
    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        base = super().translate(candidate)
        return replace(
            base,
            segments=(
                "Previous editorial answer target fixture.",
                "Full paid advertisement target fixture.",
                "Mixed advertisement and editorial target fixture.",
                "Following editorial answer target fixture.",
                "Second paid advertisement target fixture.",
                "Second mixed boundary target fixture.",
                "Second editorial answer target fixture.",
                "Third paid advertisement target fixture.",
                "Third mixed boundary target fixture.",
                "Third editorial answer target fixture.",
                "Program-owned closing promotion target fixture.",
            ),
            content_edit_json=json.dumps(
                {
                    "policy_version": "semantic-content-edit-v1",
                    "status": "accepted",
                    "decisions": [
                        {
                            "decision_id": "paid-ad-1",
                            "category": "paid_ad",
                            "confidence": 0.98,
                            "start_segment_id": "seg-0226",
                            "end_segment_id": "seg-0227",
                            "start_anchor": "This episode",
                            "end_anchor": "Google",
                            "rationale": (
                                "A separate third-party paid advertisement "
                                "ends before the editorial question resumes."
                            ),
                            "evidence_segment_ids": ["seg-0226"],
                            "retained_start_target": "",
                            "retained_end_target": (
                                "Retained editorial question target fixture."
                            ),
                        },
                        {
                            "decision_id": "paid-ad-2",
                            "category": "paid_ad",
                            "confidence": 0.98,
                            "start_segment_id": "seg-0335",
                            "end_segment_id": "seg-0336",
                            "start_anchor": "This separate",
                            "end_anchor": "Labelbox",
                            "rationale": (
                                "A second separate third-party paid "
                                "advertisement ends before the interview."
                            ),
                            "evidence_segment_ids": ["seg-0335"],
                            "retained_start_target": "",
                            "retained_end_target": (
                                "Retained second editorial question fixture."
                            ),
                        },
                        {
                            "decision_id": "paid-ad-3",
                            "category": "paid_ad",
                            "confidence": 0.98,
                            "start_segment_id": "seg-0445",
                            "end_segment_id": "seg-0446",
                            "start_anchor": "Another separate",
                            "end_anchor": "Sardine",
                            "rationale": (
                                "A third separate third-party paid "
                                "advertisement ends before the interview."
                            ),
                            "evidence_segment_ids": ["seg-0445"],
                            "retained_start_target": "",
                            "retained_end_target": (
                                "Retained third editorial question fixture."
                            ),
                        },
                    ],
                    "retention_notes": [],
                }
            ),
        )


def test_paid_ad_boundary_retains_the_editorial_question(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = CapturingRenderPlanSpeech()
    dependencies = replace(
        harness.dependencies,
        analysis=EpisodeOnePaidAdAnalysis(),
        translation=EpisodeOnePaidAdTranslation(),
        speech=speech,
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

    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["processing_state"] == "completed"
    assert speech.rendered_targets == (
        "Previous editorial answer target fixture.",
        "Retained editorial question target fixture.",
        "Following editorial answer target fixture.",
        "Retained second editorial question fixture.",
        "Second editorial answer target fixture.",
        "Retained third editorial question fixture.",
        "Third editorial answer target fixture.",
        "Program-owned closing promotion target fixture.",
    )
    rendered_analysis = json.loads(speech.last_analysis_json or "{}")
    assert [
        item["segment_id"] for item in rendered_analysis["segments"]
    ] == [
        "seg-0225",
        "seg-0227-retained-after",
        "seg-0228",
        "seg-0336-retained-after",
        "seg-0337",
        "seg-0446-retained-after",
        "seg-0447",
        "seg-0500",
    ]
    assert rendered_analysis["segments"][1]["text"].startswith("I am curious")
    assert rendered_analysis["segments"][3]["text"].startswith("It seems to me")
    assert rendered_analysis["segments"][5]["text"].startswith("What is SSI")

    detail = client.get(
        f"/api/v1/episodes/{completed['episode_id']}/processing-detail"
    ).json()
    assert len(detail["segments"]) == 11
    assert detail["segments"][1]["content_edit"]["state"] == "excluded"
    assert (
        detail["segments"][2]["content_edit"]["state"]
        == "partially_excluded"
    )
    assert detail["segments"][4]["content_edit"]["state"] == "excluded"
    assert (
        detail["segments"][5]["content_edit"]["state"]
        == "partially_excluded"
    )
    assert detail["segments"][7]["content_edit"]["state"] == "excluded"
    assert (
        detail["segments"][8]["content_edit"]["state"]
        == "partially_excluded"
    )
    assert detail["segments"][10]["content_edit"] is None
    run = client.get("/api/v1/processing-runs/1").json()
    content_edit_activity = [
        item for item in run["activity"] if item["kind"] == "content_edit"
    ]
    assert len(content_edit_activity) == 1
    assert content_edit_activity[0]["metadata"]["category_counts"] == {
        "paid_ad": 3
    }
    voice_library = client.get(
        f"/api/v1/source-channels/{channel['id']}/voice-library"
    ).json()
    assert {
        item["local_label"]
        for item in voice_library["pending_candidates"]
    } == {"speaker-01", "speaker-02"}


class MalformedContentEditTranslation(EpisodeTenHighlightTranslation):
    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        return replace(
            super().translate(candidate),
            content_edit_json="{malformed",
        )


def test_malformed_content_edit_retains_every_spoken_segment(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = CapturingRenderPlanSpeech()
    dependencies = replace(
        harness.dependencies,
        analysis=EpisodeTenHighlightAnalysis(),
        translation=MalformedContentEditTranslation(),
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

    completed = client.post("/api/v1/processing-runs:advance").json()

    assert completed["processing_state"] == "completed"
    assert speech.rendered_targets == (
        "Opening highlight target fixture.",
        "Formal episode opening target fixture.",
        "Complete discussion target fixture.",
    )
    detail = client.get(
        f"/api/v1/episodes/{completed['episode_id']}/processing-detail"
    ).json()
    assert detail["summary"]["content_edit"] == {
        "status": "degraded",
        "excluded_count": 0,
        "excluded_source_seconds": 0,
    }
    run = client.get("/api/v1/processing-runs/1").json()
    content_edit_activity = [
        item for item in run["activity"] if item["kind"] == "content_edit"
    ]
    assert content_edit_activity[0]["metadata"]["diagnostics"] == [
        "content_edit_artifact_is_malformed"
    ]
