from __future__ import annotations

from typing import Any

from transpodcast.asr_guard import (
    CHUNK_TARGET_SECONDS,
    RETRY_TARGET_SECONDS,
    runaway_reason,
    transcribe_guarded,
    transcript_runaways,
)

LOOP = "Here's something you're just like come on okay here's what you need to know. "
SPEECH = (
    "What's something you think people may be misunderstanding about the AI "
    "safety stuff and what is going on right now with these models"
)


def test_ordinary_speech_is_not_a_runaway() -> None:
    assert runaway_reason(SPEECH, 6.0) is None
    # A natural restart repeats a short phrase a few times.
    assert runaway_reason("I think, I think, I think we should ship it.", 3.0) is None


def test_a_repetition_loop_is_a_runaway() -> None:
    # A recorded loop: the host's question repeated until the chunk's token limit.
    reason = runaway_reason(LOOP * 192, 178.0)

    assert reason is not None
    assert "words/s" in reason


def test_a_slow_loop_is_caught_by_its_repeats() -> None:
    reason = runaway_reason(LOOP * 6, 175.0)

    assert reason is not None
    assert reason.startswith("6 repeats of 'here's something")


class FakeAudio:
    """Audio as a list of (seconds, text) parts; a part can loop."""

    def __init__(self, parts: list[tuple[float, str]]) -> None:
        self.parts = parts


def _seconds(audio: FakeAudio) -> float:
    return sum(seconds for seconds, _ in audio.parts)


def _split(audio: FakeAudio, target: float) -> list[tuple[FakeAudio, float]]:
    pieces: list[tuple[FakeAudio, float]] = []
    offset = 0.0
    current: list[tuple[float, str]] = []
    start = 0.0
    for part in audio.parts:
        if current and sum(s for s, _ in current) + part[0] > target:
            pieces.append((FakeAudio(current), start))
            start = offset
            current = []
        current.append(part)
        offset += part[0]
    pieces.append((FakeAudio(current), start))
    return pieces


def _transcribe(calls: list[float]) -> Any:
    def transcribe(audio: FakeAudio) -> tuple[str, list[dict[str, Any]]]:
        calls.append(_seconds(audio))
        # A chunk longer than the retry size loops on its "loop" part; a
        # short piece only loops when it is all "stuck".
        long_chunk = _seconds(audio) > RETRY_TARGET_SECONDS
        texts: list[str] = []
        words: list[dict[str, Any]] = []
        cursor = 0.0
        for seconds, text in audio.parts:
            if text == "loop" and long_chunk:
                return LOOP * 40, []
            if text == "stuck":
                return LOOP * 10, []
            texts.append(text)
            words.append(
                {"text": text, "start_seconds": cursor, "end_seconds": cursor + 1.0}
            )
            cursor += seconds
        return " ".join(texts), words

    return transcribe


def test_a_runaway_chunk_is_transcribed_again_in_short_pieces() -> None:
    first = [(25.0, f"first{index}") for index in range(7)]
    second = [
        (25.0, "intro"),
        (25.0, "loop"),
        *[(25.0, f"answer{i}") for i in range(4)],
    ]
    calls: list[float] = []

    result = transcribe_guarded(
        FakeAudio(first + second), _seconds, _split, _transcribe(calls)
    )

    assert result.chunk_count == 2
    assert calls == [175.0, 150.0, 25.0, 25.0, 25.0, 25.0, 25.0, 25.0]
    assert result.text.startswith("first0 first1")
    assert "first6 intro loop answer0" in result.text
    assert [word["start_seconds"] for word in result.words][7:9] == [175.0, 200.0]
    (runaway,) = result.runaways
    assert runaway["start_seconds"] == 175.0
    assert runaway["recovered"] is True
    assert runaway["dropped_spans"] == []
    assert result.diagnostics()["policy_version"] == "asr-runaway-guard-v1"


def test_a_piece_that_still_runs_away_is_dropped_and_reported() -> None:
    parts = [(25.0, "intro"), (25.0, "stuck"), (25.0, "after")]
    calls: list[float] = []

    result = transcribe_guarded(FakeAudio(parts), _seconds, _split, _transcribe(calls))

    assert result.text == "intro after"
    (runaway,) = result.runaways
    assert runaway["recovered"] is False
    assert runaway["dropped_spans"] == [[25.0, 50.0]]


def test_chunk_texts_are_joined_with_spaces() -> None:
    parts = [(CHUNK_TARGET_SECONDS, "come on."), (30.0, "Which is")]

    result = transcribe_guarded(FakeAudio(parts), _seconds, _split, _transcribe([]))

    assert result.text == "come on. Which is"


def test_a_stored_transcript_loop_is_found_by_its_window() -> None:
    speech = [
        (word, 10.0 + index, 10.5 + index) for index, word in enumerate(SPEECH.split())
    ]
    loop = [(word, 2600.0, 2600.0) for word in (LOOP * 20).split()]

    assert transcript_runaways(speech) == []
    ((start, end, reason),) = transcript_runaways(speech + loop)
    assert (start, end) == (2520.0, 2700.0)
    assert reason.startswith("20 repeats")
