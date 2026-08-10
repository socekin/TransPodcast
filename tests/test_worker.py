from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta
from threading import Event
from types import SimpleNamespace

from transpodcast.worker import ProcessingWorker


class RecordingService:
    def __init__(
        self,
        auto_discovery: bool = True,
        automatically_accepted: int = 0,
        active_processing: bool = False,
        maintenance_error: bool = False,
    ) -> None:
        self.auto_discovery = auto_discovery
        self.automatically_accepted = automatically_accepted
        self.active_processing = active_processing
        self.maintenance_error = maintenance_error
        self.calls: list[str] = []
        self.discovery_next_due_at: datetime | None = None
        self.discovery_lease_owner: str | None = None
        self.readiness_finished = Event()
        self.dependencies = SimpleNamespace(
            clock=SimpleNamespace(now=lambda: datetime(2026, 7, 28, 15, 0))
        )

    def run_maintenance(self) -> dict[str, object]:
        self.calls.append("maintenance")
        if self.maintenance_error:
            raise RuntimeError("R2 is unavailable")
        return {}

    def has_active_processing_work(self) -> bool:
        self.calls.append("active-processing-check")
        return self.active_processing

    def setup_readiness(self) -> dict[str, bool]:
        self.readiness_finished.set()
        return {"ready": True}

    def enrich_candidate_videos(self) -> dict[str, int]:
        self.calls.append("enrichment")
        return {"enriched": 0, "failed": 0, "pending": 0}

    def advance_next_processing_run(self) -> dict[str, object] | None:
        self.calls.append("processing")
        return None if getattr(self, "idle", False) else {"processing_run_id": 1}

    def discover(self) -> dict[str, int]:
        self.calls.append("discovery")
        return {
            "discovered": self.automatically_accepted,
            "automatically_accepted": self.automatically_accepted,
        }

    def claim_scheduled_discovery(
        self,
        lease_owner: str,
        interval_minutes: int,
    ) -> bool:
        now = self.dependencies.clock.now()
        if self.discovery_lease_owner is not None:
            return False
        if self.discovery_next_due_at is not None and now < self.discovery_next_due_at:
            return False
        self.discovery_lease_owner = lease_owner
        return True

    def complete_scheduled_discovery(
        self,
        lease_owner: str,
        interval_minutes: int,
        *,
        error: str | None = None,
    ) -> None:
        if self.discovery_lease_owner != lease_owner:
            return
        self.discovery_lease_owner = None
        retry_minutes = 15 if error else interval_minutes
        self.discovery_next_due_at = self.dependencies.clock.now() + timedelta(
            minutes=retry_minutes
        )

    def settings(self) -> dict[str, object]:
        return {
            "automation": {
                "discovery_interval_minutes": 120,
                "auto_discovery": self.auto_discovery,
            }
        }

    def apply_telegram_action(self, token: str) -> dict[str, object]:
        self.calls.append(f"telegram-action:{token}")
        return {"state": "processing_queued"}

    def deliver_pending_candidate_recommendations(self) -> int:
        self.calls.append("candidate-recommendation-delivery")
        return 0

    def deliver_pending_publication_notifications(self) -> int:
        self.calls.append("publication-notification-delivery")
        return 0


class FailingAcknowledgementTelegram:
    owner_chat_id = "owner-chat"

    def readiness(self) -> bool:
        return True

    def poll_updates(
        self,
        offset: int | None,
        timeout_seconds: int,
    ) -> list[dict[str, object]]:
        del offset, timeout_seconds
        return [
            {
                "update_id": 19,
                "callback_query": {
                    "id": "callback-1",
                    "from": {"id": "owner-chat"},
                    "data": "tp:recommendation-token",
                    "message": {
                        "chat": {"id": "owner-chat"},
                        "message_id": 7,
                    },
                },
            }
        ]

    def complete_callback(
        self,
        callback: dict[str, object],
        outcome: str,
    ) -> None:
        del callback, outcome

    def answer_callback(self, callback_query_id: str, message: str) -> None:
        del callback_query_id, message
        raise RuntimeError("Telegram callback acknowledgement failed")


def test_processing_poll_runs_before_due_auxiliary_work() -> None:
    service = RecordingService()
    worker = ProcessingWorker(service)  # type: ignore[arg-type]

    worker.run_once()
    assert service.readiness_finished.wait(timeout=1)
    worker.run_once()
    worker.close()

    assert service.calls.count("processing") == 1
    assert service.calls.count("maintenance") == 1
    assert service.calls.count("discovery") == 1


def test_due_maintenance_waits_for_active_processing() -> None:
    service = RecordingService(active_processing=True)
    service.idle = True
    worker = ProcessingWorker(service)  # type: ignore[arg-type]

    worker.run_once()
    worker.close()

    assert "maintenance" not in service.calls


def test_failed_maintenance_backs_off_without_blocking_processing() -> None:
    service = RecordingService(maintenance_error=True)
    worker = ProcessingWorker(service)  # type: ignore[arg-type]

    worker.run_once()
    assert service.readiness_finished.wait(timeout=1)
    worker.run_once()
    worker.close()

    assert service.calls.count("processing") == 1
    assert service.calls.count("maintenance") == 1
    assert service.calls.count("discovery") == 1
    assert worker.next_maintenance_at == datetime(2026, 7, 28, 15, 15)


