from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.domain import (
    AnalyzedSpeaker,
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


class InterleavedAnalysisAdapter(FakeAnalysisAdapter):
    """Reproduces the A-B-A attribution flicker in a completed run."""

    def analyze(
        self,
        candidate: SourceCandidate,
        archive: SourceArchive,
    ) -> SourceAnalysisResult:
        del candidate, archive
        return SourceAnalysisResult(
            segments=(
                CanonicalSegment(
                    segment_id="seg-0001",
                    speaker_label="speaker-01",
                    start_seconds=0.0,
                    end_seconds=7.3,
                    text="Look, the thing about this economy is",
                ),
                CanonicalSegment(
                    segment_id="seg-0002",
                    speaker_label="speaker-02",
                    start_seconds=7.3,
                    end_seconds=7.9,
                    text="No way",
                    overlap=True,
                    words=(("No", 7.31, 7.53), ("way", 7.53, 7.88)),
                ),
                CanonicalSegment(
                    segment_id="seg-0003",
                    speaker_label="speaker-01",
                    start_seconds=7.9,
                    end_seconds=14.0,
                    text="that people keep being surprised by it.",
                ),
            ),
            pipeline_version="fixture-analysis-v1",
            diarization_model="fixture-sortformer",
            transcription_model="fixture-qwen3-asr",
            alignment_model="fixture-qwen3-aligner",
            speakers=(
                AnalyzedSpeaker(
                    local_label="speaker-01",
                    reference_audio=b"RIFFfixture-one",
                    embedding=(1.0, 0.0, 0.0),
                    confidence=0.98,
                ),
                AnalyzedSpeaker(
                    local_label="speaker-02",
                    reference_audio=b"RIFFfixture-two",
                    embedding=(0.0, 1.0, 0.0),
                    confidence=0.97,
                ),
            ),
            checksum="fixture-interleaved-checksum",
            estimated_cost_usd=0.05,
        )


class ThreeSegmentTranslationAdapter(FakeTranslationAdapter):
    def translate(self, candidate: SourceCandidate) -> TranslationResult:
        return replace(
            super().translate(candidate),
            segments=("这件事的关键在于", "不可能", "它总是不断发生。"),
        )


class ThreeSegmentSpeechAdapter(FakeSpeechAdapter):
    """Keeps the Quality Gate satisfied for a three-segment episode."""

    def render(
        self,
        candidate: SourceCandidate,
        translation: TranslationResult,
    ) -> SpeechResult:
        speech = super().render(candidate, translation)
        return replace(
            speech,
            quality=replace(
                speech.quality,
                rendered_segments=len(translation.segments),
            ),
        )


def _completed_episode(root: Path) -> AcceptanceHarness:
    harness = AcceptanceHarness.ready(root)
    client = TestClient(create_app(harness.dependencies))
    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    client.post("/api/v1/discovery-runs")
    outcome = client.post("/api/v1/processing-runs:advance").json()
    assert outcome["processing_state"] == "completed", outcome
    return harness


def _run_cli(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "TRANSPODCAST_DATABASE_PATH": str(root / "transpodcast.sqlite3"),
        "TRANSPODCAST_STORAGE_MODE": "local",
        "TRANSPODCAST_LOCAL_STORAGE_ROOT": str(root / "object-storage"),
    }
    return subprocess.run(
        [
            sys.executable,
            "scripts/episode_evaluation_report.py",
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _interleaved_episode(root: Path) -> None:
    """Complete a run whose transcript carries the A-B-A flicker."""
    harness = AcceptanceHarness.ready(root)
    dependencies = replace(
        harness.dependencies,
        analysis=InterleavedAnalysisAdapter(),
        translation=ThreeSegmentTranslationAdapter(),
        speech=ThreeSegmentSpeechAdapter(),
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
    outcome = client.post("/api/v1/processing-runs:advance").json()
    assert outcome["processing_state"] == "completed", outcome
    dependencies.database.dispose()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_writes_a_report_without_modifying_the_system_of_record(
    tmp_path: Path,
) -> None:
    harness = _completed_episode(tmp_path)
    harness.dependencies.database.dispose()
    database_path = tmp_path / "transpodcast.sqlite3"
    before = _file_digest(database_path)
    output = tmp_path / "reports"

    completed = _run_cli(tmp_path, "--all", "--output", str(output))

    assert completed.returncode == 0, completed.stderr
    reports = sorted(output.rglob("report.html"))
    assert len(reports) == 1
    page = reports[0].read_text(encoding="utf-8")
    assert "节目评测报告" in page
    assert (output / "index.html").is_file()
    # The read-only guarantee, asserted as observable behavior.
    assert _file_digest(database_path) == before


def test_cli_replays_previously_exported_verdicts(tmp_path: Path) -> None:
    _interleaved_episode(tmp_path)
    output = tmp_path / "reports"
    assert _run_cli(tmp_path, "--all", "--output", str(output)).returncode == 0
    payload = json.loads(
        (sorted(output.rglob("report.json"))[0]).read_text(encoding="utf-8")
    )
    assert payload["summary"]["reviewed_count"] == 0
    key = payload["findings"][0]["key"]
    verdicts_path = tmp_path / "verdicts.json"
    verdicts_path.write_text(
        json.dumps({"verdicts": {key: "segmentation_error"}}),
        encoding="utf-8",
    )

    completed = _run_cli(
        tmp_path,
        "--all",
        "--output",
        str(output),
        "--verdicts",
        str(verdicts_path),
    )

    assert completed.returncode == 0, completed.stderr
    replayed = json.loads(
        (sorted(output.rglob("report.json"))[0]).read_text(encoding="utf-8")
    )
    assert replayed["summary"]["reviewed_count"] == 1
    recorded = next(
        finding for finding in replayed["findings"] if finding["key"] == key
    )
    assert recorded["verdict"] == "segmentation_error"


def test_report_surfaces_an_interleave_suspect_from_a_completed_run(
    tmp_path: Path,
) -> None:
    _interleaved_episode(tmp_path)
    output = tmp_path / "reports"

    completed = _run_cli(tmp_path, "--all", "--output", str(output))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(
        (sorted(output.rglob("report.json"))[0]).read_text(encoding="utf-8")
    )
    kinds = [finding["kind"] for finding in payload["findings"]]
    assert "interleave_suspect" in kinds
    assert "crosstalk" in kinds
    page = (sorted(output.rglob("report.html"))[0]).read_text(encoding="utf-8")
    assert "穿插嫌疑" in page
    assert "不可能" in page
    # The Owner can seek to the suspect moment in the publication.
    assert "data-seek=" in page


def test_an_episode_can_be_selected_by_the_identifier_the_tool_prints(
    tmp_path: Path,
) -> None:
    # The run listing prints an episode guid, so passing that guid back
    # must work; requiring a separate numeric id makes copy-paste fail.
    _interleaved_episode(tmp_path)
    output = tmp_path / "reports"
    listing = _run_cli(tmp_path, "--all", "--output", str(output), "--no-audio")
    assert listing.returncode == 0, listing.stderr
    guid = json.loads(
        (sorted(output.rglob("report.json"))[0]).read_text(encoding="utf-8")
    )["episode"]["guid"]
    assert guid in listing.stdout
    assert "run 1" in listing.stdout

    single = _run_cli(
        tmp_path,
        "--episode",
        guid,
        "--output",
        str(tmp_path / "one"),
        "--no-audio",
    )

    assert single.returncode == 0, single.stderr
    assert (tmp_path / "one" / guid / "report.html").is_file()


def test_run_selector_accepts_either_identifier_form(tmp_path: Path) -> None:
    # The listing shows a guid and a run id side by side. Requiring the
    # Owner to remember which flag takes which form is a trap, so the
    # selector accepts whichever identifier is pasted back.
    _interleaved_episode(tmp_path)
    listing = _run_cli(
        tmp_path, "--all", "--output", str(tmp_path / "all"), "--no-audio"
    )
    assert listing.returncode == 0, listing.stderr
    guid = json.loads(
        (sorted((tmp_path / "all").rglob("report.json"))[0]).read_text(
            encoding="utf-8"
        )
    )["episode"]["guid"]

    by_guid = _run_cli(
        tmp_path, "--run", guid, "--output", str(tmp_path / "g"), "--no-audio"
    )
    by_number = _run_cli(
        tmp_path, "--run", "1", "--output", str(tmp_path / "n"), "--no-audio"
    )

    assert by_guid.returncode == 0, by_guid.stderr
    assert by_number.returncode == 0, by_number.stderr
    assert (tmp_path / "g" / guid / "report.html").is_file()
    assert (tmp_path / "n" / guid / "report.html").is_file()


def test_an_unknown_identifier_explains_itself(tmp_path: Path) -> None:
    _interleaved_episode(tmp_path)

    completed = _run_cli(tmp_path, "--run", "does-not-exist", "--no-audio")

    assert completed.returncode != 0
    assert "does-not-exist" in completed.stderr + completed.stdout
