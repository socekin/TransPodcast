"""Acoustic Speaker identity checks for V21 Source Analysis.

Deterministic ownership (``deterministic_ownership.py``) fixes timing at
Speaker changes, but it cannot see a whole span that the diarizer gave the
wrong label, or a ghost label that owns a single phrase. Both need acoustic
identity evidence. This module holds the pure policies; Source Analysis
measures the voiceprints with the deployed TitaNet ``SpeakerEmbedder``.

- **Dense voiceprint windows.** Every speech interval (aligned words joined
  across gaps of at most 1 s) is tiled with 3 s windows at a 1.5 s hop. Each
  window is scored against the episode anchors, the consolidated Speaker
  centroids.
- **Sparse-label merge** (``sparse-label-merge-v1``). A label with at most
  two diarization turns and at most 3 s of owned speech is merged into the
  established Speaker its voiceprint favours by at least 0.05. A label
  without an anchor embedding is measured from its own turns, its
  fragments. Otherwise the label is kept.
- **Span Identity Check** (``span-identity-v1``). An owner run (same owner,
  gaps of at most 1 s) of at least 3 s is relabelled as a whole to another
  established Speaker when at least two clean windows inside it favour that
  Speaker with a median margin of at least 0.10 and at least 75% agreement.
  A clean window lies within the run (±0.2 s) and overlaps no word of
  another owner.

Both run after Episode-local Speaker Consolidation and before the Speaker
Ownership Risk Regions are built, so the Agent layer sees corrected owners.
Each change is returned as a Decision Record.

In calibration, every margin from 0.05 to 0.20 flipped exactly one
misattributed run, and a single-phrase ghost label scored the guest 0.430
against the host 0.353.
"""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from transpodcast.decision_records import DecisionRecord

DENSE_VOICEPRINT_POLICY_VERSION = "dense-voiceprints-v1"
SPAN_IDENTITY_POLICY_VERSION = "span-identity-v1"
SPARSE_LABEL_POLICY_VERSION = "sparse-label-merge-v1"
SPAN_IDENTITY_RECORD_KIND = "span-identity"


@dataclass(frozen=True)
class DenseVoiceprintPolicy:
    version: str = DENSE_VOICEPRINT_POLICY_VERSION
    window_seconds: float = 3.0
    hop_seconds: float = 1.5
    # Words closer than this belong to one speech interval.
    maximum_gap_seconds: float = 1.0
    # Shorter windows embed as noise; intervals shorter than this get none.
    minimum_window_seconds: float = 1.0

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpanIdentityPolicy:
    version: str = SPAN_IDENTITY_POLICY_VERSION
    maximum_gap_seconds: float = 1.0
    minimum_run_seconds: float = 3.0
    edge_tolerance_seconds: float = 0.2
    # Touching a foreign word's edge by less than this is not overlap.
    foreign_overlap_tolerance_seconds: float = 0.05
    minimum_clean_windows: int = 2
    minimum_median_margin: float = 0.10
    minimum_agreement: float = 0.75

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SparseLabelPolicy:
    version: str = SPARSE_LABEL_POLICY_VERSION
    maximum_turns: int = 2
    maximum_owned_seconds: float = 3.0
    minimum_margin: float = 0.05
    # Same floor as the fragment voiceprint judge: a best score below it is
    # noise, not identity evidence.
    minimum_score: float = 0.35
    minimum_fragment_seconds: float = 0.3
    maximum_fragment_seconds: float = 12.0

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


DENSE_VOICEPRINT_POLICY = DenseVoiceprintPolicy()
SPAN_IDENTITY_POLICY = SpanIdentityPolicy()
SPARSE_LABEL_POLICY = SparseLabelPolicy()


@dataclass(frozen=True)
class PlannedWindow:
    window_id: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class MeasuredWindow:
    window_id: str
    start_seconds: float
    end_seconds: float
    scores: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class OwnerRun:
    owner: str
    first_word_index: int
    last_word_index: int
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


# Dense voiceprint windows


def speech_intervals(
    spans: Sequence[tuple[float, float]],
    maximum_gap_seconds: float = DENSE_VOICEPRINT_POLICY.maximum_gap_seconds,
) -> tuple[tuple[float, float], ...]:
    """Join word spans, in order, across gaps of at most the given length."""
    intervals: list[list[float]] = []
    for start, end in spans:
        if intervals and start - intervals[-1][1] <= maximum_gap_seconds:
            intervals[-1][1] = max(intervals[-1][1], end)
        else:
            intervals.append([start, end])
    return tuple((start, end) for start, end in intervals)


