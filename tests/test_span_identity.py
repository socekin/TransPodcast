from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from transpodcast.decision_records import DecisionRecord
from transpodcast.pipeline_versions import SPEECH_PIPELINE_V17, SPEECH_PIPELINE_V21
from transpodcast.span_identity import (
    DENSE_VOICEPRINT_POLICY,
    SPAN_IDENTITY_POLICY,
    SPARSE_LABEL_POLICY,
    DenseVoiceprintPolicy,
    MeasuredWindow,
    PlannedWindow,
    SparseLabel,
    apply_speaker_identity,
    check_span_identity,
    decide_sparse_label,
    find_sparse_labels,
    owner_runs,
    plan_dense_voiceprint_windows,
    plan_speaker_identity,
    speech_intervals,
)


def _window(start: float, end: float, **scores: float) -> MeasuredWindow:
    return MeasuredWindow(f"w-{start}", start, end, tuple(sorted(scores.items())))


def _timeline(
    *runs: tuple[str, float, float],
    word_seconds: float = 0.5,
) -> tuple[list[tuple[float, float]], list[str]]:
    spans: list[tuple[float, float]] = []
    owners: list[str] = []
    for owner, start, end in runs:
        cursor = start
        while cursor < end - 1e-9:
            spans.append((cursor, min(end, cursor + word_seconds)))
            owners.append(owner)
            cursor += word_seconds
    return spans, owners


# Policies


def test_policies_are_versioned_with_the_recorded_calibration() -> None:
    assert DENSE_VOICEPRINT_POLICY.version == "dense-voiceprints-v1"
    assert (
        DENSE_VOICEPRINT_POLICY.window_seconds,
        DENSE_VOICEPRINT_POLICY.hop_seconds,
    ) == (
        3.0,
        1.5,
    )
    assert SPAN_IDENTITY_POLICY.version == "span-identity-v1"
    assert SPAN_IDENTITY_POLICY.minimum_run_seconds == 3.0
    assert SPAN_IDENTITY_POLICY.minimum_clean_windows == 2
    assert SPAN_IDENTITY_POLICY.minimum_median_margin == 0.10
    assert SPAN_IDENTITY_POLICY.minimum_agreement == 0.75
    assert SPARSE_LABEL_POLICY.version == "sparse-label-merge-v1"
    assert SPARSE_LABEL_POLICY.maximum_turns == 2
    assert SPARSE_LABEL_POLICY.maximum_owned_seconds == 3.0
    assert SPARSE_LABEL_POLICY.minimum_margin == 0.05


# Dense window planner


def test_speech_intervals_join_words_across_short_gaps() -> None:
    spans = [(0.0, 0.5), (1.4, 2.0), (3.1, 3.5), (3.6, 4.0)]

    assert speech_intervals(spans) == ((0.0, 2.0), (3.1, 4.0))


def test_planner_tiles_each_interval_at_the_hop() -> None:
    windows = plan_dense_voiceprint_windows([(0.0, 2.0), (2.5, 4.0), (10.0, 10.8)])

    # One interval 0-4 s; the 10 s interval is shorter than one second.
    assert [(w.start_seconds, w.end_seconds) for w in windows] == [
        (0.0, 3.0),
        (1.5, 4.0),
        (3.0, 4.0),
    ]
    assert [w.window_id for w in windows] == [
        "dense-000000000-000003000",
        "dense-000001500-000004000",
        "dense-000003000-000004000",
    ]


def test_planner_is_deterministic_and_drops_short_tails() -> None:
    spans = [(5.0, 9.9)]

    first = plan_dense_voiceprint_windows(spans)
    second = plan_dense_voiceprint_windows(list(spans))

    assert first == second
    assert [(w.start_seconds, w.end_seconds) for w in first] == [
        (5.0, 8.0),
        (6.5, 9.5),
        (8.0, 9.9),
    ]
    assert all(w.end_seconds - w.start_seconds >= 1.0 for w in first)


def test_planner_rejects_a_hop_longer_than_a_window() -> None:
    with pytest.raises(ValueError):
        plan_dense_voiceprint_windows(
            [(0.0, 5.0)], DenseVoiceprintPolicy(window_seconds=1.0, hop_seconds=2.0)
        )


# Owner runs


def test_owner_runs_break_at_owner_changes_and_long_gaps() -> None:
    spans = [(0.0, 1.0), (1.5, 2.0), (3.5, 4.0), (4.0, 5.0)]
    owners = ["a", "a", "a", "b"]

    runs = owner_runs(spans, owners)

    assert [(r.owner, r.first_word_index, r.last_word_index) for r in runs] == [
        ("a", 0, 1),
        ("a", 2, 2),
        ("b", 3, 3),
    ]


