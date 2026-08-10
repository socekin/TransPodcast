from __future__ import annotations

from dataclasses import replace

from transpodcast.adapters.codex import (
    CodexExecution,
    CodexTranslationAdapter,
)
from transpodcast.boundary_adjudication import OwnershipDispute
from transpodcast.speaker_tail_boundary_repair import (
    ATOMIC_SPEAKER_TAIL_REPAIR_POLICY_VERSION,
    SpeakerTailRepairCandidate,
    WordSpeakerSupport,
)


def _dispute() -> OwnershipDispute:
    return OwnershipDispute(
        dispute_id="dispute-371.441-speaker_3",
        words=(
            {
                "text": "right",
                "start_seconds": 371.441,
                "end_seconds": 371.72,
            },
            {
                "text": "And",
                "start_seconds": 371.72,
                "end_seconds": 371.9,
            },
        ),
        current_owner="speaker_3",
        counterpart="speaker_2",
        context_before=("that", "seems"),
        context_after=("then", "we"),
    )


def _answer(owner: str) -> dict:
    return {
        "groups": [
            {
                "group_id": "dispute-371.441-speaker_3",
                "verdicts": [
                    {
                        "dispute_id": "dispute-371.441-speaker_3",
                        "owner": owner,
                        "rationale": "Continues the sentence.",
                    }
                ],
            }
        ]
    }


class FlakyRunner:
    def __init__(self, failures: int, output: dict) -> None:
        self.failures = failures
        self.calls = 0
        self.output = output

    def run(self, *, instructions, source_data, schema):
        del instructions, schema
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("stream disconnected")
        if "groups" in source_data:
            assert (
                source_data["groups"][0]["candidates"][0]["disputed_words"]
                == "right And"
            )
        elif "components" in source_data:
            assert source_data["components"][0]["candidates"][0][
                "current_segment_id"
            ] == "seg-0035"
        else:
            assert source_data["candidates"][0]["current_segment_id"] == ("seg-0035")
        return CodexExecution(
            output=self.output,
            model="gpt-5",
            input_tokens=200,
            output_tokens=40,
            cli_version="codex-cli 1.2.3",
        )


def _adapter(runner: FlakyRunner, tmp_path) -> CodexTranslationAdapter:
    return CodexTranslationAdapter(
        runner,  # type: ignore[arg-type]
        tmp_path,
    )


def _tail_repair_candidate() -> SpeakerTailRepairCandidate:
    return SpeakerTailRepairCandidate(
        candidate_id=("tail-repair::seg-0035::seg-0036::346.864"),
        current_segment_id="seg-0035",
        next_segment_id="seg-0036",
        current_owner="speaker_3",
        next_owner="speaker_0",
        current_words=(
            WordSpeakerSupport(
                text="Ah",
                start_seconds=346.864,
                end_seconds=347.184,
                speaker_seconds=(("speaker_3", 0.32),),
            ),
            WordSpeakerSupport(
                text="kidding",
                start_seconds=347.184,
                end_seconds=347.904,
                speaker_seconds=(
                    ("speaker_0", 0.62),
                    ("speaker_3", 0.72),
                ),
            ),
            WordSpeakerSupport(
                text="Yes",
                start_seconds=347.904,
                end_seconds=347.984,
                speaker_seconds=(
                    ("speaker_0", 0.08),
                    ("speaker_3", 0.08),
                ),
            ),
        ),
        next_words=(
            WordSpeakerSupport(
                text="know",
                start_seconds=348.2,
                end_seconds=348.6,
                speaker_seconds=(("speaker_0", 0.4),),
            ),
        ),
        permitted_shift_before_word_indexes=(1, 2),
    )


def test_verdicts_and_usage_come_back_through_the_adapter(
    tmp_path,
) -> None:
    adapter = _adapter(FlakyRunner(0, _answer("speaker_2")), tmp_path)

    result = adapter.adjudicate_discourse([_dispute()])

    verdict = result.verdicts["dispute-371.441-speaker_3"]
    assert verdict is not None
    assert verdict.owner == "speaker_2"
    assert result.input_tokens == 200
    assert result.output_tokens == 40
    assert result.provider == "codex-cli"
    assert result.model == "gpt-5"
    assert result.cli_version == "codex-cli 1.2.3"
    assert result.error is None


def test_one_transient_failure_is_retried(tmp_path) -> None:
    runner = FlakyRunner(1, _answer("speaker_3"))
    adapter = _adapter(runner, tmp_path)

    result = adapter.adjudicate_discourse([_dispute()])

    assert runner.calls == 2
    verdict = result.verdicts["dispute-371.441-speaker_3"]
    assert verdict is not None
    assert verdict.owner == "speaker_3"
    assert result.error is None


def test_persistent_failure_becomes_a_recorded_abstention(
    tmp_path,
) -> None:
    runner = FlakyRunner(5, _answer("speaker_2"))
    adapter = _adapter(runner, tmp_path)

    result = adapter.adjudicate_discourse([_dispute()])

    assert runner.calls == 2
    assert result.verdicts == {}
    assert result.error is not None
    assert "stream disconnected" in result.error
    assert result.input_tokens == 0