def window_id(start_seconds: float, end_seconds: float) -> str:
    """A stable, file-name safe identity for one source time span."""
    return f"dense-{round(start_seconds * 1000):09d}-{round(end_seconds * 1000):09d}"


def plan_dense_voiceprint_windows(
    spans: Sequence[tuple[float, float]],
    policy: DenseVoiceprintPolicy = DENSE_VOICEPRINT_POLICY,
) -> tuple[PlannedWindow, ...]:
    """Tile every speech interval with fixed windows at a fixed hop.

    A window starts every ``hop_seconds`` from the interval start and ends
    ``window_seconds`` later or at the interval end; a window shorter than
    ``minimum_window_seconds`` is not planned.
    """
    if not 0 < policy.hop_seconds <= policy.window_seconds:
        raise ValueError("The hop must be positive and at most one window")
    if not 0 < policy.minimum_window_seconds <= policy.window_seconds:
        raise ValueError("The minimum window must be positive and fit a window")
    windows: dict[str, PlannedWindow] = {}
    for start, end in speech_intervals(spans, policy.maximum_gap_seconds):
        step = 0
        while (cursor := start + step * policy.hop_seconds) < end:
            stop = min(end, cursor + policy.window_seconds)
            if stop - cursor >= policy.minimum_window_seconds:
                name = window_id(cursor, stop)
                windows[name] = PlannedWindow(name, cursor, stop)
            step += 1
    return tuple(windows.values())


# Owner runs


def owner_runs(
    spans: Sequence[tuple[float, float]],
    owners: Sequence[str],
    maximum_gap_seconds: float = SPAN_IDENTITY_POLICY.maximum_gap_seconds,
) -> tuple[OwnerRun, ...]:
    """Maximal same-owner word runs whose internal gaps stay short."""
    if len(spans) != len(owners):
        raise ValueError("Every word needs exactly one owner")
    runs: list[OwnerRun] = []
    for index, ((start, end), owner) in enumerate(zip(spans, owners, strict=True)):
        last = runs[-1] if runs else None
        if (
            last is not None
            and last.owner == owner
            and start - last.end_seconds <= maximum_gap_seconds
        ):
            runs[-1] = OwnerRun(
                owner,
                last.first_word_index,
                index,
                last.start_seconds,
                max(last.end_seconds, end),
            )
        else:
            runs.append(OwnerRun(owner, index, index, start, end))
    return tuple(runs)


# Span Identity Check


@dataclass(frozen=True)
class WindowMargin:
    window: MeasuredWindow
    favoured: str
    margin: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "window_id": self.window.window_id,
            "start_seconds": self.window.start_seconds,
            "end_seconds": self.window.end_seconds,
            "scores": dict(self.window.scores),
            "favoured": self.favoured,
            "margin": round(self.margin, 6),
        }


@dataclass(frozen=True)
class SpanRelabel:
    run: OwnerRun
    target: str
    margins: tuple[WindowMargin, ...]
    median_margin: float
    agreement: float

    def to_record(self, policy: SpanIdentityPolicy) -> DecisionRecord:
        return DecisionRecord(
            kind=SPAN_IDENTITY_RECORD_KIND,
            policy_version=policy.version,
            subject={
                "first_word_index": self.run.first_word_index,
                "last_word_index": self.run.last_word_index,
                "start_seconds": self.run.start_seconds,
                "end_seconds": self.run.end_seconds,
                "from_speaker": self.run.owner,
                "to_speaker": self.target,
            },
            evidence={
                "word_index_basis": "aligned_words",
                "word_count": self.run.last_word_index - self.run.first_word_index + 1,
                "window_count": len(self.margins),
                "median_margin": round(self.median_margin, 6),
                "agreement": round(self.agreement, 6),
                "windows": [margin.to_payload() for margin in self.margins],
                "policy": policy.to_payload(),
            },
            verdict="relabelled",
            rationale=(
                "Clean dense voiceprint windows inside the whole owner run "
                "consistently favour another established Speaker."
            ),
        )


@dataclass(frozen=True)
class SpanIdentityResult:
    owners: tuple[str, ...]
    relabels: tuple[SpanRelabel, ...]
    checked_run_count: int
    policy: SpanIdentityPolicy

    def summary_payload(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy.version,
            "policy": self.policy.to_payload(),
            "checked_run_count": self.checked_run_count,
            "relabelled_run_count": len(self.relabels),
            "relabelled_word_count": sum(
                r.run.last_word_index - r.run.first_word_index + 1
                for r in self.relabels
            ),
        }


