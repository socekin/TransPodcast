from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.cli import main
from transpodcast.domain import SourceCandidate
from transpodcast.testing import AcceptanceHarness


def _client(tmp_path) -> TestClient:
    harness = AcceptanceHarness.for_candidates(
        tmp_path,
        candidates=[
            SourceCandidate(
                youtube_video_id=f"useful-{index:03d}",
                title=f"Useful conversation {index}",
                description=f"Complete notes for conversation {index}.",
                duration_seconds=5640 + index,
                language="en",
                published_at=datetime(2026, 7, 27 - index, 8, 0, tzinfo=UTC),
                source_url=f"https://www.youtube.com/watch?v=useful-{index:03d}",
                captions=f"Captions for conversation {index}.",
            )
            for index in range(3)
        ],
        triage_results=None,
    )
    client = TestClient(create_app(harness.dependencies))
    client.patch("/api/v1/settings", json={"manual_processing": True})
    return client


def _run(client: httpx.Client, *arguments: str) -> tuple[int, str]:
    output = io.StringIO()
    code = main(list(arguments), client=client, stdout=output)
    return code, output.getvalue().strip()


def test_an_agent_adds_an_episode_that_waits_for_the_owner(tmp_path) -> None:
    client = _client(tmp_path)

    code, text = _run(
        client,
        "add",
        "https://www.youtube.com/watch?v=useful-000",
        "--from",
        "Claude Code",
    )

    assert code == 0
    assert text.splitlines()[0].startswith('Added "Useful conversation 0" (1 h 34 min)')
    assert "is waiting for the Owner to start it in TransPodcast Host." in text
    (item,) = client.get("/api/v1/processing-queue").json()["items"]
    assert item["status"] == "pending_start"
    assert item["origin"] == "agent"
    assert item["origin_agent"] == "claude-code"


def test_json_output_reports_the_run_and_a_repeat_is_not_added_twice(
    tmp_path,
) -> None:
    client = _client(tmp_path)
    url = "https://www.youtube.com/watch?v=useful-001"

    _run(client, "add", url, "--from", "codex")
    code, text = _run(client, "add", url, "--json")

    assert code == 0
    result = json.loads(text)
    assert result["already_in_transpodcast"] is True
    assert result["state"] == "ready_to_start"
    assert result["processing_run_id"] == 1
    assert result["estimated_modal_cost_usd"] is None
    assert len(client.get("/api/v1/processing-queue").json()["items"]) == 1


def test_a_channel_link_is_followed_with_channel_add(tmp_path) -> None:
    client = _client(tmp_path)

    code, text = _run(client, "add", "https://www.youtube.com/@usefulshow")

    assert code == 1
    assert "this is a channel link" in text
    assert "transpodcast channel add https://www.youtube.com/@usefulshow" in text

    code, text = _run(
        client, "--json", "channel", "add", "https://www.youtube.com/@usefulshow"
    )

    assert code == 0
    channel = json.loads(text)
    (saved,) = client.get("/api/v1/source-channels").json()["items"]
    assert saved["id"] == channel["channel_id"]
    assert saved["mode"] == "agent_filtering"


def test_status_and_list_read_the_episode_library(tmp_path) -> None:
    client = _client(tmp_path)
    _run(client, "add", "https://www.youtube.com/watch?v=useful-000")
    _run(client, "add", "https://www.youtube.com/watch?v=useful-002")

    code, text = _run(client, "status")
    assert code == 0
    assert sorted(text.splitlines()) == [
        f'Run {run}: "Useful conversation {index}" is waiting for the Owner to '
        "start it in TransPodcast Host."
        for run, index in ((1, 0), (2, 2))
    ]

    code, text = _run(client, "status", "2", "--json")
    assert code == 0
    assert json.loads(text)["state"] == "ready_to_start"

    code, text = _run(client, "list", "--limit", "1")
    assert code == 0
    assert len(text.splitlines()) == 1

    code, text = _run(client, "status", "9")
    assert code == 1
    assert text == "Error: Processing Run 9 was not found."


def test_an_unreachable_service_is_reported() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    client = httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(refuse),
    )

    code, text = _run(client, "list")

    assert code == 2
    assert text.startswith("Error: TransPodcast is not reachable")


def test_the_cost_estimate_follows_recent_runs(tmp_path) -> None:
    client = _client(tmp_path)
    _run(client, "add", "https://www.youtube.com/watch?v=useful-000")
    client.post("/api/v1/processing-runs/1:start")
    completed = client.post("/api/v1/processing-runs:advance").json()
    assert completed["processing_state"] == "completed"
    cost = client.get("/api/v1/processing-runs/1").json()["estimated_cost_usd"]
    assert cost > 0

    code, text = _run(
        client, "add", "https://www.youtube.com/watch?v=useful-002", "--json"
    )

    assert code == 0
    # The same rate per source minute: 5642 s against the first Run's 5640 s.
    assert json.loads(text)["estimated_modal_cost_usd"] == round(
        cost / (5640 / 60) * 5642 / 60, 2
    )

    # A repeat reports the Episode Library state, not the Run status.
    code, text = _run(
        client, "add", "https://www.youtube.com/watch?v=useful-000", "--json"
    )
    (library_item,) = [
        item
        for item in client.get("/api/v1/episode-library").json()["items"]
        if item["processing_run_id"] == 1
    ]
    assert json.loads(text)["state"] == library_item["library_state"]


def test_an_agent_starts_a_waiting_episode_when_the_owner_asks(tmp_path) -> None:
    client = _client(tmp_path)
    _run(client, "add", "https://www.youtube.com/watch?v=useful-000")

    code, text = _run(client, "start", "1", "--from", "grok")

    assert code == 0
    assert text == (
        "Processing Run 1 was started and is queued and starts automatically."
    )
    (item,) = client.get("/api/v1/processing-queue").json()["items"]
    assert item["status"] == "queued"
    activity = client.get("/api/v1/processing-runs/1").json()["activity"]
    started = [entry for entry in activity if entry["kind"] == "agent_started"]
    assert [entry["metadata"] for entry in started] == [{"requested_by": "grok"}]

    code, text = _run(client, "start", "1")
    assert code == 0
    assert text.startswith("Processing Run 1 was already started")

    code, text = _run(client, "start", "9")
    assert code == 1
    assert text == "Error: Processing Run 9 was not found."


def test_add_with_start_queues_the_episode_at_once(tmp_path) -> None:
    client = _client(tmp_path)

    code, text = _run(
        client,
        "add",
        "https://www.youtube.com/watch?v=useful-001",
        "--from",
        "codex",
        "--start",
        "--json",
    )

    assert code == 0
    result = json.loads(text)
    assert result["started"] is True
    assert result["state"] == "queued"
    (item,) = client.get("/api/v1/processing-queue").json()["items"]
    assert (item["status"], item["origin_agent"]) == ("queued", "codex")


def test_a_finished_episode_cannot_be_started(tmp_path) -> None:
    client = _client(tmp_path)
    _run(client, "add", "https://www.youtube.com/watch?v=useful-000", "--start")
    client.post("/api/v1/processing-runs:advance")

    code, text = _run(client, "start", "1")

    assert code == 1
    assert text.startswith("Error: Processing Run 1 is")
    assert text.endswith("; it cannot be started.")
