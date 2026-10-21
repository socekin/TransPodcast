"""Guards against Qwen3-ASR runaway repetition.

Qwen3-ASR sometimes stops following the audio and repeats its own text until
the chunk's token limit, which silently replaces minutes of speech: one
recorded episode lost 2 min 45 s of the guest's answer to 192 repeats of the
host's question, and another lost a passage to 36 repeats. The transformers
backend of
qwen-asr 0.0.6 makes this far likelier: without FlashAttention 2 its audio
encoder ignores the 8 s attention windows the model was trained with
(QwenLM/Qwen3-ASR#213; a production report measured runaway chunks drop from
37.5% to about 14% with the window mask of PR #103).

The Modal transcriber applies ``apply_audio_window_fix`` and transcribes
through ``transcribe_guarded``:

- chunks of at most 180 s, as qwen-asr cuts them for forced alignment, each
  transcribed on its own;
- a chunk whose text repeats itself or is denser than speech can be is
  transcribed again in pieces of at most 30 s; a piece that still runs away
  is dropped and reported, never voiced;
- chunk texts joined with spaces (qwen-asr joins them with nothing,
  QwenLM/Qwen3-ASR#217: "comeWhich").

Everything except ``apply_audio_window_fix`` is plain Python, so it is
tested without the model.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

ASR_GUARD_POLICY_VERSION = "asr-runaway-guard-v1"
# qwen-asr cuts at a low-energy point up to 5 s past its target, and splits
# anything over 180 s again; 175 s targets keep every chunk whole.
CHUNK_TARGET_SECONDS = 175.0
RETRY_TARGET_SECONDS = 25.0
# Speech peaks near 4.6 words/s over a minute in every recorded episode; the
# recorded loops reached 41-57.
RUNAWAY_WORDS_PER_SECOND = 7.0
# No 8-word phrase repeats more than 3 times in any past episode outside
# the two loops (36 and 192).
RUNAWAY_NGRAM_WORDS = 8
RUNAWAY_NGRAM_REPEATS = 6
_MINIMUM_RATE_SECONDS = 5.0
_WORD = re.compile(r"[\w']+")

Word = dict[str, Any]
Transcribe = Callable[[Any], tuple[str, list[Word]]]
Split = Callable[[Any, float], list[tuple[Any, float]]]


def runaway_reason(text: str, seconds: float) -> str | None:
    """Why a chunk transcript cannot be speech, or ``None``."""
    words = _WORD.findall(text.lower())
    rate = len(words) / max(seconds, _MINIMUM_RATE_SECONDS)
    if rate > RUNAWAY_WORDS_PER_SECOND:
        return f"{rate:.1f} words/s"
    grams = Counter(
        tuple(words[index : index + RUNAWAY_NGRAM_WORDS])
        for index in range(len(words) - RUNAWAY_NGRAM_WORDS + 1)
    )
    if grams:
        phrase, repeats = grams.most_common(1)[0]
        if repeats >= RUNAWAY_NGRAM_REPEATS:
            return f"{repeats} repeats of '{' '.join(phrase)}'"
    return None


def transcript_runaways(
    words: Sequence[tuple[str, float, float]],
    window_seconds: float = 180.0,
) -> list[tuple[float, float, str]]:
    """Runaway windows in a stored transcript, as ``(start, end, reason)``.

    A Source Analysis made before this guard may hold a loop; reprocessing
    must then transcribe the episode again instead of reusing it.
    """
    windows: dict[int, list[str]] = {}
    for text, start, _end in words:
        windows.setdefault(int(float(start) // window_seconds), []).append(text)
    found: list[tuple[float, float, str]] = []
    for index in sorted(windows):
        reason = runaway_reason(" ".join(windows[index]), window_seconds)
        if reason is not None:
            found.append(
                (index * window_seconds, (index + 1) * window_seconds, reason)
            )
    return found


@dataclass
class GuardedTranscript:
    text: str
    words: list[Word]
    runaways: list[dict[str, Any]] = field(default_factory=list)
    chunk_count: int = 0

    def diagnostics(self) -> dict[str, Any]:
        return {
            "policy_version": ASR_GUARD_POLICY_VERSION,
            "chunk_count": self.chunk_count,
            "runaways": self.runaways,
        }


def transcribe_guarded(
    audio: Any,
    seconds: Callable[[Any], float],
    split: Split,
    transcribe: Transcribe,
) -> GuardedTranscript:
    """Transcribe ``audio`` chunk by chunk, re-transcribing runaway chunks.

    ``split(audio, target_seconds)`` returns ``(piece, offset_seconds)``
    pairs that cover the audio; ``transcribe(piece)`` returns the piece's
    text and words with times relative to the piece; ``seconds(piece)`` is
    its duration.
    """
    texts: list[str] = []
    words: list[Word] = []
    runaways: list[dict[str, Any]] = []
    chunks = split(audio, CHUNK_TARGET_SECONDS)
    for chunk, offset in chunks:
        text, chunk_words = transcribe(chunk)
        reason = runaway_reason(text, seconds(chunk))
        if reason is None:
            texts.append(text)
            words.extend(_shifted(chunk_words, offset))
            continue
        dropped: list[list[float]] = []
        for piece, piece_offset in split(chunk, RETRY_TARGET_SECONDS):
            start = offset + piece_offset
            piece_text, piece_words = transcribe(piece)
            if runaway_reason(piece_text, seconds(piece)) is not None:
                dropped.append([round(start, 3), round(start + seconds(piece), 3)])
                continue
            texts.append(piece_text)
            words.extend(_shifted(piece_words, start))
        runaways.append(
            {
                "start_seconds": round(offset, 3),
                "end_seconds": round(offset + seconds(chunk), 3),
                "reason": reason,
                "recovered": not dropped,
                "dropped_spans": dropped,
            }
        )
    return GuardedTranscript(
        text=" ".join(text.strip() for text in texts if text.strip()),
        words=words,
        runaways=runaways,
        chunk_count=len(chunks),
    )


def _shifted(words: Sequence[Word], offset: float) -> list[Word]:
    return [
        {
            **word,
            "start_seconds": float(word["start_seconds"]) + offset,
            "end_seconds": float(word["end_seconds"]) + offset,
        }
        for word in words
    ]


def apply_audio_window_fix(audio_encoder: Any) -> bool:
    """Make the qwen-asr 0.0.6 audio encoder attend within its windows.

    The encoder computes ``cu_seqlens`` for its 8 s windows but only the
    FlashAttention 2 kernel uses them; with sdpa or eager every frame
    attends to the whole chunk. This passes the block-diagonal mask the
    encoder already knows how to build to every layer (QwenLM/Qwen3-ASR
    PR #103). Returns whether the fix was applied.
    """
    if audio_encoder.config._attn_implementation == "flash_attention_2":
        return False
    for layer in audio_encoder.layers:
        original = layer.forward

        def forward(
            hidden_states: Any,
            cu_seqlens: Any,
            attention_mask: Any = None,
            _original: Any = original,
            **kwargs: Any,
        ) -> Any:
            if attention_mask is None:
                attention_mask = audio_encoder._prepare_attention_mask(
                    hidden_states, cu_seqlens
                )
            return _original(
                hidden_states, cu_seqlens, attention_mask=attention_mask, **kwargs
            )

        layer.forward = forward
    return True
