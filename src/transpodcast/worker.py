from __future__ import annotations

import logging
import signal
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from types import FrameType
from typing import cast

from transpodcast.adapters.telegram import TelegramBotAdapter
from transpodcast.config import TransPodcastSettings
from transpodcast.production import build_dependencies
from transpodcast.service import TransPodcastService

LOGGER = logging.getLogger("transpodcast.worker")


class ProcessingWorker:
    def __init__(
        self,
        service: TransPodcastService,
        telegram: TelegramBotAdapter | None = None,
    ) -> None:
        self.service = service
        self.telegram = telegram
        self.next_maintenance_at: datetime | None = None
        self.telegram_offset: int | None = None
        self._lease_owner = uuid.uuid4().hex
        self._auxiliary_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="transpodcast-auxiliary",
        )
        self._auxiliary_future: Future[None] | None = None
        self._ready = False

    def run_once(self) -> None:
        now = self.service.dependencies.clock.now()
        if self._ready:
            outcome = self.service.advance_next_processing_run()
            if outcome is not None:
                LOGGER.info("Processing advanced: %s", outcome)
        else:
            LOGGER.info("Background processing is paused for Setup Readiness")
        if (
            self._auxiliary_future is not None
            and self._auxiliary_future.done()
        ):
            try:
                self._auxiliary_future.result()
            except Exception:
                LOGGER.exception(
                    "Auxiliary worker iteration failed while Processing "
                    "Run polling continues"
                )
        if self._auxiliary_future is None or self._auxiliary_future.done():
            self._auxiliary_future = self._auxiliary_executor.submit(
                self._run_auxiliary,
                now,
            )

    def _run_auxiliary(self, now: datetime) -> None:
        readiness = self.service.setup_readiness()
        self._ready = bool(readiness["ready"])
        if (
            self.next_maintenance_at is None or now >= self.next_maintenance_at
        ) and not self.service.has_active_processing_work():
            try:
                maintenance_result = self.service.run_maintenance()
            except Exception:
                self.next_maintenance_at = now + timedelta(minutes=15)
                LOGGER.exception(
                    "Maintenance failed; retrying after backoff while "
                    "background processing continues"
                )
            else:
                LOGGER.info("Maintenance completed: %s", maintenance_result)
                self.next_maintenance_at = now + timedelta(days=1)
        if self._ready:
            enrichment = self.service.enrich_candidate_videos()
            if enrichment["enriched"] or enrichment["failed"]:
                LOGGER.info("Candidate enrichment: %s", enrichment)
            automation = cast(
                dict[str, object],
                self.service.settings()["automation"],
            )
            interval = int(
                str(automation["discovery_interval_minutes"])
            )
            if bool(
                automation.get("auto_discovery")
            ) and self.service.claim_scheduled_discovery(
                self._lease_owner,
                interval,
            ):
                self._run_discovery(interval)
        if self.telegram is not None and self.telegram.readiness():
            publication_deliveries = (
                self.service.deliver_pending_publication_notifications()
            )
            if publication_deliveries:
                LOGGER.info(
                    "Publication notifications delivered: %s",
                    publication_deliveries,
                )
            delivered = self.service.deliver_pending_candidate_recommendations()
            if delivered:
                LOGGER.info(
                    "Candidate recommendations delivered: %s",
                    delivered,
                )
            self._poll_telegram()

    def _run_discovery(self, interval_minutes: int) -> None:
        error_summary: str | None = None
        try:
            discovery_result = self.service.discover()
        except Exception as error:
            error_summary = f"{type(error).__name__}: {error}"[:500]
            LOGGER.exception("Discovery failed while Processing Run polling continues")
        else:
            LOGGER.info("Discovery completed: %s", discovery_result)
        finally:
            self.service.complete_scheduled_discovery(
                self._lease_owner,
                interval_minutes,
                error=error_summary,
            )

    def close(self) -> None:
        self._auxiliary_executor.shutdown(wait=True, cancel_futures=True)

    def _poll_telegram(self) -> None:
        if self.telegram is None:
            return
        updates = self.telegram.poll_updates(
            self.telegram_offset,
            timeout_seconds=1,
        )
        for update in updates:
            update_id = int(update["update_id"])
            self.telegram_offset = update_id + 1
            callback = update.get("callback_query") or {}
            callback_id = str(callback.get("id") or "")
            data = str(callback.get("data") or "")
            owner_id = str(
                (callback.get("from") or {}).get("id")
                or (((callback.get("message") or {}).get("chat") or {}).get("id"))
                or ""
            )
            if owner_id != self.telegram.owner_chat_id:
                if callback_id:
                    self.telegram.answer_callback(
                        callback_id,
                        "This action is not authorized.",
                    )
                continue
            if not data.startswith("tp:"):
                continue
            token = data.removeprefix("tp:")
            try:
                result = self.service.apply_telegram_action(token)
                message = f"TransPodcast updated: {result['state']}"
            except (LookupError, PermissionError, RuntimeError) as error:
                message = str(error)
            else:
                try:
                    self.telegram.complete_callback(
                        callback,
                        str(result["state"]),
                    )
                except RuntimeError:
                    LOGGER.warning(
                        "Telegram message completion failed after the "
                        "Owner decision committed"
                    )
            if callback_id:
                try:
                    self.telegram.answer_callback(callback_id, message)
                except RuntimeError:
                    LOGGER.warning(
                        "Telegram callback acknowledgement failed after "
                        "the Owner decision committed"
                    )


def configure_logging(log_root: Path) -> None:
    log_root.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        log_root / "transpodcast-worker.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)


def main() -> None:
    settings = TransPodcastSettings()
    configure_logging(settings.artifact_root.parent / "logs")
    dependencies = build_dependencies(settings)
    dependencies.database.create_schema()
    service = TransPodcastService(dependencies)
    recovered = service.recover_interrupted_processing()
    if recovered:
        LOGGER.info(
            "Recovered %s interrupted Processing Run(s)",
            recovered,
        )
    telegram = (
        dependencies.notifications
        if isinstance(dependencies.notifications, TelegramBotAdapter)
        else None
    )
    worker = ProcessingWorker(service, telegram)
    running = True

    def stop(_signal: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            try:
                worker.run_once()
            except Exception:
                LOGGER.exception("Processing Service iteration failed")
            time.sleep(settings.worker_poll_seconds)
    finally:
        worker.close()
        service.close()