# Span Identity Check


def test_a_contradicted_run_flips_as_a_whole() -> None:
    spans, owners = _timeline(("b", 0.0, 5.0), ("a", 5.0, 15.0), ("b", 15.0, 20.0))
    windows = [
        _window(5.0, 8.0, a=0.40, b=0.70),
        _window(6.5, 9.5, a=0.42, b=0.66),
        _window(8.0, 11.0, a=0.45, b=0.60),
        _window(9.5, 12.5, a=0.50, b=0.62),
    ]

    result = check_span_identity(spans, owners, windows, {"a", "b"})

    assert set(result.owners) == {"b"}
    assert len(result.relabels) == 1
    relabel = result.relabels[0]
    assert (relabel.run.owner, relabel.target) == ("a", "b")
    assert relabel.median_margin == pytest.approx(0.195)
    record = relabel.to_record(SPAN_IDENTITY_POLICY)
    assert DecisionRecord.from_payload(record.to_payload()) == record
    assert record.kind == "span-identity"
    assert record.policy_version == "span-identity-v1"
    assert record.subject["start_seconds"] == 5.0
    assert record.subject["end_seconds"] == 15.0
    assert [w["margin"] for w in record.evidence["windows"]] == [
        pytest.approx(0.30),
        pytest.approx(0.24),
        pytest.approx(0.15),
        pytest.approx(0.12),
    ]
    assert result.summary_payload()["relabelled_word_count"] == 20


def test_a_small_median_margin_keeps_the_run() -> None:
    spans, owners = _timeline(("a", 0.0, 10.0))
    windows = [
        _window(0.0, 3.0, a=0.50, b=0.58),
        _window(1.5, 4.5, a=0.50, b=0.59),
        _window(3.0, 6.0, a=0.50, b=0.57),
    ]

    result = check_span_identity(spans, owners, windows, {"a", "b"})

    assert result.relabels == ()
    assert result.owners == tuple(owners)
    assert result.checked_run_count == 1


def test_low_agreement_keeps_the_run() -> None:
    spans, owners = _timeline(("a", 0.0, 10.0))
    windows = [
        _window(0.0, 3.0, a=0.30, b=0.80),
        _window(1.5, 4.5, a=0.30, b=0.80),
        _window(3.0, 6.0, a=0.70, b=0.65),
        _window(4.5, 7.5, a=0.70, b=0.65),
    ]

    result = check_span_identity(spans, owners, windows, {"a", "b"})

    # Median margin 0.225, but only half of the windows favour b.
    assert result.relabels == ()


def test_one_clean_window_is_not_enough() -> None:
    spans, owners = _timeline(("a", 0.0, 10.0))

    result = check_span_identity(
        spans, owners, [_window(2.0, 5.0, a=0.1, b=0.9)], {"a", "b"}
    )

    assert result.relabels == ()


def test_windows_hearing_another_owner_are_not_clean() -> None:
    # A 1 s reply by b sits inside a's 10 s run; every window touching it,
    # or reaching outside the run, is excluded.
    spans, owners = _timeline(("a", 0.0, 4.5), ("b", 4.5, 5.5), ("a", 5.5, 10.0))
    windows = [
        _window(3.0, 6.0, a=0.2, b=0.9),
        _window(4.0, 7.0, a=0.2, b=0.9),
        _window(5.6, 8.6, a=0.2, b=0.9),
        _window(8.0, 11.0, a=0.2, b=0.9),
    ]

    result = check_span_identity(spans, owners, windows, {"a", "b"})

    # Only 5.6-8.6 s is clean in the 5.5-10 s run; 0-4.5 s has none.
    assert result.relabels == ()
    assert result.checked_run_count == 2

    spans, owners = _timeline(("a", 0.0, 10.0))
    assert len(check_span_identity(spans, owners, windows, {"a", "b"}).relabels) == 1


def test_short_runs_and_unestablished_owners_are_not_checked() -> None:
    spans, owners = _timeline(("a", 0.0, 2.5), ("ghost", 3.0, 9.0))
    windows = [
        _window(0.0, 2.5, a=0.1, b=0.9, ghost=0.1),
        _window(3.0, 6.0, a=0.1, b=0.9, ghost=0.1),
        _window(4.5, 7.5, a=0.1, b=0.9, ghost=0.1),
    ]

    result = check_span_identity(spans, owners, windows, {"a", "b"})

    assert result.checked_run_count == 0
    assert result.relabels == ()


