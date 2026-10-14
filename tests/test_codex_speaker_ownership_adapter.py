from __future__ import annotations

from transpodcast.adapters.codex import (
    CodexExecution,
    CodexTranslationAdapter,
)
from transpodcast.domain import CanonicalSegment
from transpodcast.speaker_ownership_v15 import (
    SpeakerOwnershipRegion,
    build_speaker_ownership_regions,
)


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
        assert source_data["regions"][0]["words"][1]["text"] == "We're"
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


def _region() -> SpeakerOwnershipRegion:
    segments = (
        CanonicalSegment(
            segment_id="seg-a",
            speaker_label="speaker-a",
            start_seconds=0.0,
            end_seconds=0.8,
            text="dimension We're",
            words=(("dimension", 0.0, 0.4), ("We're", 0.4, 0.8)),
        ),
        CanonicalSegment(
            segment_id="seg-b",
            speaker_label="speaker-b",
            start_seconds=0.8,
            end_seconds=1.2,
            text="unclear",
            words=(("unclear", 0.8, 1.2),),
        ),
    )
    return build_speaker_ownership_regions(
        segments,
        (
            ("speaker-a", 0.0, 0.4),
            ("speaker-b", 0.4, 1.2),
        ),
        {},
    )[0]


def _plan_output(region: SpeakerOwnershipRegion) -> dict:
    return {
        "plans": [
            {
                "region_id": region.region_id,
                "action": "assign_spans",
                "assignments": [
                    {
                        "first_word_index": 0,
                        "last_word_index": 0,
                        "speaker_label": "speaker-a",
                    },
                    {
                        "first_word_index": 1,
                        "last_word_index": 2,
                        "speaker_label": "speaker-b",
                    },
                ],
                "missing_speech_suspected": False,
                "rationale": "We're begins the incoming answer.",
            }
        ]
    }


def test_v15_ownership_plan_and_usage_return_through_the_adapter(
    tmp_path,
) -> None:
    region = _region()
    adapter = _adapter(FlakyRunner(0, _plan_output(region)), tmp_path)

    result = adapter.plan_speaker_ownership((region,))

    plan = result.plans[region.region_id]
    assert plan is not None
    assert [assignment.speaker_label for assignment in plan.assignments] == [
        "speaker-a",
        "speaker-b",
    ]
    assert result.input_tokens == 200
    assert result.output_tokens == 40
    assert result.provider == "codex-cli"
    assert result.model == "gpt-5"
    assert result.cli_version == "codex-cli 1.2.3"
    assert result.error is None


def test_one_transient_ownership_failure_is_retried(tmp_path) -> None:
    region = _region()
    runner = FlakyRunner(1, _plan_output(region))
    adapter = _adapter(runner, tmp_path)

    result = adapter.review_speaker_ownership((region,))

    assert runner.calls == 2
    assert result.plans[region.region_id] is not None
    assert result.error is None


def test_persistent_ownership_failure_becomes_a_recorded_degradation(
    tmp_path,
) -> None:
    region = _region()
    runner = FlakyRunner(5, _plan_output(region))
    adapter = _adapter(runner, tmp_path)

    result = adapter.plan_speaker_ownership((region,))

    assert runner.calls == 2
    assert result.plans == {}
    assert result.error is not None
    assert "stream disconnected" in result.error
    assert result.input_tokens == 0


def test_an_unparseable_ownership_answer_keeps_the_spent_tokens(
    tmp_path,
) -> None:
    # The provider answered (tokens were spent) but the payload is
    # malformed enough to fail parsing: the degradation must still
    # carry the real usage.
    region = _region()
    adapter = _adapter(FlakyRunner(0, {"plans": "not-a-list"}), tmp_path)

    result = adapter.plan_speaker_ownership((region,))

    assert result.plans == {}
    assert result.error is not None
    assert result.input_tokens == 200
    assert result.output_tokens == 40
    assert result.model == "gpt-5"
    assert result.cli_version == "codex-cli 1.2.3"
