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
    SourceArchive,
    SourceCandidate,
    SpeechResult,
    TranslationResult,
)
from transpodcast.testing import (
    AcceptanceHarness,
    FakeAnalysisAdapter,
    FakeSpeechAdapter,
    FakeTranslationAdapter,
)

# A crosstalk zone (seg-0001..seg-0006) in which speaker-02's sentence is
# split by two replies, then a calm segment.
SEGMENTS = (
    ("seg-0001", "speaker-01", 0.0, 4.0, "So the real question is whether it ships."),
    ("seg-0002", "speaker-02", 4.0, 4.6, "We would have"),
    ("seg-0003", "speaker-03", 4.6, 5.0, "Yeah."),
    ("seg-0004", "speaker-02", 5.0, 5.6, "been number"),
    ("seg-0005", "speaker-01", 5.6, 6.0, "No way."),
    ("seg-0006", "speaker-02", 6.0, 7.0, "one by now."),
    ("seg-0007", "speaker-01", 60.0, 64.0, "Here is the next topic for today."),
)
TARGETS = (
    "所以真正的问题是它到底能不能上线。",
    "我们本来",
    "对。",
    "能拿第",
    "不可能。",
    "一了。",
    "这是我们今天要聊的下一个话题。",
)
CROSSTALK = [
    {
        "candidate_id": "xt-001",
        "action": "reassemble",
        "start_segment_id": "seg-0002",
        "end_segment_id": "seg-0006",
        "lines": [
            {
                "speaker": "speaker-02",
                "segment_ids": ["seg-0002", "seg-0004", "seg-0006"],
                "target": "我们本来早就能拿第一了。",
            },
            {"speaker": "speaker-03", "segment_ids": ["seg-0003"], "target": "对。"},
            {
                "speaker": "speaker-01",
                "segment_ids": ["seg-0005"],
                "target": "不可能。",
            },
        ],
        "rationale": "speaker-02's sentence was split by two replies.",
    }
]


def _words(text: str, start: float, end: float) -> tuple[tuple[str, float, float], ...]:
    words = text.split()
    step = (end - start) / len(words)
    return tuple(
        (word, round(start + index * step, 3), round(start + (index + 1) * step, 3))
        for index, word in enumerate(words)
    )


class CrosstalkAnalysis(FakeAnalysisAdapter):
    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        self.invocation_count += 1
        return SourceAnalysisResult(
            segments=tuple(
                CanonicalSegment(
                    segment_id=segment_id,
                    speaker_label=speaker,
                    start_seconds=start,
                    end_seconds=end,
                    text=text,
                    words=_words(text, start, end),
                )
                for segment_id, speaker, start, end, text in SEGMENTS
            ),
            pipeline_version="fixture-analysis-v9",
            diarization_model="fixture-sortformer",
            transcription_model="fixture-qwen3-asr",
            alignment_model="fixture-qwen3-aligner",
            speakers=tuple(
                AnalyzedSpeaker(
                    local_label=label,
                    reference_audio=f"RIFF{label}".encode(),
                    embedding=embedding,
                    confidence=0.98,
                )
                for label, embedding in (
                    ("speaker-01", (1.0, 0.0, 0.0)),
                    ("speaker-02", (0.0, 1.0, 0.0)),
                    ("speaker-03", (0.0, 0.0, 1.0)),
                )
            ),
            checksum="crosstalk-fixture",
        )


class CrosstalkTranslation(FakeTranslationAdapter):
    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        return replace(
            super().translate(candidate),
            segments=TARGETS,
            content_edit_json=json.dumps(
                {
                    "policy_version": "semantic-content-edit-v1",
                    "status": "accepted",
                    "decisions": [],
                    "crosstalk": CROSSTALK,
                },
                ensure_ascii=False,
            ),
        )


class CapturingSpeech(FakeSpeechAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.rendered_targets: tuple[str, ...] = ()
        self.last_analysis_json: str | None = None

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
            quality=AudioQualityReport(rendered_segments=len(translation.segments)),
        )


def test_crosstalk_lines_shape_the_audio_and_the_transcript(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    speech = CapturingSpeech()
    dependencies = replace(
        harness.dependencies,
        analysis=CrosstalkAnalysis(),
        translation=CrosstalkTranslation(),
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
        TARGETS[0],
        "我们本来早就能拿第一了。",
        "对。",
        "不可能。",
        TARGETS[-1],
    )
    rendered = json.loads(speech.last_analysis_json or "{}")["segments"]
    assert [(item["segment_id"], item["speaker_label"]) for item in rendered] == [
        ("seg-0001", "speaker-01"),
        ("seg-0002-retained-crosstalk", "speaker-02"),
        ("seg-0003-retained-crosstalk", "speaker-03"),
        ("seg-0005-retained-crosstalk", "speaker-01"),
        ("seg-0007", "speaker-01"),
    ]
    detail = client.get(f"/api/v1/episodes/{completed['episode_id']}").json()
    assert [segment["target_text"] for segment in detail["segments"]] == [
        TARGETS[0],
        "我们本来早就能拿第一了。",
        "对。",
        "",
        "不可能。",
        "",
        TARGETS[-1],
    ]
    assert detail["segments"][3]["source_text"] == "been number"
