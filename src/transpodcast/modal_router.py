"""Credit-aware Modal Account routing for the speech pipeline command.

The router honors the exact command contract of ``transpodcast-modal-pipeline``
and delegates each operation to it under a selected Modal profile. Selection
is proactive: profiles are tried in configured priority order and a profile
is skipped when its remaining monthly free credit falls below the configured
threshold. The speech adapter classifies failures by searching the tail of
stderr for markers, so only the final classification may reach the router's
own stderr; intermediate attempt output goes to the router log.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_EXIT_CODE = 78
SPEND_GUARDRAIL_EXIT_CODE = 3
TRANSIENT_EXIT_CODE = 4
_BILLING_TIMEOUT_SECONDS = 120
_TERMINATE_GRACE_SECONDS = 5
# The adapter inspects the last 1000 characters of stripped stderr; keep the
# forwarded tail short enough that an appended marker line stays inside it.
_STDERR_TAIL_CHARACTERS = 800
_EXIT_MARKERS = {
    2: "[AUTH]",
    SPEND_GUARDRAIL_EXIT_CODE: "[SPEND_GUARDRAIL]",
    TRANSIENT_EXIT_CODE: "[TRANSIENT]",
}


class ModalRouterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRANSPODCAST_",
        env_file=".env",
        extra="ignore",
    )

    modal_profiles: str = ""
    modal_credit_threshold_usd: Decimal = Decimal("2")
    modal_monthly_allowance_usd: Decimal = Decimal("30")
    modal_delegate_command: str = "transpodcast-modal-pipeline"
    modal_router_deadline_seconds: float = 82800.0
    modal_router_log_path: Path = Path("data/logs/modal-router.log")


class RouterConfigError(ValueError):
    pass


@dataclass(frozen=True)
class _Attempt:
    profile: str
    returncode: int
    stderr: str


class _RouterLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(),
            "event": event,
            **fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _parse_profiles(raw: str) -> list[str]:
    names = [name.strip() for name in raw.split(",") if name.strip()]
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise RouterConfigError(f"duplicate Modal profile: {name}")
        seen.add(name)
    return names


def _delegate_environment(profile: str | None) -> dict[str, str]:
    environment = dict(os.environ)
    if profile is not None:
        environment["MODAL_PROFILE"] = profile
    return environment


def _remaining_credit(
    profile: str,
    settings: ModalRouterSettings,
) -> tuple[Decimal | None, str | None]:
    """Return (remaining monthly credit, billing-unavailable reason)."""
    try:
        completed = subprocess.run(
            ["modal", "billing", "summary", "--json"],
            env=_delegate_environment(profile),
            capture_output=True,
            text=True,
            timeout=_BILLING_TIMEOUT_SECONDS,
        )
    except OSError as error:
        return None, f"billing command failed to start: {error}"
    except subprocess.TimeoutExpired:
        return None, "billing command timed out"
    if completed.returncode != 0:
        return None, f"billing command exited {completed.returncode}"
    try:
        document = json.loads(completed.stdout)
        credits = Decimal(str(document["adjustments"]["credits"]))
    except (
        json.JSONDecodeError,
        InvalidOperation,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        return None, f"billing summary unparsable: {error}"
    consumed = max(Decimal(0), -credits)
    remaining = max(
        Decimal(0),
        settings.modal_monthly_allowance_usd - consumed,
    )
    return remaining, None


def _signal_group(process: subprocess.Popen[str], signum: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signum)


def _run_delegate(
    command: list[str],
    profile: str | None,
    deadline_at: float,
) -> tuple[int, str, str, bool]:
    """Run one delegate attempt; returns (code, stdout, stderr, stalled)."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_delegate_environment(profile),
        start_new_session=True,
    )
    timeout = max(0.1, deadline_at - time.monotonic())
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return process.returncode, stdout, stderr, True


def _emit_failure(attempt: _Attempt) -> None:
    tail = attempt.stderr.strip()[-_STDERR_TAIL_CHARACTERS:]
    if tail:
        sys.stderr.write(tail)
        sys.stderr.write("\n")
    marker = _EXIT_MARKERS.get(attempt.returncode)
    if marker is not None:
        sys.stderr.write(
            f"{marker} final Modal Account attempt ({attempt.profile}) failed\n"
        )


