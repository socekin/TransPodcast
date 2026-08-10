from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from transpodcast.decision_records import (
    DECISION_LEDGER_SCHEMA_VERSION,
    DecisionLedger,
    DecisionRecord,
)
from transpodcast.models import Base


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as value:
            yield value
    finally:
        engine.dispose()


def _record(**overrides) -> DecisionRecord:
    values: dict = {
        "kind": "consolidation",
        "policy_version": "speaker-consolidation-v2",
        "subject": {"source_label": "speaker_0"},
        "evidence": {"median_similarity": 0.73, "window_votes": 4},
        "verdict": "merged",
        "rationale": (
            "Every screened window voted speaker_2; median similarity 0.73."
        ),
    }
    values.update(overrides)
    return DecisionRecord(**values)


def test_ledger_round_trips_through_payload() -> None:
    ledger = DecisionLedger(
        processing_run_id=8,
        records=(
            _record(),
            _record(
                kind="reference-selection",
                subject={"speaker": "speaker_2"},
                evidence={"start_seconds": 213.0, "end_seconds": 225.0},
                verdict="selected",
                rationale="Longest surviving turn after head/tail trim.",
            ),
        ),
    )

    payload = ledger.to_payload()

    assert payload["schema_version"] == DECISION_LEDGER_SCHEMA_VERSION
    assert [record["kind"] for record in payload["records"]] == [
        "consolidation",
        "reference-selection",
    ]
    assert DecisionLedger.from_payload(payload) == ledger


def test_record_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        _record(kind="vibes")


def test_record_rejects_blank_rationale() -> None:
    with pytest.raises(ValueError):
        _record(rationale="   ")


def test_record_rejects_blank_policy_version() -> None:
    with pytest.raises(ValueError):
        _record(policy_version="")


def test_persisted_ledger_is_content_addressed_and_resolvable(
    session,
) -> None:
    ledger = DecisionLedger(processing_run_id=8, records=(_record(),))
    created_at = datetime(2026, 8, 1, tzinfo=UTC)

    first = ledger.persist(session, created_at=created_at)
    second = ledger.persist(session, created_at=created_at)

    assert first == second
    assert DecisionLedger.resolve(session, first) == ledger


def test_different_ledgers_produce_different_references(session) -> None:
    created_at = datetime(2026, 8, 1, tzinfo=UTC)
    kept = DecisionLedger(
        processing_run_id=8,
        records=(_record(verdict="kept"),),
    )
    merged = DecisionLedger(
        processing_run_id=8,
        records=(_record(verdict="merged"),),
    )

    assert kept.persist(session, created_at=created_at) != merged.persist(
        session,
        created_at=created_at,
    )


def test_record_order_is_replay_order() -> None:
    ledger = DecisionLedger(
        processing_run_id=8,
        records=tuple(
            _record(subject={"source_label": f"speaker_{index}"})
            for index in range(4)
        ),
    )

    restored = DecisionLedger.from_payload(ledger.to_payload())

    assert [
        record.subject["source_label"] for record in restored.records
    ] == ["speaker_0", "speaker_1", "speaker_2", "speaker_3"]
