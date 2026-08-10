from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_MODAL_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$MODAL_STUB_CALLS"
case "$*" in
  "billing summary --json")
    file="$MODAL_BILLING_DIR/${MODAL_PROFILE:-default}.json"
    if [ -f "$file" ]; then
      cat "$file"
      exit 0
    fi
    echo "billing unavailable" >&2
    exit 1
    ;;
  *)
    exit 64
    ;;
esac
"""

_PIPELINE_STUB = """\
import json
import os
import sys
import time

profile = os.environ.get("MODAL_PROFILE") or None
with open(os.environ["PIPELINE_RECORD"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "profile": profile}) + "\\n")
plan_name = (profile or "default") + ".json"
plan_path = os.path.join(os.environ["PIPELINE_PLAN_DIR"], plan_name)
plan = {}
if os.path.exists(plan_path):
    with open(plan_path, encoding="utf-8") as fh:
        plan = json.load(fh)
time.sleep(plan.get("sleep", 0))
sys.stdout.write(plan.get("stdout", ""))
sys.stderr.write(plan.get("stderr", ""))
code = int(plan.get("exit_code", 0))
arguments = dict(zip(sys.argv[1::2], sys.argv[2::2]))
if plan.get("invalid_output"):
    with open(arguments["--output"], "w", encoding="utf-8") as fh:
        fh.write("partial-json")
if code == 0 and not plan.get("invalid_output"):
    with open(arguments["--output"], "w", encoding="utf-8") as fh:
        json.dump(plan.get("response", {"ok": True}), fh)