def clean_run_windows(
    run: OwnerRun,
    windows: Sequence[MeasuredWindow],
    spans: Sequence[tuple[float, float]],
    owners: Sequence[str],
    policy: SpanIdentityPolicy = SPAN_IDENTITY_POLICY,
) -> tuple[MeasuredWindow, ...]:
    """Windows inside the run (±edge tolerance) that hear no foreign word.

    ``windows`` must be sorted by start time.
    """
    low = run.start_seconds - policy.edge_tolerance_seconds
    high = run.end_seconds + policy.edge_tolerance_seconds
    starts = [window.start_seconds for window in windows]
    selected: list[MeasuredWindow] = []
    for window in windows[bisect.bisect_left(starts, low) :]:
        if window.start_seconds > high:
            break
        if window.end_seconds <= high:
            selected.append(window)
    foreign = [
        (start, end)
        for (start, end), owner in zip(spans, owners, strict=True)
        if owner != run.owner and end > low and start < high
    ]
    return tuple(
        window
        for window in selected
        if not any(
            min(window.end_seconds, end) - max(window.start_seconds, start)
            > policy.foreign_overlap_tolerance_seconds
            for start, end in foreign
        )
    )


def evaluate_run(
    run: OwnerRun,
    windows: Sequence[MeasuredWindow],
    established: Collection[str],
    policy: SpanIdentityPolicy = SPAN_IDENTITY_POLICY,
) -> SpanRelabel | None:
    """Relabel decision for one run from its clean windows, or ``None``."""
    scored = [
        (window, {s: v for s, v in window.scores if s in established})
        for window in windows
    ]
    usable = [
        (window, scores)
        for window, scores in scored
        if run.owner in scores and len(scores) >= 2
    ]
    if len(usable) < policy.minimum_clean_windows:
        return None
    best: SpanRelabel | None = None
    for rival in sorted(set(established) - {run.owner}):
        margins = [
            WindowMargin(
                window,
                max(scores, key=lambda s: (scores[s], s)),
                scores[rival] - scores[run.owner],
            )
            for window, scores in usable
            if rival in scores
        ]
        if len(margins) < policy.minimum_clean_windows:
            continue
        median = statistics.median(m.margin for m in margins)
        agreement = sum(m.favoured == rival for m in margins) / len(margins)
        candidate = SpanRelabel(run, rival, tuple(margins), median, agreement)
        if best is None or median > best.median_margin:
            best = candidate
    if (
        best is None
        or best.median_margin < policy.minimum_median_margin
        or best.agreement < policy.minimum_agreement
    ):
        return None
    return best


def check_span_identity(
    spans: Sequence[tuple[float, float]],
    owners: Sequence[str],
    windows: Sequence[MeasuredWindow],
    established: Collection[str],
    policy: SpanIdentityPolicy = SPAN_IDENTITY_POLICY,
) -> SpanIdentityResult:
    """Relabel whole owner runs that the dense voiceprints contradict.

    ``spans`` and ``owners`` describe the aligned word timeline in order.
    Only runs owned by an established Speaker and lasting at least
    ``minimum_run_seconds`` are checked; decisions use the input owners, so
    one relabel never changes another run's evidence.
    """
    ordered = sorted(windows, key=lambda window: window.start_seconds)
    result = list(owners)
    relabels: list[SpanRelabel] = []
    checked = 0
    for run in owner_runs(spans, owners, policy.maximum_gap_seconds):
        if (
            run.duration_seconds < policy.minimum_run_seconds
            or run.owner not in established
        ):
            continue
        checked += 1
        relabel = evaluate_run(
            run,
            clean_run_windows(run, ordered, spans, owners, policy),
            established,
            policy,
        )
        if relabel is None:
            continue
        relabels.append(relabel)
        for index in range(run.first_word_index, run.last_word_index + 1):
            result[index] = relabel.target
    return SpanIdentityResult(tuple(result), tuple(relabels), checked, policy)


# Sparse-label merge


@dataclass(frozen=True)
class SparseLabel:
    label: str
    turns: tuple[tuple[float, float], ...]
    owned_seconds: float


