from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import event

from transpodcast.database import Database
from transpodcast.dependencies import AppDependencies
from transpodcast.domain import (
    AnalyzedSpeaker,
    CanonicalSegment,
    ChannelResolution,
    OwnerNotification,
    PublicationMetadataResult,
    RecentVideoWindow,
    SourceAnalysisResult,
    SourceArchive,
    SourceCandidate,
    SourceThumbnail,
    SourceVideoPreview,
    SpeechResult,
    TranslationResult,
    TriageDecision,
    TriageResult,
)
from transpodcast.models import (
    CandidateVideoRecord,
    EpisodeRecord,
    ProcessingRunRecord,
    SettingRecord,
    SourceChannelRecord,
)
from transpodcast.pipeline_versions import (
    CURRENT_SPEECH_PIPELINE_VERSION,
    SPEECH_PIPELINE_V15,
    analysis_version_for_pipeline,
)
from transpodcast.speaker_ownership_v15 import (
    SpeakerOwnershipPlan,
    SpeakerOwnershipPlanningResult,
    SpeakerOwnershipRegion,
)
from transpodcast.storage import LocalPublicationStorage


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 28, 10, 0)

    def now(self) -> datetime:
        return self.value


@contextmanager
def capture_sql_statements(database: Database) -> Iterator[list[str]]:
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append(statement)

    event.listen(
        database.sqlalchemy_engine,
        "before_cursor_execute",
        capture_statement,
    )
    try:
        yield statements
    finally:
        event.remove(
            database.sqlalchemy_engine,
            "before_cursor_execute",
            capture_statement,
        )


def seed_owner_read_performance_fixture(
    database: Database,
    *,
    run_count: int,
    artifact_payload_bytes: int,
    include_episodes: bool,
) -> None:
    started_at = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    artifact = json.dumps(
        {"marker": "x" * artifact_payload_bytes},
        separators=(",", ":"),
    )
    with database.session() as session:
        channel = SourceChannelRecord(
            youtube_channel_id=f"UC-OWNER-READ-{run_count}",
            title="Owner Read Benchmark",
            canonical_url="https://www.youtube.com/@owner-read-benchmark",
            mode="agent_filtering",
            created_at=started_at,
        )
        session.add(channel)
        session.flush()
        for index in range(run_count):
            recorded_at = started_at + timedelta(minutes=index)
            candidate = CandidateVideoRecord(
                source_channel_id=channel.id,
                youtube_video_id=f"owner-read-{run_count}-{index:04d}",
                title=f"Owner Read Benchmark Episode {index + 1}",
                description="Synthetic local performance fixture.",
                duration_seconds=5_400,
                language="en",
                published_at=recorded_at,
                source_url=(
                    "https://www.youtube.com/watch?v="
                    f"owner-read-{run_count}-{index:04d}"
                ),
                captions=None,
                status="processed",
                triage_decision="process",
                triage_confidence=1.0,
                triage_rationale="Synthetic fixture.",
                matched_interests_json="[]",
                created_at=recorded_at,
            )
            session.add(candidate)
            session.flush()
            run = ProcessingRunRecord(
                candidate_video_id=candidate.id,
                status="completed",
                stage="publication_queue",
                pipeline_version="transpodcast-modal-pipeline-v14",
                analysis_json=artifact,
                translation_json=artifact,
                content_edit_json=artifact,
                publication_metadata_json=artifact,
                artwork_json=artifact,
                render_json=artifact,
                started_at=recorded_at,
                completed_at=recorded_at + timedelta(minutes=1),
            )
            session.add(run)
            session.flush()
            if include_episodes:
                session.add(
                    EpisodeRecord(
                        candidate_video_id=candidate.id,
                        processing_run_id=run.id,
                        title=f"Benchmark Episode {index + 1}",
                        overview="Synthetic episode overview.",
                        state="publication_queue",
                        duration_seconds=5_400,
                        target_locale="zh-CN",
                        source_show=channel.title,
                        source_title=candidate.title,
                        source_url=candidate.source_url,
                        source_published_at=candidate.published_at,
                        guests_json="[]",
                        transcript_json=artifact,
                        chapters_json="[]",
                        quality_json="{}",
                        preview_key=f"previews/benchmark-{index}.m4a",
                        preview_expires_at=recorded_at + timedelta(days=7),
                        publication_guid=f"owner-read-{run_count}-{index:04d}",
                        created_at=recorded_at,
                        published_at=None,
                    )
                )


