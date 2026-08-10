"""Decision Records: durable evidence for automatic speaker decisions.

Every automatic decision made while interpreting a source episode —
Episode-local Speaker Consolidation verdicts, Boundary Ownership
Adjudication votes, Voice Reference selection, and fallbacks — persists a
structured record so any later defect can be replayed and diagnosed. The
v6-v8 retrospective's core process failure was that production decisions
retained no scores; this module is the guarantee that it cannot recur.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from transpodcast.artifacts import (
    persist_structured_artifact,
    resolve_structured_artifact,
)

DECISION_LEDGER_SCHEMA_VERSION = "speaker-decision-ledger-v1"
DECISION_LEDGER_ARTIFACT_KIND = "speaker-decision-ledger"
DECISION_RECORD_KINDS = frozenset(
    {
        "consolidation",
        "ownership-adjudication",
        "speaker-tail-boundary-repair",
        "speaker-boundary-adjudication",
        "reference-selection",
        "segment-attribution",
        "fallback",
    }
)


@dataclass(frozen=True)
class DecisionRecord:
    kind: str
    policy_version: str
    subject: dict[str, Any]
    evidence: dict[str, Any]
    verdict: str
    rationale: str

    def __post_init__(self) -> None:
        if self.kind not in DECISION_RECORD_KINDS:
            raise ValueError(
                f"Unknown Decision Record kind: {self.kind!r}"
            )
        if not self.policy_version.strip():
            raise ValueError("A Decision Record requires a policy version")
        if not self.verdict.strip():
            raise ValueError("A Decision Record requires a verdict")
        if not self.rationale.strip():
            raise ValueError("A Decision Record requires a rationale")

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "policy_version": self.policy_version,
            "subject": self.subject,
            "evidence": self.evidence,
            "verdict": self.verdict,
            "rationale": self.rationale,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DecisionRecord:
        return cls(
            kind=str(payload["kind"]),
            policy_version=str(payload["policy_version"]),
            subject=dict(payload["subject"]),
            evidence=dict(payload["evidence"]),
            verdict=str(payload["verdict"]),
            rationale=str(payload["rationale"]),
        )


@dataclass(frozen=True)
class DecisionLedger:
    """The complete, replay-ordered decision evidence for one run."""

    processing_run_id: int
    records: tuple[DecisionRecord, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": DECISION_LEDGER_SCHEMA_VERSION,
            "processing_run_id": self.processing_run_id,
            "records": [record.to_payload() for record in self.records],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DecisionLedger:
        if payload.get("schema_version") != DECISION_LEDGER_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported Decision Ledger schema: "
                f"{payload.get('schema_version')!r}"
            )
        return cls(
            processing_run_id=int(payload["processing_run_id"]),
            records=tuple(
                DecisionRecord.from_payload(record)
                for record in payload["records"]
            ),
        )

    def persist(self, session: Session, *, created_at: datetime) -> str:
        return persist_structured_artifact(
            session,
            artifact_kind=DECISION_LEDGER_ARTIFACT_KIND,
            payload=_canonical_json(self.to_payload()),
            created_at=created_at,
        )

    @classmethod
    def resolve(
        cls,
        session: Session,
        reference_json: str,
    ) -> DecisionLedger:
        return cls.from_payload(
            json.loads(resolve_structured_artifact(session, reference_json))
        )


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
