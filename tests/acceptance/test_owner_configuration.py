from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from stat import S_IMODE

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.service import TransPodcastService
from transpodcast.storage import LocalPublicationStorage
from transpodcast.testing import AcceptanceHarness


class _TransactionTrackingStorage(LocalPublicationStorage):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.lock_is_held: Callable[[], bool] = lambda: False
        self.protected_writes: list[bool] = []

    def put_feed_token_hash(self, token_hash: str) -> None:
        self.protected_writes.append(self.lock_is_held())
        super().put_feed_token_hash(token_hash)

    def put_feed_snapshot(self, content: bytes) -> None:
        self.protected_writes.append(self.lock_is_held())
        super().put_feed_snapshot(content)


class _LockTrackingService(TransPodcastService):
    def __init__(self, dependencies) -> None:
        super().__init__(dependencies)
        self._lock_state = threading.local()
        self._activity_lock = threading.Lock()
        self._lock_attempts = 0
        self._active_transactions = 0
        self.maximum_concurrent_transactions = 0
        self.first_lock_acquired = threading.Event()
        self.second_lock_attempted = threading.Event()

    @contextmanager
    def _feed_token_lock(self, path: Path) -> Iterator[None]:
        with self._activity_lock:
            self._lock_attempts += 1
            attempt = self._lock_attempts
            if attempt == 2:
                self.second_lock_attempted.set()
        with super()._feed_token_lock(path):
            with self._activity_lock:
                self._active_transactions += 1
                self.maximum_concurrent_transactions = max(
                    self.maximum_concurrent_transactions,
                    self._active_transactions,
                )
            if attempt == 1:
                self.first_lock_acquired.set()
                if not self.second_lock_attempted.wait(timeout=5):
                    raise AssertionError(
                        "Second feed-token transaction did not start"
                    )
            self._lock_state.held = True
            try:
                yield
            finally:
                self._lock_state.held = False
                with self._activity_lock:
                    self._active_transactions -= 1

    def feed_transaction_lock_is_held(self) -> bool:
        return bool(getattr(self._lock_state, "held", False))


def test_owner_api_rejects_untrusted_hosts(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    response = client.get(
        "/api/v1/settings",
        headers={"Host": "attacker.example"},
    )

    assert response.status_code == 400


def test_owner_app_generates_and_reuses_a_private_feed_token(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    token_path = tmp_path / "runtime-secrets" / "feed-token"
    dependencies = replace(
        harness.dependencies,
        feed_token="",
        feed_token_path=token_path,
    )

    client = TestClient(create_app(dependencies))

    assert token_path.is_file()
    generated_token = token_path.read_text(encoding="utf-8").strip()
    assert len(generated_token) >= 32
    assert S_IMODE(token_path.stat().st_mode) == 0o600
    subscription = client.get("/api/v1/private-feed-subscription")
    assert subscription.status_code == 200
    assert generated_token in subscription.json()["subscription_url"]

    TestClient(create_app(dependencies))

    assert token_path.read_text(encoding="utf-8").strip() == generated_token


def test_concurrent_feed_token_revocations_remain_consistent(tmp_path) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    storage = _TransactionTrackingStorage(tmp_path / "tracked-storage")
    dependencies = replace(
        harness.dependencies,
        storage=storage,
        feed_token="",
    )
    service = _LockTrackingService(dependencies)
    storage.lock_is_held = service.feed_transaction_lock_is_held

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(service.revoke_feed_token)
            assert service.first_lock_acquired.wait(timeout=5)
            second = executor.submit(service.revoke_feed_token)
            first.result(timeout=10)
            second.result(timeout=10)

        token_path = dependencies.feed_token_path
        assert token_path is not None
        current_token = token_path.read_text(encoding="utf-8").strip()
        expected_hash = hashlib.sha256(current_token.encode()).hexdigest()
        assert storage.read_feed_token_hash() == expected_hash
        assert current_token in (
            storage.root / "feed/current.xml"
        ).read_text(encoding="utf-8")
        assert storage.protected_writes == [True, True, True, True]
        assert service.maximum_concurrent_transactions == 1
    finally:
        service.close()


def test_owner_versions_interests_and_updates_safe_automation_settings(
    tmp_path,
) -> None:
    harness = AcceptanceHarness.ready(tmp_path)
    client = TestClient(create_app(harness.dependencies))

    initial = client.get("/api/v1/interest-profile").json()
    assert initial["version"] == 1

    updated = client.put(
        "/api/v1/interest-profile",
        json={
            "markdown": (
                "# Interests\n\n"
                "Product strategy, developer tools, and AI infrastructure.\n\n"
                "# Exclusions\n\n"
                "Celebrity news and pure entertainment."
            )
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    revisions = client.get(
        "/api/v1/interest-profile/revisions"
    ).json()["items"]
    assert [item["version"] for item in revisions] == [2, 1]
    assert revisions[0]["active"] is True
    assert revisions[1]["active"] is False

    defaults = client.get("/api/v1/settings").json()
    assert defaults["automation"] == {
        "discovery_interval_minutes": 120,
        "duration_guardrail_minutes": 180,
        "processing_target_hours": 24,
        # The AcceptanceHarness seeds automatic mode; production
        # defaults to manual processing.
        "manual_processing": False,
        "auto_discovery": False,
    }
    assert defaults["speech"] == {"model": "indextts2"}
    assert defaults["retention"]["intermediate_days"] == 7
    assert defaults["agent"] == {
        "provider": "codex",
        "readiness": {"codex": "configured", "claude": "configured"},
    }
    assert defaults["secrets"] == {
        "agent": "configured",
        "modal": "configured",
        "cloudflare": "configured",
        "telegram": "configured",
        "feed_token": "configured",
    }

    changed = client.patch(
        "/api/v1/settings",
        json={
            "discovery_interval_minutes": 60,
            "duration_guardrail_minutes": 240,
            "notification_preferences": {
                "candidate_decision": True,
                "automatic_ignore": False,
                "processing_failure": True,
                "spend_guardrail": True,
                "publication_decision": True,
            },
        },
    )
    assert changed.status_code == 200
    assert changed.json()["automation"]["discovery_interval_minutes"] == 60
    assert changed.json()["automation"]["duration_guardrail_minutes"] == 240
    assert changed.json()["notifications"]["automatic_ignore"] is False

    speech = client.patch(
        "/api/v1/settings",
        json={"speech_model": "indextts25"},
    )
    assert speech.status_code == 200
    assert speech.json()["speech"] == {"model": "indextts25"}
    assert client.patch(
        "/api/v1/settings",
        json={"speech_model": "unsupported"},
    ).status_code == 422

    agent = client.patch(
        "/api/v1/settings",
        json={"agent_provider": "claude"},
    )
    assert agent.status_code == 200
    assert agent.json()["agent"]["provider"] == "claude"
    assert agent.json()["speech"] == {"model": "indextts25"}
    assert client.patch(
        "/api/v1/settings",
        json={"agent_provider": "unsupported"},
    ).status_code == 422

    client.post(
        "/api/v1/source-channels",
        json={
            "url": "https://www.youtube.com/@usefulshow",
            "mode": "agent_filtering",
        },
    )
    channels = client.get("/api/v1/source-channels").json()["items"]
    paused = client.patch(
        f"/api/v1/source-channels/{channels[0]['id']}",
        json={"mode": "paused"},
    )
    assert paused.status_code == 200
    assert paused.json()["mode"] == "paused"
    assert client.post("/api/v1/discovery-runs").json()["discovered"] == 0

    serialized = str(client.get("/api/v1/settings").json())
    assert harness.feed_token not in serialized
