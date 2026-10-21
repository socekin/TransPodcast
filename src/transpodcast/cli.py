"""The ``transpodcast`` command: local Agents submit and follow episodes.

Codex, Claude Code, Grok, and other local Agents run it when the Owner shares
a YouTube podcast link with them. It talks only to the TransPodcast host's Control
API: add one episode, follow a channel, read progress, and start a Run. A
start spends Modal credit, so Agents start a Run only when the Owner asks
them to in conversation. Deleting, reprocessing, and publishing stay in
TransPodcast Host.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from typing import Any, TextIO

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
API_URL_ENVIRONMENT = "TRANSPODCAST_API_URL"
ACTIVE_LIBRARY_STATES = ("ready_to_start", "queued", "processing", "action_required")
# How each Episode Library state completes "Run 12: <title> ...".
LIBRARY_STATE_TEXT = {
    "ready_to_start": "is waiting for the Owner to start it in TransPodcast Host",
    "queued": "is queued and starts automatically",
    "processing": "is processing",
    "action_required": "needs the Owner's attention in TransPodcast Host",
    "ready_to_publish": "is finished and waiting for the Owner to publish it",
    "published": "is published to the Private Feed",
    "unpublished": "is finished but not published",
    "taken_down": "was taken down",
    "closed": "was closed by the Owner without an episode",
}


class CommandError(Exception):
    """A failure to report to the Agent, with the exit code to return."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
    stdout: TextIO | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    output = stdout or sys.stdout
    api_url = os.environ.get(API_URL_ENVIRONMENT, DEFAULT_API_URL)
    owned = client is None
    http = client or httpx.Client(base_url=api_url, timeout=120.0)
    try:
        result, text = arguments.handler(http, arguments)
    except CommandError as error:
        print(f"Error: {error}", file=output)
        return error.exit_code
    except httpx.TransportError as error:
        print(
            f"Error: TransPodcast is not reachable at {api_url} ({error}). "
            "Ask the Owner to check that the TransPodcast services are running.",
            file=output,
        )
        return 2
    finally:
        if owned:
            http.close()
    print(
        json.dumps(result, ensure_ascii=False, indent=2) if arguments.json else text,
        file=output,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transpodcast",
        description=(
            "Submit YouTube podcast episodes to TransPodcast for Mandarin "
            "dubbing and follow their progress."
        ),
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    # Agents also write the flag after the command: `transpodcast add URL --json`.
    json_option = argparse.ArgumentParser(add_help=False)
    json_option.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print the result as JSON",
    )
    commands = parser.add_subparsers(required=True, metavar="command")

    add = commands.add_parser(
        "add", parents=[json_option], help="add one episode by its YouTube link"
    )
    add.add_argument("url", help="a YouTube video link")
    add.add_argument(
        "--from",
        dest="requested_by",
        default="agent",
        help="your Agent name, shown to the Owner (codex, claude, grok, ...)",
    )
    add.add_argument(
        "--start",
        action="store_true",
        help="also start processing; only when the Owner asked you to start it",
    )
    add.set_defaults(handler=_add_episode)

    start = commands.add_parser(
        "start",
        parents=[json_option],
        help="start a waiting episode; only when the Owner asked you to",
    )
    start.add_argument("run_id", type=int, help="the Processing Run id from `add`")
    start.add_argument(
        "--from",
        dest="requested_by",
        default="agent",
        help="your Agent name, recorded with the start",
    )
    start.set_defaults(handler=_start)

    channel = commands.add_parser("channel", help="follow a YouTube channel")
    channel_commands = channel.add_subparsers(required=True, metavar="action")
    channel_add = channel_commands.add_parser(
        "add", parents=[json_option], help="follow a channel by its YouTube link"
    )
    channel_add.add_argument("url", help="a YouTube channel link")
    channel_add.set_defaults(handler=_add_channel)

    status = commands.add_parser(
        "status",
        parents=[json_option],
        help="show one episode, or every episode in progress",
    )
    status.add_argument(
        "run_id", nargs="?", type=int, help="the Processing Run id from `add`"
    )
    status.set_defaults(handler=_status)

    library = commands.add_parser(
        "list", parents=[json_option], help="list the latest episodes"
    )
    library.add_argument("--limit", type=int, default=10)
    library.set_defaults(handler=_list)
    return parser


def _add_episode(
    http: httpx.Client,
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], str]:
    resolution = _request(
        http, "POST", "/api/v1/source-resolutions", {"url": arguments.url}
    )
    video = resolution.get("requested_video")
    if video is None:
        raise CommandError(
            "this is a channel link, not an episode. Run "
            f"`transpodcast channel add {arguments.url}` to follow the channel."
        )
    imported = _request(
        http,
        "POST",
        f"/api/v1/source-resolutions/{resolution['resolution_id']}:import-video",
        {"requested_by": _agent_name(arguments.requested_by)},
    )
    run_id = imported.get("processing_run_id")
    # The Episode Library state, as `status` shows it, also for a repeat.
    state = _library_state(http, run_id) if run_id is not None else None
    started = False
    if arguments.start and run_id is not None and state == "ready_to_start":
        state, _estimate = _start_run(http, run_id, arguments.requested_by)
        started = True
    result = {
        "title": video["title"],
        "channel": resolution["channel"]["title"],
        "duration_seconds": video["duration_seconds"],
        "processing_run_id": run_id,
        "episode_id": imported.get("episode_id"),
        "state": state,
        "already_in_transpodcast": bool(imported["reused"]),
        "started": started,
        "estimated_modal_cost_usd": imported.get("estimated_cost_usd"),
    }
    lines = [
        (
            f'"{video["title"]}" is already in TransPodcast.'
            if result["already_in_transpodcast"]
            else f'Added "{video["title"]}" '
            f"({_duration(int(video['duration_seconds']))}) "
            f"from {result['channel']}."
        )
    ]
    if result["processing_run_id"] is None:
        lines.append(
            "It was not queued for processing; the Owner can process it "
            "from TransPodcast Host."
        )
    else:
        lines.append(
            f"Processing Run {run_id} "
            f"{'was started and ' if started else ''}"
            f"{LIBRARY_STATE_TEXT.get(str(state), 'was added')}."
        )
    cost = result["estimated_modal_cost_usd"]
    if cost is not None and (started or not result["already_in_transpodcast"]):
        lines.append(f"Estimated Modal cost: about ${cost:.2f}.")
    return result, "\n".join(lines)


