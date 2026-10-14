from typing import Any

from tests.test_speaker_ownership_continuity import draft, example
from transpodcast.adapters.codex import CodexExecution
from transpodcast.adapters.codex_ownership_continuity import CodexContinuityJudge


class Runner:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def run(
        self, *, instructions: str, source_data: dict[str, Any], schema: dict[str, Any]
    ) -> CodexExecution:
        self.requests.append(
            dict(instructions=instructions, source_data=source_data, schema=schema)
        )
        return CodexExecution(
            output={
                "plans": [
                    {
                        "region_id": "risk",
                        "action": "keep",
                        "assignments": [],
                        "missing_speech_suspected": False,
                        "rationale": "The observed handoffs are plausible.",
                    }
                ]
            },
            model="fixture",
            input_tokens=123,
            output_tokens=45,
            cli_version="fixture",
        )


def test_review_receives_original_evidence_and_partial_repair_contract() -> None:
    _, region = example()
    runner = Runner()
    judge = CodexContinuityJudge(runner)
    result = judge.review((region,), draft())

    assert result.plans["risk"].action == "keep"
    assert result.input_tokens == 123
    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request["source_data"]["regions"][0]["word_rows"][1][4] == "b"
    assert (
        request["source_data"]["planner_draft"]["plans"][0]["assignments"][0][
            "speaker_label"
        ]
        == "a"
    )
    assert (
        "uncertainty about a later continuation must NOT discard"
        in request["instructions"]
    )
