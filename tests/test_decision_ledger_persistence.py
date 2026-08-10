from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from transpodcast.decision_records import DecisionLedger
from transpodcast.models import Base
from transpodcast.service import TransPodcastService


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as value:
            yield value
    finally:
        engine.dispose()


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 1, tzinfo=UTC)


def _analysis_payload(records: list[dict]) -> str:
    return json.dumps(
        {
            "segments": [],
            "speakers": [],
            "speaker_consolidation": {
                "policy_version": "speaker-consolidation-v2",
                "speaker_mapping": {"speaker-0": "speaker-1"},
                "decision_records": records,
            },
        }
    )


def _record_payload() -> dict:
    return {
        "kind": "consolidation",
        "policy_version": "speaker-consolidation-v2",
        "subject": {"source_label": "speaker-0"},
        "evidence": {"median_similarity": 0.72},
        "verdict": "merged",
        "rationale": "Unanimous window vote with a clear median.",
    }


def _service_shell() -> TransPodcastService:
    service = TransPodcastService.__new__(TransPodcastService)
    dependencies = type("Deps", (), {"clock": _Clock()})()
    service.dependencies = dependencies
    return service


def test_ledger_is_persisted_and_referenced(session) -> None:
    run = SimpleNamespace(
        id=8,
        analysis_json=_analysis_payload([_record_payload()]),
    )

    _service_shell()._persist_decision_ledger(session, run)

    stored = json.loads(run.analysis_json)
    reference = stored["speaker_consolidation"][
        "decision_ledger_reference"
    ]
    ledger = DecisionLedger.resolve(
        session,
        json.dumps(reference),
    )
    assert ledger.processing_run_id == 8
    assert ledger.records[0].verdict == "merged"


def test_consolidation_and_adjudication_share_one_ledger(
    session,
) -> None:
    adjudication_record = {
        "kind": "ownership-adjudication",
        "policy_version": "boundary-ownership-adjudication-v1",
        "subject": {"dispute_id": "dispute-371.441-speaker-3"},
        "evidence": {"current_owner": "speaker-3"},
        "verdict": "reassigned-by-agreement",
        "rationale": "Both judges assigned the other Speaker.",
    }
    payload = json.loads(_analysis_payload([_record_payload()]))
    payload["boundary_adjudication"] = {
        "policy_version": "boundary-ownership-adjudication-v1",
        "decision_records": [adjudication_record],
    }
    run = SimpleNamespace(id=8, analysis_json=json.dumps(payload))

    _service_shell()._persist_decision_ledger(session, run)

    stored = json.loads(run.analysis_json)
    consolidation_ref = stored["speaker_consolidation"][
        "decision_ledger_reference"
    ]
    adjudication_ref = stored["boundary_adjudication"][
        "decision_ledger_reference"
    ]
    assert consolidation_ref == adjudication_ref
    ledger = DecisionLedger.resolve(
        session,
        json.dumps(consolidation_ref),
    )
    assert [record.kind for record in ledger.records] == [
        "consolidation",
        "ownership-adjudication",
    ]


def test_analysis_without_records_is_left_untouched(session) -> None:
    original = json.dumps({"segments": [], "speakers": []})
    run = SimpleNamespace(id=8, analysis_json=original)

    _service_shell()._persist_decision_ledger(session, run)

    assert run.analysis_json == original
