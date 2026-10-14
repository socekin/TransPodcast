from dataclasses import replace

from tests.test_speaker_ownership_continuity import example
from transpodcast.ownership_acoustic_coverage import (
    acoustic_window_evidence,
    find_ownership_edge_gaps,
    plan_ownership_acoustic_windows,
    project_acoustic_word_evidence,
)


def test_continuation_windows_keep_the_original_word_scope_and_tail() -> None:
    _, region = example()
    words = tuple(
        replace(
            region.words[0],
            word_index=i,
            start_seconds=float(i),
            end_seconds=i + 0.5,
            current_owner="a",
        )
        for i in range(10)
    )
    region = replace(region, words=words, end_word_index=10)
    windows = plan_ownership_acoustic_windows((region,), (), {}, "source-sha")
    assert windows
    assert windows[-1].last_word_index == 9
    for window in windows:
        selected = words[window.first_word_index : window.last_word_index + 1]
        assert all(w.start_seconds >= window.start_seconds for w in selected)
        assert all(w.end_seconds <= window.end_seconds for w in selected)
        assert window.end_seconds - window.start_seconds <= 4


def test_short_response_does_not_get_a_neighbor_contaminated_measurement() -> None:
    _, region = example()
    assert plan_ownership_acoustic_windows((region,), (), {}, "source-sha") == ()


def test_voiceprint_can_disagree_with_raw_owner_but_cannot_certify_mixed_speech() -> (
    None
):
    _, region = example()
    word = replace(region.words[0], start_seconds=0, end_seconds=4)
    region = replace(region, words=(word,), end_word_index=1)
    clean = plan_ownership_acoustic_windows((region,), (), {}, "source-sha")[0]
    evidence = acoustic_window_evidence(clean, {"a": 0.2, "b": 0.8})
    assert evidence is not None and evidence.owner == "b"
    assert (
        acoustic_window_evidence(
            replace(clean, overlap_fraction=0.5), {"a": 0.2, "b": 0.8}
        )
        is None
    )
    assert acoustic_window_evidence(clean, {"a": 0.6, "b": 0.65}) is None
    assert acoustic_window_evidence(clean, {"a": 0.1, "b": 0.3}) is None


def test_repeated_raw_labels_for_one_identity_do_not_create_crosstalk() -> None:
    _, region = example()
    word = replace(region.words[0], start_seconds=0, end_seconds=4)
    region = replace(region, words=(word,), end_word_index=1)
    windows = plan_ownership_acoustic_windows(
        (region,), (("x", 0, 4), ("y", 0, 4)), {"x": "a", "y": "a"}, "source-sha"
    )
    assert windows[0].overlap_fraction == 0


def test_inconsistent_timing_cannot_expand_evidence_to_an_uncovered_word() -> None:
    _, region = example()
    words = (
        replace(region.words[0], start_seconds=0, end_seconds=0.5),
        replace(region.words[0], word_index=1, start_seconds=1, end_seconds=7),
        replace(region.words[0], word_index=2, start_seconds=2, end_seconds=2.5),
    )
    windows = plan_ownership_acoustic_windows(
        (replace(region, words=words, end_word_index=3),), (), {}, "source-sha"
    )
    assert not any(w.first_word_index == 0 and w.last_word_index == 2 for w in windows)


def test_dominant_window_does_not_vote_on_mixed_or_zero_duration_words() -> None:
    _, region = example()
    words = tuple(
        replace(
            region.words[0],
            word_index=i,
            start_seconds=float(i),
            end_seconds=i + 0.5,
            speaker_seconds=(("a", 0.5),),
        )
        for i in range(4)
    )
    words = (
        words[0],
        replace(words[1], speaker_seconds=(("a", 0.3), ("b", 0.2))),
        replace(words[2], end_seconds=2.0),
        words[3],
    )
    windows = plan_ownership_acoustic_windows(
        (replace(region, words=words, end_word_index=4),), (), {}, "source"
    )
    evidence = project_acoustic_word_evidence(windows[0], {"a": 0.2, "b": 0.8}, words)
    assert [(v.start_word_index, v.end_word_index, v.owner) for v in evidence] == [
        (0, 1, "b"),
        (3, 4, "b"),
    ]


def test_scope_edge_changes_request_evidence_for_the_whole_initial_owner_run() -> None:
    _, region = example()
    region = replace(region, start_word_index=3, end_word_index=7)
    original = ("a",) * 10
    changed = ("a",) * 3 + ("b",) * 4 + ("a",) * 3
    assert find_ownership_edge_gaps((region,), original, changed) == ((0, 10),)
    assert find_ownership_edge_gaps((region,), original, original) == ()