class FakeSourceAdapter:
    """A production-shaped Source Adapter with deterministic behavior.

    Every operation records itself in ``operation_log`` and carries the
    delay profile measured against the production adapter, so product
    tests can assert both call counts and a simulated latency budget
    without touching live YouTube.
    """

    DELAY_PROFILE_MS = {
        "resolve_channel": 2436,
        "resolve_source": 2939,
        "recent_video_previews": 1025,
        "enrich_video_previews": 2500,
        "video": 2256,
        "captions": 724,
    }

    def __init__(
        self,
        candidates: list[SourceCandidate] | None = None,
    ) -> None:
        self.candidates = candidates
        self.archive_invocation_count = 0
        self.operation_log: list[str] = []

    def readiness(self) -> bool:
        return True

    def simulated_elapsed_ms(self) -> int:
        return sum(
            self.DELAY_PROFILE_MS.get(operation, 0)
            for operation in self.operation_log
        )

    def resolve_channel(self, url: str) -> ChannelResolution:
        self.operation_log.append("resolve_channel")
        return ChannelResolution(
            youtube_channel_id="UC_USEFUL_SHOW",
            title="Useful Show",
            canonical_url=url,
            avatar_url="https://example.com/useful-show-avatar.jpg",
            subscriber_count=892_000,
            video_count=512,
        )

    def resolve_source(
        self,
        url: str,
    ) -> tuple[ChannelResolution, SourceCandidate | None]:
        self.operation_log.append("resolve_source")
        channel = ChannelResolution(
            youtube_channel_id="UC_USEFUL_SHOW",
            title="Useful Show",
            canonical_url="https://www.youtube.com/@usefulshow",
            avatar_url="https://example.com/useful-show-avatar.jpg",
            subscriber_count=892_000,
            video_count=512,
        )
        if "watch?v=" not in url and "youtu.be/" not in url:
            return channel, None
        matching = next(
            (
                candidate
                for candidate in self._catalog()
                if candidate.source_url == url
            ),
            self._catalog()[0],
        )
        return channel, replace(matching, captions=None)

    def recent_video_previews(
        self,
        youtube_channel_id: str,
    ) -> RecentVideoWindow:
        assert youtube_channel_id == "UC_USEFUL_SHOW"
        self.operation_log.append("recent_video_previews")
        return RecentVideoWindow(
            previews=tuple(
                SourceVideoPreview(
                    youtube_video_id=candidate.youtube_video_id,
                    title=candidate.title,
                    duration_seconds=candidate.duration_seconds,
                    source_url=candidate.source_url,
                    published_at=None,
                    view_count=None,
                )
                for candidate in self._catalog()[:10]
            ),
            channel_avatar_url=(
                "https://example.com/useful-show-avatar.jpg"
            ),
        )

    def enrich_video_previews(
        self,
        previews: list[SourceVideoPreview],
    ) -> list[SourceVideoPreview]:
        self.operation_log.append("enrich_video_previews")
        by_id = {
            candidate.youtube_video_id: candidate
            for candidate in self._catalog()
        }
        enriched: list[SourceVideoPreview] = []
        for index, preview in enumerate(previews):
            candidate = by_id.get(preview.youtube_video_id)
            enriched.append(
                SourceVideoPreview(
                    youtube_video_id=preview.youtube_video_id,
                    title=preview.title,
                    duration_seconds=preview.duration_seconds,
                    source_url=preview.source_url,
                    published_at=(
                        candidate.published_at
                        if candidate is not None
                        else None
                    ),
                    view_count=10_000 * (index + 1),
                )
            )
        return enriched

    def _catalog(self) -> list[SourceCandidate]:
        return self.discover("UC_USEFUL_SHOW")

    def discover(self, youtube_channel_id: str) -> list[SourceCandidate]:
        assert youtube_channel_id == "UC_USEFUL_SHOW"
        if self.candidates is not None:
            return self.candidates
        return [
            SourceCandidate(
                youtube_video_id="useful-001",
                title="Building products that last",
                description="A practical conversation about durable products.",
                duration_seconds=3600,
                language="en",
                published_at=datetime(2026, 7, 28, 11, 0, tzinfo=UTC),
                source_url="https://www.youtube.com/watch?v=useful-001",
                captions="Products last when teams preserve intent.",
            )
        ]

    def recent_videos(
        self,
        youtube_channel_id: str,
    ) -> list[SourceCandidate]:
        return self.discover(youtube_channel_id)[:10]

    def video(self, url: str) -> SourceCandidate:
        self.operation_log.append("video")
        candidates = self.discover("UC_USEFUL_SHOW")
        matching = [
            candidate
            for candidate in candidates
            if candidate.source_url == url
        ]
        return matching[0] if matching else candidates[0]

    def supported_source_video(
        self,
        url: str,
    ) -> SourceCandidate | None:
        candidates = self.discover("UC_USEFUL_SHOW")
        return next(
            (
                candidate
                for candidate in candidates
                if candidate.source_url == url
            ),
            None,
        )

    def archive(self, candidate: SourceCandidate) -> SourceArchive:
        self.archive_invocation_count += 1
        content = b"M4A!deterministic-source-archive"
        return SourceArchive(
            content=content,
            extension="m4a",
            media_type="audio/mp4",
            checksum=hashlib.sha256(content).hexdigest(),
        )

    def thumbnail(self, candidate: SourceCandidate) -> SourceThumbnail:
        del candidate
        return SourceThumbnail(
            content=(
                b"\x89PNG\r\n\x1a\n"
                b"\x00\x00\x00\rIHDR"
                b"\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde"
                b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0"
                b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef"
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
            source_url="https://example.com/source-thumbnail.png",
            media_type="image/png",
            width=1,
            height=1,
        )


class FakeTriageAdapter:
    def __init__(
        self,
        results: dict[str, TriageResult] | None = None,
    ) -> None:
        self.results = results or {}
        self.invocation_count = 0

    def evaluate(
        self,
        candidate: SourceCandidate,
        interest_profile: str,
    ) -> TriageResult:
        self.invocation_count += 1
        assert candidate.captions
        assert "Product strategy" in interest_profile
        configured = self.results.get(candidate.youtube_video_id)
        if configured is not None:
            return configured
        return TriageResult(
            decision=TriageDecision.PROCESS,
            confidence=0.96,
            rationale="The video directly matches durable product strategy.",
            matched_interests=("product strategy",),
        )


class FakeAnalysisAdapter:
    def __init__(self) -> None:
        self.invocation_count = 0
        self.pending_adjudication = False

    def readiness(self) -> bool:
        return True

    def measure_ownership(
        self, analysis: SourceAnalysisResult, archive: SourceArchive,
        ranges: list[tuple[int, int]],
    ) -> dict[str, Any]:
        return {"voiceprints": [], "measurements": [], "inference_seconds": 0.0}

    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        self.invocation_count += 1
        assert archive.checksum
        if self.pending_adjudication:
            return self._pending_adjudication_analysis()
        text = candidate.captions or "Source transcript unavailable."
        return SourceAnalysisResult(
            segments=(
                CanonicalSegment(
                    segment_id="seg-0001",
                    speaker_label="speaker-01",
                    start_seconds=0,
                    end_seconds=float(candidate.duration_seconds),
                    text=text,
                    words=((text, 0.0, float(candidate.duration_seconds)),),
                ),
            ),
            # Like the Modal analysis, report the Source Analysis version.
            pipeline_version=analysis_version_for_pipeline(
                candidate.speech_pipeline_version or CURRENT_SPEECH_PIPELINE_VERSION
            ),
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
            boundary_adjudication_json=json.dumps({
                "continuity_evidence": {"voiceprints": [], "measured_window_count": 0}
            }),
            checksum=hashlib.sha256(text.encode()).hexdigest(),
            estimated_cost_usd=0.05,
            call_seconds=(
                ("diarization", 1.2),
                ("speaker_embeddings", 0.3),
                ("transcription", 2.4),
            ),
        )

    def _pending_adjudication_analysis(self) -> SourceAnalysisResult:
        """A pending short-boundary ownership candidate fixture."""
        segments = (
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker-01",
                start_seconds=0.0,
                end_seconds=10.0,
                text="we keep talking",
                words=(
                    ("we", 0.0, 1.0),
                    ("keep", 1.0, 2.0),
                    ("talking", 2.0, 10.0),
                ),
            ),
            CanonicalSegment(
                segment_id="seg-0002",
                speaker_label="speaker-02",
                start_seconds=10.1,
                end_seconds=10.4,
                text="and",
                words=(("and", 10.1, 10.4),),
            ),
            CanonicalSegment(
                segment_id="seg-0003",
                speaker_label="speaker-01",
                start_seconds=10.5,
                end_seconds=22.0,
                text="continue here",
                words=(
                    ("continue", 10.5, 11.5),
                    ("here", 11.5, 22.0),
                ),
            ),
            CanonicalSegment(
                segment_id="seg-0004",
                speaker_label="speaker-02",
                start_seconds=30.0,
                end_seconds=40.0,
                text="a different point",
                words=(
                    ("a", 30.0, 31.0),
                    ("different", 31.0, 33.0),
                    ("point", 33.0, 40.0),
                ),
            ),
        )
        adjudication = {
            "policy_version": "boundary-ownership-adjudication-v3",
            "dispute_count": 1,
            "candidate_count": 1,
            "short_boundary_candidate_count": 1,
            "sparse_label_candidate_count": 1,
            "stable_segment_count": 3,
            "changed_count": 0,
            "marked_count": 0,
            "decision_records": [],
            "pending": {
                "disputes": [
                    {
                        "dispute_id": "dispute-10.1-speaker-02",
                        "words": [
                            {
                                "text": "and",
                                "start_seconds": 10.1,
                                "end_seconds": 10.4,
                            }
                        ],
                        "current_owner": "speaker-02",
                        "counterpart": "speaker-01",
                        "candidate_owners": [
                            "speaker-01",
                            "speaker-02",
                        ],
                        "trigger_reasons": [
                            "short-boundary",
                            "sparse-label",
                        ],
                        "group_id": "group-10.1",
                        "context_before_owner": "speaker-01",
                        "context_after_owner": "speaker-01",
                        "context_before": ["we", "keep", "talking"],
                        "context_after": ["continue", "here"],
                    }
                ],
                "voiceprints": {"dispute-10.1-speaker-02": None},
            },
        }
        consolidation = {
            "policy_version": "episode-speaker-consolidation-v1",
            "decision_records": [],
            "speaker_mapping": {
                "speaker-01": "speaker-01",
                "speaker-02": "speaker-02",
            },
            "window_count": 0,
        }
        text = "we keep talking and continue here"
        return SourceAnalysisResult(
            segments=segments,
            pipeline_version=SPEECH_PIPELINE_V15,
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
                AnalyzedSpeaker(
                    local_label="speaker-02",
                    reference_audio=b"RIFFfixture-counterpart-reference",
                    embedding=(0.0, 1.0, 0.0),
                    confidence=0.98,
                ),
            ),
            speaker_consolidation_json=json.dumps(consolidation),
            boundary_adjudication_json=json.dumps(adjudication),
            checksum=hashlib.sha256(text.encode()).hexdigest(),
            estimated_cost_usd=0.05,
            call_seconds=(
                ("diarization", 1.2),
                ("speaker_embeddings", 0.3),
                ("transcription", 2.4),
            ),
        )