def test_the_margin_threshold_is_inclusive() -> None:
    spans, owners = _timeline(("a", 0.0, 6.0))
    windows = [
        _window(0.0, 3.0, a=0.5, b=0.625),
        _window(1.5, 4.5, a=0.5, b=0.625),
    ]

    assert len(check_span_identity(spans, owners, windows, {"a", "b"}).relabels) == 1
    stricter = SPAN_IDENTITY_POLICY.__class__(minimum_median_margin=0.13)
    assert (
        check_span_identity(spans, owners, windows, {"a", "b"}, stricter).relabels == ()
    )


# Sparse-label merge


def test_sparse_labels_have_few_turns_and_little_owned_speech() -> None:
    turns = [
        ("a", 0.0, 10.0),
        ("a", 12.0, 20.0),
        ("a", 22.0, 30.0),
        ("ghost", 10.0, 11.2),
        ("long", 31.0, 40.0),
    ]
    spans, owners = _timeline(
        ("a", 0.0, 10.0), ("ghost", 10.0, 11.0), ("long", 31.0, 40.0)
    )

    sparse = find_sparse_labels(turns, spans, owners)

    assert [(s.label, s.owned_seconds) for s in sparse] == [("ghost", 1.0)]


def test_a_clearly_favoured_sparse_label_merges() -> None:
    ghost = SparseLabel("speaker_2", ((1637.2, 1638.32),), 1.0)

    decision = decide_sparse_label(
        ghost,
        {"speaker_0": 0.430, "speaker_1": 0.353},
        {"speaker_0", "speaker_1"},
        "fragments",
    )

    assert decision.target == "speaker_0"
    assert decision.margin == pytest.approx(0.077)
    record = decision.to_record(SPARSE_LABEL_POLICY)
    assert DecisionRecord.from_payload(record.to_payload()) == record
    assert (record.kind, record.verdict) == ("consolidation", "merged")
    assert record.policy_version == "sparse-label-merge-v1"
    assert record.subject == {"source_label": "speaker_2"}
    assert record.evidence["merged_into"] == "speaker_0"


@pytest.mark.parametrize(
    ("scores", "reason"),
    [
        ({"a": 0.50, "b": 0.47}, "clearly"),
        ({"a": 0.30, "b": 0.10}, "too low"),
        (None, "could be scored"),
        ({"a": 0.9}, "Only one"),
    ],
)
def test_an_unclear_sparse_label_is_kept(
    scores: dict[str, float] | None, reason: str
) -> None:
    ghost = SparseLabel("ghost", ((1.0, 2.0),), 1.0)

    decision = decide_sparse_label(
        ghost, scores, {"a", "b"} if scores != {"a": 0.9} else {"a"}, "fragments"
    )

    assert decision.target is None
    assert reason in decision.reason
    assert decision.to_record(SPARSE_LABEL_POLICY).verdict == "kept"


def test_a_label_without_an_anchor_is_measured_from_its_fragments() -> None:
    spans, owners = _timeline(
        ("a", 0.0, 10.0), ("ghost", 10.0, 11.0), ("b", 11.5, 20.0)
    )
    turns = [("a", 0.0, 10.0), ("ghost", 10.0, 11.2), ("b", 11.5, 20.0)]

    plan = plan_speaker_identity(spans, owners, turns, {"a", "b"})

    assert plan.established == {"a", "b"}
    assert [
        (w.window_id, w.start_seconds, w.end_seconds) for w in plan.fragments["ghost"]
    ] == [("fragment-ghost-01", 10.0, 11.2)]
    outcome = apply_speaker_identity(
        plan,
        spans,
        owners,
        [],
        {
            "ghost": [
                MeasuredWindow(
                    "fragment-ghost-01", 10.0, 11.2, (("a", 0.2), ("b", 0.6))
                )
            ]
        },
        {},
    )
    assert outcome.merges == {"ghost": "b"}
    assert "ghost" not in outcome.owners
    assert outcome.sparse_summary()["merged"] == {"ghost": "b"}
    assert [r["kind"] for r in outcome.decision_records()] == ["consolidation"]


# Source Analysis integration and version gating