@dataclass(frozen=True)
class SparseLabelDecision:
    sparse: SparseLabel
    target: str | None
    scores: tuple[tuple[str, float], ...]
    margin: float | None
    voiceprint_source: str
    reason: str

    @property
    def merged(self) -> bool:
        return self.target is not None

    def to_record(self, policy: SparseLabelPolicy) -> DecisionRecord:
        evidence: dict[str, Any] = {
            "turn_count": len(self.sparse.turns),
            "turns": [
                {"start_seconds": start, "end_seconds": end}
                for start, end in self.sparse.turns
            ],
            "owned_seconds": round(self.sparse.owned_seconds, 6),
            "voiceprint_source": self.voiceprint_source,
            "scores": dict(self.scores),
            "margin": None if self.margin is None else round(self.margin, 6),
            "policy": policy.to_payload(),
        }
        if self.target is not None:
            evidence["merged_into"] = self.target
        return DecisionRecord(
            kind="consolidation",
            policy_version=policy.version,
            subject={"source_label": self.sparse.label},
            evidence=evidence,
            verdict="merged" if self.merged else "kept",
            rationale=self.reason,
        )


def find_sparse_labels(
    turns: Sequence[tuple[str, float, float]],
    spans: Sequence[tuple[float, float]],
    owners: Sequence[str],
    policy: SparseLabelPolicy = SPARSE_LABEL_POLICY,
) -> tuple[SparseLabel, ...]:
    """Labels with few diarization turns and little owned speech.

    ``turns`` carry consolidated labels; ``spans`` and ``owners`` describe
    the aligned word timeline.
    """
    grouped: dict[str, list[tuple[float, float]]] = {}
    for label, start, end in turns:
        grouped.setdefault(label, []).append((start, end))
    owned: dict[str, float] = {}
    for (start, end), owner in zip(spans, owners, strict=True):
        owned[owner] = owned.get(owner, 0.0) + max(0.0, end - start)
    return tuple(
        SparseLabel(label, tuple(sorted(spans_)), owned.get(label, 0.0))
        for label, spans_ in sorted(grouped.items())
        if len(spans_) <= policy.maximum_turns
        and owned.get(label, 0.0) <= policy.maximum_owned_seconds
    )


def fragment_window_id(label: str, number: int) -> str:
    safe = "".join(c if c.isalnum() or c in "_-" else "-" for c in label)
    return f"fragment-{safe}-{number:02d}"


def plan_sparse_fragments(
    sparse: Sequence[SparseLabel],
    policy: SparseLabelPolicy = SPARSE_LABEL_POLICY,
) -> dict[str, tuple[PlannedWindow, ...]]:
    """The measurable turns of each sparse label, keyed by label."""
    planned: dict[str, tuple[PlannedWindow, ...]] = {}
    for item in sparse:
        fragments = []
        for number, (start, end) in enumerate(item.turns, start=1):
            stop = min(end, start + policy.maximum_fragment_seconds)
            if stop - start >= policy.minimum_fragment_seconds - 1e-9:
                fragments.append(
                    PlannedWindow(fragment_window_id(item.label, number), start, stop)
                )
        planned[item.label] = tuple(fragments)
    return planned


def fragment_voiceprint_scores(
    fragments: Sequence[MeasuredWindow],
) -> tuple[tuple[str, float], ...]:
    """Duration-weighted mean score of a label's measured fragments."""
    totals: dict[str, float] = {}
    weights: dict[str, float] = {}
    for fragment in fragments:
        weight = max(1e-9, fragment.end_seconds - fragment.start_seconds)
        for speaker, score in fragment.scores:
            totals[speaker] = totals.get(speaker, 0.0) + weight * score
            weights[speaker] = weights.get(speaker, 0.0) + weight
    return tuple(
        sorted((speaker, totals[speaker] / weights[speaker]) for speaker in totals)
    )


def decide_sparse_label(
    sparse: SparseLabel,
    scores: Mapping[str, float] | None,
    established: Collection[str],
    voiceprint_source: str,
    policy: SparseLabelPolicy = SPARSE_LABEL_POLICY,
) -> SparseLabelDecision:
    """Merge into the clearly favoured established Speaker, else keep."""
    candidates = {
        speaker: float(score)
        for speaker, score in (scores or {}).items()
        if speaker in established and speaker != sparse.label
    }
    ordered = tuple(sorted(candidates.items()))

    def keep(reason: str, margin: float | None = None) -> SparseLabelDecision:
        return SparseLabelDecision(
            sparse, None, ordered, margin, voiceprint_source, reason
        )

    if not candidates:
        return keep("No voiceprint of this label could be scored.")
    ranked = sorted(candidates, key=lambda s: (candidates[s], s), reverse=True)
    best = candidates[ranked[0]]
    margin = best - candidates[ranked[1]] if len(ranked) > 1 else None
    if best < policy.minimum_score:
        return keep("The best voiceprint score is too low to be evidence.", margin)
    if margin is not None and margin < policy.minimum_margin:
        return keep("The voiceprint does not clearly favour one Speaker.", margin)
    if margin is None:
        return keep("Only one established Speaker could be compared.")
    return SparseLabelDecision(
        sparse,
        ranked[0],
        ordered,
        margin,
        voiceprint_source,
        "A sparse label whose voiceprint clearly favours an established "
        "Speaker is that Speaker.",
    )