sys.exit(code)
"""


def _billing_document(credits: str) -> str:
    return json.dumps(
        {
            "metered_cost": "14.01631411",
            "billed_cost": "0E-8",
            "adjustments": {
                "free_storage": "-0.47143004",
                "reservation_adjustment": "-0E-8",
                "plan_cost": "0E-8",
                "credits": credits,
            },
            "metered_cost_breakdown": {},
        }
    )


class RouterRun:
    def __init__(
        self,
        completed: subprocess.CompletedProcess[str],
        root: Path,
    ) -> None:
        self.completed = completed
        self.root = root

    @property
    def returncode(self) -> int:
        return self.completed.returncode

    @property
    def stdout(self) -> str:
        return self.completed.stdout

    @property
    def stderr(self) -> str:
        return self.completed.stderr

    @property
    def adapter_tail(self) -> str:
        return self.completed.stderr.strip()[-1000:]

    @property
    def output_path(self) -> Path:
        return self.root / "response.json"

    @property
    def attempts(self) -> list[dict[str, object]]:
        record = self.root / "pipeline-record.jsonl"
        if not record.is_file():
            return []
        return [
            json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()
        ]

    @property
    def billing_calls(self) -> list[str]:
        calls = self.root / "modal-calls.log"
        if not calls.is_file():
            return []
        return calls.read_text(encoding="utf-8").splitlines()

    @property
    def log_text(self) -> str:
        log = self.root / "router.log"
        return log.read_text(encoding="utf-8") if log.is_file() else ""


def _run_router(
    tmp_path: Path,
    *,
    arguments: list[str] | None = None,
    operation: str = "analyze",
    profiles: str | None = "alpha,beta",
    billing: dict[str, str | None] | None = None,
    plans: dict[str, dict[str, object]] | None = None,
    env_extra: dict[str, str] | None = None,
    write_dotenv: str | None = None,
    request: dict[str, object] | None = None,
) -> RouterRun:
    bin_dir = tmp_path / "bin"
    billing_dir = tmp_path / "billing"
    plan_dir = tmp_path / "plans"
    for directory in (bin_dir, billing_dir, plan_dir):
        directory.mkdir(exist_ok=True)

    modal_stub = bin_dir / "modal"
    modal_stub.write_text(_MODAL_STUB, encoding="utf-8")
    modal_stub.chmod(0o755)
    pipeline_stub = tmp_path / "pipeline_stub.py"
    pipeline_stub.write_text(_PIPELINE_STUB, encoding="utf-8")

    for profile, document in (billing or {}).items():
        if document is not None:
            (billing_dir / f"{profile}.json").write_text(
                document,
                encoding="utf-8",
            )
    for profile, plan in (plans or {}).items():
        (plan_dir / f"{profile}.json").write_text(
            json.dumps(plan),
            encoding="utf-8",
        )

    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(request or {}),
        encoding="utf-8",
    )
    if write_dotenv is not None:
        (tmp_path / ".env").write_text(write_dotenv, encoding="utf-8")

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("TRANSPODCAST_") and key != "MODAL_PROFILE"
    }
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    environment["MODAL_STUB_CALLS"] = str(tmp_path / "modal-calls.log")
    environment["MODAL_BILLING_DIR"] = str(billing_dir)
    environment["PIPELINE_PLAN_DIR"] = str(plan_dir)
    environment["PIPELINE_RECORD"] = str(tmp_path / "pipeline-record.jsonl")
    environment["TRANSPODCAST_MODAL_DELEGATE_COMMAND"] = (
        f"{sys.executable} {pipeline_stub}"
    )
    environment["TRANSPODCAST_MODAL_ROUTER_LOG_PATH"] = str(tmp_path / "router.log")
    if profiles is not None:
        environment["TRANSPODCAST_MODAL_PROFILES"] = profiles
    environment.update(env_extra or {})

    if arguments is None:
        arguments = [
            "--operation",
            operation,
            "--request",
            str(request_path),
            "--output",
            str(tmp_path / "response.json"),
        ]
    completed = subprocess.run(
        [sys.executable, "-m", "transpodcast.modal_router", *arguments],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
        timeout=60,
    )
    return RouterRun(completed, tmp_path)


def test_routes_first_eligible_profile(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        billing={"alpha": _billing_document("-13.54488408")},
        plans={"alpha": {"stdout": "done", "response": {"ready": True}}},
    )

    assert run.returncode == 0
    assert run.stdout == "done"
    response = json.loads(run.output_path.read_text())
    assert response["ready"] is True
    assert response["routing"]["selected_profile"] == "alpha"
    assert [attempt["profile"] for attempt in run.attempts] == ["alpha"]
    assert run.billing_calls == ["billing summary --json"]


def test_skips_profile_below_threshold(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        billing={
            "alpha": _billing_document("-28.50"),
            "beta": _billing_document("-5.00"),
        },
    )

    assert run.returncode == 0
    assert [attempt["profile"] for attempt in run.attempts] == ["beta"]
    assert "alpha" in run.log_text


def test_remaining_exactly_at_threshold_is_eligible(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        billing={"alpha": _billing_document("-28.00")},
    )

    assert run.returncode == 0
    assert [attempt["profile"] for attempt in run.attempts] == ["alpha"]


@pytest.mark.parametrize(
    "document",
    [
        None,
        "not json at all",
        json.dumps({"adjustments": {}}),
        json.dumps({"adjustments": {"credits": "not-a-number"}}),
    ],
)
def test_billing_unavailable_leaves_profile_eligible(
    tmp_path: Path,
    document: str | None,
) -> None:
    run = _run_router(tmp_path, billing={"alpha": document})

    assert run.returncode == 0
    assert [attempt["profile"] for attempt in run.attempts] == ["alpha"]
    assert "billing_unavailable" in run.log_text
    assert '"reason"' in run.log_text


def test_failed_attempt_fails_over_and_hides_stderr(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        billing={
            "alpha": _billing_document("-1.00"),
            "beta": _billing_document("-1.00"),
        },
        plans={
            "alpha": {"exit_code": 1, "stderr": "boom-alpha"},
            "beta": {"stdout": "ok", "stderr": "warn-beta"},
        },
    )

    assert run.returncode == 0
    assert run.stdout == "ok"
    assert "boom-alpha" not in run.stderr
    assert "boom-alpha" not in run.log_text
    assert '"reason": "command_failed"' in run.log_text
    assert [attempt["profile"] for attempt in run.attempts] == [
        "alpha",
        "beta",
    ]


def test_success_response_contains_correlated_routing_evidence(
    tmp_path: Path,
) -> None:
    run = _run_router(
        tmp_path,
        billing={"alpha": _billing_document("-1.00")},
        plans={"alpha": {"response": {"segments": []}}},
        request={
            "routing_context": {
                "processing_run_id": 15,
                "processing_attempt_id": 42,
                "correlation_id": "run-15-analysis-42",
                "stage": "source_analysis",
            }
        },
    )

    assert run.returncode == 0
    response = json.loads(run.output_path.read_text())
    assert response["routing"] == {
        "operation": "analyze",
        "selected_profile": "alpha",
        "correlation_id": "run-15-analysis-42",
        "processing_run_id": 15,
        "processing_attempt_id": 42,
        "stage": "source_analysis",
        "attempts": [
            {
                "profile": "alpha",
                "outcome": "succeeded",
                "remaining_usd": "29.00",
            }
        ],
    }
    assert '"operation": "analyze"' in run.log_text
    assert '"correlation_id": "run-15-analysis-42"' in run.log_text


def test_failover_response_preserves_order_without_provider_stderr(
    tmp_path: Path,
) -> None:
    run = _run_router(
        tmp_path,
        billing={
            "alpha": _billing_document("-1.00"),
            "beta": _billing_document("-2.00"),
        },
        plans={
            "alpha": {"exit_code": 1, "stderr": "secret provider detail"},
            "beta": {"response": {"ok": True}},
        },
    )

    response = json.loads(run.output_path.read_text())
    assert response["routing"]["selected_profile"] == "beta"
    assert response["routing"]["attempts"] == [
        {
            "profile": "alpha",
            "outcome": "failed",
            "reason": "command_failed",
            "remaining_usd": "29.00",
        },
        {
            "profile": "beta",
            "outcome": "succeeded",
            "remaining_usd": "28.00",
        },
    ]
    assert "secret provider detail" not in json.dumps(response)


def test_mixed_markers_classify_by_final_attempt(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        billing={
            "alpha": _billing_document("-1.00"),
            "beta": _billing_document("-1.00"),
        },
        plans={
            "alpha": {"exit_code": 4, "stderr": "[TRANSIENT] alpha blip\n"},
            "beta": {"exit_code": 2, "stderr": "[AUTH] beta denied\n"},
        },
    )

    assert run.returncode == 2
    assert "[TRANSIENT]" not in run.adapter_tail
    assert "[SPEND_GUARDRAIL]" not in run.adapter_tail
    assert "[AUTH]" in run.adapter_tail
    routing = json.loads(run.output_path.read_text())["routing"]
    assert routing.get("selected_profile") is None
    assert routing["attempts"] == [
        {
            "profile": "alpha",
            "outcome": "failed",
            "reason": "transient_provider_failure",
            "remaining_usd": "29.00",
        },
        {
            "profile": "beta",
            "outcome": "failed",
            "reason": "authentication_failed",
            "remaining_usd": "29.00",
        },
    ]


def test_failed_route_replaces_corrupt_provider_output_with_safe_evidence(
    tmp_path: Path,
) -> None:
    run = _run_router(
        tmp_path,
        billing={"alpha": _billing_document("-1.00")},
        profiles="alpha",
        plans={
            "alpha": {
                "exit_code": 1,
                "stderr": "secret provider detail",
                "invalid_output": True,
            }
        },
    )

    assert run.returncode == 1
    response = json.loads(run.output_path.read_text())
    assert response["routing"]["attempts"][0]["reason"] == (
        "command_failed"
    )
    assert "secret provider detail" not in run.output_path.read_text()


def test_corrupt_success_output_fails_over_to_the_next_account(
    tmp_path: Path,
) -> None:
    run = _run_router(
        tmp_path,
        billing={
            "alpha": _billing_document("-1.00"),
            "beta": _billing_document("-1.00"),
        },
        plans={
            "alpha": {"invalid_output": True},
            "beta": {"response": {"ok": True}},
        },
    )

    assert run.returncode == 0
    routing = json.loads(run.output_path.read_text())["routing"]
    assert routing["selected_profile"] == "beta"
    assert routing["attempts"][0]["reason"] == "invalid_response"


def test_long_stderr_keeps_marker_inside_adapter_tail(tmp_path: Path) -> None:
    noise = "y" * 5000
    run = _run_router(
        tmp_path,
        billing={"alpha": _billing_document("-1.00"), "beta": None},
        plans={
            "alpha": {
                "exit_code": 3,
                "stderr": f"[SPEND_GUARDRAIL] limit\n{noise}",
            },
            "beta": {"exit_code": 3, "stderr": "[SPEND_GUARDRAIL] limit\n"},
        },
    )

    assert run.returncode == 3
    assert "[SPEND_GUARDRAIL]" in run.adapter_tail


def test_any_guardrail_attempt_wins_final_classification(
    tmp_path: Path,
) -> None:
    run = _run_router(
        tmp_path,
        billing={
            "alpha": _billing_document("-1.00"),
            "beta": _billing_document("-1.00"),
        },
        plans={
            "alpha": {
                "exit_code": 3,
                "stderr": "[SPEND_GUARDRAIL] env limit\n",
            },
            "beta": {"exit_code": 1, "stderr": "generic failure\n"},
        },
    )

    assert run.returncode == 3
    assert "[SPEND_GUARDRAIL]" in run.adapter_tail


def test_no_eligible_profile_reports_spend_guardrail(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        billing={
            "alpha": _billing_document("-29.00"),
            "beta": _billing_document("-30.00"),
        },
    )

    assert run.returncode == 3
    assert "[SPEND_GUARDRAIL]" in run.adapter_tail
    final_line = run.stderr.strip().splitlines()[-1]
    assert final_line.startswith("[SPEND_GUARDRAIL]")
    assert run.attempts == []


def test_readiness_is_routed(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        operation="readiness",
        billing={"alpha": _billing_document("-13.54488408")},
    )

    assert run.returncode == 0
    attempts = run.attempts
    assert [attempt["profile"] for attempt in attempts] == ["alpha"]
    argv = attempts[0]["argv"]
    assert isinstance(argv, list)
    assert "readiness" in argv


def test_remaster_bypasses_routing(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        operation="remaster",
        plans={"default": {"stdout": "remastered"}},
    )

    assert run.returncode == 0
    assert run.stdout == "remastered"
    assert run.billing_calls == []
    assert [attempt["profile"] for attempt in run.attempts] == [None]


def test_remaster_with_empty_profiles_is_a_config_error(
    tmp_path: Path,
) -> None:
    run = _run_router(tmp_path, operation="remaster", profiles="")

    assert run.returncode == 78
    assert "[CONFIG]" in run.stderr
    assert run.attempts == []


def test_stalled_delegate_is_terminated_as_transient(tmp_path: Path) -> None:
    started = time.monotonic()
    run = _run_router(
        tmp_path,
        billing={
            "alpha": _billing_document("-1.00"),
            "beta": _billing_document("-1.00"),
        },
        plans={"alpha": {"sleep": 30}},
        env_extra={"TRANSPODCAST_MODAL_ROUTER_DEADLINE_SECONDS": "2"},
    )

    assert time.monotonic() - started < 20
    assert run.returncode == 4
    assert "[TRANSIENT]" in run.adapter_tail
    assert [attempt["profile"] for attempt in run.attempts] == ["alpha"]


@pytest.mark.parametrize(
    ("profiles", "env_extra"),
    [
        ("", {}),
        (None, {}),
        ("alpha,alpha", {}),
        ("alpha", {"TRANSPODCAST_MODAL_CREDIT_THRESHOLD_USD": "two"}),
        ("alpha", {"TRANSPODCAST_MODAL_MONTHLY_ALLOWANCE_USD": "lots"}),
    ],
)
def test_invalid_configuration_is_a_distinct_error(
    tmp_path: Path,
    profiles: str | None,
    env_extra: dict[str, str],
) -> None:
    run = _run_router(tmp_path, profiles=profiles, env_extra=env_extra)

    assert run.returncode == 78
    assert "[CONFIG]" in run.stderr
    assert "[SPEND_GUARDRAIL]" not in run.stderr
    assert run.attempts == []


def test_print_profiles_lists_configured_order(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        arguments=["--print-profiles"],
        profiles="alpha, beta,gamma",
    )

    assert run.returncode == 0
    assert run.stdout.splitlines() == ["alpha", "beta", "gamma"]


def test_print_profiles_with_unset_list_prints_nothing(
    tmp_path: Path,
) -> None:
    run = _run_router(tmp_path, arguments=["--print-profiles"], profiles=None)

    assert run.returncode == 0
    assert run.stdout == ""


def test_print_profiles_rejects_duplicates(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        arguments=["--print-profiles"],
        profiles="alpha,alpha",
    )

    assert run.returncode == 78
    assert "[CONFIG]" in run.stderr


def test_configuration_loads_from_dotenv_in_working_directory(
    tmp_path: Path,
) -> None:
    run = _run_router(
        tmp_path,
        profiles=None,
        billing={"solo": _billing_document("-13.54488408")},
        plans={"solo": {"stdout": "dotenv"}},
        write_dotenv="TRANSPODCAST_MODAL_PROFILES=solo\n",
    )

    assert run.returncode == 0
    assert run.stdout == "dotenv"
    assert [attempt["profile"] for attempt in run.attempts] == ["solo"]


def test_environment_overrides_dotenv(tmp_path: Path) -> None:
    run = _run_router(
        tmp_path,
        profiles="alpha",
        billing={"alpha": _billing_document("-13.54488408")},
        write_dotenv="TRANSPODCAST_MODAL_PROFILES=solo\n",
    )

    assert run.returncode == 0
    assert [attempt["profile"] for attempt in run.attempts] == ["alpha"]
