from __future__ import annotations

import httpx
import pytest

from transpodcast.adapters.telegram import TelegramBotAdapter
from transpodcast.domain import OwnerNotification


def test_telegram_http_errors_do_not_disclose_the_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_token = "secret-bot-token"
    request = httpx.Request(
        "POST",
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
    )
    response = httpx.Response(400, request=request)
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    adapter = TelegramBotAdapter(bot_token, "owner-chat")

    with pytest.raises(RuntimeError) as raised:
        adapter.send(
            OwnerNotification(
                kind="candidate_recommendation",
                title="Recommended episode",
                body="Episode summary",
                actions=(),
            )
        )

    assert bot_token not in str(raised.value)
    assert str(raised.value) == "Telegram send failed with HTTP 400"


def test_telegram_send_returns_the_delivered_message_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request(
        "POST",
        "https://api.telegram.org/bottest-token/sendMessage",
    )
    response = httpx.Response(
        200,
        request=request,
        json={"ok": True, "result": {"message_id": 481}},
    )
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    adapter = TelegramBotAdapter("test-token", "owner-chat")

    message_id = adapter.send(
        OwnerNotification(
            kind="candidate_recommendation",
            title="Recommended episode",
            body="Episode summary",
            actions=(),
        )
    )

    assert message_id == "481"


def test_owner_console_decision_replaces_telegram_buttons_with_chinese_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request(
        "POST",
        "https://api.telegram.org/bottest-token/editMessageText",
    )
    response = httpx.Response(200, request=request, json={"ok": True})
    payloads: list[dict[str, object]] = []

    def post(*args, **kwargs):  # type: ignore[no-untyped-def]
        payloads.append(kwargs["json"])
        return response

    monkeypatch.setattr(httpx, "post", post)
    adapter = TelegramBotAdapter("test-token", "owner-chat")

    adapter.complete_message("481", "publish")

    assert payloads == [
        {
            "chat_id": "owner-chat",
            "message_id": "481",
            "text": (
                "发布决定已记录: 已发布\n\n"
                "你可以在 Web 节目库中查看最新状态。"
            ),
            "reply_markup": {"inline_keyboard": []},
        }
    ]
