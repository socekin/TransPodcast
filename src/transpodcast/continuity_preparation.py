"""Shared immutable ownership preparation for acoustic and worker stages."""

from collections.abc import Sequence
from typing import Any

from transpodcast.boundary_adjudication import OwnershipDispute, VoiceprintVerdict
from transpodcast.domain import CanonicalSegment
from transpodcast.speaker_ownership_continuity import (
    ContinuityRegion,
    _immutable_timeline,
    expand_continuity_regions,
)
from transpodcast.speaker_ownership_v15 import (
    OwnershipVoiceprintEvidence,
    SpeakerOwnershipRegion,
    build_speaker_ownership_regions,
)


def prepare_continuity_regions(
    segments: Sequence[CanonicalSegment],
    turns: Sequence[tuple[str, float, float]],
    mapping: dict[str, str],
    full_asr_text: str,
    pending: dict[str, Any],
    evidence: Sequence[OwnershipVoiceprintEvidence] = (),
    ranges: Sequence[tuple[int, int]] | None = None,
) -> tuple[ContinuityRegion, ...]:
    timeline = _immutable_timeline(segments, turns, mapping, full_asr_text)
    if not timeline:
        return ()
    risks = build_speaker_ownership_regions(
        segments,
        turns,
        mapping,
        full_asr_text,
        tuple(OwnershipDispute.from_payload(d) for d in pending.get("disputes", [])),
        {
            key: VoiceprintVerdict.from_payload(v) if v else None
            for key, v in pending.get("voiceprints", {}).items()
        },
    )
    retained = tuple(v for r in risks for v in r.voiceprint_evidence)
    if ranges is not None:
        labels = tuple(sorted({w.current_owner for w in timeline}))
        risks = tuple(
            SpeakerOwnershipRegion(
                f"edge-{start}-{end}",
                start,
                end,
                tuple(timeline[start:end]),
                labels,
                ("coverage-edge",),
            )
            for start, end in ranges
        )
    return expand_continuity_regions(
        segments,
        risks,
        turns,
        mapping,
        full_asr_text,
        voiceprint_evidence=(*retained, *evidence),
        maximum_seconds=max(w.end_seconds for w in timeline) + 1,
        maximum_words=len(timeline),
    )


def decode_continuity_evidence(
    payload: dict[str, Any],
) -> tuple[OwnershipVoiceprintEvidence, ...]:
    return tuple(
        OwnershipVoiceprintEvidence(
            str(v["dispute_id"]),
            int(v["first_word_index"]),
            int(v["last_word_index"]) + 1,
            str(v["owner"]),
            tuple(sorted((str(k), float(s)) for k, s in v["scores"].items())),
        )
        for v in payload.get("voiceprints", [])
    )