def _start(
    http: httpx.Client,
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], str]:
    state = _library_state(http, arguments.run_id)
    if state is None:
        raise CommandError(f"Processing Run {arguments.run_id} was not found.")
    if state in ("queued", "processing"):
        result = {
            "processing_run_id": arguments.run_id,
            "state": state,
            "started": False,
            "estimated_modal_cost_usd": None,
        }
        return result, (
            f"Processing Run {arguments.run_id} was already started and "
            f"{LIBRARY_STATE_TEXT[state]}."
        )
    if state != "ready_to_start":
        raise CommandError(
            f"Processing Run {arguments.run_id} "
            f"{LIBRARY_STATE_TEXT.get(state, 'is ' + state)}; it cannot be started."
        )
    state, estimate = _start_run(http, arguments.run_id, arguments.requested_by)
    result = {
        "processing_run_id": arguments.run_id,
        "state": state,
        "started": True,
        "estimated_modal_cost_usd": estimate,
    }
    lines = [
        f"Processing Run {arguments.run_id} was started and "
        f"{LIBRARY_STATE_TEXT.get(str(state), 'is ' + str(state))}."
    ]
    if estimate is not None:
        lines.append(f"Estimated Modal cost: about ${estimate:.2f}.")
    return result, "\n".join(lines)


def _start_run(
    http: httpx.Client,
    run_id: int,
    requested_by: str,
) -> tuple[str | None, float | None]:
    started = _request(
        http,
        "POST",
        f"/api/v1/processing-runs/{run_id}:start",
        {"requested_by": _agent_name(requested_by)},
    )
    estimate = started.get("estimated_cost_usd")
    return _library_state(http, run_id), (
        float(estimate) if estimate is not None else None
    )


def _library_state(http: httpx.Client, run_id: int) -> str | None:
    return next(
        (
            str(item["library_state"])
            for item in _library(http)
            if item["processing_run_id"] == run_id
        ),
        None,
    )


def _add_channel(
    http: httpx.Client,
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], str]:
    channel = _request(
        http,
        "POST",
        "/api/v1/source-channels",
        {"url": arguments.url, "mode": "agent_filtering", "backfill_video_ids": []},
    )
    result = {"channel_id": channel["id"], "title": channel["title"]}
    return result, (
        f'Following "{channel["title"]}". When TransPodcast checks the channel, '
        "the Agent filter picks new episodes that match the Owner's "
        "interests."
    )


def _status(
    http: httpx.Client,
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], str]:
    items = _library(http)
    if arguments.run_id is not None:
        match = next(
            (item for item in items if item["processing_run_id"] == arguments.run_id),
            None,
        )
        if match is None:
            raise CommandError(f"Processing Run {arguments.run_id} was not found.")
        summary = _summary(match)
        return summary, _line(summary)
    active = [
        _summary(item)
        for item in items
        if item["library_state"] in ACTIVE_LIBRARY_STATES
    ]
    text = "\n".join(_line(item) for item in active) or "No episode is in progress."
    return {"episodes": active}, text


def _list(
    http: httpx.Client,
    arguments: argparse.Namespace,
) -> tuple[dict[str, Any], str]:
    items = sorted(_library(http), key=lambda item: item["created_at"], reverse=True)
    latest = [_summary(item) for item in items[: max(1, arguments.limit)]]
    text = "\n".join(_line(item) for item in latest) or "The library is empty."
    return {"episodes": latest}, text


def _library(http: httpx.Client) -> list[dict[str, Any]]:
    return list(_request(http, "GET", "/api/v1/episode-library")["items"])


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "processing_run_id": item["processing_run_id"],
        "episode_id": item["episode_id"],
        "title": item["title"],
        "channel": item["source_show"],
        "state": item["library_state"],
        "stage": item["stage"],
    }


def _line(item: dict[str, Any]) -> str:
    state = LIBRARY_STATE_TEXT.get(str(item["state"]), f"is {item['state']}")
    return f'Run {item["processing_run_id"]}: "{item["title"]}" {state}.'


def _request(
    http: httpx.Client,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = http.request(method, path, json=payload)
    if response.is_error:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise CommandError(f"TransPodcast rejected the request: {detail}")
    value = response.json()
    if not isinstance(value, dict):
        raise CommandError("TransPodcast returned an unexpected response.")
    return value


def _agent_name(value: str) -> str:
    """``Claude Code`` -> ``claude-code``, as the Control API accepts."""
    name = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:32]
    return name or "agent"


def _duration(seconds: int) -> str:
    hours, minutes = divmod(round(seconds / 60), 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


if __name__ == "__main__":
    sys.exit(main())