def test_not_due_maintenance_does_not_delay_discovered_processing() -> None:
    service = RecordingService(automatically_accepted=1)
    service.idle = True
    worker = ProcessingWorker(service)  # type: ignore[arg-type]
    worker.next_maintenance_at = datetime(2026, 7, 29, 15, 0)

    worker.run_once()
    assert service.readiness_finished.wait(timeout=1)
    worker.run_once()
    worker.close()

    assert service.calls.count("processing") == 1
    assert service.calls.count("enrichment") >= 1
    assert service.calls.count("discovery") == 1


def test_scheduled_discovery_waits_for_the_auto_discovery_setting() -> None:
    service = RecordingService(auto_discovery=False)
    worker = ProcessingWorker(service)  # type: ignore[arg-type]
    worker.next_maintenance_at = datetime(2026, 7, 29, 15, 0)

    worker.run_once()
    worker.close()

    assert "discovery" not in service.calls


def test_callback_acknowledgement_failure_does_not_fail_worker_iteration() -> None:
    service = RecordingService(auto_discovery=False)
    service.idle = True
    telegram = FailingAcknowledgementTelegram()
    worker = ProcessingWorker(  # type: ignore[arg-type]
        service,
        telegram,
    )
    worker.next_maintenance_at = datetime(2026, 7, 29, 15, 0)

    worker.run_once()
    worker.close()

    assert "telegram-action:recommendation-token" in service.calls
    assert "candidate-recommendation-delivery" in service.calls
    assert worker.telegram_offset == 20


def test_discovery_does_not_block_processing_run_polling() -> None:
    discovery_started = Event()
    release_discovery = Event()

    class BlockingDiscoveryService(RecordingService):
        def discover(self) -> dict[str, int]:
            self.calls.append("discovery")
            discovery_started.set()
            release_discovery.wait(timeout=5)
            return {
                "discovered": 0,
                "automatically_accepted": 0,
            }

    service = BlockingDiscoveryService()
    worker = ProcessingWorker(service)  # type: ignore[arg-type]
    worker.next_maintenance_at = datetime(2026, 7, 29, 15, 0)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first_poll = executor.submit(worker.run_once)
            assert discovery_started.wait(timeout=1)
            try:
                first_poll.result(timeout=0.2)
            except TimeoutError as error:
                raise AssertionError(
                    "Source Channel discovery blocked the Processing Run poller"
                ) from error

        worker.run_once()

        assert service.calls.count("processing") == 1
        assert service.calls.count("discovery") == 1
    finally:
        release_discovery.set()
        worker.close()


def test_readiness_refresh_does_not_block_processing_run_polling() -> None:
    readiness_started = Event()
    release_readiness = Event()
    readiness_finished = Event()

    class BlockingReadinessService(RecordingService):
        def setup_readiness(self) -> dict[str, bool]:
            readiness_started.set()
            release_readiness.wait(timeout=5)
            readiness_finished.set()
            return {"ready": True}

    service = BlockingReadinessService(auto_discovery=False)
    worker = ProcessingWorker(service)  # type: ignore[arg-type]

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first_poll = executor.submit(worker.run_once)
            assert readiness_started.wait(timeout=1)
            try:
                first_poll.result(timeout=0.2)
            except TimeoutError as error:
                raise AssertionError(
                    "Readiness refresh blocked the Processing Run poller"
                ) from error

        worker.run_once()
        assert service.calls.count("processing") == 0
        release_readiness.set()
        assert readiness_finished.wait(timeout=1)
        worker.run_once()

        assert service.calls.count("processing") == 1
    finally:
        release_readiness.set()
        worker.close()


def test_discovery_schedule_survives_worker_restart() -> None:
    service = RecordingService()
    first_worker = ProcessingWorker(service)  # type: ignore[arg-type]
    first_worker.next_maintenance_at = datetime(2026, 7, 29, 15, 0)

    first_worker.run_once()
    first_worker.close()

    restarted_worker = ProcessingWorker(service)  # type: ignore[arg-type]
    restarted_worker.next_maintenance_at = datetime(2026, 7, 29, 15, 0)
    restarted_worker.run_once()
    restarted_worker.close()

    assert service.calls.count("discovery") == 1


def test_overdue_discovery_runs_as_startup_catch_up() -> None:
    service = RecordingService()
    service.discovery_next_due_at = datetime(2026, 7, 28, 14, 59)
    worker = ProcessingWorker(service)  # type: ignore[arg-type]
    worker.next_maintenance_at = datetime(2026, 7, 29, 15, 0)

    worker.run_once()
    worker.close()

    assert service.calls.count("discovery") == 1


def test_maintenance_does_not_block_processing_run_polling() -> None:
    maintenance_started = Event()
    release_maintenance = Event()

    class BlockingMaintenanceService(RecordingService):
        def run_maintenance(self) -> dict[str, object]:
            self.calls.append("maintenance")
            maintenance_started.set()
            release_maintenance.wait(timeout=5)
            return {}

    service = BlockingMaintenanceService(auto_discovery=False)
    worker = ProcessingWorker(service)  # type: ignore[arg-type]

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first_poll = executor.submit(worker.run_once)
            assert maintenance_started.wait(timeout=1)
            try:
                first_poll.result(timeout=0.2)
            except TimeoutError as error:
                raise AssertionError(
                    "Maintenance blocked the Processing Run poller"
                ) from error

        worker.run_once()

        assert service.calls.count("processing") == 1
        assert service.calls.count("maintenance") == 1
    finally:
        release_maintenance.set()
        worker.close()