def _fake_models(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]) -> None:
    from transpodcast import modal_pipeline

    words = (
        [
            {"text": f"w{i}.", "start_seconds": i * 0.5, "end_seconds": i * 0.5 + 0.5}
            for i in range(16)
        ]
        + [
            {
                "text": f"x{i}",
                "start_seconds": 8.5 + i * 0.5,
                "end_seconds": 9.0 + i * 0.5,
            }
            for i in range(23)
        ]
        + [
            {"text": "You", "start_seconds": 20.5, "end_seconds": 21.0},
            {"text": "should.", "start_seconds": 21.0, "end_seconds": 21.5},
        ]
    )
    turns = [
        {"speaker": "a", "start_seconds": 0.0, "end_seconds": 8.2},
        {"speaker": "b", "start_seconds": 8.4, "end_seconds": 20.0},
        {"speaker": "c", "start_seconds": 20.5, "end_seconds": 21.5},
    ]

    def vector(key: str) -> list[float]:
        if key.startswith("consolidation-window::a"):
            return [1.0, 0.0]
        if key.startswith("dense-") and int(key.split("-")[1]) < 8000:
            # The diarizer's speaker a is really speaker b's voice.
            return [0.2, 0.98]
        if key.startswith("fragment-c"):
            return [0.05, 0.9]
        return [0.0, 1.0]

    def fake_remote(
        app_name: str, class_name: str, method_name: str, payload: Any
    ) -> dict[str, Any]:
        del class_name, method_name
        if app_name == modal_pipeline.SORTFORMER_APP:
            return {"segments": turns, "model_id": "sortformer"}
        if app_name == modal_pipeline.QWEN_ASR_APP:
            return {
                "text": " ".join(w["text"] for w in words),
                "words": words,
                "asr_model_id": "qwen3-asr",
                "aligner_model_id": "qwen3-forced-aligner",
            }
        calls.append(dict(payload))
        return {
            "model_id": "nvidia/speakerverification_en_titanet_large",
            # pragma: allowlist nextline secret
            "model_revision": "0dc382f40121a5fbd34db10a2bb04d826c2be6a8",
            "inference_seconds": 0.5,
            "embeddings": {key: vector(key) for key in payload},
        }

    def fake_references(
        source_wav: Path, diarization: Any, reference_root: Path
    ) -> dict[str, Path]:
        del source_wav, diarization
        references = {}
        for label in ("a", "b", "c"):
            path = reference_root / f"{label}.wav"
            path.write_bytes(label.encode())
            references[label] = path
        return references

    monkeypatch.setattr(
        modal_pipeline,
        "_transcode",
        lambda source, destination, **kwargs: destination.write_bytes(b"wav"),
    )
    monkeypatch.setattr(modal_pipeline, "_remote_method", fake_remote)
    monkeypatch.setattr(modal_pipeline, "_extract_speaker_references", fake_references)
    monkeypatch.setattr(
        modal_pipeline,
        "_extract_reference_audio",
        lambda source_wav, destination, **kwargs: destination.write_bytes(b"clip"),
    )
    monkeypatch.setattr(
        modal_pipeline,
        "_dense_clip_audio",
        lambda source_wav, windows: {w.window_id: b"clip" for w in windows},
    )


def _analyze(tmp_path: Path, version: str) -> dict[str, Any]:
    from transpodcast.modal_pipeline import analyze

    source = tmp_path / "source.webm"
    source.write_bytes(b"source-audio")
    return analyze(
        {
            "analysis": {"pipeline_version": version},
            "source": {
                "audio_path": str(source),
                "checksum": hashlib.sha256(source.read_bytes()).hexdigest(),
            },
        },
        tmp_path / "output",
    )


def test_v21_corrects_owners_before_risk_regions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    _fake_models(monkeypatch, calls)

    result = _analyze(tmp_path, SPEECH_PIPELINE_V21)

    owners = {s["speaker_label"] for s in result["segments"]}
    assert owners == {"b"}
    consolidation = result["speaker_consolidation"]
    assert consolidation["speaker_mapping"]["c"] == "b"
    assert consolidation["sparse_label_merge"]["merged"] == {"c": "b"}
    assert "c" not in {s["local_label"] for s in result["speakers"]}
    assert "c" not in consolidation["reference_selection"]
    records = {
        (r["kind"], r["policy_version"], r["verdict"])
        for r in consolidation["decision_records"]
    }
    assert ("consolidation", "sparse-label-merge-v1", "merged") in records
    assert ("span-identity", "span-identity-v1", "relabelled") in records
    boundary = result["boundary_adjudication"]
    assert boundary["span_identity"]["relabelled_run_count"] == 1
    dense = boundary["dense_voiceprints"]
    assert dense["errors"] == []
    assert dense["policy_version"] == "dense-voiceprints-v1"
    assert dense["model_id"] == "nvidia/speakerverification_en_titanet_large"
    assert dense["fragment_count"] == 1
    assert dense["measured_window_count"] == dense["window_count"] + 1
    assert dense["fragments"][0]["window_id"] == "fragment-c-01"
    # The fragment rides in the same embedding call as the dense windows.
    dense_calls = [c for c in calls if any(k.startswith("dense-") for k in c)]
    assert len(dense_calls) == 1
    assert "fragment-c-01" in dense_calls[0]
    # Risk regions see the corrected owners: no handoff remains to dispute.
    assert boundary["dispute_count"] == 0
    assert result["call_seconds"]["speaker_embeddings"] >= 1.0


