from __future__ import annotations

from transpodcast.modal_pipeline import plan_consolidation_windows


def _turn(speaker: str, start: float, end: float) -> dict:
    return {
        "speaker": speaker,
        "start_seconds": start,
        "end_seconds": end,
    }


def test_long_turns_are_chunked_and_short_scraps_dropped() -> None:
    turns = [
        _turn("speaker-a", 100.0, 127.0),
        _turn("speaker-a", 200.0, 201.0),
    ]

    planned = plan_consolidation_windows(turns)

    windows = planned["speaker-a"]
    assert [
        (window["start_seconds"], window["end_seconds"])
        for window in windows
    ] == [(100.0, 112.0), (112.0, 124.0), (124.0, 127.0)]
    assert all(window["clean"] for window in windows)


def test_overlapped_stretches_are_carved_out_not_discarded() -> None:
    # Conversational audio overlaps constantly; a whole-window veto
    # starves labels of voters (a historical calibration failure). The
    # exclusive stretch before the overlap keeps its vote.
    turns = [
        _turn("speaker-a", 100.0, 110.0),
        _turn("speaker-b", 108.0, 112.0),
        _turn("speaker-a", 200.0, 210.0),
    ]

    planned = plan_consolidation_windows(turns)

    assert [
        (
            window["start_seconds"],
            window["end_seconds"],
            window["clean"],
        )
        for window in planned["speaker-a"]
    ] == [(100.0, 108.0, True), (200.0, 210.0, True)]


def test_fully_overlapped_turns_produce_no_windows() -> None:
    turns = [
        _turn("speaker-a", 100.0, 103.0),
        _turn("speaker-b", 99.0, 104.0),
    ]

    planned = plan_consolidation_windows(turns)

    assert "speaker-a" not in planned


def test_cold_open_windows_are_kept_for_consolidation() -> None:
    # Position screening applies only to Voice Reference selection; the
    # phantom labels this stage merges live inside the cold open.
    turns = [_turn("speaker-a", 0.0, 8.0)]

    planned = plan_consolidation_windows(turns)

    assert planned["speaker-a"] == [
        {
            "start_seconds": 0.0,
            "end_seconds": 8.0,
            "clean": True,
        }
    ]


def test_each_label_is_capped_with_even_spread() -> None:
    turns = [
        _turn("speaker-a", float(index * 100), float(index * 100 + 10))
        for index in range(12)
    ]

    planned = plan_consolidation_windows(turns, per_label_maximum=6)

    windows = planned["speaker-a"]
    assert len(windows) == 6
    assert windows[0]["start_seconds"] == 0.0
    assert windows[-1]["start_seconds"] == 1100.0