class FakeTranslationAdapter:
    """Deterministic Translation Adapter for one fake Agent provider."""

    def __init__(self, provider: str = "fake-codex") -> None:
        self.provider = provider
        self.invocation_count = 0
        self.ownership_invocation_count = 0
        self.metadata_invocation_count = 0
        self.discourse_error: str | None = None
        self.speaker_ownership_plans: dict[str, SpeakerOwnershipPlan] = {}
        self.speaker_ownership_reviews: dict[str, SpeakerOwnershipPlan] = {}
        self.planned_speaker_ownership_regions: list[
            SpeakerOwnershipRegion
        ] = []
        self.reviewed_speaker_ownership_regions: list[
            SpeakerOwnershipRegion
        ] = []

    def readiness(self) -> bool:
        return True

    def plan_ownership_continuity(
        self, regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult:
        self.ownership_invocation_count += 1
        return self.plan_speaker_ownership(regions)

    def review_ownership_continuity(
        self, regions: Sequence[SpeakerOwnershipRegion],
        planning: SpeakerOwnershipPlanningResult,
    ) -> SpeakerOwnershipPlanningResult:
        self.ownership_invocation_count += 1
        return self.review_speaker_ownership(regions)

    def plan_speaker_ownership(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult:
        self.planned_speaker_ownership_regions.extend(regions)
        if self.discourse_error is not None:
            return SpeakerOwnershipPlanningResult(
                plans={},
                error=self.discourse_error,
            )
        return SpeakerOwnershipPlanningResult(
            plans={
                region.region_id: self.speaker_ownership_plans.get(
                    region.region_id,
                    SpeakerOwnershipPlan(
                        action="keep",
                        rationale="Deterministic fixture keep plan.",
                    ),
                )
                for region in regions
            },
            input_tokens=40 if regions else 0,
            output_tokens=15 if regions else 0,
            provider=self.provider if regions else "",
            model="deterministic-ownership-planner" if regions else "",
            cli_version="fixture-cli 1.0" if regions else "",
        )

    def review_speaker_ownership(
        self,
        regions: Sequence[SpeakerOwnershipRegion],
    ) -> SpeakerOwnershipPlanningResult:
        self.reviewed_speaker_ownership_regions.extend(regions)
        return SpeakerOwnershipPlanningResult(
            plans={
                region.region_id: self.speaker_ownership_reviews.get(
                    region.region_id,
                    SpeakerOwnershipPlan(
                        action="keep",
                        rationale="Deterministic fixture keep review.",
                    ),
                )
                for region in regions
            },
            input_tokens=30 if regions else 0,
            output_tokens=10 if regions else 0,
            provider=self.provider if regions else "",
            model="deterministic-ownership-reviewer" if regions else "",
            cli_version="fixture-cli 1.0" if regions else "",
        )

    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        self.invocation_count += 1
        return TranslationResult(
            title=f"Useful Show · {candidate.title}",
            overview="A complete translated conversation about durable products.",
            target_locale="zh-CN",
            segments=("A product should preserve its original intent.",),
            provider=self.provider,
            model="deterministic-fixture",
            input_tokens=120,
            output_tokens=80,
            reviewed=True,
            review_model="deterministic-review-fixture",
            input_hash="fixture-input-hash",
            output_hash="fixture-output-hash",
        )

    def publication_metadata(
        self,
        candidate: SourceCandidate,
        current: TranslationResult,
    ) -> PublicationMetadataResult:
        del current
        self.metadata_invocation_count += 1
        source_segments = json.loads(candidate.analysis_json or "{}").get(
            "segments",
            [],
        )
        artifact = {
            "policy_version": "publication-metadata-v1",
            "status": "accepted",
            "description": (
                f"{chr(0x4E00) * 180}\n\n{chr(0x4E01) * 180}"
            ),
            "participant_line": "",
            "chapters": [
                {
                    "anchor_segment_id": str(item["segment_id"]),
                    "title": chr(0x4E10 + index) * 4,
                    "rationale": "Deterministic metadata fixture.",
                }
                for index, item in enumerate(source_segments[:4], start=1)
            ],
        }
        return PublicationMetadataResult(
            artifact_json=json.dumps(artifact, ensure_ascii=False),
            provider=self.provider,
            model="deterministic-metadata-fixture",
            input_tokens=30,
            output_tokens=20,
            cli_version="fixture-cli 1.0",
            session_id="fixture-metadata-session",
        )

    def retranslate_segment(
        self,
        candidate: SourceCandidate,
        current: TranslationResult,
        segment_ordinal: int,
    ) -> TranslationResult:
        self.invocation_count += 1
        segments = list(current.segments)
        source_segments = json.loads(
            candidate.analysis_json or "{}"
        ).get("segments", [])
        translated_segments = list(source_segments)
        source_text = str(
            translated_segments[segment_ordinal - 1]["text"]
        )
        segments[segment_ordinal - 1] = (
            f"Corrected translation: {source_text}"
        )
        return replace(
            current,
            segments=tuple(segments),
            input_tokens=current.input_tokens + 20,
            output_tokens=current.output_tokens + 10,
            elapsed_ms=current.elapsed_ms + 5,
            input_hash="fixture-revision-input-hash",
            output_hash="fixture-revision-output-hash",
        )


class FakeSpeechAdapter:
    def __init__(self) -> None:
        self.invocation_count = 0
        self.last_render_scope: tuple[str, int | None] | None = None
        self.last_analysis_json: str | None = None

    def readiness(self) -> bool:
        return True

    def listening_audit_readiness(self) -> bool:
        return True

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        self.invocation_count += 1
        self.last_render_scope = ("full", None)
        self.last_analysis_json = candidate.analysis_json
        return self._result(translation)

    def render_segment(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
        segment_ordinal: int,
        prior_preview_key: str,
    ) -> SpeechResult:
        del prior_preview_key
        self.invocation_count += 1
        self.last_render_scope = ("segment", segment_ordinal)
        self.last_analysis_json = candidate.analysis_json
        return self._result(translation)

    def remaster(self, speech: SpeechResult) -> SpeechResult:
        return speech

    @staticmethod
    def _result(translation: TranslationResult) -> SpeechResult:
        assert translation.target_locale == "zh-CN"
        return SpeechResult(
            publication_audio=b"M4A!deterministic-audio-fixture",
            duration_seconds=3720,
            chapters=((0, "Introduction"), (900, "Durability")),
            pipeline_version="fixture-pipeline-v1",
            segment_timings=tuple(
                (float(index), float(index + 1))
                for index in range(len(translation.segments))
            ),
        )


class FakeTelegramAdapter:
    def __init__(self) -> None:
        self.messages: list[OwnerNotification] = []
        self.completed_messages: list[tuple[str, str]] = []

    def readiness(self) -> bool:
        return True

    def send(self, notification: OwnerNotification) -> str:
        self.messages.append(notification)
        return str(len(self.messages))

    def latest(self) -> OwnerNotification:
        return self.messages[-1]

    def complete_message(self, message_id: str, outcome: str) -> None:
        self.completed_messages.append((message_id, outcome))


@dataclass(frozen=True)
class AcceptanceHarness:
    """Ready dependencies with deterministic fakes for both Agent providers.

    ``codex`` serves the default provider through ``translation``; ``claude``
    is registered under ``agent_translations`` so tests can select it with
    the ``agent_provider`` setting.
    """

    dependencies: AppDependencies
    codex: FakeTranslationAdapter
    modal: FakeSpeechAdapter
    telegram: FakeTelegramAdapter
    storage: LocalPublicationStorage
    feed_token: str
    claude: FakeTranslationAdapter

    @classmethod
    def ready(cls, root: Path) -> AcceptanceHarness:
        return cls.for_candidates(root, candidates=None, triage_results=None)

    @classmethod
    def for_candidates(
        cls,
        root: Path,
        candidates: list[SourceCandidate] | None,
        triage_results: dict[str, TriageResult] | None,
    ) -> AcceptanceHarness:
        codex = FakeTranslationAdapter()
        claude = FakeTranslationAdapter(provider="fake-claude")
        modal = FakeSpeechAdapter()
        telegram = FakeTelegramAdapter()
        storage = LocalPublicationStorage(root / "object-storage")
        feed_token = "test-feed-token-with-sufficient-entropy"
        database = Database(root / "transpodcast.sqlite3")
        # Production defaults to manual processing. The legacy
        # acceptance flows exercise the automatic pipeline, so the
        # harness pre-seeds automatic mode; onboarding tests opt back
        # into the manual default explicitly.
        database.create_schema()
        with database.session() as session:
            session.add(
                SettingRecord(
                    key="manual_processing",
                    value_json="false",
                )
            )
        dependencies = AppDependencies(
            database=database,
            source=FakeSourceAdapter(candidates),
            triage=FakeTriageAdapter(triage_results),
            translation=codex,
            speech=modal,
            storage=storage,
            notifications=telegram,
            clock=FixedClock(),
            feed_token=feed_token,
            publication_domain="http://testserver",
            artifact_root=root / "artifacts",
            analysis=FakeAnalysisAdapter(),
            feed_token_path=root / "secrets" / "feed-token",
            agent_translations={"claude": claude},
            agent_triage={"claude": FakeTriageAdapter(triage_results)},
        )
        return cls(
            dependencies=dependencies,
            codex=codex,
            modal=modal,
            telegram=telegram,
            storage=storage,
            feed_token=feed_token,
            claude=claude,
        )

    @property
    def live_provider_invocation_count(self) -> int:
        return 0