def test_v17_source_analysis_never_measures_dense_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    _fake_models(monkeypatch, calls)

    result = _analyze(tmp_path, SPEECH_PIPELINE_V17)

    assert [s["speaker_label"] for s in result["segments"]][0] == "a"
    assert "dense_voiceprints" not in result["boundary_adjudication"]
    assert "span_identity" not in result["boundary_adjudication"]
    assert "sparse_label_merge" not in result["speaker_consolidation"]
    assert not any(
        key.startswith(("dense-", "fragment-")) for call in calls for key in call
    )
    assert result["boundary_adjudication"]["dispute_count"] > 0


def test_a_failed_dense_measurement_changes_no_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from transpodcast import modal_pipeline

    calls: list[dict[str, Any]] = []
    _fake_models(monkeypatch, calls)

    def broken(source_wav: Path, windows: Any) -> dict[str, bytes]:
        raise OSError("disk full")

    monkeypatch.setattr(modal_pipeline, "_dense_clip_audio", broken)

    result = _analyze(tmp_path, SPEECH_PIPELINE_V21)

    assert [s["speaker_label"] for s in result["segments"]][0] == "a"
    assert result["boundary_adjudication"]["dense_voiceprints"]["errors"] == [
        "OSError: disk full"
    ]
    assert result["boundary_adjudication"]["span_identity"]["skipped"]
    assert "c" not in result["speaker_consolidation"]["speaker_mapping"]


def test_dense_measurements_resume_from_matching_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from transpodcast import modal_pipeline

    calls: list[dict[str, Any]] = []
    _fake_models(monkeypatch, calls)
    windows = (PlannedWindow("dense-000000000-000003000", 0.0, 3.0),)
    anchors = {"a": (1.0, 0.0), "b": (0.0, 1.0)}

    first = modal_pipeline._measure_dense_voiceprints(
        tmp_path / "source.wav", windows, (), anchors, "sha"
    )
    resumed = modal_pipeline._measure_dense_voiceprints(
        tmp_path / "source.wav", windows, (), anchors, "sha", prior=first
    )
    rebound = modal_pipeline._measure_dense_voiceprints(
        tmp_path / "source.wav", windows, (), {"a": (1.0, 0.0)}, "sha", prior=first
    )

    assert len(calls) == 2
    assert resumed["measurements"] == first["measurements"]
    assert resumed["resumed_window_count"] == 1
    assert rebound["resumed_window_count"] == 0


def test_relabelled_segments_split_only_at_owner_changes() -> None:
    from transpodcast.modal_pipeline import _relabel_segments

    segments = [
        {
            "segment_id": "seg-0001",
            "speaker_label": "a",
            "start_seconds": 0.0,
            "end_seconds": 1.5,
            "text": "one two three",
            "words": [
                {"text": "one", "start_seconds": 0.0, "end_seconds": 0.5},
                {"text": "two", "start_seconds": 0.5, "end_seconds": 1.0},
                {"text": "three", "start_seconds": 1.0, "end_seconds": 1.5},
            ],
            "overlap": True,
        },
        {
            "segment_id": "seg-0002",
            "speaker_label": "b",
            "start_seconds": 2.0,
            "end_seconds": 2.5,
            "text": "four",
            "words": [{"text": "four", "start_seconds": 2.0, "end_seconds": 2.5}],
            "overlap": False,
        },
    ]
    turns = [{"speaker": "a", "start_seconds": 0.0, "end_seconds": 2.5}]

    assert _relabel_segments(segments, ["a", "a", "a", "b"], turns) is segments
    split = _relabel_segments(segments, ["a", "c", "c", "c"], turns)

    assert [(s["segment_id"], s["speaker_label"], s["text"]) for s in split] == [
        ("seg-0001", "a", "one"),
        ("seg-0002", "c", "two three"),
        ("seg-0003", "c", "four"),
    ]
    assert [s["overlap"] for s in split] == [False, False, False]
    assert split[1]["start_seconds"] == 0.5
