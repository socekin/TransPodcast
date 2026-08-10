from __future__ import annotations

from typing import Any

import httpx

from transpodcast.domain import OwnerNotification
from transpodcast.errors import (
    NotificationDeliveryRejectedError,
    NotificationDeliveryUncertainError,
)
from transpodcast.i18n import zh_cn


class TelegramBotAdapter:
    def __init__(self, bot_token: str, owner_chat_id: str) -> None:
        self.bot_token = bot_token
        self.owner_chat_id = owner_chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def readiness(self) -> bool:
        return bool(self.bot_token and self.owner_chat_id)

    def send(self, notification: OwnerNotification) -> str | None:
        if not self.readiness():
            raise NotificationDeliveryRejectedError(
                "Telegram Bot is not configured"
            )
        payload: dict[str, Any] = {
            "chat_id": self.owner_chat_id,
            "text": f"{notification.title}\n\n{notification.body}",
        }
        if notification.actions:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {
                            "text": action.label,
                            "callback_data": f"tp:{action.token}",
                        }
                        for action in notification.actions
                    ]
                ]
            }
        response = self._post(
            "sendMessage",
            payload,
            timeout=30,
            operation="send",
        )
        try:
            result = response.json().get("result") or {}
        except ValueError:
            return None
        message_id = result.get("message_id")
        return str(message_id) if message_id is not None else None

    def poll_updates(
        self,
        offset: int | None,
        timeout_seconds: int = 25,
    ) -> list[dict[str, Any]]:
        response = self._get(
            "getUpdates",
            params={
                "offset": offset,
                "timeout": timeout_seconds,
                "allowed_updates": '["callback_query"]',
            },
            timeout=timeout_seconds + 5,
            operation="poll",
        )
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram update polling failed")
        return list(payload.get("result") or [])

    def answer_callback(
        self,
        callback_query_id: str,
        message: str,
    ) -> None:
        self._post(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
                "text": message,
            },
            timeout=30,
            operation="callback acknowledgement",
        )

    def complete_callback(
        self,
        callback: dict[str, Any],
        outcome: str,
    ) -> None:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        if chat_id is None or message_id is None:
            return
        original = str(message.get("text") or "TransPodcast decision")
        result = zh_cn(f"publication_decision.outcome.{outcome}")
        self._post(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": (
                    f"{original}\n\n"
                    f"{zh_cn('publication_decision.recorded')}: {result}"
                ),
                "reply_markup": {"inline_keyboard": []},
            },
            timeout=30,
            operation="message completion",
        )

    def complete_message(self, message_id: str, outcome: str) -> None:
        result = zh_cn(f"publication_decision.outcome.{outcome}")
        self._post(
            "editMessageText",
            {
                "chat_id": self.owner_chat_id,
                "message_id": message_id,
                "text": (
                    f"{zh_cn('publication_decision.recorded')}: {result}\n\n"
                    f"{zh_cn('publication_decision.owner_console_hint')}"
                ),
                "reply_markup": {"inline_keyboard": []},
            },
            timeout=30,
            operation="message completion",
        )

    def _post(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        timeout: int,
        operation: str,
    ) -> httpx.Response:
        try:
            response = httpx.post(
                f"{self.base_url}/{method}",
                json=payload,
                timeout=timeout,
            )
        except httpx.HTTPError:
            raise NotificationDeliveryUncertainError(
                f"Telegram {operation} failed"
            ) from None
        self._ensure_success(response, operation)
        return response

    def _get(
        self,
        method: str,
        *,
        params: dict[str, Any],
        timeout: int,
        operation: str,
    ) -> httpx.Response:
        try:
            response = httpx.get(
                f"{self.base_url}/{method}",
                params=params,
                timeout=timeout,
            )
        except httpx.HTTPError:
            raise NotificationDeliveryUncertainError(
                f"Telegram {operation} failed"
            ) from None
        self._ensure_success(response, operation)
        return response

    @staticmethod
    def _ensure_success(response: httpx.Response, operation: str) -> None:
        if response.is_error:
            raise NotificationDeliveryRejectedError(
                f"Telegram {operation} failed with HTTP "
                f"{response.status_code}"
            )