# Whole calibration: plan, measure (Source Analysis), apply


@dataclass(frozen=True)
class IdentityPlan:
    """What to measure for one episode, from owners before calibration."""

    sparse: tuple[SparseLabel, ...]
    established: frozenset[str]
    windows: tuple[PlannedWindow, ...]
    fragments: Mapping[str, tuple[PlannedWindow, ...]]

    def planned(self) -> tuple[PlannedWindow, ...]:
        return (
            *self.windows,
            *(window for group in self.fragments.values() for window in group),
        )


@dataclass(frozen=True)
class IdentityOutcome:
    owners: tuple[str, ...]
    merges: Mapping[str, str]
    sparse_decisions: tuple[SparseLabelDecision, ...]
    span_check: SpanIdentityResult
    sparse_policy: SparseLabelPolicy = SPARSE_LABEL_POLICY

    def sparse_summary(self) -> dict[str, Any]:
        return {
            "policy_version": self.sparse_policy.version,
            "policy": self.sparse_policy.to_payload(),
            "sparse_label_count": len(self.sparse_decisions),
            "merged": dict(self.merges),
        }

    def decision_records(self) -> list[dict[str, Any]]:
        return [
            *(
                d.to_record(self.sparse_policy).to_payload()
                for d in self.sparse_decisions
            ),
            *(
                r.to_record(self.span_check.policy).to_payload()
                for r in self.span_check.relabels
            ),
        ]


def plan_speaker_identity(
    spans: Sequence[tuple[float, float]],
    owners: Sequence[str],
    turns: Sequence[tuple[str, float, float]],
    anchor_labels: Collection[str],
    *,
    dense_policy: DenseVoiceprintPolicy = DENSE_VOICEPRINT_POLICY,
    sparse_policy: SparseLabelPolicy = SPARSE_LABEL_POLICY,
) -> IdentityPlan:
    """Plan dense windows and the fragments of sparse labels without anchors.

    ``turns`` carry consolidated labels. An established Speaker has an anchor
    embedding and is not sparse.
    """
    sparse = find_sparse_labels(turns, spans, owners, sparse_policy)
    labels = {item.label for item in sparse}
    return IdentityPlan(
        sparse=sparse,
        established=frozenset(set(anchor_labels) - labels),
        windows=plan_dense_voiceprint_windows(spans, dense_policy),
        fragments=plan_sparse_fragments(
            [item for item in sparse if item.label not in anchor_labels],
            sparse_policy,
        ),
    )


def apply_speaker_identity(
    plan: IdentityPlan,
    spans: Sequence[tuple[float, float]],
    owners: Sequence[str],
    windows: Sequence[MeasuredWindow],
    fragments: Mapping[str, Sequence[MeasuredWindow]],
    anchor_scores: Mapping[str, Mapping[str, float]],
    *,
    span_policy: SpanIdentityPolicy = SPAN_IDENTITY_POLICY,
    sparse_policy: SparseLabelPolicy = SPARSE_LABEL_POLICY,
) -> IdentityOutcome:
    """Merge sparse labels, then relabel contradicted owner runs.

    ``fragments`` holds the measured fragments of each sparse label without
    an anchor; ``anchor_scores`` holds, for each sparse label with an
    anchor, its anchor's similarity to the other anchors.
    """
    decisions: list[SparseLabelDecision] = []
    for item in plan.sparse:
        if item.label in anchor_scores:
            decisions.append(
                decide_sparse_label(
                    item,
                    anchor_scores[item.label],
                    plan.established,
                    "anchor",
                    sparse_policy,
                )
            )
            continue
        found = fragments.get(item.label, ())
        decisions.append(
            decide_sparse_label(
                item,
                dict(fragment_voiceprint_scores(found)) if found else None,
                plan.established,
                "fragments",
                sparse_policy,
            )
        )
    merges: dict[str, str] = {}
    for decision in decisions:
        if decision.target is not None:
            merges[decision.sparse.label] = decision.target
    merged = [merges.get(owner, owner) for owner in owners]
    check = check_span_identity(spans, merged, windows, plan.established, span_policy)
    return IdentityOutcome(check.owners, merges, tuple(decisions), check, sparse_policy)