def test_an_unparseable_answer_keeps_the_spent_tokens(
    tmp_path,
) -> None:
    # The provider answered (tokens were spent) but the payload is
    # malformed enough to crash parsing: the abstention must still
    # carry the real usage.
    adapter = _adapter(FlakyRunner(0, {"verdicts": ["not-a-mapping"]}), tmp_path)

    result = adapter.adjudicate_discourse([_dispute()])

    assert result.verdicts == {}
    assert result.error is not None
    assert result.input_tokens == 200
    assert result.output_tokens == 40
    assert result.model == "gpt-5"
    assert result.cli_version == "codex-cli 1.2.3"


def test_speaker_tail_repair_verdict_and_usage_are_validated(
    tmp_path,
) -> None:
    candidate = _tail_repair_candidate()
    runner = FlakyRunner(
        0,
        {
            "verdicts": [
                {
                    "candidate_id": candidate.candidate_id,
                    "action": "shift_tail",
                    "shift_before_word_index": 2,
                    "rationale": "A new answer begins with Yes.",
                }
            ]
        },
    )
    adapter = _adapter(runner, tmp_path)

    result = adapter.adjudicate_speaker_tail_repairs([candidate])

    verdict = result.verdicts[candidate.candidate_id]
    assert verdict is not None
    assert verdict.action == "shift_tail"
    assert verdict.shift_before_word_index == 2
    assert result.input_tokens == 200
    assert result.output_tokens == 40
    assert result.provider == "codex-cli"


def test_incomplete_speaker_tail_answer_is_a_safe_batch_abstention(
    tmp_path,
) -> None:
    adapter = _adapter(FlakyRunner(0, {"verdicts": []}), tmp_path)

    result = adapter.adjudicate_speaker_tail_repairs([_tail_repair_candidate()])

    assert result.verdicts == {}
    assert result.error is not None
    assert "missing candidates" in result.error
    assert result.input_tokens == 200


def test_v13_isolates_one_identifiable_malformed_tail_verdict(
    tmp_path,
) -> None:
    first = replace(
        _tail_repair_candidate(),
        question_policy_version=ATOMIC_SPEAKER_TAIL_REPAIR_POLICY_VERSION,
    )
    second = replace(
        first,
        candidate_id="tail-repair::seg-0040::seg-0041::400.000",
        current_segment_id="seg-0040",
        next_segment_id="seg-0041",
    )
    adapter = _adapter(
        FlakyRunner(
            0,
            {
                "verdicts": [
                    {
                        "candidate_id": first.candidate_id,
                        "action": "shift_tail",
                        "shift_before_word_index": 0,
                        "rationale": "Malformed local boundary.",
                    },
                    {
                        "candidate_id": second.candidate_id,
                        "action": "shift_tail",
                        "shift_before_word_index": 2,
                        "rationale": "Valid independent boundary.",
                    },
                ]
            },
        ),
        tmp_path,
    )

    result = adapter.adjudicate_speaker_tail_repairs([first, second])

    assert result.error is None
    assert result.verdicts[first.candidate_id] is None
    assert result.verdicts[second.candidate_id] is not None
    assert "not acoustically permitted" in result.validation_errors[
        first.candidate_id
    ]
    assert result.raw_proposals[first.candidate_id] == (
        {
            "action": "shift_tail",
            "shift_before_word_index": 0,
            "rationale": "Malformed local boundary.",
        },
    )
    assert second.candidate_id not in result.validation_errors
    assert result.input_tokens == 200


def test_v13_isolates_duplicate_output_for_one_candidate(tmp_path) -> None:
    first = replace(
        _tail_repair_candidate(),
        question_policy_version=ATOMIC_SPEAKER_TAIL_REPAIR_POLICY_VERSION,
    )
    second = replace(
        first,
        candidate_id="tail-repair::seg-0040::seg-0041::400.000",
        current_segment_id="seg-0040",
        next_segment_id="seg-0041",
    )
    duplicate = {
        "candidate_id": first.candidate_id,
        "action": "shift_tail",
        "shift_before_word_index": 2,
        "rationale": "Duplicated proposal.",
    }
    adapter = _adapter(
        FlakyRunner(
            0,
            {
                "verdicts": [
                    duplicate,
                    duplicate,
                    {
                        "candidate_id": second.candidate_id,
                        "action": "keep",
                        "shift_before_word_index": -1,
                        "rationale": "Independent valid proposal.",
                    },
                ]
            },
        ),
        tmp_path,
    )

    result = adapter.adjudicate_speaker_tail_repairs([first, second])

    assert result.error is None
    assert result.verdicts[first.candidate_id] is None
    assert result.verdicts[second.candidate_id] is not None
    assert "duplicate proposals" in result.validation_errors[first.candidate_id]
    assert len(result.raw_proposals[first.candidate_id]) == 2
