"""Quality Gate reading of the Listening Audit (D3) for V19/V20 renders.

The render records the audit in ``render_settings_json.listening_audit``. The
Quality Gate applies the Owner's D3 decision:

- hard findings that remain after the one automatic re-render
  (``blocking_unit_ids``) send the run to the Review Queue, until the Owner
  continues past them;
- soft findings are listed on the Publication Queue item and never block;
- a missing or failed audit is an explicit ``audit_unavailable`` finding. It
  is listed and does not block: production stays fully automatic, and the
  deterministic audio checks still gate the episode.

Historical renders (V15-V18) carry no audit; nothing is reported for them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from transpodcast.pipeline_versions import RHYTHM_PIPELINES

_SUMMARY_FINDINGS = 3


@dataclass(frozen=True)
class ListeningAuditVerdict:
    payload: dict[str, Any]

    @property
    def blocking_unit_ids(self) -> list[str]:
        value = self.payload.get("blocking_unit_ids")
        return [str(item) for item in value] if isinstance(value, list) else []

    @property
    def acknowledged(self) -> bool:
        return self.payload.get("owner_acknowledged") is True

    @property
    def blocks_publication(self) -> bool:
        return bool(self.blocking_unit_ids) and not self.acknowledged

    def review_summary(self) -> str:
        """Owner-facing Review Queue summary naming the remaining failures."""
        blocking = set(self.blocking_unit_ids)
        details = [
            f"{item.get('unit_id')} ({item.get('kind')}: {item.get('detail')})"
            for item in _findings(self.payload)
            if item.get("severity") == "hard" and item.get("unit_id") in blocking
        ]
        shown = "; ".join(details[:_SUMMARY_FINDINGS])
        more = len(details) - _SUMMARY_FINDINGS
        return (
            "Listening Audit hard findings remain after one automatic "
            f"re-render in {len(blocking)} unit(s): {shown}"
            + (f"; and {more} more" if more > 0 else "")
        )


def listening_audit_verdict(
    pipeline_version: str,
    render_settings_json: str,
) -> ListeningAuditVerdict | None:
    """Return the audit verdict, or ``None`` for pipelines without an audit."""
    if pipeline_version not in RHYTHM_PIPELINES:
        return None
    settings = _mapping(render_settings_json)
    payload = settings.get("listening_audit")
    if not isinstance(payload, dict):
        return ListeningAuditVerdict(unavailable_payload())
    return ListeningAuditVerdict(dict(payload))


def unavailable_payload() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "hard_unit_ids": [],
        "blocking_unit_ids": [],
        "findings": [
            {
                "unit_id": "",
                "kind": "audit_unavailable",
                "severity": "soft",
                "detail": "the render recorded no Listening Audit",
                "parent_segment_id": None,
            }
        ],
    }


def listening_audit_summary(verdict: ListeningAuditVerdict) -> dict[str, Any]:
    """The bounded view for the Quality Gate record and the TransPodcast Host."""
    payload = verdict.payload
    findings = _findings(payload)
    retry = payload.get("retry")
    usage = payload.get("usage")
    return {
        "status": str(payload.get("status") or "unavailable"),
        "policy_version": payload.get("policy_version"),
        "scope": payload.get("scope"),
        "unit_count": payload.get("unit_count"),
        "mean_cer": payload.get("mean_cer"),
        "hard_count": sum(item.get("severity") == "hard" for item in findings),
        "soft_count": sum(
            item.get("severity") == "soft"
            and item.get("kind") != "audit_unavailable"
            for item in findings
        ),
        "unavailable_count": sum(
            item.get("kind") == "audit_unavailable" for item in findings
        ),
        "hard_unit_ids": list(payload.get("hard_unit_ids") or []),
        "blocking_unit_ids": verdict.blocking_unit_ids,
        "owner_acknowledged": verdict.acknowledged,
        "findings": findings,
        "retry": (
            {
                key: retry.get(key)
                for key in ("seed", "status", "unit_ids", "resolved_unit_ids")
            }
            if isinstance(retry, dict)
            else None
        ),
        "gpu_seconds": (
            usage.get("gpu_seconds") if isinstance(usage, dict) else None
        ),
    }


def acknowledge_listening_audit(
    render_json: str,
    acknowledged_at: datetime,
) -> str:
    """Record the Owner's decision to continue past remaining hard findings.

    The acknowledgment lives in the render checkpoint next to the audit it
    answers, so a new render starts unacknowledged.
    """
    checkpoint = json.loads(render_json)
    settings = _mapping(str(checkpoint.get("render_settings_json") or "{}"))
    payload = settings.get("listening_audit")
    if not isinstance(payload, dict):
        return render_json
    settings["listening_audit"] = {
        **payload,
        "owner_acknowledged": True,
        "owner_acknowledged_at": acknowledged_at.isoformat(),
    }
    checkpoint["render_settings_json"] = json.dumps(
        settings,
        ensure_ascii=False,
        sort_keys=True,
    )
    return json.dumps(checkpoint)


def findings_by_segment(
    payload: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Group unit findings by their Canonical segment for Processing Detail."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in _findings(payload):
        segment_id = item.get("parent_segment_id")
        if isinstance(segment_id, str) and segment_id:
            grouped.setdefault(segment_id, []).append(item)
    return grouped


def _findings(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("findings")
    if not isinstance(value, list):
        return []
    return [
        {
            "unit_id": str(item.get("unit_id") or ""),
            "parent_segment_id": item.get("parent_segment_id"),
            "kind": str(item.get("kind") or ""),
            "severity": str(item.get("severity") or ""),
            "detail": str(item.get("detail") or ""),
            "output_start_seconds": item.get("output_start_seconds"),
            "output_end_seconds": item.get("output_end_seconds"),
        }
        for item in value
        if isinstance(item, Mapping)
    ]


def _mapping(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