def _routing_context(request_path: Path) -> dict[str, Any]:
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        request = {}
    raw = request.get("routing_context") if isinstance(request, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    context: dict[str, Any] = {
        "correlation_id": str(raw.get("correlation_id") or uuid.uuid4().hex),
    }
    for key in ("processing_run_id", "processing_attempt_id"):
        value = raw.get(key)
        if isinstance(value, int):
            context[key] = value
    stage = raw.get("stage")
    if isinstance(stage, str) and stage:
        context["stage"] = stage[:64]
    return context


def _failure_reason(returncode: int) -> str:
    return {
        2: "authentication_failed",
        SPEND_GUARDRAIL_EXIT_CODE: "spend_guardrail",
        TRANSIENT_EXIT_CODE: "transient_provider_failure",
    }.get(returncode, "command_failed")


def _attach_routing_evidence(
    output_path: Path,
    evidence: dict[str, Any],
) -> None:
    value = (
        json.loads(output_path.read_text(encoding="utf-8"))
        if output_path.exists()
        else {}
    )
    if not isinstance(value, dict):
        raise TypeError("Modal pipeline response must be an object")
    value["routing"] = evidence
    output_path.write_text(
        json.dumps(value, ensure_ascii=False),
        encoding="utf-8",
    )


def _route(
    delegate: list[str],
    profiles: list[str],
    settings: ModalRouterSettings,
    log: _RouterLog,
    *,
    operation: str,
    output_path: Path,
    context: dict[str, Any],
) -> int:
    deadline_at = time.monotonic() + settings.modal_router_deadline_seconds
    attempts: list[_Attempt] = []
    routing_attempts: list[dict[str, str]] = []
    log_context = {"operation": operation, **context}

    def persist_terminal_evidence(selected_profile: str | None) -> None:
        evidence = {
            "operation": operation,
            **context,
            "attempts": routing_attempts,
        }
        if selected_profile is not None:
            evidence["selected_profile"] = selected_profile
        try:
            _attach_routing_evidence(output_path, evidence)
        except (OSError, TypeError, json.JSONDecodeError) as error:
            with contextlib.suppress(OSError):
                output_path.write_text(
                    json.dumps({"routing": evidence}, ensure_ascii=False),
                    encoding="utf-8",
                )
            log.write(
                "routing_evidence_failed",
                reason=type(error).__name__,
                **log_context,
            )

    for profile in profiles:
        remaining, unavailable_reason = _remaining_credit(profile, settings)
        if remaining is None:
            log.write(
                "billing_unavailable",
                profile=profile,
                reason=unavailable_reason,
                **log_context,
            )
        elif remaining < settings.modal_credit_threshold_usd:
            log.write(
                "profile_skipped",
                profile=profile,
                remaining_usd=str(remaining),
                threshold_usd=str(settings.modal_credit_threshold_usd),
                **log_context,
            )
            routing_attempts.append(
                {
                    "profile": profile,
                    "outcome": "skipped",
                    "reason": "insufficient_credit",
                    "remaining_usd": str(remaining),
                }
            )
            continue
        else:
            log.write(
                "profile_eligible",
                profile=profile,
                remaining_usd=str(remaining),
                **log_context,
            )
        attempt_evidence = {
            "profile": profile,
            "remaining_usd": (str(remaining) if remaining is not None else "unknown"),
        }
        log.write("attempt_started", profile=profile, **log_context)
        returncode, stdout, stderr, stalled = _run_delegate(
            delegate,
            profile,
            deadline_at,
        )
        if stalled:
            routing_attempts.append(
                {
                    **attempt_evidence,
                    "outcome": "failed",
                    "reason": "router_deadline",
                }
            )
            log.write(
                "attempt_stalled",
                profile=profile,
                reason="router_deadline",
                **log_context,
            )
            persist_terminal_evidence(None)
            sys.stderr.write(
                "[TRANSIENT] Modal pipeline attempt on profile "
                f"{profile} exceeded the router deadline\n"
            )
            return TRANSIENT_EXIT_CODE
        if returncode == 0:
            routing_attempts.append({**attempt_evidence, "outcome": "succeeded"})
            evidence = {
                "operation": operation,
                "selected_profile": profile,
                **context,
                "attempts": routing_attempts,
            }
            try:
                _attach_routing_evidence(output_path, evidence)
            except (OSError, TypeError, json.JSONDecodeError):
                routing_attempts[-1] = {
                    **attempt_evidence,
                    "outcome": "failed",
                    "reason": "invalid_response",
                }
                log.write(
                    "attempt_failed",
                    profile=profile,
                    returncode=1,
                    reason="invalid_response",
                    **log_context,
                )
                attempts.append(_Attempt(profile, 1, ""))
                continue
            log.write("attempt_succeeded", profile=profile, **log_context)
            sys.stdout.write(stdout)
            sys.stderr.write(stderr)
            return 0
        log.write(
            "attempt_failed",
            profile=profile,
            returncode=returncode,
            reason=_failure_reason(returncode),
            **log_context,
        )
        routing_attempts.append(
            {
                **attempt_evidence,
                "outcome": "failed",
                "reason": _failure_reason(returncode),
            }
        )
        attempts.append(_Attempt(profile, returncode, stderr))
    guardrail = next(
        (
            attempt
            for attempt in attempts
            if attempt.returncode == SPEND_GUARDRAIL_EXIT_CODE
        ),
        None,
    )
    if guardrail is not None:
        persist_terminal_evidence(None)
        _emit_failure(guardrail)
        return SPEND_GUARDRAIL_EXIT_CODE
    if attempts:
        persist_terminal_evidence(None)
        _emit_failure(attempts[-1])
        return attempts[-1].returncode
    persist_terminal_evidence(None)
    log.write("no_eligible_profile", profiles=profiles, **log_context)
    sys.stderr.write(
        "[SPEND_GUARDRAIL] no Modal Account has remaining credit at or "
        "above the configured threshold\n"
    )
    return SPEND_GUARDRAIL_EXIT_CODE


def _passthrough(
    delegate: list[str],
    log: _RouterLog,
    context: dict[str, Any],
) -> int:
    log.write("remaster_passthrough", operation="remaster", **context)
    completed = subprocess.run(delegate, check=False)
    return completed.returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Route TransPodcast Modal pipeline operations across Modal "
            "Accounts by remaining monthly credit."
        )
    )
    parser.add_argument(
        "--print-profiles",
        action="store_true",
        help="Print the configured Modal profiles in priority order.",
    )
    parser.add_argument(
        "--operation",
        choices=("analyze", "render", "remaster", "readiness"),
    )
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        settings = ModalRouterSettings()
        profiles = _parse_profiles(settings.modal_profiles)
    except (ValidationError, RouterConfigError) as error:
        print(f"[CONFIG] {error}", file=sys.stderr)
        raise SystemExit(CONFIG_EXIT_CODE) from error
    if arguments.print_profiles:
        for name in profiles:
            print(name)
        return
    if (
        arguments.operation is None
        or arguments.request is None
        or arguments.output is None
    ):
        parser.error("--operation, --request, and --output are required")
    if not profiles:
        print(
            "[CONFIG] TRANSPODCAST_MODAL_PROFILES must list at least one Modal profile",
            file=sys.stderr,
        )
        raise SystemExit(CONFIG_EXIT_CODE)
    log = _RouterLog(settings.modal_router_log_path)
    context = _routing_context(arguments.request)
    delegate = [
        *shlex.split(settings.modal_delegate_command),
        "--operation",
        arguments.operation,
        "--request",
        str(arguments.request),
        "--output",
        str(arguments.output),
    ]
    if arguments.operation == "remaster":
        raise SystemExit(_passthrough(delegate, log, context))
    raise SystemExit(
        _route(
            delegate,
            profiles,
            settings,
            log,
            operation=arguments.operation,
            output_path=arguments.output,
            context=context,
        )
    )


if __name__ == "__main__":
    main()
